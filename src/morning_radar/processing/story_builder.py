"""Build traceable Story objects from classified, deduplicated RawItems."""

from __future__ import annotations

import hashlib
import logging
import math
from collections import Counter
from datetime import datetime

from morning_radar.ai import AIOutputError
from morning_radar.ai.models import MergedStoryDraft
from morning_radar.ai.provider import AIProvider
from morning_radar.models import PublishedAtRole, RawItem, Story, StorySourceRef
from morning_radar.processing.deduplicate import deduplicate_items
from morning_radar.processing.grouping import group_items_by_normalized_title
from morning_radar.provenance import verified_source_urls, verified_source_urls_for_items

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
LANE_ORDER = (
    "official_editorial",
    "github_release",
    "secondary_editorial",
    "hacker_news",
    "significant_market",
    "other",
)
PUBLISHED_AT_ROLE_BY_SOURCE_TYPE = {
    "rss": PublishedAtRole.FEED_ENTRY_TIME,
    "atom": PublishedAtRole.FEED_ENTRY_TIME,
    "hacker_news": PublishedAtRole.HN_SUBMISSION_TIME,
    "github": PublishedAtRole.GITHUB_RELEASE_PUBLISHED_TIME,
    "market": PublishedAtRole.MARKET_TRADING_DAY,
}
LOGGER = logging.getLogger(__name__)


class StoryValidationError(ValueError):
    pass


def filter_story_candidate_inputs(
    items: list[RawItem],
    *,
    market_movement_threshold: float,
) -> tuple[list[RawItem], int]:
    """Suppress routine market rows from Story AI without dropping raw data."""
    selected: list[RawItem] = []
    suppressed = 0
    for item in items:
        if item.source_type != "market":
            selected.append(item)
            continue
        change = item.metadata.get("change_percent")
        valid_change = (
            isinstance(change, (int, float))
            and not isinstance(change, bool)
            and math.isfinite(change)
        )
        if valid_change and abs(change) < market_movement_threshold:
            suppressed += 1
            continue
        selected.append(item)
    return selected, suppressed


def _story_id(items: list[RawItem]) -> str:
    identity = "|".join(sorted(item.id for item in items))
    return f"story-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


def choose_primary_source(items: list[RawItem]) -> RawItem:
    """Choose a source deterministically; AI does not control canonical links."""

    def source_key(item: RawItem) -> tuple[int, int, int, str]:
        official = bool(item.metadata.get("official"))
        priority = str(item.metadata.get("priority", "low"))
        has_time = item.published_at is not None
        return (
            0 if official else 1,
            PRIORITY_ORDER.get(priority, 3),
            0 if has_time else 1,
            item.url,
        )

    if not items:
        raise ValueError("cannot choose a primary source from no items")
    return min(items, key=source_key)


def _validate_draft_urls(draft: MergedStoryDraft, items: list[RawItem]) -> None:
    allowed = set(verified_source_urls_for_items(items))
    returned = set(draft.source_urls)
    if draft.primary_source_url:
        returned.add(draft.primary_source_url)
    invented = returned - allowed
    if invented:
        raise StoryValidationError(
            "AI story draft contains URL outside verified source set: "
            f"{sorted(invented)[0]}"
        )


def _source_ref(item: RawItem) -> StorySourceRef:
    """Snapshot collector context without broadening verified URL provenance."""
    discussion_url = None
    if item.source_type == "hacker_news":
        candidate = item.metadata.get("discussion_url")
        if isinstance(candidate, str) and candidate in verified_source_urls(item):
            discussion_url = candidate
    return StorySourceRef(
        raw_item_id=item.id,
        title=item.title,
        source_name=item.source_name,
        source_type=item.source_type,
        url=item.url,
        author=item.author,
        # This is the collected source's time (HN submission time for HN),
        # not a claimed original-article or underlying-event time.
        published_at=item.published_at,
        published_at_role=PUBLISHED_AT_ROLE_BY_SOURCE_TYPE.get(
            item.source_type,
            PublishedAtRole.UNKNOWN,
        ),
        fetched_at=item.fetched_at,
        discussion_url=discussion_url,
    )


def build_story(
    items: list[RawItem],
    *,
    provider: AIProvider,
    now: datetime,
) -> Story:
    draft = provider.merge_story(items)
    _validate_draft_urls(draft, items)
    primary = choose_primary_source(items)
    source_urls = list(verified_source_urls_for_items(items))
    published_values = [item.published_at for item in items if item.published_at is not None]
    provisional = Story(
        id=_story_id(items),
        canonical_title=draft.canonical_title,
        category=draft.category,
        entity_names=draft.entity_names,
        product_names=draft.product_names,
        topic_names=draft.topic_names,
        published_at=min(published_values) if published_values else None,
        updated_at=now,
        source_item_ids=[item.id for item in items],
        source_urls=source_urls,
        primary_source_url=primary.url,
        source_refs=[_source_ref(item) for item in items],
        facts=draft.facts,
        analysis=draft.analysis,
        uncertainties=draft.uncertainties,
        relevance_score=0,
        importance_score=0,
        novelty_score=0,
        credibility_score=0,
        status=draft.status,
    )
    score = provider.score_story(provisional)
    return provisional.model_copy(
        update={
            "relevance_score": score.relevance_score,
            "importance_score": score.importance_score,
            "novelty_score": score.novelty_score,
            "credibility_score": score.credibility_score,
        }
    )


def build_stories(
    items: list[RawItem],
    *,
    provider: AIProvider,
    now: datetime,
    maximum_ai_items: int | None = None,
) -> list[Story]:
    if not items:
        LOGGER.info("Skipping AI classification: no recent items")
        return []

    unique = deduplicate_items(items)
    candidates = preselect_ai_candidates(unique, maximum_items=maximum_ai_items)
    if not candidates:
        LOGGER.warning("AI candidate budget left no items for classification")
        return []
    classifications = provider.classify_items(candidates)
    relevant_ids = {item.item_id for item in classifications.items if item.relevant}
    relevant = [item for item in candidates if item.id in relevant_ids]

    stories: list[Story] = []
    for group in group_items_by_normalized_title(relevant):
        try:
            draft = provider.merge_story(group)
        except AIOutputError:
            LOGGER.exception(
                "AI degradation: merge failed; skipping candidate group with %d item(s)",
                len(group),
            )
            continue
        if draft.same_event or len(group) == 1:
            # Reuse a tiny adapter to avoid changing the provider contract.
            try:
                stories.append(
                    build_story(group, provider=_DraftProvider(provider, draft), now=now)
                )
            except AIOutputError:
                LOGGER.exception(
                    "AI degradation: scoring failed; skipping candidate group with %d item(s)",
                    len(group),
                )
        else:
            for item in group:
                try:
                    stories.append(build_story([item], provider=provider, now=now))
                except AIOutputError:
                    LOGGER.exception(
                        "AI degradation: single-item story generation failed; "
                        "skipping item %s",
                        item.id,
                    )
    return rank_stories(stories)


def preselect_ai_candidates(
    items: list[RawItem],
    *,
    maximum_items: int | None,
) -> list[RawItem]:
    """Deterministically prioritize candidates before the first AI call."""
    if maximum_items is None:
        return list(items)
    maximum_items = max(0, maximum_items)

    lanes: dict[str, list[RawItem]] = {name: [] for name in LANE_ORDER}
    for item in items:
        lanes[_candidate_lane(item)].append(item)
    for lane_items in lanes.values():
        lane_items.sort(key=_lane_candidate_key)

    nonempty_lanes = [name for name in LANE_ORDER if lanes[name]]
    selected: list[RawItem] = []
    selected_ids: set[str] = set()
    if maximum_items >= len(nonempty_lanes):
        reserved_lanes = nonempty_lanes
    else:
        reserved_lanes = sorted(
            nonempty_lanes,
            key=lambda name: (
                _global_candidate_key(lanes[name][0]),
                LANE_ORDER.index(name),
            ),
        )[:maximum_items]
    for lane_name in reserved_lanes:
        item = lanes[lane_name][0]
        selected.append(item)
        selected_ids.add(item.id)

    remaining = sorted(
        (item for item in items if item.id not in selected_ids),
        key=_global_candidate_key,
    )
    selected.extend(remaining[: max(0, maximum_items - len(selected))])
    selected_counts = Counter(_candidate_lane(item) for item in selected)
    LOGGER.info(
        "AI candidate lanes: official_editorial=%d github_release=%d "
        "secondary_editorial=%d hacker_news=%d significant_market=%d "
        "other=%d selected_total=%d cap=%d",
        *(selected_counts[name] for name in LANE_ORDER),
        len(selected),
        maximum_items,
    )
    if len(selected) < len(items):
        LOGGER.info(
            "AI candidate cap applied: candidates=%d selected=%d",
            len(items),
            len(selected),
        )
    return selected


def _candidate_lane(item: RawItem) -> str:
    if item.source_type == "github":
        return "github_release"
    if item.source_type == "hacker_news":
        return "hacker_news"
    if item.source_type == "market":
        return "significant_market"
    if item.source_type in {"rss", "atom"}:
        return (
            "official_editorial"
            if item.metadata.get("official")
            else "secondary_editorial"
        )
    return "other"


def _lane_candidate_key(item: RawItem) -> tuple[int, float, str]:
    priority = str(item.metadata.get("priority", "low"))
    event_time = item.published_at or item.fetched_at
    return (PRIORITY_ORDER.get(priority, 3), -event_time.timestamp(), item.id)


def _global_candidate_key(item: RawItem) -> tuple[int, int, float, str]:
    lane_key = _lane_candidate_key(item)
    return (0 if item.metadata.get("official") else 1, *lane_key)


class _DraftProvider:
    """Delegate every task except one already-computed merge result."""

    def __init__(self, provider: AIProvider, draft: MergedStoryDraft) -> None:
        self.provider = provider
        self.draft = draft

    def merge_story(self, items: list[RawItem]) -> MergedStoryDraft:
        del items
        return self.draft

    def score_story(self, story: Story):
        return self.provider.score_story(story)


def ranking_score(story: Story) -> float:
    return (
        story.importance_score * 0.4
        + story.relevance_score * 0.3
        + story.credibility_score * 0.2
        + story.novelty_score * 0.1
    )


def rank_stories(stories: list[Story]) -> list[Story]:
    return sorted(
        stories,
        key=lambda story: (ranking_score(story), story.updated_at, story.id),
        reverse=True,
    )

"""Frozen pre-B0.5 Story pipeline used only for offline comparison tests."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Protocol

from morning_radar.ai import AIOutputError
from morning_radar.ai.models import ClassificationBatch, MergedStoryDraft, StoryScore
from morning_radar.models import RawItem, Story
from morning_radar.processing.deduplicate import deduplicate_items
from morning_radar.processing.grouping import group_items_by_normalized_title
from morning_radar.processing.story_builder import build_story, rank_stories

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
LANE_ORDER = (
    "official_primary",
    "github_release",
    "trusted_practitioner",
    "practice_discovery",
    "secondary_editorial",
    "hacker_news_ambient",
    "significant_market",
    "other",
)
LOGGER = logging.getLogger(__name__)


class LegacyAIProvider(Protocol):
    """Contract retained solely to reproduce the pre-B0.5 architecture."""

    def classify_items(self, items: list[RawItem]) -> ClassificationBatch: ...

    def merge_story(self, items: list[RawItem]) -> MergedStoryDraft: ...

    def score_story(self, story: Story) -> StoryScore: ...


def build_stories(
    items: list[RawItem],
    *,
    provider: LegacyAIProvider,
    now: datetime,
    maximum_ai_items: int | None = None,
) -> list[Story]:
    """Run the frozen legacy classify/merge path outside production runtime."""
    if not items:
        LOGGER.info("Skipping AI classification: no recent items (legacy evaluation)")
        return []
    unique = deduplicate_items(items)
    candidates = preselect_ai_candidates(unique, maximum_items=maximum_ai_items)
    if not candidates:
        LOGGER.warning("Legacy AI candidate budget left no items for classification")
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
                "AI degradation: merge failed in legacy evaluation for %d item(s)",
                len(group),
            )
            continue
        if draft.same_event or len(group) == 1:
            try:
                stories.append(
                    build_story(group, provider=_DraftProvider(provider, draft), now=now)
                )
            except AIOutputError:
                LOGGER.exception("Legacy scoring failed for %d item(s)", len(group))
        else:
            for item in group:
                try:
                    stories.append(build_story([item], provider=provider, now=now))
                except AIOutputError:
                    LOGGER.exception("Legacy Story failed for item %s", item.id)
    return rank_stories(stories)


def preselect_ai_candidates(
    items: list[RawItem], *, maximum_items: int | None
) -> list[RawItem]:
    """Frozen deterministic pre-B0.5 cap used by offline comparisons."""
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
        "Legacy candidate lanes: %s selected_total=%d cap=%d",
        {name: selected_counts[name] for name in LANE_ORDER},
        len(selected),
        maximum_items,
    )
    return selected


def _candidate_lane(item: RawItem) -> str:
    if item.source_type == "github":
        return "github_release"
    if item.source_role.value == "practitioner":
        return "trusted_practitioner"
    if item.source_role.value == "upstream_discovery":
        return "practice_discovery"
    if item.source_type == "hacker_news":
        return (
            "practice_discovery"
            if item.metadata.get("selection_reason") == "high_signal_discovery"
            or item.practice_signal_kind is not None
            else "hacker_news_ambient"
        )
    if item.source_type == "market":
        return "significant_market"
    if item.source_type in {"rss", "atom"}:
        return (
            "official_primary"
            if item.source_role.value == "official_primary"
            or item.metadata.get("official")
            else "secondary_editorial"
        )
    return "other"


def _lane_candidate_key(item: RawItem) -> tuple[int, int, int, float, str]:
    priority = str(item.metadata.get("priority", "low"))
    event_time = item.published_at or item.fetched_at
    score = item.metadata.get("score", 0)
    comments = item.metadata.get("comments", 0)
    community_score = int(score) if isinstance(score, (int, float)) else 0
    community_comments = int(comments) if isinstance(comments, (int, float)) else 0
    return (
        PRIORITY_ORDER.get(priority, 3),
        -community_score,
        -community_comments,
        -event_time.timestamp(),
        item.id,
    )


def _global_candidate_key(item: RawItem) -> tuple[int, int, int, int, float, str]:
    lane_key = _lane_candidate_key(item)
    return (0 if _candidate_lane(item) == "official_primary" else 1, *lane_key)


class _DraftProvider:
    def __init__(self, provider: LegacyAIProvider, draft: MergedStoryDraft) -> None:
        self.provider = provider
        self.draft = draft

    def merge_story(self, items: list[RawItem]) -> MergedStoryDraft:
        del items
        return self.draft

    def score_story(self, story: Story):
        return self.provider.score_story(story)

"""Build traceable Story objects from classified, deduplicated RawItems."""

from __future__ import annotations

import hashlib
import logging
import math
from collections import Counter
from datetime import datetime

from morning_radar.ai import AIBudgetExceeded, AIOutputError
from morning_radar.ai.models import ClassificationBatch, MergedStoryDraft
from morning_radar.ai.provider import AIProvider
from morning_radar.ai.request_payload import get_call_meta, story_materials
from morning_radar.models import PublishedAtRole, RawItem, Story, StorySourceRef
from morning_radar.models.core import TechnicalQuoteNormalization, TechnicalReading
from morning_radar.processing.attention import attention_key
from morning_radar.processing.deduplicate import _comparison_url, deduplicate_items
from morning_radar.processing.grouping import _version_markers, bounded_event_groups
from morning_radar.provenance import verified_source_urls, verified_source_urls_for_items

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


def _validate_draft_urls(
    draft: MergedStoryDraft, items: list[RawItem],
) -> tuple[TechnicalReading | None, list[TechnicalQuoteNormalization]]:
    allowed = set(verified_source_urls_for_items(items))
    returned = set(draft.source_urls)
    if draft.primary_source_url:
        returned.add(draft.primary_source_url)
    reading, conversions = draft.technical_reading, []
    if reading is not None:
        from morning_radar.research.materials import validate_technical_reading
        reading, conversions = validate_technical_reading(reading, items)
    from morning_radar.research.materials import validate_analysis
    validate_analysis(draft.grounded_analysis, items)
    invented = returned - allowed
    if invented:
        raise StoryValidationError(
            f"AI story draft contains URL outside verified source set: {sorted(invented)[0]}"
        )

    return reading, conversions


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
        material_provenance=item.metadata.get("material_provenance"),
        # This is the collected source's time (HN submission time for HN),
        # not a claimed original-article or underlying-event time.
        published_at=item.published_at,
        published_at_role=PUBLISHED_AT_ROLE_BY_SOURCE_TYPE.get(
            item.source_type,
            PublishedAtRole.UNKNOWN,
        ),
        fetched_at=item.fetched_at,
        discussion_url=discussion_url,
        source_role=item.source_role,
        statement_type=item.statement_type,
        practice_signal_kind=item.practice_signal_kind,
        backfill_recorded_at=item.metadata.get("backfill_recorded_at"),
        source_date=item.metadata.get("source_date"),
        date_precision=item.metadata.get("date_precision"),
        source_timezone=item.metadata.get("source_timezone"),
        published_date_role=item.metadata.get("published_date_role"),
    )


def title_only_story(
    items: list[RawItem], *, now: datetime, category: str = "other_reading",
) -> Story:
    """Preserve a discovered lead without asking a model to invent material or score facts."""
    if not items or any(item.summary.strip() or item.content_excerpt.strip() for item in items):
        raise ValueError("Title-only fallback requires no source text")
    return unverified_story(items, now=now, category=category,
        reason="目前仅有标题，缺少摘要或原文摘录，具体内容与可用性尚不能确认。")


def input_version_conflicts(items: list[RawItem]) -> list[str]:
    conflicts = []
    for item in items:
        title = _version_markers(item.title)
        body = _version_markers(item.summary + " " + item.content_excerpt)
        if any(len(title[key]) == len(body[key]) == 1
               and title[key].isdisjoint(body[key])
               for key in title.keys() & body.keys()):
            conflicts.append("来源标题与摘录的型号/版本不一致，尚不能选定其中一种表述。"
                             + "原题：" + item.title[:300] + "；来源摘要：" + item.summary[:600])
    return conflicts


def unverified_story(items: list[RawItem], *, now: datetime,
                     reason: str, category: str = "other_reading",
                     title_prefix: str = "待核对线索｜") -> Story:
    """Preserve input identity and links after failure, never reuse rejected prose."""
    primary = choose_primary_source(items)
    dates = [item.published_at for item in items if item.published_at is not None]
    return Story(
        id=_story_id(items), canonical_title=(title_prefix + primary.title)[:500],
        lead_reason=reason,
        category=category,
        published_at=min(dates) if dates else None, updated_at=now,
        source_item_ids=[item.id for item in items],
        source_urls=list(verified_source_urls_for_items(items)), primary_source_url=primary.url,
        source_refs=[_source_ref(item) for item in items], facts=[],
        uncertainties=[reason],
        relevance_score=0, importance_score=0, novelty_score=0, credibility_score=0,
    )


def build_story(
    items: list[RawItem],
    *,
    provider: AIProvider,
    now: datetime,
) -> Story:
    if items and not any(item.summary.strip() or item.content_excerpt.strip() for item in items):
        return title_only_story(items, now=now)
    conflicts = input_version_conflicts(items)
    if conflicts:
        return unverified_story(items, now=now, reason="；".join(conflicts),
                                title_prefix="来源型号表述冲突，待核对｜")
    draft = provider.merge_story(items)
    reading, conversions = _validate_draft_urls(draft, items)
    from morning_radar.research.materials import validate_analysis
    grounded_analysis, analysis_conversions = validate_analysis(draft.grounded_analysis, items)
    materials, omitted = story_materials(items)
    uncertainties = list(draft.uncertainties)
    if draft.technical_reading is not None and reading is None:
        missing = [label for name, label in (("problem", "问题说明"), ("method", "方法概述"))
                   if getattr(draft.technical_reading, name) is None]
        uncertainties.append(
            "技术阅读卡未展示：模型输出缺少有来源依据的" + "、".join(missing) + "。"
        )
    if draft.analysis:
        uncertainties.append("部分分析未附可追溯的来源前提，未纳入本次分析。")
    # Collector metadata/title alone cannot substantiate a factual article.
    has_material = any(item.summary.strip() or item.content_excerpt.strip() for item in items)
    if not has_material and draft.facts:
        uncertainties.append("仅有标题与采集元数据，缺少可支撑事实正文的摘要或原文摘录。")
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
        technical_reading=reading,
        technical_quote_normalizations=conversions,
        evidence_ids=[str(item.metadata["evidence_id"]) for item in items
                      if item.metadata.get("evidence_id")],
        facts=draft.facts if has_material else [],
        analysis=[],
        opinions=draft.opinions,
        grounded_analysis=grounded_analysis,
        analysis_quote_normalizations=analysis_conversions,
        source_materials=materials,
        source_materials_omitted=omitted,
        uncertainties=uncertainties,
        relevance_score=0,
        importance_score=0,
        novelty_score=0,
        credibility_score=0,
        status=draft.status,
    )
    score = provider.score_story(provisional)
    scored = provisional.model_copy(
        update={
            "relevance_score": score.relevance_score,
            "importance_score": score.importance_score,
            "novelty_score": score.novelty_score,
            "credibility_score": score.credibility_score,
        }
    )
    scored._score_decision = score
    return scored



def _capture_failed_score_meta(provider, score_out, items) -> None:
    if score_out is None:
        return
    failed = score_out.setdefault("_failed_meta", {})
    meta = getattr(provider, "last_call_meta", None)
    if meta is None:
        inner = getattr(provider, "provider", None)
        meta = getattr(inner, "last_call_meta", None)
    for item in items:
        failed[item.id] = meta


def _merge_with_url_recovery(provider, group):
    # A fabricated URL invalidates this response. Do not silently reassign its
    # prose to another URL or generate again; callers retain the original lead.
    draft = provider.merge_story(group)
    _validate_draft_urls(draft, group)
    return draft


def build_stories(
    items: list[RawItem],
    *,
    provider: AIProvider,
    now: datetime,
    maximum_ai_items: int | None = None,
    item_outcomes: dict[str, str] | None = None,
    classification_out: dict[str, object] | None = None,
    score_out: dict[str, object] | None = None,
    classifications: ClassificationBatch | None = None,
) -> list[Story]:
    from morning_radar.intake.models import ReasonCode

    def note(item_id: str, reason: ReasonCode, *, story_id: str | None = None) -> None:
        if item_outcomes is not None:
            item_outcomes[item_id] = reason.value

    if not items:
        LOGGER.info("Skipping AI classification: no recent items")
        return []

    unique = deduplicate_items(items)
    candidates = preselect_ai_candidates(unique, maximum_items=maximum_ai_items)
    candidate_ids = {item.id for item in candidates}
    for item in unique:
        if item.id not in candidate_ids:
            note(item.id, ReasonCode.DEFERRED_BUDGET)
    if not candidates:
        LOGGER.warning("AI candidate budget left no items for classification")
        return []
    if classifications is None:
        # Keep rich material intact; partition requests instead of truncating evidence.
        from morning_radar.ai.request_payload import classify_items_payload, dumps

        batches: list[list[RawItem]] = []
        batch: list[RawItem] = []
        for item in candidates:
            proposed = [*batch, item]
            if batch and (len(proposed) > 8 or len(dumps(classify_items_payload(
                proposed, getattr(provider, "topic_context", None)
            ))) > 18000):
                batches.append(batch)
                batch = []
            batch.append(item)
        if batch:
            batches.append(batch)
        decisions = []
        for batch in batches:
            try:
                output = provider.classify_items(batch)
                decisions.extend(output.items)
                if classification_out is not None:
                    attempts = classification_out.setdefault("_call_meta_by_item", {})
                    attempts.update({item.id: get_call_meta(output) for item in batch})
            except AIBudgetExceeded:
                for item in batch:
                    note(item.id, ReasonCode.DEFERRED_BUDGET)
            except AIOutputError:
                for item in batch:
                    note(item.id, ReasonCode.CLASSIFICATION_RESPONSE_MISSING)
        classifications = ClassificationBatch(items=decisions)
    classified_by_id = {item.item_id: item for item in classifications.items}
    if classification_out is not None:
        classification_out.update(classified_by_id)
        meta = get_call_meta(classifications)
        if meta is not None:
            classification_out["_call_meta"] = meta
    relevant_ids = {item.item_id for item in classifications.items if item.relevant}
    for item in candidates:
        classified = classified_by_id.get(item.id)
        if classified is None:
            if not item_outcomes or item.id not in item_outcomes:
                note(item.id, ReasonCode.CLASSIFICATION_RESPONSE_MISSING)
        elif item.id not in relevant_ids:
            note(item.id, ReasonCode.CLASSIFIED_IRRELEVANT)

    stories: list[Story] = []
    for group in bounded_event_groups(candidates):
        if not any(item.id in relevant_ids for item in group):
            continue
        conflicts = input_version_conflicts(group)
        if conflicts:
            stories.append(unverified_story(group, now=now, reason="；".join(conflicts),
                                            title_prefix="来源型号表述冲突，待核对｜"))
            for item in group:
                note(item.id, ReasonCode.PROCESSED)
            continue
        # Deduplication allocates one candidate slot, not permission to discard
        # richer observations of the same page. Preserve source roles and IDs;
        # the model still checks whether these observations describe one event.
        group_urls = {_comparison_url(item) for item in group}
        retained_ids = {item.id for item in group}
        group = [*group, *(
            item for item in items
            if item.id not in retained_ids and _comparison_url(item) in group_urls
        )]
        if not any(item.summary.strip() or item.content_excerpt.strip() for item in group):
            stories.append(title_only_story(group, now=now,
                           category=classified_by_id[group[0].id].category))
            for item in group:
                note(item.id, ReasonCode.PROCESSED)
            continue
        try:
            draft = _merge_with_url_recovery(provider, group)
        except AIBudgetExceeded:
            for item in group:
                note(item.id, ReasonCode.DEFERRED_BUDGET)
            continue
        except (AIOutputError, StoryValidationError):
            LOGGER.exception(
                "AI degradation: merge failed; retaining original leads "
                "for candidate group with %d item(s)",
                len(group),
            )
            for item in group:
                note(item.id, ReasonCode.MERGE_FAILED)
                stories.append(unverified_story([item], now=now,
                    reason="生成结果未通过校验；仅保留来源原题与链接，事实正文尚待核对。"))
            continue
        if draft.same_event or len(group) == 1:
            try:
                story = build_story(group, provider=_DraftProvider(provider, draft), now=now)
            except AIBudgetExceeded:
                for item in group:
                    note(item.id, ReasonCode.DEFERRED_BUDGET)
                continue
            except (AIOutputError, StoryValidationError):
                LOGGER.exception(
                    "AI degradation: scoring failed; skipping candidate group with %d item(s)",
                    len(group),
                )
                _capture_failed_score_meta(provider, score_out, group)
                for item in group:
                    note(item.id, ReasonCode.SCORE_FAILED)
                    stories.append(unverified_story([item], now=now,
                        reason="评分或证据校验失败，暂保留来源线索供核对。"))
                continue
            stories.append(story)
            if score_out is not None:
                score_out[story.id] = getattr(story, "_score_decision", None)
            for item in group:
                note(item.id, ReasonCode.MERGED_INTO if len(group) > 1 else ReasonCode.PROCESSED)
        else:
            for item in group:
                try:
                    story = build_story([item], provider=provider, now=now)
                except AIBudgetExceeded:
                    note(item.id, ReasonCode.DEFERRED_BUDGET)
                    continue
                except (AIOutputError, StoryValidationError):
                    LOGGER.exception(
                        "AI degradation: single-item story generation failed; skipping item %s",
                        item.id,
                    )
                    _capture_failed_score_meta(provider, score_out, [item])
                    note(item.id, ReasonCode.SCORE_FAILED)
                    stories.append(unverified_story([item], now=now,
                        reason="生成或评分未通过校验，暂保留来源线索供核对。"))
                    continue
                stories.append(story)
                if score_out is not None:
                    score_out[story.id] = getattr(story, "_score_decision", None)
                note(item.id, ReasonCode.PROCESSED)
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
        "AI candidate lanes: official_primary=%d github_release=%d "
        "trusted_practitioner=%d practice_discovery=%d secondary_editorial=%d "
        "hacker_news_ambient=%d significant_market=%d other=%d "
        "selected_total=%d cap=%d",
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
    if item.source_role.value == "practitioner":
        return "trusted_practitioner"
    if item.source_role.value == "upstream_discovery":
        return "practice_discovery"
    if item.source_type == "hacker_news":
        return (
            "practice_discovery"
            if item.metadata.get("selection_reason") == "high_signal_discovery"
            or "high_signal_discovery" in item.metadata.get("discovery_reasons", [])
            or item.practice_signal_kind is not None
            else "hacker_news_ambient"
        )
    if item.source_type == "market":
        return "significant_market"
    if item.source_type in {"rss", "atom", "official_changelog"}:
        return (
            "official_primary"
            if item.source_role.value == "official_primary" or item.metadata.get("official")
            else "secondary_editorial"
        )
    return "other"


def _lane_candidate_key(item: RawItem) -> tuple:
    priority = str(item.metadata.get("priority", "low"))
    event_time = item.published_at or item.fetched_at
    return (
        PRIORITY_ORDER.get(priority, 3),
        attention_key(item.metadata.get("score")),
        attention_key(item.metadata.get("comments")),
        -event_time.timestamp(),
        item.id,
    )


def _global_candidate_key(item: RawItem) -> tuple:
    lane_key = _lane_candidate_key(item)
    return (0 if _candidate_lane(item) == "official_primary" else 1, *lane_key)


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

"""Turn validated Stories and Signals into a bounded DailyBrief."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime

from morning_radar.ai import AIOutputError
from morning_radar.ai.models import (
    BriefDraft,
    GeneratedBriefItem,
    GeneratedJudgementDraft,
    GeneratedWatchDraft,
)
from morning_radar.ai.output_validation import sanitize_memory_drafts
from morning_radar.ai.provider import AIProvider
from morning_radar.models import BriefItem, BriefStoryContext, DailyBrief, Signal, Story

SECTION_NAMES = (
    "top_stories",
    "market_and_companies",
    "ai_and_open_source",
    "trend_radar",
    "developer_discussions",
)
LOGGER = logging.getLogger(__name__)


class BriefValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BriefLimits:
    maximum_items: int
    top_story_items: int = 3
    other_reading_items: int = 6


@dataclass(frozen=True, slots=True)
class BriefGenerationResult:
    brief: DailyBrief
    watch_drafts: list[GeneratedWatchDraft]
    judgement_drafts: list[GeneratedJudgementDraft]


def ranked_eligible_stories(
    stories: list[Story],
    *,
    relevance_threshold: float,
    importance_threshold: float,
) -> list[Story]:
    eligible = [
        story for story in stories if story.relevance_score >= relevance_threshold
    ]
    eligible.sort(
        key=lambda story: (
            story.importance_score >= importance_threshold,
            story.importance_score,
            story.relevance_score,
        ),
        reverse=True,
    )
    return eligible


def _brief_item_id(story_ids: list[str], section: str) -> str:
    identity = f"{section}:{'|'.join(sorted(story_ids))}"
    return f"brief-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


def _story_context(story: Story) -> BriefStoryContext:
    """Copy display context from a validated Story without another AI call."""
    return BriefStoryContext(
        story_id=story.id,
        canonical_title=story.canonical_title,
        category=story.category,
        entity_names=story.entity_names,
        product_names=story.product_names,
        topic_names=story.topic_names,
        published_at=story.published_at,
        facts=story.facts,
        analysis=story.analysis,
        uncertainties=story.uncertainties,
        status=story.status,
        primary_source_url=story.primary_source_url,
        source_refs=story.source_refs,
    )


def _deterministic_generated_item(
    story: Story,
    *,
    section: str,
    fallback: bool = False,
) -> GeneratedBriefItem:
    """Create a conservative, schema-valid item from an already verified Story."""
    return GeneratedBriefItem(
        story_ids=[story.id],
        section=section,
        title=story.canonical_title,
        what_happened=story.facts[0] if story.facts else story.canonical_title,
        why_it_matters=(
            "降级模式下暂时无法生成重要性分析，请查看已验证事实与来源。"
            if fallback or not story.analysis
            else (
                story.analysis[0]
            )
        ),
        uncertainty=(
            "AI 晨报分析暂时不可用。"
            if fallback
            else (story.uncertainties[0] if story.uncertainties else None)
        ),
        source_urls=story.source_urls,
    )


def _validated_item(
    generated: GeneratedBriefItem,
    *,
    story_by_id: dict[str, Story],
    section: str,
) -> BriefItem:
    if not generated.story_ids or any(
        story_id not in story_by_id for story_id in generated.story_ids
    ):
        raise BriefValidationError("Brief item references an unknown Story ID")
    allowed_urls = {
        url
        for story_id in generated.story_ids
        for url in story_by_id[story_id].source_urls
    }
    if not generated.source_urls or not set(generated.source_urls).issubset(allowed_urls):
        raise BriefValidationError("Brief item URL is missing or not present in its Stories")
    return BriefItem(
        id=_brief_item_id(generated.story_ids, section),
        section=section,
        title=generated.title,
        what_happened=generated.what_happened,
        why_it_matters=generated.why_it_matters,
        market_or_community_reaction=generated.market_or_community_reaction,
        uncertainty=generated.uncertainty,
        source_urls=generated.source_urls,
        story_ids=generated.story_ids,
        story_contexts=[_story_context(story_by_id[story_id]) for story_id in generated.story_ids],
    )


def generate_daily_brief_with_memory(
    *,
    brief_date: date,
    generated_at: datetime,
    timezone: str,
    stories: list[Story],
    signals: list[Signal],
    provider: AIProvider,
    limits: BriefLimits,
    enabled_sections: dict[str, bool],
    run_stats: dict[str, int | float | str | bool],
    relevance_threshold: float = 0,
    importance_threshold: float = 0,
    maximum_ai_items: int | None = None,
) -> BriefGenerationResult:
    stats = dict(run_stats)
    eligible_stories = ranked_eligible_stories(
        stories,
        relevance_threshold=relevance_threshold,
        importance_threshold=importance_threshold,
    )
    ai_stories = eligible_stories[: limits.maximum_items]
    stats["threshold_eligible_stories"] = len(eligible_stories)
    stats["ai_brief_story_inputs"] = len(ai_stories)
    bounded_signals = sorted(
        signals,
        key=lambda signal: (signal.strength, signal.id),
        reverse=True,
    )
    if maximum_ai_items is not None:
        bounded_signals = bounded_signals[:maximum_ai_items]
    stats["ai_signal_inputs"] = len(bounded_signals)
    direction_signals = [
        signal
        for signal in bounded_signals
        if len(set(signal.supporting_story_ids)) >= 2
        and signal.supporting_source_count >= 2
    ]
    stats["direction_signal_inputs"] = len(direction_signals)

    if ai_stories:
        try:
            draft = provider.write_brief(ai_stories, bounded_signals)
            draft = sanitize_memory_drafts(draft, ai_stories)
        except AIOutputError:
            LOGGER.exception(
                "AI degradation: brief generation failed; using verified Story facts"
            )
            stats["ai_brief_fallback"] = True
            draft = _fallback_brief_draft(ai_stories)
    else:
        LOGGER.info("Skipping AI brief generation: no stories")
        draft = BriefDraft(items=[])
    story_by_id = {story.id: story for story in eligible_stories}
    sections: dict[str, list[BriefItem]] = {name: [] for name in SECTION_NAMES}
    used_story_ids: set[str] = set()
    main_item_count = 0

    for generated in draft.items:
        if main_item_count >= limits.maximum_items:
            break
        if any(story_id in used_story_ids for story_id in generated.story_ids):
            continue
        section = generated.section if generated.section in sections else "top_stories"
        referenced = [
            story_by_id[story_id]
            for story_id in generated.story_ids
            if story_id in story_by_id
        ]
        important = bool(referenced) and any(
            story.importance_score >= importance_threshold for story in referenced
        )
        top_enabled = enabled_sections.get("top_stories", True)
        if top_enabled and important and len(sections["top_stories"]) < limits.top_story_items:
            section = "top_stories"
        elif section == "top_stories":
            proposed = referenced[0].category if referenced else "ai_and_open_source"
            section = (
                proposed
                if proposed in sections and proposed != "top_stories"
                else "ai_and_open_source"
            )
        if not enabled_sections.get(section, True):
            continue
        item = _validated_item(generated, story_by_id=story_by_id, section=section)
        sections[section].append(item)
        used_story_ids.update(item.story_ids)
        main_item_count += 1

    other_reading: list[BriefItem] = []
    remaining_capacity = max(0, limits.maximum_items - main_item_count)
    other_reading_capacity = min(limits.other_reading_items, remaining_capacity)
    for story in eligible_stories:
        if len(other_reading) >= other_reading_capacity:
            break
        if story.id in used_story_ids:
            continue
        generated = _deterministic_generated_item(story, section="other_reading")
        other_reading.append(
            _validated_item(
                generated,
                story_by_id=story_by_id,
                section="other_reading",
            )
        )
        used_story_ids.add(story.id)

    direction = None
    if direction_signals and enabled_sections.get("direction_observation", True):
        try:
            direction = provider.write_direction_observation(direction_signals).observation
        except AIOutputError:
            LOGGER.exception(
                "AI degradation: direction observation failed; section omitted"
            )
            stats["ai_direction_fallback"] = True
    elif not direction_signals:
        LOGGER.info("Skipping AI direction observation: no coherent evidence signals")
    cognitive_extension = (
        draft.cognitive_extension
        if enabled_sections.get("cognitive_extension", True)
        else None
    )
    return BriefGenerationResult(
        brief=DailyBrief(
            date=brief_date,
            timezone=timezone,
            generated_at=generated_at,
            top_stories=sections["top_stories"],
            market_and_companies=sections["market_and_companies"],
            ai_and_open_source=sections["ai_and_open_source"],
            trend_radar=sections["trend_radar"],
            developer_discussions=sections["developer_discussions"],
            other_reading=other_reading,
            direction_observation=direction,
            cognitive_extension=cognitive_extension,
            # v0.3 display is projected from the same validated structured Watch
            # source. ``watch_next`` remains on the schema only for old JSON.
            watch_next=[watch.expectation for watch in draft.watch_items],
            run_stats=stats,
        ),
        watch_drafts=draft.watch_items,
        judgement_drafts=draft.judgements,
    )


def generate_daily_brief(
    *,
    brief_date: date,
    generated_at: datetime,
    timezone: str,
    stories: list[Story],
    signals: list[Signal],
    provider: AIProvider,
    limits: BriefLimits,
    enabled_sections: dict[str, bool],
    run_stats: dict[str, int | float | str | bool],
    relevance_threshold: float = 0,
    importance_threshold: float = 0,
    maximum_ai_items: int | None = None,
) -> DailyBrief:
    """Compatibility wrapper for callers that only need the display Brief."""
    return generate_daily_brief_with_memory(
        brief_date=brief_date,
        generated_at=generated_at,
        timezone=timezone,
        stories=stories,
        signals=signals,
        provider=provider,
        limits=limits,
        enabled_sections=enabled_sections,
        run_stats=run_stats,
        relevance_threshold=relevance_threshold,
        importance_threshold=importance_threshold,
        maximum_ai_items=maximum_ai_items,
    ).brief


def _fallback_brief_draft(stories: list[Story]) -> BriefDraft:
    """Build a schema-valid draft without inventing analysis or new facts."""
    return BriefDraft(
        items=[
            _deterministic_generated_item(
                story,
                section=story.category,
                fallback=True,
            )
            for story in stories
        ]
    )

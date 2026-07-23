"""Turn validated Stories and Signals into a bounded DailyBrief."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime

from morning_radar.ai.models import GeneratedBriefItem
from morning_radar.ai.provider import AIProvider
from morning_radar.models import BriefItem, DailyBrief, Signal, Story

SECTION_NAMES = (
    "top_stories",
    "market_and_companies",
    "ai_and_open_source",
    "trend_radar",
    "developer_discussions",
)


class BriefValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BriefLimits:
    maximum_items: int
    top_story_items: int = 3


def _brief_item_id(story_ids: list[str], section: str) -> str:
    identity = f"{section}:{'|'.join(sorted(story_ids))}"
    return f"brief-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


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
) -> DailyBrief:
    draft = provider.write_brief(stories, signals)
    story_by_id = {story.id: story for story in stories}
    sections: dict[str, list[BriefItem]] = {name: [] for name in SECTION_NAMES}
    used_story_ids: set[str] = set()

    for generated in draft.items:
        if len(used_story_ids) >= limits.maximum_items:
            break
        if any(story_id in used_story_ids for story_id in generated.story_ids):
            continue
        section = generated.section if generated.section in sections else "top_stories"
        if len(sections["top_stories"]) < limits.top_story_items:
            section = "top_stories"
        if not enabled_sections.get(section, True):
            continue
        item = _validated_item(generated, story_by_id=story_by_id, section=section)
        sections[section].append(item)
        used_story_ids.update(item.story_ids)

    direction = None
    if signals and enabled_sections.get("direction_observation", True):
        direction = provider.write_direction_observation(signals).observation
    cognitive_extension = (
        draft.cognitive_extension
        if enabled_sections.get("cognitive_extension", True)
        else None
    )
    return DailyBrief(
        date=brief_date,
        timezone=timezone,
        generated_at=generated_at,
        top_stories=sections["top_stories"],
        market_and_companies=sections["market_and_companies"],
        ai_and_open_source=sections["ai_and_open_source"],
        trend_radar=sections["trend_radar"],
        developer_discussions=sections["developer_discussions"],
        direction_observation=direction,
        cognitive_extension=cognitive_extension,
        watch_next=draft.watch_next,
        run_stats=run_stats,
    )


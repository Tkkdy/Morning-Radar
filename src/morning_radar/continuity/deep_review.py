"""Rare deterministic triggers for standalone deep Judgement review."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlsplit

from morning_radar.continuity.candidates import StoryMemory
from morning_radar.models import CurrentJudgement, JudgementViewState

_TENSION_MARKERS = (
    "不再",
    "撤回",
    "正式否认",
    "推翻",
    "而非",
    "并未",
    "不同于",
    "漏洞修复",
    "修复方法",
    "修复方式",
    "更强的防护",
    "停止支持",
    "不再提供",
    "contradicted",
    "retracted",
    "discontinued",
)


@dataclass(frozen=True, slots=True)
class DeepReviewTrigger:
    judgement_id: str
    reason: str
    story_memory: list[StoryMemory] = field(default_factory=list)


def _relevant(memory: StoryMemory, judgement: CurrentJudgement) -> bool:
    claim = judgement.latest_record.claim.casefold()
    anchors = [*memory.story.entity_names, *memory.story.product_names]
    if any(value.casefold() in claim for value in anchors if len(value) >= 3):
        return True
    claim_tokens = set(re.findall(r"[a-z0-9][a-z0-9._-]{3,}", claim))
    story_text = " ".join([memory.story.canonical_title, *memory.story.facts]).casefold()
    return bool(claim_tokens.intersection(re.findall(r"[a-z0-9][a-z0-9._-]{3,}", story_text)))


def scan_deep_review_triggers(
    *,
    current_date: date,
    judgements: dict[str, CurrentJudgement],
    story_memory: list[StoryMemory],
    window_days: int = 21,
    minimum_stories: int = 4,
    minimum_dates: int = 3,
    minimum_sources: int = 3,
) -> list[DeepReviewTrigger]:
    """Return conservative review candidates; an empty result costs zero AI calls."""
    oldest = current_date - timedelta(days=window_days)
    triggers: list[DeepReviewTrigger] = []
    for view in judgements.values():
        if view.state is JudgementViewState.NEEDS_REVIEW:
            triggers.append(
                DeepReviewTrigger(
                    judgement_id=view.latest_record.judgement_id,
                    reason="dependency_changed",
                )
            )
            continue
        relevant = [
            memory
            for memory in story_memory
            if (
                oldest <= memory.ref.date <= current_date
                and memory.ref.date > view.latest_record.recorded_at.date()
                and _relevant(memory, view)
            )
        ]
        tension = [
            memory
            for memory in relevant
            if memory.ref.date == current_date
            and any(
                marker in " ".join(memory.story.facts).casefold() for marker in _TENSION_MARKERS
            )
        ]
        if tension:
            triggers.append(
                DeepReviewTrigger(
                    judgement_id=view.latest_record.judgement_id,
                    reason="counterevidence_tension",
                    story_memory=tension,
                )
            )
            continue
        dates = {memory.ref.date for memory in relevant}
        prior_relevant = [memory for memory in relevant if memory.ref.date < current_date]
        prior_dates = {memory.ref.date for memory in prior_relevant}
        sources = {
            urlsplit(url).hostname
            for memory in relevant
            for url in memory.story.source_urls
            if urlsplit(url).hostname
        }
        prior_sources = {
            urlsplit(url).hostname
            for memory in prior_relevant
            for url in memory.story.source_urls
            if urlsplit(url).hostname
        }
        crossed_threshold = not (
            len(prior_relevant) >= minimum_stories
            and len(prior_dates) >= minimum_dates
            and len(prior_sources) >= minimum_sources
        )
        if (
            crossed_threshold
            and current_date in dates
            and len(relevant) >= minimum_stories
            and len(dates) >= minimum_dates
            and len(sources) >= minimum_sources
        ):
            triggers.append(
                DeepReviewTrigger(
                    judgement_id=view.latest_record.judgement_id,
                    reason="multi_date_multi_source_accumulation",
                    story_memory=relevant,
                )
            )
    return triggers

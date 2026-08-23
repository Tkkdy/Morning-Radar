"""Evaluate a complete bounded Story batch and derive deterministic reader selection."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from morning_radar.ai import AIBudgetExceeded, AIOutputError
from morning_radar.ai.provider import AIProvider
from morning_radar.editorial.fallback import degraded_editorial_batch
from morning_radar.editorial.models import (
    DailyEditorialDecisions,
    EditorialDecision,
    EditorialDecisionBatch,
    FactStatus,
    Placement,
)
from morning_radar.models import SourceRole, StatementType, Story

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReaderSelection:
    top_story_ids: list[str] = field(default_factory=list)
    main_story_ids: list[str] = field(default_factory=list)
    other_reading_story_ids: list[str] = field(default_factory=list)
    support_by_story_id: dict[str, list[str]] = field(default_factory=dict)

    @property
    def visible_story_ids(self) -> list[str]:
        return [
            *self.top_story_ids,
            *self.main_story_ids,
            *self.other_reading_story_ids,
        ]


@dataclass(frozen=True, slots=True)
class EditorialRunResult:
    daily: DailyEditorialDecisions
    selection: ReaderSelection | None = None

    @property
    def active(self) -> bool:
        return (
            self.daily.enabled
            and not self.daily.shadow_mode
            and not self.daily.degraded
            and self.selection is not None
        )


def _has_independent_verification(story: Story) -> bool:
    if len(story.source_refs) < 2 or len(set(story.source_urls)) < 2:
        return False
    return any(
        ref.source_role is not SourceRole.OFFICIAL_PRIMARY
        and ref.statement_type
        in {StatementType.FIRSTHAND_OBSERVATION, StatementType.TEST_EXPERIMENT}
        for ref in story.source_refs
    )


def validate_editorial_batch(
    batch: EditorialDecisionBatch,
    stories: list[Story],
) -> EditorialDecisionBatch:
    story_by_id = {story.id: story for story in stories}
    decision_by_id: dict[str, EditorialDecision] = {}
    for decision in batch.decisions:
        if decision.story_id in decision_by_id:
            raise ValueError("Editorial output contains duplicate Story IDs")
        if decision.story_id not in story_by_id:
            raise ValueError("Editorial output references an unknown Story ID")
        decision_by_id[decision.story_id] = decision
    if set(decision_by_id) != set(story_by_id):
        raise ValueError("Editorial output must contain exactly one decision per Story")

    for decision in batch.decisions:
        story = story_by_id[decision.story_id]
        independently_verified = _has_independent_verification(story)
        if decision.fact_status is FactStatus.VERIFIED_FACT and not story.source_refs:
            raise ValueError("verified_fact requires explicit input source evidence")
        if (
            decision.context_snapshot is not None
            and decision.context_snapshot.independently_verified is True
            and not independently_verified
        ):
            raise ValueError("context cannot claim independent verification without evidence")
        if decision.placement is not Placement.SUPPORT:
            continue
        target_id = decision.support_for_story_id
        if target_id not in decision_by_id:
            raise ValueError("SUPPORT target must exist in the same editorial batch")
        if target_id == decision.story_id:
            raise ValueError("SUPPORT cannot reference itself")
        target = decision_by_id[target_id]
        if target.placement in {Placement.SUPPORT, Placement.DROP}:
            raise ValueError("SUPPORT cannot target SUPPORT or DROP")
    return batch


def select_reader_stories(
    stories: list[Story],
    decisions: list[EditorialDecision],
) -> ReaderSelection:
    original_order = {story.id: index for index, story in enumerate(stories)}
    by_placement: dict[Placement, list[EditorialDecision]] = {
        placement: [] for placement in Placement
    }
    for decision in decisions:
        by_placement[decision.placement].append(decision)
    for placement_decisions in by_placement.values():
        placement_decisions.sort(
            key=lambda decision: (
                -decision.reader_value,
                original_order[decision.story_id],
                decision.story_id,
            )
        )
    support_by_story_id: dict[str, list[str]] = {}
    for decision in by_placement[Placement.SUPPORT]:
        assert decision.support_for_story_id is not None
        support_by_story_id.setdefault(decision.support_for_story_id, []).append(
            decision.story_id
        )
    return ReaderSelection(
        top_story_ids=[item.story_id for item in by_placement[Placement.TOP]],
        main_story_ids=[item.story_id for item in by_placement[Placement.STORY]],
        other_reading_story_ids=[
            *(item.story_id for item in by_placement[Placement.NEWS]),
            *(item.story_id for item in by_placement[Placement.ONE_LINER]),
        ],
        support_by_story_id=support_by_story_id,
    )


def evaluate_editorial(
    stories: list[Story],
    *,
    provider: AIProvider,
    current_date: date,
    generated_at: datetime,
    enabled: bool,
    shadow_mode: bool,
    profile_version: str,
    maximum_stories: int,
) -> EditorialRunResult:
    if not enabled:
        return EditorialRunResult(
            daily=DailyEditorialDecisions(
                date=current_date,
                generated_at=generated_at,
                profile_version=profile_version,
                enabled=False,
                shadow_mode=shadow_mode,
            )
        )
    if len(stories) > maximum_stories:
        LOGGER.error(
            "Editorial degradation: complete Story batch exceeds limit stories=%d limit=%d",
            len(stories),
            maximum_stories,
        )
        return EditorialRunResult(
            daily=degraded_editorial_batch(
                current_date=current_date,
                generated_at=generated_at,
                profile_version=profile_version,
                shadow_mode=shadow_mode,
                reason="story_limit_exceeded",
            )
        )
    if not stories:
        return EditorialRunResult(
            daily=DailyEditorialDecisions(
                date=current_date,
                generated_at=generated_at,
                profile_version=profile_version,
                enabled=True,
                shadow_mode=shadow_mode,
            ),
            selection=select_reader_stories([], []),
        )
    try:
        batch = validate_editorial_batch(provider.evaluate_editorial(stories), stories)
    except (AIOutputError, AIBudgetExceeded, OSError, ValueError):
        LOGGER.exception("Editorial degradation: batch evaluation failed; using legacy brief")
        return EditorialRunResult(
            daily=degraded_editorial_batch(
                current_date=current_date,
                generated_at=generated_at,
                profile_version=profile_version,
                shadow_mode=shadow_mode,
                reason="provider_or_validation_failure",
            )
        )
    daily = DailyEditorialDecisions(
        date=current_date,
        generated_at=generated_at,
        profile_version=profile_version,
        enabled=True,
        shadow_mode=shadow_mode,
        decisions=batch.decisions,
    )
    return EditorialRunResult(
        daily=daily,
        selection=select_reader_stories(stories, batch.decisions),
    )

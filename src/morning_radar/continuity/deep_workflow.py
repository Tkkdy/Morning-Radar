"""Standalone, trigger-only deep Judgement review workflow."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from morning_radar.ai import AIBudget, AIBudgetExceeded, AIOutputError, DeepSeekProvider
from morning_radar.ai.models import (
    ContinuityResolutionInput,
    ContinuityStorySummary,
    PriorJudgementInput,
)
from morning_radar.ai.provider import AIProvider
from morning_radar.continuity.deep_review import scan_deep_review_triggers
from morning_radar.continuity.history import load_continuity_history, load_story_memory
from morning_radar.continuity.materialize import merge_daily_continuity
from morning_radar.continuity.reducer import reduce_judgements
from morning_radar.continuity.validation import validate_continuity_resolution
from morning_radar.models import DailyContinuity, JudgementRecord
from morning_radar.settings import AppConfig, load_model
from morning_radar.storage import save_model
from morning_radar.time_utils import display_date, utc_now


@dataclass(frozen=True, slots=True)
class DeepContinuityResult:
    daily: DailyContinuity
    stats: dict[str, int | str] = field(default_factory=dict)


def _summary(memory) -> ContinuityStorySummary:
    return ContinuityStorySummary(
        ref=memory.ref,
        canonical_title=memory.story.canonical_title,
        facts=memory.story.facts,
        entity_names=memory.story.entity_names,
        product_names=memory.story.product_names,
        topic_names=memory.story.topic_names,
        status=memory.story.status,
    )


def run_deep_continuity_workflow(
    root: Path,
    *,
    provider: AIProvider | None = None,
    current_date: date | None = None,
    generated_at: datetime | None = None,
) -> DeepContinuityResult:
    root = root.resolve()
    app = load_model(root / "config/app.yaml", AppConfig)
    now = generated_at or utc_now()
    day = current_date or display_date(now)
    history = load_continuity_history(root, current_date=day)
    judgements = reduce_judgements(history)
    memories = load_story_memory(
        root,
        current_date=day + timedelta(days=1),
        history_days=app.deep_review_window_days + 1,
    )
    triggers = scan_deep_review_triggers(
        current_date=day,
        judgements=judgements,
        story_memory=memories,
        window_days=app.deep_review_window_days,
        minimum_stories=app.deep_review_minimum_stories,
        minimum_dates=app.deep_review_minimum_dates,
        minimum_sources=app.deep_review_minimum_sources,
    )
    existing = next((item for item in history if item.date == day), None)
    empty = existing or DailyContinuity(date=day, generated_at=now)
    reviewable = [trigger for trigger in triggers if trigger.story_memory]
    if not reviewable:
        return DeepContinuityResult(
            daily=empty,
            stats={
                "judgement_deep_review_triggers": len(triggers),
                "judgement_deep_review_calls": 0,
                "deep_continuity_status": "no_ai_trigger",
            },
        )
    active_provider = provider or DeepSeekProvider.from_environment(
        budget=AIBudget(5, app.maximum_continuity_input_characters, app.maximum_ai_items, 6),
        prompt_dir=root / "prompts",
    )
    by_latest = {view.latest_record.judgement_id: view for view in judgements.values()}
    updates: list[JudgementRecord] = []
    calls = 0
    for trigger in reviewable:
        view = by_latest[trigger.judgement_id]
        context = ContinuityResolutionInput(
            prior_hypotheses=[
                PriorJudgementInput(
                    judgement_id=view.latest_record.judgement_id,
                    root_judgement_id=view.root_judgement_id,
                    claim=view.latest_record.claim,
                    rationale=view.latest_record.rationale,
                    uncertainty=view.latest_record.uncertainty,
                    current_story_candidates=[_summary(item) for item in trigger.story_memory],
                )
            ]
        )
        try:
            output = active_provider.resolve_continuity(context)
            validate_continuity_resolution(output, context)
            calls += 1
        except (AIOutputError, AIBudgetExceeded, ValueError):
            continue
        for draft in output.judgement_updates:
            identity = "|".join(
                [
                    view.root_judgement_id,
                    str(day),
                    draft.update_kind.value,
                    *(ref.story.story_id for ref in draft.evidence_refs),
                ]
            )
            updates.append(
                JudgementRecord(
                    judgement_id=f"judgement-{hashlib.sha256(identity.encode()).hexdigest()[:20]}",
                    root_judgement_id=view.root_judgement_id,
                    recorded_at=now,
                    claim=draft.claim,
                    rationale=draft.rationale,
                    evidence_refs=draft.evidence_refs,
                    uncertainty=draft.uncertainty,
                    watch_ids=view.latest_record.watch_ids,
                    depends_on_judgement_ids=view.latest_record.depends_on_judgement_ids,
                    updates_judgement_id=view.latest_record.judgement_id,
                    update_kind=draft.update_kind,
                )
            )
    daily = merge_daily_continuity(
        existing,
        DailyContinuity(date=day, generated_at=now, judgements=updates),
    )
    if updates:
        save_model(root / "data/continuity" / f"{day}.json", daily)
    return DeepContinuityResult(
        daily=daily,
        stats={
            "judgement_deep_review_triggers": len(triggers),
            "judgement_deep_review_calls": calls,
            "judgement_deep_review_updates": len(updates),
            "deep_continuity_status": "completed",
        },
    )

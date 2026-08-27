"""Compact, persisted lifecycle trace for every accepted RawItem."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import Field, field_validator

from morning_radar.models import Candidate, RawItem, Story
from morning_radar.models.core import RadarModel, _validate_aware_datetime


class DecisionStage(StrEnum):
    RAW_ACCEPTANCE = "raw_acceptance"
    FRESHNESS_WINDOW = "freshness_window"
    ROUTINE_FILTER = "routine_filter"
    DEDUPLICATION = "deduplication"
    CANDIDATE_ADMISSION = "candidate_admission"
    HIGH_RECALL_GUARDRAIL = "high_recall_guardrail"
    SEMANTIC_TRIAGE = "semantic_triage"
    INVESTIGATION = "investigation"
    EVIDENCE_STATE = "evidence_state"
    STORY_CONSTRUCTION = "story_construction"
    READER_SELECTION = "reader_selection"
    FINAL_DISPOSITION = "final_disposition"


class SystemReasonCode(StrEnum):
    OUTSIDE_FRESHNESS_WINDOW = "OUTSIDE_FRESHNESS_WINDOW"
    ROUTINE_MARKET_MOVEMENT = "ROUTINE_MARKET_MOVEMENT"
    DEFERRED_BY_BUDGET = "DEFERRED_BY_BUDGET"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    PARSE_FAILED = "PARSE_FAILED"
    AI_OUTPUT_INVALID = "AI_OUTPUT_INVALID"
    PRESELECTION_CAP = "PRESELECTION_CAP"


class DecisionTransition(RadarModel):
    stage: DecisionStage
    decision: str = Field(min_length=1, max_length=120)
    reason_codes: list[str] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=1000)
    candidate_id: str | None = None
    story_id: str | None = None


class RawDecisionTrace(RadarModel):
    raw_item_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    source_name: str = Field(min_length=1, max_length=300)
    transitions: list[DecisionTransition] = Field(default_factory=list)


class DailyDecisionTrace(RadarModel):
    date: date
    generated_at: datetime
    records: list[RawDecisionTrace] = Field(default_factory=list)

    _generated_is_aware = field_validator("generated_at")(_validate_aware_datetime)


class DecisionTraceBuilder:
    def __init__(self, items: list[RawItem]) -> None:
        self.records = {
            item.id: RawDecisionTrace(
                raw_item_id=item.id,
                title=item.title,
                source_name=item.source_name,
                transitions=[
                    DecisionTransition(
                        stage=DecisionStage.RAW_ACCEPTANCE,
                        decision="ACCEPTED",
                    )
                ],
            )
            for item in items
        }

    def add(
        self,
        raw_item_ids: list[str],
        *,
        stage: DecisionStage,
        decision: str,
        reason_codes: list[str] | None = None,
        rationale: str = "",
        candidate_id: str | None = None,
        story_id: str | None = None,
    ) -> None:
        transition = DecisionTransition(
            stage=stage,
            decision=decision,
            reason_codes=reason_codes or [],
            rationale=rationale,
            candidate_id=candidate_id,
            story_id=story_id,
        )
        for raw_item_id in raw_item_ids:
            record = self.records.get(raw_item_id)
            if record is not None:
                self.records[raw_item_id] = record.model_copy(
                    update={"transitions": [*record.transitions, transition]}
                )

    def add_candidates(self, candidates: list[Candidate]) -> None:
        for candidate in candidates:
            self.add(
                candidate.raw_item_ids,
                stage=DecisionStage.CANDIDATE_ADMISSION,
                decision="ADMITTED",
                candidate_id=candidate.id,
            )
            if candidate.must_triage:
                self.add(
                    candidate.raw_item_ids,
                    stage=DecisionStage.HIGH_RECALL_GUARDRAIL,
                    decision="MUST_TRIAGE",
                    reason_codes=["HIGH_RECALL_GUARDRAIL"],
                    candidate_id=candidate.id,
                )
            self.add(
                candidate.raw_item_ids,
                stage=DecisionStage.SEMANTIC_TRIAGE,
                decision=(
                    candidate.semantic_disposition.value.upper()
                    if candidate.semantic_disposition
                    else "UNRESOLVED"
                ),
                reason_codes=[code.value for code in candidate.reason_codes],
                rationale=candidate.rationale,
                candidate_id=candidate.id,
            )
            self.add(
                candidate.raw_item_ids,
                stage=DecisionStage.EVIDENCE_STATE,
                decision=candidate.evidence_state.value.upper(),
                candidate_id=candidate.id,
            )
            if candidate.execution_state.value not in {"not_needed", "not_started"}:
                self.add(
                    candidate.raw_item_ids,
                    stage=DecisionStage.INVESTIGATION,
                    decision=candidate.execution_state.value.upper(),
                    reason_codes=(
                        [candidate.execution_state.value.upper()]
                        if candidate.execution_state.value.startswith(("failed", "deferred"))
                        else []
                    ),
                    rationale="; ".join(candidate.missing_evidence),
                    candidate_id=candidate.id,
                )

    def add_story_results(
        self,
        candidates: list[Candidate],
        stories: list[Story],
        dispositions: dict[str, str],
    ) -> None:
        story_by_candidate = {
            candidate_id: story
            for story in stories
            for candidate_id in story.candidate_ids
        }
        for candidate in candidates:
            decision = dispositions.get(candidate.id)
            if decision is None:
                continue
            story = story_by_candidate.get(candidate.id)
            self.add(
                candidate.raw_item_ids,
                stage=DecisionStage.STORY_CONSTRUCTION,
                decision=decision,
                reason_codes=(
                    ["CORE_CLAIM_UNSUPPORTED"]
                    if decision == "STORY_REJECTED"
                    else (
                        [SystemReasonCode.AI_OUTPUT_INVALID]
                        if decision == "STORY_FAILED_AI"
                        else (
                            [SystemReasonCode.DEFERRED_BY_BUDGET]
                            if decision == "STORY_DEFERRED_BY_BUDGET"
                            else []
                        )
                    )
                ),
                candidate_id=candidate.id,
                story_id=story.id if story else None,
            )

    def finish(
        self,
        *,
        trace_date: date,
        generated_at: datetime,
        stories: list[Story],
        brief_story_ids: set[str],
    ) -> DailyDecisionTrace:
        story_by_raw = {
            raw_item_id: story
            for story in stories
            for raw_item_id in story.source_item_ids
        }
        for raw_item_id, record in list(self.records.items()):
            story = story_by_raw.get(raw_item_id)
            if story and story.id in brief_story_ids:
                decision = "BRIEF_SELECTED"
            elif story:
                decision = "STORY_NOT_SELECTED"
            else:
                decision = "NO_STORY"
            self.records[raw_item_id] = record.model_copy(
                update={
                    "transitions": [
                        *record.transitions,
                        DecisionTransition(
                            stage=DecisionStage.FINAL_DISPOSITION,
                            decision=decision,
                            story_id=story.id if story else None,
                        ),
                    ]
                }
            )
        return DailyDecisionTrace(
            date=trace_date,
            generated_at=generated_at,
            records=list(self.records.values()),
        )

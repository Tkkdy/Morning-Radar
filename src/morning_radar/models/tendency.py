"""Immutable Tendency Intelligence records for v0.35."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import Field, field_validator

from morning_radar.models.continuity import StoryEvidenceRef, StoryOccurrenceRef
from morning_radar.models.core import RadarModel, SourceRole, _validate_aware_datetime


class TendencyStanding(StrEnum):
    CANDIDATE = "candidate"
    EMERGING = "emerging"
    PERSISTENT = "persistent"
    OVERTURNED = "overturned"


class TendencyUpdateKind(StrEnum):
    SUPPORTED = "supported"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    REVISED = "revised"
    OVERTURNED = "overturned"


class TendencyEvidenceCluster(RadarModel):
    cluster_id: str = Field(min_length=1)
    story_refs: list[StoryOccurrenceRef] = Field(min_length=1)
    observed_dates: list[date] = Field(min_length=1)
    actor_keys: list[str] = Field(default_factory=list)
    event_identity: str = Field(min_length=1)
    source_roles: list[SourceRole] = Field(default_factory=list)
    source_count: int = Field(ge=1)
    titles: list[str] = Field(min_length=1)
    facts: list[str] = Field(default_factory=list)


class TendencyAssessment(RadarModel):
    shared_mechanism: str = Field(min_length=1, max_length=1500)
    baseline: str = Field(min_length=1, max_length=1000)
    falsifier: str = Field(min_length=1, max_length=1000)
    observable_impacts: list[str] = Field(min_length=1)
    counterevidence_considered: bool
    decision_rationale: str = Field(min_length=1, max_length=1500)
    formation_exception_rationale: str | None = Field(default=None, max_length=1000)
    core_claim_invalidated: bool = False


class TendencyDecisionRecord(RadarModel):
    record_id: str = Field(min_length=1)
    tendency_id: str = Field(min_length=1)
    recorded_at: datetime
    previous_record_id: str | None = None
    standing_after: TendencyStanding
    update_kind: TendencyUpdateKind | None = None
    claim: str = Field(min_length=1, max_length=1500)
    assessment: TendencyAssessment
    supporting_cluster_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[StoryEvidenceRef] = Field(default_factory=list)
    counterevidence_cluster_ids: list[str] = Field(default_factory=list)
    counterevidence_refs: list[StoryEvidenceRef] = Field(default_factory=list)
    formed_at: date | None = None
    formation_cluster_ids: list[str] = Field(default_factory=list)
    policy_version: str = Field(min_length=1)

    _recorded_is_aware = field_validator("recorded_at")(_validate_aware_datetime)


class DailyTendencies(RadarModel):
    date: date
    generated_at: datetime
    decisions: list[TendencyDecisionRecord] = Field(default_factory=list)

    _generated_is_aware = field_validator("generated_at")(_validate_aware_datetime)


class TendencyCurrentView(RadarModel):
    tendency_id: str
    latest_record_id: str
    standing: TendencyStanding
    latest_update: TendencyUpdateKind | None = None
    claim: str
    assessment: TendencyAssessment
    formed_at: date | None = None
    formation_cluster_ids: list[str] = Field(default_factory=list)
    last_recorded_at: datetime
    policy_version: str

    _recorded_is_aware = field_validator("last_recorded_at")(_validate_aware_datetime)

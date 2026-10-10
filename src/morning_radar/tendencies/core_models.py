"""Version 2 contracts: observations, scoped inferences and immutable revisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from morning_radar.models.core import RadarModel, _validate_aware_datetime


class CoreEvidence(RadarModel):
    schema_version: Literal[2] = 2
    evidence_id: str
    input_id: str
    content_version: str
    event_ids: list[str] = Field(default_factory=list)
    source_url: str | None = None
    source_kind: Literal[
        "source_claim",
        "original_fact",
        "personal_observation",
        "user_hypothesis",
        "system_inference",
    ] = "source_claim"
    source_role: str = "unknown"
    title: str
    excerpt: str = Field(max_length=2000)
    excerpt_spans: list[tuple[int, int]] = Field(default_factory=list)
    excerpt_source_sha256: str | None = None
    scope: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    observed_at: datetime
    ingested_at: datetime
    status: str = "captured"
    information_gaps: list[str] = Field(default_factory=list)
    synthetic: bool = False
    provenance: str

    _times = field_validator("published_at", "observed_at", "ingested_at")(
        lambda value: _validate_aware_datetime(value) if value else None
    )


class Citation(RadarModel):
    evidence_id: str
    quote: str = Field(min_length=8, max_length=280)
    role: Literal["support", "counter", "context", "source_claim", "boundary", "observation"]
    condition_key: str = Field(min_length=1, max_length=80)


class Baseline(RadarModel):
    text: str = Field(min_length=1, max_length=600)
    assumption: bool = True
    evidence_ids: list[str] = Field(default_factory=list)


class Condition(RadarModel):
    key: str = Field(min_length=1, max_length=80)
    observation: str = Field(min_length=1, max_length=600)
    interpretation: str = Field(min_length=1, max_length=600)
    evidence_ids: list[str] = Field(min_length=1)


class ObservationTest(RadarModel):
    condition_key: str
    observe: str = Field(min_length=1, max_length=600)
    if_seen: str = Field(min_length=1, max_length=600)
    if_absent: str = Field(min_length=1, max_length=600)
    # Absence is never inferred from elapsed time or an unavailable source.
    absence_requires_completed_observation: Literal[True] = True


class Hypothesis(RadarModel):
    claim: str = Field(min_length=1, max_length=1200)
    scope: list[str] = Field(min_length=1, max_length=10)
    scope_basis: Literal["observed", "inferred"] = "observed"
    scope_reason: str = Field(default="", max_length=600)
    from_state: str = Field(min_length=1, max_length=600)
    to_state: str = Field(min_length=1, max_length=600)
    baseline: list[Baseline] = Field(min_length=1, max_length=8)
    conditions: list[Condition] = Field(min_length=1, max_length=8)
    mechanism: str = Field(min_length=1, max_length=1500)
    unresolved: list[str] = Field(default_factory=list, max_length=12)
    alternatives: list[str] = Field(default_factory=list, max_length=6)
    tests: list[ObservationTest] = Field(min_length=1, max_length=8)


class CoreDecision(RadarModel):
    hypothesis_id: str | None = None
    previous_revision_id: str | None = None
    parent_hypothesis_id: str | None = None
    parent_revision_id: str | None = None
    seed_id: str | None = None
    kind: Literal[
        "early",
        "supported",
        "strengthened",
        "weakened",
        "narrowed",
        "split",
        "overturned",
        "no_change",
        "analysis_reassessment",
        "user_correction",
        "archived",
    ]
    basis: Literal["external_evidence", "analysis_reassessment", "user_correction"]
    hypothesis: Hypothesis
    citations: list[Citation] = Field(default_factory=list, max_length=30)
    changed_parts: list[str] = Field(default_factory=list)
    relinquished: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1500)


class CoreOutput(RadarModel):
    # Validate each proposal separately, so one invalid item cannot erase siblings.
    decisions: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=20)
    outcome: Literal["proposed", "no_changes", "insufficient_evidence"]


class SplitOutput(RadarModel):
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    gaps: list[str] = Field(default_factory=list, max_length=20)


class Revision(RadarModel):
    schema_version: Literal[2] = 2
    revision_id: str
    hypothesis_id: str
    previous_revision_id: str | None = None
    parent_hypothesis_id: str | None = None
    parent_revision_id: str | None = None
    first_recorded_at: datetime
    recorded_at: datetime
    origin: Literal["system", "user"]
    seed_id: str | None = None
    standing: Literal["early", "active", "overturned", "archived"]
    decision: CoreDecision
    evidence: list[CoreEvidence]
    run_id: str
    analysis_version: str
    observed_signatures: list[str] = Field(default_factory=list)
    synthetic: bool = False
    _times = field_validator("first_recorded_at", "recorded_at")(_validate_aware_datetime)

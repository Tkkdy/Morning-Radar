"""B0.5 Candidate lifecycle and claim-evidence boundary models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from morning_radar.models.core import (
    PracticeSignalKind,
    RadarModel,
    SourceRole,
    StatementType,
    _validate_aware_datetime,
    _validate_http_url,
)


class SemanticDisposition(StrEnum):
    DROP = "drop"
    BUILD = "build"
    INVESTIGATE = "investigate"


class EvidenceState(StrEnum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    CONTRADICTED = "contradicted"


class ExecutionState(StrEnum):
    NOT_NEEDED = "not_needed"
    NOT_STARTED = "not_started"
    EXECUTED = "executed"
    DEFERRED_BY_BUDGET = "deferred_by_budget"
    FAILED_NETWORK = "failed_network"
    FAILED_PARSE = "failed_parse"
    FAILED_AI = "failed_ai"


class ClaimType(StrEnum):
    EXISTENCE_CAPABILITY = "existence_capability"
    AVAILABILITY = "availability"
    RELEASE_GA = "release_ga"
    PERFORMANCE = "performance"
    NOVELTY_FIRST = "novelty_first"
    PRICING_POLICY = "pricing_policy"
    COMMUNITY_ADOPTION = "community_adoption"
    FIRSTHAND_BEHAVIOR = "firsthand_behavior"
    OTHER = "other"


class EvidenceAuthority(StrEnum):
    SELF_AUTHORITATIVE = "self_authoritative"
    FIRSTHAND_OBSERVATION = "firsthand_observation"
    INDEPENDENT_REPORTING = "independent_reporting"
    DISCOVERY_ONLY = "discovery_only"


class CandidateReasonCode(StrEnum):
    LOW_IMPACT = "LOW_IMPACT"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    HYPOTHESIS_DISCONFIRMED = "HYPOTHESIS_DISCONFIRMED"
    UNRESOLVABLE_LOW_VALUE = "UNRESOLVABLE_LOW_VALUE"
    CORE_CLAIM_UNSUPPORTED = "CORE_CLAIM_UNSUPPORTED"
    POTENTIAL_CAPABILITY_CHANGE = "POTENTIAL_CAPABILITY_CHANGE"
    POTENTIAL_WORKFLOW_CHANGE = "POTENTIAL_WORKFLOW_CHANGE"
    POTENTIAL_ECOSYSTEM_CHANGE = "POTENTIAL_ECOSYSTEM_CHANGE"
    DEVELOPER_IMPACT = "DEVELOPER_IMPACT"
    OFFICIAL_SELF_CLAIM = "OFFICIAL_SELF_CLAIM"
    FIRSTHAND_OBSERVATION = "FIRSTHAND_OBSERVATION"
    HIGH_RECALL_GUARDRAIL = "HIGH_RECALL_GUARDRAIL"


class ObservationQuality(RadarModel):
    firsthandness: bool = False
    specificity: bool = False
    artifact_support: bool = False
    temporal_coherence: bool = False
    identity_history: bool = False
    independent_reproducibility: bool = False


class CandidateEvidence(RadarModel):
    """Real pipeline evidence. Model prior knowledge is never represented here."""

    evidence_id: str = Field(min_length=1)
    raw_item_id: str | None = None
    url: str
    publisher: str = Field(min_length=1, max_length=300)
    source_role: SourceRole
    statement_type: StatementType = StatementType.UNKNOWN
    authority: EvidenceAuthority
    claim_types: list[ClaimType] = Field(default_factory=list)
    scope: str = Field(default="", max_length=1000)
    excerpt: str = Field(default="", max_length=4000)
    official_surface_verified: bool = False
    retrieved_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    observation_quality: ObservationQuality | None = None

    _url_is_http = field_validator("url")(_validate_http_url)
    _retrieved_is_aware = field_validator("retrieved_at")(_validate_aware_datetime)


class Candidate(RadarModel):
    """A hypothesis formed from one or more RawItems and awaiting a Story boundary."""

    id: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    raw_item_ids: list[str] = Field(min_length=1)
    hypothesis: str = Field(min_length=1, max_length=1500)
    entity_names: list[str] = Field(default_factory=list)
    product_names: list[str] = Field(default_factory=list)
    topic_names: list[str] = Field(default_factory=list)
    potential_novelty: str = Field(default="", max_length=1500)
    potential_impact: str = Field(default="", max_length=1500)
    affected_audiences: list[str] = Field(default_factory=list)
    impact_mechanism: str = Field(default="", max_length=1500)
    alternative_explanation: str | None = Field(default=None, max_length=1500)
    semantic_disposition: SemanticDisposition | None = None
    evidence_state: EvidenceState = EvidenceState.INSUFFICIENT
    execution_state: ExecutionState = ExecutionState.NOT_STARTED
    reason_codes: list[CandidateReasonCode] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=1500)
    missing_evidence: list[str] = Field(default_factory=list)
    verification_target: str | None = Field(default=None, max_length=1000)
    verification_path: str | None = Field(default=None, max_length=1500)
    investigation_priority: float = Field(default=0, ge=0, le=1)
    must_triage: bool = False
    evidence: list[CandidateEvidence] = Field(default_factory=list)
    statement_type: StatementType = StatementType.UNKNOWN
    practice_signal_kind: PracticeSignalKind | None = None

    _created_is_aware = field_validator("created_at")(_validate_aware_datetime)
    _updated_is_aware = field_validator("updated_at")(_validate_aware_datetime)

    @model_validator(mode="after")
    def investigation_has_a_concrete_path(self) -> Candidate:
        if self.semantic_disposition is SemanticDisposition.INVESTIGATE and not (
            self.missing_evidence and self.verification_target and self.verification_path
        ):
            raise ValueError(
                "INVESTIGATE requires missing_evidence, verification_target, and "
                "verification_path"
            )
        if self.semantic_disposition is SemanticDisposition.DROP and not self.reason_codes:
            raise ValueError("DROP requires at least one structured reason code")
        return self


class StoryClaimSupport(RadarModel):
    claim: str = Field(min_length=1, max_length=2000)
    claim_type: ClaimType = ClaimType.OTHER
    evidence_ids: list[str] = Field(min_length=1)
    evidence_scope: str = Field(min_length=1, max_length=1500)
    claim_scope: str = Field(min_length=1, max_length=1500)
    scope_supported: bool = False

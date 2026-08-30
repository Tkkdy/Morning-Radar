"""Evaluation-only Minimal Atomic semantics with deterministic system routing."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Any

from pydantic import Field

from morning_radar.ai import AIOutputError
from morning_radar.ai.deepseek_provider import DeepSeekProvider
from morning_radar.candidates.eligibility import (
    UNTRUSTED_BUILD_AUTHORITIES,
    _evidence_can_support_build,
)
from morning_radar.candidates.freshness import apply_freshness_guard
from morning_radar.models import Candidate, SemanticDisposition
from morning_radar.models.core import RadarModel

ASSESSMENT_SCHEMA_VERSION = "candidate-semantic-assessment-minimal-v1"
ROUTER_VERSION = "deterministic-resource-router-minimal-v1"


class ScopeRelevance(StrEnum):
    IN_SCOPE = "in_scope"
    OUT_OF_SCOPE = "out_of_scope"
    UNKNOWN = "unknown"


class ImpactLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class CoreClaimEvidenceRelation(StrEnum):
    DIRECT_SUPPORT = "direct_support"
    CRITICAL_GAP = "critical_gap"
    COUNTEREVIDENCE_PRESENT = "counterevidence_present"
    UNKNOWN = "unknown"


class VerificationRouteability(StrEnum):
    NOT_NEEDED = "NOT_NEEDED"
    EXECUTABLE = "EXECUTABLE"
    INCOMPLETE = "INCOMPLETE"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class ProposedRoute(StrEnum):
    DROP = "drop"
    BUILD = "build"
    INVESTIGATE = "investigate"
    UNRESOLVED = "unresolved"


class AssessmentValidationStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"


class AssessmentValidationReason(StrEnum):
    INVALID_EVIDENCE_REFERENCE = "INVALID_EVIDENCE_REFERENCE"
    DUPLICATE_EVIDENCE_REFERENCE = "DUPLICATE_EVIDENCE_REFERENCE"
    INVALID_DIRECT_SUPPORT_BINDING = "INVALID_DIRECT_SUPPORT_BINDING"
    INVALID_COUNTEREVIDENCE_BINDING = "INVALID_COUNTEREVIDENCE_BINDING"


class RouteReason(StrEnum):
    DETERMINISTIC_DUPLICATE = "DETERMINISTIC_DUPLICATE"
    EVIDENCE_DIRECT_SUPPORT = "EVIDENCE_DIRECT_SUPPORT"
    CRITICAL_GAP_EXECUTABLE = "CRITICAL_GAP_EXECUTABLE"
    CRITICAL_GAP_INCOMPLETE = "CRITICAL_GAP_INCOMPLETE"
    CRITICAL_GAP_UNSUPPORTED = "CRITICAL_GAP_UNSUPPORTED"
    UNKNOWN_ASSESSMENT = "UNKNOWN_ASSESSMENT"
    SEMANTIC_LOW_UNRESOLVED = "SEMANTIC_LOW_UNRESOLVED"
    SEMANTIC_OUT_OF_SCOPE_UNRESOLVED = "SEMANTIC_OUT_OF_SCOPE_UNRESOLVED"
    COUNTEREVIDENCE_UNRESOLVED = "COUNTEREVIDENCE_UNRESOLVED"
    DETERMINISTIC_FRESHNESS_CONSTRAINT = "DETERMINISTIC_FRESHNESS_CONSTRAINT"
    ASSESSMENT_INVALID = "ASSESSMENT_INVALID"


class RouteabilityReason(StrEnum):
    ELIGIBLE_DIRECT_SUPPORT = "ELIGIBLE_DIRECT_SUPPORT"
    EXISTING_DESTINATION_FETCH_SUPPORTED = "EXISTING_DESTINATION_FETCH_SUPPORTED"
    INCOMPLETE_VERIFICATION_CONTRACT = "INCOMPLETE_VERIFICATION_CONTRACT"
    NO_EXISTING_DESTINATION = "NO_EXISTING_DESTINATION"
    DIRECT_FETCH_UNSUPPORTED = "DIRECT_FETCH_UNSUPPORTED"
    INVESTIGATION_BUDGET_UNAVAILABLE = "INVESTIGATION_BUDGET_UNAVAILABLE"
    RELATION_NOT_ROUTEABLE = "RELATION_NOT_ROUTEABLE"
    ASSESSMENT_INVALID = "ASSESSMENT_INVALID"


class MinimalCandidateSemanticAssessment(RadarModel):
    """Only semantic interpretation; no action, capability, Budget, or final route."""

    candidate_id: str = Field(min_length=1)
    scope_relevance: ScopeRelevance
    scope_basis: str = Field(default="", max_length=400)
    impact_level: ImpactLevel
    impact_basis: str = Field(default="", max_length=400)
    core_claim_evidence_relation: CoreClaimEvidenceRelation
    relation_evidence_ids: list[str] = Field(default_factory=list)
    relation_basis: str = Field(default="", max_length=400)
    affected_audiences: list[str] = Field(default_factory=list)
    impact_mechanism: str = Field(default="", max_length=700)
    alternative_explanation: str | None = Field(default=None, max_length=700)
    missing_evidence: list[str] = Field(default_factory=list)
    verification_target: str | None = Field(default=None, max_length=700)
    verification_path: str | None = Field(default=None, max_length=1000)


class MinimalCandidateSemanticAssessmentBatch(RadarModel):
    candidates: list[MinimalCandidateSemanticAssessment]


class DeterministicEvidenceProfile(RadarModel):
    candidate_id: str
    eligible_evidence_ids: list[str] = Field(default_factory=list)
    evidence_authority_summary: dict[str, int] = Field(default_factory=dict)
    evidence_excerpt_presence: dict[str, bool] = Field(default_factory=dict)
    existing_destination_evidence_id: str | None = None
    must_triage: bool
    confirmed_duplicate: bool = False


class SystemRoutingContext(RadarModel):
    bounded_direct_fetch_supported: bool = True
    investigation_budget_available: bool = True


class AssessmentValidationResult(RadarModel):
    candidate_id: str
    status: AssessmentValidationStatus
    reasons: list[AssessmentValidationReason] = Field(default_factory=list)


class VerificationRouteabilityResult(RadarModel):
    candidate_id: str
    routeability: VerificationRouteability
    reason: RouteabilityReason
    destination_evidence_id: str | None = None


class MinimalRoutingDecision(RadarModel):
    candidate_id: str
    route: ProposedRoute
    reason: RouteReason | AssessmentValidationReason
    investigation_priority: float | None = None
    assessment_schema_version: str = ASSESSMENT_SCHEMA_VERSION
    router_version: str = ROUTER_VERSION


def _existing_destination_evidence_id(candidate: Candidate) -> str | None:
    for evidence in candidate.evidence:
        if not evidence.metadata.get("is_discussion_url"):
            return evidence.evidence_id
    return None


def build_evidence_profile(candidate: Candidate) -> DeterministicEvidenceProfile:
    eligible_ids = [
        evidence.evidence_id
        for evidence in candidate.evidence
        if evidence.authority not in UNTRUSTED_BUILD_AUTHORITIES
        and bool(evidence.excerpt.strip())
    ]
    if bool(eligible_ids) != _evidence_can_support_build(candidate):
        raise RuntimeError("Evaluation Evidence profile diverged from production BUILD gate")
    return DeterministicEvidenceProfile(
        candidate_id=candidate.id,
        eligible_evidence_ids=eligible_ids,
        evidence_authority_summary=dict(
            Counter(evidence.authority.value for evidence in candidate.evidence)
        ),
        evidence_excerpt_presence={
            evidence.evidence_id: bool(evidence.excerpt.strip())
            for evidence in candidate.evidence
        },
        existing_destination_evidence_id=_existing_destination_evidence_id(candidate),
        must_triage=candidate.must_triage,
    )


def validate_assessment_batch(
    output: MinimalCandidateSemanticAssessmentBatch,
    candidates: list[Candidate],
) -> MinimalCandidateSemanticAssessmentBatch:
    returned = [assessment.candidate_id for assessment in output.candidates]
    expected = {candidate.id for candidate in candidates}
    if len(returned) != len(set(returned)) or set(returned) != expected:
        raise AIOutputError("Minimal Atomic assessment must return every Candidate exactly once")
    return output


def validate_assessment(
    candidate: Candidate,
    assessment: MinimalCandidateSemanticAssessment,
) -> AssessmentValidationResult:
    if candidate.id != assessment.candidate_id:
        raise ValueError("Candidate and assessment IDs must match")
    reasons: list[AssessmentValidationReason] = []
    candidate_ids = {evidence.evidence_id for evidence in candidate.evidence}
    referenced = set(assessment.relation_evidence_ids)
    if not referenced.issubset(candidate_ids):
        reasons.append(AssessmentValidationReason.INVALID_EVIDENCE_REFERENCE)
    if len(referenced) != len(assessment.relation_evidence_ids):
        reasons.append(AssessmentValidationReason.DUPLICATE_EVIDENCE_REFERENCE)
    eligible = set(build_evidence_profile(candidate).eligible_evidence_ids)
    has_eligible_binding = bool(referenced.intersection(eligible))
    if (
        assessment.core_claim_evidence_relation is CoreClaimEvidenceRelation.DIRECT_SUPPORT
        and not has_eligible_binding
    ):
        reasons.append(AssessmentValidationReason.INVALID_DIRECT_SUPPORT_BINDING)
    if (
        assessment.core_claim_evidence_relation
        is CoreClaimEvidenceRelation.COUNTEREVIDENCE_PRESENT
        and not has_eligible_binding
    ):
        reasons.append(AssessmentValidationReason.INVALID_COUNTEREVIDENCE_BINDING)
    return AssessmentValidationResult(
        candidate_id=candidate.id,
        status=(
            AssessmentValidationStatus.INVALID
            if reasons
            else AssessmentValidationStatus.VALID
        ),
        reasons=list(dict.fromkeys(reasons)),
    )


def semantic_investigation_warranted(
    assessment: MinimalCandidateSemanticAssessment,
) -> bool:
    return (
        assessment.core_claim_evidence_relation is CoreClaimEvidenceRelation.CRITICAL_GAP
        and assessment.scope_relevance is ScopeRelevance.IN_SCOPE
        and assessment.impact_level in {ImpactLevel.HIGH, ImpactLevel.MEDIUM}
    )


def derive_routeability(
    candidate: Candidate,
    profile: DeterministicEvidenceProfile,
    assessment: MinimalCandidateSemanticAssessment,
    validation: AssessmentValidationResult,
    context: SystemRoutingContext,
) -> VerificationRouteabilityResult:
    if validation.status is AssessmentValidationStatus.INVALID:
        return VerificationRouteabilityResult(
            candidate_id=candidate.id,
            routeability=VerificationRouteability.UNKNOWN,
            reason=RouteabilityReason.ASSESSMENT_INVALID,
        )
    relation = assessment.core_claim_evidence_relation
    if relation is CoreClaimEvidenceRelation.DIRECT_SUPPORT:
        return VerificationRouteabilityResult(
            candidate_id=candidate.id,
            routeability=VerificationRouteability.NOT_NEEDED,
            reason=RouteabilityReason.ELIGIBLE_DIRECT_SUPPORT,
        )
    if relation is not CoreClaimEvidenceRelation.CRITICAL_GAP:
        return VerificationRouteabilityResult(
            candidate_id=candidate.id,
            routeability=VerificationRouteability.UNKNOWN,
            reason=RouteabilityReason.RELATION_NOT_ROUTEABLE,
        )
    if not (
        any(value.strip() for value in assessment.missing_evidence)
        and assessment.verification_target
        and assessment.verification_target.strip()
        and assessment.verification_path
        and assessment.verification_path.strip()
    ):
        return VerificationRouteabilityResult(
            candidate_id=candidate.id,
            routeability=VerificationRouteability.INCOMPLETE,
            reason=RouteabilityReason.INCOMPLETE_VERIFICATION_CONTRACT,
        )
    if profile.existing_destination_evidence_id is None:
        return VerificationRouteabilityResult(
            candidate_id=candidate.id,
            routeability=VerificationRouteability.UNSUPPORTED,
            reason=RouteabilityReason.NO_EXISTING_DESTINATION,
        )
    if not context.bounded_direct_fetch_supported:
        return VerificationRouteabilityResult(
            candidate_id=candidate.id,
            routeability=VerificationRouteability.UNSUPPORTED,
            reason=RouteabilityReason.DIRECT_FETCH_UNSUPPORTED,
        )
    if not context.investigation_budget_available:
        return VerificationRouteabilityResult(
            candidate_id=candidate.id,
            routeability=VerificationRouteability.UNSUPPORTED,
            reason=RouteabilityReason.INVESTIGATION_BUDGET_UNAVAILABLE,
        )
    return VerificationRouteabilityResult(
        candidate_id=candidate.id,
        routeability=VerificationRouteability.EXECUTABLE,
        reason=RouteabilityReason.EXISTING_DESTINATION_FETCH_SUPPORTED,
        destination_evidence_id=profile.existing_destination_evidence_id,
    )


def _freshness_allows_build(candidate: Candidate) -> bool:
    probe = candidate.model_copy(
        update={"semantic_disposition": SemanticDisposition.BUILD}
    )
    return (
        apply_freshness_guard([probe])[0].semantic_disposition
        is SemanticDisposition.BUILD
    )


def _investigation_priority(impact: ImpactLevel) -> float | None:
    return {ImpactLevel.HIGH: 0.9, ImpactLevel.MEDIUM: 0.6}.get(impact)


def invalid_assessment_decision(
    candidate: Candidate,
    validation: AssessmentValidationResult,
) -> MinimalRoutingDecision:
    if validation.status is not AssessmentValidationStatus.INVALID:
        raise ValueError("Invalid decision requires INVALID assessment")
    return MinimalRoutingDecision(
        candidate_id=candidate.id,
        route=ProposedRoute.UNRESOLVED,
        reason=validation.reasons[0],
    )


def route_assessment(
    candidate: Candidate,
    profile: DeterministicEvidenceProfile,
    assessment: MinimalCandidateSemanticAssessment,
    validation: AssessmentValidationResult,
    context: SystemRoutingContext,
) -> MinimalRoutingDecision:
    if validation.status is not AssessmentValidationStatus.VALID:
        raise ValueError("Deterministic router accepts only VALID assessments")

    def decision(
        route: ProposedRoute,
        reason: RouteReason,
        priority: float | None = None,
    ) -> MinimalRoutingDecision:
        return MinimalRoutingDecision(
            candidate_id=candidate.id,
            route=route,
            reason=reason,
            investigation_priority=priority,
        )

    if profile.confirmed_duplicate:
        return decision(ProposedRoute.DROP, RouteReason.DETERMINISTIC_DUPLICATE)
    if assessment.scope_relevance is ScopeRelevance.OUT_OF_SCOPE:
        return decision(
            ProposedRoute.UNRESOLVED, RouteReason.SEMANTIC_OUT_OF_SCOPE_UNRESOLVED
        )
    if assessment.impact_level is ImpactLevel.LOW:
        return decision(ProposedRoute.UNRESOLVED, RouteReason.SEMANTIC_LOW_UNRESOLVED)
    if (
        assessment.core_claim_evidence_relation
        is CoreClaimEvidenceRelation.COUNTEREVIDENCE_PRESENT
    ):
        return decision(ProposedRoute.UNRESOLVED, RouteReason.COUNTEREVIDENCE_UNRESOLVED)
    if any(
        value.value == "unknown"
        for value in (
            assessment.scope_relevance,
            assessment.impact_level,
            assessment.core_claim_evidence_relation,
        )
    ):
        return decision(ProposedRoute.UNRESOLVED, RouteReason.UNKNOWN_ASSESSMENT)
    if (
        assessment.core_claim_evidence_relation is CoreClaimEvidenceRelation.DIRECT_SUPPORT
        and assessment.scope_relevance is ScopeRelevance.IN_SCOPE
        and assessment.impact_level in {ImpactLevel.HIGH, ImpactLevel.MEDIUM}
    ):
        if not _freshness_allows_build(candidate):
            return decision(
                ProposedRoute.UNRESOLVED,
                RouteReason.DETERMINISTIC_FRESHNESS_CONSTRAINT,
            )
        return decision(ProposedRoute.BUILD, RouteReason.EVIDENCE_DIRECT_SUPPORT)
    routeability = derive_routeability(
        candidate, profile, assessment, validation, context
    )
    if (
        semantic_investigation_warranted(assessment)
        and routeability.routeability is VerificationRouteability.EXECUTABLE
    ):
        return decision(
            ProposedRoute.INVESTIGATE,
            RouteReason.CRITICAL_GAP_EXECUTABLE,
            _investigation_priority(assessment.impact_level),
        )
    if routeability.routeability is VerificationRouteability.INCOMPLETE:
        return decision(ProposedRoute.UNRESOLVED, RouteReason.CRITICAL_GAP_INCOMPLETE)
    if routeability.routeability is VerificationRouteability.UNSUPPORTED:
        return decision(ProposedRoute.UNRESOLVED, RouteReason.CRITICAL_GAP_UNSUPPORTED)
    return decision(ProposedRoute.UNRESOLVED, RouteReason.UNKNOWN_ASSESSMENT)


class MinimalAtomicAssessmentProviderAdapter:
    def __init__(self, provider: DeepSeekProvider) -> None:
        self.provider = provider

    def assess_candidates(
        self, candidates: list[Candidate]
    ) -> MinimalCandidateSemanticAssessmentBatch:
        return self.provider._parse(
            task="candidate_triage",
            schema=MinimalCandidateSemanticAssessmentBatch,
            payload_data=[candidate.model_dump(mode="json") for candidate in candidates],
            item_count=len(candidates),
            allowed_urls={
                evidence.url for candidate in candidates for evidence in candidate.evidence
            },
            output_validator=lambda output: validate_assessment_batch(output, candidates),
        )


def assessment_artifact(
    candidate: Candidate,
    assessment: MinimalCandidateSemanticAssessment,
    validation: AssessmentValidationResult,
    decision: MinimalRoutingDecision,
    context: SystemRoutingContext,
) -> dict[str, Any]:
    profile = build_evidence_profile(candidate)
    routeability = derive_routeability(
        candidate, profile, assessment, validation, context
    )
    return {
        "candidate_id": candidate.id,
        "assessment_schema_version": ASSESSMENT_SCHEMA_VERSION,
        "router_version": ROUTER_VERSION,
        "raw_atomic_assessment": assessment.model_dump(mode="json"),
        "assessment_validation_status": validation.status.value,
        "assessment_validation_reasons": [reason.value for reason in validation.reasons],
        "deterministic_evidence_profile": profile.model_dump(mode="json"),
        "verification_routeability": routeability.routeability.value,
        "routeability_reason": routeability.reason.value,
        "destination_evidence_id": routeability.destination_evidence_id,
        "semantic_investigation_warranted": semantic_investigation_warranted(assessment),
        "derived_route": decision.route.value,
        "derived_route_reason": decision.reason.value,
        "investigation_priority": decision.investigation_priority,
    }

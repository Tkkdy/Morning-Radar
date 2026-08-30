from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from morning_radar.ai import AIBudget, AIOutputError
from morning_radar.ai.deepseek_provider import DeepSeekProvider
from morning_radar.evaluation.minimal_atomic_router import (
    AssessmentValidationReason,
    AssessmentValidationStatus,
    CoreClaimEvidenceRelation,
    ImpactLevel,
    MinimalAtomicAssessmentProviderAdapter,
    MinimalCandidateSemanticAssessment,
    MinimalCandidateSemanticAssessmentBatch,
    ProposedRoute,
    ScopeRelevance,
    SystemRoutingContext,
    VerificationRouteability,
    assessment_artifact,
    build_evidence_profile,
    derive_routeability,
    invalid_assessment_decision,
    route_assessment,
    validate_assessment,
    validate_assessment_batch,
)
from morning_radar.models import (
    Candidate,
    CandidateEvidence,
    ClaimScopeDimensions,
    EvidenceAuthority,
    SourceRole,
    TemporalScope,
)


def _evidence(
    evidence_id: str = "evidence-1",
    *,
    excerpt: str = "Official release notes support the claim.",
    authority: EvidenceAuthority = EvidenceAuthority.SELF_AUTHORITATIVE,
    discussion: bool = False,
) -> CandidateEvidence:
    return CandidateEvidence(
        evidence_id=evidence_id,
        url=f"https://example.com/{evidence_id}",
        publisher="Example",
        source_role=SourceRole.OFFICIAL_PRIMARY,
        authority=authority,
        excerpt=excerpt,
        official_surface_verified=authority is EvidenceAuthority.SELF_AUTHORITATIVE,
        support_scope=ClaimScopeDimensions(temporal=TemporalScope.CURRENTLY_EXISTS),
        metadata={"is_discussion_url": True} if discussion else {},
    )


def _candidate(
    candidate_id: str = "candidate-1",
    *,
    evidence: list[CandidateEvidence] | None = None,
) -> Candidate:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    return Candidate(
        id=candidate_id,
        created_at=now,
        updated_at=now,
        raw_item_ids=[f"raw-{candidate_id}"],
        hypothesis="Example adds a supported AI feature",
        evidence=evidence if evidence is not None else [_evidence()],
    )


def _assessment(
    candidate_id: str = "candidate-1",
    *,
    scope: ScopeRelevance = ScopeRelevance.IN_SCOPE,
    impact: ImpactLevel = ImpactLevel.HIGH,
    relation: CoreClaimEvidenceRelation = CoreClaimEvidenceRelation.DIRECT_SUPPORT,
    evidence_ids: list[str] | None = None,
    complete_contract: bool = False,
) -> MinimalCandidateSemanticAssessment:
    return MinimalCandidateSemanticAssessment(
        candidate_id=candidate_id,
        scope_relevance=scope,
        impact_level=impact,
        core_claim_evidence_relation=relation,
        relation_evidence_ids=evidence_ids if evidence_ids is not None else ["evidence-1"],
        missing_evidence=["official confirmation"] if complete_contract else [],
        verification_target="existing destination facts" if complete_contract else None,
        verification_path="read the supplied destination" if complete_contract else None,
    )


def _decision(
    candidate: Candidate,
    assessment: MinimalCandidateSemanticAssessment,
    *,
    context: SystemRoutingContext | None = None,
):
    validation = validate_assessment(candidate, assessment)
    if validation.status is AssessmentValidationStatus.INVALID:
        return invalid_assessment_decision(candidate, validation)
    return route_assessment(
        candidate,
        build_evidence_profile(candidate),
        assessment,
        validation,
        context or SystemRoutingContext(),
    )


def test_bound_direct_support_routes_to_build() -> None:
    assert _decision(_candidate(), _assessment()).route is ProposedRoute.BUILD


def test_source_identity_without_excerpt_cannot_authorize_build() -> None:
    candidate = _candidate(evidence=[_evidence(excerpt="")])
    validation = validate_assessment(candidate, _assessment())

    assert validation.status is AssessmentValidationStatus.INVALID
    assert validation.reasons == [
        AssessmentValidationReason.INVALID_DIRECT_SUPPORT_BINDING
    ]
    assert invalid_assessment_decision(candidate, validation).route is ProposedRoute.UNRESOLVED


def test_system_derives_executable_gap_without_model_action_or_source() -> None:
    candidate = _candidate()
    assessment = _assessment(
        relation=CoreClaimEvidenceRelation.CRITICAL_GAP,
        evidence_ids=[],
        complete_contract=True,
    )
    validation = validate_assessment(candidate, assessment)
    routeability = derive_routeability(
        candidate,
        build_evidence_profile(candidate),
        assessment,
        validation,
        SystemRoutingContext(),
    )

    assert routeability.routeability is VerificationRouteability.EXECUTABLE
    assert routeability.destination_evidence_id == "evidence-1"
    assert _decision(candidate, assessment).route is ProposedRoute.INVESTIGATE


@pytest.mark.parametrize(
    "context",
    [
        SystemRoutingContext(bounded_direct_fetch_supported=False),
        SystemRoutingContext(investigation_budget_available=False),
    ],
)
def test_system_capability_or_budget_shortage_is_unresolved(
    context: SystemRoutingContext,
) -> None:
    assessment = _assessment(
        relation=CoreClaimEvidenceRelation.CRITICAL_GAP,
        evidence_ids=[],
        complete_contract=True,
    )

    assert _decision(_candidate(), assessment, context=context).route is ProposedRoute.UNRESOLVED


def test_missing_destination_is_unsupported_not_drop() -> None:
    candidate = _candidate(evidence=[_evidence(discussion=True)])
    assessment = _assessment(
        relation=CoreClaimEvidenceRelation.CRITICAL_GAP,
        evidence_ids=[],
        complete_contract=True,
    )

    assert _decision(candidate, assessment).route is ProposedRoute.UNRESOLVED


def test_gap_contract_requires_missing_evidence_target_and_path() -> None:
    candidate = _candidate()
    assessment = _assessment(
        relation=CoreClaimEvidenceRelation.CRITICAL_GAP,
        evidence_ids=[],
        complete_contract=True,
    ).model_copy(update={"verification_path": None})
    validation = validate_assessment(candidate, assessment)

    routeability = derive_routeability(
        candidate,
        build_evidence_profile(candidate),
        assessment,
        validation,
        SystemRoutingContext(),
    )

    assert routeability.routeability is VerificationRouteability.INCOMPLETE


@pytest.mark.parametrize(
    "forbidden",
    [
        "verification_action",
        "verification_source_evidence_id",
        "verification_feasibility",
        "semantic_disposition",
        "investigation_priority",
    ],
)
def test_minimal_schema_rejects_system_owned_fields(forbidden: str) -> None:
    payload = _assessment().model_dump(mode="json")
    payload[forbidden] = "forbidden"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        MinimalCandidateSemanticAssessment.model_validate(payload)


def test_candidate_local_invalid_binding_does_not_destroy_batch() -> None:
    candidates = [_candidate(f"candidate-{index}") for index in range(38)]
    assessments = [
        _assessment(candidate.id, evidence_ids=["evidence-1"]) for candidate in candidates
    ]
    assessments[17] = _assessment(candidates[17].id, evidence_ids=["invented"])
    batch = validate_assessment_batch(
        MinimalCandidateSemanticAssessmentBatch(candidates=assessments), candidates
    )

    decisions = []
    for candidate, assessment in zip(candidates, batch.candidates, strict=True):
        decisions.append(_decision(candidate, assessment))

    assert sum(decision.route is ProposedRoute.BUILD for decision in decisions) == 37
    assert decisions[17].route is ProposedRoute.UNRESOLVED


def test_batch_identity_failure_remains_structurally_fatal() -> None:
    with pytest.raises(AIOutputError, match="every Candidate exactly once"):
        validate_assessment_batch(
            MinimalCandidateSemanticAssessmentBatch(candidates=[_assessment("wrong")]),
            [_candidate()],
        )


def test_artifact_contains_no_dual_reference_or_model_action_fields() -> None:
    candidate = _candidate()
    assessment = _assessment(
        relation=CoreClaimEvidenceRelation.CRITICAL_GAP,
        evidence_ids=[],
        complete_contract=True,
    )
    validation = validate_assessment(candidate, assessment)
    decision = _decision(candidate, assessment)
    artifact = assessment_artifact(
        candidate, assessment, validation, decision, SystemRoutingContext()
    )

    raw = artifact["raw_atomic_assessment"]
    assert "verification_action" not in raw
    assert "verification_source_evidence_id" not in raw
    assert "verification_feasibility" not in raw
    assert artifact["derived_route"] == "investigate"
    assert artifact["destination_evidence_id"] == "evidence-1"


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[dict] = []

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=self.content),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=10,
                completion_tokens_details=None,
            ),
            system_fingerprint="offline-test",
        )


def test_adapter_parses_minimal_schema_with_one_bounded_call(tmp_path) -> None:
    response = json.dumps(
        {"candidates": [_assessment().model_dump(mode="json")]},
        ensure_ascii=False,
    )
    completions = _FakeCompletions(response)
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "candidate_triage.md").write_text("Minimal Atomic", encoding="utf-8")
    provider = DeepSeekProvider(
        model="deepseek-v4-flash",
        api_key="offline",
        base_url="https://api.deepseek.com",
        budget=AIBudget(1, 80_000, 1),
        prompt_dir=prompt_dir,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        candidate_triage_temperature=1.0,
    )

    output = MinimalAtomicAssessmentProviderAdapter(provider).assess_candidates(
        [_candidate()]
    )

    assert output.candidates[0].candidate_id == "candidate-1"
    assert len(completions.requests) == 1
    assert completions.requests[0]["temperature"] == 1.0

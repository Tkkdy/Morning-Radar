from datetime import UTC, datetime

from morning_radar.candidates import apply_freshness_guard
from morning_radar.models import (
    Candidate,
    CandidateEvidence,
    EvidenceAuthority,
    EvidenceState,
    ExecutionState,
    SemanticDisposition,
    SourceRole,
)

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def build_candidate(authority: EvidenceAuthority) -> Candidate:
    return Candidate(
        id="candidate-one",
        created_at=NOW,
        updated_at=NOW,
        raw_item_ids=["raw-one"],
        hypothesis="Example now supports a new vision model",
        semantic_disposition=SemanticDisposition.BUILD,
        evidence_state=EvidenceState.SUFFICIENT,
        execution_state=ExecutionState.NOT_NEEDED,
        evidence=[
            CandidateEvidence(
                evidence_id="evidence-one",
                raw_item_id="raw-one",
                url="https://example.com/vision",
                publisher="Example",
                source_role=SourceRole.COMMUNITY_DISCOVERY,
                authority=authority,
                scope="Vision documentation exists.",
            )
        ],
    )


def test_temporal_community_claim_requires_freshness_investigation() -> None:
    [guarded] = apply_freshness_guard(
        [build_candidate(EvidenceAuthority.DISCOVERY_ONLY)]
    )

    assert guarded.semantic_disposition is SemanticDisposition.INVESTIGATE
    assert guarded.execution_state is ExecutionState.NOT_STARTED
    assert guarded.missing_evidence
    assert guarded.verification_path


def test_official_self_authoritative_temporal_claim_keeps_fast_path() -> None:
    candidate = build_candidate(EvidenceAuthority.SELF_AUTHORITATIVE)

    assert apply_freshness_guard([candidate]) == [candidate]

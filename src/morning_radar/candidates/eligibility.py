"""Deterministic Evidence eligibility for semantic BUILD decisions."""

from __future__ import annotations

from morning_radar.models import (
    Candidate,
    CandidateReasonCode,
    EvidenceAuthority,
    EvidenceState,
    ExecutionState,
    SemanticDisposition,
)

UNTRUSTED_BUILD_AUTHORITIES = {
    EvidenceAuthority.DISCOVERY_ONLY,
    EvidenceAuthority.UNVERIFIED_EXTERNAL,
}


def _evidence_can_support_build(candidate: Candidate) -> bool:
    for evidence in candidate.evidence:
        if evidence.authority in UNTRUSTED_BUILD_AUTHORITIES:
            continue
        excerpt = evidence.excerpt.strip()
        if not excerpt:
            continue
        return True
    return False


def apply_build_eligibility_guard(candidates: list[Candidate]) -> list[Candidate]:
    """Downgrade unsupported model BUILD decisions without converting them to DROP."""
    guarded: list[Candidate] = []
    for candidate in candidates:
        if (
            candidate.semantic_disposition is not SemanticDisposition.BUILD
            or _evidence_can_support_build(candidate)
        ):
            guarded.append(candidate)
            continue
        guarded.append(
            Candidate.model_validate(
                {
                    **candidate.model_dump(),
                    "semantic_disposition": SemanticDisposition.INVESTIGATE,
                    "evidence_state": EvidenceState.INSUFFICIENT,
                    "execution_state": ExecutionState.NOT_STARTED,
                    "reason_codes": list(
                        dict.fromkeys(
                            [
                                *candidate.reason_codes,
                                CandidateReasonCode.BUILD_DOWNGRADED_EVIDENCE_INSUFFICIENT,
                            ]
                        )
                    ),
                    "missing_evidence": list(
                        dict.fromkeys(
                            [
                                *candidate.missing_evidence,
                                "可用于事实支持的已验证正文或短摘录",
                            ]
                        )
                    ),
                    "verification_target": (
                        candidate.verification_target or "现有 destination URL 的事实内容"
                    ),
                    "verification_path": (
                        candidate.verification_path
                        or "对现有 destination URL 做 bounded direct fetch"
                    ),
                }
            )
        )
    return guarded

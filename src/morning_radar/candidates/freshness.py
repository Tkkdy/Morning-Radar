"""Lightweight, claim-specific freshness guard for temporal hypotheses."""

from __future__ import annotations

import re

from morning_radar.models import (
    Candidate,
    CandidateReasonCode,
    EvidenceAuthority,
    EvidenceState,
    ExecutionState,
    SemanticDisposition,
)

TEMPORAL_CLAIM_PATTERN = re.compile(
    r"\b(new|launch(?:ed|es)?|release[ds]?|introduced?|first|now supports?|"
    r"preview|ga|new model|new version|v\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)


def apply_freshness_guard(candidates: list[Candidate]) -> list[Candidate]:
    """Route temporal BUILD claims to investigation when timing is not authoritative."""
    guarded: list[Candidate] = []
    for candidate in candidates:
        has_temporal_claim = bool(TEMPORAL_CLAIM_PATTERN.search(candidate.hypothesis))
        has_authoritative_evidence = any(
            evidence.authority is EvidenceAuthority.SELF_AUTHORITATIVE
            for evidence in candidate.evidence
        )
        if (
            has_temporal_claim
            and candidate.semantic_disposition is SemanticDisposition.BUILD
            and not has_authoritative_evidence
        ):
            guarded.append(
                Candidate.model_validate(
                    {
                        **candidate.model_dump(),
                        "semantic_disposition": SemanticDisposition.INVESTIGATE,
                        "evidence_state": EvidenceState.PARTIAL,
                        "execution_state": ExecutionState.NOT_STARTED,
                        "reason_codes": list(
                            dict.fromkeys(
                                [
                                    *candidate.reason_codes,
                                    CandidateReasonCode.CORE_CLAIM_UNSUPPORTED,
                                ]
                            )
                        ),
                        "missing_evidence": list(
                            dict.fromkeys(
                                [
                                    *candidate.missing_evidence,
                                    "事件发生时间以及与既有版本或能力的关系",
                                ]
                            )
                        ),
                        "verification_target": (
                            candidate.verification_target or "现有 destination URL"
                        ),
                        "verification_path": (
                            candidate.verification_path
                            or "对现有 destination URL 做 bounded direct fetch"
                        ),
                        "investigation_priority": max(
                            candidate.investigation_priority, 0.7
                        ),
                    }
                )
            )
        else:
            guarded.append(candidate)
    return guarded

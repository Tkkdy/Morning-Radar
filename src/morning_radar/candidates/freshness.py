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
    TemporalScope,
)

TEMPORAL_CLAIM_PATTERN = re.compile(
    r"\b(new|launch(?:ed|es)?|release[ds]?|introduced?|first|now supports?|"
    r"preview|ga|new model|new version|v\d+(?:\.\d+)*)\b|"
    r"新发布|刚刚发布|今天(?:发布|推出|上线)|首次|首个|现已支持|正式发布|全面开放",
    re.IGNORECASE,
)

FIRST_CLAIM_PATTERN = re.compile(r"\bfirst(?: ever)?\b|首次|首个", re.IGNORECASE)


def apply_freshness_guard(candidates: list[Candidate]) -> list[Candidate]:
    """Route temporal BUILD claims to investigation when timing is not authoritative."""
    guarded: list[Candidate] = []
    for candidate in candidates:
        has_temporal_claim = bool(TEMPORAL_CLAIM_PATTERN.search(candidate.hypothesis))
        required_temporal_scope = (
            TemporalScope.FIRST_EVER
            if FIRST_CLAIM_PATTERN.search(candidate.hypothesis)
            else TemporalScope.NEWLY_RELEASED
        )
        has_authoritative_evidence = any(
            evidence.authority is EvidenceAuthority.SELF_AUTHORITATIVE
            and (
                evidence.support_scope.temporal is required_temporal_scope
                or (
                    required_temporal_scope is TemporalScope.NEWLY_RELEASED
                    and evidence.support_scope.temporal is TemporalScope.FIRST_EVER
                )
            )
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

"""Resolve one concrete Evidence gap per bounded Candidate investigation."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime

from morning_radar.ai.provider import AIProvider
from morning_radar.candidates.engine import triage_candidates
from morning_radar.evidence.http import EvidenceFetchError, SafeEvidenceFetcher
from morning_radar.evidence.official import OfficialSurfaceResolver
from morning_radar.models import (
    Candidate,
    CandidateEvidence,
    EvidenceAuthority,
    ExecutionState,
    SemanticDisposition,
    SourceRole,
    StatementType,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EvidenceResolutionResult:
    candidates: list[Candidate] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _destination(candidate: Candidate) -> str | None:
    non_discussion = [
        evidence
        for evidence in candidate.evidence
        if not evidence.metadata.get("is_discussion_url")
    ]
    return non_discussion[0].url if non_discussion else None


def resolve_evidence(
    candidates: list[Candidate],
    *,
    provider: AIProvider,
    fetcher: SafeEvidenceFetcher,
    official_resolver: OfficialSurfaceResolver,
    now: datetime,
    maximum_investigations: int,
    maximum_triage_input_characters: int,
) -> EvidenceResolutionResult:
    investigations = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.semantic_disposition is SemanticDisposition.INVESTIGATE
        ),
        key=lambda candidate: (-candidate.investigation_priority, candidate.id),
    )
    selected = investigations[:maximum_investigations]
    selected_ids = {candidate.id for candidate in selected}
    resolved: dict[str, Candidate] = {candidate.id: candidate for candidate in candidates}
    fetched: list[Candidate] = []
    fetch_count = 0
    network_failed = 0
    parse_failed = 0
    for candidate in investigations[maximum_investigations:]:
        resolved[candidate.id] = candidate.model_copy(
            update={"execution_state": ExecutionState.DEFERRED_BY_BUDGET}
        )
    for candidate in selected:
        target = _destination(candidate)
        if target is None:
            resolved[candidate.id] = candidate.model_copy(
                update={"execution_state": ExecutionState.FAILED_PARSE}
            )
            parse_failed += 1
            continue
        try:
            result = fetcher.fetch(target)
        except EvidenceFetchError as exc:
            state = (
                ExecutionState.FAILED_PARSE
                if exc.reason in {"PARSE_FAILED", "UNSUPPORTED_CONTENT_TYPE"}
                else ExecutionState.FAILED_NETWORK
            )
            resolved[candidate.id] = candidate.model_copy(update={"execution_state": state})
            if state is ExecutionState.FAILED_PARSE:
                parse_failed += 1
            else:
                network_failed += 1
            LOGGER.warning("Evidence fetch failed candidate=%s reason=%s", candidate.id, exc.reason)
            continue
        fetch_count += 1
        trust = official_resolver.verify(result.final_url)
        identity = hashlib.sha256(result.final_url.encode()).hexdigest()[:20]
        evidence = CandidateEvidence(
            evidence_id=f"evidence-fetch-{identity}",
            url=result.final_url,
            publisher=trust.entity if trust else result.final_url,
            source_role=(
                SourceRole.OFFICIAL_PRIMARY if trust else SourceRole.EDITORIAL
            ),
            statement_type=(
                StatementType.FACTUAL_ANNOUNCEMENT
                if trust
                else StatementType.UNKNOWN
            ),
            authority=(
                EvidenceAuthority.SELF_AUTHORITATIVE
                if trust
                else EvidenceAuthority.INDEPENDENT_REPORTING
            ),
            scope=result.text[:1000],
            excerpt=result.text[:2000],
            official_surface_verified=trust is not None,
            retrieved_at=now,
            metadata={
                "canonical_url": result.canonical_url,
                "redirect_chain": list(result.redirect_chain),
                "response_bytes": result.response_bytes,
            },
        )
        updated = candidate.model_copy(
            update={
                "evidence": [*candidate.evidence, evidence],
                "execution_state": ExecutionState.EXECUTED,
                "updated_at": now,
            }
        )
        fetched.append(updated)
        resolved[candidate.id] = updated
    if fetched:
        retriaged = triage_candidates(
            fetched,
            provider=provider,
            maximum_batch_items=len(fetched),
            maximum_input_characters=maximum_triage_input_characters,
        ).candidates
        for candidate in retriaged:
            resolved[candidate.id] = (
                candidate
                if candidate.execution_state is ExecutionState.FAILED_AI
                else candidate.model_copy(
                    update={"execution_state": ExecutionState.EXECUTED}
                )
            )
    final = [resolved[candidate.id] for candidate in candidates]
    return EvidenceResolutionResult(
        candidates=final,
        stats={
            "investigation_recommended": len(investigations),
            "investigation_executed": len(selected_ids),
            "investigation_deferred": max(0, len(investigations) - len(selected_ids)),
            "evidence_http_fetches": fetch_count,
            "investigation_failed_network": network_failed,
            "investigation_failed_parse": parse_failed,
        },
    )

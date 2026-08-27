"""High-recall Candidate admission followed by bounded semantic triage."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from morning_radar.ai import AIBudgetExceeded, AIOutputError
from morning_radar.ai.provider import AIProvider
from morning_radar.models import (
    AssertionScope,
    AvailabilityScope,
    Candidate,
    CandidateEvidence,
    CandidateReasonCode,
    ClaimScopeDimensions,
    EvidenceAuthority,
    ExecutionState,
    ObservationQuality,
    RadarDisposition,
    RadarEvidenceRef,
    RadarSignal,
    RawItem,
    SemanticDisposition,
    SourceRole,
    StatementType,
    TemporalScope,
)
from morning_radar.processing.grouping import group_items_by_normalized_title
from morning_radar.provenance import verified_source_urls

LOGGER = logging.getLogger(__name__)
RELEASE_PATTERN = re.compile(
    r"\b(release[ds]?|launch(?:ed)?|introduc(?:e[ds]?|ing)|preview|ga|"
    r"v\d+(?:\.\d+)*)\b|发布|推出|上线|预览|正式可用",
    re.IGNORECASE,
)
TEMPORAL_RELEASE_PATTERN = re.compile(
    r"\b(new|release[ds]?|launch(?:ed)?|introduced?|first|preview|ga)\b|"
    r"新发布|首次|发布|推出|上线|预览|正式可用",
    re.IGNORECASE,
)
GA_PATTERN = re.compile(r"\b(ga|general availability)\b|全面开放|正式可用", re.IGNORECASE)
DEVELOPER_PATTERN = re.compile(
    r"\b(api|sdk|model|agent|mcp|github|repository|endpoint|inference|developer|coding)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CandidateRunResult:
    candidates: list[Candidate] = field(default_factory=list)
    stats: dict[str, int | bool] = field(default_factory=dict)


def _candidate_id(raw_item_ids: list[str]) -> str:
    identity = "|".join(sorted(raw_item_ids))
    return f"candidate-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


def _evidence_id(item: RawItem, url: str) -> str:
    identity = f"{item.id}|{url}"
    return f"evidence-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


def _verified_github_repository(item: RawItem, url: str) -> str | None:
    repository = item.metadata.get("repository")
    if not isinstance(repository, str) and len(item.repository_candidates) == 1:
        repository = item.repository_candidates[0]
    if item.source_type not in {"github", "fixture_github"} or not isinstance(
        repository, str
    ):
        return None
    parsed = urlsplit(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if parsed.hostname != "github.com" or len(path_parts) < 2:
        return None
    path_repository = "/".join(path_parts[:2])
    return repository if path_repository.casefold() == repository.casefold() else None


def _official_entities(item: RawItem, url: str) -> list[str]:
    repository = _verified_github_repository(item, url)
    if repository:
        return [repository]
    values = [*item.company_candidates]
    configured_entity = item.metadata.get("entity")
    if isinstance(configured_entity, str) and configured_entity.strip():
        values.append(configured_entity.strip())
    if not values and item.source_role is SourceRole.OFFICIAL_PRIMARY:
        values.append(item.source_name)
    return list(dict.fromkeys(values))


def _authority(item: RawItem, url: str) -> EvidenceAuthority:
    github_repository = _verified_github_repository(item, url)
    if github_repository or (
        item.source_type != "github"
        and (item.source_role is SourceRole.OFFICIAL_PRIMARY or item.metadata.get("official"))
    ):
        return EvidenceAuthority.SELF_AUTHORITATIVE
    if item.statement_type in {
        StatementType.FIRSTHAND_OBSERVATION,
        StatementType.TEST_EXPERIMENT,
    }:
        return EvidenceAuthority.FIRSTHAND_OBSERVATION
    if item.source_role is SourceRole.EDITORIAL:
        return EvidenceAuthority.INDEPENDENT_REPORTING
    return EvidenceAuthority.DISCOVERY_ONLY


def _support_scope(
    item: RawItem,
    *,
    authority: EvidenceAuthority,
) -> ClaimScopeDimensions:
    text = " ".join((item.title, item.summary, item.content_excerpt))
    if authority is EvidenceAuthority.SELF_AUTHORITATIVE:
        return ClaimScopeDimensions(
            availability=(
                AvailabilityScope.GA if GA_PATTERN.search(text) else AvailabilityScope.UNKNOWN
            ),
            temporal=(
                TemporalScope.NEWLY_RELEASED
                if item.published_at is not None and TEMPORAL_RELEASE_PATTERN.search(text)
                else TemporalScope.CURRENTLY_EXISTS
            ),
            assertion=AssertionScope.OFFICIALLY_ANNOUNCED,
        )
    if authority is EvidenceAuthority.FIRSTHAND_OBSERVATION:
        return ClaimScopeDimensions(
            availability=AvailabilityScope.ONE_ACCOUNT,
            temporal=(
                TemporalScope.OBSERVED_NOW
                if item.published_at is not None
                else TemporalScope.UNKNOWN
            ),
            assertion=AssertionScope.OBSERVED,
        )
    if authority is EvidenceAuthority.INDEPENDENT_REPORTING:
        return ClaimScopeDimensions(
            temporal=TemporalScope.CURRENTLY_EXISTS,
            assertion=AssertionScope.INDEPENDENTLY_VERIFIED,
        )
    return ClaimScopeDimensions()


def _candidate_evidence(item: RawItem) -> list[CandidateEvidence]:
    text = " ".join((item.title, item.summary, item.content_excerpt)).casefold()
    observation_quality = (
        ObservationQuality(
            firsthandness=item.statement_type
            in {StatementType.FIRSTHAND_OBSERVATION, StatementType.TEST_EXPERIMENT},
            specificity=bool(item.product_candidates or len(item.title.split()) >= 5),
            artifact_support=bool(
                re.search(
                    r"\b(model id|endpoint|response|log|build|screenshot|artifact)\b",
                    text,
                )
            ),
            temporal_coherence=item.published_at is not None,
            identity_history=bool(item.metadata.get("practitioner_id")),
            independent_reproducibility=False,
        )
        if item.source_role is SourceRole.PRACTITIONER
        else None
    )
    return [
        CandidateEvidence(
            evidence_id=_evidence_id(item, url),
            raw_item_id=item.id,
            url=url,
            publisher=item.source_name,
            source_role=(
                SourceRole.OFFICIAL_PRIMARY
                if item.metadata.get("official")
                else item.source_role
            ),
            statement_type=item.statement_type,
            authority=(authority := _authority(item, url)),
            authoritative_for=(
                _official_entities(item, url)
                if authority is EvidenceAuthority.SELF_AUTHORITATIVE
                else []
            ),
            subject_entities=list(
                dict.fromkeys([*item.company_candidates, *_official_entities(item, url)])
            ),
            support_scope=_support_scope(item, authority=authority),
            scope=item.title,
            excerpt=(item.content_excerpt or item.summary)[:1000],
            official_surface_verified=authority is EvidenceAuthority.SELF_AUTHORITATIVE,
            retrieved_at=item.fetched_at,
            metadata={
                "source_type": item.source_type,
                "is_discussion_url": url != item.url,
                "score": item.metadata.get("score", 0),
                "comments": item.metadata.get("comments", 0),
            },
            observation_quality=observation_quality,
        )
        for url in verified_source_urls(item)
    ]


def _must_triage(items: list[RawItem]) -> bool:
    text = " ".join(f"{item.title} {item.url}" for item in items)
    likely_official = any(
        item.source_role is SourceRole.OFFICIAL_PRIMARY or item.metadata.get("official")
        for item in items
    )
    strong_community = any(
        int(item.metadata.get("score", 0) or 0) >= 150
        or int(item.metadata.get("comments", 0) or 0) >= 80
        for item in items
    )
    return bool(
        likely_official
        or RELEASE_PATTERN.search(text)
        and (strong_community or DEVELOPER_PATTERN.search(text))
        or strong_community and DEVELOPER_PATTERN.search(text)
    )


def admit_candidates(items: list[RawItem], *, now: datetime) -> list[Candidate]:
    """Admit every eligible RawItem group before semantic resource decisions."""
    candidates: list[Candidate] = []
    for group in group_items_by_normalized_title(items):
        raw_ids = [item.id for item in group]
        evidence = [ref for item in group for ref in _candidate_evidence(item)]
        must_triage = _must_triage(group)
        candidates.append(
            Candidate(
                id=_candidate_id(raw_ids),
                created_at=now,
                updated_at=now,
                raw_item_ids=raw_ids,
                hypothesis=group[0].title,
                entity_names=list(
                    dict.fromkeys(name for item in group for name in item.company_candidates)
                ),
                product_names=list(
                    dict.fromkeys(name for item in group for name in item.product_candidates)
                ),
                topic_names=list(
                    dict.fromkeys(name for item in group for name in item.topic_candidates)
                ),
                reason_codes=(
                    [CandidateReasonCode.HIGH_RECALL_GUARDRAIL] if must_triage else []
                ),
                must_triage=must_triage,
                evidence=evidence,
                statement_type=group[0].statement_type,
                practice_signal_kind=group[0].practice_signal_kind,
            )
        )
    return candidates


def _triage_order(candidate: Candidate) -> tuple[int, float, str]:
    community = max(
        (
            float(evidence.metadata.get("score", 0) or 0)
            for evidence in candidate.evidence
        ),
        default=0,
    )
    return (0 if candidate.must_triage else 1, -community, candidate.id)


def triage_candidates(
    candidates: list[Candidate],
    *,
    provider: AIProvider,
    maximum_batch_items: int,
    maximum_input_characters: int,
) -> CandidateRunResult:
    """Understand Candidates in cheap batches; resource failure never means DROP."""
    ordered = sorted(candidates, key=_triage_order)
    results: dict[str, Candidate] = {candidate.id: candidate for candidate in candidates}
    triaged = 0
    failed = 0
    deferred = 0
    cursor = 0
    input_used = 0
    deferred_candidates: list[Candidate] = []
    while cursor < len(ordered):
        batch: list[Candidate] = []
        while cursor < len(ordered) and len(batch) < maximum_batch_items:
            candidate = ordered[cursor]
            encoded = candidate.model_dump_json()
            if input_used + len(encoded) > maximum_input_characters:
                deferred_candidates = ordered[cursor:]
                deferred += len(deferred_candidates)
                cursor = len(ordered)
                break
            batch.append(candidate)
            input_used += len(encoded)
            cursor += 1
        if not batch:
            break
        try:
            output = provider.triage_candidates(batch)
        except AIBudgetExceeded:
            LOGGER.warning("Candidate triage deferred: shared AI budget unavailable")
            budget_deferred = [*batch, *ordered[cursor:]]
            deferred += len(budget_deferred)
            for candidate in budget_deferred:
                results[candidate.id] = candidate.model_copy(
                    update={"execution_state": ExecutionState.DEFERRED_BY_BUDGET}
                )
            cursor = len(ordered)
            break
        except (AIOutputError, OSError, ValueError):
            LOGGER.exception("Candidate triage degradation: batch retained as unresolved")
            failed += len(batch)
            for candidate in batch:
                results[candidate.id] = candidate.model_copy(
                    update={"execution_state": ExecutionState.FAILED_AI}
                )
            continue
        drafts = {draft.candidate_id: draft for draft in output.candidates}
        for candidate in batch:
            draft = drafts[candidate.id]
            reason_codes = list(
                dict.fromkeys([*candidate.reason_codes, *draft.reason_codes])
            )
            try:
                results[candidate.id] = Candidate.model_validate(
                    {
                        **candidate.model_dump(),
                        "updated_at": candidate.updated_at,
                        "hypothesis": draft.hypothesis,
                        "potential_novelty": draft.potential_novelty,
                        "potential_impact": draft.potential_impact,
                        "affected_audiences": draft.affected_audiences,
                        "impact_mechanism": draft.impact_mechanism,
                        "alternative_explanation": draft.alternative_explanation,
                        "semantic_disposition": draft.semantic_disposition,
                        "evidence_state": draft.evidence_state,
                        "execution_state": (
                            ExecutionState.NOT_NEEDED
                            if draft.semantic_disposition
                            in {SemanticDisposition.BUILD, SemanticDisposition.DROP}
                            else ExecutionState.NOT_STARTED
                        ),
                        "reason_codes": reason_codes,
                        "rationale": draft.rationale,
                        "missing_evidence": draft.missing_evidence,
                        "verification_target": draft.verification_target,
                        "verification_path": draft.verification_path,
                        "investigation_priority": draft.investigation_priority,
                    }
                )
            except ValueError:
                failed += 1
                results[candidate.id] = candidate.model_copy(
                    update={"execution_state": ExecutionState.FAILED_AI}
                )
            else:
                triaged += 1
    for candidate in deferred_candidates:
        results[candidate.id] = candidate.model_copy(
            update={"execution_state": ExecutionState.DEFERRED_BY_BUDGET}
        )
    final = [results[candidate.id] for candidate in candidates]
    return CandidateRunResult(
        candidates=final,
        stats={
            "candidate_admitted": len(candidates),
            "candidate_triaged": triaged,
            "candidate_triage_failed": failed,
            "candidate_triage_deferred": deferred,
            "candidate_triage_input_characters": input_used,
            "candidate_build": sum(
                item.semantic_disposition is SemanticDisposition.BUILD for item in final
            ),
            "candidate_investigate": sum(
                item.semantic_disposition is SemanticDisposition.INVESTIGATE for item in final
            ),
            "candidate_drop": sum(
                item.semantic_disposition is SemanticDisposition.DROP for item in final
            ),
        },
    )


def candidate_story_inputs(
    candidates: list[Candidate], items: list[RawItem]
) -> tuple[list[RawItem], dict[str, Candidate]]:
    build = {
        raw_id: candidate
        for candidate in candidates
        if candidate.semantic_disposition is SemanticDisposition.BUILD
        for raw_id in candidate.raw_item_ids
    }
    return [item for item in items if item.id in build], build


def radar_signals_from_candidates(
    candidates: list[Candidate], *, maximum_signals: int
) -> list[RadarSignal]:
    """Retain valuable unresolved Candidates without weakening the Story boundary."""
    notable_reasons = {
        CandidateReasonCode.POTENTIAL_CAPABILITY_CHANGE,
        CandidateReasonCode.POTENTIAL_WORKFLOW_CHANGE,
        CandidateReasonCode.POTENTIAL_ECOSYSTEM_CHANGE,
        CandidateReasonCode.DEVELOPER_IMPACT,
        CandidateReasonCode.FIRSTHAND_OBSERVATION,
    }

    def evidence_is_radar_grade(candidate: Candidate) -> bool:
        for evidence in candidate.evidence:
            if evidence.authority in {
                EvidenceAuthority.SELF_AUTHORITATIVE,
                EvidenceAuthority.INDEPENDENT_REPORTING,
            }:
                return True
            quality = evidence.observation_quality
            if (
                evidence.authority is EvidenceAuthority.FIRSTHAND_OBSERVATION
                and quality
                and quality.firsthandness
                and quality.specificity
                and quality.artifact_support
            ):
                return True
            if (
                evidence.source_role is SourceRole.COMMUNITY_DISCOVERY
                and float(evidence.metadata.get("score", 0) or 0) >= 150
            ):
                return True
        return False

    unresolved = [
        candidate
        for candidate in candidates
        if candidate.semantic_disposition is SemanticDisposition.INVESTIGATE
        and candidate.potential_impact
        and candidate.investigation_priority >= 0.7
        and bool(set(candidate.reason_codes) & notable_reasons)
        and evidence_is_radar_grade(candidate)
    ]
    unresolved.sort(key=lambda item: (-item.investigation_priority, item.id))
    signals: list[RadarSignal] = []
    for candidate in unresolved[:maximum_signals]:
        raw_evidence = [item for item in candidate.evidence if item.raw_item_id]
        if not raw_evidence:
            continue
        refs = [
            RadarEvidenceRef(
                raw_item_id=item.raw_item_id or "",
                url=item.url,
                source_role=item.source_role,
            )
            for item in raw_evidence
        ]
        identity = hashlib.sha256(candidate.id.encode()).hexdigest()[:20]
        signals.append(
            RadarSignal(
                id=f"radar-{identity}",
                observed_at=candidate.created_at,
                claim=candidate.hypothesis,
                why_notable=candidate.potential_impact,
                support_refs=refs,
                source_roles=list(dict.fromkeys(item.source_role for item in refs)),
                missing_evidence=candidate.missing_evidence,
                uncertainty=(
                    candidate.rationale
                    or "该 Candidate 尚未跨过 Claim–Evidence Story Boundary。"
                ),
                statement_type=candidate.statement_type,
                research_disposition=RadarDisposition.RADAR_SIGNAL,
            )
        )
    return signals

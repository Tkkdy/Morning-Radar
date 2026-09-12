"""Create and resolve a bounded daily batch of high-value research cases."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from morning_radar.ai import AIBudgetExceeded, AIOutputError
from morning_radar.ai.errors import AIAuthenticationError, AIBillingUnavailable
from morning_radar.ai.provider import AIProvider
from morning_radar.ai.request_payload import (
    evidence_snapshot,
    fit_research_request,
    freeze_call_meta,
    get_call_meta,
    slim_evidence,
)
from morning_radar.models import (
    RadarSignal,
    RawItem,
    ResearchCase,
    ResearchDisposition,
    SourceRole,
    StatementType,
)
from morning_radar.processing import normalize_url

LOGGER = logging.getLogger(__name__)
VAGUE_PRAISE = ("太牛", "最好", "best in the world", "amazing", "awesome")


@dataclass(frozen=True, slots=True)
class ResearchRunResult:
    cases: list[ResearchCase] = field(default_factory=list)
    verified_item_ids: frozenset[str] = frozenset()
    radar_signals: list[RadarSignal] = field(default_factory=list)
    stats: dict[str, int | bool] = field(default_factory=dict)
    item_outcomes: dict[str, object] = field(default_factory=dict)
    omitted_cases: dict[str, str] = field(default_factory=dict)
    case_resolutions: dict[str, object] = field(default_factory=dict)
    call_meta: dict | None = None
    case_call_meta: dict[str, dict] = field(default_factory=dict)
    planned_cases: list[ResearchCase] = field(default_factory=list)


def _is_research_lead(item: RawItem) -> bool:
    if item.source_role in {SourceRole.PRACTITIONER, SourceRole.UPSTREAM_DISCOVERY}:
        return True
    return bool(
        item.source_role == SourceRole.COMMUNITY_DISCOVERY
        and (
            item.metadata.get("selection_reason")
            in {"high_signal_discovery", "watchlist_discovery"}
            or "watchlist_discovery" in item.metadata.get("discovery_reasons", [])
        )
    )


def _is_concrete(item: RawItem) -> bool:
    text = " ".join((item.title, item.summary, item.content_excerpt)).casefold()
    if any(value in text for value in VAGUE_PRAISE) and not (
        item.practice_signal_kind or item.topic_candidates or item.product_candidates
    ):
        return False
    return bool(
        len(item.title.split()) >= 4
        or len(item.summary.strip()) >= 40
        or item.practice_signal_kind
        or item.product_candidates
    )


def _content_version(item: RawItem) -> str | None:
    value = item.metadata.get("content_version")
    return value if isinstance(value, str) and value else None


def build_research_cases(
    items: list[RawItem],
    *,
    maximum_cases: int,
) -> list[ResearchCase]:
    """Gate leads deterministically and join only evidence already in this run."""
    leads = [item for item in items if _is_research_lead(item) and _is_concrete(item)]
    leads.sort(
        key=lambda item: (
            0 if item.source_role == SourceRole.PRACTITIONER else 1,
            -int(item.metadata.get("score", 0) or 0),
            -(item.published_at or item.fetched_at).timestamp(),
            item.id,
        )
    )
    cases: list[ResearchCase] = []
    for lead in leads[:maximum_cases]:
        lead_url = normalize_url(lead.url)
        lead_entities = set(lead.company_candidates)
        lead_products = set(lead.product_candidates)
        support: list[RawItem] = []
        for item in items:
            if item.id == lead.id or item.source_role == SourceRole.UPSTREAM_DISCOVERY:
                continue
            same_original = normalize_url(item.url) == lead_url
            anchored_primary = bool(
                item.source_role == SourceRole.OFFICIAL_PRIMARY
                and (
                    lead_entities.intersection(item.company_candidates)
                    or lead_products.intersection(item.product_candidates)
                )
            )
            if same_original:
                support.append((item, "same_url"))
            elif anchored_primary:
                support.append((item, "official_primary_entity_overlap"))
        identity = hashlib.sha256(lead.id.encode()).hexdigest()[:20]
        cases.append(
            ResearchCase(
                id=f"research-{identity}",
                observed_at=lead.fetched_at,
                claim=lead.title,
                entity_keys=lead.company_candidates,
                product_keys=lead.product_candidates,
                topic_keys=lead.topic_candidates,
                statement_type=lead.statement_type,
                practice_signal_kind=lead.practice_signal_kind,
                lead=evidence_snapshot(
                    lead,
                    association_basis="lead",
                    content_version=_content_version(lead),
                ),
                supporting_evidence=[
                    evidence_snapshot(
                        item,
                        association_basis=basis,
                        content_version=_content_version(item),
                    )
                    for item, basis in support[:3]
                ],
            )
        )
    return cases


def resolve_research(
    items: list[RawItem],
    *,
    provider: AIProvider,
    maximum_cases: int,
    maximum_radar_signals: int,
    maximum_input_characters: int = 12000,
    item_retry_attempts: int = 0,
    split_retry_attempts: int = 0,
) -> ResearchRunResult:
    planned_cases = build_research_cases(items, maximum_cases=maximum_cases)
    cases = planned_cases
    had_cases = bool(cases)
    topic_context = getattr(provider, "topic_context", None)
    fitted = fit_research_request(
        cases,
        topic_context=topic_context,
        maximum_characters=maximum_input_characters,
    )
    omitted = dict(fitted.omitted)
    cases = fitted.included
    research_input_characters = len(fitted.payload_text)
    if not cases:
        from morning_radar.intake.models import ReasonCode

        item_outcomes = {
            case.lead.raw_item_id: ReasonCode.RESEARCH_DEFERRED
            for case in planned_cases
            if case.id in omitted or fitted.unexecuted
        }
        return ResearchRunResult(
            planned_cases=planned_cases,
            omitted_cases=omitted,
            item_outcomes=item_outcomes,
            call_meta=None,
            stats={
                "research_cases": 0,
                "research_logical_ai_calls": 0,
                "research_input_characters": research_input_characters,
                "research_budget_skipped": had_cases,
                "research_unresolved": 0,
                "research_omitted_cases": len(omitted),
                "research_truncated_fields": fitted.truncated_fields,
                "research_unexecuted": fitted.unexecuted,
            },
        )
    from morning_radar.intake.models import ReasonCode

    budget = getattr(provider, "budget", None)
    calls_before = getattr(budget, "calls_used", 0)
    batch, isolation_stats, unavailable, case_reasons, fatal_kind, case_call_meta = (
        _resolve_research_batch(
            cases,
            provider=provider,
            item_retry_attempts=item_retry_attempts,
            split_retry_attempts=split_retry_attempts,
        )
    )
    if unavailable and not batch.cases:
        LOGGER.exception("Research degradation: batch resolution failed; signals omitted")
        item_outcomes = {
            case.lead.raw_item_id: case_reasons.get(
                case.id,
                ReasonCode.RESEARCH_FATAL if fatal_kind else ReasonCode.RESEARCH_OUTPUT_TRUNCATED,
            )
            for case in cases
        }
        for case in planned_cases:
            if case.id in omitted:
                item_outcomes[case.lead.raw_item_id] = ReasonCode.RESEARCH_DEFERRED
        return ResearchRunResult(
            cases=cases,
            planned_cases=planned_cases,
            stats={
                "research_cases": len(cases),
                "research_input_characters": research_input_characters,
                "research_logical_ai_calls": getattr(budget, "calls_used", 0) - calls_before,
                "research_unavailable": True,
                "research_unresolved": len(cases),
                "research_omitted_cases": len(omitted),
                **isolation_stats,
            },
            item_outcomes=item_outcomes,
            omitted_cases=omitted,
            call_meta=freeze_call_meta(get_call_meta(batch)),
            case_call_meta=case_call_meta,
        )

    cases_by_id = {case.id: case for case in cases}
    verified: set[str] = set()
    signals: list[RadarSignal] = []
    for resolved in batch.cases:
        case = cases_by_id.get(resolved.case_id)
        if case is None:
            continue
        # Scope is decided semantically inside the existing batch. Fail closed so
        # a discovery lead cannot bypass the normal Story relevance boundary.
        if not resolved.in_scope or not resolved.scope_rationale.strip():
            continue
        disposition = resolved.disposition
        if disposition == ResearchDisposition.VERIFIED_STORY_CANDIDATE:
            if case.supporting_evidence:
                verified.add(case.lead.raw_item_id)
            else:
                disposition = ResearchDisposition.RADAR_SIGNAL
        if disposition != ResearchDisposition.RADAR_SIGNAL:
            continue
        if not resolved.why_notable or not resolved.uncertainty:
            continue
        identity = hashlib.sha256(case.id.encode()).hexdigest()[:20]
        refs = [slim_evidence(case.lead), *[slim_evidence(ref) for ref in case.supporting_evidence]]
        signals.append(
            RadarSignal(
                id=f"radar-{identity}",
                observed_at=case.observed_at,
                claim=resolved.claim,
                why_notable=resolved.why_notable,
                support_refs=refs,
                source_roles=list(dict.fromkeys(ref.source_role for ref in refs)),
                missing_evidence=resolved.missing_evidence,
                uncertainty=resolved.uncertainty,
                statement_type=resolved.statement_type,
            )
        )
    signals = signals[:maximum_radar_signals]
    from morning_radar.intake.models import ReasonCode

    item_outcomes: dict[str, ReasonCode] = {}
    for original in planned_cases:
        if original.id in omitted:
            item_outcomes[original.lead.raw_item_id] = ReasonCode.RESEARCH_DEFERRED
    resolved_by_id = {item.case_id: item for item in batch.cases}
    case_resolutions = {item.case_id: item for item in batch.cases}
    for case in cases:
        lead_id = case.lead.raw_item_id
        if case.id in case_reasons:
            item_outcomes[lead_id] = case_reasons[case.id]
            continue
        resolved = resolved_by_id.get(case.id)
        if resolved is not None and (not resolved.in_scope or not resolved.scope_rationale.strip()):
            item_outcomes[lead_id] = ReasonCode.RESEARCH_OUT_OF_SCOPE
    return ResearchRunResult(
        cases=cases,
        planned_cases=planned_cases,
        verified_item_ids=frozenset(verified),
        radar_signals=signals,
        stats={
            "research_cases": len(cases),
            "research_input_characters": research_input_characters,
            "research_verified_story_candidates": len(verified),
            "research_unresolved": max(0, len(cases) - len(verified) - len(signals)),
            "radar_signals": len(signals),
            "research_logical_ai_calls": getattr(budget, "calls_used", 0) - calls_before,
            "research_omitted_cases": len(omitted),
            "research_truncated_fields": fitted.truncated_fields,
            **isolation_stats,
        },
        item_outcomes=item_outcomes,
        omitted_cases=omitted,
        case_resolutions=case_resolutions,
        call_meta=freeze_call_meta(get_call_meta(batch)),
        case_call_meta=case_call_meta,
    )


class _SharedRetries:
    def __init__(self, split: int, item: int) -> None:
        self.split = max(0, split)
        self.item = max(0, item)


def _call_research(provider, cases):
    from morning_radar.ai.models import ResearchResolutionBatch
    from morning_radar.research.isolation import IsolatedResearchResult

    isolator = getattr(provider, "resolve_research_cases_isolated", None)
    try:
        if isolator is not None:
            isolated = isolator(cases)
        else:
            batch = provider.resolve_research_cases(cases)
            isolated = IsolatedResearchResult(batch=batch)
        if isolated.call_meta is None:
            isolated.call_meta = freeze_call_meta(
                get_call_meta(isolated.batch) or getattr(provider, "last_call_meta", None)
            )
        else:
            isolated.call_meta = freeze_call_meta(isolated.call_meta)
        return isolated
    except (AIBillingUnavailable, AIAuthenticationError, AIBudgetExceeded) as exc:
        meta = freeze_call_meta(
            getattr(exc, "call_meta", None) or getattr(provider, "last_call_meta", None)
        )
        return IsolatedResearchResult(
            batch=ResearchResolutionBatch(),
            fatal_kind=type(exc).__name__,
            error=str(exc),
            call_meta=meta,
        )
    except AIOutputError as exc:
        truncated = "truncated" in str(exc).casefold()
        return IsolatedResearchResult(
            batch=ResearchResolutionBatch(),
            truncated=truncated,
            error=str(exc),
            call_meta=freeze_call_meta(
                getattr(exc, "call_meta", None) or getattr(provider, "last_call_meta", None)
            ),
        )


def _reasons_from_isolated(cases, isolated, *, ReasonCode):
    reasons = {}
    success = {item.case_id for item in isolated.batch.cases}
    for case in cases:
        if case.id in success:
            continue
        if case.id in isolated.invalid_ids:
            reasons[case.id] = ReasonCode.RESEARCH_OUTPUT_INVALID
        elif case.id in isolated.missing_ids:
            reasons[case.id] = ReasonCode.RESEARCH_CASE_MISSING
        elif isolated.fatal_kind:
            reasons[case.id] = ReasonCode.RESEARCH_FATAL
        elif isolated.truncated:
            reasons[case.id] = ReasonCode.RESEARCH_OUTPUT_TRUNCATED
        else:
            reasons[case.id] = ReasonCode.RESEARCH_CASE_MISSING
    return reasons


def _case_call_meta_from_isolated(cases, isolated) -> dict[str, dict]:
    meta = freeze_call_meta(isolated.call_meta)
    if meta is None:
        return {}
    involved = {item.case_id for item in isolated.batch.cases}
    involved.update(case_id for case_id in isolated.invalid_ids if case_id)
    involved.update(case_id for case_id in isolated.missing_ids if case_id)
    if (isolated.truncated or isolated.fatal_kind) and not isolated.batch.cases:
        involved.update(case.id for case in cases)
    mapping: dict[str, dict] = {}
    for case in cases:
        if case.id in involved:
            mapping[case.id] = dict(meta)
    return mapping


def _resolve_research_batch(
    cases,
    *,
    provider: AIProvider,
    retries: _SharedRetries | None = None,
    item_retry_attempts: int = 0,
    split_retry_attempts: int = 0,
):
    from morning_radar.ai.models import ResearchResolutionBatch
    from morning_radar.intake.models import ReasonCode

    if retries is None:
        retries = _SharedRetries(split_retry_attempts, item_retry_attempts)
    isolated = _call_research(provider, cases)
    isolation_stats = {
        "research_invalid_cases": len(isolated.invalid_ids),
        "research_missing_cases": len(isolated.missing_ids),
        "research_unknown_cases": len(isolated.unknown_ids),
        "research_duplicate_cases": len(isolated.duplicate_ids),
    }
    if isolated.fatal_kind:
        isolation_stats["research_fatal_kind"] = isolated.fatal_kind
        reasons = {case.id: ReasonCode.RESEARCH_FATAL for case in cases}
        return (
            isolated.batch,
            isolation_stats,
            True,
            reasons,
            isolated.fatal_kind,
            _case_call_meta_from_isolated(cases, isolated),
        )
    if isolated.truncated and retries.split > 0 and len(cases) > 1:
        mid = max(1, len(cases) // 2)
        parts = [cases[:mid], cases[mid:]]
        merged_cases = []
        reasons: dict[str, object] = {}
        case_call_meta: dict[str, dict] = {}
        fatal_kind = None
        for part in parts:
            if fatal_kind:
                for case in part:
                    reasons[case.id] = ReasonCode.RESEARCH_FATAL
                continue
            if retries.split <= 0:
                for case in part:
                    reasons.setdefault(case.id, ReasonCode.RESEARCH_DEFERRED)
                continue
            retries.split -= 1
            (
                part_batch,
                part_stats,
                _failed,
                part_reasons,
                part_fatal,
                part_meta,
            ) = _resolve_research_batch(
                part,
                provider=provider,
                retries=retries,
            )
            merged_cases.extend(part_batch.cases)
            reasons.update(part_reasons)
            case_call_meta.update(part_meta)
            for key in isolation_stats:
                if key in part_stats and key != "research_fatal_kind":
                    isolation_stats[key] = isolation_stats.get(key, 0) + part_stats.get(key, 0)
            if part_fatal:
                fatal_kind = part_fatal
                isolation_stats["research_fatal_kind"] = part_fatal
        merged = ResearchResolutionBatch(cases=merged_cases)
        return (
            merged,
            isolation_stats,
            bool(fatal_kind) and not merged.cases,
            reasons,
            fatal_kind,
            case_call_meta,
        )
    reasons = _reasons_from_isolated(cases, isolated, ReasonCode=ReasonCode)
    case_call_meta = _case_call_meta_from_isolated(cases, isolated)
    retry_ids = [
        case_id
        for case_id, reason in reasons.items()
        if reason in {ReasonCode.RESEARCH_OUTPUT_INVALID, ReasonCode.RESEARCH_CASE_MISSING}
    ]
    if retry_ids and retries.item > 0:
        remaining = [case for case in cases if case.id in set(retry_ids)]
        if remaining:
            retries.item -= 1
            (
                retry_batch,
                retry_stats,
                _failed,
                retry_reasons,
                retry_fatal,
                retry_meta,
            ) = _resolve_research_batch(
                remaining,
                provider=provider,
                retries=retries,
            )
            known = {item.case_id for item in isolated.batch.cases}
            extra = [item for item in retry_batch.cases if item.case_id not in known]
            isolated.batch = ResearchResolutionBatch(cases=[*isolated.batch.cases, *extra])
            reasons.update(retry_reasons)
            case_call_meta.update(retry_meta)
            for case in extra:
                reasons.pop(case.case_id, None)
            for key in isolation_stats:
                if key in retry_stats:
                    isolation_stats[key] = isolation_stats.get(key, 0) + retry_stats.get(key, 0)
            if retry_fatal:
                isolation_stats["research_fatal_kind"] = retry_fatal
                return isolated.batch, isolation_stats, False, reasons, retry_fatal, case_call_meta
    unavailable = (isolated.truncated or bool(isolated.fatal_kind)) and not isolated.batch.cases
    return (
        isolated.batch,
        isolation_stats,
        unavailable,
        reasons,
        isolated.fatal_kind,
        case_call_meta,
    )


def eligible_story_inputs(
    items: list[RawItem],
    *,
    verified_item_ids: frozenset[str],
) -> list[RawItem]:
    """Prevent discovery-only summaries from silently becoming Story facts."""
    return [
        item
        for item in items
        if item.source_role != SourceRole.UPSTREAM_DISCOVERY
        and (
            item.source_role != SourceRole.PRACTITIONER
            or item.id in verified_item_ids
            or item.statement_type == StatementType.FACTUAL_ANNOUNCEMENT
        )
    ]

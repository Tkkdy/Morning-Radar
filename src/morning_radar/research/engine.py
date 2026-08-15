"""Create and resolve a bounded daily batch of high-value research cases."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field

from morning_radar.ai import AIBudgetExceeded, AIOutputError
from morning_radar.ai.provider import AIProvider
from morning_radar.models import (
    RadarSignal,
    RawItem,
    ResearchCase,
    ResearchDisposition,
    ResearchEvidenceRef,
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


def _is_research_lead(item: RawItem) -> bool:
    if item.source_role in {SourceRole.PRACTITIONER, SourceRole.UPSTREAM_DISCOVERY}:
        return True
    return bool(
        item.source_role == SourceRole.COMMUNITY_DISCOVERY
        and item.metadata.get("selection_reason") == "high_signal_discovery"
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


def _evidence_ref(item: RawItem) -> ResearchEvidenceRef:
    return ResearchEvidenceRef(
        raw_item_id=item.id,
        url=item.url,
        source_role=item.source_role,
    )


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
            if same_original or anchored_primary:
                support.append(item)
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
                lead=_evidence_ref(lead),
                supporting_evidence=[_evidence_ref(item) for item in support[:3]],
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
) -> ResearchRunResult:
    cases = build_research_cases(items, maximum_cases=maximum_cases)
    had_cases = bool(cases)
    research_input_characters = len(
        json.dumps(
            [case.model_dump(mode="json") for case in cases],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    while cases and research_input_characters > maximum_input_characters:
        cases.pop()
        research_input_characters = len(
            json.dumps(
                [case.model_dump(mode="json") for case in cases],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    if not cases:
        return ResearchRunResult(
            stats={
                "research_cases": 0,
                "research_logical_ai_calls": 0,
                "research_input_characters": research_input_characters,
                "research_budget_skipped": had_cases,
            }
        )
    budget = getattr(provider, "budget", None)
    calls_before = getattr(budget, "calls_used", 0)
    try:
        batch = provider.resolve_research_cases(cases)
    except (AIOutputError, AIBudgetExceeded):
        LOGGER.exception("Research degradation: batch resolution failed; signals omitted")
        return ResearchRunResult(
            cases=cases,
            stats={
                "research_cases": len(cases),
                "research_input_characters": research_input_characters,
                "research_logical_ai_calls": getattr(budget, "calls_used", 0) - calls_before,
                "research_unavailable": True,
            },
        )

    cases_by_id = {case.id: case for case in cases}
    verified: set[str] = set()
    signals: list[RadarSignal] = []
    for resolved in batch.cases:
        case = cases_by_id.get(resolved.case_id)
        if case is None:
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
        refs = [case.lead, *case.supporting_evidence]
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
    return ResearchRunResult(
        cases=cases,
        verified_item_ids=frozenset(verified),
        radar_signals=signals,
        stats={
            "research_cases": len(cases),
            "research_input_characters": research_input_characters,
            "research_verified_story_candidates": len(verified),
            "radar_signals": len(signals),
            "research_logical_ai_calls": getattr(budget, "calls_used", 0) - calls_before,
        },
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

"""One bounded daily Tendency evaluation and immutable materialization."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from morning_radar.ai import AIBudgetExceeded, AIOutputError
from morning_radar.ai.provider import AIProvider
from morning_radar.continuity.candidates import StoryMemory
from morning_radar.models import (
    BriefTendency,
    DailyContinuity,
    DailyTendencies,
    SourceRole,
    StoryEvidenceRef,
    TendencyCurrentView,
    TendencyDecisionRecord,
    TendencyEvidenceCluster,
    TendencyStanding,
    TendencyUpdateKind,
)
from morning_radar.tendencies.clusters import build_evidence_clusters
from morning_radar.tendencies.reducer import reduce_tendencies

LOGGER = logging.getLogger(__name__)
POLICY_VERSION = "tendency_policy_v1"


@dataclass(frozen=True, slots=True)
class TendencyRunResult:
    daily: DailyTendencies
    current_views: list[TendencyCurrentView] = field(default_factory=list)
    brief_tendencies: list[BriefTendency] = field(default_factory=list)
    stats: dict[str, int | bool] = field(default_factory=dict)


def _refs_for(
    cluster_ids: list[str],
    clusters: dict[str, TendencyEvidenceCluster],
) -> list[StoryEvidenceRef]:
    return [
        StoryEvidenceRef(story=ref)
        for cluster_id in cluster_ids
        for ref in clusters[cluster_id].story_refs
    ]


def _formation_valid(
    cluster_ids: list[str],
    clusters: dict[str, TendencyEvidenceCluster],
    *,
    formation_support,
    claim_scope_supported: bool,
    scope_alignment_rationale: str,
    exception_rationale: str | None,
) -> bool:
    if len(cluster_ids) < 2:
        return False
    selected = [clusters[value] for value in cluster_ids]
    support_by_id = {item.cluster_id: item for item in formation_support}
    if (
        set(support_by_id) != set(cluster_ids)
        or len(support_by_id) != len(formation_support)
        or not all(
            support_by_id[value].directly_supports_direction
            and support_by_id[value].rationale.strip()
            and support_by_id[value].evidence_scope.strip()
            for value in cluster_ids
        )
        or not claim_scope_supported
        or not scope_alignment_rationale.strip()
    ):
        return False
    if len({day for cluster in selected for day in cluster.observed_dates}) < 2:
        return False
    if len(selected) >= 3:
        return True
    actor_sets = [set(cluster.actor_keys) for cluster in selected]
    different_actors = all(actor_sets) and not actor_sets[0].intersection(actor_sets[1])
    primary_evidence = all(
        SourceRole.OFFICIAL_PRIMARY in cluster.source_roles for cluster in selected
    )
    return bool(different_actors and primary_evidence and exception_rationale)


def _materialize(
    draft,
    *,
    current_date: date,
    generated_at: datetime,
    clusters: dict[str, TendencyEvidenceCluster],
    previous: TendencyCurrentView | None,
) -> TendencyDecisionRecord | None:
    support_ids = list(dict.fromkeys(draft.supporting_cluster_ids))
    counter_ids = list(dict.fromkeys(draft.counterevidence_cluster_ids))
    if not support_ids and not counter_ids:
        return None
    if any(value not in clusters for value in [*support_ids, *counter_ids]):
        return None
    if not draft.assessment.counterevidence_considered:
        return None
    if previous is not None and not any(
        max(clusters[value].observed_dates) > previous.last_recorded_at.date()
        for value in [*support_ids, *counter_ids]
    ):
        # Time passing, re-reading old evidence, or a policy rerun is not an update.
        return None
    formation_ids: list[str] = []
    formed_at = previous.formed_at if previous else None
    if draft.standing_after is TendencyStanding.EMERGING and (
        previous is None or previous.standing is TendencyStanding.CANDIDATE
    ):
        if not _formation_valid(
            support_ids,
            clusters,
            formation_support=draft.formation_support,
            claim_scope_supported=draft.claim_scope_supported,
            scope_alignment_rationale=draft.scope_alignment_rationale,
            exception_rationale=draft.assessment.formation_exception_rationale,
        ):
            return None
        formation_ids = support_ids
        formed_at = current_date
    if draft.standing_after is TendencyStanding.PERSISTENT:
        if previous is None or previous.formed_at is None:
            return None
        new_support = [
            clusters[value]
            for value in support_ids
            if value not in previous.formation_cluster_ids
            and max(clusters[value].observed_dates) > previous.formed_at
        ]
        if not new_support:
            return None
    if (
        draft.update_kind is TendencyUpdateKind.REVISED
        and previous is not None
        and draft.claim.strip() == previous.claim.strip()
    ):
        return None
    if (
        draft.standing_after is TendencyStanding.OVERTURNED
        and (
            previous is None
            or not draft.assessment.core_claim_invalidated
            or not counter_ids
        )
    ):
        return None

    tendency_id = (
        previous.tendency_id
        if previous is not None
        else f"tendency-{hashlib.sha256(draft.claim.casefold().encode()).hexdigest()[:20]}"
    )
    record_identity = "|".join(
        (
            tendency_id,
            str(current_date),
            previous.latest_record_id if previous else "root",
            draft.claim,
            str(draft.update_kind),
        )
    )
    return TendencyDecisionRecord(
        record_id=f"tendency-record-{hashlib.sha256(record_identity.encode()).hexdigest()[:20]}",
        tendency_id=tendency_id,
        recorded_at=generated_at,
        previous_record_id=previous.latest_record_id if previous else None,
        standing_after=draft.standing_after,
        update_kind=draft.update_kind,
        claim=draft.claim,
        assessment=draft.assessment,
        supporting_cluster_ids=support_ids,
        evidence_refs=_refs_for(support_ids, clusters),
        counterevidence_cluster_ids=counter_ids,
        counterevidence_refs=_refs_for(counter_ids, clusters),
        formed_at=formed_at,
        formation_cluster_ids=(
            formation_ids or (previous.formation_cluster_ids if previous else [])
        ),
        policy_version=POLICY_VERSION,
    )


def evaluate_daily_tendencies(
    *,
    current_date: date,
    generated_at: datetime,
    story_memory: list[StoryMemory],
    continuities: list[DailyContinuity],
    history: list[DailyTendencies],
    provider: AIProvider,
    maximum_clusters: int,
    maximum_input_characters: int = 24000,
) -> TendencyRunResult:
    current_views = reduce_tendencies(history)
    evaluation_views = sorted(
        current_views,
        key=lambda view: (view.last_recorded_at, view.tendency_id),
        reverse=True,
    )[:maximum_clusters]
    clusters = build_evidence_clusters(story_memory, continuities)[:maximum_clusters]
    def context_characters() -> int:
        return len(
            json.dumps(
                {
                    "evidence_clusters": [
                        cluster.model_dump(mode="json") for cluster in clusters
                    ],
                    "current_views": [
                        view.model_dump(mode="json") for view in evaluation_views
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    while context_characters() > maximum_input_characters:
        if len(clusters) > 2:
            clusters.pop()
        elif len(evaluation_views) > 1:
            evaluation_views.pop()
        else:
            break
    tendency_input_characters = context_characters()
    dates = {day for cluster in clusters for day in cluster.observed_dates}
    if (
        tendency_input_characters > maximum_input_characters
        or len(clusters) < 2
        or (len(dates) < 2 and not current_views)
    ):
        daily = DailyTendencies(date=current_date, generated_at=generated_at)
        return TendencyRunResult(
            daily=daily,
            current_views=current_views,
            brief_tendencies=_brief_views(current_views),
            stats={
                "tendency_clusters": len(clusters),
                "tendency_input_characters": tendency_input_characters,
                "tendency_logical_ai_calls": 0,
                "tendency_budget_skipped": (
                    tendency_input_characters > maximum_input_characters
                ),
            },
        )
    budget = getattr(provider, "budget", None)
    calls_before = getattr(budget, "calls_used", 0)
    try:
        output = provider.evaluate_tendencies(clusters, evaluation_views)
    except (AIOutputError, AIBudgetExceeded):
        LOGGER.exception("Tendency degradation: evaluation failed; preserving prior view")
        daily = DailyTendencies(date=current_date, generated_at=generated_at)
        return TendencyRunResult(
            daily=daily,
            current_views=current_views,
            brief_tendencies=_brief_views(current_views),
            stats={
                "tendency_clusters": len(clusters),
                "tendency_input_characters": tendency_input_characters,
                "tendency_logical_ai_calls": getattr(budget, "calls_used", 0) - calls_before,
                "tendency_unavailable": True,
            },
        )
    cluster_by_id = {cluster.cluster_id: cluster for cluster in clusters}
    view_by_id = {view.tendency_id: view for view in current_views}
    decisions: list[TendencyDecisionRecord] = []
    used_existing: set[str] = set()
    used_tendency_ids: set[str] = set()
    for draft in output.decisions:
        previous = (
            view_by_id.get(draft.existing_tendency_id)
            if draft.existing_tendency_id
            else None
        )
        if draft.existing_tendency_id and draft.existing_tendency_id in used_existing:
            continue
        record = _materialize(
            draft,
            current_date=current_date,
            generated_at=generated_at,
            clusters=cluster_by_id,
            previous=previous,
        )
        if record is None:
            continue
        if (
            record.tendency_id in used_tendency_ids
            or (previous is None and record.tendency_id in view_by_id)
        ):
            continue
        decisions.append(record)
        used_tendency_ids.add(record.tendency_id)
        if draft.existing_tendency_id:
            used_existing.add(draft.existing_tendency_id)
    daily = DailyTendencies(date=current_date, generated_at=generated_at, decisions=decisions)
    updated_views = reduce_tendencies([*history, daily])
    return TendencyRunResult(
        daily=daily,
        current_views=updated_views,
        brief_tendencies=_brief_views(updated_views),
        stats={
            "tendency_clusters": len(clusters),
            "tendency_input_characters": tendency_input_characters,
            "tendency_decisions": len(decisions),
            "tendency_logical_ai_calls": getattr(budget, "calls_used", 0) - calls_before,
        },
    )


def _brief_views(views: list[TendencyCurrentView]) -> list[BriefTendency]:
    return [
        BriefTendency(
            tendency_id=view.tendency_id,
            standing=view.standing.value,
            latest_update=view.latest_update.value if view.latest_update else None,
            claim=view.claim,
            shared_mechanism=view.assessment.shared_mechanism,
            decision_rationale=view.assessment.decision_rationale,
        )
        for view in views
        if view.standing is not TendencyStanding.CANDIDATE
    ]

"""Bounded orchestration for cross-day continuity intelligence."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from morning_radar.ai import AIBudgetExceeded, AIOutputError
from morning_radar.ai.models import (
    ContinuityRelationCandidate,
    ContinuityResolution,
    ContinuityResolutionInput,
    ContinuityStorySummary,
    ContinuityWatchInput,
    PriorJudgementInput,
)
from morning_radar.ai.provider import AIProvider
from morning_radar.continuity.candidates import (
    RelationCandidate,
    StoryMemory,
    deterministic_relation,
    generate_relation_candidates,
)
from morning_radar.continuity.reducer import reduce_judgements, reduce_watches
from morning_radar.continuity.validation import validate_continuity_resolution
from morning_radar.models import (
    CurrentJudgement,
    CurrentWatch,
    DailyContinuity,
    JudgementRecord,
    Story,
    StoryOccurrenceRef,
    StoryRelationRecord,
    WatchEvent,
    WatchEventType,
)

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ContinuityRunResult:
    daily: DailyContinuity
    stats: dict[str, int] = field(default_factory=dict)
    current_watches: dict[str, CurrentWatch] = field(default_factory=dict)
    current_judgements: dict[str, CurrentJudgement] = field(default_factory=dict)


def _summary(memory: StoryMemory) -> ContinuityStorySummary:
    return ContinuityStorySummary(
        ref=memory.ref,
        canonical_title=memory.story.canonical_title,
        facts=memory.story.facts,
        entity_names=memory.story.entity_names,
        product_names=memory.story.product_names,
        topic_names=memory.story.topic_names,
        status=memory.story.status,
    )


def _relation_input(candidate: RelationCandidate) -> ContinuityRelationCandidate:
    return ContinuityRelationCandidate(
        previous=_summary(candidate.previous),
        current=_summary(candidate.current),
        shared_products=candidate.shared_products,
        shared_entities=candidate.shared_entities,
        shared_topics=candidate.shared_topics,
        product_named_in_both_titles=candidate.product_named_in_both_titles,
        explicit_version_progression=candidate.explicit_version_progression,
        prerelease_to_stable=candidate.prerelease_to_stable,
        same_release_series=candidate.same_release_series,
        status_progression=candidate.status_progression,
        days_apart=candidate.days_apart,
    )


def _story_mentions_watch(story: Story, watch: CurrentWatch) -> bool:
    story_values = {
        value.casefold()
        for value in (
            *story.entity_names,
            *story.product_names,
            *story.topic_names,
        )
    }
    anchors = {
        value.casefold()
        for value in (
            *watch.entity_anchors,
            *watch.product_anchors,
            *watch.topic_anchors,
        )
    }
    if story_values.intersection(anchors):
        return True
    title = story.canonical_title.casefold()
    return any(anchor in title for anchor in anchors if len(anchor) >= 3)


def _story_mentions_judgement(story: Story, judgement: CurrentJudgement) -> bool:
    claim = judgement.latest_record.claim.casefold()
    anchors = [*story.entity_names, *story.product_names]
    return any(value.casefold() in claim for value in anchors if len(value) >= 3)


def _trim_context(
    context: ContinuityResolutionInput,
    *,
    maximum_items: int,
    maximum_characters: int,
) -> tuple[ContinuityResolutionInput, int]:
    source_lanes = [
        list(context.relation_candidates),
        list(context.watch_candidates),
        list(context.prior_hypotheses),
    ]
    selected: list[list[object]] = [[], [], []]
    nonempty_lanes = [index for index, lane in enumerate(source_lanes) if lane]
    remaining_slots = max(0, maximum_items)
    cursors = [0, 0, 0]
    if remaining_slots >= len(nonempty_lanes):
        for index in nonempty_lanes:
            selected[index].append(source_lanes[index][0])
            cursors[index] = 1
            remaining_slots -= 1
    for index in range(3):
        while remaining_slots and cursors[index] < len(source_lanes[index]):
            selected[index].append(source_lanes[index][cursors[index]])
            cursors[index] += 1
            remaining_slots -= 1

    def build_context() -> tuple[ContinuityResolutionInput, int]:
        bounded = ContinuityResolutionInput(
            relation_candidates=selected[0],
            watch_candidates=selected[1],
            prior_hypotheses=selected[2],
        )
        characters = len(
            json.dumps(
                bounded.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return bounded, characters

    while True:
        bounded, characters = build_context()
        if characters <= maximum_characters:
            return bounded, characters
        removable = [index for index, lane in enumerate(selected) if len(lane) > 1]
        if removable:
            selected[max(removable, key=lambda index: len(selected[index]))].pop()
            continue
        populated = [index for index, lane in enumerate(selected) if lane]
        if not populated:
            return bounded, characters
        largest = max(
            populated,
            key=lambda index: len(
                json.dumps(
                    selected[index][-1].model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ),
        )
        selected[largest].pop()


def _record_id(prefix: str, *parts: object) -> str:
    identity = "|".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


def resolve_daily_continuity(
    *,
    current_date: date,
    generated_at: datetime,
    current_stories: list[Story],
    historical_stories: list[StoryMemory],
    continuity_history: list[DailyContinuity],
    provider: AIProvider,
    history_days: int,
    maximum_candidates: int,
    maximum_open_watches: int,
    maximum_ai_items: int,
    maximum_input_characters: int,
    reserved_input_characters: int = 0,
    enable_ai: bool = True,
) -> ContinuityRunResult:
    """Resolve deterministic work plus bounded, isolated AI lanes for one day."""
    current_memory = [
        StoryMemory(
            ref=StoryOccurrenceRef(date=current_date, story_id=story.id),
            story=story,
        )
        for story in current_stories
    ]
    current_watches = reduce_watches(continuity_history)
    current_judgements = reduce_judgements(continuity_history)
    relation_candidates = generate_relation_candidates(
        current_memory,
        historical_stories,
        maximum_days=history_days,
        maximum_candidates=maximum_candidates,
    )

    deterministic_relations: list[StoryRelationRecord] = []
    unresolved_relations: list[RelationCandidate] = []
    for candidate in relation_candidates:
        relation = deterministic_relation(candidate, recorded_at=generated_at)
        if relation is None:
            unresolved_relations.append(candidate)
        else:
            deterministic_relations.append(relation)

    oldest_considered = current_date - timedelta(days=history_days)
    open_watches = [
        watch
        for watch in current_watches.values()
        if (watch.is_open and oldest_considered <= watch.opened_at.date() < current_date)
    ]
    open_watches.sort(key=lambda item: (item.opened_at, item.watch_id), reverse=True)
    watch_inputs: list[ContinuityWatchInput] = []
    for watch in open_watches[:maximum_open_watches]:
        candidates = [
            _summary(memory)
            for memory in current_memory
            if _story_mentions_watch(memory.story, watch)
        ]
        if candidates:
            watch_inputs.append(
                ContinuityWatchInput(
                    watch_id=watch.watch_id,
                    expectation=watch.expectation,
                    entity_anchors=watch.entity_anchors,
                    product_anchors=watch.product_anchors,
                    topic_anchors=watch.topic_anchors,
                    current_story_candidates=candidates,
                )
            )

    judgement_inputs: list[PriorJudgementInput] = []
    for view in current_judgements.values():
        if view.latest_record.recorded_at.date() >= current_date:
            continue
        candidates = [
            _summary(memory)
            for memory in current_memory
            if _story_mentions_judgement(memory.story, view)
        ]
        if candidates:
            judgement_inputs.append(
                PriorJudgementInput(
                    judgement_id=view.latest_record.judgement_id,
                    root_judgement_id=view.root_judgement_id,
                    claim=view.latest_record.claim,
                    rationale=view.latest_record.rationale,
                    uncertainty=view.latest_record.uncertainty,
                    current_story_candidates=candidates,
                )
            )

    full_context = ContinuityResolutionInput(
        relation_candidates=[_relation_input(value) for value in unresolved_relations],
        watch_candidates=watch_inputs,
        prior_hypotheses=judgement_inputs,
    )
    full_input_items = (
        len(full_context.relation_candidates)
        + len(full_context.watch_candidates)
        + len(full_context.prior_hypotheses)
    )
    budget = getattr(provider, "budget", None)
    remaining_character_budget = maximum_input_characters
    if budget is not None:
        remaining_character_budget = max(
            0,
            getattr(budget, "maximum_input_characters", 0)
            - getattr(budget, "input_characters_used", 0)
            - reserved_input_characters,
        )
    effective_character_cap = min(
        maximum_input_characters,
        remaining_character_budget,
    )
    context, input_characters = _trim_context(
        full_context,
        maximum_items=maximum_ai_items,
        maximum_characters=effective_character_cap,
    )
    input_items = (
        len(context.relation_candidates)
        + len(context.watch_candidates)
        + len(context.prior_hypotheses)
    )
    stats = {
        "historical_story_candidates": len(historical_stories),
        "continuity_candidates": len(relation_candidates),
        "open_watches_considered": len(open_watches[:maximum_open_watches]),
        "continuity_relation_inputs": len(context.relation_candidates),
        "continuity_watch_inputs": len(context.watch_candidates),
        "continuity_judgement_inputs": len(context.prior_hypotheses),
        "continuity_character_budget_available": effective_character_cap,
        "continuity_input_chars": input_characters if input_items else 0,
        "continuity_logical_ai_calls": 0,
        "continuity_network_requests": 0,
        "continuity_budget_skipped": int(full_input_items > 0 and input_items == 0),
        "relation_deterministic": len(deterministic_relations),
        "relation_ai_inputs": len(context.relation_candidates),
        "watch_inputs": len(context.watch_candidates),
        "judgement_candidates": len(context.prior_hypotheses),
    }
    if input_items == 0 or not enable_ai:
        if full_input_items and enable_ai:
            LOGGER.warning(
                "Skipping continuity AI: no candidate fits remaining character budget=%d",
                effective_character_cap,
            )
        stats.update(
            {
                "relations_confirmed": len(deterministic_relations),
                "relation_confirmed": len(deterministic_relations),
                "relations_rejected": 0,
                "relation_rejected": 0,
                "relations_unresolved": len(unresolved_relations),
                "relation_unresolved": len(unresolved_relations),
                "watch_matches": 0,
                "judgement_updates": 0,
                "judgement_direct_revisions": 0,
                "revised": 0,
                "overturned": 0,
                "needs_review": sum(
                    view.state.value == "needs_review" for view in current_judgements.values()
                ),
            }
        )
        return ContinuityRunResult(
            daily=DailyContinuity(
                date=current_date,
                generated_at=generated_at,
                relations=deterministic_relations,
            ),
            stats=stats,
            current_watches=current_watches,
            current_judgements=current_judgements,
        )

    calls_before = getattr(budget, "calls_used", 0)
    requests_before = getattr(budget, "network_requests_used", 0)
    lane_contexts = [
        ContinuityResolutionInput(relation_candidates=context.relation_candidates),
        ContinuityResolutionInput(watch_candidates=context.watch_candidates),
        ContinuityResolutionInput(prior_hypotheses=context.prior_hypotheses),
    ]
    lane_results: list[ContinuityResolution] = []
    degraded_lanes = 0
    for lane_context in lane_contexts:
        if not (
            lane_context.relation_candidates
            or lane_context.watch_candidates
            or lane_context.prior_hypotheses
        ):
            continue
        try:
            lane_result = provider.resolve_continuity(lane_context)
            validate_continuity_resolution(lane_result, lane_context)
            lane_results.append(lane_result)
        except AIBudgetExceeded:
            LOGGER.warning("Continuity lane skipped: AI budget unavailable")
            stats["continuity_budget_skipped"] = 1
            degraded_lanes += 1
        except (AIOutputError, ValueError):
            LOGGER.exception("AI degradation: continuity lane failed; deterministic result kept")
            degraded_lanes += 1
    resolution = ContinuityResolution(
        relations=[item for result in lane_results for item in result.relations],
        watch_matches=[item for result in lane_results for item in result.watch_matches],
        judgement_updates=[item for result in lane_results for item in result.judgement_updates],
    )
    stats["fast_continuity_degraded"] = int(degraded_lanes > 0)
    stats["continuity_logical_ai_calls"] = (
        getattr(budget, "calls_used", calls_before) - calls_before
    )
    stats["continuity_network_requests"] = (
        getattr(budget, "network_requests_used", requests_before) - requests_before
    )

    relations = list(deterministic_relations)
    rejected_relation_pairs: set[tuple[StoryOccurrenceRef, StoryOccurrenceRef]] = set()
    watch_events: list[WatchEvent] = []
    judgement_updates: list[JudgementRecord] = []
    if resolution is not None:
        for draft in resolution.relations:
            LOGGER.info(
                "Continuity AI relation: previous=%s current=%s confirmed=%s type=%s rationale=%s",
                draft.previous_story,
                draft.current_story,
                draft.confirmed,
                draft.relation_type,
                draft.rationale,
            )
            if not draft.confirmed:
                rejected_relation_pairs.add((draft.previous_story, draft.current_story))
                continue
            relations.append(
                StoryRelationRecord(
                    relation_id=_record_id(
                        "relation",
                        draft.previous_story.date,
                        draft.previous_story.story_id,
                        draft.current_story.date,
                        draft.current_story.story_id,
                        draft.relation_type,
                    ),
                    recorded_at=generated_at,
                    previous_story=draft.previous_story,
                    current_story=draft.current_story,
                    relation_type=draft.relation_type,
                    change_summary=draft.what_changed,
                    rationale=draft.rationale or "已确认存在直接发展关系。",
                    evidence_refs=draft.evidence_refs,
                )
            )
        for match in resolution.watch_matches:
            LOGGER.info(
                "Continuity AI Watch: watch_id=%s matched=%s stories=%s rationale=%s",
                match.watch_id,
                match.matched,
                match.matched_story_refs,
                match.rationale,
            )
            if not match.matched:
                continue
            watch = current_watches[match.watch_id]
            watch_events.append(
                WatchEvent(
                    watch_id=watch.watch_id,
                    recorded_at=generated_at,
                    event_type=WatchEventType.MATCHED,
                    expectation=watch.expectation,
                    entity_anchors=watch.entity_anchors,
                    product_anchors=watch.product_anchors,
                    topic_anchors=watch.topic_anchors,
                    source_story_refs=watch.source_story_refs,
                    matched_story_refs=match.matched_story_refs,
                    rationale=match.rationale or "新事实直接回应了观察事项。",
                )
            )
        by_latest_id = {
            view.latest_record.judgement_id: view for view in current_judgements.values()
        }
        for update in resolution.judgement_updates:
            LOGGER.info(
                "Continuity AI Judgement: prior=%s update=%s evidence=%s",
                update.prior_judgement_id,
                update.update_kind,
                [ref.story for ref in update.evidence_refs],
            )
            previous = by_latest_id[update.prior_judgement_id]
            judgement_updates.append(
                JudgementRecord(
                    judgement_id=_record_id(
                        "judgement",
                        previous.root_judgement_id,
                        current_date,
                        update.update_kind,
                        *(ref.story.story_id for ref in update.evidence_refs),
                    ),
                    root_judgement_id=previous.root_judgement_id,
                    recorded_at=generated_at,
                    claim=update.claim,
                    rationale=update.rationale,
                    evidence_refs=update.evidence_refs,
                    uncertainty=update.uncertainty,
                    watch_ids=previous.latest_record.watch_ids,
                    depends_on_judgement_ids=(previous.latest_record.depends_on_judgement_ids),
                    updates_judgement_id=previous.latest_record.judgement_id,
                    update_kind=update.update_kind,
                )
            )

    combined_history = [
        *continuity_history,
        DailyContinuity(
            date=current_date,
            generated_at=generated_at,
            relations=relations,
            watch_events=watch_events,
            judgements=judgement_updates,
        ),
    ]
    resulting_judgements = reduce_judgements(combined_history)
    stats.update(
        {
            "relations_confirmed": len(relations),
            "relation_confirmed": len(relations),
            "relations_rejected": len(rejected_relation_pairs),
            "relation_rejected": len(rejected_relation_pairs),
            "relations_unresolved": max(
                0,
                len(relation_candidates) - len(relations) - len(rejected_relation_pairs),
            ),
            "relation_unresolved": max(
                0, len(relation_candidates) - len(relations) - len(rejected_relation_pairs)
            ),
            "watch_matches": len(watch_events),
            "judgement_updates": len(judgement_updates),
            "judgement_direct_revisions": len(judgement_updates),
            "revised": sum(item.update_kind.value == "revised" for item in judgement_updates),
            "overturned": sum(item.update_kind.value == "overturned" for item in judgement_updates),
            "needs_review": sum(
                view.state.value == "needs_review" for view in resulting_judgements.values()
            ),
        }
    )
    for candidate in relation_candidates:
        LOGGER.info(
            "Continuity candidate: previous=%s current=%s products=%s "
            "version_progression=%s status_progression=%s days=%d",
            candidate.previous.ref,
            candidate.current.ref,
            candidate.shared_products,
            candidate.explicit_version_progression,
            candidate.status_progression,
            candidate.days_apart,
        )
    return ContinuityRunResult(
        daily=combined_history[-1],
        stats=stats,
        current_watches=current_watches,
        current_judgements=resulting_judgements,
    )

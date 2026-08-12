"""Derive current Watch and Judgement views from immutable daily events."""

from __future__ import annotations

from collections.abc import Iterable

from morning_radar.models import (
    CurrentJudgement,
    CurrentWatch,
    DailyContinuity,
    JudgementRecord,
    JudgementUpdateKind,
    JudgementViewState,
    RelationDisposition,
    StoryRelationRecord,
    WatchEvent,
    WatchEventType,
)

REVIEW_TRIGGER_KINDS = {
    JudgementUpdateKind.WEAKENED,
    JudgementUpdateKind.REVISED,
    JudgementUpdateKind.OVERTURNED,
}


def reduce_relations(
    history: Iterable[DailyContinuity],
) -> dict[str, StoryRelationRecord]:
    """Return confirmed relations that have not been retracted by later records."""
    records = sorted(
        (record for daily in history for record in daily.relations),
        key=lambda record: (record.recorded_at, record.relation_id),
    )
    all_by_id: dict[str, StoryRelationRecord] = {}
    active: dict[str, StoryRelationRecord] = {}
    for record in records:
        if record.relation_id in all_by_id:
            raise ValueError(f"duplicate Story relation ID: {record.relation_id}")
        if record.disposition is RelationDisposition.CONFIRMED:
            active[record.relation_id] = record
        else:
            target = all_by_id.get(record.retracts_relation_id or "")
            if target is None or target.disposition is not RelationDisposition.CONFIRMED:
                raise ValueError("relation retraction references an unknown confirmed relation")
            if target.relation_id not in active:
                raise ValueError("relation has already been retracted")
            active.pop(target.relation_id)
        all_by_id[record.relation_id] = record
    return active


def _watch_events(history: Iterable[DailyContinuity]) -> list[WatchEvent]:
    return sorted(
        (event for daily in history for event in daily.watch_events),
        key=lambda event: (event.recorded_at, event.watch_id, event.event_type),
    )


def reduce_watches(history: Iterable[DailyContinuity]) -> dict[str, CurrentWatch]:
    """Replay immutable Watch events into the current Watch view."""
    current: dict[str, CurrentWatch] = {}
    for event in _watch_events(history):
        existing = current.get(event.watch_id)
        if event.event_type is WatchEventType.OPENED:
            if existing is not None:
                raise ValueError(f"duplicate Watch open event: {event.watch_id}")
            current[event.watch_id] = CurrentWatch(
                watch_id=event.watch_id,
                expectation=event.expectation,
                opened_at=event.recorded_at,
                entity_anchors=event.entity_anchors,
                product_anchors=event.product_anchors,
                topic_anchors=event.topic_anchors,
                source_story_refs=event.source_story_refs,
            )
            continue
        if existing is None:
            raise ValueError(f"Watch event references unknown Watch: {event.watch_id}")
        if not existing.is_open:
            raise ValueError(f"Watch event follows a terminal event: {event.watch_id}")
        if event.event_type is WatchEventType.MATCHED:
            current[event.watch_id] = existing.model_copy(
                update={
                    "matched_story_refs": event.matched_story_refs,
                    "is_open": False,
                }
            )
        else:
            current[event.watch_id] = existing.model_copy(update={"is_open": False})
    return current


def _judgement_records(history: Iterable[DailyContinuity]) -> list[JudgementRecord]:
    return sorted(
        (record for daily in history for record in daily.judgements),
        key=lambda record: (record.recorded_at, record.judgement_id),
    )


def reduce_judgements(history: Iterable[DailyContinuity]) -> dict[str, CurrentJudgement]:
    """Replay append-only Judgement records and derive single-hop review state."""
    by_id: dict[str, JudgementRecord] = {}
    latest_by_root: dict[str, JudgementRecord] = {}
    for record in _judgement_records(history):
        if record.judgement_id in by_id:
            raise ValueError(f"duplicate Judgement ID: {record.judgement_id}")
        if record.updates_judgement_id is not None:
            target = by_id.get(record.updates_judgement_id)
            if target is None:
                raise ValueError(
                    "Judgement update references unknown or future record: "
                    f"{record.updates_judgement_id}"
                )
            if target.root_judgement_id != record.root_judgement_id:
                raise ValueError("Judgement update must remain in the same root chain")
            if latest_by_root.get(record.root_judgement_id) != target:
                raise ValueError("Judgement update must reference the latest chain record")
        elif record.root_judgement_id in latest_by_root:
            raise ValueError(f"duplicate root Judgement: {record.root_judgement_id}")
        by_id[record.judgement_id] = record
        latest_by_root[record.root_judgement_id] = record

    result: dict[str, CurrentJudgement] = {}
    for root_id, latest in latest_by_root.items():
        triggers: list[str] = []
        for dependency_id in latest.depends_on_judgement_ids:
            dependency = by_id.get(dependency_id)
            if dependency is None:
                raise ValueError(f"unknown Judgement dependency: {dependency_id}")
            dependency_latest = latest_by_root[dependency.root_judgement_id]
            if (
                dependency_latest.update_kind in REVIEW_TRIGGER_KINDS
                and dependency_latest.recorded_at > latest.recorded_at
            ):
                triggers.append(dependency_latest.judgement_id)
        result[root_id] = CurrentJudgement(
            root_judgement_id=root_id,
            latest_record=latest,
            state=(
                JudgementViewState.NEEDS_REVIEW
                if triggers
                else JudgementViewState.ACTIVE
            ),
            review_trigger_ids=triggers,
        )
    return result

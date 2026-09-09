"""Merge fresh inputs with recovered work under a reserved candidate quota."""

from __future__ import annotations

from dataclasses import dataclass, field

from morning_radar.intake.identity import intake_key
from morning_radar.intake.models import IntakeRecord
from morning_radar.models import RawItem
from morning_radar.processing.story_builder import preselect_ai_candidates


@dataclass(slots=True)
class ProcessSelection:
    items: list[RawItem]
    records: list[IntakeRecord]
    recovery_items: list[RawItem]
    fresh_items: list[RawItem]
    deferred: list[IntakeRecord] = field(default_factory=list)
    selected_keys: set[str] = field(default_factory=set)


def select_process_candidates(
    *,
    fresh: list[IntakeRecord],
    recovery: list[IntakeRecord],
    maximum_items: int,
    reserved_recovery_slots: int,
) -> ProcessSelection:
    maximum_items = max(0, maximum_items)
    reserved = min(max(0, reserved_recovery_slots), len(recovery), maximum_items)
    recovery_reserved = recovery[:reserved]
    leftover_recovery = recovery[reserved:]
    remaining_slots = maximum_items - reserved
    fresh_items = [record.item for record in fresh]
    fresh_selected_items = preselect_ai_candidates(
        fresh_items,
        maximum_items=remaining_slots,
    )
    selected_fresh_ids = {item.id for item in fresh_selected_items}
    unused_fresh_slots = max(0, remaining_slots - len(fresh_selected_items))
    extra_recovery = leftover_recovery[:unused_fresh_slots]
    selected_fresh = [record for record in fresh if record.item.id in selected_fresh_ids]
    selected_records = [*recovery_reserved, *extra_recovery, *selected_fresh]
    selected_keys = {
        intake_key(record.input_id, record.content_version) for record in selected_records
    }
    deferred = [
        record
        for record in [*recovery, *fresh]
        if intake_key(record.input_id, record.content_version) not in selected_keys
    ]
    return ProcessSelection(
        items=[record.item for record in selected_records],
        records=selected_records,
        recovery_items=[record.item for record in recovery],
        fresh_items=fresh_items,
        deferred=deferred,
        selected_keys=selected_keys,
    )

def version_observation_key(record: IntakeRecord) -> tuple:
    """Order content versions by durable observation time, not publish time or hash."""
    return (
        record.durable_at or record.first_seen_at,
        record.fetched_at,
        record.batch_id,
        record.content_version,
    )


def choose_latest_content_versions(records: list[IntakeRecord]) -> list[IntakeRecord]:
    """Keep one actionable version per input_id: the latest observed content."""
    grouped: dict[str, list[IntakeRecord]] = {}
    for record in records:
        grouped.setdefault(record.input_id, []).append(record)
    chosen: list[IntakeRecord] = []
    for versions in grouped.values():
        versions.sort(key=version_observation_key)
        chosen.append(versions[-1])
    return chosen


def split_latest_versions(
    fresh: list[IntakeRecord],
    recovery: list[IntakeRecord],
) -> tuple[list[IntakeRecord], list[IntakeRecord]]:
    latest_keys = {
        intake_key(record.input_id, record.content_version)
        for record in choose_latest_content_versions([*recovery, *fresh])
    }
    fresh_latest = [
        record
        for record in fresh
        if intake_key(record.input_id, record.content_version) in latest_keys
    ]
    recovery_latest = [
        record
        for record in recovery
        if intake_key(record.input_id, record.content_version) in latest_keys
    ]
    return fresh_latest, recovery_latest

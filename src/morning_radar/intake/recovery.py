"""Select previously saved unfinished inputs without reapplying the 24h window."""

from __future__ import annotations

from datetime import datetime

from morning_radar.intake.checkpoint import load_recent_complete_checkpoints
from morning_radar.intake.identity import intake_key
from morning_radar.intake.ledger import ProcessingLedgerStore
from morning_radar.intake.models import IntakeRecord, ProcessingStatus, ReasonCode
from morning_radar.time_utils import hours_ago


def recover_unfinished_records(
    root,
    *,
    ledger: ProcessingLedgerStore,
    now: datetime,
    lookback_days: int,
    maximum_items: int | None,
    exclude_keys: set[str] | None = None,
    collection_hours: int | None = None,
) -> tuple[list[IntakeRecord], list]:
    from morning_radar.intake.candidates import choose_latest_content_versions
    from morning_radar.processing.filtering import filter_news_window

    excluded = exclude_keys or set()
    checkpoints = load_recent_complete_checkpoints(
        root,
        now=now,
        lookback_days=lookback_days,
    )
    records_by_key: dict[str, IntakeRecord] = {}
    for checkpoint in checkpoints:
        for record in checkpoint.items:
            records_by_key[intake_key(record.input_id, record.content_version)] = record
    cutoff = hours_ago(now, hours=lookback_days * 24)
    unfinished = [
        entry
        for entry in ledger.unfinished()
        if intake_key(entry.input_id, entry.content_version) not in excluded
        and (entry.durable_at or entry.first_seen_at) >= cutoff
    ]
    aged_unresolved = [
        entry
        for entry in ledger.unfinished()
        if (entry.durable_at or entry.first_seen_at) < cutoff
    ]
    eligible: list[IntakeRecord] = []
    for entry in unfinished:
        record = records_by_key.get(intake_key(entry.input_id, entry.content_version))
        if record is None:
            continue
        first_saved = entry.durable_at or record.durable_at or record.first_seen_at
        if collection_hours is not None and not filter_news_window(
            [record.item],
            now=first_saved,
            hours=collection_hours,
        ):
            ledger.update(
                entry.input_id,
                entry.content_version,
                now=now,
                processing=ProcessingStatus.EXCLUDED,
                reason_code=ReasonCode.EXCLUDED_STALE,
                stage="window",
                outcome="excluded_stale",
            )
            continue
        eligible.append(record)
    selected = choose_latest_content_versions(eligible)
    selected.sort(
        key=lambda record: (
            record.durable_at or record.first_seen_at,
            record.input_id,
        )
    )
    if maximum_items is not None:
        selected = selected[:maximum_items]
    return selected, aged_unresolved

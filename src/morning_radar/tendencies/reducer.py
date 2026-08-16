"""Derive current Tendency views without mutating historical decisions."""

from __future__ import annotations

from morning_radar.models import DailyTendencies, TendencyCurrentView


def reduce_tendencies(history: list[DailyTendencies]) -> list[TendencyCurrentView]:
    latest = {}
    for daily in sorted(history, key=lambda item: item.date):
        for record in sorted(daily.decisions, key=lambda item: item.recorded_at):
            previous = latest.get(record.tendency_id)
            if previous is None and record.previous_record_id is not None:
                continue
            if previous is not None and record.previous_record_id != previous.latest_record_id:
                continue
            latest[record.tendency_id] = TendencyCurrentView(
                tendency_id=record.tendency_id,
                latest_record_id=record.record_id,
                standing=record.standing_after,
                latest_update=record.update_kind,
                claim=record.claim,
                assessment=record.assessment,
                formed_at=record.formed_at or (previous.formed_at if previous else None),
                formation_cluster_ids=(
                    record.formation_cluster_ids
                    or (previous.formation_cluster_ids if previous else [])
                ),
                last_recorded_at=record.recorded_at,
                policy_version=record.policy_version,
            )
    return sorted(latest.values(), key=lambda value: value.tendency_id)

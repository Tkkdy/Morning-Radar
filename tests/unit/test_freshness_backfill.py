"""Offline regressions for first-discovery backfill eligibility."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from morning_radar.intake.candidates import select_process_candidates
from morning_radar.intake.checkpoint import build_intake_records
from morning_radar.intake.freshness import late_discovery_reason
from morning_radar.intake.ledger import ProcessingLedgerStore
from morning_radar.intake.models import ProcessingStatus, ReasonCode
from morning_radar.intake.service import prepare_process
from morning_radar.models import SourceRole
from morning_radar.pipeline import MorningRadarPipeline
from morning_radar.settings import AppConfig, load_model
from tests.unit.test_phase1_patch import (
    copy_project,
    install_fake_provider,
    official_item,
    save_checkpoint,
)

NOW = datetime(2026, 9, 12, 5, tzinfo=UTC)


def _late_item(suffix: str, *, title: str, published_at: datetime, priority: str = "high"):
    return official_item(suffix, title=title, published_at=published_at).model_copy(
        update={
            "fetched_at": NOW,
            "metadata": {"official": True, "priority": priority, "source_id": "openai_news"},
        }
    )


def _seed(project, checkpoint) -> ProcessingLedgerStore:
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint, now=checkpoint.manifest.created_at)
    ledger.save()
    return ledger


def test_late_official_events_use_recovery_lane_then_complete(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    items = [
        _late_item("agents", title="Introducing Agents API", published_at=NOW - timedelta(days=2)),
        _late_item("live", title="GPT-Live-1 API", published_at=NOW - timedelta(days=2)),
        _late_item("deepseek", title="DeepSeek v4.1 Flash", published_at=NOW - timedelta(days=2)),
    ]
    checkpoint = save_checkpoint(project, items, now=NOW, batch_id="batch-late")
    _seed(project, checkpoint)
    install_fake_provider(monkeypatch)
    prepared = prepare_process(
        project,
        load_model(project / "config/app.yaml", AppConfig),
        intake=None,
        batch_id="batch-late",
        now=NOW,
    )
    assert {record.item.title for record in prepared.selection.records} == {
        item.title for item in items
    }
    assert all(
        prepared.ledger.get(record.input_id, record.content_version).candidate_diagnostics[
            "freshness"
        ]
        == "eligible_late"
        for record in prepared.selection.records
    )
    MorningRadarPipeline(project).process(batch_id="batch-late", now=NOW, notify=False)
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    assert all(
        ledger.get(record.input_id, record.content_version).processing is ProcessingStatus.COMPLETED
        for record in checkpoint.items
    )


def test_refetch_preserves_first_seen_and_does_not_create_freshness(tmp_path) -> None:
    project = copy_project(tmp_path)
    item = _late_item(
        "agents", title="Introducing Agents API", published_at=NOW - timedelta(days=2)
    )
    first = save_checkpoint(project, [item], now=NOW, batch_id="batch-first")
    ledger = _seed(project, first)
    initial = ledger.get(first.items[0].input_id, first.items[0].content_version).first_seen_at
    refetched = item.model_copy(update={"fetched_at": NOW + timedelta(days=1)})
    second = save_checkpoint(
        project, [refetched], now=NOW + timedelta(days=1), batch_id="batch-refetch"
    )
    ledger.upsert_checkpoint(second, now=NOW + timedelta(days=1))
    ledger.save()
    entry = ledger.get(second.items[0].input_id, second.items[0].content_version)
    assert entry.first_seen_at == initial
    assert (
        late_discovery_reason(
            second.items[0], now=NOW + timedelta(days=1), normal_hours=24, lookback_days=7
        )
        == "eligible_late"
    )


def test_expired_future_and_low_value_late_items_are_diagnosed(tmp_path) -> None:
    project = copy_project(tmp_path)
    expired = _late_item(
        "expired", title="Expired announcement", published_at=NOW - timedelta(days=8)
    )
    future = _late_item(
        "future", title="Future announcement", published_at=NOW + timedelta(hours=1)
    )
    low = _late_item(
        "low", title="Routine note", published_at=NOW - timedelta(days=2), priority="low"
    )
    checkpoint = save_checkpoint(
        project, [expired, future, low], now=NOW, batch_id="batch-rejected"
    )
    _seed(project, checkpoint)
    prepared = prepare_process(
        project,
        load_model(project / "config/app.yaml", AppConfig),
        batch_id="batch-rejected",
        now=NOW,
    )
    assert prepared.selection.records == []
    reasons = {
        record.title: prepared.ledger.get(
            record.input_id, record.content_version
        ).candidate_diagnostics["reason"]
        for record in checkpoint.items
    }
    assert reasons == {
        "Expired announcement": "expired",
        "Future announcement": "future_timestamp",
        "Routine note": "low_value",
    }
    assert all(
        prepared.ledger.get(record.input_id, record.content_version).reason_code
        is ReasonCode.EXCLUDED_STALE
        for record in checkpoint.items
    )


def test_date_precision_is_explicit_and_midnight_timestamp_is_not_relaxed() -> None:
    day_only = _late_item(
        "day", title="Day precision", published_at=NOW - timedelta(days=2)
    ).model_copy(
        update={
            "published_at": None,
            "metadata": {
                "official": True,
                "priority": "high",
                "source_id": "openai_news",
                "source_date": "2026-09-10",
                "date_precision": "day",
            },
        }
    )
    midnight = _late_item(
        "midnight", title="Midnight timestamp", published_at=datetime(2026, 9, 10, tzinfo=UTC)
    )
    records = build_intake_records([day_only, midnight], batch_id="batch-date", run_id="run-date")
    assert (
        late_discovery_reason(records[0], now=NOW, normal_hours=24, lookback_days=7)
        == "eligible_late"
    )
    assert late_discovery_reason(records[1], now=NOW, normal_hours=24, lookback_days=1) == "expired"


def test_late_recovery_respects_reserved_slots_and_retry_identity() -> None:
    late = _late_item("late", title="Late official event", published_at=NOW - timedelta(days=2))
    fresh = _late_item("fresh", title="Fresh official event", published_at=NOW - timedelta(hours=1))
    late_record, fresh_record = build_intake_records([late, fresh], batch_id="batch", run_id="run")
    selected = select_process_candidates(
        fresh=[fresh_record], recovery=[late_record], maximum_items=2, reserved_recovery_slots=1
    )
    assert {item.id for item in selected.items} == {late.id, fresh.id}
    assert late_record.item.source_role is SourceRole.OFFICIAL_PRIMARY

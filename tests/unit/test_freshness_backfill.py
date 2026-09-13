"""Offline regressions for first-discovery backfill eligibility."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from morning_radar.collectors.orchestrator import CollectionResult
from morning_radar.intake.candidates import select_process_candidates
from morning_radar.intake.checkpoint import build_intake_records, load_checkpoint_by_batch_id
from morning_radar.intake.freshness import late_discovery_reason
from morning_radar.intake.ledger import ProcessingLedgerStore
from morning_radar.intake.models import ProcessingStatus, ReasonCode
from morning_radar.intake.service import collect_intake, prepare_process
from morning_radar.models import SourceRole
from morning_radar.settings import AppConfig, load_model
from tests.unit.test_phase1_patch import (
    copy_project,
    official_item,
    save_checkpoint,
)

NOW = datetime(2026, 9, 12, 5, tzinfo=UTC)
FIRST_DAY = datetime(2026, 9, 10, 5, tzinfo=UTC)
SECOND_DAY = datetime(2026, 9, 11, 5, tzinfo=UTC)


def _late_item(
    suffix: str,
    *,
    title: str,
    published_at: datetime,
    fetched_at: datetime = NOW,
    priority: str = "high",
):
    return official_item(suffix, title=title, published_at=published_at).model_copy(
        update={
            "fetched_at": fetched_at,
            "metadata": {"official": True, "priority": priority, "source_id": "openai_news"},
        }
    )


def _seed(project, checkpoint) -> ProcessingLedgerStore:
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint, now=checkpoint.manifest.created_at)
    ledger.save()
    return ledger


def _install_collection(monkeypatch):
    collected: list = []

    def fake_production(*args, **kwargs):
        return (
            CollectionResult(
                items=list(collected),
                raw_collected=len(collected),
                after_buffer=len(collected),
                after_dedup=len(collected),
            ),
            [],
            [],
        )

    monkeypatch.setattr("morning_radar.intake.service._production_collect", fake_production)
    return collected


def test_three_samples_keep_first_seen_through_refetch_and_checkpoint_reload(
    tmp_path, monkeypatch
) -> None:
    project = copy_project(tmp_path)
    app = load_model(project / "config/app.yaml", AppConfig)
    collected = _install_collection(monkeypatch)
    agents = _late_item(
        "agents",
        title="Introducing Agents API",
        published_at=datetime(2026, 9, 10, tzinfo=UTC),
        fetched_at=FIRST_DAY,
    )
    live = _late_item(
        "live",
        title="GPT-Live-1 API",
        published_at=datetime(2026, 9, 10, tzinfo=UTC),
        fetched_at=SECOND_DAY,
    )
    deepseek = _late_item(
        "deepseek",
        title="DeepSeek v4.1 Flash",
        published_at=datetime(2026, 9, 10, 6, 11, 5, tzinfo=UTC),
        fetched_at=SECOND_DAY,
    )
    collected[:] = [agents]
    collect_intake(project, app, now=FIRST_DAY)
    collected[:] = [live, deepseek]
    collect_intake(project, app, now=SECOND_DAY)
    collected[:] = [
        agents.model_copy(update={"fetched_at": NOW}),
        live.model_copy(update={"fetched_at": NOW}),
        deepseek.model_copy(update={"fetched_at": NOW}),
    ]
    refetched = collect_intake(project, app, now=NOW)
    checkpoint = load_checkpoint_by_batch_id(project, refetched.checkpoint.manifest.batch_id)
    expected_first_seen = {
        "Introducing Agents API": FIRST_DAY,
        "GPT-Live-1 API": SECOND_DAY,
        "DeepSeek v4.1 Flash": SECOND_DAY,
    }
    assert {
        record.title: record.first_seen_at for record in checkpoint.items
    } == expected_first_seen
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    assert {
        record.title: ledger.get(record.input_id, record.content_version).first_seen_at
        for record in checkpoint.items
    } == expected_first_seen
    prepared = prepare_process(
        project,
        app,
        batch_id=checkpoint.manifest.batch_id,
        now=NOW,
    )
    assert {record.item.title for record in prepared.selection.records} == {
        "Introducing Agents API",
        "GPT-Live-1 API",
        "DeepSeek v4.1 Flash",
    }
    assert all(
        prepared.ledger.get(record.input_id, record.content_version).candidate_diagnostics[
            "freshness"
        ]
        == "eligible_late"
        for record in prepared.selection.records
    )


def test_refetch_after_lookback_does_not_regain_candidate_eligibility(
    tmp_path, monkeypatch
) -> None:
    project = copy_project(tmp_path)
    app = load_model(project / "config/app.yaml", AppConfig)
    collected = _install_collection(monkeypatch)
    item = _late_item(
        "agents",
        title="Introducing Agents API",
        published_at=datetime(2026, 9, 10, tzinfo=UTC),
        fetched_at=FIRST_DAY,
    )
    collected[:] = [item]
    collect_intake(project, app, now=FIRST_DAY)
    refetch_day = FIRST_DAY + timedelta(days=8)
    collected[:] = [item.model_copy(update={"fetched_at": refetch_day})]
    refetched = collect_intake(project, app, now=refetch_day)
    checkpoint = load_checkpoint_by_batch_id(project, refetched.checkpoint.manifest.batch_id)
    prepared = prepare_process(
        project,
        app,
        batch_id=checkpoint.manifest.batch_id,
        now=refetch_day,
    )
    assert prepared.selection.records == []
    reloaded = ProcessingLedgerStore(project / "data/intake/ledger.json")
    record = checkpoint.items[0]
    entry = reloaded.get(record.input_id, record.content_version)
    assert entry.first_seen_at == FIRST_DAY
    assert entry.candidate_diagnostics == {"selected": False, "reason": "expired"}


def test_deferred_budget_diagnostics_survive_ledger_reload(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    app = load_model(project / "config/app.yaml", AppConfig).model_copy(
        update={"maximum_ai_items": 1, "maximum_raw_items": 1, "maximum_ai_calls": 10}
    )
    collected = _install_collection(monkeypatch)
    collected[:] = [
        _late_item("budget-a", title="Budget candidate A", published_at=NOW - timedelta(hours=2)),
        _late_item("budget-b", title="Budget candidate B", published_at=NOW - timedelta(hours=1)),
    ]
    intake = collect_intake(project, app, now=NOW)
    checkpoint = load_checkpoint_by_batch_id(project, intake.checkpoint.manifest.batch_id)
    prepared = prepare_process(project, app, batch_id=checkpoint.manifest.batch_id, now=NOW)
    assert len(prepared.selection.records) == 1
    assert len(prepared.selection.deferred) == 1
    deferred = prepared.selection.deferred[0]
    reloaded = ProcessingLedgerStore(project / "data/intake/ledger.json")
    entry = reloaded.get(deferred.input_id, deferred.content_version)
    assert entry.processing is ProcessingStatus.DEFERRED_BUDGET
    assert entry.reason_code is ReasonCode.DEFERRED_BUDGET
    assert entry.candidate_diagnostics["selected"] is False
    assert entry.candidate_diagnostics["reason"] == "deferred_budget"
    assert entry.candidate_diagnostics["cap"] == 1


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

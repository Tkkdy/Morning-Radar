import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from morning_radar.collectors.http import HttpClient
from morning_radar.collectors.orchestrator import CollectionResult
from morning_radar.collectors.rss import RSSCollector
from morning_radar.intake.candidates import select_process_candidates
from morning_radar.intake.checkpoint import (
    load_complete_checkpoint,
    write_intake_checkpoint,
)
from morning_radar.intake.identity import content_version
from morning_radar.intake.inspect import inspect_intake
from morning_radar.intake.ledger import ProcessingLedgerStore
from morning_radar.intake.models import (
    CheckpointManifest,
    IntakeCheckpoint,
    ProcessingStatus,
    ReasonCode,
)
from morning_radar.intake.recovery import recover_unfinished_records
from morning_radar.models import RawItem
from morning_radar.pipeline import MorningRadarPipeline
from morning_radar.settings import SourceConfig
from morning_radar.storage import read_json, save_model

NOW = datetime(2026, 9, 8, 5, 0, tzinfo=UTC)


def raw_item(
    suffix: str,
    *,
    published_at: datetime,
    title: str | None = None,
    source_id: str = "openai",
) -> RawItem:
    return RawItem(
        id=f"item-{suffix}",
        title=title or f"Official announcement {suffix}",
        url=f"https://openai.com/news/{suffix}",
        source_name="OpenAI News",
        source_type="rss",
        published_at=published_at,
        fetched_at=NOW,
        summary="An official product announcement with enough detail.",
        content_excerpt="An official product announcement with enough detail.",
        source_role="official_primary",
        statement_type="factual_announcement",
        metadata={"official": True, "priority": "high", "source_id": source_id},
    )


def test_content_version_ignores_fetch_time_and_heat() -> None:
    first = raw_item("a", published_at=NOW - timedelta(hours=2))
    second = first.model_copy(
        update={
            "fetched_at": NOW + timedelta(hours=1),
            "metadata": {**first.metadata, "stars": 99, "change_percent": 0.4},
        }
    )
    changed = first.model_copy(update={"title": "Updated official announcement a"})
    assert content_version(first) == content_version(second)
    assert content_version(first) != content_version(changed)


def test_collect_does_not_construct_ai_provider(tmp_path, monkeypatch) -> None:
    def fail_provider(*args, **kwargs):
        raise AssertionError("collect must not create an AI provider")

    monkeypatch.setattr(
        "morning_radar.ai.deepseek_provider.DeepSeekProvider.from_environment",
        fail_provider,
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    source = Path(".").resolve()
    project = tmp_path / "project"
    shutil.copytree(source / "config", project / "config")
    shutil.copytree(source / "fixtures", project / "fixtures")
    pipeline = MorningRadarPipeline(project)
    intake = pipeline.collect(fixtures=True, dry_run=True, now=NOW)
    assert intake.checkpoint.manifest.complete is True
    assert intake.path.exists()
    assert (project / ".tmp/dry-run/data/intake/ledger.json").exists()
    assert not (project / "data/intake").exists()


def test_checkpoint_survives_new_workdir(tmp_path) -> None:
    first = tmp_path / "runner-a"
    second = tmp_path / "runner-b"
    item = raw_item("kept", published_at=NOW - timedelta(hours=3))
    collection = CollectionResult(items=[item], raw_collected=1, after_buffer=1, after_dedup=1)
    checkpoint = write_intake_checkpoint(
        first,
        items=[item],
        now=NOW,
        cutoff_at=NOW - timedelta(hours=30),
        collection=collection,
        source_state={"rss": {"openai": {"etag": '"v1"', "last_modified": ""}}},
    )
    dest = second / "data/intake/checkpoints"
    dest.mkdir(parents=True)
    shutil.copy2(
        first / "data/intake/checkpoints" / f"{checkpoint.manifest.batch_id}.json",
        dest / f"{checkpoint.manifest.batch_id}.json",
    )
    loaded = load_complete_checkpoint(
        dest / f"{checkpoint.manifest.batch_id}.json"
    )
    assert loaded is not None
    assert loaded.items[0].input_id == item.id
    assert loaded.manifest.complete is True


def test_incomplete_checkpoint_is_not_consumed(tmp_path) -> None:
    path = tmp_path / "data/intake/checkpoints/batch-bad.json"
    path.parent.mkdir(parents=True)
    incomplete = IntakeCheckpoint(
        manifest=CheckpointManifest(
            complete=False,
            batch_id="batch-bad",
            run_id="run-bad",
            created_at=NOW,
            cutoff_at=NOW,
            item_count=1,
        ),
        items=[],
        source_state={"rss": {"openai": {"etag": '"stale"'}}},
    )
    save_model(path, incomplete)
    assert load_complete_checkpoint(path) is None


def test_rss_etag_is_not_written_until_commit(tmp_path) -> None:
    xml = Path("fixtures/rss/example.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=xml,
            headers={"ETag": '"fixture-v1"', "Last-Modified": "Wed, 22 Jul 2026 23:00:00 GMT"},
        )

    state = tmp_path / "rss.json"
    collector = RSSCollector(
        [
            SourceConfig(
                id="fixture",
                name="Fixture Feed",
                type="rss",
                url="https://feeds.example/fixture.xml",
                priority="high",
                topics=["ai_models"],
                official=True,
            )
        ],
        http=HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler))),
        state_path=state,
        now=datetime(2026, 7, 23, 1, tzinfo=UTC),
    )
    items = collector.collect()
    assert items
    assert not state.exists()
    collector.commit_source_state()
    assert read_json(state)["fixture"]["etag"] == '"fixture-v1"'


def test_etag_without_checkpoint_is_inconsistent(tmp_path) -> None:
    from morning_radar.intake.checkpoint import inconsistent_cache_sources

    state = tmp_path / "data/state/rss.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps({"openai": {"etag": '"v1"', "last_modified": ""}}),
        encoding="utf-8",
    )
    assert inconsistent_cache_sources(
        tmp_path,
        state_name="rss",
        state_path=state,
    ) == ["openai"]


def test_recovery_bypasses_24h_window_and_reserves_slots() -> None:
    fresh = [
        raw_item(f"new-{index}", published_at=NOW - timedelta(hours=1), title=f"New {index}")
        for index in range(10)
    ]
    recovered = raw_item("old", published_at=NOW - timedelta(hours=29))
    from morning_radar.intake.checkpoint import build_intake_records

    fresh_records = build_intake_records(fresh, batch_id="b", run_id="r")
    recovery_records = build_intake_records([recovered], batch_id="old", run_id="old")
    selected = select_process_candidates(
        fresh=fresh_records,
        recovery=recovery_records,
        maximum_items=5,
        reserved_recovery_slots=1,
    )
    assert recovered.id in {item.id for item in selected.items}
    assert len(selected.items) == 5
    assert selected.deferred


def test_budget_deferral_is_not_recorded_as_irrelevant(tmp_path) -> None:
    item = raw_item("queued", published_at=NOW - timedelta(hours=2))
    collection = CollectionResult(items=[item], raw_collected=1, after_buffer=1, after_dedup=1)
    checkpoint = write_intake_checkpoint(
        tmp_path,
        items=[item],
        now=NOW,
        cutoff_at=NOW - timedelta(hours=30),
        collection=collection,
        source_state={},
    )
    ledger = ProcessingLedgerStore(tmp_path / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint, now=NOW)
    ledger.update(
        item.id,
        checkpoint.items[0].content_version,
        now=NOW,
        processing=ProcessingStatus.DEFERRED_BUDGET,
        reason_code=ReasonCode.DEFERRED_BUDGET,
        outcome="deferred_budget",
    )
    ledger.save()
    entry = ledger.get(item.id, checkpoint.items[0].content_version)
    assert entry is not None
    assert entry.processing is ProcessingStatus.DEFERRED_BUDGET
    assert entry.reason_code is ReasonCode.DEFERRED_BUDGET
    recovered, aged = recover_unfinished_records(
        tmp_path,
        ledger=ledger,
        now=NOW + timedelta(days=1),
        lookback_days=7,
        maximum_items=8,
    )
    assert recovered
    assert aged == []


def test_inspect_unknown_event_is_not_a_coverage_gap(tmp_path) -> None:
    payload = inspect_intake(tmp_path, url="https://openai.com/missing")
    assert payload["found"] is False
    assert payload["coverage_gap"] is False
    assert "未找到采集证据" in str(payload["message"])


def test_cli_collect_help_and_inspect_unknown() -> None:
    from morning_radar.cli import build_parser

    parser = build_parser()
    collect_help = parser.parse_args(["collect", "--dry-run"])
    assert collect_help.command == "collect"
    assert collect_help.dry_run is True
    inspect_help = parser.parse_args(["inspect", "--url", "https://example.com/x", "--json"])
    assert inspect_help.command == "inspect"

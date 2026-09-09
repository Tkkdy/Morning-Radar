from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from morning_radar.ai import AIBudget, FakeAIProvider
from morning_radar.ai.deepseek_provider import DeepSeekProvider
from morning_radar.ai.models import ResearchResolutionBatch, ResearchResolutionDraft
from morning_radar.collectors.orchestrator import CollectionResult
from morning_radar.intake.checkpoint import (
    latest_complete_checkpoint,
    load_checkpoint_by_batch_id,
    write_intake_checkpoint,
)
from morning_radar.intake.ledger import ProcessingLedgerStore
from morning_radar.intake.models import ProcessingStatus, PublishStatus
from morning_radar.intake.publish import PublishStore
from morning_radar.models import RawItem, ResearchDisposition, SourceRole, StatementType
from morning_radar.pipeline import MorningRadarPipeline
from morning_radar.research.engine import resolve_research
from morning_radar.research.isolation import IsolatedResearchResult
from morning_radar.time_utils import display_date

DAY_N = datetime(2026, 9, 7, 5, tzinfo=UTC)
DAY_N1 = datetime(2026, 9, 8, 5, tzinfo=UTC)


def official_item(suffix: str, *, published_at: datetime, title: str | None = None) -> RawItem:
    return RawItem(
        id=f"item-{suffix}",
        title=title or f"Official announcement {suffix}",
        url=f"https://openai.com/index/{suffix}",
        source_name="OpenAI News",
        source_type="rss",
        published_at=published_at,
        fetched_at=DAY_N,
        summary="An official product announcement with enough concrete detail.",
        content_excerpt="An official product announcement with enough concrete detail.",
        source_role=SourceRole.OFFICIAL_PRIMARY,
        statement_type=StatementType.FACTUAL_ANNOUNCEMENT,
        metadata={"official": True, "priority": "high", "source_id": "openai_news"},
    )


def save_checkpoint(root: Path, items: list[RawItem], *, now: datetime, batch_id: str):
    collection = CollectionResult(
        items=items,
        raw_collected=len(items),
        after_buffer=len(items),
        after_dedup=len(items),
    )
    return write_intake_checkpoint(
        root,
        items=items,
        now=now,
        cutoff_at=now - timedelta(hours=30),
        collection=collection,
        source_state={
            "rss": {
                "openai_news": {
                    "etag": '"v1"',
                    "status": "ok",
                    "item_ids": [item.id for item in items],
                }
            }
        },
        batch_id=batch_id,
        run_id=f"run-{batch_id}",
    )


def copy_project(tmp_path: Path) -> Path:
    source = Path(".").resolve()
    project = tmp_path / "project"
    for name in ("config", "fixtures", "templates", "prompts"):
        shutil.copytree(source / name, project / name)
    (project / "site/assets").mkdir(parents=True)
    shutil.copy2(source / "site/assets/style.css", project / "site/assets/style.css")
    return project


def install_fake_provider(monkeypatch) -> None:
    def from_environment(*, budget, prompt_dir):
        provider = FakeAIProvider()
        provider.budget = budget
        return provider

    monkeypatch.setattr(
        "morning_radar.pipeline.DeepSeekProvider.from_environment",
        from_environment,
    )


def test_t01_latest_checkpoint_uses_created_at_not_filename(tmp_path) -> None:
    older = official_item("old", published_at=DAY_N - timedelta(hours=2))
    newer = official_item("new", published_at=DAY_N - timedelta(hours=1))
    save_checkpoint(tmp_path, [older], now=DAY_N, batch_id="batch-zzzz")
    save_checkpoint(tmp_path, [newer], now=DAY_N1, batch_id="batch-0000")
    latest = latest_complete_checkpoint(tmp_path)
    assert latest is not None
    assert latest.manifest.batch_id == "batch-0000"
    assert latest.items[0].item.id == "item-new"


def test_t02_explicit_batch_and_missing_batch_error(tmp_path) -> None:
    item = official_item("kept", published_at=DAY_N - timedelta(hours=2))
    save_checkpoint(tmp_path, [item], now=DAY_N, batch_id="batch-explicit")
    save_checkpoint(tmp_path, [item], now=DAY_N1, batch_id="batch-other")
    loaded = load_checkpoint_by_batch_id(tmp_path, "batch-explicit")
    assert loaded.manifest.created_at == DAY_N
    try:
        load_checkpoint_by_batch_id(tmp_path, "batch-missing")
    except FileNotFoundError:
        return
    raise AssertionError("missing batch should error")


def test_t03_cross_day_empty_batch_still_processes_saved_input(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    install_fake_provider(monkeypatch)
    item = official_item(
        "hf-incident",
        published_at=DAY_N1 - timedelta(hours=29),
        title="The Hugging Face incident and the road ahead",
    )
    checkpoint = save_checkpoint(project, [item], now=DAY_N, batch_id="batch-day-n")
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint, now=DAY_N)
    ledger.save()
    pipeline = MorningRadarPipeline(project)
    brief = pipeline.process(batch_id="batch-day-n", now=DAY_N1, notify=False)
    assert brief.date == display_date(DAY_N1)
    displayed = brief.top_stories + brief.ai_and_open_source + brief.other_reading
    assert displayed
    entry = ProcessingLedgerStore(project / "data/intake/ledger.json").get(
        item.id, checkpoint.items[0].content_version
    )
    assert entry is not None
    assert entry.processing is ProcessingStatus.COMPLETED


def test_t07_second_process_is_noop(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    calls = {"count": 0}

    def from_environment(*, budget, prompt_dir):
        calls["count"] += 1
        provider = FakeAIProvider()
        provider.budget = budget
        return provider

    monkeypatch.setattr(
        "morning_radar.pipeline.DeepSeekProvider.from_environment",
        from_environment,
    )
    item = official_item("once", published_at=DAY_N - timedelta(hours=3))
    checkpoint = save_checkpoint(project, [item], now=DAY_N, batch_id="batch-once")
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint, now=DAY_N)
    ledger.save()
    pipeline = MorningRadarPipeline(project)
    first = pipeline.process(batch_id="batch-once", now=DAY_N, notify=False)
    second = pipeline.process(batch_id="batch-once", now=DAY_N, notify=False)
    assert calls["count"] == 1
    assert first.date == second.date


def test_t09_processing_cap_does_not_drop_saved_evidence(tmp_path) -> None:
    items = [
        official_item(f"n{index}", published_at=DAY_N - timedelta(hours=index + 1))
        for index in range(3)
    ]
    collection = CollectionResult(
        items=items,
        raw_collected=3,
        after_buffer=3,
        after_dedup=3,
    )
    checkpoint = write_intake_checkpoint(
        tmp_path,
        items=items,
        now=DAY_N,
        cutoff_at=DAY_N - timedelta(hours=30),
        collection=collection,
        source_state={},
        batch_id="batch-three",
        run_id="run-three",
    )
    ledger = ProcessingLedgerStore(tmp_path / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint, now=DAY_N)
    ledger.save()
    assert len(checkpoint.items) == 3
    assert len(ledger.ledger.entries) == 3


def test_t11_null_case_does_not_drop_valid_sibling() -> None:
    from tests.unit.test_deepseek_provider import FakeChatCompletions, research_case

    valid = ResearchResolutionDraft(
        case_id="research-1",
        in_scope=True,
        scope_rationale="该观察直接涉及 AI 产品行为。",
        disposition=ResearchDisposition.RADAR_SIGNAL,
        statement_type=StatementType.FIRSTHAND_OBSERVATION,
        claim="开发者观察到结构化输出发生截断。",
        why_notable="该变化影响实际工作流。",
        missing_evidence=["独立复现"],
        uncertainty="尚待官方确认。",
    )
    payload = json.dumps({"cases": [valid.model_dump(mode="json"), None]}, ensure_ascii=False)
    provider = DeepSeekProvider(
        model="configured-test-model",
        api_key="test-key",
        base_url="https://api.deepseek.test",
        budget=AIBudget(5, 100_000, 20),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeChatCompletions([payload]))
        ),
        network_attempts=1,
    )
    isolated = provider.resolve_research_cases_isolated([research_case()])
    assert isolated.batch.cases
    assert isolated.batch.cases[0].case_id == "research-1"
    assert isolated.invalid_ids


def test_t12_english_case_is_rejected_and_schema_is_complete() -> None:
    from tests.unit.test_deepseek_provider import FakeChatCompletions, research_case

    narrative = (
        "This is a long English narrative that should fail Chinese "
        "validation for user visible research output fields."
    )
    english = {
        "case_id": "research-1",
        "in_scope": True,
        "scope_rationale": narrative,
        "disposition": "radar_signal",
        "statement_type": "firsthand_observation",
        "claim": narrative,
        "why_notable": narrative,
        "missing_evidence": [],
        "uncertainty": narrative,
    }
    payload = json.dumps({"cases": [english]}, ensure_ascii=False)
    provider = DeepSeekProvider(
        model="configured-test-model",
        api_key="test-key",
        base_url="https://api.deepseek.test",
        budget=AIBudget(5, 100_000, 20),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeChatCompletions([payload]))
        ),
        network_attempts=1,
    )
    isolated = provider.resolve_research_cases_isolated([research_case()])
    assert isolated.batch.cases == []
    assert isolated.invalid_ids == ["research-1"]
    schema = ResearchResolutionBatch.model_json_schema()
    assert "verified_story_candidate" in json.dumps(schema)
    assert "cases" in schema["properties"]


def test_t13_split_retries_are_shared_not_exponential() -> None:
    from tests.unit.test_research import item

    class AlwaysTruncated(FakeAIProvider):
        def __init__(self) -> None:
            self.calls = 0
            self.budget = AIBudget(10, 100_000, 20)

        def resolve_research_cases_isolated(self, cases):
            self.calls += 1
            self.budget.consume(json.dumps([case.id for case in cases]), item_count=len(cases))
            return IsolatedResearchResult(
                batch=ResearchResolutionBatch(),
                truncated=True,
            )

    leads = [
        item(f"p{index}", role=SourceRole.PRACTITIONER, url=f"https://example.com/{index}")
        for index in range(8)
    ]
    provider = AlwaysTruncated()
    resolve_research(
        leads,
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=0,
        split_retry_attempts=2,
    )
    assert provider.calls <= 3
    assert provider.budget.calls_used <= 3


def test_t14_generated_status_matches_displayed_stories(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    install_fake_provider(monkeypatch)
    shown = official_item("shown", published_at=DAY_N - timedelta(hours=2))
    hidden = official_item(
        "hidden",
        published_at=DAY_N - timedelta(hours=2),
        title="Minor market note hidden",
    )
    checkpoint = save_checkpoint(project, [shown, hidden], now=DAY_N, batch_id="batch-show")
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint, now=DAY_N)
    ledger.save()
    pipeline = MorningRadarPipeline(project)
    brief = pipeline.process(batch_id="batch-show", now=DAY_N, notify=False)
    store = ProcessingLedgerStore(project / "data/intake/ledger.json")
    displayed = brief.top_stories + brief.ai_and_open_source + brief.other_reading
    shown_ids = {item_id for item in displayed for item_id in item.story_ids}
    generated = [
        entry
        for entry in store.ledger.entries.values()
        if entry.publish is PublishStatus.GENERATED
    ]
    assert generated
    assert all(entry.story_id in shown_ids for entry in generated)


def test_t15_record_deploy_is_idempotent_and_hash_checked(tmp_path) -> None:
    store = PublishStore(tmp_path / "publish.json")
    store.mark_generated(
        brief_date="2026-09-08",
        brief_hash="abc123",
        generated_at=DAY_N1,
        artifact_path="data/briefs/2026-09-08.json",
    )
    first = store.mark_deployed("2026-09-08", now=DAY_N1, brief_hash="abc123")
    second = store.mark_deployed("2026-09-08", now=DAY_N1 + timedelta(hours=1), brief_hash="abc123")
    assert first.deployed is True
    assert second.deployed_at == first.deployed_at
    try:
        store.mark_deployed("2026-09-08", now=DAY_N1, brief_hash="other")
    except ValueError:
        pass
    else:
        raise AssertionError("hash mismatch must fail")
    same = store.mark_generated(
        brief_date="2026-09-08",
        brief_hash="abc123",
        generated_at=DAY_N1,
        artifact_path="data/briefs/2026-09-08.json",
    )
    assert same.deployed is True


def test_historical_samples_are_loaded_without_inventing_missing_days() -> None:
    payload = json.loads(
        Path("tests/fixtures/reliability/historical_samples.json").read_text(encoding="utf-8")
    )
    samples = {sample["id"]: sample for sample in payload["samples"]}
    assert samples["H01"]["actual_raw"] is None
    assert samples["H05"]["relevance_score"] == 0.3
    assert samples["H05"]["formal_threshold"] == 0.55

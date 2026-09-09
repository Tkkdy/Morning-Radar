from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from morning_radar.ai import AIBillingUnavailable, AIBudget, AIOutputError, FakeAIProvider
from morning_radar.ai.models import ClassificationBatch, ClassifiedItem, ResearchResolutionBatch
from morning_radar.cli import main as cli_main
from morning_radar.collectors.orchestrator import CollectionResult
from morning_radar.intake.checkpoint import (
    inconsistent_cache_sources,
    write_intake_checkpoint,
)
from morning_radar.intake.identity import content_version
from morning_radar.intake.ledger import ProcessingLedgerStore
from morning_radar.intake.models import ProcessingStatus, PublishStatus, ReasonCode
from morning_radar.intake.publish import PublishStore
from morning_radar.intake.service import collect_intake
from morning_radar.models import SourceRole, Story
from morning_radar.pipeline import MorningRadarPipeline, _artifact_digest
from morning_radar.research.engine import resolve_research
from morning_radar.research.isolation import IsolatedResearchResult
from morning_radar.settings import AppConfig, load_model
from morning_radar.storage import load_models
from tests.unit.test_phase1_patch import (
    DAY_N,
    DAY_N1,
    copy_project,
    install_fake_provider,
    official_item,
    save_checkpoint,
)
from tests.unit.test_research import item as research_item


def test_u01_rebuilds_missing_site_without_new_provider(tmp_path, monkeypatch) -> None:
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
    item = official_item("site", published_at=DAY_N - timedelta(hours=2))
    checkpoint = save_checkpoint(project, [item], now=DAY_N, batch_id="batch-site")
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint, now=DAY_N)
    ledger.save()
    pipeline = MorningRadarPipeline(project)
    brief = pipeline.process(batch_id="batch-site", now=DAY_N, notify=False)
    site = project / "site/index.html"
    assert site.exists()
    site.unlink()
    (project / "data/state/site_build.json").unlink(missing_ok=True)
    again = MorningRadarPipeline(project).process(batch_id="batch-site", now=DAY_N, notify=False)
    assert again.date == brief.date
    assert site.exists()
    assert "Official announcement site" in site.read_text(encoding="utf-8")
    assert calls["count"] == 1


def test_u02_rebuild_replaces_stale_site(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    install_fake_provider(monkeypatch)
    item = official_item("fresh-page", published_at=DAY_N - timedelta(hours=2))
    checkpoint = save_checkpoint(project, [item], now=DAY_N, batch_id="batch-page")
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint, now=DAY_N)
    ledger.save()
    pipeline = MorningRadarPipeline(project)
    pipeline.process(batch_id="batch-page", now=DAY_N, notify=False)
    site = project / "site/index.html"
    site.write_text("stale homepage without current brief", encoding="utf-8")
    (project / "data/state/site_build.json").unlink(missing_ok=True)
    pipeline.process(batch_id="batch-page", now=DAY_N, notify=False)
    html = site.read_text(encoding="utf-8")
    assert "stale homepage" not in html
    assert "fresh-page" in html or "Official announcement" in html


def test_u04_same_day_increment_keeps_previous_story(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    install_fake_provider(monkeypatch)
    first = official_item("alpha", published_at=DAY_N - timedelta(hours=3), title="Alpha kept")
    second = official_item("beta", published_at=DAY_N - timedelta(hours=2), title="Beta added")
    checkpoint_a = save_checkpoint(project, [first], now=DAY_N, batch_id="batch-a")
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint_a, now=DAY_N)
    ledger.save()
    pipeline = MorningRadarPipeline(project)
    pipeline.process(batch_id="batch-a", now=DAY_N, notify=False)
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    done = ledger.get(first.id, content_version(first))
    assert done is not None
    assert done.processing is ProcessingStatus.COMPLETED
    checkpoint_ab = save_checkpoint(
        project, [first, second], now=DAY_N + timedelta(minutes=5), batch_id="batch-ab"
    )
    ledger.upsert_checkpoint(checkpoint_ab, now=DAY_N)
    ledger.save()
    brief = MorningRadarPipeline(project).process(batch_id="batch-ab", now=DAY_N, notify=False)
    titles = [
        item.title
        for item in brief.top_stories + brief.ai_and_open_source + brief.other_reading
    ]
    assert any("Alpha" in title for title in titles)
    assert any("Beta" in title for title in titles)
    html = (project / "site/index.html").read_text(encoding="utf-8")
    assert "Alpha" in html and "Beta" in html
    stories = load_models(project / "data/stories" / f"{brief.date}.json", Story)
    story_titles = [story.canonical_title for story in stories]
    assert any("Alpha" in title for title in story_titles)
    assert any("Beta" in title for title in story_titles)


class IrrelevantProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.classify_calls = 0
        self.budget = AIBudget(20, 100_000, 40)

    def classify_items(self, items):
        self.classify_calls += 1
        return ClassificationBatch(
            items=[
                ClassifiedItem(
                    item_id=item.id,
                    relevant=False,
                    relevance_reason="不相关。",
                    important=False,
                    importance_reason="一般。",
                    category="other_reading",
                )
                for item in items
            ]
        )


def test_u06_classified_irrelevant_is_not_a_retryable_failure(
    tmp_path, monkeypatch
) -> None:
    project = copy_project(tmp_path)
    provider = IrrelevantProvider()

    def from_environment(*, budget, prompt_dir):
        provider.budget = budget
        return provider

    monkeypatch.setattr(
        "morning_radar.pipeline.DeepSeekProvider.from_environment",
        from_environment,
    )
    item = official_item("noise", published_at=DAY_N - timedelta(hours=2))
    checkpoint = save_checkpoint(project, [item], now=DAY_N, batch_id="batch-noise")
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint, now=DAY_N)
    ledger.save()
    pipeline = MorningRadarPipeline(project)
    pipeline.process(batch_id="batch-noise", now=DAY_N, notify=False)
    entry = ProcessingLedgerStore(project / "data/intake/ledger.json").get(
        item.id, content_version(item)
    )
    assert entry is not None
    assert entry.processing is ProcessingStatus.EXCLUDED
    assert entry.reason_code is ReasonCode.CLASSIFIED_IRRELEVANT
    first_calls = provider.classify_calls
    pipeline.process(batch_id="batch-noise", now=DAY_N, notify=False)
    assert provider.classify_calls == first_calls


def test_u08_partial_research_success_covers_all_original_cases() -> None:
    class SplitThenSucceed(FakeAIProvider):
        def __init__(self) -> None:
            self.calls = 0
            self.budget = AIBudget(10, 100_000, 20)

        def resolve_research_cases_isolated(self, cases):
            self.calls += 1
            self.budget.consume("x" * len(cases), item_count=len(cases))
            if len(cases) > 2:
                return IsolatedResearchResult(
                    batch=ResearchResolutionBatch(),
                    truncated=True,
                )
            return IsolatedResearchResult(batch=super().resolve_research_cases(cases))

    leads = [
        research_item(f"p{index}", role=SourceRole.PRACTITIONER, url=f"https://example.com/{index}")
        for index in range(8)
    ]
    provider = SplitThenSucceed()
    result = resolve_research(
        leads,
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=0,
        split_retry_attempts=2,
    )
    lead_ids = {lead.id for lead in leads}
    successful_leads = {
        case.lead.raw_item_id
        for case in result.cases
        if case.lead.raw_item_id not in result.item_outcomes
    }
    assert lead_ids == set(result.item_outcomes) | successful_leads
    assert len(successful_leads) == 2
    assert len(result.item_outcomes) == 6
    assert all(
        reason is ReasonCode.RESEARCH_DEFERRED
        for reason in result.item_outcomes.values()
    )
    assert provider.calls == 3
    assert provider.budget.calls_used == 3


def test_u09_fatal_child_stops_sibling_calls() -> None:
    class FatalAfterFirstSplit(FakeAIProvider):
        def __init__(self) -> None:
            self.calls = 0
            self.sizes: list[int] = []
            self.budget = AIBudget(10, 100_000, 20)

        def resolve_research_cases_isolated(self, cases):
            self.calls += 1
            self.sizes.append(len(cases))
            self.budget.consume("x", item_count=len(cases))
            if len(cases) > 4:
                return IsolatedResearchResult(
                    batch=ResearchResolutionBatch(),
                    truncated=True,
                )
            raise AIBillingUnavailable("billing unavailable")

    leads = [
        research_item(f"p{index}", role=SourceRole.PRACTITIONER, url=f"https://example.com/f{index}")
        for index in range(8)
    ]
    provider = FatalAfterFirstSplit()
    result = resolve_research(
        leads,
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=0,
        split_retry_attempts=2,
    )
    assert provider.calls == 2
    assert provider.sizes == [8, 4]
    assert len(result.item_outcomes) == 8
    assert all(
        reason is ReasonCode.RESEARCH_FATAL
        for reason in result.item_outcomes.values()
    )


def test_u11_stale_item_is_saved_then_excluded(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    fresh = official_item("fresh", published_at=DAY_N - timedelta(hours=2))
    stale = official_item("stale31", published_at=DAY_N - timedelta(hours=31))
    collection = CollectionResult(
        items=[fresh, stale],
        raw_collected=2,
        after_buffer=2,
        after_dedup=2,
    )

    def fake_production(*args, **kwargs):
        return collection, [], []

    monkeypatch.setattr(
        "morning_radar.intake.service._production_collect",
        fake_production,
    )
    app = load_model(project / "config/app.yaml", AppConfig)
    intake = collect_intake(project, app, now=DAY_N)
    assert len(intake.checkpoint.items) == 2
    pipeline = MorningRadarPipeline(project)
    install_fake_provider(monkeypatch)
    pipeline.process(batch_id=intake.checkpoint.manifest.batch_id, now=DAY_N, notify=False)
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    stale_entry = ledger.get(stale.id, content_version(stale))
    assert stale_entry is not None
    assert stale_entry.reason_code is ReasonCode.EXCLUDED_STALE
    assert stale_entry.processing is ProcessingStatus.EXCLUDED


def test_u12_declared_item_ids_without_payload_are_inconsistent(tmp_path) -> None:
    item = official_item("real", published_at=DAY_N - timedelta(hours=2))
    write_intake_checkpoint(
        tmp_path,
        items=[item],
        now=DAY_N,
        cutoff_at=DAY_N - timedelta(hours=30),
        collection=CollectionResult(items=[item], raw_collected=1, after_buffer=1, after_dedup=1),
        source_state={
            "rss": {
                "openai_news": {
                    "etag": '"v9"',
                    "status": "ok",
                    "item_ids": ["missing-payload"],
                }
            }
        },
        batch_id="batch-missing-payload",
        run_id="run-missing-payload",
    )
    state = tmp_path / "data/state/rss.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"openai_news": {"etag": '"v9"'}}), encoding="utf-8")
    assert "openai_news" in inconsistent_cache_sources(
        tmp_path, state_name="rss", state_path=state
    )


def test_u13_daily_workflow_passes_brief_hash() -> None:
    workflow = Path(".github/workflows/daily-brief.yml").read_text(encoding="utf-8")
    assert "--brief-hash" in workflow
    assert "steps.process.outputs.brief_hash" in workflow
    assert "record-deploy --date" in workflow

def _seeded_ledger(project, checkpoint):
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint, now=DAY_N)
    ledger.save()
    return ledger


def _practitioner(suffix: str):
    return research_item(
        f"p{suffix}",
        role=SourceRole.PRACTITIONER,
        url=f"https://example.com/{suffix}",
    ).model_copy(
        update={
            "published_at": DAY_N - timedelta(hours=2),
            "fetched_at": DAY_N,
        }
    )


def test_u03_interrupted_save_recovers_in_new_workdir(tmp_path, monkeypatch) -> None:
    first = copy_project(tmp_path / "first")
    install_fake_provider(monkeypatch)
    item = official_item(
        "hf-incident",
        published_at=DAY_N1 - timedelta(hours=29),
        title="The Hugging Face incident and the road ahead",
    )
    checkpoint = save_checkpoint(first, [item], now=DAY_N, batch_id="batch-day-n")
    _seeded_ledger(first, checkpoint)

    def boom(self, *args, **kwargs):
        raise RuntimeError("interrupted before save")

    monkeypatch.setattr(MorningRadarPipeline, "_commit_generation", boom)
    try:
        MorningRadarPipeline(first).process(batch_id="batch-day-n", now=DAY_N, notify=False)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected save interruption")

    second = copy_project(tmp_path / "second")
    shutil.copytree(first / "data/intake", second / "data/intake", dirs_exist_ok=True)
    empty = save_checkpoint(second, [], now=DAY_N1, batch_id="batch-empty")
    ledger = ProcessingLedgerStore(second / "data/intake/ledger.json")
    ledger.upsert_checkpoint(empty, now=DAY_N1)
    ledger.save()
    monkeypatch.undo()
    install_fake_provider(monkeypatch)
    brief = MorningRadarPipeline(second).process(
        batch_id="batch-empty", now=DAY_N1, notify=False
    )
    displayed = brief.top_stories + brief.ai_and_open_source + brief.other_reading
    assert displayed
    entry = ProcessingLedgerStore(second / "data/intake/ledger.json").get(
        item.id, checkpoint.items[0].content_version
    )
    assert entry is not None
    assert entry.processing is ProcessingStatus.COMPLETED

def test_u05_duplicate_same_day_keeps_existing_brief(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    install_fake_provider(monkeypatch)
    item = official_item("alpha", published_at=DAY_N - timedelta(hours=3), title="Alpha kept")
    checkpoint = save_checkpoint(project, [item], now=DAY_N, batch_id="batch-dup")
    _seeded_ledger(project, checkpoint)
    MorningRadarPipeline(project).process(batch_id="batch-dup", now=DAY_N, notify=False)
    again = MorningRadarPipeline(project).process(batch_id="batch-dup", now=DAY_N, notify=False)
    stories = load_models(project / "data/stories" / f"{again.date}.json", Story)
    assert any("Alpha" in story.canonical_title for story in stories)
    html = (project / "site/index.html").read_text(encoding="utf-8")
    assert "Alpha" in html


def test_u05_failed_increment_retries_merged_set(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    install_fake_provider(monkeypatch)
    first = official_item("alpha", published_at=DAY_N - timedelta(hours=3), title="Alpha kept")
    second = official_item("beta", published_at=DAY_N - timedelta(hours=2), title="Beta added")
    checkpoint_a = save_checkpoint(project, [first], now=DAY_N, batch_id="batch-a2")
    ledger = _seeded_ledger(project, checkpoint_a)
    MorningRadarPipeline(project).process(batch_id="batch-a2", now=DAY_N, notify=False)
    brief_path = next((project / "data/briefs").glob("*.json"))
    brief_date = brief_path.stem
    original_save = MorningRadarPipeline._commit_generation
    calls = {"count": 0}

    def flaky(self, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("increment save failed")
        return original_save(self, *args, **kwargs)

    checkpoint_ab = save_checkpoint(
        project, [first, second], now=DAY_N + timedelta(minutes=5), batch_id="batch-ab2"
    )
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint_ab, now=DAY_N)
    ledger.save()
    monkeypatch.setattr(MorningRadarPipeline, "_commit_generation", flaky)
    try:
        MorningRadarPipeline(project).process(batch_id="batch-ab2", now=DAY_N, notify=False)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected increment save failure")
    saved = load_models(project / "data/stories" / f"{brief_date}.json", Story)
    assert any("Alpha" in story.canonical_title for story in saved)
    assert not any("Beta" in story.canonical_title for story in saved)
    monkeypatch.undo()
    install_fake_provider(monkeypatch)
    brief = MorningRadarPipeline(project).process(batch_id="batch-ab2", now=DAY_N, notify=False)
    stories = load_models(project / "data/stories" / f"{brief.date}.json", Story)
    titles = [story.canonical_title for story in stories]
    assert any("Alpha" in title for title in titles)
    assert any("Beta" in title for title in titles)
    html = (project / "site/index.html").read_text(encoding="utf-8")
    assert "Alpha" in html and "Beta" in html

def test_u07_merge_failure_retries_and_versions_stay_isolated(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = FakeAIProvider()
    provider.budget = AIBudget(20, 100_000, 40)
    provider.merge_calls = 0

    def merge_story(items):
        provider.merge_calls += 1
        if provider.merge_calls == 1:
            raise AIOutputError("merge failed")
        return FakeAIProvider.merge_story(provider, items)

    provider.merge_story = merge_story

    def from_environment(*, budget, prompt_dir):
        provider.budget = budget
        return provider

    monkeypatch.setattr(
        "morning_radar.pipeline.DeepSeekProvider.from_environment",
        from_environment,
    )
    v1 = official_item("ver", published_at=DAY_N - timedelta(hours=3), title="Version one unique")
    checkpoint = save_checkpoint(project, [v1], now=DAY_N, batch_id="batch-v1")
    _seeded_ledger(project, checkpoint)
    MorningRadarPipeline(project).process(batch_id="batch-v1", now=DAY_N, notify=False)
    failed = ProcessingLedgerStore(project / "data/intake/ledger.json").get(
        v1.id, content_version(v1)
    )
    assert failed is not None
    assert failed.processing is ProcessingStatus.FAILED_RETRY
    assert failed.reason_code is ReasonCode.MERGE_FAILED
    assert failed.attempt_count == 1
    MorningRadarPipeline(project).process(batch_id="batch-v1", now=DAY_N, notify=False)
    recovered = ProcessingLedgerStore(project / "data/intake/ledger.json").get(
        v1.id, content_version(v1)
    )
    assert recovered is not None
    assert recovered.processing is ProcessingStatus.COMPLETED
    assert recovered.attempt_count == 1
    v2 = official_item("ver", published_at=DAY_N - timedelta(hours=2), title="Version two unique")
    checkpoint_v2 = save_checkpoint(
        project, [v2], now=DAY_N + timedelta(minutes=5), batch_id="batch-v2"
    )
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint_v2, now=DAY_N)
    ledger.save()
    MorningRadarPipeline(project).process(batch_id="batch-v2", now=DAY_N, notify=False)
    versions = ProcessingLedgerStore(project / "data/intake/ledger.json").find_by_input_id(v1.id)
    assert {entry.content_version for entry in versions} == {
        content_version(v1),
        content_version(v2),
    }
    by_version = {entry.content_version: entry for entry in versions}
    assert by_version[content_version(v1)].processing is ProcessingStatus.COMPLETED
    assert by_version[content_version(v2)].processing is ProcessingStatus.COMPLETED


def test_u07_score_failure_is_retryable(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = FakeAIProvider()
    provider.score_calls = 0

    def score_story(story):
        provider.score_calls += 1
        if provider.score_calls == 1:
            raise AIOutputError("score failed")
        return FakeAIProvider.score_story(provider, story)

    provider.score_story = score_story

    def from_environment(*, budget, prompt_dir):
        provider.budget = budget
        return provider

    monkeypatch.setattr(
        "morning_radar.pipeline.DeepSeekProvider.from_environment",
        from_environment,
    )
    item = official_item("score", published_at=DAY_N - timedelta(hours=2))
    checkpoint = save_checkpoint(project, [item], now=DAY_N, batch_id="batch-score")
    _seeded_ledger(project, checkpoint)
    MorningRadarPipeline(project).process(batch_id="batch-score", now=DAY_N, notify=False)
    failed = ProcessingLedgerStore(project / "data/intake/ledger.json").get(
        item.id, content_version(item)
    )
    assert failed is not None
    assert failed.processing is ProcessingStatus.FAILED_RETRY
    assert failed.reason_code is ReasonCode.SCORE_FAILED
    MorningRadarPipeline(project).process(batch_id="batch-score", now=DAY_N, notify=False)
    recovered = ProcessingLedgerStore(project / "data/intake/ledger.json").get(
        item.id, content_version(item)
    )
    assert recovered is not None
    assert recovered.processing is ProcessingStatus.COMPLETED

def test_u09_keeps_successful_branch_then_stops_on_fatal() -> None:
    class SuccessThenFatal(FakeAIProvider):
        def __init__(self) -> None:
            self.calls = 0
            self.sizes: list[int] = []
            self.budget = AIBudget(10, 100_000, 20)

        def resolve_research_cases_isolated(self, cases):
            self.calls += 1
            self.sizes.append(len(cases))
            self.budget.consume("x", item_count=len(cases))
            if len(cases) > 4:
                return IsolatedResearchResult(
                    batch=ResearchResolutionBatch(),
                    truncated=True,
                )
            if self.calls == 2:
                return IsolatedResearchResult(batch=super().resolve_research_cases(cases))
            raise AIBillingUnavailable("billing unavailable")

    leads = [
        research_item(f"p{index}", role=SourceRole.PRACTITIONER, url=f"https://example.com/s{index}")
        for index in range(8)
    ]
    provider = SuccessThenFatal()
    result = resolve_research(
        leads,
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=0,
        split_retry_attempts=2,
    )
    assert provider.calls == 3
    assert provider.sizes == [8, 4, 4]
    successful = {
        case.lead.raw_item_id
        for case in result.cases
        if case.lead.raw_item_id not in result.item_outcomes
    }
    assert len(successful) == 4
    assert len(result.item_outcomes) == 4
    assert all(
        reason is ReasonCode.RESEARCH_FATAL
        for reason in result.item_outcomes.values()
    )


def test_u10_mixed_invalid_missing_and_deferred_cover_original_cases() -> None:
    class MixedBatch(FakeAIProvider):
        def __init__(self) -> None:
            self.calls = 0
            self.sizes: list[int] = []
            self.budget = AIBudget(10, 100_000, 20)

        def resolve_research_cases_isolated(self, cases):
            self.calls += 1
            self.sizes.append(len(cases))
            self.budget.consume("x", item_count=len(cases))
            if len(cases) > 4:
                return IsolatedResearchResult(
                    batch=ResearchResolutionBatch(),
                    truncated=True,
                )
            success = FakeAIProvider().resolve_research_cases(cases[:1])
            return IsolatedResearchResult(
                batch=success,
                invalid_ids=[cases[1].id],
                missing_ids=[cases[2].id],
            )

    leads = [
        research_item(f"p{index}", role=SourceRole.PRACTITIONER, url=f"https://example.com/m{index}")
        for index in range(8)
    ]
    provider = MixedBatch()
    result = resolve_research(
        leads,
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=0,
        split_retry_attempts=1,
    )
    assert provider.calls == 2
    assert provider.sizes == [8, 4]
    assert provider.budget.calls_used == 2
    reasons = list(result.item_outcomes.values())
    assert reasons.count(ReasonCode.RESEARCH_OUTPUT_INVALID) == 1
    assert reasons.count(ReasonCode.RESEARCH_CASE_MISSING) == 2
    assert reasons.count(ReasonCode.RESEARCH_DEFERRED) == 4
    successful = {
        case.lead.raw_item_id
        for case in result.cases
        if case.lead.raw_item_id not in result.item_outcomes
    }
    assert len(successful) == 1
    assert set(result.item_outcomes) | successful == {lead.id for lead in leads}

def test_u10_pipeline_ledger_keeps_research_reasons(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = FakeAIProvider()
    provider.calls = 0
    provider.budget = AIBudget(20, 100_000, 40)

    def resolve_research_cases_isolated(cases):
        provider.calls += 1
        provider.budget.consume("x", item_count=len(cases))
        if len(cases) > 4:
            return IsolatedResearchResult(
                batch=ResearchResolutionBatch(),
                truncated=True,
            )
        success = FakeAIProvider().resolve_research_cases(cases[:1])
        return IsolatedResearchResult(
            batch=success,
            invalid_ids=[cases[1].id],
            missing_ids=[cases[2].id],
        )

    provider.resolve_research_cases_isolated = resolve_research_cases_isolated

    def from_environment(*, budget, prompt_dir):
        provider.budget = budget
        return provider

    monkeypatch.setattr(
        "morning_radar.pipeline.DeepSeekProvider.from_environment",
        from_environment,
    )
    leads = [_practitioner(str(index)) for index in range(8)]
    checkpoint = save_checkpoint(project, leads, now=DAY_N, batch_id="batch-research")
    _seeded_ledger(project, checkpoint)
    pipeline = MorningRadarPipeline(project)
    pipeline.app.research_item_retry_attempts = 0
    pipeline.app.research_split_retry_attempts = 1
    pipeline.process(batch_id="batch-research", now=DAY_N, notify=False)
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    reasons = [ledger.get(item.id, content_version(item)).reason_code for item in leads]
    assert reasons.count(ReasonCode.RESEARCH_OUTPUT_INVALID) == 1
    assert reasons.count(ReasonCode.RESEARCH_CASE_MISSING) == 2
    assert reasons.count(ReasonCode.RESEARCH_DEFERRED) == 4
    deferred = [
        ledger.get(item.id, content_version(item))
        for item in leads
        if ledger.get(item.id, content_version(item)).reason_code is ReasonCode.RESEARCH_DEFERRED
    ]
    assert deferred
    assert all(entry.processing is ProcessingStatus.DEFERRED_BUDGET for entry in deferred)
    assert all(entry.processing is not ProcessingStatus.WAITING_EVIDENCE for entry in deferred)


def test_u11_processing_cap_explains_every_saved_item(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    install_fake_provider(monkeypatch)
    items = [
        official_item(f"cap{index}", published_at=DAY_N - timedelta(hours=index + 1))
        for index in range(3)
    ]
    checkpoint = save_checkpoint(project, items, now=DAY_N, batch_id="batch-cap")
    _seeded_ledger(project, checkpoint)
    pipeline = MorningRadarPipeline(project)
    pipeline.app.maximum_ai_items = 1
    pipeline.process(batch_id="batch-cap", now=DAY_N, notify=False)
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    entries = [ledger.get(item.id, content_version(item)) for item in items]
    assert all(entry is not None for entry in entries)
    statuses = {entry.processing for entry in entries}
    assert ProcessingStatus.COMPLETED in statuses
    assert ProcessingStatus.DEFERRED_BUDGET in statuses
    deferred_reasons = {
        entry.reason_code
        for entry in entries
        if entry.processing is ProcessingStatus.DEFERRED_BUDGET
    }
    assert deferred_reasons == {ReasonCode.DEFERRED_BUDGET}

def test_u12_empty_feed_is_consistent_and_304_needs_payload(tmp_path) -> None:
    empty_root = tmp_path / "empty"
    empty_state = empty_root / "data/state/rss.json"
    empty_state.parent.mkdir(parents=True, exist_ok=True)
    write_intake_checkpoint(
        empty_root,
        items=[],
        now=DAY_N,
        cutoff_at=DAY_N - timedelta(hours=30),
        collection=CollectionResult(items=[], raw_collected=0, after_buffer=0, after_dedup=0),
        source_state={
            "rss": {
                "openai_news": {
                    "etag": '"empty-v1"',
                    "status": "empty",
                    "item_ids": [],
                }
            }
        },
        batch_id="batch-empty-feed",
        run_id="run-empty-feed",
    )
    empty_state.write_text(json.dumps({"openai_news": {"etag": '"empty-v1"'}}), encoding="utf-8")
    assert "openai_news" not in inconsistent_cache_sources(
        empty_root, state_name="rss", state_path=empty_state
    )

    item = official_item("prior", published_at=DAY_N - timedelta(hours=2))
    prior_root = tmp_path / "prior"
    write_intake_checkpoint(
        prior_root,
        items=[item],
        now=DAY_N,
        cutoff_at=DAY_N - timedelta(hours=30),
        collection=CollectionResult(items=[item], raw_collected=1, after_buffer=1, after_dedup=1),
        source_state={
            "rss": {
                "openai_news": {
                    "etag": '"v-304"',
                    "status": "ok",
                    "item_ids": [item.id],
                }
            }
        },
        batch_id="batch-prior",
        run_id="run-prior",
    )
    write_intake_checkpoint(
        prior_root,
        items=[],
        now=DAY_N + timedelta(hours=1),
        cutoff_at=DAY_N - timedelta(hours=29),
        collection=CollectionResult(items=[], raw_collected=0, after_buffer=0, after_dedup=0),
        source_state={
            "rss": {
                "openai_news": {
                    "etag": '"v-304"',
                    "status": "not_modified",
                    "item_ids": [],
                }
            }
        },
        batch_id="batch-304",
        run_id="run-304",
    )
    state = prior_root / "data/state/rss.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"openai_news": {"etag": '"v-304"'}}), encoding="utf-8")
    assert "openai_news" not in inconsistent_cache_sources(
        prior_root, state_name="rss", state_path=state
    )

    missing_root = tmp_path / "missing304"
    write_intake_checkpoint(
        missing_root,
        items=[],
        now=DAY_N,
        cutoff_at=DAY_N - timedelta(hours=30),
        collection=CollectionResult(items=[], raw_collected=0, after_buffer=0, after_dedup=0),
        source_state={
            "rss": {
                "openai_news": {
                    "etag": '"orphan-304"',
                    "status": "not_modified",
                    "item_ids": [],
                }
            }
        },
        batch_id="batch-orphan-304",
        run_id="run-orphan-304",
    )
    orphan_state = missing_root / "data/state/rss.json"
    orphan_state.parent.mkdir(parents=True, exist_ok=True)
    orphan_state.write_text(
        json.dumps({"openai_news": {"etag": '"orphan-304"'}}), encoding="utf-8"
    )
    assert "openai_news" in inconsistent_cache_sources(
        missing_root, state_name="rss", state_path=orphan_state
    )

def test_u14_record_deploy_binds_hash_and_survives_notify_failure(
    tmp_path, monkeypatch
) -> None:
    project = copy_project(tmp_path)
    install_fake_provider(monkeypatch)
    first = official_item("deploy-a", published_at=DAY_N - timedelta(hours=3), title="Deploy A")
    checkpoint = save_checkpoint(project, [first], now=DAY_N, batch_id="batch-deploy-a")
    _seeded_ledger(project, checkpoint)
    brief_a = MorningRadarPipeline(project).process(
        batch_id="batch-deploy-a", now=DAY_N, notify=False
    )
    hash_a = _artifact_digest(project / "data/briefs" / f"{brief_a.date}.json")
    monkeypatch.chdir(project)
    assert cli_main(["record-deploy", "--date", str(brief_a.date), "--brief-hash", hash_a]) == 0
    first_record = PublishStore(project / "data/state/publish.json").get(str(brief_a.date))
    assert first_record is not None and first_record.deployed is True
    deployed_at = first_record.deployed_at
    assert cli_main(["record-deploy", "--date", str(brief_a.date), "--brief-hash", hash_a]) == 0
    repeated = PublishStore(project / "data/state/publish.json").get(str(brief_a.date))
    assert repeated is not None
    assert repeated.deployed_at == deployed_at
    try:
        cli_main(["record-deploy", "--date", str(brief_a.date), "--brief-hash", "deadbeefdeadbeef"])
    except SystemExit:
        pass
    else:
        raise AssertionError("mismatched hash must fail")
    second = official_item("deploy-b", published_at=DAY_N - timedelta(hours=2), title="Deploy B")
    checkpoint_b = save_checkpoint(
        project, [first, second], now=DAY_N + timedelta(minutes=5), batch_id="batch-deploy-b"
    )
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint_b, now=DAY_N)
    ledger.save()
    brief_b = MorningRadarPipeline(project).process(
        batch_id="batch-deploy-b", now=DAY_N, notify=False
    )
    hash_b = _artifact_digest(project / "data/briefs" / f"{brief_b.date}.json")
    assert hash_b != hash_a
    latest = PublishStore(project / "data/state/publish.json").get(str(brief_b.date))
    assert latest is not None
    assert latest.brief_hash == hash_b
    assert latest.deployed is False
    try:
        cli_main(["record-deploy", "--date", str(brief_b.date), "--brief-hash", hash_a])
    except SystemExit:
        pass
    else:
        raise AssertionError("confirming A must not mark B")
    assert cli_main(["record-deploy", "--date", str(brief_b.date), "--brief-hash", hash_b]) == 0

    def fail_notify(self, brief, *, force=False):
        raise RuntimeError("notify failed")

    monkeypatch.setattr("morning_radar.pipeline.WxPusherNotifier.notify", fail_notify)
    with pytest.raises(RuntimeError):
        MorningRadarPipeline(project).notify_latest()
    surviving = PublishStore(project / "data/state/publish.json").get(str(brief_b.date))
    assert surviving is not None
    assert surviving.deployed is True
    assert surviving.brief_hash == hash_b
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    confirmed = [
        entry
        for entry in ledger.ledger.entries.values()
        if entry.publish is PublishStatus.DEPLOY_CONFIRMED
    ]
    assert confirmed
    assert all(entry.brief_hash == hash_b for entry in confirmed)


def test_u15_new_workdir_reloads_saved_brief(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    install_fake_provider(monkeypatch)
    item = official_item("kept", published_at=DAY_N - timedelta(hours=2), title="Kept across dirs")
    checkpoint = save_checkpoint(project, [item], now=DAY_N, batch_id="batch-kept")
    _seeded_ledger(project, checkpoint)
    first = MorningRadarPipeline(project).process(batch_id="batch-kept", now=DAY_N, notify=False)
    clone = tmp_path / "clone"
    shutil.copytree(project, clone)
    second = MorningRadarPipeline(clone).process(batch_id="batch-kept", now=DAY_N, notify=False)
    assert second.date == first.date
    html = (clone / "site/index.html").read_text(encoding="utf-8")
    assert "Kept across dirs" in html
    samples = json.loads(
        Path("tests/fixtures/reliability/historical_samples.json").read_text(encoding="utf-8")
    )
    by_id = {sample["id"]: sample for sample in samples["samples"]}
    assert by_id["H01"]["actual_raw"] is None


def test_u16_fixtures_stay_isolated_from_production_and_post_daily() -> None:
    workflow = Path(".github/workflows/post-daily.yml").read_text(encoding="utf-8")
    daily = Path(".github/workflows/daily-brief.yml").read_text(encoding="utf-8")
    assert "display_title, '(fixtures)'" in workflow
    assert "display_title, '(dry-run)'" in workflow
    assert "!(inputs.dry_run || false) && !(inputs.fixtures || false)" in daily
    assert "record-deploy --date" in daily


def test_u16_fixture_collect_does_not_write_production_state(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    install_fake_provider(monkeypatch)
    pipeline = MorningRadarPipeline(project)
    pipeline.collect(fixtures=True, dry_run=True, now=DAY_N)
    assert (project / ".tmp/dry-run/data/intake/ledger.json").exists()
    assert not (project / "data/intake").exists()
    assert not (project / "data/state/publish.json").exists()

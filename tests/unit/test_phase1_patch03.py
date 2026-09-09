from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from morning_radar.ai import AIOutputError, FakeAIProvider
from morning_radar.cli import main as cli_main
from morning_radar.intake.generation import generation_is_complete
from morning_radar.intake.identity import content_version
from morning_radar.intake.inspect import inspect_intake
from morning_radar.intake.ledger import ProcessingLedgerStore
from morning_radar.intake.models import ProcessingStatus, ReasonCode
from morning_radar.models import Story
from morning_radar.pipeline import MorningRadarPipeline, _artifact_digest
from morning_radar.storage import load_models
from tests.unit.test_phase1_patch import (
    DAY_N,
    copy_project,
    official_item,
    save_checkpoint,
)


def _ledger(project) -> ProcessingLedgerStore:
    return ProcessingLedgerStore(project / "data/intake/ledger.json")


def _seed(project, checkpoint, now=DAY_N):
    ledger = _ledger(project)
    ledger.upsert_checkpoint(checkpoint, now=now)
    ledger.save()
    return ledger


def _version_item(suffix: str, *, title: str, excerpt: str, published_at, fetched_at=None):
    item = official_item(suffix, published_at=published_at, title=title)
    return item.model_copy(
        update={
            "summary": excerpt,
            "content_excerpt": excerpt,
            "fetched_at": fetched_at or published_at,
        }
    )


def _install_tracking_provider(monkeypatch, provider=None):
    provider = provider or FakeAIProvider()
    provider.classified_titles = []
    original = FakeAIProvider.classify_items

    def classify_items(self, items):
        provider.classified_titles.extend([item.title for item in items])
        return original(self, items)

    provider.classify_items = classify_items.__get__(provider, FakeAIProvider)

    def from_environment(*, budget, prompt_dir):
        provider.budget = budget
        return provider

    monkeypatch.setattr(
        "morning_radar.pipeline.DeepSeekProvider.from_environment",
        from_environment,
    )
    return provider


def _story_titles(project, brief_date) -> list[str]:
    path = project / "data/stories" / f"{brief_date}.json"
    if not path.exists():
        return []
    return [story.canonical_title for story in load_models(path, Story)]


def test_v01_two_pending_versions_only_latest_enters_model(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = _install_tracking_provider(monkeypatch)
    v1 = _version_item(
        "alpha",
        title="Alpha original",
        excerpt="old excerpt for alpha v1",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N - timedelta(hours=3),
    )
    checkpoint_v1 = save_checkpoint(project, [v1], now=DAY_N, batch_id="batch-v1")
    _seed(project, checkpoint_v1)
    v2 = _version_item(
        "alpha",
        title="Alpha revised",
        excerpt="updated excerpt for alpha v2",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N + timedelta(minutes=5),
    )
    assert content_version(v1) != content_version(v2)
    checkpoint_v2 = save_checkpoint(
        project, [v1, v2], now=DAY_N + timedelta(minutes=5), batch_id="batch-v2"
    )
    _seed(project, checkpoint_v2, now=DAY_N + timedelta(minutes=5))
    brief = MorningRadarPipeline(project).process(batch_id="batch-v2", now=DAY_N, notify=False)
    assert "Alpha revised" in provider.classified_titles
    assert "Alpha original" not in provider.classified_titles
    ledger = _ledger(project)
    e1 = ledger.get(v1.id, content_version(v1))
    e2 = ledger.get(v2.id, content_version(v2))
    assert e2 is not None and e2.processing is ProcessingStatus.COMPLETED
    assert e1 is not None
    assert e1.processing is not ProcessingStatus.COMPLETED
    assert e1.reason_code is ReasonCode.SUPERSEDED
    assert any("revised" in title.lower() for title in _story_titles(project, brief.date))


def test_v02_selected_version_failure_stays_on_that_version(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = FakeAIProvider()
    provider.merge_calls = 0

    def merge_story(items):
        provider.merge_calls += 1
        raise AIOutputError("merge failed")

    provider.merge_story = merge_story
    _install_tracking_provider(monkeypatch, provider)
    v1 = _version_item(
        "alpha",
        title="Alpha original",
        excerpt="old excerpt for alpha v1",
        published_at=DAY_N - timedelta(hours=3),
    )
    v2 = _version_item(
        "alpha",
        title="Alpha revised",
        excerpt="updated excerpt for alpha v2",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N + timedelta(minutes=5),
    )
    _seed(project, save_checkpoint(project, [v1], now=DAY_N, batch_id="batch-v1"))
    _seed(
        project,
        save_checkpoint(project, [v1, v2], now=DAY_N + timedelta(minutes=5), batch_id="batch-v2"),
        now=DAY_N + timedelta(minutes=5),
    )
    MorningRadarPipeline(project).process(batch_id="batch-v2", now=DAY_N, notify=False)
    ledger = _ledger(project)
    e1 = ledger.get(v1.id, content_version(v1))
    e2 = ledger.get(v2.id, content_version(v2))
    assert e2 is not None
    assert e2.processing is ProcessingStatus.FAILED_RETRY
    assert e2.reason_code is ReasonCode.MERGE_FAILED
    assert e1 is not None
    assert e1.processing is not ProcessingStatus.COMPLETED
    assert e1.reason_code is not ReasonCode.MERGE_FAILED


def test_v03_completed_v1_then_only_v2_is_computed(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = _install_tracking_provider(monkeypatch)
    v1 = _version_item(
        "alpha",
        title="Alpha original",
        excerpt="old excerpt for alpha v1",
        published_at=DAY_N - timedelta(hours=3),
    )
    _seed(project, save_checkpoint(project, [v1], now=DAY_N, batch_id="batch-v1"))
    MorningRadarPipeline(project).process(batch_id="batch-v1", now=DAY_N, notify=False)
    first_titles = list(provider.classified_titles)
    v2 = _version_item(
        "alpha",
        title="Alpha revised",
        excerpt="updated excerpt for alpha v2",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N + timedelta(minutes=5),
    )
    _seed(
        project,
        save_checkpoint(project, [v2], now=DAY_N + timedelta(minutes=5), batch_id="batch-v2"),
        now=DAY_N + timedelta(minutes=5),
    )
    MorningRadarPipeline(project).process(batch_id="batch-v2", now=DAY_N, notify=False)
    new_titles = provider.classified_titles[len(first_titles) :]
    assert new_titles == ["Alpha revised"]
    ledger = _ledger(project)
    assert ledger.get(v1.id, content_version(v1)).processing is ProcessingStatus.COMPLETED
    assert ledger.get(v2.id, content_version(v2)).processing is ProcessingStatus.COMPLETED
    same = save_checkpoint(
        project, [v2], now=DAY_N + timedelta(minutes=10), batch_id="batch-v2-again"
    )
    _seed(project, same, now=DAY_N + timedelta(minutes=10))
    MorningRadarPipeline(project).process(batch_id="batch-v2-again", now=DAY_N, notify=False)
    assert provider.classified_titles[len(first_titles) :] == ["Alpha revised"]


def test_v05_failed_new_version_keeps_old_story(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = FakeAIProvider()
    provider.merge_calls = 0

    def merge_story(items):
        provider.merge_calls += 1
        if any("revised" in item.title for item in items):
            raise AIOutputError("merge failed")
        return FakeAIProvider.merge_story(provider, items)

    provider.merge_story = merge_story
    _install_tracking_provider(monkeypatch, provider)
    v1 = _version_item(
        "alpha",
        title="Alpha original",
        excerpt="old excerpt for alpha v1",
        published_at=DAY_N - timedelta(hours=3),
    )
    _seed(project, save_checkpoint(project, [v1], now=DAY_N, batch_id="batch-v1"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-v1", now=DAY_N, notify=False)
    assert any("original" in title.lower() for title in _story_titles(project, brief.date))
    v2 = _version_item(
        "alpha",
        title="Alpha revised",
        excerpt="updated excerpt for alpha v2",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N + timedelta(minutes=5),
    )
    _seed(
        project,
        save_checkpoint(project, [v2], now=DAY_N + timedelta(minutes=5), batch_id="batch-v2"),
        now=DAY_N + timedelta(minutes=5),
    )
    MorningRadarPipeline(project).process(batch_id="batch-v2", now=DAY_N, notify=False)
    titles = _story_titles(project, brief.date)
    assert any("original" in title.lower() for title in titles)
    assert not any("revised" in title.lower() for title in titles)
    html = (project / "site/index.html").read_text(encoding="utf-8")
    assert "Alpha original" in html
    ledger = _ledger(project)
    assert ledger.get(v1.id, content_version(v1)).processing is ProcessingStatus.COMPLETED
    e2 = ledger.get(v2.id, content_version(v2))
    assert e2.processing is ProcessingStatus.FAILED_RETRY
    assert e2.reason_code is ReasonCode.MERGE_FAILED
    assert e2.publish.value != "deploy_confirmed"


def test_v06_score_failure_keeps_old_story(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = FakeAIProvider()

    def score_story(story):
        if "revised" in story.canonical_title:
            raise AIOutputError("score failed")
        return FakeAIProvider.score_story(provider, story)

    provider.score_story = score_story
    _install_tracking_provider(monkeypatch, provider)
    v1 = _version_item(
        "alpha",
        title="Alpha original",
        excerpt="old excerpt for alpha v1",
        published_at=DAY_N - timedelta(hours=3),
    )
    _seed(project, save_checkpoint(project, [v1], now=DAY_N, batch_id="batch-v1"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-v1", now=DAY_N, notify=False)
    v2 = _version_item(
        "alpha",
        title="Alpha revised",
        excerpt="updated excerpt for alpha v2",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N + timedelta(minutes=5),
    )
    _seed(
        project,
        save_checkpoint(project, [v2], now=DAY_N + timedelta(minutes=5), batch_id="batch-v2"),
        now=DAY_N + timedelta(minutes=5),
    )
    MorningRadarPipeline(project).process(batch_id="batch-v2", now=DAY_N, notify=False)
    assert any("original" in title.lower() for title in _story_titles(project, brief.date))
    e2 = _ledger(project).get(v2.id, content_version(v2))
    assert e2.reason_code is ReasonCode.SCORE_FAILED


def test_v07_retry_success_replaces_without_duplicating(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = FakeAIProvider()
    provider.merge_calls = 0

    def merge_story(items):
        provider.merge_calls += 1
        if any("revised" in item.title for item in items) and provider.merge_calls < 3:
            raise AIOutputError("merge failed")
        return FakeAIProvider.merge_story(provider, items)

    provider.merge_story = merge_story
    _install_tracking_provider(monkeypatch, provider)
    v1 = _version_item(
        "alpha",
        title="Alpha original",
        excerpt="old excerpt for alpha v1",
        published_at=DAY_N - timedelta(hours=3),
    )
    other = official_item("other", published_at=DAY_N - timedelta(hours=2), title="Unrelated kept")
    _seed(project, save_checkpoint(project, [v1, other], now=DAY_N, batch_id="batch-v1"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-v1", now=DAY_N, notify=False)
    v2 = _version_item(
        "alpha",
        title="Alpha revised",
        excerpt="updated excerpt for alpha v2",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N + timedelta(minutes=5),
    )
    _seed(
        project,
        save_checkpoint(project, [v2], now=DAY_N + timedelta(minutes=5), batch_id="batch-v2"),
        now=DAY_N + timedelta(minutes=5),
    )
    MorningRadarPipeline(project).process(batch_id="batch-v2", now=DAY_N, notify=False)
    MorningRadarPipeline(project).process(batch_id="batch-v2", now=DAY_N, notify=False)
    titles = _story_titles(project, brief.date)
    assert any("revised" in title.lower() for title in titles)
    assert not any("original" in title.lower() for title in titles)
    assert any("Unrelated" in title for title in titles)
    ledger = _ledger(project)
    assert ledger.get(v1.id, content_version(v1)).processing is ProcessingStatus.COMPLETED
    assert ledger.get(v2.id, content_version(v2)).processing is ProcessingStatus.COMPLETED
    payload = inspect_intake(project, input_id=v1.id)
    assert payload["found"] is True


def test_v08_partial_member_failure_does_not_drop_merged_story(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = FakeAIProvider()

    def merge_story(items):
        if any("revised" in item.title for item in items):
            raise AIOutputError("merge failed")
        return FakeAIProvider.merge_story(provider, items)

    provider.merge_story = merge_story
    _install_tracking_provider(monkeypatch, provider)
    left = official_item(
        "member-a",
        published_at=DAY_N - timedelta(hours=3),
        title="OpenAI launches the Widget Platform",
    )
    right = official_item(
        "member-c",
        published_at=DAY_N - timedelta(hours=3),
        title="OpenAI launches the Widget Platform",
    )
    _seed(project, save_checkpoint(project, [left, right], now=DAY_N, batch_id="batch-merged"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-merged", now=DAY_N, notify=False)
    stories = load_models(project / "data/stories" / f"{brief.date}.json", Story)
    assert stories
    updated = left.model_copy(
        update={
            "title": "OpenAI launches the Widget Platform revised",
            "summary": "updated member excerpt",
            "content_excerpt": "updated member excerpt",
            "fetched_at": DAY_N + timedelta(minutes=5),
        }
    )
    _seed(
        project,
        save_checkpoint(
            project, [updated], now=DAY_N + timedelta(minutes=5), batch_id="batch-member"
        ),
        now=DAY_N + timedelta(minutes=5),
    )
    MorningRadarPipeline(project).process(batch_id="batch-member", now=DAY_N, notify=False)
    after = load_models(project / "data/stories" / f"{brief.date}.json", Story)
    assert after
    assert any(left.id in story.source_item_ids for story in after)


def test_v09_saved_item_is_recoverable_after_48h(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = _install_tracking_provider(monkeypatch)
    item = official_item("late", published_at=DAY_N - timedelta(hours=3), title="Late but saved")
    checkpoint = save_checkpoint(project, [item], now=DAY_N, batch_id="batch-saved")
    _seed(project, checkpoint)
    later = DAY_N + timedelta(hours=48)
    brief = MorningRadarPipeline(project).process(batch_id="batch-saved", now=later, notify=False)
    entry = _ledger(project).get(item.id, content_version(item))
    assert entry.processing is ProcessingStatus.COMPLETED
    assert entry.reason_code is not ReasonCode.EXCLUDED_STALE
    assert "Late but saved" in provider.classified_titles
    assert _story_titles(project, brief.date)


def test_v10_recovery_entrances_match_v09(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    item = official_item("late", published_at=DAY_N - timedelta(hours=3), title="Late but saved")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-saved"))
    later = DAY_N + timedelta(hours=48)
    empty = save_checkpoint(project, [], now=later, batch_id="batch-empty")
    _seed(project, empty, now=later)
    brief = MorningRadarPipeline(project).process(batch_id="batch-empty", now=later, notify=False)
    entry = _ledger(project).get(item.id, content_version(item))
    assert entry.processing is ProcessingStatus.COMPLETED
    assert _story_titles(project, brief.date)


def test_v11_initially_stale_stays_excluded(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    item = official_item("stale", published_at=DAY_N - timedelta(hours=31), title="Already stale")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-stale"))
    MorningRadarPipeline(project).process(batch_id="batch-stale", now=DAY_N, notify=False)
    entry = _ledger(project).get(item.id, content_version(item))
    assert entry.processing is ProcessingStatus.EXCLUDED
    assert entry.reason_code is ReasonCode.EXCLUDED_STALE
    later = DAY_N + timedelta(hours=48)
    empty = save_checkpoint(project, [], now=later, batch_id="batch-empty-stale")
    _seed(project, empty, now=later)
    MorningRadarPipeline(project).process(batch_id="batch-empty-stale", now=later, notify=False)
    entry = _ledger(project).get(item.id, content_version(item))
    assert entry.reason_code is ReasonCode.EXCLUDED_STALE


def test_v12_aged_unresolved_is_not_auto_finished(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    item = official_item("aged", published_at=DAY_N - timedelta(hours=3), title="Aged unfinished")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-aged"))
    later = DAY_N + timedelta(days=8)
    MorningRadarPipeline(project).process(batch_id="batch-aged", now=later, notify=False)
    entry = _ledger(project).get(item.id, content_version(item))
    assert entry.processing not in {ProcessingStatus.COMPLETED, ProcessingStatus.EXCLUDED}
    assert entry.outcome == "aged_unresolved"



def test_v04_deploy_does_not_mark_unprocessed_version(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    v1 = _version_item(
        "alpha",
        title="Alpha original",
        excerpt="old excerpt for alpha v1",
        published_at=DAY_N - timedelta(hours=3),
    )
    _seed(project, save_checkpoint(project, [v1], now=DAY_N, batch_id="batch-v1"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-v1", now=DAY_N, notify=False)
    digest = _artifact_digest(project / "data/briefs" / f"{brief.date}.json")
    monkeypatch.chdir(project)
    assert cli_main(["record-deploy", "--date", str(brief.date), "--brief-hash", digest]) == 0
    v2 = _version_item(
        "alpha",
        title="Alpha revised",
        excerpt="updated excerpt for alpha v2",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N + timedelta(minutes=5),
    )
    _seed(
        project,
        save_checkpoint(project, [v2], now=DAY_N + timedelta(minutes=5), batch_id="batch-v2"),
        now=DAY_N + timedelta(minutes=5),
    )
    provider = FakeAIProvider()

    def merge_story(items):
        if any("revised" in item.title for item in items):
            raise AIOutputError("merge failed")
        return FakeAIProvider.merge_story(provider, items)

    provider.merge_story = merge_story
    _install_tracking_provider(monkeypatch, provider)
    MorningRadarPipeline(project).process(batch_id="batch-v2", now=DAY_N, notify=False)
    e2 = _ledger(project).get(v2.id, content_version(v2))
    assert e2.publish.value != "deploy_confirmed"
    assert e2.processing is ProcessingStatus.FAILED_RETRY


def test_v13_partial_save_is_not_a_complete_generation(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    first = official_item("alpha", published_at=DAY_N - timedelta(hours=3), title="Alpha kept")
    _seed(project, save_checkpoint(project, [first], now=DAY_N, batch_id="batch-a"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-a", now=DAY_N, notify=False)
    second = official_item("beta", published_at=DAY_N - timedelta(hours=2), title="Beta added")
    _seed(
        project,
        save_checkpoint(
            project, [first, second], now=DAY_N + timedelta(minutes=5), batch_id="batch-ab"
        ),
        now=DAY_N + timedelta(minutes=5),
    )
    original = __import__("morning_radar.storage", fromlist=["save_model"]).save_model

    def fail_brief(path, model):
        if "briefs" in Path(path).parts:
            raise OSError("brief write failed")
        return original(path, model)

    monkeypatch.setattr("morning_radar.storage.save_model", fail_brief)
    with pytest.raises(OSError):
        MorningRadarPipeline(project).process(batch_id="batch-ab", now=DAY_N, notify=False)
    assert generation_is_complete(project, str(brief.date)) is False
    monkeypatch.undo()
    _install_tracking_provider(monkeypatch)
    recovered = MorningRadarPipeline(project).process(batch_id="batch-ab", now=DAY_N, notify=False)
    titles = _story_titles(project, recovered.date)
    assert any("Alpha" in title for title in titles)
    assert any("Beta" in title for title in titles)
    assert generation_is_complete(project, str(recovered.date)) is True


def test_v14_complete_files_then_missing_ledger_are_healed(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = _install_tracking_provider(monkeypatch)
    item = official_item("heal", published_at=DAY_N - timedelta(hours=2), title="Heal ledger")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-heal"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-heal", now=DAY_N, notify=False)
    ledger = _ledger(project)
    ledger.update(
        item.id,
        content_version(item),
        now=DAY_N,
        processing=ProcessingStatus.IN_PROGRESS,
        outcome="in_progress",
    )
    ledger.save()
    calls_before = len(provider.classified_titles)
    again = MorningRadarPipeline(project).process(batch_id="batch-heal", now=DAY_N, notify=False)
    assert again.date == brief.date
    restored = _ledger(project).get(item.id, content_version(item))
    assert restored.processing is ProcessingStatus.COMPLETED
    assert len(provider.classified_titles) == calls_before


def test_v15_new_directory_restores_from_prepared_without_model(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    first = official_item("alpha", published_at=DAY_N - timedelta(hours=3), title="Alpha kept")
    _seed(project, save_checkpoint(project, [first], now=DAY_N, batch_id="batch-a"))
    MorningRadarPipeline(project).process(batch_id="batch-a", now=DAY_N, notify=False)
    second = official_item("beta", published_at=DAY_N - timedelta(hours=2), title="Beta added")
    _seed(
        project,
        save_checkpoint(
            project, [first, second], now=DAY_N + timedelta(minutes=5), batch_id="batch-ab"
        ),
        now=DAY_N + timedelta(minutes=5),
    )
    original = __import__("morning_radar.storage", fromlist=["save_model"]).save_model

    def fail_brief(path, model):
        if "briefs" in Path(path).parts:
            raise OSError("brief write failed")
        return original(path, model)

    monkeypatch.setattr("morning_radar.storage.save_model", fail_brief)
    with pytest.raises(OSError):
        MorningRadarPipeline(project).process(batch_id="batch-ab", now=DAY_N, notify=False)
    clone = tmp_path / "clone"
    shutil.copytree(project, clone)
    monkeypatch.undo()
    _install_tracking_provider(monkeypatch)
    recovered = MorningRadarPipeline(clone).process(batch_id="batch-ab", now=DAY_N, notify=False)
    titles = _story_titles(clone, recovered.date)
    assert any("Alpha" in title for title in titles)
    assert any("Beta" in title for title in titles)


def test_v16_incomplete_generation_rejects_record_deploy(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    item = official_item("deploy", published_at=DAY_N - timedelta(hours=2), title="Deploy item")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-deploy"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-deploy", now=DAY_N, notify=False)
    digest = _artifact_digest(project / "data/briefs" / f"{brief.date}.json")
    (project / "data/state/generation.json").unlink()
    monkeypatch.chdir(project)
    with pytest.raises(SystemExit):
        cli_main(["record-deploy", "--date", str(brief.date), "--brief-hash", digest])
    from morning_radar.intake.generation import save_generation_commit

    save_generation_commit(
        project,
        brief_date=str(brief.date),
        brief_hash=digest,
        stories_digest=_artifact_digest(project / "data/stories" / f"{brief.date}.json"),
    )
    assert cli_main(["record-deploy", "--date", str(brief.date), "--brief-hash", digest]) == 0

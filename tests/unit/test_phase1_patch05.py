from __future__ import annotations

from datetime import timedelta

import pytest

from morning_radar.cli import main as cli_main
from morning_radar.intake.generation import GenerationControlError
from morning_radar.intake.identity import content_version
from morning_radar.intake.models import ProcessingStatus, PublishStatus, ReasonCode
from morning_radar.intake.publish import PublishStore
from morning_radar.models import DailyBrief
from morning_radar.pipeline import MorningRadarPipeline, _artifact_digest
from morning_radar.storage import load_model
from tests.unit.test_phase1_patch import DAY_N, copy_project, official_item, save_checkpoint
from tests.unit.test_phase1_patch03 import (
    _install_tracking_provider,
    _ledger,
    _seed,
    _story_titles,
    _version_item,
)


def test_t01_interrupt_does_not_reprocess_superseded_version(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = _install_tracking_provider(monkeypatch)
    v1 = _version_item(
        "alpha",
        title="Alpha original",
        excerpt="old excerpt for alpha v1",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N - timedelta(hours=3),
    )
    other = official_item(
        "other", published_at=DAY_N - timedelta(hours=2), title="Unrelated kept"
    )
    _seed(project, save_checkpoint(project, [v1], now=DAY_N, batch_id="batch-v1"))
    v2 = _version_item(
        "alpha",
        title="Alpha revised",
        excerpt="updated excerpt for alpha v2",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N + timedelta(minutes=5),
    )
    _seed(
        project,
        save_checkpoint(
            project, [v1, v2, other], now=DAY_N + timedelta(minutes=5), batch_id="batch-v2"
        ),
        now=DAY_N + timedelta(minutes=5),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("status confirmation interrupted")

    monkeypatch.setattr("morning_radar.pipeline._mark_brief_generated", boom)
    with pytest.raises(RuntimeError):
        MorningRadarPipeline(project).process(batch_id="batch-v2", now=DAY_N, notify=False)
    assert "Alpha revised" in provider.classified_titles
    assert "Alpha original" not in provider.classified_titles
    monkeypatch.undo()
    recovered_provider = _install_tracking_provider(monkeypatch)
    MorningRadarPipeline(project).process(batch_id="batch-v2", now=DAY_N, notify=False)
    assert recovered_provider.classified_titles == []
    titles = _story_titles(project, "2026-09-07")
    assert any("revised" in title.lower() for title in titles)
    assert not any("original" in title.lower() for title in titles)
    assert any("Unrelated" in title for title in titles)
    ledger = _ledger(project)
    e1 = ledger.get(v1.id, content_version(v1))
    e2 = ledger.get(v2.id, content_version(v2))
    assert e1.processing is ProcessingStatus.EXCLUDED
    assert e1.reason_code is ReasonCode.SUPERSEDED
    assert e1.superseded_by == content_version(v2)
    assert e2.processing is ProcessingStatus.COMPLETED
    MorningRadarPipeline(project).process(batch_id="batch-v2", now=DAY_N, notify=False)
    assert recovered_provider.classified_titles == []
    assert _ledger(project).get(v1.id, content_version(v1)).reason_code is ReasonCode.SUPERSEDED


def test_t02_recovered_ledger_can_be_deploy_confirmed(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    item = official_item("shown", published_at=DAY_N - timedelta(hours=2), title="Shown item")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-shown"))

    def boom(*args, **kwargs):
        raise RuntimeError("status confirmation interrupted")

    monkeypatch.setattr("morning_radar.pipeline._mark_brief_generated", boom)
    with pytest.raises(RuntimeError):
        MorningRadarPipeline(project).process(batch_id="batch-shown", now=DAY_N, notify=False)
    monkeypatch.undo()
    recovered_provider = _install_tracking_provider(monkeypatch)
    brief = MorningRadarPipeline(project).process(batch_id="batch-shown", now=DAY_N, notify=False)
    assert recovered_provider.classified_titles == []
    digest = _artifact_digest(project / "data/briefs" / f"{brief.date}.json")
    monkeypatch.chdir(project)
    assert cli_main(["record-deploy", "--date", str(brief.date), "--brief-hash", digest]) == 0
    record = PublishStore(project / "data/state/publish.json").get(str(brief.date))
    assert record is not None
    assert record.deployed is True
    assert record.brief_hash == digest
    entry = _ledger(project).get(item.id, content_version(item))
    assert entry.processing is ProcessingStatus.COMPLETED
    assert entry.brief_hash == digest
    assert entry.brief_date == str(brief.date)
    assert entry.publish is PublishStatus.DEPLOY_CONFIRMED
    assert entry.deployed_at is not None
    deployed_at = entry.deployed_at
    MorningRadarPipeline(project).process(batch_id="batch-shown", now=DAY_N, notify=False)
    assert cli_main(["record-deploy", "--date", str(brief.date), "--brief-hash", digest]) == 0
    again = _ledger(project).get(item.id, content_version(item))
    assert again.publish is PublishStatus.DEPLOY_CONFIRMED
    assert again.deployed_at == deployed_at
    assert again.brief_hash == digest


def test_t03_notify_latest_sends_healed_brief(tmp_path, monkeypatch) -> None:
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
    original = __import__("morning_radar.storage", fromlist=["save_models"]).save_models

    def block_new_public(path, models):
        from morning_radar.intake import generation as gen

        prepared = gen.load_prepared_generation(project)
        commit = gen.load_generation_commit(project)
        if (
            prepared is not None
            and commit is not None
            and prepared.get("generation_id") != commit.get("generation_id")
        ):
            raise OSError("public write blocked")
        return original(path, models)

    monkeypatch.setattr("morning_radar.storage.save_models", block_new_public)
    with pytest.raises(OSError):
        MorningRadarPipeline(project).process(batch_id="batch-ab", now=DAY_N, notify=False)
    captured: list[DailyBrief] = []

    def fake_notify(self, brief, *, force=False):
        captured.append(brief)
        assert force is False
        return True

    monkeypatch.undo()
    monkeypatch.setattr("morning_radar.pipeline.WxPusherNotifier.notify", fake_notify)
    MorningRadarPipeline(project).notify_latest()
    assert captured
    disk = load_model(project / "data/briefs" / f"{captured[0].date}.json", DailyBrief)
    assert captured[0].model_dump(mode="json") == disk.model_dump(mode="json")
    titles = [
        item.title
        for item in disk.top_stories + disk.ai_and_open_source + disk.other_reading
    ]
    assert any("Beta" in title for title in titles)
    (project / "data/state/generation.json").write_text("{not-json", encoding="utf-8")
    captured.clear()
    with pytest.raises(GenerationControlError):
        MorningRadarPipeline(project).notify_latest()
    assert captured == []

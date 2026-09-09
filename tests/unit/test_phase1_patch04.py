from __future__ import annotations

from datetime import timedelta

import pytest

from morning_radar.cli import main as cli_main
from morning_radar.intake import generation as gen
from morning_radar.intake.identity import content_version
from morning_radar.intake.models import ProcessingStatus, ReasonCode
from morning_radar.intake.publish import PublishStore
from morning_radar.pipeline import MorningRadarPipeline, _artifact_digest
from tests.unit.test_phase1_patch import DAY_N, copy_project, official_item, save_checkpoint
from tests.unit.test_phase1_patch03 import (
    _install_tracking_provider,
    _ledger,
    _seed,
    _story_titles,
    _version_item,
)


def test_w01_pending_prepared_does_not_complete_against_old_commit(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    first = official_item("alpha", published_at=DAY_N - timedelta(hours=3), title="Alpha kept")
    _seed(project, save_checkpoint(project, [first], now=DAY_N, batch_id="batch-a"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-a", now=DAY_N, notify=False)
    assert generation_complete_for(project, str(brief.date))
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
        prepared = gen.load_prepared_generation(project)
        commit = gen.load_generation_commit(project)
        if (
            prepared is not None
            and commit is not None
            and prepared.get("generation_id")
            and prepared.get("generation_id") != commit.get("generation_id")
        ):
            raise OSError("public write blocked")
        return original(path, models)

    monkeypatch.setattr("morning_radar.storage.save_models", block_new_public)
    with pytest.raises(OSError):
        MorningRadarPipeline(project).process(batch_id="batch-ab", now=DAY_N, notify=False)
    assert "Beta added" not in " ".join(_story_titles(project, brief.date))
    e_beta = _ledger(project).get(second.id, content_version(second))
    assert e_beta is None or e_beta.processing is not ProcessingStatus.COMPLETED
    monkeypatch.undo()
    later = _install_tracking_provider(monkeypatch)
    recovered = MorningRadarPipeline(project).process(batch_id="batch-ab", now=DAY_N, notify=False)
    titles = _story_titles(project, recovered.date)
    assert any("Alpha" in title for title in titles)
    assert any("Beta" in title for title in titles)
    assert later.classified_titles == []
    assert _ledger(project).get(second.id, content_version(second)).processing is (
        ProcessingStatus.COMPLETED
    )


def generation_complete_for(project, brief_date: str) -> bool:
    return gen.generation_is_complete(project, brief_date)


def test_w03_redeployed_state_survives_repeat_process(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    item = official_item("once", published_at=DAY_N - timedelta(hours=2), title="Once")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-once"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-once", now=DAY_N, notify=False)
    digest = _artifact_digest(project / "data/briefs" / f"{brief.date}.json")
    monkeypatch.chdir(project)
    assert cli_main(["record-deploy", "--date", str(brief.date), "--brief-hash", digest]) == 0
    first = PublishStore(project / "data/state/publish.json").get(str(brief.date))
    assert first is not None and first.deployed is True
    deployed_at = first.deployed_at
    MorningRadarPipeline(project).process(batch_id="batch-once", now=DAY_N, notify=False)
    again = PublishStore(project / "data/state/publish.json").get(str(brief.date))
    assert again is not None
    assert again.deployed is True
    assert again.brief_hash == digest
    assert again.deployed_at == deployed_at


def test_w04_heal_completes_publish_without_model(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = _install_tracking_provider(monkeypatch)
    item = official_item("heal", published_at=DAY_N - timedelta(hours=2), title="Heal publish")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-heal"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-heal", now=DAY_N, notify=False)
    digest = _artifact_digest(project / "data/briefs" / f"{brief.date}.json")
    (project / "data/state/publish.json").unlink()
    ledger = _ledger(project)
    ledger.update(
        item.id,
        content_version(item),
        now=DAY_N,
        processing=ProcessingStatus.IN_PROGRESS,
        outcome="in_progress",
    )
    ledger.save()
    calls = list(provider.classified_titles)
    MorningRadarPipeline(project).process(batch_id="batch-heal", now=DAY_N, notify=False)
    assert provider.classified_titles == calls
    record = PublishStore(project / "data/state/publish.json").get(str(brief.date))
    assert record is not None
    assert record.brief_hash == digest
    assert record.artifact_path == f"data/briefs/{brief.date}.json"
    monkeypatch.chdir(project)
    assert cli_main(["record-deploy", "--date", str(brief.date), "--brief-hash", digest]) == 0


def test_w05_restore_does_not_drop_unrelated_ledger_rows(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    item = official_item("keep", published_at=DAY_N - timedelta(hours=2), title="Keep me")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-keep"))
    MorningRadarPipeline(project).process(batch_id="batch-keep", now=DAY_N, notify=False)
    extra = official_item("extra", published_at=DAY_N - timedelta(hours=1), title="Later extra")
    _seed(
        project,
        save_checkpoint(project, [extra], now=DAY_N + timedelta(minutes=5), batch_id="batch-extra"),
        now=DAY_N + timedelta(minutes=5),
    )
    gen.heal_incomplete_generation(project)
    assert _ledger(project).get(extra.id, content_version(extra)) is not None


def test_w07_recaptured_legal_version_stays_recoverable(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = _install_tracking_provider(monkeypatch)
    item = official_item("late", published_at=DAY_N - timedelta(hours=3), title="Late but legal")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-t"))
    later = DAY_N + timedelta(hours=48)
    recapture = save_checkpoint(project, [item], now=later, batch_id="batch-recapture")
    _seed(project, recapture, now=later)
    brief = MorningRadarPipeline(project).process(
        batch_id="batch-recapture", now=later, notify=False
    )
    entry = _ledger(project).get(item.id, content_version(item))
    assert entry.processing is ProcessingStatus.COMPLETED
    assert entry.reason_code is not ReasonCode.EXCLUDED_STALE
    assert "Late but legal" in provider.classified_titles
    assert _story_titles(project, brief.date)


def test_w08_never_processed_stale_stays_excluded_on_empty_batch(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    item = official_item("stale", published_at=DAY_N - timedelta(hours=31), title="Already stale")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-stale"))
    later = DAY_N + timedelta(hours=1)
    empty = save_checkpoint(project, [], now=later, batch_id="batch-empty")
    _seed(project, empty, now=later)
    MorningRadarPipeline(project).process(batch_id="batch-empty", now=later, notify=False)
    entry = _ledger(project).get(item.id, content_version(item))
    assert entry is not None
    assert entry.processing is not ProcessingStatus.COMPLETED
    assert entry.reason_code is ReasonCode.EXCLUDED_STALE
    titles = _story_titles(project, str(brief_date(later)))
    assert not any("Already stale" in title for title in titles)


def brief_date(now):
    from morning_radar.time_utils import display_date

    return display_date(now)


def test_w10_latest_of_ten_versions_is_processed(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    provider = _install_tracking_provider(monkeypatch)
    versions = []
    for index in range(10):
        item = _version_item(
            "many",
            title=f"Version {index:02d} unique",
            excerpt=f"excerpt version {index}",
            published_at=DAY_N - timedelta(hours=3),
            fetched_at=DAY_N + timedelta(minutes=index),
        )
        versions.append(item)
        _seed(
            project,
            save_checkpoint(
                project, [item], now=DAY_N + timedelta(minutes=index), batch_id=f"batch-v{index}"
            ),
            now=DAY_N + timedelta(minutes=index),
        )
    empty = save_checkpoint(project, [], now=DAY_N + timedelta(hours=1), batch_id="batch-empty")
    _seed(project, empty, now=DAY_N + timedelta(hours=1))
    MorningRadarPipeline(project).process(
        batch_id="batch-empty", now=DAY_N + timedelta(hours=1), notify=False
    )
    assert provider.classified_titles[-1:] == ["Version 09 unique"]
    ledger = _ledger(project)
    latest = ledger.get(versions[-1].id, content_version(versions[-1]))
    assert latest.processing is ProcessingStatus.COMPLETED
    assert latest.reason_code is not ReasonCode.SUPERSEDED
    older = ledger.get(versions[0].id, content_version(versions[0]))
    assert older.reason_code is ReasonCode.SUPERSEDED


def test_w13_missing_story_file_is_incomplete(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    item = official_item("story", published_at=DAY_N - timedelta(hours=2), title="Needs story")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-story"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-story", now=DAY_N, notify=False)
    (project / "data/stories" / f"{brief.date}.json").unlink()
    assert gen.generation_is_complete(project, str(brief.date)) is False
    monkeypatch.chdir(project)
    digest = _artifact_digest(project / "data/briefs" / f"{brief.date}.json")
    with pytest.raises(SystemExit):
        cli_main(["record-deploy", "--date", str(brief.date), "--brief-hash", digest])


def test_w14_corrupt_generation_control_is_not_legacy(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    item = official_item("ctrl", published_at=DAY_N - timedelta(hours=2), title="Control")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-ctrl"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-ctrl", now=DAY_N, notify=False)
    (project / "data/state/generation.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(gen.GenerationControlError):
        gen.generation_is_complete(project, str(brief.date))


def test_w15_notify_and_build_site_heal_or_block(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    item = official_item("site", published_at=DAY_N - timedelta(hours=2), title="Site item")
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-site"))
    brief = MorningRadarPipeline(project).process(batch_id="batch-site", now=DAY_N, notify=False)
    (project / "data/stories" / f"{brief.date}.json").unlink()
    notified = {"n": 0}

    def fake_notify(self, payload, *, force=False):
        notified["n"] += 1
        return True

    monkeypatch.setattr("morning_radar.pipeline.WxPusherNotifier.notify", fake_notify)
    pipeline = MorningRadarPipeline(project)
    pipeline.notify_latest()
    assert notified["n"] == 1
    assert (project / "data/stories" / f"{brief.date}.json").exists()
    (project / "data/state/generation.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(gen.GenerationControlError):
        pipeline.build_site()


def test_w16_copied_prepared_restores_without_model(tmp_path, monkeypatch) -> None:
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
    import shutil

    clone = tmp_path / "clone"
    shutil.copytree(project, clone)
    monkeypatch.undo()
    clone_provider = _install_tracking_provider(monkeypatch)
    recovered = MorningRadarPipeline(clone).process(batch_id="batch-ab", now=DAY_N, notify=False)
    titles = _story_titles(clone, recovered.date)
    assert any("Alpha" in title for title in titles)
    assert any("Beta" in title for title in titles)
    assert clone_provider.classified_titles == []


def test_w12_tied_observation_does_not_supersede_peer(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    _install_tracking_provider(monkeypatch)
    v1 = _version_item(
        "tie",
        title="Tie alpha",
        excerpt="excerpt one",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N,
    )
    v2 = _version_item(
        "tie",
        title="Tie beta",
        excerpt="excerpt two",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N,
    )
    checkpoint = save_checkpoint(project, [v1, v2], now=DAY_N, batch_id="batch-tie")
    _seed(project, checkpoint)
    MorningRadarPipeline(project).process(batch_id="batch-tie", now=DAY_N, notify=False)
    ledger = _ledger(project)
    e1 = ledger.get(v1.id, content_version(v1))
    e2 = ledger.get(v2.id, content_version(v2))
    statuses = {e1.reason_code, e2.reason_code}
    assert ReasonCode.SUPERSEDED not in statuses

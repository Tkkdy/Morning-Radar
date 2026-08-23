import logging
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

from morning_radar.models import (
    DailyBrief,
    DailyContinuity,
    DailyTendencies,
    GitHubSnapshot,
    MarketSnapshot,
    Story,
    StoryOccurrenceRef,
    WatchEvent,
    WatchEventType,
)
from morning_radar.pipeline import MorningRadarPipeline
from morning_radar.storage import save_model, save_models


def test_full_fixture_pipeline_has_no_network_or_real_ai(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    caplog.set_level(logging.INFO)

    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixture pipeline must not access the network")

    monkeypatch.setattr("socket.create_connection", fail_network)
    project = Path(".").resolve()
    pipeline = MorningRadarPipeline(project)
    pipeline.root = project
    # Dry-run keeps generated data out of production paths.
    pipeline.root = project
    monkeypatch.setattr(pipeline, "root", project)
    brief = pipeline.run(fixtures=True, dry_run=True)

    output = project / ".tmp/dry-run"
    assert brief.run_stats["fixture_mode"] is True
    assert brief.top_stories
    assert (output / "data/briefs/2026-07-23.json").exists()
    assert (output / "data/continuity/2026-07-23.json").exists()
    assert (output / "data/radar_signals/2026-07-23.json").exists()
    assert (output / "data/tendencies/2026-07-23.json").exists()
    assert (output / "data/editorial/2026-07-23.json").exists()
    assert (output / "site/index.html").exists()
    assert (output / "site/archive.html").exists()
    assert "raw_collected=4" in caplog.text
    assert "recent_24h=4" in caplog.text
    assert "story_candidate_input=4" in caplog.text
    assert "routine_market_suppressed=0" in caplog.text
    assert "main_brief_items=3" in caplog.text
    assert "other_reading_items=0" in caplog.text
    assert "total_displayed_items=3" in caplog.text
    assert "logical_ai_calls=0" in caplog.text
    assert brief.run_stats["main_brief_items"] == 3
    assert brief.run_stats["other_reading_items"] == 0
    assert brief.run_stats["total_displayed_items"] == 3
    assert brief.run_stats["ai_input_characters"] == 0
    assert brief.run_stats["ai_maximum_input_characters"] == 120000
    assert brief.watch_next == ["继续观察 OpenAI 的后续官方发布与开发者反馈。"]
    index = (output / "site/index.html").read_text(encoding="utf-8")
    assert '<p class="section-label">top_stories</p>' not in index
    assert '<p class="section-label">今日重点</p>' in index


def test_editorial_artifact_save_failure_does_not_block_fixture_brief(
    monkeypatch,
) -> None:
    original_save_model = __import__(
        "morning_radar.pipeline",
        fromlist=["save_model"],
    ).save_model

    def fail_only_editorial(path, model) -> None:
        if path.parent.name == "editorial":
            raise OSError("simulated editorial storage failure")
        original_save_model(path, model)

    monkeypatch.setattr("morning_radar.pipeline.save_model", fail_only_editorial)
    pipeline = MorningRadarPipeline(Path(".").resolve())
    brief = pipeline.run(fixtures=True, dry_run=True)
    assert brief.top_stories
    assert brief.run_stats["editorial_shadow_mode"] is True


def test_dry_run_reads_production_history_without_mutating_it(
    tmp_path,
    monkeypatch,
) -> None:
    source_project = Path(".").resolve()
    project = tmp_path / "project"
    shutil.copytree(source_project / "config", project / "config")
    (project / "fixtures").mkdir()
    shutil.copy2(
        source_project / "fixtures/sample_items.json",
        project / "fixtures/sample_items.json",
    )

    historical_story = Story(
        id="story-historical-openai",
        canonical_title="OpenAI structured output preview announced",
        category="ai_and_open_source",
        updated_at=datetime(2026, 7, 22, 1, tzinfo=UTC),
        source_item_ids=["historical-item"],
        source_urls=["https://example.com/openai/previous"],
        primary_source_url="https://example.com/openai/previous",
        entity_names=["OpenAI"],
        facts=["OpenAI announced an earlier structured output preview."],
        relevance_score=0.9,
        importance_score=0.8,
        novelty_score=0.8,
        credibility_score=0.9,
    )
    historical_continuity = DailyContinuity(
        date=date(2026, 7, 22),
        generated_at=datetime(2026, 7, 22, 2, tzinfo=UTC),
        watch_events=[
            WatchEvent(
                watch_id="watch-openai-structured-output",
                recorded_at=datetime(2026, 7, 22, 2, tzinfo=UTC),
                event_type=WatchEventType.OPENED,
                expectation="Watch for OpenAI structured output release details.",
                entity_anchors=["OpenAI"],
                source_story_refs=[
                    StoryOccurrenceRef(
                        date=date(2026, 7, 22),
                        story_id=historical_story.id,
                    )
                ],
            )
        ],
    )
    story_path = project / "data/stories/2026-07-22.json"
    continuity_path = project / "data/continuity/2026-07-22.json"
    tendency_path = project / "data/tendencies/2026-07-22.json"
    save_models(story_path, [historical_story])
    save_model(continuity_path, historical_continuity)
    save_model(
        tendency_path,
        DailyTendencies(
            date=date(2026, 7, 22),
            generated_at=datetime(2026, 7, 22, 2, tzinfo=UTC),
        ),
    )
    production_before = {
        story_path: story_path.read_bytes(),
        continuity_path: continuity_path.read_bytes(),
        tendency_path: tendency_path.read_bytes(),
    }

    pipeline = MorningRadarPipeline(project)
    monkeypatch.setattr(pipeline, "build_site", lambda **kwargs: None)
    brief = pipeline.run(fixtures=True, dry_run=True, notify=False)

    output = project / ".tmp/dry-run"
    assert brief.run_stats["historical_story_candidates"] == 1
    assert brief.run_stats["open_watches_considered"] == 1
    for relative_path in (
        "data/raw/2026-07-23.json",
        "data/stories/2026-07-23.json",
        "data/signals/2026-07-23.json",
        "data/briefs/2026-07-23.json",
        "data/continuity/2026-07-23.json",
        "data/radar_signals/2026-07-23.json",
        "data/tendencies/2026-07-23.json",
    ):
        assert (output / relative_path).exists()
        assert not (project / relative_path).exists()
    assert {
        path: path.read_bytes() for path in production_before
    } == production_before


def test_preview_snapshot_view_merges_production_history_with_current_output(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    pipeline = MorningRadarPipeline(Path(".").resolve())
    historical_github = GitHubSnapshot(
        date=date(2026, 7, 22),
        captured_at=datetime(2026, 7, 22, 1, tzinfo=UTC),
        repository="example/agent",
        stars=100,
        forks=10,
        open_issues=2,
        updated_at=datetime(2026, 7, 22, 1, tzinfo=UTC),
    )
    current_github = historical_github.model_copy(
        update={
            "date": date(2026, 7, 23),
            "captured_at": datetime(2026, 7, 23, 1, tzinfo=UTC),
            "stars": 110,
        }
    )
    historical_market = MarketSnapshot(
        date=date(2026, 7, 22),
        captured_at=datetime(2026, 7, 22, 1, tzinfo=UTC),
        company="Example",
        ticker="EX",
        trading_date=date(2026, 7, 22),
        close=100,
        previous_close=99,
        change_percent=0.01,
    )
    current_market = historical_market.model_copy(
        update={
            "date": date(2026, 7, 23),
            "captured_at": datetime(2026, 7, 23, 1, tzinfo=UTC),
            "trading_date": date(2026, 7, 23),
            "close": 101,
        }
    )
    for lane, historical, current in (
        ("github", historical_github, current_github),
        ("market", historical_market, current_market),
    ):
        save_models(project / f"data/snapshots/{lane}/2026-07-22.json", [historical])
        save_models(
            project / f".tmp/dry-run/data/snapshots/{lane}/2026-07-23.json",
            [current],
        )

    github = pipeline._snapshots(
        project / "data/snapshots/github",
        project / ".tmp/dry-run/data/snapshots/github",
        GitHubSnapshot,
        date(2026, 7, 23),
    )
    market = pipeline._snapshots(
        project / "data/snapshots/market",
        project / ".tmp/dry-run/data/snapshots/market",
        MarketSnapshot,
        date(2026, 7, 23),
    )

    assert [item.date for item in github] == [date(2026, 7, 22), date(2026, 7, 23)]
    assert [item.date for item in market] == [date(2026, 7, 22), date(2026, 7, 23)]
    assert not (project / "data/snapshots/github/2026-07-23.json").exists()
    assert not (project / "data/snapshots/market/2026-07-23.json").exists()


def test_preview_site_merges_history_and_today_without_mutating_history(tmp_path) -> None:
    source_project = Path(".").resolve()
    history_root = tmp_path / "production"
    output_root = tmp_path / "preview"
    historical = DailyBrief(
        date=date(2026, 7, 22),
        timezone="Asia/Singapore",
        generated_at=datetime(2026, 7, 22, 1, tzinfo=UTC),
    )
    current = historical.model_copy(
        update={
            "date": date(2026, 7, 23),
            "generated_at": datetime(2026, 7, 23, 1, tzinfo=UTC),
            "watch_next": ["preview wins"],
        }
    )
    history_path = history_root / "data/briefs/2026-07-22.json"
    save_model(history_path, historical)
    duplicate_path = history_root / "data/briefs/2026-07-23.json"
    save_model(
        duplicate_path,
        current.model_copy(update={"watch_next": ["production duplicate"]}),
    )
    save_model(output_root / "data/briefs/2026-07-23.json", current)
    history_before = history_path.read_bytes()

    MorningRadarPipeline(source_project).build_site(
        output_root=output_root,
        history_root=history_root,
    )

    archive = (output_root / "site/archive.html").read_text(encoding="utf-8")
    assert "2026-07-22" in archive
    assert "2026-07-23" in archive
    assert (output_root / "site/briefs/2026-07-22.html").exists()
    assert (output_root / "site/briefs/2026-07-23.html").exists()
    index = (output_root / "site/index.html").read_text(encoding="utf-8")
    assert "preview wins" in index
    assert "production duplicate" not in index
    assert history_path.read_bytes() == history_before
    assert "production duplicate" in duplicate_path.read_text(encoding="utf-8")
    assert not (history_root / "site").exists()

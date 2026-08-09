import logging
from pathlib import Path

from morning_radar.pipeline import MorningRadarPipeline


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
    assert brief.watch_next == ["继续观察 OpenAI 的后续官方发布与开发者反馈。"]
    index = (output / "site/index.html").read_text(encoding="utf-8")
    assert '<p class="section-label">top_stories</p>' not in index
    assert '<p class="section-label">今日重点</p>' in index

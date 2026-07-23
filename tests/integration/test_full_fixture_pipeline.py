from pathlib import Path

from morning_radar.pipeline import MorningRadarPipeline


def test_full_fixture_pipeline_has_no_network_or_real_ai(tmp_path, monkeypatch) -> None:
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


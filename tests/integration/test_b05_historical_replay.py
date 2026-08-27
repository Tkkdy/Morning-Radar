from datetime import date
from pathlib import Path

from morning_radar.evaluation.b05 import (
    ResourceEnvelope,
    budget_sweep,
    replay_architectures,
)


def test_deepseek_golden_failure_gets_semantic_triage_before_story_budget() -> None:
    report = replay_architectures(
        Path("."),
        date(2026, 8, 22),
        ResourceEnvelope(50, 120_000, 40, 17, 4),
    )
    deepseek_id = "item-4d7b9f9d11a89fb3b930"

    assert deepseek_id not in report["legacy"]["semantic_raw_ids"]
    assert deepseek_id in report["legacy"]["preselection_cap_raw_ids"]
    assert report["legacy"]["elimination_reason"] == "PRESELECTION_CAP"
    assert deepseek_id in report["b05"]["semantic_raw_ids"]
    assert deepseek_id in report["b05"]["must_triage_raw_ids"]
    assert report["legacy"]["major_event_recall"] == 0
    assert report["b05"]["major_event_recall"] == 1
    full = report["b05"]["full_pipeline"]
    assert full["pipeline"] == "MorningRadarPipeline.run"
    assert full["within_hard_cap"] is True
    assert full["input_characters"] <= 120_000
    assert full["run_stats"]["candidate_triage_input_characters"] >= 68_000
    assert {
        "triage",
        "story",
        "editorial",
        "continuity",
        "tendency",
        "brief",
    }.issubset(full["stage_calls"])
    assert full["evidence_integrity_violations"] == 0


def test_budget_sweep_is_repeatable_and_tracks_resource_dimensions() -> None:
    rows = budget_sweep(Path("."), date(2026, 8, 22))

    assert len(rows) == 4
    assert [row["input_characters"] for row in rows] == sorted(
        row["input_characters"] for row in rows
    )
    assert all(row["evidence_integrity_violations"] == 0 for row in rows)
    assert all("proxy_cost" in row and "runtime_seconds" in row for row in rows)
    assert all(row["reader_precision"] == "NOT_EVALUATED" for row in rows)

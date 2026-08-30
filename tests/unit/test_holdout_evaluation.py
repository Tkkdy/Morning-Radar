from __future__ import annotations

from datetime import date
from pathlib import Path

from morning_radar.evaluation.holdout import (
    HoldoutWorkload,
    assess_holdout_preflight,
    build_holdout_selection_report,
    select_holdout_set,
)

ROOT = Path(__file__).resolve().parents[2]


def workload(
    day: int,
    *,
    admitted: int,
    eligible: int,
    recent: int,
    raw: int = 1,
    excluded: bool = False,
) -> HoldoutWorkload:
    return HoldoutWorkload(
        evaluation_date=date(2026, 1, day),
        raw_count=raw,
        recent_count=recent,
        eligible_count=eligible,
        admitted_count=admitted,
        excluded=excluded,
        exclusion_reason="development case" if excluded else None,
    )


def test_heavy_selection_uses_declared_precedence_and_ignores_raw_count() -> None:
    rows = [
        workload(1, admitted=0, eligible=100, recent=100, raw=10_000),
        workload(2, admitted=4, eligible=4, recent=4, raw=1),
        workload(3, admitted=3, eligible=100, recent=100, raw=1000),
        workload(4, admitted=4, eligible=5, recent=5),
        workload(5, admitted=4, eligible=5, recent=6),
    ]

    selection = select_holdout_set(rows)

    assert selection.heavy.evaluation_date == date(2026, 1, 5)
    assert selection.sparse.evaluation_date == date(2026, 1, 3)
    assert selection.heavy.raw_count == 1


def test_date_resolves_exact_ties_and_excluded_dates_cannot_be_selected() -> None:
    rows = [
        workload(1, admitted=9, eligible=9, recent=9, excluded=True),
        workload(2, admitted=5, eligible=5, recent=5),
        workload(3, admitted=5, eligible=5, recent=5),
        workload(4, admitted=1, eligible=1, recent=1),
    ]

    selection = select_holdout_set(rows)

    assert selection.heavy.evaluation_date == date(2026, 1, 2)
    assert selection.sparse.evaluation_date == date(2026, 1, 4)
    assert date(2026, 1, 1) not in {
        selection.heavy.evaluation_date,
        selection.normal.evaluation_date,
        selection.sparse.evaluation_date,
    }


def test_sparse_requires_nonzero_admitted_workload() -> None:
    rows = [
        workload(1, admitted=0, eligible=0, recent=0, raw=20_000),
        workload(2, admitted=1, eligible=1, recent=1),
        workload(3, admitted=2, eligible=2, recent=2),
    ]

    selection = select_holdout_set(rows)

    assert selection.sparse.evaluation_date == date(2026, 1, 2)
    assert selection.heavy.evaluation_date == date(2026, 1, 3)


def test_normal_uses_lower_median_and_nearest_unused_date() -> None:
    even = [
        workload(1, admitted=1, eligible=1, recent=1),
        workload(2, admitted=2, eligible=2, recent=2),
        workload(3, admitted=3, eligible=3, recent=3),
        workload(4, admitted=4, eligible=4, recent=4),
    ]
    collision = [
        workload(1, admitted=1, eligible=1, recent=1),
        workload(2, admitted=2, eligible=2, recent=2),
        workload(3, admitted=3, eligible=3, recent=3),
    ]

    assert select_holdout_set(even).normal.evaluation_date == date(2026, 1, 2)
    assert select_holdout_set(collision).normal.evaluation_date == date(2026, 1, 2)


def test_recorded_fixtures_produce_deterministic_corrected_selection() -> None:
    first = build_holdout_selection_report(ROOT)
    second = build_holdout_selection_report(ROOT)

    assert first == second
    assert first["external_provider_calls"] == 0
    assert first["provider_result"] == "NOT_EVALUATED"
    selection = first["selection"]
    assert selection["raw_count_role"] == "DIAGNOSTIC_ONLY"
    selected = {selection[role]["evaluation_date"] for role in ("heavy", "normal", "sparse")}
    assert "2026-07-23" not in selected
    assert "2026-08-22" not in selected


def test_preflight_requires_safe_pipeline_and_actual_semantic_triage() -> None:
    summary = {
        "status": "COMPLETED",
        "environment": {
            "evaluation_mode": "holdout",
            "pipeline": "MorningRadarPipeline.run",
            "live_collectors": "DISABLED",
            "evidence_network": "DISABLED",
            "notification": "DISABLED",
            "production_writes": "DISABLED",
        },
        "safety_observations": {"whole_run_attempts": 1, "evidence_fetch_calls": 0},
        "resource_envelope": {"network_requests_total": 0},
        "candidate_funnel": {"admitted": 4, "triaged": 4},
    }

    assert assess_holdout_preflight(summary)["status"] == "READY_FOR_HOLDOUT_REAL_EVAL"
    summary["candidate_funnel"] = {"admitted": 55, "triaged": 0}
    blocked = assess_holdout_preflight(summary)
    assert blocked["status"] == "NOT_READY"
    assert "ALL_CANDIDATES_DEFERRED_BEFORE_SEMANTIC_TRIAGE" in blocked["blockers"]

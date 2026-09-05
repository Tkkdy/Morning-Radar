"""Offline deterministic selection of frozen B0.5 semantic holdout workloads."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from morning_radar.candidates import admit_candidates, attach_official_source_entities
from morning_radar.evaluation.semantic import SemanticEvaluationMode, _load_frozen_workload
from morning_radar.processing import filter_news_window, filter_story_candidate_inputs
from morning_radar.settings import AppConfig, SourceConfig, load_model, load_model_list

DEFAULT_EXCLUSIONS = {
    date(2026, 7, 23): (
        "Current B0.5 full-fixture integration assertions use this date for "
        "Candidate and diagnostics behavior."
    ),
    date(2026, 8, 22): (
        "B0.5 development/regression date used for the DeepSeek Vision golden case."
    ),
}


@dataclass(frozen=True, slots=True)
class HoldoutWorkload:
    """Provider-independent workload measured before semantic triage."""

    evaluation_date: date
    raw_count: int
    recent_count: int
    eligible_count: int
    admitted_count: int
    excluded: bool = False
    exclusion_reason: str | None = None

    @property
    def workload_tuple(self) -> tuple[int, int, int]:
        return (self.admitted_count, self.eligible_count, self.recent_count)

    def as_report_row(self) -> dict[str, object]:
        row = asdict(self)
        row["evaluation_date"] = str(self.evaluation_date)
        row["workload_tuple"] = list(self.workload_tuple)
        return row


@dataclass(frozen=True, slots=True)
class HoldoutSelection:
    heavy: HoldoutWorkload
    normal: HoldoutWorkload
    sparse: HoldoutWorkload
    normal_median_policy: str = "lower median; nearest unused index if role collision"

    def as_report(self) -> dict[str, object]:
        return {
            "workload_precedence": [
                "admitted_count",
                "eligible_count",
                "recent_count",
                "date ascending",
            ],
            "raw_count_role": "DIAGNOSTIC_ONLY",
            "normal_median_policy": self.normal_median_policy,
            "heavy": self.heavy.as_report_row(),
            "normal": self.normal.as_report_row(),
            "sparse": self.sparse.as_report_row(),
            "holdout_contamination_rule": (
                "Selected dates become holdouts immediately. If a selected finding is used "
                "to change B0.5 production semantics, the complete trio becomes development-"
                "contaminated and must be replaced for final generalization proof."
            ),
        }


def collect_holdout_workloads(
    root: Path,
    *,
    exclusions: dict[date, str] | None = None,
) -> list[HoldoutWorkload]:
    """Measure every historical Raw date through frozen production admission."""
    root = root.resolve()
    excluded_dates = DEFAULT_EXCLUSIONS if exclusions is None else exclusions
    app = load_model(root / "config/app.yaml", AppConfig)
    sources = load_model_list(root / "config/sources.yaml", "sources", SourceConfig)
    source_entities = {
        source.id: source.entity
        for source in sources
        if source.official and source.entity
    }
    rows: list[HoldoutWorkload] = []
    for raw_path in sorted((root / "data/raw").glob("*.json")):
        try:
            evaluation_date = date.fromisoformat(raw_path.stem)
        except ValueError:
            continue
        raw, frozen_now = _load_frozen_workload(
            root,
            evaluation_date,
            evaluation_mode=SemanticEvaluationMode.HOLDOUT,
        )
        enriched = attach_official_source_entities(raw, source_entities)
        recent = filter_news_window(
            enriched,
            now=frozen_now,
            hours=app.news_window_hours,
        )
        eligible, _ = filter_story_candidate_inputs(
            recent,
            market_movement_threshold=app.market_movement_threshold,
        )
        admitted = admit_candidates(eligible, now=frozen_now)
        exclusion_reason = excluded_dates.get(evaluation_date)
        rows.append(
            HoldoutWorkload(
                evaluation_date=evaluation_date,
                raw_count=len(raw),
                recent_count=len(recent),
                eligible_count=len(eligible),
                admitted_count=len(admitted),
                excluded=exclusion_reason is not None,
                exclusion_reason=exclusion_reason,
            )
        )
    return rows


def _ordered(rows: Iterable[HoldoutWorkload]) -> list[HoldoutWorkload]:
    return sorted(
        rows,
        key=lambda row: (*row.workload_tuple, row.evaluation_date),
    )


def select_holdout_set(rows: Iterable[HoldoutWorkload]) -> HoldoutSelection:
    """Select distinct Heavy, lower-median Normal, and nonzero Sparse dates."""
    eligible = _ordered(row for row in rows if not row.excluded)
    if len(eligible) < 3:
        raise ValueError("At least three eligible historical dates are required")

    heaviest_tuple = max(row.workload_tuple for row in eligible)
    heavy = min(
        (row for row in eligible if row.workload_tuple == heaviest_tuple),
        key=lambda row: row.evaluation_date,
    )
    sparse = min(
        (row for row in eligible if row.admitted_count > 0),
        key=lambda row: (*row.workload_tuple, row.evaluation_date),
        default=None,
    )
    if sparse is None:
        raise ValueError("No eligible date has admitted semantic workload")
    if sparse.evaluation_date == heavy.evaluation_date:
        raise ValueError("At least two distinct nonzero workloads are required")

    median_index = (len(eligible) - 1) // 2
    candidate_indices = sorted(
        range(len(eligible)),
        key=lambda index: (abs(index - median_index), index),
    )
    used = {heavy.evaluation_date, sparse.evaluation_date}
    normal = next(
        (
            eligible[index]
            for index in candidate_indices
            if eligible[index].evaluation_date not in used
        ),
        None,
    )
    if normal is None:
        raise ValueError("No distinct eligible date remains for the normal holdout")
    return HoldoutSelection(heavy=heavy, normal=normal, sparse=sparse)


def build_holdout_selection_report(root: Path) -> dict[str, object]:
    rows = collect_holdout_workloads(root)
    selection = select_holdout_set(rows)
    return {
        "status": "DETERMINISTIC_SELECTION_COMPLETE",
        "provider_result": "NOT_EVALUATED",
        "external_provider_calls": 0,
        "selection": selection.as_report(),
        "workloads": [row.as_report_row() for row in _ordered(rows)],
    }


def assess_holdout_preflight(summary: dict[str, object]) -> dict[str, object]:
    """Classify an offline Fake run without treating Fake semantics as quality evidence."""
    blockers: list[str] = []
    environment = summary.get("environment", {})
    safety = summary.get("safety_observations", {})
    resources = summary.get("resource_envelope", {})
    funnel = summary.get("candidate_funnel", {})
    if summary.get("status") != "COMPLETED":
        blockers.append("PIPELINE_NOT_COMPLETED")
    if not isinstance(environment, dict) or environment.get("evaluation_mode") != "holdout":
        blockers.append("NOT_HOLDOUT_MODE")
    if (
        not isinstance(environment, dict)
        or environment.get("pipeline") != "MorningRadarPipeline.run"
    ):
        blockers.append("PRODUCTION_PIPELINE_NOT_REACHED")
    for key in ("live_collectors", "evidence_network", "notification", "production_writes"):
        if not isinstance(environment, dict) or environment.get(key) != "DISABLED":
            blockers.append(f"{key.upper()}_NOT_DISABLED")
    if not isinstance(safety, dict) or safety.get("whole_run_attempts") != 1:
        blockers.append("WHOLE_RUN_ATTEMPTS_NOT_ONE")
    if not isinstance(safety, dict) or safety.get("evidence_fetch_calls") != 0:
        blockers.append("EVIDENCE_FETCH_ATTEMPTED")
    if not isinstance(resources, dict) or resources.get("network_requests_total") != 0:
        blockers.append("EXTERNAL_PROVIDER_REQUEST_OBSERVED")
    if not isinstance(funnel, dict) or not funnel.get("admitted"):
        blockers.append("NO_ADMITTED_SEMANTIC_WORKLOAD")
    elif not funnel.get("triaged"):
        blockers.append("ALL_CANDIDATES_DEFERRED_BEFORE_SEMANTIC_TRIAGE")
    return {
        "status": "READY_FOR_HOLDOUT_REAL_EVAL" if not blockers else "NOT_READY",
        "blockers": blockers,
        "semantic_quality": "NOT_EVALUATED_WITH_FAKE_PROVIDER",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(build_holdout_selection_report(args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

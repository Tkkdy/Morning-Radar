from __future__ import annotations

from pathlib import Path

import pytest

from morning_radar.evaluation.minimal_atomic_evaluation import (
    EXPECTED_CANDIDATES,
    EXPECTED_PAYLOAD_SHA256,
    EXPECTED_PROMPT_SHA256,
    EXPECTED_SCHEMA_SHA256,
    HISTORICAL_COMPARATORS,
    MinimalAtomicEvaluationSafetyError,
    analyze_trials,
    build_gate_manifest,
    run_experiment,
)


def test_frozen_gate_manifest_matches_precommitted_identity() -> None:
    manifest = build_gate_manifest(Path("."))

    assert manifest["status"] == "READY_FOR_MINIMAL_ATOMIC_REAL_REEVAL"
    assert manifest["candidate_count"] == EXPECTED_CANDIDATES
    assert manifest["candidate_payload_sha256"] == EXPECTED_PAYLOAD_SHA256
    assert manifest["prompt_sha256"] == EXPECTED_PROMPT_SHA256
    assert manifest["schema_sha256"] == EXPECTED_SCHEMA_SHA256
    assert manifest["trial_count"] == 3
    assert manifest["whole_trial_rerun"] == "DISABLED"
    assert manifest["historical_comparators"] == HISTORICAL_COMPARATORS
    assert manifest["current_v1_v2"] == "HISTORICAL_COMPARATORS_ONLY_NOT_RERUN"


def test_real_runner_requires_explicit_confirmation_before_environment_or_writes(
    tmp_path: Path,
) -> None:
    with pytest.raises(MinimalAtomicEvaluationSafetyError, match="explicit confirmation"):
        run_experiment(Path("."), tmp_path / "result")

    assert not (tmp_path / "result").exists()


def test_trial_analysis_reports_completion_stability_and_local_invalid_rate() -> None:
    trials = []
    for ordinal in range(1, 4):
        rows = [
            {
                "candidate_id": f"candidate-{index}",
                "derived_route": "build" if index < 2 else "unresolved",
                "assessment_validation_status": (
                    "invalid" if index == 2 and ordinal == 2 else "valid"
                ),
            }
            for index in range(3)
        ]
        trials.append(
            {
                "status": "COMPLETED",
                "candidates": rows,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "reasoning_tokens": 0,
                "network_requests": 1,
            }
        )

    metrics = analyze_trials(trials)

    assert metrics["trial_completion"] == "3/3"
    assert metrics["mean_route_agreement"] == 1.0
    assert metrics["stable_3_of_3_count"] == 3
    assert metrics["candidate_local_valid_rate"] == pytest.approx(8 / 9)
    assert metrics["invalid_rate"] == pytest.approx(1 / 9)
    assert metrics["evidence_boundary_violations"] == 0
    assert metrics["usage"] == {
        "prompt_tokens": 30,
        "completion_tokens": 15,
        "reasoning_tokens": 0,
        "network_requests": 3,
    }

"""Frozen proposed-only Minimal Atomic real-evaluation gate and runner."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import TypeAdapter

from morning_radar.ai import AIBudget
from morning_radar.ai.deepseek_provider import DeepSeekProvider
from morning_radar.candidates import admit_candidates, attach_official_source_entities
from morning_radar.candidates.engine import _triage_order
from morning_radar.evaluation.minimal_atomic_router import (
    ASSESSMENT_SCHEMA_VERSION,
    ROUTER_VERSION,
    AssessmentValidationStatus,
    MinimalAtomicAssessmentProviderAdapter,
    MinimalCandidateSemanticAssessmentBatch,
    ProposedRoute,
    SystemRoutingContext,
    assessment_artifact,
    build_evidence_profile,
    invalid_assessment_decision,
    route_assessment,
    validate_assessment,
)
from morning_radar.models import Candidate, RawItem
from morning_radar.processing import filter_news_window, filter_story_candidate_inputs
from morning_radar.settings import AppConfig, SourceConfig, load_model, load_model_list
from morning_radar.storage import write_json

FROZEN_DATE = date(2026, 8, 22)
FROZEN_NOW = datetime.fromisoformat("2026-08-22T07:56:14.225732+08:00")
EXPECTED_CANDIDATES = 38
EXPECTED_PAYLOAD_SHA256 = "1212995347ba2710b22537b3b0e2973e00f4e7872c22c445dee833bcf3b4c858"
EXPECTED_MODEL = "deepseek-v4-flash"
EXPECTED_HOST = "api.deepseek.com"
TEMPERATURE = 1.0
TRIALS = 3
PROMPT_PATH = Path("prompts/evaluation_minimal_atomic/candidate_triage.md")
EXPECTED_PROMPT_SHA256 = "4c193118a7e992f5a0165b17fc183df6a3a12ede120a83b77c73a1f44bcfbf6a"
EXPECTED_SCHEMA_SHA256 = "7c8e80a9783be3b42e3d800bce9936c28826c6261cf63dc1d87149d7665e90df"

HISTORICAL_COMPARATORS = {
    "CURRENT": {"mean_route_agreement": 0.614, "stable_3_of_3": 16},
    "ATOMIC_V1": {"mean_route_agreement": 0.807, "stable_3_of_3": 27},
    "ATOMIC_V2": {"mean_route_agreement": 0.667, "stable_3_of_3": 19},
}

ANCHOR_IDS = {
    "DeepSeek Vision golden": "candidate-1d548e3bcbd3593d5128",
    "Meta AI glasses": "candidate-86e75913221b5cd57185",
    "Starcloud orbital data centers": "candidate-97efeb77b03c55418eea",
    "Border phone-data": "candidate-b1facd7245c2927ec3e9",
    "Physical books / AI scanning": "candidate-bf8d7b923eea97b08071",
    "Flock camera": "candidate-bbe255b7a8c2c25ffb5b",
    "Micro1": "candidate-7453f9eb595a55420dda",
    "GitHub Copilot Teams": "candidate-006b36b9d1483b09aa8c",
    "Ollama": "candidate-628202a98a8bc40296ef",
    "GitHub Copilot Slack": "candidate-a3e6f24c629d26c03c7e",
    "Cloudflare": "candidate-c63c4d2eb9ab9c5f7db2",
    "pydantic-ai v2.33": "candidate-d16cffc461481617342c",
}


class MinimalAtomicEvaluationSafetyError(RuntimeError):
    """The frozen experiment identity or explicit authorization is absent."""


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_frozen_candidates(root: Path) -> tuple[Candidate, ...]:
    app = load_model(root / "config/app.yaml", AppConfig)
    raw = TypeAdapter(list[RawItem]).validate_json(
        (root / f"data/raw/{FROZEN_DATE}.json").read_text(encoding="utf-8")
    )
    brief = json.loads(
        (root / f"data/briefs/{FROZEN_DATE}.json").read_text(encoding="utf-8")
    )
    recorded_now = datetime.fromisoformat(
        str(brief["generated_at"]).replace("Z", "+00:00")
    )
    if recorded_now != FROZEN_NOW:
        raise MinimalAtomicEvaluationSafetyError("Frozen historical now changed")
    sources = load_model_list(root / "config/sources.yaml", "sources", SourceConfig)
    source_entities = {
        source.id: source.entity
        for source in sources
        if source.official and source.entity
    }
    recent = filter_news_window(
        attach_official_source_entities(raw, source_entities),
        now=FROZEN_NOW,
        hours=app.news_window_hours,
    )
    eligible, _ = filter_story_candidate_inputs(
        recent,
        market_movement_threshold=app.market_movement_threshold,
    )
    candidates = tuple(
        sorted(admit_candidates(eligible, now=FROZEN_NOW), key=_triage_order)
    )
    payload = _compact_json(
        [candidate.model_dump(mode="json") for candidate in candidates]
    )
    if len(candidates) != EXPECTED_CANDIDATES:
        raise MinimalAtomicEvaluationSafetyError("Frozen Candidate count changed")
    if _sha256(payload) != EXPECTED_PAYLOAD_SHA256:
        raise MinimalAtomicEvaluationSafetyError("Frozen Candidate payload changed")
    return candidates


def _schema_sha256() -> str:
    return _sha256(
        _compact_json(MinimalCandidateSemanticAssessmentBatch.model_json_schema())
    )


def _prompt_sha256(root: Path) -> str:
    return _sha256((root / PROMPT_PATH).read_text(encoding="utf-8"))


def build_gate_manifest(root: Path) -> dict[str, Any]:
    candidates = build_frozen_candidates(root)
    prompt_sha = _prompt_sha256(root)
    schema_sha = _schema_sha256()
    if prompt_sha != EXPECTED_PROMPT_SHA256 or schema_sha != EXPECTED_SCHEMA_SHA256:
        raise MinimalAtomicEvaluationSafetyError("Frozen prompt or schema hash changed")
    return {
        "status": "READY_FOR_REAL_SEMANTIC_EVAL",
        "frozen_date": str(FROZEN_DATE),
        "frozen_now": FROZEN_NOW.isoformat(),
        "candidate_count": len(candidates),
        "candidate_ids_in_order": [candidate.id for candidate in candidates],
        "candidate_payload_sha256": EXPECTED_PAYLOAD_SHA256,
        "prompt_path": PROMPT_PATH.as_posix(),
        "prompt_sha256": prompt_sha,
        "schema_sha256": schema_sha,
        "assessment_schema_version": ASSESSMENT_SCHEMA_VERSION,
        "router_version": ROUTER_VERSION,
        "model": EXPECTED_MODEL,
        "host": EXPECTED_HOST,
        "thinking": "disabled",
        "temperature": TEMPERATURE,
        "top_p": "OMITTED",
        "seed": "OMITTED",
        "batch_shape": [EXPECTED_CANDIDATES],
        "trial_count": TRIALS,
        "whole_trial_rerun": "DISABLED",
        "current_v1_v2": "HISTORICAL_COMPARATORS_ONLY_NOT_RERUN",
        "historical_comparators": HISTORICAL_COMPARATORS,
        "evidence_http": "DISABLED",
        "story": "DISABLED",
        "downstream": "DISABLED",
        "production_writes": "DISABLED",
        "acceptance_metrics": [
            "trial_completion",
            "candidate_local_valid_rate",
            "route_agreement",
            "stable_3_of_3_count",
            "stable_build_anchors",
            "stable_investigate_anchors",
            "reasonable_no_resource_behavior",
            "unresolved_rate",
            "invalid_rate",
            "evidence_boundary_violations",
            "prompt_completion_usage",
        ],
        "rejection_blockers": [
            "EVIDENCE_INTEGRITY_REGRESSION",
            "BATCH_FATAL_VALIDATION_REGRESSION",
            "UTILITY_COLLAPSE_INTO_UNRESOLVED",
            "LOSS_OF_KNOWN_EVIDENCE_BACKED_BUILD_ANCHORS",
            "MATERIAL_STABILITY_REGRESSION_VERSUS_V1_DIRECTION",
        ],
    }


def _provider(
    root: Path,
    *,
    model: str,
    base_url: str,
    api_key: str,
    observations: list[dict[str, Any]],
) -> DeepSeekProvider:
    return DeepSeekProvider(
        model=model,
        api_key=api_key,
        base_url=base_url,
        budget=AIBudget(1, 80_000, EXPECTED_CANDIDATES),
        prompt_dir=(root / PROMPT_PATH).parent,
        candidate_triage_temperature=TEMPERATURE,
        response_observer=observations.append,
    )


def _run_trial(
    root: Path,
    candidates: tuple[Candidate, ...],
    ordinal: int,
    *,
    model: str,
    base_url: str,
    api_key: str,
) -> dict[str, Any]:
    build_gate_manifest(root)
    observations: list[dict[str, Any]] = []
    provider = _provider(
        root,
        model=model,
        base_url=base_url,
        api_key=api_key,
        observations=observations,
    )
    rows: list[dict[str, Any]] = []
    error = None
    output = None
    context = SystemRoutingContext()
    try:
        output = MinimalAtomicAssessmentProviderAdapter(provider).assess_candidates(
            list(candidates)
        )
        by_id = {assessment.candidate_id: assessment for assessment in output.candidates}
        for candidate in candidates:
            assessment = by_id[candidate.id]
            validation = validate_assessment(candidate, assessment)
            decision = (
                invalid_assessment_decision(candidate, validation)
                if validation.status is AssessmentValidationStatus.INVALID
                else route_assessment(
                    candidate,
                    build_evidence_profile(candidate),
                    assessment,
                    validation,
                    context,
                )
            )
            rows.append(
                assessment_artifact(
                    candidate, assessment, validation, decision, context
                )
            )
    except Exception as exc:  # noqa: BLE001 - preserve bounded trial failure
        error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
    usage = provider.budget.task_usage.get("candidate_triage", {})
    responses = [item for item in observations if item["event"] == "response_received"]
    attempts = [
        item for item in observations if item["event"] == "structured_attempt_started"
    ]
    return {
        "trial_id": f"minimal-atomic-trial-{ordinal}",
        "status": "COMPLETED" if output is not None and error is None else "FAILED",
        "structured_attempts": len(attempts),
        "network_requests": provider.budget.network_requests_used,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "reasoning_tokens": usage.get("reasoning_tokens", 0),
        "response_observations": responses,
        "distribution": dict(Counter(row["derived_route"] for row in rows)),
        "candidates": rows,
        "error": error,
    }


def analyze_trials(trials: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [trial for trial in trials if trial["status"] == "COMPLETED"]
    route_maps = [
        {row["candidate_id"]: row["derived_route"] for row in trial["candidates"]}
        for trial in completed
    ]
    agreements = []
    for left, right in itertools.combinations(route_maps, 2):
        shared = sorted(set(left).intersection(right))
        agreements.append(
            sum(left[candidate_id] == right[candidate_id] for candidate_id in shared)
            / len(shared)
            if shared
            else 0.0
        )
    stable_ids = (
        [
            candidate_id
            for candidate_id in route_maps[0]
            if len({routes[candidate_id] for routes in route_maps}) == 1
        ]
        if len(route_maps) == TRIALS
        else []
    )
    all_rows = [row for trial in completed for row in trial["candidates"]]
    invalid = [
        row for row in all_rows if row["assessment_validation_status"] == "invalid"
    ]
    boundary_violations = [
        row
        for row in all_rows
        if row["derived_route"] == ProposedRoute.BUILD.value
        and row["assessment_validation_status"] != "valid"
    ]
    anchor_routes = {
        label: [routes.get(candidate_id) for routes in route_maps]
        for label, candidate_id in ANCHOR_IDS.items()
    }
    return {
        "trial_completion": f"{len(completed)}/{TRIALS}",
        "candidate_local_valid_rate": (
            (len(all_rows) - len(invalid)) / len(all_rows) if all_rows else 0.0
        ),
        "pairwise_route_agreement": agreements,
        "mean_route_agreement": sum(agreements) / len(agreements) if agreements else 0.0,
        "stable_3_of_3_count": len(stable_ids),
        "stable_candidate_ids": stable_ids,
        "stable_build_anchors": sum(
            len(routes) == TRIALS and set(routes) == {ProposedRoute.BUILD.value}
            for routes in anchor_routes.values()
        ),
        "stable_investigate_anchors": sum(
            len(routes) == TRIALS and set(routes) == {ProposedRoute.INVESTIGATE.value}
            for routes in anchor_routes.values()
        ),
        "anchor_routes": anchor_routes,
        "unresolved_rate": (
            sum(row["derived_route"] == ProposedRoute.UNRESOLVED.value for row in all_rows)
            / len(all_rows)
            if all_rows
            else 0.0
        ),
        "invalid_rate": len(invalid) / len(all_rows) if all_rows else 0.0,
        "evidence_boundary_violations": len(boundary_violations),
        "usage": {
            "prompt_tokens": sum(trial["prompt_tokens"] for trial in trials),
            "completion_tokens": sum(trial["completion_tokens"] for trial in trials),
            "reasoning_tokens": sum(trial["reasoning_tokens"] for trial in trials),
            "network_requests": sum(trial["network_requests"] for trial in trials),
        },
    }


def run_experiment(
    root: Path,
    output: Path,
    *,
    confirm_real_provider_eval: bool = False,
) -> dict[str, Any]:
    if not confirm_real_provider_eval:
        raise MinimalAtomicEvaluationSafetyError(
            "Real Minimal Atomic evaluation requires explicit confirmation"
        )
    model = os.getenv("DEEPSEEK_MODEL", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "")
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if model != EXPECTED_MODEL:
        raise MinimalAtomicEvaluationSafetyError("Unexpected model")
    if urlsplit(base_url).hostname != EXPECTED_HOST or not api_key:
        raise MinimalAtomicEvaluationSafetyError("Host or credential preflight failed")
    manifest = build_gate_manifest(root)
    candidates = build_frozen_candidates(root)
    output = output.resolve()
    if output == root.resolve() or not output.is_relative_to(root.resolve()):
        raise MinimalAtomicEvaluationSafetyError("Output must be isolated below repository root")
    trials = []
    write_json(output / "manifest.json", manifest)
    for ordinal in range(1, TRIALS + 1):
        trial = _run_trial(
            root,
            candidates,
            ordinal,
            model=model,
            base_url=base_url,
            api_key=api_key,
        )
        trials.append(trial)
        write_json(output / "trials" / f"{trial['trial_id']}.json", trial)
    report = {
        "status": "COMPLETED",
        "manifest": manifest,
        "metrics": analyze_trials(trials),
    }
    write_json(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-real-minimal-atomic-eval", action="store_true")
    args = parser.parse_args()
    try:
        report = run_experiment(
            args.root,
            args.output,
            confirm_real_provider_eval=args.confirm_real_minimal_atomic_eval,
        )
    except Exception as exc:  # noqa: BLE001 - never rerun whole trials
        print(f"Minimal Atomic evaluation stopped: {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps({"status": report["status"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

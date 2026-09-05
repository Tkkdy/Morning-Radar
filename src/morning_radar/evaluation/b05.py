"""Historical architecture comparison and repeatable B0.5 budget sweep."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from morning_radar.ai import AIBudget, FakeAIProvider
from morning_radar.candidates import admit_candidates, triage_candidates
from morning_radar.evaluation.legacy import preselect_ai_candidates
from morning_radar.evidence import EvidenceFetchResult
from morning_radar.models import RawItem, Story
from morning_radar.pipeline import MorningRadarPipeline
from morning_radar.processing import (
    filter_news_window,
    filter_story_candidate_inputs,
    story_evidence_integrity_violations,
)
from morning_radar.processing.deduplicate import deduplicate_items
from morning_radar.storage import write_json


@dataclass(frozen=True, slots=True)
class ResourceEnvelope:
    maximum_calls: int
    maximum_input_characters: int
    maximum_batch_items: int
    maximum_story_candidates: int
    maximum_investigations: int


class _OfflineEvidenceFetcher:
    """Deterministic HTTP boundary substitute; pipeline resolution stays unchanged."""

    def fetch(self, url: str) -> EvidenceFetchResult:
        return EvidenceFetchResult(
            requested_url=url,
            final_url=url,
            content_type="text/plain",
            text="Offline replay of the already collected destination URL.",
            canonical_url=None,
            redirect_chain=(),
            response_bytes=58,
        )


def _load_raw(
    root: Path, replay_date: date
) -> tuple[list[RawItem], datetime, dict[str, Any]]:
    raw = TypeAdapter(list[RawItem]).validate_json(
        (root / f"data/raw/{replay_date}.json").read_text(encoding="utf-8")
    )
    brief = json.loads(
        (root / f"data/briefs/{replay_date}.json").read_text(encoding="utf-8")
    )
    return (
        raw,
        datetime.fromisoformat(brief["generated_at"].replace("Z", "+00:00")),
        dict(brief.get("run_stats", {})),
    )


def _labels(root: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (root / "fixtures/b05_golden_cases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


def _useful_historical_ids(root: Path) -> set[str]:
    return {
        str(label["historical_raw_item_id"])
        for label in _labels(root)
        if label.get("useful") and label.get("historical_raw_item_id")
    }


def _proxy_cost(input_characters: int, logical_calls: int) -> float:
    """Trackable fallback when a Provider does not report actual token cost."""
    estimated_tokens = math.ceil(input_characters / 4)
    return round(estimated_tokens * 0.0000003 + logical_calls * 0.0001, 6)


def _protected_characters(maximum: int) -> dict[str, int]:
    configured = {
        "story": 14_000,
        "editorial": 6_000,
        "continuity": 6_000,
        "tendency": 11_000,
        "brief": 7_000,
    }
    if maximum >= 120_000:
        return configured
    scale = maximum * 0.4 / sum(configured.values())
    return {stage: math.floor(value * scale) for stage, value in configured.items()}


def full_offline_replay(
    root: Path,
    replay_date: date,
    envelope: ResourceEnvelope,
) -> dict[str, Any]:
    """Run production orchestration with only external AI/HTTP replaced."""
    raw, now, _ = _load_raw(root, replay_date)
    budget = AIBudget(
        maximum_calls=envelope.maximum_calls,
        maximum_input_characters=envelope.maximum_input_characters,
        maximum_items=envelope.maximum_batch_items,
        protected_minimums={
            "triage": 1,
            "story": 4,
            "editorial": 1,
            "continuity": 1,
            "tendency": 1,
            "brief": 1,
        },
        protected_input_minimums=_protected_characters(
            envelope.maximum_input_characters
        ),
    )
    pipeline = MorningRadarPipeline(root)
    pipeline.app = pipeline.app.model_copy(
        update={
            "maximum_ai_calls": envelope.maximum_calls,
            "maximum_ai_input_characters": envelope.maximum_input_characters,
            "maximum_triage_batch_items": envelope.maximum_batch_items,
            "maximum_story_candidates": envelope.maximum_story_candidates,
            "maximum_investigations": envelope.maximum_investigations,
        }
    )
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="morning-radar-b05-") as directory:
        output_root = Path(directory)
        history_root = output_root / "history"
        for category in ("stories", "continuity", "tendencies"):
            source = root / "data" / category
            destination = history_root / "data" / category
            destination.mkdir(parents=True, exist_ok=True)
            for path in source.glob("*.json"):
                try:
                    artifact_date = date.fromisoformat(path.stem)
                except ValueError:
                    continue
                if artifact_date < replay_date:
                    shutil.copy2(path, destination / path.name)
        brief = pipeline.run(
            dry_run=True,
            notify=False,
            offline_raw_items=raw,
            offline_provider=FakeAIProvider(budget=budget),
            offline_now=now,
            offline_evidence_fetcher=_OfflineEvidenceFetcher(),  # type: ignore[arg-type]
            offline_output_root=output_root,
            offline_history_root=history_root,
        )
        stories = TypeAdapter(list[Story]).validate_json(
            (output_root / f"data/stories/{replay_date}.json").read_text(
                encoding="utf-8"
            )
        )
    violations = [
        violation
        for story in stories
        for violation in story_evidence_integrity_violations(story)
    ]
    return {
        "pipeline": "MorningRadarPipeline.run",
        "logical_calls": budget.calls_used,
        "input_characters": budget.input_characters_used,
        "maximum_calls": budget.maximum_calls,
        "maximum_input_characters": budget.maximum_input_characters,
        "within_hard_cap": (
            budget.calls_used <= budget.maximum_calls
            and budget.input_characters_used <= budget.maximum_input_characters
        ),
        "stage_calls": dict(budget.stage_calls),
        "stage_input_characters": dict(budget.stage_input_characters),
        "stories": len(stories),
        "evidence_integrity_violations": len(violations),
        "integrity_details": violations,
        "runtime_seconds": round(time.perf_counter() - started, 6),
        "run_stats": dict(brief.run_stats),
    }


def replay_architectures(
    root: Path,
    replay_date: date,
    envelope: ResourceEnvelope,
) -> dict[str, Any]:
    raw, now, historical_stats = _load_raw(root, replay_date)
    recent = filter_news_window(raw, now=now, hours=24)
    eligible, suppressed = filter_story_candidate_inputs(
        recent, market_movement_threshold=0.03
    )
    useful_ids = _useful_historical_ids(root)

    # Frozen legacy replay rule; intentionally isolated from the production runtime.
    legacy_inputs = [
        item
        for item in eligible
        if item.source_role.value != "upstream_discovery"
        and item.source_role.value != "practitioner"
    ]
    legacy_unique = deduplicate_items(legacy_inputs)
    legacy_limit = min(
        envelope.maximum_batch_items,
        max(0, envelope.maximum_calls - 7) * 2 // 5,
    )
    legacy_selected = preselect_ai_candidates(
        legacy_unique, maximum_items=legacy_limit
    )
    legacy_ids = {item.id for item in legacy_selected}
    legacy_cap_ids = {item.id for item in legacy_unique} - legacy_ids
    legacy_chars = len(
        json.dumps(
            [item.model_dump(mode="json") for item in legacy_selected],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )

    admitted = admit_candidates(eligible, now=now)
    started = time.perf_counter()
    result = triage_candidates(
        admitted,
        provider=FakeAIProvider(),
        maximum_batch_items=envelope.maximum_batch_items,
        maximum_input_characters=envelope.maximum_input_characters,
    )
    runtime = time.perf_counter() - started
    triaged_ids = {
        raw_id
        for candidate in result.candidates
        if candidate.semantic_disposition is not None
        for raw_id in candidate.raw_item_ids
    }
    must_triage_ids = {
        raw_id
        for candidate in result.candidates
        if candidate.must_triage
        for raw_id in candidate.raw_item_ids
    }
    new_chars = int(result.stats["candidate_triage_input_characters"])
    new_calls = math.ceil(
        int(result.stats["candidate_triaged"]) / envelope.maximum_batch_items
    )
    full_replay = full_offline_replay(root, replay_date, envelope)

    def recall(values: set[str]) -> float:
        return len(values & useful_ids) / len(useful_ids) if useful_ids else 1.0

    return {
        "date": str(replay_date),
        "resource_envelope": asdict(envelope),
        "input": {
            "raw": len(raw),
            "recent": len(recent),
            "eligible": len(eligible),
            "routine_suppressed": suppressed,
        },
        "historical_actual_usage": {
            "logical_ai_calls": historical_stats.get("logical_ai_calls"),
            "network_ai_requests": historical_stats.get("network_ai_requests"),
            "ai_input_characters": historical_stats.get("ai_input_characters"),
            "prompt_tokens": historical_stats.get("ai_prompt_tokens"),
            "completion_tokens": historical_stats.get("ai_completion_tokens"),
            "reasoning_tokens": historical_stats.get("ai_reasoning_tokens"),
            "actual_cost": None,
        },
        "legacy": {
            "semantic_inputs": len(legacy_selected),
            "semantic_raw_ids": sorted(legacy_ids),
            "preselection_cap_raw_ids": sorted(legacy_cap_ids),
            "elimination_reason": "PRESELECTION_CAP",
            "major_event_recall": recall(legacy_ids),
            "logical_calls": 1 if legacy_selected else 0,
            "input_characters": legacy_chars,
            "proxy_cost": _proxy_cost(legacy_chars, 1 if legacy_selected else 0),
        },
        "b05": {
            **result.stats,
            "semantic_raw_ids": sorted(triaged_ids),
            "must_triage_raw_ids": sorted(must_triage_ids),
            "major_event_recall": recall(triaged_ids),
            "logical_calls": new_calls,
            "input_characters": new_chars,
            "proxy_cost": _proxy_cost(new_chars, new_calls),
            "runtime_seconds": round(runtime, 6),
            "evidence_integrity_violations": full_replay[
                "evidence_integrity_violations"
            ],
            "full_pipeline": full_replay,
        },
    }


def budget_sweep(root: Path, replay_date: date) -> list[dict[str, Any]]:
    envelopes = [
        ResourceEnvelope(50, 20_000, 20, 6, 2),
        ResourceEnvelope(50, 40_000, 30, 10, 3),
        ResourceEnvelope(50, 80_000, 40, 14, 4),
        ResourceEnvelope(50, 120_000, 40, 17, 5),
    ]
    rows: list[dict[str, Any]] = []
    previous_cost = 0.0
    previous_recall = 0.0
    for envelope in envelopes:
        report = replay_architectures(root, replay_date, envelope)
        b05 = report["b05"]
        cost = float(b05["proxy_cost"])
        recall = float(b05["major_event_recall"])
        useful_recoveries = max(0.0, recall - previous_recall)
        additional_cost = max(0.0, cost - previous_cost)
        rows.append(
            {
                "envelope": asdict(envelope),
                "major_event_recall": recall,
                "reader_precision": "NOT_EVALUATED",
                "evidence_integrity_violations": b05["evidence_integrity_violations"],
                "logical_calls": b05["logical_calls"],
                "input_characters": b05["input_characters"],
                "actual_cost": None,
                "proxy_cost": cost,
                "http_fetches": b05["full_pipeline"]["run_stats"].get(
                    "evidence_http_fetches", 0
                ),
                "investigation_workload": b05["full_pipeline"]["run_stats"].get(
                    "investigation_recommended", 0
                ),
                "maximum_investigations": envelope.maximum_investigations,
                "runtime_seconds": b05["runtime_seconds"],
                "additional_cost": round(additional_cost, 6),
                "additional_useful_recoveries": useful_recoveries,
                "marginal_cost_per_useful_recovery": (
                    round(additional_cost / useful_recoveries, 6)
                    if useful_recoveries
                    else None
                ),
                "invalid_candidate_workload": "NOT_EVALUATED",
                "unlabelled_candidate_workload": max(
                    0, int(b05["candidate_triaged"]) - len(_useful_historical_ids(root))
                ),
            }
        )
        previous_cost = cost
        previous_recall = recall
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--date", type=date.fromisoformat, default=date(2026, 8, 22))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    envelope = ResourceEnvelope(50, 120_000, 40, 17, 4)
    report = {
        "same_budget_comparison": replay_architectures(args.root, args.date, envelope),
        "budget_sweep": budget_sweep(args.root, args.date),
        "notes": {
            "provider": "FakeAIProvider",
            "semantic_quality": "Run separately with a configured production Provider.",
            "actual_cost": "Unavailable in offline replay; proxy retained.",
        },
    }
    if args.output:
        write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

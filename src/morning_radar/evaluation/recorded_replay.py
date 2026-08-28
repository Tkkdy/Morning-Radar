"""Replay recorded semantic decisions through current deterministic guards only."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from morning_radar.candidates import (
    admit_candidates,
    apply_build_eligibility_guard,
    apply_freshness_guard,
    attach_official_source_entities,
)
from morning_radar.models import Candidate, RawItem, SemanticDisposition
from morning_radar.processing.story_builder import _deterministic_claim_subject
from morning_radar.settings import SourceConfig, load_model_list
from morning_radar.storage import load_models, read_json


class RecordedReplayError(RuntimeError):
    """The saved artifacts cannot support an honest deterministic replay."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RecordedReplayError(f"NOT_REPLAYABLE: missing {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _source_entities(root: Path) -> dict[str, str]:
    sources = load_model_list(root / "config/sources.yaml", "sources", SourceConfig)
    return {
        source.id: source.entity
        for source in sources
        if source.official and source.entity
    }


def _recorded_semantic_candidates(
    *,
    root: Path,
    run_directory: Path,
    evaluation_date: date,
    rows: list[dict[str, Any]],
) -> list[Candidate]:
    persisted_path = run_directory / "data/candidates" / f"{evaluation_date}.json"
    raw_path = root / "data/raw" / f"{evaluation_date}.json"
    if not persisted_path.exists() or not raw_path.exists():
        raise RecordedReplayError(
            "NOT_REPLAYABLE: persisted Candidate or original RawItem data is missing"
        )
    persisted = load_models(persisted_path, Candidate)
    raw = load_models(raw_path, RawItem)
    eligible_raw_ids = {
        raw_id for candidate in persisted for raw_id in candidate.raw_item_ids
    }
    enriched_raw = attach_official_source_entities(raw, _source_entities(root))
    admitted = admit_candidates(
        [item for item in enriched_raw if item.id in eligible_raw_ids],
        now=persisted[0].created_at,
    )
    admitted_by_id = {candidate.id: candidate for candidate in admitted}
    row_by_id = {row["candidate_id"]: row for row in rows}
    replayed: list[Candidate] = []
    for recorded in persisted:
        base = admitted_by_id.get(recorded.id)
        row = row_by_id.get(recorded.id)
        model_disposition = row.get("model_semantic_disposition") if row else None
        if base is None or model_disposition is None:
            raise RecordedReplayError(
                f"NOT_REPLAYABLE: missing admission or model disposition for {recorded.id}"
            )
        payload = recorded.model_dump()
        payload.update(
            {
                "raw_item_ids": base.raw_item_ids,
                "entity_names": base.entity_names,
                "product_names": base.product_names,
                "topic_names": base.topic_names,
                "evidence": base.evidence,
                "semantic_disposition": SemanticDisposition(model_disposition),
            }
        )
        replayed.append(Candidate.model_validate(payload))
    return replayed


def _subject_recovery(
    candidates: list[Candidate], story_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    recoveries: list[dict[str, Any]] = []
    for row in story_rows:
        if row.get("story_result") != "STORY_REJECTED":
            continue
        candidate = candidates_by_id.get(row["candidate_id"])
        if candidate is None:
            continue
        evidence_by_id = {
            evidence.evidence_id: evidence for evidence in candidate.evidence
        }
        subjects: list[str | None] = []
        for support in row.get("claim_supports", []):
            selected = [
                evidence_by_id[evidence_id]
                for evidence_id in support.get("evidence_ids", [])
                if evidence_id in evidence_by_id
            ]
            subjects.append(
                _deterministic_claim_subject(
                    support["claim"],
                    candidate_entities=candidate.entity_names,
                    evidence=selected,
                )
            )
        recoveries.append(
            {
                "candidate_id": candidate.id,
                "title": candidate.hypothesis,
                "derived_subjects": subjects,
                "all_subjects_derived": bool(subjects) and all(subjects),
                "original_rejection_reason": row.get("rejection_reason"),
            }
        )
    return recoveries


def run_recorded_replay(
    *, root: Path, run_directory: Path, evaluation_date: date
) -> dict[str, Any]:
    """Replay saved model dispositions without constructing or calling a Provider."""
    root = root.resolve()
    run_directory = run_directory.resolve()
    summary_path = run_directory / "summary.json"
    if not summary_path.exists():
        raise RecordedReplayError(f"NOT_REPLAYABLE: missing {summary_path}")
    summary = read_json(summary_path)
    environment = summary.get("environment", {})
    if summary.get("status") != "COMPLETED" or environment.get("provider") != "deepseek":
        raise RecordedReplayError(
            "NOT_REPLAYABLE: artifacts are not a completed DeepSeek evaluation"
        )
    rows = _read_jsonl(run_directory / "candidates.jsonl")
    stories = _read_jsonl(run_directory / "stories.jsonl")
    replayed_model = _recorded_semantic_candidates(
        root=root,
        run_directory=run_directory,
        evaluation_date=evaluation_date,
        rows=rows,
    )
    guarded = apply_freshness_guard(apply_build_eligibility_guard(replayed_model))
    baseline_by_id = {row["candidate_id"]: row["semantic_disposition"] for row in rows}
    downgraded = [
        {
            "candidate_id": candidate.id,
            "title": candidate.hypothesis,
            "reason_codes": [code.value for code in candidate.reason_codes],
        }
        for candidate in guarded
        if baseline_by_id.get(candidate.id) == "build"
        and candidate.semantic_disposition is SemanticDisposition.INVESTIGATE
    ]
    return {
        "status": "REPLAYED_OFFLINE",
        "provider_calls": 0,
        "evaluation_date": str(evaluation_date),
        "recorded_git_sha": environment.get("git_sha"),
        "baseline_distribution": dict(Counter(baseline_by_id.values())),
        "replayed_distribution": dict(
            Counter(
                candidate.semantic_disposition.value
                for candidate in guarded
                if candidate.semantic_disposition is not None
            )
        ),
        "build_to_investigate": downgraded,
        "subject_recovery": _subject_recovery(guarded, stories),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    try:
        report = run_recorded_replay(
            root=args.root,
            run_directory=args.run_directory,
            evaluation_date=args.date,
        )
    except RecordedReplayError as exc:
        print(str(exc))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

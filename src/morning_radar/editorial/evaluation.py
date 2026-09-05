"""One-shot held-out evaluation for the frozen production editorial prompt."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from morning_radar.ai import AIBudget, DeepSeekProvider
from morning_radar.ai.provider import AIProvider
from morning_radar.editorial.evaluator import validate_editorial_batch
from morning_radar.editorial.models import EditorialDecision, EditorialDecisionBatch, Placement
from morning_radar.models import SourceRole, StatementType, Story, StorySourceRef
from morning_radar.storage import write_json


@dataclass(frozen=True, slots=True)
class EvalCase:
    id: str
    scenario: str
    evidence_kind: str
    exact_placements: tuple[Placement, ...]
    adjacent_placements: tuple[Placement, ...]
    expected_reasons: frozenset[str]
    expected_retain: bool
    p0_rule: str


@dataclass(frozen=True, slots=True)
class RateGate:
    metric_key: str
    count_key: str
    threshold_numerator: int
    threshold_denominator: int


RATE_GATES = (
    RateGate("exact_or_adjacent_rate", "exact_or_adjacent", 4, 5),
    RateGate("reason_agreement_rate", "reason_agreement", 3, 4),
    RateGate("retention_agreement_rate", "retention_agreement", 4, 5),
)


def load_eval_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        cases.append(
            EvalCase(
                id=raw["id"],
                scenario=raw["scenario"],
                evidence_kind=raw["evidence_kind"],
                exact_placements=tuple(Placement(value) for value in raw["exact_placements"]),
                adjacent_placements=tuple(Placement(value) for value in raw["adjacent_placements"]),
                expected_reasons=frozenset(raw["expected_reasons"]),
                expected_retain=raw["expected_retain"],
                p0_rule=raw["p0_rule"],
            )
        )
    return cases


def _source_ref(
    case: EvalCase,
    suffix: str,
    *,
    source_role: SourceRole,
    statement_type: StatementType,
) -> StorySourceRef:
    now = datetime(2026, 8, 23, 0, 0, tzinfo=UTC)
    return StorySourceRef(
        raw_item_id=f"raw-{case.id}-{suffix}",
        title=case.scenario,
        source_name=f"Held-out {suffix}",
        source_type="rss",
        url=f"https://eval.invalid/{case.id}/{suffix}",
        published_at=now,
        fetched_at=now,
        source_role=source_role,
        statement_type=statement_type,
    )


def _evidence_refs(case: EvalCase) -> list[StorySourceRef]:
    official = _source_ref(
        case,
        "official",
        source_role=SourceRole.OFFICIAL_PRIMARY,
        statement_type=StatementType.FACTUAL_ANNOUNCEMENT,
    )
    practitioner_test = _source_ref(
        case,
        "independent-test",
        source_role=SourceRole.PRACTITIONER,
        statement_type=StatementType.TEST_EXPERIMENT,
    )
    practitioner_observation = _source_ref(
        case,
        "practitioner",
        source_role=SourceRole.PRACTITIONER,
        statement_type=StatementType.FIRSTHAND_OBSERVATION,
    )
    editorial = _source_ref(
        case,
        "secondary",
        source_role=SourceRole.EDITORIAL,
        statement_type=StatementType.ANALYSIS_JUDGEMENT,
    )
    return {
        "official_objective_change": [official],
        "official_plus_independent_test": [official, practitioner_test],
        "independent_test": [practitioner_test, editorial],
        "official_plus_practitioner_observation": [official, practitioner_observation],
        "multi_source_claim": [official, editorial],
    }[case.evidence_kind]


def stories_for_cases(cases: list[EvalCase]) -> list[Story]:
    now = datetime(2026, 8, 23, 0, 0, tzinfo=UTC)
    stories: list[Story] = []
    for case in cases:
        refs = _evidence_refs(case)
        stories.append(
            Story(
                id=case.id,
                canonical_title=case.scenario,
                category="ai_and_open_source",
                published_at=now,
                updated_at=now,
                source_item_ids=[ref.raw_item_id for ref in refs],
                source_urls=[ref.url for ref in refs],
                primary_source_url=refs[0].url,
                source_refs=refs,
                facts=[case.scenario],
                relevance_score=0.5,
                importance_score=0.5,
                novelty_score=0.5,
                credibility_score=0.5,
            )
        )
    return stories


def _is_p0(case: EvalCase, decision: EditorialDecision) -> bool:
    if case.p0_rule == "none":
        return False
    if case.p0_rule == "major_developer_change_not_dropped":
        return decision.placement in {Placement.DROP, Placement.ONE_LINER}
    if case.p0_rule == "minor_delta_not_promoted":
        return decision.placement in {Placement.TOP, Placement.STORY}
    if case.p0_rule == "generic_policy_not_top":
        return decision.placement is Placement.TOP
    if case.p0_rule == "persistent_consequence_not_dropped":
        return decision.placement in {Placement.DROP, Placement.ONE_LINER}
    if case.p0_rule == "weak_signal_not_top":
        return decision.placement is Placement.TOP
    raise ValueError(f"Unknown P0 rule: {case.p0_rule}")


def score_batch(cases: list[EvalCase], batch: EditorialDecisionBatch) -> dict[str, Any]:
    decisions = {decision.story_id: decision for decision in batch.decisions}
    rows: list[dict[str, Any]] = []
    for case in cases:
        decision = decisions[case.id]
        exact = decision.placement in case.exact_placements
        adjacent = not exact and decision.placement in case.adjacent_placements
        reason_agreement = any(reason in decision.reason for reason in case.expected_reasons)
        retention_agreement = decision.retain_for_trends is case.expected_retain
        p0 = _is_p0(case, decision)
        rows.append(
            {
                "id": case.id,
                "placement": decision.placement.value,
                "exact": exact,
                "adjacent": adjacent,
                "exact_or_adjacent": exact or adjacent,
                "reason_agreement": reason_agreement,
                "retention_agreement": retention_agreement,
                "p0": p0,
            }
        )
    total = len(rows)

    def count(key: str) -> int:
        return sum(bool(row[key]) for row in rows)

    return {
        "total": total,
        "exact": count("exact"),
        "adjacent": count("adjacent"),
        "exact_or_adjacent": count("exact_or_adjacent"),
        "reason_agreement": count("reason_agreement"),
        "retention_agreement": count("retention_agreement"),
        "p0_count": count("p0"),
        "exact_rate": count("exact") / total,
        "exact_or_adjacent_rate": count("exact_or_adjacent") / total,
        "reason_agreement_rate": count("reason_agreement") / total,
        "retention_agreement_rate": count("retention_agreement") / total,
        "cases": rows,
    }


def evaluate_quality_gate(metrics: dict[str, Any]) -> dict[str, Any]:
    total = int(metrics["total"])
    if total <= 0:
        raise ValueError("Editorial Eval quality gate requires at least one case")
    gates: dict[str, dict[str, Any]] = {}
    for gate in RATE_GATES:
        actual_count = int(metrics[gate.count_key])
        passed = actual_count * gate.threshold_denominator >= total * gate.threshold_numerator
        gates[gate.metric_key] = {
            "actual": actual_count / total,
            "actual_count": actual_count,
            "total": total,
            "threshold": gate.threshold_numerator / gate.threshold_denominator,
            "passed": passed,
        }
    p0_count = int(metrics["p0_count"])
    gates["p0_count"] = {
        "actual": p0_count,
        "threshold": 0,
        "passed": p0_count == 0,
    }
    return {
        "passed": all(gate["passed"] for gate in gates.values()),
        "gates": gates,
    }


def format_quality_gate_summary(metrics: dict[str, Any]) -> str:
    quality_gate = metrics["quality_gate"]
    gates = quality_gate["gates"]
    lines = [
        "## Editorial held-out Eval quality Gate",
        "",
        "| Gate | Actual | Threshold | Result |",
        "| --- | ---: | ---: | --- |",
    ]
    for metric_key in (
        "exact_or_adjacent_rate",
        "reason_agreement_rate",
        "retention_agreement_rate",
    ):
        gate = gates[metric_key]
        lines.append(
            f"| {metric_key} | {gate['actual']:.1%} | >= {gate['threshold']:.0%} | "
            f"{'PASS' if gate['passed'] else 'FAIL'} |"
        )
    p0_gate = gates["p0_count"]
    lines.append(
        f"| p0_count | {p0_gate['actual']} | = 0 | {'PASS' if p0_gate['passed'] else 'FAIL'} |"
    )
    lines.extend(
        [
            "",
            f"Overall: {'PASS' if quality_gate['passed'] else 'FAIL'}",
        ]
    )
    return "\n".join(lines)


def run_eval(
    project_root: Path,
    output_dir: Path,
    *,
    provider: AIProvider | None = None,
) -> dict[str, Any]:
    cases = load_eval_cases(project_root / "tests/fixtures/editorial_eval_cases.jsonl")
    stories = stories_for_cases(cases)
    active_provider = provider or DeepSeekProvider.from_environment(
        budget=AIBudget(maximum_calls=1, maximum_input_characters=120000, maximum_items=20),
        prompt_dir=project_root / "prompts",
    )
    batch = active_provider.evaluate_editorial(stories)
    write_json(output_dir / "raw_model_output.json", batch.model_dump(mode="json"))
    validated = validate_editorial_batch(batch, stories)
    metrics = score_batch(cases, validated)
    metrics["quality_gate"] = evaluate_quality_gate(metrics)
    write_json(
        output_dir / "validated_results.json",
        {
            "model": os.getenv("DEEPSEEK_MODEL", ""),
            "profile_version": "1.0",
            "evaluated_at": datetime.now(UTC).isoformat(),
            "decisions": validated.model_dump(mode="json")["decisions"],
            "metrics": metrics,
        },
    )
    write_json(output_dir / "metrics.json", metrics)
    return metrics


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one frozen held-out Editorial Eval")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    metrics = run_eval(args.project_root.resolve(), args.output_dir.resolve())
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    summary = format_quality_gate_summary(metrics)
    print(summary)
    github_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if github_summary:
        with Path(github_summary).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(summary)
            handle.write("\n")
    return 0 if metrics["quality_gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

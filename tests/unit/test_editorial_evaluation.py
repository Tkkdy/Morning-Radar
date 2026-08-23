import hashlib
import json
from pathlib import Path

import pytest

import morning_radar.editorial.evaluation as evaluation_module
from morning_radar.editorial.evaluation import (
    evaluate_quality_gate,
    format_quality_gate_summary,
    load_eval_cases,
    main,
    run_eval,
    score_batch,
    stories_for_cases,
)
from morning_radar.editorial.models import (
    DecisionReason,
    EditorialDecision,
    EditorialDecisionBatch,
    FactStatus,
    Treatment,
)


def quality_metrics(
    *,
    total: int = 20,
    exact_or_adjacent: int = 16,
    reason_agreement: int = 15,
    retention_agreement: int = 16,
    p0_count: int = 0,
) -> dict[str, object]:
    return {
        "total": total,
        "exact_or_adjacent": exact_or_adjacent,
        "reason_agreement": reason_agreement,
        "retention_agreement": retention_agreement,
        "p0_count": p0_count,
    }


def batch_for_cases(*, fail_quality_gate: bool = False) -> EditorialDecisionBatch:
    cases = load_eval_cases(Path("tests/fixtures/editorial_eval_cases.jsonl"))
    decisions: list[EditorialDecision] = []
    for index, case in enumerate(cases):
        placement = case.exact_placements[0]
        if fail_quality_gate and index == 0:
            placement = evaluation_module.Placement.DROP
        treatment = {
            "TOP": Treatment.SHORT_NEWS,
            "STORY": Treatment.DEEP_STORY,
            "NEWS": Treatment.SHORT_NEWS,
            "ONE-LINER": Treatment.ONE_LINER,
            "DROP": Treatment.HIDDEN,
        }[placement.value]
        decisions.append(
            EditorialDecision(
                story_id=case.id,
                placement=placement,
                treatment=treatment,
                reader_value=2,
                evidence_value=2,
                fact_status=FactStatus.CLAIM,
                editorial_confidence=0.8,
                news_delta="测试变化。",
                why_now="测试原因。",
                decision_reasons=[next(iter(case.expected_reasons))],
                retain_for_trends=case.expected_retain,
                trend_links=["testable-evolution"] if case.expected_retain else [],
                uncertainty="测试不确定性。",
            )
        )
    return EditorialDecisionBatch(decisions=decisions)


class StaticEditorialProvider:
    def __init__(self, batch: EditorialDecisionBatch) -> None:
        self.batch = batch

    def evaluate_editorial(self, stories):
        del stories
        return self.batch


def test_held_out_cases_are_complete_and_do_not_expose_expected_labels_to_stories() -> None:
    cases = load_eval_cases(Path("tests/fixtures/editorial_eval_cases.jsonl"))
    stories = stories_for_cases(cases)
    assert len(cases) == len(stories) == 11
    assert [story.id for story in stories] == [case.id for case in cases]
    assert all("expected" not in story.model_dump_json() for story in stories)


def test_original_e01_to_e08_cases_remain_frozen() -> None:
    lines = Path("tests/fixtures/editorial_eval_cases.jsonl").read_text(
        encoding="utf-8"
    ).splitlines(keepends=True)
    frozen_bytes = "".join(lines[:8]).replace("\r\n", "\n").encode()

    assert hashlib.sha256(frozen_bytes).hexdigest() == (
        "57e499ba9eb55f6e4c609f379e9f6d359fd1a4bc206cde1c86642822b8ad8b73"
    )


def test_eval_cases_cover_retention_boundaries() -> None:
    cases = load_eval_cases(Path("tests/fixtures/editorial_eval_cases.jsonl"))

    assert sum(case.expected_retain for case in cases) >= 4
    assert sum(not case.expected_retain for case in cases) >= 4
    assert any(
        not case.expected_retain
        and bool({"TOP", "STORY"} & {item.value for item in case.exact_placements})
        for case in cases
    )
    assert any(
        case.expected_retain
        and bool({"ONE-LINER", "DROP"} & {item.value for item in case.exact_placements})
        for case in cases
    )


def test_score_batch_reports_exact_adjacent_reason_retention_and_p0() -> None:
    cases = load_eval_cases(Path("tests/fixtures/editorial_eval_cases.jsonl"))
    placements = [
        "TOP",
        "NEWS",
        "STORY",
        "TOP",
        "STORY",
        "NEWS",
        "DROP",
        "ONE-LINER",
        "STORY",
        "DROP",
        "DROP",
    ]
    decisions = []
    for case, placement in zip(cases, placements, strict=True):
        treatment = {
            "TOP": Treatment.SHORT_NEWS,
            "STORY": Treatment.DEEP_STORY,
            "NEWS": Treatment.SHORT_NEWS,
            "ONE-LINER": Treatment.ONE_LINER,
            "DROP": Treatment.HIDDEN,
        }[placement]
        decisions.append(
            EditorialDecision(
                story_id=case.id,
                placement=placement,
                treatment=treatment,
                reader_value=2,
                evidence_value=2,
                fact_status=FactStatus.CLAIM,
                editorial_confidence=0.8,
                news_delta="测试变化。",
                why_now="测试原因。",
                decision_reasons=[DecisionReason.MINOR_NEWS_DELTA],
                retain_for_trends=case.expected_retain,
                trend_links=["test-trend"] if case.expected_retain else [],
                uncertainty="测试不确定性。",
            )
        )
    metrics = score_batch(cases, EditorialDecisionBatch(decisions=decisions))
    assert metrics["total"] == 11
    assert metrics["exact"] == 6
    assert metrics["adjacent"] == 3
    assert metrics["p0_count"] == 2
    assert metrics["retention_agreement"] == 11


def test_quality_gate_passes_when_all_thresholds_are_met() -> None:
    result = evaluate_quality_gate(quality_metrics())

    assert result["passed"] is True
    assert all(gate["passed"] for gate in result["gates"].values())


@pytest.mark.parametrize(
    "metrics",
    [
        quality_metrics(exact_or_adjacent=15),
        quality_metrics(reason_agreement=14),
        quality_metrics(retention_agreement=15),
        quality_metrics(p0_count=1),
    ],
)
def test_quality_gate_fails_when_any_requirement_is_missed(
    metrics: dict[str, object],
) -> None:
    assert evaluate_quality_gate(metrics)["passed"] is False


def test_quality_gate_uses_integer_counts_at_float_boundaries() -> None:
    passing = quality_metrics()
    passing["exact_or_adjacent_rate"] = 0.7999999999999999
    failing = quality_metrics(exact_or_adjacent=15)
    failing["exact_or_adjacent_rate"] = 0.8000000000000002

    assert evaluate_quality_gate(passing)["passed"] is True
    assert evaluate_quality_gate(failing)["passed"] is False


@pytest.mark.parametrize(("passed", "expected_exit"), [(True, 0), (False, 1)])
def test_cli_exit_code_reflects_quality_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    passed: bool,
    expected_exit: int,
) -> None:
    metrics = quality_metrics()
    metrics["quality_gate"] = evaluate_quality_gate(metrics)
    metrics["quality_gate"]["passed"] = passed
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setattr(evaluation_module, "run_eval", lambda *args, **kwargs: metrics)

    assert main(["--output-dir", str(tmp_path / "results")]) == expected_exit
    summary = summary_path.read_text(encoding="utf-8")
    assert "exact_or_adjacent_rate" in summary
    assert ">= 80%" in summary
    assert "p0_count" in summary


def test_failed_quality_gate_still_writes_all_eval_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "editorial-eval"
    metrics = run_eval(
        Path.cwd(),
        output_dir,
        provider=StaticEditorialProvider(batch_for_cases(fail_quality_gate=True)),
    )

    assert metrics["quality_gate"]["passed"] is False
    for filename in (
        "raw_model_output.json",
        "validated_results.json",
        "metrics.json",
    ):
        path = output_dir / filename
        assert path.is_file()
        assert json.loads(path.read_text(encoding="utf-8"))


def test_quality_gate_summary_reports_all_actuals_and_thresholds() -> None:
    metrics = quality_metrics()
    metrics["quality_gate"] = evaluate_quality_gate(metrics)

    summary = format_quality_gate_summary(metrics)

    assert "80.0%" in summary
    assert "75.0%" in summary
    assert "p0_count" in summary
    assert "Overall: PASS" in summary

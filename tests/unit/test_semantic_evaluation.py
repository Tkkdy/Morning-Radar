from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from morning_radar.ai import AIBudget, FakeAIProvider
from morning_radar.evaluation import semantic
from morning_radar.evaluation.semantic import (
    EvaluationSafetyError,
    SemanticShadowProvider,
    deepseek_configuration_from_environment,
    run_semantic_evaluation,
)
from morning_radar.pipeline import MorningRadarPipeline

ROOT = Path(__file__).resolve().parents[2]
EVALUATION_DATE = date(2026, 8, 22)


class SpyFakeProvider(FakeAIProvider):
    def __init__(self, *, budget: AIBudget) -> None:
        super().__init__(budget=budget)
        self.triage_calls = 0
        self.editorial_calls = 0

    def triage_candidates(self, candidates):
        self.triage_calls += 1
        return super().triage_candidates(candidates)

    def evaluate_editorial(self, stories):
        self.editorial_calls += 1
        return super().evaluate_editorial(stories)


def test_semantic_shadow_routes_tasks_and_shares_one_budget() -> None:
    budget = AIBudget(50, 120_000, 40)
    semantic_provider = SpyFakeProvider(budget=budget)
    downstream_provider = SpyFakeProvider(budget=budget)
    provider = SemanticShadowProvider(
        semantic_provider=semantic_provider,
        downstream_provider=downstream_provider,
        budget=budget,
    )

    provider.triage_candidates([])
    provider.evaluate_editorial([])

    assert semantic_provider.triage_calls == 1
    assert semantic_provider.editorial_calls == 0
    assert downstream_provider.triage_calls == 0
    assert downstream_provider.editorial_calls == 1
    assert provider.budget is semantic_provider.budget is downstream_provider.budget


def test_missing_deepseek_environment_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in semantic.REQUIRED_DEEPSEEK_ENV:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(EvaluationSafetyError) as caught:
        deepseek_configuration_from_environment()

    message = str(caught.value)
    assert "DEEPSEEK_API_KEY" in message
    assert "DEEPSEEK_MODEL" in message
    assert "DEEPSEEK_BASE_URL" in message


def test_real_provider_requires_explicit_confirmation_before_env_or_output(
    tmp_path: Path,
) -> None:
    with pytest.raises(EvaluationSafetyError, match="confirm-real-provider-eval"):
        run_semantic_evaluation(
            root=ROOT,
            evaluation_date=EVALUATION_DATE,
            provider_kind="deepseek",
            output_root=tmp_path,
        )

    assert not (tmp_path / str(EVALUATION_DATE)).exists()


def test_secret_is_redacted_from_diagnostics() -> None:
    secret = "test-secret-value"

    assert secret not in semantic._sanitize(f"Bearer {secret} failed", (secret,))


def test_fake_harness_uses_production_pipeline_and_writes_review_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline_calls: list[dict[str, object]] = []
    integrity_calls: list[str] = []
    original_run = MorningRadarPipeline.run
    original_integrity = semantic.story_evidence_integrity_violations

    def observed_run(self, **kwargs):
        pipeline_calls.append(kwargs)
        assert self.app.maximum_investigations == 0
        return original_run(self, **kwargs)

    def observed_integrity(story):
        integrity_calls.append(story.id)
        return original_integrity(story)

    monkeypatch.setattr(MorningRadarPipeline, "run", observed_run)
    monkeypatch.setattr(semantic, "story_evidence_integrity_violations", observed_integrity)

    summary = run_semantic_evaluation(
        root=ROOT,
        evaluation_date=EVALUATION_DATE,
        provider_kind="fake",
        output_root=tmp_path,
    )
    run_directory = tmp_path / str(EVALUATION_DATE)

    assert summary["status"] == "COMPLETED"
    assert len(pipeline_calls) == 1
    call = pipeline_calls[0]
    assert call["dry_run"] is True
    assert call["notify"] is False
    assert call["offline_raw_items"]
    assert call["offline_now"].isoformat() == "2026-08-21T23:56:14.225732+00:00"
    assert Path(call["offline_output_root"]) == run_directory
    assert not Path(call["offline_history_root"]).is_relative_to(ROOT / "data")
    assert summary["safety_observations"]["evidence_fetch_calls"] == 0
    assert summary["environment"]["evidence_network"] == "DISABLED"
    assert summary["environment"]["notification"] == "DISABLED"
    assert summary["environment"]["production_writes"] == "DISABLED"
    assert summary["safety_observations"]["whole_run_attempts"] == 1
    assert summary["deepseek_golden"]["candidate_admitted"] is True
    assert summary["deepseek_golden"]["entered_semantic_triage"] is True
    assert any(
        "BUILD_DOWNGRADED_EVIDENCE_INSUFFICIENT" in transition["reason_codes"]
        for transition in summary["deepseek_golden"]["trace"]
    )
    assert integrity_calls
    assert len(integrity_calls) == summary["integrity"]["persisted_stories"]
    assert summary["integrity"]["evidence_integrity_violations"] == 0
    assert summary["candidate_funnel"]["investigations_not_executed_for_eval"] > 0
    assert (
        summary["resource_envelope"][
            "provider_prompt_tokens_are_separate_from_serialized_characters"
        ]
        is True
    )

    required = {
        "candidates.jsonl",
        "candidate_review.md",
        "stories.jsonl",
        "story_review.md",
        "semantic_review_sample.md",
        "summary.json",
    }
    assert required.issubset({path.name for path in run_directory.iterdir()})
    review = (run_directory / "candidate_review.md").read_text(encoding="utf-8")
    assert "UNREVIEWED" in review
    assert "CORRECT" not in review
    candidates = [
        json.loads(line)
        for line in (run_directory / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    golden = next(row for row in candidates if semantic.GOLDEN_RAW_ID in row["raw_item_ids"])
    assert golden["model_semantic_disposition"] is not None
    story_rows = [
        json.loads(line)
        for line in (run_directory / "stories.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    rejected = [row for row in story_rows if row["story_result"] == "STORY_REJECTED"]
    assert story_rows
    assert all(row["rejection_reason"] for row in rejected)

    with pytest.raises(EvaluationSafetyError, match="rerun is blocked"):
        run_semantic_evaluation(
            root=ROOT,
            evaluation_date=EVALUATION_DATE,
            provider_kind="fake",
            output_root=tmp_path,
        )
    assert len(pipeline_calls) == 1

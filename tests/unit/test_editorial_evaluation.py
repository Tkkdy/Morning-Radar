from pathlib import Path

from morning_radar.editorial.evaluation import (
    load_eval_cases,
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


def test_held_out_cases_are_complete_and_do_not_expose_expected_labels_to_stories() -> None:
    cases = load_eval_cases(Path("tests/fixtures/editorial_eval_cases.jsonl"))
    stories = stories_for_cases(cases)
    assert len(cases) == len(stories) == 8
    assert [story.id for story in stories] == [case.id for case in cases]
    assert all("expected" not in story.model_dump_json() for story in stories)


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
    assert metrics["total"] == 8
    assert metrics["exact"] == 3
    assert metrics["adjacent"] == 3
    assert metrics["p0_count"] == 2
    assert metrics["retention_agreement"] == 8

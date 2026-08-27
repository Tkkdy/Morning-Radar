import pytest

from morning_radar.ai import AIBudget, AIBudgetExceeded


def test_protected_minimum_prevents_early_stage_from_starving_later_work() -> None:
    budget = AIBudget(
        maximum_calls=4,
        maximum_input_characters=10_000,
        maximum_items=40,
        protected_minimums={"triage": 1, "story": 1, "brief": 1},
    )

    budget.consume("triage", item_count=1, stage="triage")
    budget.consume("borrow shared", item_count=1, stage="triage")
    with pytest.raises(AIBudgetExceeded, match="protecting later stages"):
        budget.consume("would starve story or brief", item_count=1, stage="triage")

    budget.consume("story", item_count=1, stage="story")
    budget.consume("brief", item_count=1, stage="brief")


def test_completed_stage_releases_unused_minimum_to_shared_pool() -> None:
    budget = AIBudget(
        maximum_calls=3,
        maximum_input_characters=10_000,
        maximum_items=40,
        protected_minimums={"triage": 1, "story": 1, "brief": 1},
    )

    budget.consume("triage", item_count=1, stage="triage")
    budget.complete_stage("story")
    budget.consume("shared", item_count=1, stage="triage")
    budget.consume("brief", item_count=1, stage="brief")

    assert budget.stage_calls == {"triage": 2, "brief": 1}

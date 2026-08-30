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


def test_character_reserve_prevents_triage_from_starving_later_work() -> None:
    budget = AIBudget(
        maximum_calls=10,
        maximum_input_characters=100,
        maximum_items=40,
        protected_input_minimums={"story": 20, "brief": 20},
    )

    budget.consume("t" * 60, item_count=1, stage="triage")
    with pytest.raises(AIBudgetExceeded, match="character pool"):
        budget.consume("x", item_count=1, stage="triage")

    budget.consume("s" * 20, item_count=1, stage="story")
    budget.consume("b" * 20, item_count=1, stage="brief")


def test_available_input_characters_reflects_live_stage_reservations() -> None:
    budget = AIBudget(
        maximum_calls=10,
        maximum_input_characters=100,
        maximum_items=40,
        protected_input_minimums={"story": 20, "brief": 20},
    )

    assert budget.available_input_characters(stage="triage") == 60
    budget.consume("t" * 25, item_count=1, stage="triage")
    assert budget.available_input_characters(stage="triage") == 35
    budget.complete_stage("story")
    assert budget.available_input_characters(stage="triage") == 55


def test_completed_stage_releases_unused_character_reserve() -> None:
    budget = AIBudget(
        maximum_calls=10,
        maximum_input_characters=100,
        maximum_items=40,
        protected_input_minimums={"story": 30, "brief": 20},
    )

    budget.consume("t" * 50, item_count=1, stage="triage")
    budget.complete_stage("story")
    budget.consume("x" * 30, item_count=1, stage="triage")
    budget.consume("b" * 20, item_count=1, stage="brief")

    assert budget.stage_input_characters == {"triage": 80, "brief": 20}

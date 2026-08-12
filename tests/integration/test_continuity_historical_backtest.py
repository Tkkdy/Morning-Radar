import json
from datetime import date
from pathlib import Path

import pytest

from morning_radar.ai import FakeAIProvider
from morning_radar.continuity.candidates import StoryMemory
from morning_radar.continuity.engine import resolve_daily_continuity
from morning_radar.models import (
    DailyContinuity,
    Story,
    StoryOccurrenceRef,
    WatchEvent,
    WatchEventType,
)
from morning_radar.storage import load_models


def _story(ref: dict[str, str]) -> Story:
    day = date.fromisoformat(ref["date"])
    stories = load_models(Path("data/stories") / f"{day}.json", Story)
    return next(story for story in stories if story.id == ref["story_id"])


@pytest.mark.parametrize(
    "case",
    json.loads(
        Path("fixtures/continuity_golden.json").read_text(encoding="utf-8")
    )["cases"],
    ids=lambda case: case["name"],
)
def test_real_history_golden_relations(case: dict[str, object]) -> None:
    previous_ref = case["previous"]
    current_ref = case["current"]
    previous_date = date.fromisoformat(previous_ref["date"])
    current_date = date.fromisoformat(current_ref["date"])
    previous = _story(previous_ref)
    current = _story(current_ref)

    result = resolve_daily_continuity(
        current_date=current_date,
        generated_at=current.updated_at,
        current_stories=[current],
        historical_stories=[
            StoryMemory(
                ref=StoryOccurrenceRef(date=previous_date, story_id=previous.id),
                story=previous,
            )
        ],
        continuity_history=[],
        provider=FakeAIProvider(),
        history_days=14,
        maximum_candidates=20,
        maximum_open_watches=20,
        maximum_ai_items=40,
        maximum_input_characters=30_000,
    )

    assert bool(result.daily.relations) is case["expected_relation"]


def test_mcp_legacy_watch_can_backtest_but_is_not_automatically_activated() -> None:
    previous_date = date(2026, 7, 28)
    current_date = date(2026, 7, 29)
    previous = _story(
        {"date": str(previous_date), "story_id": "story-124f756773bdde748301"}
    )
    current = _story(
        {"date": str(current_date), "story_id": "story-61e13c601a33cabb863a"}
    )
    historical = StoryMemory(
        ref=StoryOccurrenceRef(date=previous_date, story_id=previous.id),
        story=previous,
    )
    opened = WatchEvent(
        watch_id="golden-mcp-watch",
        recorded_at=previous.updated_at,
        event_type=WatchEventType.OPENED,
        expectation="观察 MCP Python SDK 是否发布 v2 stable。",
        product_anchors=["MCP Python SDK"],
        source_story_refs=[historical.ref],
    )

    with_watch = resolve_daily_continuity(
        current_date=current_date,
        generated_at=current.updated_at,
        current_stories=[current],
        historical_stories=[historical],
        continuity_history=[
            DailyContinuity(
                date=previous_date,
                generated_at=previous.updated_at,
                watch_events=[opened],
            )
        ],
        provider=FakeAIProvider(),
        history_days=14,
        maximum_candidates=20,
        maximum_open_watches=20,
        maximum_ai_items=40,
        maximum_input_characters=30_000,
    )
    without_structured_history = resolve_daily_continuity(
        current_date=current_date,
        generated_at=current.updated_at,
        current_stories=[current],
        historical_stories=[historical],
        continuity_history=[],
        provider=FakeAIProvider(),
        history_days=14,
        maximum_candidates=20,
        maximum_open_watches=20,
        maximum_ai_items=40,
        maximum_input_characters=30_000,
    )

    assert any(
        event.event_type is WatchEventType.MATCHED
        for event in with_watch.daily.watch_events
    )
    assert without_structured_history.daily.watch_events == []

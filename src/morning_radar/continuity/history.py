"""Bounded loading and validation for immutable continuity history."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from morning_radar.continuity.candidates import StoryMemory
from morning_radar.models import DailyContinuity, Story, StoryOccurrenceRef
from morning_radar.storage import load_model, load_models


def load_story_memory(
    root: Path,
    *,
    current_date: date,
    history_days: int,
) -> list[StoryMemory]:
    result: list[StoryMemory] = []
    for offset in range(1, history_days + 1):
        story_date = current_date - timedelta(days=offset)
        path = root / "data/stories" / f"{story_date}.json"
        if not path.exists():
            continue
        result.extend(
            StoryMemory(
                ref=StoryOccurrenceRef(date=story_date, story_id=story.id),
                story=story,
            )
            for story in load_models(path, Story)
        )
    return result


def load_continuity_history(
    root: Path,
    *,
    current_date: date,
    history_days: int | None = None,
) -> list[DailyContinuity]:
    directory = root / "data/continuity"
    if not directory.exists():
        return []
    if history_days is None:
        paths = sorted(directory.glob("*.json"))
    else:
        earliest = current_date - timedelta(days=history_days)
        paths = [
            directory / f"{day}.json"
            for day in (earliest + timedelta(days=offset) for offset in range(history_days))
            if (directory / f"{day}.json").exists()
        ]
    return [load_model(path, DailyContinuity) for path in paths]

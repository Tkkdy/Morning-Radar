"""Standalone Tendency workflow with its own provider and safety budget."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from morning_radar.ai import AIBudget, DeepSeekProvider
from morning_radar.ai.provider import AIProvider
from morning_radar.continuity.candidates import StoryMemory
from morning_radar.continuity.history import load_continuity_history, load_story_memory
from morning_radar.models import Story, StoryOccurrenceRef
from morning_radar.settings import AppConfig, load_model
from morning_radar.storage import load_models, save_model
from morning_radar.tendencies.engine import TendencyRunResult, evaluate_daily_tendencies
from morning_radar.tendencies.history import load_tendency_history
from morning_radar.time_utils import display_date, utc_now


def run_tendency_workflow(
    root: Path,
    *,
    provider: AIProvider | None = None,
    current_date: date | None = None,
    generated_at: datetime | None = None,
) -> TendencyRunResult:
    """Evaluate and persist Tendency state without touching the production brief."""
    root = root.resolve()
    app = load_model(root / "config/app.yaml", AppConfig)
    now = generated_at or utc_now()
    day = current_date or display_date(now)
    active_provider = provider or DeepSeekProvider.from_environment(
        budget=AIBudget(
            app.tendency_maximum_ai_calls,
            app.maximum_tendency_input_characters,
            app.maximum_ai_items,
            app.tendency_maximum_network_requests,
        ),
        prompt_dir=root / "prompts",
    )
    current_path = root / "data/stories" / f"{day}.json"
    current_stories = load_models(current_path, Story) if current_path.exists() else []
    current_memory = [
        StoryMemory(
            ref=StoryOccurrenceRef(date=day, story_id=story.id),
            story=story,
        )
        for story in current_stories
    ]
    historical = load_story_memory(
        root,
        current_date=day,
        history_days=max(app.trend_window_days, app.deep_review_window_days),
    )
    result = evaluate_daily_tendencies(
        current_date=day,
        generated_at=now,
        story_memory=[*historical, *current_memory],
        continuities=load_continuity_history(root, current_date=day),
        history=load_tendency_history(root, current_date=day),
        provider=active_provider,
        maximum_clusters=app.maximum_tendency_candidates,
        maximum_input_characters=app.maximum_tendency_input_characters,
    )
    if not result.stats.get("tendency_unavailable"):
        save_model(root / "data/tendencies" / f"{day}.json", result.daily)
    return result

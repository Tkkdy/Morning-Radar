"""Offline regression coverage for MR-PROD-HOTFIX-20260911."""

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from morning_radar.ai import (
    AIBudget,
    AIBudgetExceeded,
    AIOutputError,
    DeepSeekProvider,
    FakeAIProvider,
)
from morning_radar.ai.models import (
    ContinuityResolution,
    ResolvedRelationDraft,
)
from morning_radar.ai.output_validation import validate_simplified_chinese_output
from morning_radar.briefing import BriefLimits, generate_daily_brief
from morning_radar.models import (
    Signal,
    SignalType,
    Story,
    StoryOccurrenceRef,
    StoryStatus,
)

NOW = datetime(2026, 9, 11, 1, 0, tzinfo=UTC)


def _story(index: int) -> Story:
    url = f"https://example.test/story-{index}"
    return Story(
        id=f"story-{index}",
        canonical_title=f"Story {index}",
        category="ai_and_open_source",
        topic_names=["ai_coding"],
        published_at=NOW,
        updated_at=NOW,
        source_item_ids=[f"item-{index}"],
        source_urls=[url],
        primary_source_url=url,
        facts=[f"Verified fact {index}"],
        analysis=[f"Verified analysis {index}"],
        relevance_score=0.9,
        importance_score=0.8,
        novelty_score=0.7,
        credibility_score=0.9,
        status=StoryStatus.UPDATED,
    )


def _signal() -> Signal:
    return Signal(
        id="signal", signal_type=SignalType.TOPIC_HEATING, topic="ai_coding",
        window_days=3, supporting_story_ids=["story-1", "story-2"],
        supporting_source_count=2, supporting_company_count=0, strength=0.8,
        explanation="Verified multi-story evidence", created_at=NOW, updated_at=NOW,
    )


def _brief(provider, *, stories=None, signals=None, enabled=None):
    return generate_daily_brief(
        brief_date=date(2026, 9, 11), generated_at=NOW, timezone="Asia/Singapore",
        stories=stories or [_story(1), _story(2)],
        signals=signals if signals is not None else [_signal()],
        provider=provider, limits=BriefLimits(maximum_items=4),
        enabled_sections=enabled or {}, run_stats={},
    )


class BudgetDirectionProvider(FakeAIProvider):
    def __init__(self, reason: str) -> None:
        super().__init__()
        self.reason, self.direction_calls = reason, 0

    def write_direction_observation(self, signals):
        del signals
        self.direction_calls += 1
        raise AIBudgetExceeded(self.reason)


@pytest.mark.parametrize(
    "reason",
    [
        "AI daily input character limit exceeded",
        "AI daily call limit exceeded",
        "AI global network request limit exceeded",
    ],
)
def test_hf01_hf03_direction_budget_omits_only_optional_section(reason: str) -> None:
    provider = BudgetDirectionProvider(reason)

    brief = _brief(provider)

    assert brief.top_stories
    assert brief.direction_observation is None
    assert provider.direction_calls == 1
    assert brief.run_stats["ai_direction_fallback"] is True
    assert brief.run_stats["ai_direction_fallback_reason"] == reason


class RecoveryThenBudgetProvider(BudgetDirectionProvider):
    def __init__(self) -> None:
        super().__init__("AI daily input character limit exceeded")
        self.recovery_calls = []

    def write_brief(self, stories, signals):
        del stories, signals
        raise AIOutputError("batch output truncated")

    def recover_brief_item(self, story, signals, editorial_decision=None):
        self.recovery_calls.append(story.id)
        if len(self.recovery_calls) > 1:
            raise AIBudgetExceeded("AI daily input character limit exceeded")
        return super(BudgetDirectionProvider, self).recover_brief_item(
            story, signals, editorial_decision
        )


def test_hf02_recovery_budget_degradation_keeps_all_verified_body_items() -> None:
    provider = RecoveryThenBudgetProvider()
    stories = [_story(1), _story(2), _story(3)]

    brief = _brief(provider, stories=stories)

    items = [*brief.top_stories, *brief.ai_and_open_source]
    assert {item.story_ids[0] for item in items} == {story.id for story in stories}
    assert provider.recovery_calls == ["story-1", "story-2"]
    assert brief.direction_observation is None
    assert brief.run_stats["ai_brief_item_fallbacks"] == 2
    assert brief.run_stats["ai_direction_fallback_reason"] == (
        "AI daily input character limit exceeded"
    )


def test_hf04_no_signal_or_disabled_direction_makes_no_provider_call() -> None:
    provider = BudgetDirectionProvider("AI daily call limit exceeded")

    without_signal = _brief(provider, signals=[])
    disabled = _brief(provider, enabled={"direction_observation": False})

    assert provider.direction_calls == 0
    assert "ai_direction_fallback" not in without_signal.run_stats
    assert "ai_direction_fallback" not in disabled.run_stats


class OutputDirectionProvider(FakeAIProvider):
    def write_direction_observation(self, signals):
        del signals
        raise AIOutputError("invalid direction output")


def test_hf05_direction_output_failure_preserves_existing_degradation() -> None:
    brief = _brief(OutputDirectionProvider())

    assert brief.direction_observation is None
    assert brief.run_stats["ai_direction_fallback"] is True
    assert "ai_direction_fallback_reason" not in brief.run_stats


def _relation(*, rationale: str | None) -> ResolvedRelationDraft:
    return ResolvedRelationDraft(
        confirmed=False,
        previous_story=StoryOccurrenceRef(date=date(2026, 9, 10), story_id="old"),
        current_story=StoryOccurrenceRef(date=date(2026, 9, 11), story_id="new"),
        rationale=rationale,
    )


def test_hf06_hf07_nullable_continuity_narratives_skip_language_validation() -> None:
    validate_simplified_chinese_output(ContinuityResolution(relations=[_relation(rationale=None)]))
    validate_simplified_chinese_output(ContinuityResolution(relations=[_relation(rationale="中文说明")]))
    with pytest.raises(ValueError, match="English prose"):
        validate_simplified_chinese_output(ContinuityResolution(relations=[_relation(
            rationale="This is an excessively long English narrative that must remain rejected."
        )]))
    with pytest.raises(ValidationError):
        ResolvedRelationDraft.model_validate({"confirmed": False})


class _OneShotCompletions:
    def __init__(self, content: str) -> None:
        self.content, self.calls = content, 0

    def create(self, **kwargs):
        del kwargs
        self.calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=self.content), finish_reason="stop"
            )],
            usage=None,
        )


def test_hf08_deepseek_mock_parses_nullable_relation_without_structured_retry() -> None:
    response = ContinuityResolution(relations=[_relation(rationale=None)]).model_dump_json()
    completions = _OneShotCompletions(response)
    provider = DeepSeekProvider(
        model="test", api_key="test", base_url="https://api.deepseek.test",
        budget=AIBudget(2, 10_000, 5), prompt_dir=Path("prompts"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    result = provider._parse(
        task="resolve_continuity", schema=ContinuityResolution, payload_data={},
        item_count=1, allowed_urls=set(),
    )

    assert result.relations[0].rationale is None
    assert completions.calls == 1
    assert provider.budget.calls_used == 1
    assert provider.budget.network_requests_used == 1

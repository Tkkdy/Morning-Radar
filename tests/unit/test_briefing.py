import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from morning_radar.ai import (
    AIBudget,
    AIBudgetExceeded,
    AIOutputError,
    DeepSeekProvider,
    FakeAIProvider,
)
from morning_radar.ai.models import (
    BriefDraft,
    BriefItemRecoveryDraft,
    ClassificationBatch,
    GeneratedBriefItem,
    ResearchResolutionBatch,
    ResearchResolutionDraft,
)
from morning_radar.ai.request_payload import dumps
from morning_radar.briefing import (
    BriefLimits,
    BriefValidationError,
    core_brief_request_payloads,
    generate_daily_brief,
    generate_daily_brief_with_memory,
)
from morning_radar.editorial.models import EditorialDecision, FactStatus, Placement
from morning_radar.models import (
    BriefItem,
    BriefStoryContext,
    DailyBrief,
    RadarSignal,
    RawItem,
    ResearchDisposition,
    ResearchEvidenceRef,
    Signal,
    SignalType,
    SourceRole,
    StatementType,
    Story,
    StorySourceRef,
)
from morning_radar.pipeline import _reserve_brief_core_budget, suppress_displayed_radar_duplicates
from morning_radar.processing import build_stories
from morning_radar.research.engine import resolve_research

NOW = datetime(2026, 7, 23, 1, tzinfo=UTC)


def story(index: int, *, category: str = "ai_and_open_source") -> Story:
    url = f"https://example.com/story-{index}"
    return Story(
        id=f"story-{index}",
        canonical_title=f"Story {index}",
        category=category,
        entity_names=[],
        product_names=[],
        topic_names=["ai_coding"],
        published_at=NOW,
        updated_at=NOW,
        source_item_ids=[f"item-{index}"],
        source_urls=[url],
        primary_source_url=url,
        facts=[f"Fact {index}"],
        analysis=[f"Analysis {index}"],
        uncertainties=[],
        relevance_score=0.9,
        importance_score=0.8,
        novelty_score=0.7,
        credibility_score=0.9,
    )


def brief(stories: list[Story], *, maximum: int = 12):
    return generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=FakeAIProvider(),
        limits=BriefLimits(maximum_items=maximum, top_story_items=2),
        enabled_sections={},
        run_stats={"raw_items": 4, "stories": len(stories)},
    )


def test_same_story_is_not_repeated_and_item_limit_is_enforced() -> None:
    result = brief([story(index) for index in range(5)], maximum=3)
    all_items = [
        *result.top_stories,
        *result.market_and_companies,
        *result.ai_and_open_source,
        *result.trend_radar,
        *result.developer_discussions,
    ]

    assert len(all_items) == 3
    assert len({item.story_ids[0] for item in all_items}) == 3
    assert len(result.top_stories) == 2


class SuccessfulBatchOnlyProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.batch_calls = 0

    def write_brief(self, stories, signals):
        self.batch_calls += 1
        return super().write_brief(stories, signals)

    def recover_brief_item(self, story, signals, editorial_decision=None):
        raise AssertionError("successful batch must not enter item recovery")


def test_successful_batch_does_not_enter_recovery_or_fallback() -> None:
    provider = SuccessfulBatchOnlyProvider()
    stories = [story(index) for index in range(4)]

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=provider,
        limits=BriefLimits(maximum_items=4),
        enabled_sections={},
        run_stats={},
    )

    assert provider.batch_calls == 1
    assert len(_main_items(result)) == 4
    assert "ai_brief_batch_failed" not in result.run_stats
    assert "ai_brief_fallback" not in result.run_stats


def test_empty_optional_sections_and_observations_remain_empty() -> None:
    result = brief([])

    assert result.top_stories == []
    assert result.market_and_companies == []
    assert result.direction_observation is None
    assert result.cognitive_extension is None


class RecordingEmptyProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.classify_calls = 0
        self.write_calls = 0
        self.direction_calls = 0

    def classify_items(self, items) -> ClassificationBatch:
        self.classify_calls += 1
        return super().classify_items(items)

    def write_brief(self, stories, signals) -> BriefDraft:
        self.write_calls += 1
        return super().write_brief(stories, signals)

    def write_direction_observation(self, signals):
        self.direction_calls += 1
        return super().write_direction_observation(signals)


def test_empty_pipeline_inputs_skip_all_ai_calls(caplog) -> None:
    caplog.set_level(logging.INFO)
    provider = RecordingEmptyProvider()

    stories = build_stories([], provider=provider, now=NOW)
    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=provider,
        limits=BriefLimits(maximum_items=12),
        enabled_sections={},
        run_stats={},
    )

    assert result.top_stories == []
    assert provider.classify_calls == 0
    assert provider.write_calls == 0
    assert provider.direction_calls == 0
    assert "Skipping AI classification: no recent items" in caplog.text
    assert "Skipping AI brief generation: no stories" in caplog.text
    assert "Skipping AI direction observation: no coherent evidence signals" in caplog.text


def test_source_links_are_complete_and_traceable() -> None:
    source_story = story(1)
    result = brief([source_story])

    assert result.top_stories[0].source_urls == source_story.source_urls


class SelectiveBriefProvider(FakeAIProvider):
    def __init__(self, selected_indexes: list[int]) -> None:
        self.selected_indexes = selected_indexes
        self.write_calls = 0

    def write_brief(self, stories, signals):
        del signals
        self.write_calls += 1
        return BriefDraft(
            items=[
                GeneratedBriefItem(
                    story_ids=[source_story.id],
                    section="ai_and_open_source",
                    title=f"Selected {index}",
                    what_happened="Selected story",
                    why_it_matters="Selected importance",
                    source_urls=[source_story.primary_source_url],
                )
                for index in self.selected_indexes
                for source_story in stories
                if source_story.id == f"story-{index}"
            ]
        )


class AllTopStoriesProvider(FakeAIProvider):
    def write_brief(self, stories, signals):
        del signals
        return BriefDraft(
            items=[
                GeneratedBriefItem(
                    story_ids=[source_story.id],
                    section="top_stories",
                    title=source_story.canonical_title,
                    what_happened=source_story.facts[0],
                    why_it_matters=source_story.analysis[0],
                    source_urls=source_story.source_urls,
                )
                for source_story in stories
            ]
        )


def test_top_story_limit_demotes_overflow_without_hiding_it() -> None:
    stories = [story(index) for index in range(4)]

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=AllTopStoriesProvider(),
        limits=BriefLimits(maximum_items=12, top_story_items=3),
        enabled_sections={},
        run_stats={},
        importance_threshold=0.6,
    )

    assert [item.story_ids for item in result.top_stories] == [
        ["story-0"],
        ["story-1"],
        ["story-2"],
    ]
    assert [item.story_ids for item in result.ai_and_open_source] == [["story-3"]]
    assert result.other_reading == []


def test_other_reading_keeps_unselected_eligible_stories_in_ranked_order() -> None:
    stories = [story(index) for index in range(5)]
    provider = SelectiveBriefProvider([1, 3])

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=provider,
        limits=BriefLimits(maximum_items=12, other_reading_items=6),
        enabled_sections={"top_stories": False},
        run_stats={},
    )

    main_story_ids = {
        story_id
        for item in result.ai_and_open_source
        for story_id in item.story_ids
    }
    assert [item.story_ids for item in result.other_reading] == [
        [stories[0].id],
        [stories[2].id],
        [stories[4].id],
    ]
    assert main_story_ids.isdisjoint(
        {story_id for item in result.other_reading for story_id in item.story_ids}
    )
    assert result.other_reading[0].source_urls == stories[0].source_urls
    assert result.other_reading[0].story_contexts[0].story_id == stories[0].id
    assert provider.write_calls == 2


def test_other_reading_respects_total_and_independent_item_limits() -> None:
    stories = [story(index) for index in range(10)]

    total_limited = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=SelectiveBriefProvider([0, 1]),
        limits=BriefLimits(maximum_items=4, other_reading_items=6),
        enabled_sections={"top_stories": False},
        run_stats={},
    )
    independently_limited = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=SelectiveBriefProvider([0]),
        limits=BriefLimits(maximum_items=12, other_reading_items=6),
        enabled_sections={"top_stories": False},
        run_stats={},
    )

    assert len(total_limited.ai_and_open_source) == 2
    assert len(total_limited.other_reading) == 2
    assert len(independently_limited.other_reading) == 6


def test_brief_item_embeds_deterministic_single_story_context() -> None:
    source_story = story(1).model_copy(
        update={
            "entity_names": ["Example Corp"],
            "product_names": ["Example Product"],
            "topic_names": ["ai_coding", "agents"],
            "uncertainties": ["Source details may change."],
            "source_refs": [
                StorySourceRef(
                    raw_item_id="item-1",
                    title="Collector title",
                    source_name="Example RSS",
                    source_type="rss",
                    url="https://example.com/story-1",
                    author="Ada",
                    published_at=NOW,
                    fetched_at=NOW,
                )
            ],
        }
    )

    context = brief([source_story]).top_stories[0].story_contexts[0]

    assert context.story_id == source_story.id
    assert context.canonical_title == source_story.canonical_title
    assert context.category == source_story.category
    assert context.entity_names == ["Example Corp"]
    assert context.product_names == ["Example Product"]
    assert context.topic_names == ["ai_coding", "agents"]
    assert context.published_at == NOW
    assert context.facts == source_story.facts
    assert context.analysis == source_story.analysis
    assert context.uncertainties == ["Source details may change."]
    assert context.status == source_story.status
    assert context.primary_source_url == source_story.primary_source_url
    assert context.source_refs == source_story.source_refs


class MultiStoryProvider(FakeAIProvider):
    def write_brief(self, stories, signals):
        del signals
        return BriefDraft(
            items=[
                GeneratedBriefItem(
                    story_ids=[stories[1].id, stories[0].id],
                    section="ai_and_open_source",
                    title="Combined",
                    what_happened="Combined summary",
                    why_it_matters="Combined importance",
                    source_urls=[stories[1].primary_source_url, stories[0].primary_source_url],
                )
            ]
        )


class MultiStoryThenSingleProvider(FakeAIProvider):
    def write_brief(self, stories, signals):
        del signals
        items = [
            GeneratedBriefItem(
                story_ids=[stories[0].id, stories[1].id],
                section="ai_and_open_source",
                title="Combined",
                what_happened="Combined summary",
                why_it_matters="Combined importance",
                source_urls=[stories[0].primary_source_url, stories[1].primary_source_url],
            )
        ]
        if len(stories) > 2:
            items.append(
                GeneratedBriefItem(
                    story_ids=[stories[2].id],
                    section="ai_and_open_source",
                    title="Single",
                    what_happened="Single summary",
                    why_it_matters="Single importance",
                    source_urls=[stories[2].primary_source_url],
                )
            )
        return BriefDraft(
            items=items
        )


def test_maximum_brief_items_counts_cards_not_referenced_stories() -> None:
    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=[story(1), story(2), story(3)],
        signals=[],
        provider=MultiStoryThenSingleProvider(),
        limits=BriefLimits(maximum_items=2, top_story_items=2),
        enabled_sections={},
        run_stats={},
    )

    assert [item.story_ids for item in result.top_stories] == [["story-1", "story-2"]]
    assert [item.story_ids for item in result.other_reading] == [["story-3"]]


class BriefInputRecordingProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.story_inputs: list[list[Story]] = []

    def write_brief(self, stories, signals):
        self.story_inputs.append(list(stories))
        return super().write_brief(stories, signals)


def test_brief_ai_input_is_bounded_to_display_capacity() -> None:
    provider = BriefInputRecordingProvider()

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=[story(index) for index in range(16)],
        signals=[],
        provider=provider,
        limits=BriefLimits(maximum_items=12),
        enabled_sections={},
        run_stats={},
    )

    assert [len(batch) for batch in provider.story_inputs] == [4, 4, 4]
    assert result.run_stats["threshold_eligible_stories"] == 16
    assert result.run_stats["ai_brief_story_inputs"] == 12


def test_multi_story_contexts_preserve_generated_story_id_order() -> None:
    first = story(1)
    second = story(2)

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=[first, second],
        signals=[],
        provider=MultiStoryProvider(),
        limits=BriefLimits(maximum_items=5),
        enabled_sections={"top_stories": False},
        run_stats={},
    )

    contexts = result.ai_and_open_source[0].story_contexts
    assert [context.story_id for context in contexts] == [second.id, first.id]
    assert [context.canonical_title for context in contexts] == [
        second.canonical_title,
        first.canonical_title,
    ]


class InventedBriefProvider(FakeAIProvider):
    def write_brief(self, stories, signals):
        del signals
        return BriefDraft(
            items=[
                GeneratedBriefItem(
                    story_ids=[stories[0].id],
                    section="top_stories",
                    title="Invented",
                    what_happened="Invented",
                    why_it_matters="Invented",
                    source_urls=["https://invented.example/story"],
                )
            ]
        )


class UnknownStoryBriefProvider(FakeAIProvider):
    def write_brief(self, stories, signals):
        del signals
        return BriefDraft(
            items=[
                GeneratedBriefItem(
                    story_ids=["unknown-story"],
                    section="top_stories",
                    title="Unknown",
                    what_happened="Unknown",
                    why_it_matters="Unknown",
                    source_urls=[stories[0].primary_source_url],
                )
            ],
            watch_next=["继续关注 AI 行业发展。"],
        )


def test_unknown_story_id_remains_a_hard_brief_failure() -> None:
    with pytest.raises(BriefValidationError, match="unknown Story ID"):
        generate_daily_brief(
            brief_date=date(2026, 7, 23),
            generated_at=NOW,
            timezone="Asia/Singapore",
            stories=[story(1)],
            signals=[],
            provider=UnknownStoryBriefProvider(),
            limits=BriefLimits(maximum_items=3),
            enabled_sections={},
            run_stats={},
        )


def test_brief_rejects_url_not_present_in_referenced_story() -> None:
    with pytest.raises(BriefValidationError, match="URL"):
        generate_daily_brief(
            brief_date=date(2026, 7, 23),
            generated_at=NOW,
            timezone="Asia/Singapore",
            stories=[story(1)],
            signals=[],
            provider=InventedBriefProvider(),
            limits=BriefLimits(maximum_items=3),
            enabled_sections={},
            run_stats={},
        )


def test_disabled_section_is_not_emitted() -> None:
    stories = [story(1), story(2), story(3, category="market_and_companies")]
    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=FakeAIProvider(),
        limits=BriefLimits(maximum_items=5, top_story_items=2),
        enabled_sections={"market_and_companies": False},
        run_stats={},
    )

    assert result.market_and_companies == []


def test_thresholds_filter_relevance_and_reserve_top_for_important_stories() -> None:
    relevant_important = story(1)
    relevant_secondary = story(2).model_copy(update={"importance_score": 0.4})
    irrelevant = story(3).model_copy(update={"relevance_score": 0.4})

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=[irrelevant, relevant_secondary, relevant_important],
        signals=[],
        provider=FakeAIProvider(),
        limits=BriefLimits(maximum_items=5, top_story_items=2),
        enabled_sections={},
        run_stats={},
        relevance_threshold=0.55,
        importance_threshold=0.6,
    )

    assert [item.story_ids for item in result.top_stories] == [["story-1"]]
    assert [item.story_ids for item in result.ai_and_open_source] == [["story-2"]]
    assert result.run_stats["threshold_eligible_stories"] == 2


def test_disabled_top_stories_still_populates_enabled_sections() -> None:
    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=[story(1), story(2)],
        signals=[],
        provider=FakeAIProvider(),
        limits=BriefLimits(maximum_items=5, top_story_items=2),
        enabled_sections={"top_stories": False, "ai_and_open_source": True},
        run_stats={},
        relevance_threshold=0.55,
        importance_threshold=0.6,
    )

    assert result.top_stories == []
    assert len(result.ai_and_open_source) == 2


def test_low_importance_story_categorized_as_top_is_rerouted() -> None:
    low_importance_top = story(1, category="top_stories").model_copy(
        update={"importance_score": 0.4}
    )

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=[low_importance_top],
        signals=[],
        provider=FakeAIProvider(),
        limits=BriefLimits(maximum_items=5),
        enabled_sections={},
        run_stats={},
        relevance_threshold=0.55,
        importance_threshold=0.6,
    )

    assert result.top_stories == []
    assert len(result.ai_and_open_source) == 1


class BriefFailureProvider(FakeAIProvider):
    def write_brief(self, stories, signals):
        del stories, signals
        raise AIOutputError("structured output failed")

    def recover_brief_item(self, story, signals, editorial_decision=None):
        del story, signals, editorial_decision
        raise AIOutputError("item recovery failed")


def test_brief_failure_uses_only_verified_story_facts_and_marks_fallback(caplog) -> None:
    source_story = story(1)

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=[source_story],
        signals=[],
        provider=BriefFailureProvider(),
        limits=BriefLimits(maximum_items=5),
        enabled_sections={},
        run_stats={},
        relevance_threshold=0.55,
        importance_threshold=0.6,
    )

    assert result.top_stories[0].what_happened == source_story.facts[0]
    assert result.top_stories[0].why_it_matters == source_story.analysis[0]
    assert result.top_stories[0].uncertainty is None
    assert result.top_stories[0].generation_status == "fallback_existing_analysis"
    assert result.top_stories[0].generation_note == "本次成稿使用已验证的已有分析。"
    assert result.top_stories[0].source_urls == source_story.source_urls
    assert result.top_stories[0].story_contexts[0].story_id == source_story.id
    assert result.top_stories[0].story_contexts[0].source_refs == []
    assert result.other_reading == []
    assert result.run_stats["ai_brief_fallback"] is True
    assert "AI degradation: brief batch failed" in caplog.text
    assert result.run_stats["ai_brief_batch_failed"] is True
    assert result.run_stats["ai_brief_recovery_attempts"] == 1
    assert result.run_stats["ai_brief_recovery_successes"] == 0
    assert result.run_stats["ai_brief_item_fallbacks"] == 1


class ItemRecoveryProvider(FakeAIProvider):
    def __init__(self, failed_story_ids: set[str] | None = None) -> None:
        self.failed_story_ids = failed_story_ids or set()
        self.batch_calls = 0
        self.recovery_story_ids: list[str] = []

    def write_brief(self, stories, signals):
        del stories, signals
        self.batch_calls += 1
        raise AIOutputError("batch output failed")

    def recover_brief_item(self, story, signals, editorial_decision=None):
        self.recovery_story_ids.append(story.id)
        if story.id in self.failed_story_ids:
            raise AIOutputError("item output failed")
        return super().recover_brief_item(story, signals, editorial_decision)


def _main_items(result):
    return [
        *result.top_stories,
        *result.market_and_companies,
        *result.ai_and_open_source,
        *result.trend_radar,
        *result.developer_discussions,
    ]


def test_batch_failure_recovers_every_story_without_memory_side_effects() -> None:
    stories = [story(index) for index in range(4)]
    provider = ItemRecoveryProvider()

    result = generate_daily_brief_with_memory(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=provider,
        limits=BriefLimits(maximum_items=4),
        enabled_sections={},
        run_stats={},
    )

    assert provider.batch_calls == 1
    assert provider.recovery_story_ids == [story.id for story in stories]
    assert len(_main_items(result.brief)) == 4
    assert "ai_brief_fallback" not in result.brief.run_stats
    assert result.brief.run_stats["ai_brief_batch_failed"] is True
    assert result.brief.run_stats["ai_brief_recovery_attempts"] == 4
    assert result.brief.run_stats["ai_brief_recovery_successes"] == 4
    assert result.brief.run_stats["ai_brief_item_fallbacks"] == 0
    assert result.watch_drafts == []
    assert result.judgement_drafts == []
    assert result.brief.cognitive_extension is None


def test_one_failed_item_recovery_does_not_contaminate_other_stories() -> None:
    stories = [story(index) for index in range(4)]
    provider = ItemRecoveryProvider({"story-2"})

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=provider,
        limits=BriefLimits(maximum_items=4),
        enabled_sections={},
        run_stats={},
    )

    items = {item.story_ids[0]: item for item in _main_items(result)}
    assert items["story-2"].why_it_matters == "Analysis 2"
    assert items["story-2"].uncertainty is None
    assert items["story-2"].generation_status == "fallback_existing_analysis"
    assert all(
        items[f"story-{index}"].why_it_matters == f"Analysis {index}"
        for index in (0, 1, 3)
    )
    assert result.run_stats["ai_brief_fallback"] is True
    assert result.run_stats["ai_brief_recovery_successes"] == 3
    assert result.run_stats["ai_brief_item_fallbacks"] == 1


def test_all_item_recoveries_fail_but_brief_remains_schema_valid() -> None:
    stories = [story(index) for index in range(4)]
    provider = ItemRecoveryProvider({story.id for story in stories})

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=provider,
        limits=BriefLimits(maximum_items=4),
        enabled_sections={},
        run_stats={},
    )

    assert len(_main_items(result)) == 4
    assert all(
        item.why_it_matters == f"Analysis {index}"
        for index, item in enumerate(_main_items(result))
    )
    assert all(item.uncertainty is None for item in _main_items(result))
    assert all(
        item.generation_status == "fallback_existing_analysis"
        for item in _main_items(result)
    )
    assert result.run_stats["ai_brief_fallback"] is True
    assert result.run_stats["ai_brief_recovery_attempts"] == 4
    assert result.run_stats["ai_brief_recovery_successes"] == 0
    assert result.run_stats["ai_brief_item_fallbacks"] == 4


class RecoveryBudgetExceededProvider(ItemRecoveryProvider):
    def recover_brief_item(self, story, signals, editorial_decision=None):
        self.recovery_story_ids.append(story.id)
        raise AIBudgetExceeded("AI daily call limit exceeded")


def test_recovery_stops_immediately_when_budget_is_unavailable() -> None:
    stories = [story(index) for index in range(4)]
    provider = RecoveryBudgetExceededProvider()

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=provider,
        limits=BriefLimits(maximum_items=4),
        enabled_sections={},
        run_stats={},
    )

    assert provider.recovery_story_ids == ["story-0"]
    assert result.run_stats["ai_brief_recovery_attempts"] == 0
    assert result.run_stats["ai_brief_recovery_successes"] == 0
    assert result.run_stats["ai_brief_item_fallbacks"] == 4
    assert result.run_stats["ai_brief_fallback"] is True


class _ScriptedBriefCompletions:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        finish_reason, content = self.responses.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content), finish_reason=finish_reason
                )
            ],
            usage=None,
        )


def test_real_budget_bounds_truncated_batch_recovery_and_preserves_existing_analysis() -> None:
    stories = [story(1), story(2)]
    recovered = BriefItemRecoveryDraft(
        item=GeneratedBriefItem(
            story_ids=[stories[0].id], section="ai_and_open_source", title="恢复条目",
            what_happened="已验证事实 1", why_it_matters="恢复分析 1",
            source_urls=stories[0].source_urls,
        )
    ).model_dump_json()
    completions = _ScriptedBriefCompletions([
        ("length", "{}"), ("length", "{}"), ("stop", recovered),
    ])
    budget = AIBudget(maximum_calls=2, maximum_input_characters=100_000, maximum_items=8)
    provider = DeepSeekProvider(
        model="test", api_key="test", base_url="https://api.deepseek.test", budget=budget,
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    result = generate_daily_brief(
        brief_date=date(2026, 9, 14), generated_at=NOW, timezone="Asia/Singapore",
        stories=stories, signals=[], provider=provider, limits=BriefLimits(maximum_items=2),
        enabled_sections={}, run_stats={},
    )

    items = {item.story_ids[0]: item for item in _main_items(result)}
    assert budget.calls_used == 2
    assert budget.network_requests_used == 3
    assert result.run_stats["ai_brief_recovery_budget_exhausted"] is True
    assert result.run_stats["ai_brief_recovery_successes"] == 1
    assert items["story-1"].why_it_matters == "恢复分析 1"
    assert items["story-2"].why_it_matters == "Analysis 2"
    assert items["story-2"].generation_reason == "recovery_budget_unavailable"
    assert completions.requests[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert completions.requests[0]["max_tokens"] == 4096


def test_real_provider_budget_reservation_bounds_4_plus_1_batches_and_recovery() -> None:
    """Use the provider's production payload shape to bound an entire degraded brief."""
    stories = [story(index) for index in range(5)]
    batch_payloads, recovery_payloads = core_brief_request_payloads(stories, [])
    core_payloads = [
        *(dumps(payload) for payload in batch_payloads),
        dumps(recovery_payloads[0]),
    ]
    recovery = BriefItemRecoveryDraft(
        item=GeneratedBriefItem(
            story_ids=[stories[0].id],
            section="ai_and_open_source",
            title="恢复条目",
            what_happened="已验证事实 0",
            why_it_matters="恢复分析 0",
            source_urls=stories[0].source_urls,
        )
    ).model_dump_json()
    second_batch = BriefDraft(
        items=[
            GeneratedBriefItem(
                story_ids=[stories[4].id],
                section="ai_and_open_source",
                title="第五条",
                what_happened="已验证事实 4",
                why_it_matters="正常分析 4",
                source_urls=stories[4].source_urls,
            )
        ]
    ).model_dump_json()
    completions = _ScriptedBriefCompletions(
        [("length", "{}"), ("length", "{}"), ("stop", second_batch), ("stop", recovery)]
    )
    budget = AIBudget(
        maximum_calls=3,
        maximum_input_characters=(
            sum(len(dumps(payload)) for payload in batch_payloads)
            + max(len(dumps(payload)) for payload in recovery_payloads)
        ),
        maximum_items=8,
        maximum_network_requests=4,
    )
    provider = DeepSeekProvider(
        model="test",
        api_key="test",
        base_url="https://api.deepseek.test",
        budget=budget,
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    _reserve_brief_core_budget(provider, stories, [])
    assert budget.reserved_core_calls == 3
    assert budget.reserved_core_input_characters == budget.maximum_input_characters
    with pytest.raises(AIBudgetExceeded):
        provider.write_direction_observation([])
    assert completions.requests == []

    result = generate_daily_brief(
        brief_date=date(2026, 9, 14),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=provider,
        limits=BriefLimits(maximum_items=5),
        enabled_sections={},
        run_stats={},
    )

    items = {item.story_ids[0]: item for item in _main_items(result)}
    assert budget.calls_used == budget.maximum_calls
    assert budget.input_characters_used == budget.maximum_input_characters
    assert budget.network_requests_used == budget.maximum_network_requests
    assert [request["messages"][1]["content"] for request in completions.requests] == [
        core_payloads[0], core_payloads[0], core_payloads[1], core_payloads[2]
    ]
    assert items["story-4"].generation_status == "generated"
    assert items["story-0"].why_it_matters == "恢复分析 0"
    for story_id in ("story-1", "story-2", "story-3"):
        assert items[story_id].generation_reason == "recovery_budget_unavailable"
    assert result.run_stats["ai_brief_batches"] == 2
    assert result.run_stats["ai_brief_recovery_successes"] == 1
    assert result.run_stats["ai_brief_recovery_budget_exhausted"] is True


def test_real_provider_reservation_uses_long_payloads_signals_and_editorial_decisions() -> None:
    stories = [
        story(index).model_copy(
            update={
                "facts": [f"事实 {index} " + "长输入" * (900 + index * 50)],
                "analysis": [f"分析 {index} " + "详细依据" * (500 + index * 30)],
            }
        )
        for index in range(5)
    ]
    signal = Signal(
        id="signal-long",
        signal_type=SignalType.TOPIC_HEATING,
        topic="ai_coding",
        window_days=3,
        supporting_story_ids=[stories[0].id, stories[1].id],
        supporting_source_count=2,
        supporting_company_count=0,
        strength=0.9,
        explanation="已验证的多来源信号。",
        created_at=NOW,
        updated_at=NOW,
    )
    decisions = [
        EditorialDecision(
            story_id=item.id,
            placement=Placement.STORY,
            reader_value=3,
            evidence_value=2,
            fact_status=FactStatus.CLAIM,
            retain_for_trends=False,
            reason="与开发者工作流直接相关。",
        )
        for item in stories
    ]
    batch_payloads, recovery_payloads = core_brief_request_payloads(
        stories,
        [signal],
        decisions,
    )
    auxiliary_payload = dumps([signal.model_dump(mode="json")])
    reserved_characters = sum(len(dumps(payload)) for payload in batch_payloads) + max(
        len(dumps(payload)) for payload in recovery_payloads
    )
    second_batch = BriefDraft(
        items=[
            GeneratedBriefItem(
                story_ids=[stories[4].id],
                section="ai_and_open_source",
                title="第五条",
                what_happened="已验证事实 4",
                why_it_matters="正常分析 4",
                source_urls=stories[4].source_urls,
            )
        ]
    ).model_dump_json()
    recovery = BriefItemRecoveryDraft(
        item=GeneratedBriefItem(
            story_ids=[stories[0].id],
            section="ai_and_open_source",
            title="恢复条目",
            what_happened="已验证事实 0",
            why_it_matters="恢复分析 0",
            source_urls=stories[0].source_urls,
        )
    ).model_dump_json()
    direction_response = (
        '{"observation":"信号仍在形成。",'
        '"evidence_story_ids":["story-0","story-1"],"confidence":"medium"}'
    )
    completions = _ScriptedBriefCompletions(
        [
            ("stop", direction_response),
            ("length", "{}"),
            ("length", "{}"),
            ("stop", second_batch),
            ("stop", recovery),
        ]
    )
    budget = AIBudget(
        maximum_calls=4,
        maximum_input_characters=reserved_characters + len(auxiliary_payload),
        maximum_items=8,
        maximum_network_requests=5,
    )
    provider = DeepSeekProvider(
        model="test",
        api_key="test",
        base_url="https://api.deepseek.test",
        budget=budget,
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    _reserve_brief_core_budget(provider, stories, [signal], decisions)
    assert budget.reserved_core_calls == 3
    assert budget.reserved_core_input_characters == reserved_characters
    assert provider.write_direction_observation([signal]).observation == "信号仍在形成。"
    with pytest.raises(AIOutputError):
        provider.write_brief(stories[:4], [signal], decisions[:4])
    provider.write_brief(stories[4:], [signal], decisions[4:])
    provider.recover_brief_item(stories[0], [signal], decisions[0])

    assert [request["messages"][1]["content"] for request in completions.requests] == [
        auxiliary_payload,
        dumps(batch_payloads[0]),
        dumps(batch_payloads[0]),
        dumps(batch_payloads[1]),
        dumps(recovery_payloads[0]),
    ]
    assert budget.calls_used == budget.maximum_calls
    assert budget.input_characters_used <= budget.maximum_input_characters
    assert budget.network_requests_used == budget.maximum_network_requests


def test_real_provider_released_reservation_allows_remaining_auxiliary_capacity() -> None:
    source_story = story(1)
    brief_payload = {
        "stories": [source_story.model_dump(mode="json")],
        "signals": [],
        "editorial_decisions": [],
    }
    auxiliary_payload: list[object] = []

    def serialize(payload) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    draft = BriefDraft(
        items=[
            GeneratedBriefItem(
                story_ids=[source_story.id],
                section="ai_and_open_source",
                title="正常条目",
                what_happened="已验证事实 1",
                why_it_matters="正常分析 1",
                source_urls=source_story.source_urls,
            )
        ]
    ).model_dump_json()
    # ``direction_observation`` serializes the empty signal list itself; this
    # is deliberately the same payload the Provider will account for below.
    completions = _ScriptedBriefCompletions([("stop", draft), ("stop", '{"observation":null}')])
    maximum_characters = len(serialize(brief_payload)) + len(serialize(auxiliary_payload))
    budget = AIBudget(maximum_calls=2, maximum_input_characters=maximum_characters, maximum_items=8)
    completions = _ScriptedBriefCompletions([("stop", draft), ("stop", '{"observation":null}')])
    provider = DeepSeekProvider(
        model="test",
        api_key="test",
        base_url="https://api.deepseek.test",
        budget=budget,
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    _reserve_brief_core_budget(provider, [source_story], [])
    with pytest.raises(AIBudgetExceeded):
        provider.write_direction_observation([])
    assert completions.requests == []
    provider.write_brief([source_story], [])
    assert budget.reserved_core_calls == 1
    budget.release_core_reservation()
    assert provider.write_direction_observation([]).observation is None
    assert budget.calls_used == budget.maximum_calls
    assert budget.input_characters_used == budget.maximum_input_characters
    assert budget.network_requests_used == 2


def test_real_provider_insufficient_reservation_degrades_without_exceeding_limits() -> None:
    stories = [story(index) for index in range(5)]

    def serialize(payload) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    first_payload = serialize(
        {
            "stories": [item.model_dump(mode="json") for item in stories[:4]],
            "signals": [],
            "editorial_decisions": [],
        }
    )
    second_payload = serialize(
        {"stories": [stories[4].model_dump(mode="json")], "signals": [], "editorial_decisions": []}
    )
    second_batch = BriefDraft(
        items=[
            GeneratedBriefItem(
                story_ids=[stories[4].id], section="ai_and_open_source", title="第五条",
                what_happened="已验证事实 4", why_it_matters="正常分析 4",
                source_urls=stories[4].source_urls,
            )
        ]
    ).model_dump_json()
    completions = _ScriptedBriefCompletions(
        [("length", "{}"), ("length", "{}"), ("stop", second_batch)]
    )
    budget = AIBudget(
        maximum_calls=2,
        maximum_input_characters=len(first_payload) + len(second_payload),
        maximum_items=8,
        maximum_network_requests=3,
    )
    provider = DeepSeekProvider(
        model="test",
        api_key="test",
        base_url="https://api.deepseek.test",
        budget=budget,
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    _reserve_brief_core_budget(provider, stories, [])
    assert budget.reserved_core_calls == budget.maximum_calls
    result = generate_daily_brief(
        brief_date=date(2026, 9, 14), generated_at=NOW, timezone="Asia/Singapore",
        stories=stories, signals=[], provider=provider, limits=BriefLimits(maximum_items=5),
        enabled_sections={}, run_stats={},
    )

    items = {item.story_ids[0]: item for item in _main_items(result)}
    assert budget.calls_used == budget.maximum_calls
    assert budget.input_characters_used == budget.maximum_input_characters
    assert budget.network_requests_used == budget.maximum_network_requests
    assert items["story-4"].generation_status == "generated"
    for story_id in ("story-0", "story-1", "story-2", "story-3"):
        assert items[story_id].generation_reason == "recovery_budget_unavailable"


class OrderedBudgetProvider(FakeAIProvider):
    """Records bounded task ordering while delegating accounting to AIBudget."""

    def __init__(self, budget: AIBudget) -> None:
        super().__init__()
        self.budget = budget
        self.events: list[str] = []
        self.brief_calls = 0

    def write_brief(self, stories, signals, editorial_decisions=None):
        del signals
        del editorial_decisions
        self.events.append(f"brief:{','.join(story.id for story in stories)}")
        self.budget.consume("b" * 120, item_count=len(stories))
        self.brief_calls += 1
        if self.brief_calls == 1:
            raise AIOutputError("truncated batch output")
        return super().write_brief(stories, [])

    def recover_brief_item(self, story, signals, editorial_decision=None):
        del signals
        del editorial_decision
        self.events.append(f"recover:{story.id}")
        self.budget.consume("r" * 50, item_count=1)
        return super().recover_brief_item(story, [])

    def write_direction_observation(self, signals):
        self.events.append("direction")
        self.budget.consume("d" * 20, item_count=len(signals))
        return super().write_direction_observation(signals)


def test_real_budget_runs_core_batches_before_limited_recovery_and_auxiliary() -> None:
    stories = [story(index) for index in range(5)]
    signal = Signal(
        id="signal-one", signal_type=SignalType.TOPIC_HEATING, topic="ai_coding",
        window_days=3, supporting_story_ids=["story-0", "story-1"],
        supporting_source_count=2, supporting_company_count=0, strength=0.7,
        explanation="Verified multi-source evidence", created_at=NOW, updated_at=NOW,
    )
    budget = AIBudget(maximum_calls=5, maximum_input_characters=300, maximum_items=8)
    provider = OrderedBudgetProvider(budget)

    result = generate_daily_brief(
        brief_date=date(2026, 9, 14), generated_at=NOW, timezone="Asia/Singapore",
        stories=stories, signals=[signal], provider=provider,
        limits=BriefLimits(maximum_items=5), enabled_sections={}, run_stats={},
    )

    items = {item.story_ids[0]: item for item in _main_items(result)}
    assert provider.events == [
        "brief:story-0,story-1,story-2,story-3", "brief:story-4",
        "recover:story-0", "recover:story-1", "direction",
    ]
    assert budget.calls_used == 3
    assert budget.input_characters_used == 290
    assert budget.input_characters_used <= budget.maximum_input_characters
    assert items["story-4"].generation_status == "generated"
    assert items["story-0"].generation_status == "generated"
    for story_id in ["story-1", "story-2", "story-3"]:
        assert items[story_id].generation_reason == "recovery_budget_unavailable"
    assert result.run_stats["ai_brief_batch_failures"] == 1
    assert result.run_stats["ai_brief_recovery_successes"] == 1
    assert result.run_stats["ai_brief_recovery_budget_exhausted"] is True
    assert result.run_stats["ai_direction_fallback"] is True


def test_radar_suppression_uses_final_visible_provenance_not_company_or_domain() -> None:
    source_url = "https://example.test/events/fable?utm_source=radar"
    context = BriefStoryContext(
        story_id="visible-story", canonical_title="Visible event", category="top_stories",
        primary_source_url=source_url,
        source_refs=[StorySourceRef(
            raw_item_id="visible-raw", title="Visible source", source_name="Example",
            source_type="rss", url=source_url, fetched_at=NOW,
        )],
    )
    visible = BriefItem(
        id="visible", section="top_stories", title="Visible event", what_happened="Fact",
        why_it_matters="Analysis", source_urls=[source_url], story_ids=["visible-story"],
        story_contexts=[context],
    )
    other_url = "https://example.test/events/other-reading"
    other = BriefItem(
        id="other", section="other_reading", title="Other visible event", what_happened="Fact",
        source_urls=[other_url], story_ids=["other-story"],
        story_contexts=[BriefStoryContext(
            story_id="other-story", canonical_title="Other visible event",
            category="ai_and_open_source",
            primary_source_url=other_url,
            source_refs=[StorySourceRef(
                raw_item_id="other-visible-raw", title="Other source", source_name="Example",
                source_type="rss", url=other_url, fetched_at=NOW,
            )],
        )],
    )
    brief = DailyBrief(
        date=date(2026, 9, 14), timezone="Asia/Singapore", generated_at=NOW,
        top_stories=[visible], other_reading=[other],
    )

    def radar(signal_id: str, *references: tuple[str, str]) -> RadarSignal:
        return RadarSignal(
            id=signal_id, observed_at=NOW, claim=signal_id, why_notable="Worth checking",
            support_refs=[ResearchEvidenceRef(raw_item_id=raw_id, url=url,
                                               source_role=SourceRole.PRACTITIONER,
                                               association_basis=(
                                                   "lead" if index == 0 else "support"
                                               ))
                          for index, (raw_id, url) in enumerate(references)],
            source_roles=[SourceRole.PRACTITIONER], missing_evidence=["Independent evidence"],
            uncertainty="Unverified", statement_type=StatementType.FIRSTHAND_OBSERVATION,
        )

    displayed = suppress_displayed_radar_duplicates(
        brief,
        [
            radar("same-raw", ("visible-raw", "https://another.test/copy")),
            radar("same-normalized-url", ("other-raw", "https://example.test/events/fable")),
            radar("same-event-with-auxiliary", ("visible-raw", source_url),
                  ("independent-raw", "https://example.test/events/independent")),
            radar("shared-auxiliary", ("independent-raw", "https://example.test/events/independent"),
                  ("visible-raw", source_url)),
            radar("independent", ("other-raw", "https://example.test/events/independent")),
            radar("same-other-reading", ("other-visible-raw", other_url)),
            radar("not-rendered-lead", ("not-rendered-raw", "https://example.test/unrendered")),
        ],
    )

    assert [signal.id for signal in displayed] == [
        "shared-auxiliary", "independent", "not-rendered-lead"
    ]


def test_research_lead_association_reaches_display_duplicate_suppression() -> None:
    def raw_item(item_id: str, url: str, role: SourceRole) -> RawItem:
        return RawItem(
            id=item_id,
            title=f"{item_id} observed structured response behaviour",
            url=url,
            source_name="Example",
            source_type="rss",
            fetched_at=NOW,
            summary="Concrete reproducible observation.",
            content_excerpt="Concrete reproducible observation with enough detail.",
            source_role=role,
            statement_type=StatementType.FIRSTHAND_OBSERVATION,
            company_candidates=["openai"],
            metadata={"score": 10},
        )

    lead_a = raw_item("lead-a", "https://example.test/lead-a", SourceRole.PRACTITIONER)
    lead_b = raw_item("lead-b", "https://example.test/lead-b", SourceRole.PRACTITIONER)
    shared_auxiliary = raw_item(
        "official-shared", "https://example.test/official", SourceRole.OFFICIAL_PRIMARY
    )

    class RadarOnlyProvider(FakeAIProvider):
        def resolve_research_cases(self, cases):
            return ResearchResolutionBatch(
                cases=[
                    ResearchResolutionDraft(
                        case_id=case.id,
                        in_scope=True,
                        scope_rationale="AI 产品行为的具体观察。",
                        disposition=ResearchDisposition.RADAR_SIGNAL,
                        statement_type=case.statement_type,
                        claim=case.claim,
                        why_notable="需要继续跟踪。",
                        uncertainty="仍需更多证据。",
                    )
                    for case in cases
                ]
            )

    research = resolve_research(
        [lead_a, lead_b, shared_auxiliary],
        provider=RadarOnlyProvider(),
        maximum_cases=8,
        maximum_radar_signals=8,
    )
    assert len(research.radar_signals) == 2
    assert all(
        signal.support_refs[0].association_basis == "lead"
        for signal in research.radar_signals
    )
    assert all(
        any(ref.raw_item_id == "official-shared" for ref in signal.support_refs)
        for signal in research.radar_signals
    )
    visible = DailyBrief(
        date=date(2026, 9, 14),
        timezone="Asia/Singapore",
        generated_at=NOW,
        top_stories=[
            BriefItem(
                id="visible-lead-a",
                section="top_stories",
                title="Visible lead A",
                what_happened="Fact",
                source_urls=[lead_a.url],
                story_ids=["visible-a"],
                story_contexts=[
                    BriefStoryContext(
                        story_id="visible-a",
                        canonical_title="Visible lead A",
                        category="top_stories",
                        primary_source_url=lead_a.url,
                        source_refs=[
                            StorySourceRef(
                                raw_item_id=lead_a.id,
                                title=lead_a.title,
                                source_name=lead_a.source_name,
                                source_type=lead_a.source_type,
                                url=lead_a.url,
                                fetched_at=NOW,
                            )
                        ],
                    )
                ],
            )
        ],
    )
    displayed = suppress_displayed_radar_duplicates(visible, research.radar_signals)
    assert [signal.support_refs[0].raw_item_id for signal in displayed] == ["lead-b"]


def test_radar_duplicate_suppression_keeps_legacy_first_reference_compatibility() -> None:
    legacy = RadarSignal(
        id="legacy",
        observed_at=NOW,
        claim="Legacy signal",
        why_notable="Worth checking",
        support_refs=[
            ResearchEvidenceRef(
                raw_item_id="visible-raw",
                url="https://example.test/visible",
                source_role=SourceRole.PRACTITIONER,
            ),
            ResearchEvidenceRef(
                raw_item_id="shared-aux",
                url="https://example.test/shared",
                source_role=SourceRole.OFFICIAL_PRIMARY,
            ),
        ],
        source_roles=[SourceRole.PRACTITIONER],
        missing_evidence=["Independent evidence"],
        uncertainty="Unverified",
        statement_type=StatementType.FIRSTHAND_OBSERVATION,
    )
    visible = DailyBrief(
        date=date(2026, 9, 14), timezone="Asia/Singapore", generated_at=NOW,
        top_stories=[
            BriefItem(
                id="visible", section="top_stories", title="Visible", what_happened="Fact",
                source_urls=["https://example.test/visible"], story_ids=["visible-story"],
                story_contexts=[
                    BriefStoryContext(
                        story_id="visible-story", canonical_title="Visible", category="top_stories",
                        primary_source_url="https://example.test/visible",
                        source_refs=[StorySourceRef(
                            raw_item_id="visible-raw", title="Visible", source_name="Example",
                            source_type="rss", url="https://example.test/visible", fetched_at=NOW,
                        )],
                    )
                ],
            )
        ],
    )
    assert suppress_displayed_radar_duplicates(visible, [legacy]) == []


class DirectionFailureProvider(FakeAIProvider):
    def write_direction_observation(self, signals):
        del signals
        raise AIOutputError("direction output failed")


def test_direction_failure_is_omitted_and_marked_without_losing_brief(caplog) -> None:
    signal = Signal(
        id="signal-one",
        signal_type=SignalType.TOPIC_HEATING,
        topic="ai_coding",
        window_days=3,
        supporting_story_ids=["story-1", "story-2"],
        supporting_source_count=2,
        supporting_company_count=0,
        strength=0.7,
        explanation="Verified multi-day evidence",
        created_at=NOW,
        updated_at=NOW,
    )

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=[story(1), story(2)],
        signals=[signal],
        provider=DirectionFailureProvider(),
        limits=BriefLimits(maximum_items=5),
        enabled_sections={},
        run_stats={},
    )

    assert result.top_stories
    assert result.direction_observation is None
    assert result.run_stats["ai_direction_fallback"] is True
    assert "AI degradation: direction observation failed" in caplog.text


class SignalRecordingProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.brief_signal_count = 0
        self.direction_signal_count = 0

    def write_brief(self, stories, signals):
        self.brief_signal_count = len(signals)
        return super().write_brief(stories, signals)

    def write_direction_observation(self, signals):
        self.direction_signal_count = len(signals)
        return super().write_direction_observation(signals)


def test_signal_ai_inputs_are_bounded_by_maximum_ai_items() -> None:
    base_signal = Signal(
        id="signal-0",
        signal_type=SignalType.TOPIC_HEATING,
        topic="ai_coding",
        window_days=3,
        supporting_story_ids=["story-1", "story-2"],
        supporting_source_count=2,
        supporting_company_count=0,
        strength=0.7,
        explanation="Verified evidence",
        created_at=NOW,
        updated_at=NOW,
    )
    signals = [
        base_signal.model_copy(update={"id": f"signal-{index}", "strength": index / 10})
        for index in range(5)
    ]
    provider = SignalRecordingProvider()

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=[story(1)],
        signals=signals,
        provider=provider,
        limits=BriefLimits(maximum_items=5),
        enabled_sections={},
        run_stats={},
        maximum_ai_items=2,
    )

    assert provider.brief_signal_count == 2
    assert provider.direction_signal_count == 2
    assert result.run_stats["ai_signal_inputs"] == 2


def test_weak_single_story_signal_skips_direction_observation() -> None:
    weak_signal = Signal(
        id="signal-weak",
        signal_type=SignalType.TOPIC_HEATING,
        topic="ai_coding",
        window_days=1,
        supporting_story_ids=["story-1"],
        supporting_source_count=1,
        supporting_company_count=0,
        strength=0.4,
        explanation="Single event only",
        created_at=NOW,
        updated_at=NOW,
    )
    provider = SignalRecordingProvider()

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=[story(1)],
        signals=[weak_signal],
        provider=provider,
        limits=BriefLimits(maximum_items=5),
        enabled_sections={},
        run_stats={},
    )

    assert provider.direction_signal_count == 0
    assert result.direction_observation is None
    assert result.run_stats["direction_signal_inputs"] == 0

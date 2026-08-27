import logging
from datetime import UTC, date, datetime

import pytest

from morning_radar.ai import AIOutputError, FakeAIProvider
from morning_radar.ai.models import BriefDraft, ClassificationBatch, GeneratedBriefItem
from morning_radar.briefing import BriefLimits, BriefValidationError, generate_daily_brief
from morning_radar.evaluation.legacy import build_stories
from morning_radar.models import Signal, SignalType, Story, StorySourceRef

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
                    story_ids=[stories[index].id],
                    section="ai_and_open_source",
                    title=f"Selected {index}",
                    what_happened="Selected story",
                    why_it_matters="Selected importance",
                    source_urls=[stories[index].primary_source_url],
                )
                for index in self.selected_indexes
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
    assert provider.write_calls == 1


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
        self.story_inputs: list[Story] = []

    def write_brief(self, stories, signals):
        self.story_inputs = list(stories)
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

    assert len(provider.story_inputs) == 12
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
    assert result.top_stories[0].why_it_matters == (
        "降级模式下暂时无法生成重要性分析，请查看已验证事实与来源。"
    )
    assert result.top_stories[0].uncertainty == "AI 晨报分析暂时不可用。"
    assert result.top_stories[0].source_urls == source_story.source_urls
    assert result.top_stories[0].story_contexts[0].story_id == source_story.id
    assert result.top_stories[0].story_contexts[0].source_refs == []
    assert result.other_reading == []
    assert result.run_stats["ai_brief_fallback"] is True
    assert "AI degradation: brief generation failed" in caplog.text


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

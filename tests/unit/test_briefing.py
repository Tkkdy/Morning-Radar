from datetime import UTC, date, datetime

import pytest

from morning_radar.ai import FakeAIProvider
from morning_radar.ai.models import BriefDraft, GeneratedBriefItem
from morning_radar.briefing import BriefLimits, BriefValidationError, generate_daily_brief
from morning_radar.models import Story

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


def test_source_links_are_complete_and_traceable() -> None:
    source_story = story(1)
    result = brief([source_story])

    assert result.top_stories[0].source_urls == source_story.source_urls


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


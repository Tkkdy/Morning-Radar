from datetime import UTC, datetime

import pytest

from morning_radar.ai import FakeAIProvider
from morning_radar.ai.models import MergedStoryDraft
from morning_radar.models import RawItem
from morning_radar.processing.story_builder import (
    StoryValidationError,
    build_stories,
    build_story,
    choose_primary_source,
    ranking_score,
)

NOW = datetime(2026, 7, 23, 1, tzinfo=UTC)


def item(
    item_id: str,
    title: str,
    url: str,
    *,
    source: str,
    official: bool = False,
    priority: str = "low",
) -> RawItem:
    return RawItem(
        id=item_id,
        title=title,
        url=url,
        source_name=source,
        source_type="fixture",
        published_at=NOW,
        fetched_at=NOW,
        summary=f"Fact from {source}",
        metadata={"official": official, "priority": priority},
    )


def test_primary_source_prefers_official_over_community_popularity() -> None:
    community = item(
        "community",
        "Release",
        "https://news.example/release",
        source="Community",
        priority="high",
    )
    official = item(
        "official",
        "Release",
        "https://lab.example/release",
        source="Lab",
        official=True,
        priority="medium",
    )

    assert choose_primary_source([community, official]) == official


def test_same_event_multiple_sources_becomes_one_story_with_complete_links() -> None:
    items = [
        item("one", "Agent release", "https://one.example/release", source="One"),
        item("two", "AGENT  RELEASE", "https://two.example/release", source="Two"),
    ]

    stories = build_stories(items, provider=FakeAIProvider(), now=NOW)

    assert len(stories) == 1
    assert set(stories[0].source_urls) == {value.url for value in items}
    assert set(stories[0].source_item_ids) == {"one", "two"}


def test_different_versions_remain_separate_stories() -> None:
    items = [
        item("one", "Agent v1.2 released", "https://example.com/v1.2", source="One"),
        item("two", "Agent v1.3 released", "https://example.com/v1.3", source="One"),
    ]

    assert len(build_stories(items, provider=FakeAIProvider(), now=NOW)) == 2


class InventingProvider(FakeAIProvider):
    def merge_story(self, items: list[RawItem]) -> MergedStoryDraft:
        return MergedStoryDraft(
            same_event=True,
            canonical_title=items[0].title,
            category="top_stories",
            source_urls=["https://invented.example/claim"],
            primary_source_url="https://invented.example/claim",
        )


def test_business_layer_rejects_provider_invented_url() -> None:
    with pytest.raises(StoryValidationError, match="outside source"):
        build_story(
            [item("one", "Release", "https://real.example/release", source="Real")],
            provider=InventingProvider(),
            now=NOW,
        )


def test_ranking_weights_importance_more_than_novelty() -> None:
    base = item("one", "Release", "https://example.com/release", source="Real")
    important = build_story([base], provider=FakeAIProvider(), now=NOW)
    novelty_only = important.model_copy(
        update={
            "importance_score": 0,
            "relevance_score": 0,
            "credibility_score": 0,
            "novelty_score": 1,
        }
    )

    assert ranking_score(important) > ranking_score(novelty_only)


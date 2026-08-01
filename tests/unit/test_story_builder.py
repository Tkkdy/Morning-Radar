from datetime import UTC, datetime

import pytest

from morning_radar.ai import AIBudget, AIOutputError, FakeAIProvider
from morning_radar.ai.models import ClassificationBatch, MergedStoryDraft
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
    with pytest.raises(StoryValidationError, match="outside verified source set"):
        build_story(
            [item("one", "Release", "https://real.example/release", source="Real")],
            provider=InventingProvider(),
            now=NOW,
        )


class HnDiscussionProvider(FakeAIProvider):
    discussion_url = "https://news.ycombinator.com/item?id=49132412"

    def merge_story(self, items: list[RawItem]) -> MergedStoryDraft:
        return MergedStoryDraft(
            same_event=True,
            canonical_title=items[0].title,
            category="developer_discussions",
            source_urls=[items[0].url, self.discussion_url],
            primary_source_url=items[0].url,
        )


def test_story_builder_accepts_and_preserves_verified_hn_sources() -> None:
    original_url = "https://example.com/hn-story"
    discussion_url = HnDiscussionProvider.discussion_url
    hn_item = item(
        "hn-one",
        "HN AI release",
        original_url,
        source="Hacker News",
    ).model_copy(
        update={
            "source_type": "hacker_news",
            "metadata": {
                "official": False,
                "discussion_url": discussion_url,
                "original_url": original_url,
                "community_signal": True,
            },
        }
    )

    story = build_story([hn_item], provider=HnDiscussionProvider(), now=NOW)

    assert story.primary_source_url == original_url
    assert story.source_urls == [original_url, discussion_url]


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


class RecordingProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.classified_count = 0
        self.budget = AIBudget(100, 100_000, 40)

    def classify_items(self, items):
        self.classified_count = len(items)
        self.budget.consume("fixture payload", item_count=len(items))
        return super().classify_items(items)


def test_ai_candidate_cap_is_applied_before_classification() -> None:
    provider = RecordingProvider()
    items = [
        item(
            f"item-{index}",
            f"Candidate {index}",
            f"https://example.com/{index}",
            source="Fixture",
        )
        for index in range(45)
    ]

    stories = build_stories(
        items,
        provider=provider,
        now=NOW,
        maximum_ai_items=40,
    )

    assert provider.classified_count == 40
    assert provider.budget.calls_used == 1
    assert len(stories) == 40


class ClassificationFailureProvider(FakeAIProvider):
    def classify_items(self, items):
        del items
        raise AIOutputError("invalid structured output")


def test_no_input_skips_classification_normally() -> None:
    assert build_stories([], provider=ClassificationFailureProvider(), now=NOW) == []


class ZeroRelevantProvider(FakeAIProvider):
    def classify_items(self, items):
        del items
        return ClassificationBatch(items=[])


def test_successful_classification_with_zero_relevant_items_is_normally_empty() -> None:
    stories = build_stories(
        [item("one", "Release", "https://example.com/one", source="Fixture")],
        provider=ZeroRelevantProvider(),
        now=NOW,
    )

    assert stories == []


def test_global_classification_ai_failure_propagates() -> None:
    with pytest.raises(AIOutputError, match="invalid structured output"):
        build_stories(
            [item("one", "Release", "https://example.com/one", source="Fixture")],
            provider=ClassificationFailureProvider(),
            now=NOW,
        )


class PartialMergeFailureProvider(FakeAIProvider):
    def merge_story(self, items):
        if items[0].id == "broken":
            raise AIOutputError("invalid merge output")
        return super().merge_story(items)


def test_one_story_failure_does_not_discard_other_candidates(caplog) -> None:
    stories = build_stories(
        [
            item("broken", "Broken candidate", "https://example.com/broken", source="One"),
            item("valid", "Valid candidate", "https://example.com/valid", source="Two"),
        ],
        provider=PartialMergeFailureProvider(),
        now=NOW,
    )

    assert [story.source_item_ids for story in stories] == [["valid"]]
    assert "AI degradation: merge failed" in caplog.text

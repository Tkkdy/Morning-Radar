from datetime import UTC, datetime

import pytest

from morning_radar.ai import AIBudget, AIOutputError, FakeAIProvider
from morning_radar.ai.models import ClassificationBatch, MergedStoryDraft
from morning_radar.evaluation.legacy import build_stories, preselect_ai_candidates
from morning_radar.models import (
    PublishedAtRole,
    RawItem,
    SourceRole,
    StatementType,
)
from morning_radar.processing.story_builder import (
    StoryValidationError,
    build_story,
    choose_primary_source,
    filter_story_candidate_inputs,
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


def test_story_source_ref_preserves_rss_collector_context() -> None:
    rss_item = item(
        "rss-one",
        "RSS release",
        "https://example.com/releases/1",
        source="Example RSS",
    ).model_copy(update={"source_type": "rss", "author": "Ada"})

    story = build_story([rss_item], provider=FakeAIProvider(), now=NOW)

    assert [source_ref.model_dump() for source_ref in story.source_refs] == [
        {
            "raw_item_id": "rss-one",
            "title": "RSS release",
            "source_name": "Example RSS",
            "source_type": "rss",
            "url": "https://example.com/releases/1",
            "author": "Ada",
            "published_at": NOW,
            "published_at_role": PublishedAtRole.FEED_ENTRY_TIME,
            "fetched_at": NOW,
                "discussion_url": None,
                "source_role": SourceRole.EDITORIAL,
                "statement_type": StatementType.UNKNOWN,
                "practice_signal_kind": None,
        }
    ]


@pytest.mark.parametrize(
    ("source_type", "published_at_role"),
    [
        ("rss", PublishedAtRole.FEED_ENTRY_TIME),
        ("atom", PublishedAtRole.FEED_ENTRY_TIME),
        ("github", PublishedAtRole.GITHUB_RELEASE_PUBLISHED_TIME),
        ("market", PublishedAtRole.MARKET_TRADING_DAY),
        ("fixture", PublishedAtRole.UNKNOWN),
    ],
)
def test_story_source_ref_assigns_published_at_role_by_source_type(
    source_type: str,
    published_at_role: PublishedAtRole,
) -> None:
    source_item = item(
        "source-one",
        "Release",
        "https://example.com/release",
        source="Example",
    ).model_copy(update={"source_type": source_type})

    story = build_story([source_item], provider=FakeAIProvider(), now=NOW)

    assert story.source_refs[0].published_at_role is published_at_role


def test_rss_source_ref_keeps_none_published_at_without_inventing_time() -> None:
    source_item = item(
        "rss-one",
        "RSS release",
        "https://example.com/releases/1",
        source="Example RSS",
    ).model_copy(update={"source_type": "rss", "published_at": None})

    story = build_story([source_item], provider=FakeAIProvider(), now=NOW)

    assert story.source_refs[0].published_at is None
    assert story.source_refs[0].published_at_role is PublishedAtRole.FEED_ENTRY_TIME


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
    assert story.source_refs[0].url == original_url
    assert story.source_refs[0].discussion_url == discussion_url
    assert story.source_refs[0].published_at == NOW
    assert story.source_refs[0].published_at_role is PublishedAtRole.HN_SUBMISSION_TIME


def test_story_source_ref_rejects_unverified_hn_discussion_url() -> None:
    hn_item = item(
        "hn-one",
        "HN AI release",
        "https://example.com/hn-story",
        source="Hacker News",
    ).model_copy(
        update={
            "source_type": "hacker_news",
            "metadata": {
                "discussion_url": "https://news.ycombinator.com/newest?id=49132412"
            },
        }
    )

    story = build_story([hn_item], provider=FakeAIProvider(), now=NOW)

    assert story.source_refs[0].discussion_url is None


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


def test_routine_market_gate_suppresses_only_valid_subthreshold_moves() -> None:
    routine = [
        item(
            f"market-{index}",
            f"Market move {index}",
            f"https://finance.example/{index}",
            source="Market",
        ).model_copy(
            update={
                "source_type": "market",
                "metadata": {"change_percent": change},
            }
        )
        for index, change in enumerate((0.0044, 0.0283, 0.0037, 0.0082, 0.0029, -0.0121, 0.0003))
    ]
    significant = item(
        "market-significant",
        "Large market move",
        "https://finance.example/significant",
        source="Market",
    ).model_copy(
        update={"source_type": "market", "metadata": {"change_percent": -0.03}}
    )
    invalid = [
        item(
            f"market-invalid-{index}",
            "Unknown market move",
            f"https://finance.example/invalid/{index}",
            source="Market",
        ).model_copy(
            update={"source_type": "market", "metadata": {"change_percent": value}}
        )
        for index, value in enumerate((None, "0.01", True, float("nan")))
    ]
    news = item(
        "news",
        "Company launches a product",
        "https://news.example/product",
        source="News",
    )

    selected, suppressed = filter_story_candidate_inputs(
        [*routine, significant, *invalid, news],
        market_movement_threshold=0.03,
    )

    assert suppressed == 7
    assert {value.id for value in selected} == {
        "market-significant",
        "market-invalid-0",
        "market-invalid-1",
        "market-invalid-2",
        "market-invalid-3",
        "news",
    }


def _lane_item(
    item_id: str,
    *,
    source_type: str,
    official: bool = False,
    priority: str = "low",
    hour: int = 0,
) -> RawItem:
    return item(
        item_id,
        f"Candidate {item_id}",
        f"https://example.com/{item_id}",
        source=item_id,
        official=official,
        priority=priority,
    ).model_copy(
        update={
            "source_type": source_type,
            "published_at": NOW.replace(hour=hour),
        }
    )


def test_preselection_reserves_nonempty_source_lanes_under_cap() -> None:
    official = [
        _lane_item(
            f"official-{index}",
            source_type="rss",
            official=True,
            priority="high",
            hour=index,
        )
        for index in range(8)
    ]
    github = _lane_item("github", source_type="github", official=True, priority="high")
    secondary = _lane_item("secondary", source_type="rss", priority="medium")
    hacker_news = _lane_item("hn", source_type="hacker_news")

    selected = preselect_ai_candidates(
        [*official, github, secondary, hacker_news],
        maximum_items=4,
    )

    assert len(selected) == 4
    assert {value.id for value in selected} == {
        "official-7",
        "github",
        "secondary",
        "hn",
    }


def test_preselection_fills_empty_lane_capacity_and_is_deterministic() -> None:
    candidates = [
        _lane_item(
            "older-medium",
            source_type="rss",
            official=True,
            priority="medium",
            hour=1,
        ),
        _lane_item(
            "newer-medium",
            source_type="rss",
            official=True,
            priority="medium",
            hour=2,
        ),
        _lane_item(
            "low",
            source_type="rss",
            official=True,
            priority="low",
            hour=3,
        ),
    ]

    first = preselect_ai_candidates(candidates, maximum_items=2)
    second = preselect_ai_candidates(list(reversed(candidates)), maximum_items=2)

    assert [value.id for value in first] == ["newer-medium", "older-medium"]
    assert [value.id for value in second] == ["newer-medium", "older-medium"]
    assert preselect_ai_candidates(candidates, maximum_items=0) == []


def test_deepseek_like_high_signal_hn_item_survives_editorial_pressure() -> None:
    editorial = [
        _lane_item(
            f"editorial-{index}",
            source_type="rss",
            priority="medium",
            hour=index % 4,
        )
        for index in range(30)
    ]
    deepseek_signal = _lane_item(
        "deepseek-v4-pro-practice",
        source_type="hacker_news",
    ).model_copy(
        update={
            "source_role": SourceRole.COMMUNITY_DISCOVERY,
            "metadata": {
                "selection_reason": "high_signal_discovery",
                "score": 320,
                "comments": 140,
                "community_signal": True,
            },
        }
    )

    selected = preselect_ai_candidates(
        [*editorial, deepseek_signal], maximum_items=5
    )

    assert deepseek_signal in selected


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

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from morning_radar.models import BriefItem, RawItem, Story


def make_raw_item(**overrides: object) -> RawItem:
    values: dict[str, object] = {
        "id": "item-1",
        "title": "A real release",
        "url": "https://example.com/releases/1",
        "source_name": "Example",
        "source_type": "rss",
        "published_at": datetime(2026, 7, 22, 23, tzinfo=UTC),
        "fetched_at": datetime(2026, 7, 23, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return RawItem.model_validate(values)


def test_timezone_serialization_and_round_trip() -> None:
    item = make_raw_item()

    restored = RawItem.model_validate_json(item.model_dump_json())

    assert restored.published_at == item.published_at
    assert restored.fetched_at.tzinfo is not None


def test_missing_published_time_is_allowed() -> None:
    item = make_raw_item(published_at=None)

    assert item.published_at is None


@pytest.mark.parametrize("url", ["not-a-url", "ftp://example.com/file", "/relative"])
def test_raw_item_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(ValidationError, match="absolute http"):
        make_raw_item(url=url)


def test_raw_item_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        make_raw_item(fetched_at=datetime(2026, 7, 23, 1))


def test_story_primary_source_must_be_in_source_urls() -> None:
    with pytest.raises(ValidationError, match="present in source_urls"):
        Story(
            id="story-1",
            canonical_title="Release",
            category="ai_and_open_source",
            published_at=None,
            updated_at=datetime(2026, 7, 23, tzinfo=UTC),
            source_item_ids=["item-1"],
            source_urls=["https://example.com/a"],
            primary_source_url="https://example.com/b",
            relevance_score=1,
            importance_score=0.8,
            novelty_score=0.8,
            credibility_score=1,
        )


def test_old_story_json_without_source_refs_loads_with_an_empty_list() -> None:
    legacy = {
        "id": "story-1",
        "canonical_title": "Release",
        "category": "ai_and_open_source",
        "published_at": None,
        "updated_at": "2026-07-23T00:00:00Z",
        "source_item_ids": ["item-1"],
        "source_urls": ["https://example.com/a"],
        "primary_source_url": "https://example.com/a",
        "relevance_score": 1,
        "importance_score": 0.8,
        "novelty_score": 0.8,
        "credibility_score": 1,
    }

    assert Story.model_validate(legacy).source_refs == []


@pytest.mark.parametrize(
    ("source_ref", "error"),
    [
        (
            {"url": "https://example.com/other"},
            "source_ref url must be present in source_urls",
        ),
        (
            {"discussion_url": "https://news.ycombinator.com/item?id=123"},
            "source_ref discussion_url must be present in source_urls",
        ),
        (
            {"raw_item_id": "item-other"},
            "source_ref raw_item_id must be present in source_item_ids",
        ),
    ],
)
def test_story_source_refs_cannot_expand_story_provenance(
    source_ref: dict[str, str],
    error: str,
) -> None:
    values = {
        "id": "story-1",
        "canonical_title": "Release",
        "category": "ai_and_open_source",
        "published_at": None,
        "updated_at": "2026-07-23T00:00:00Z",
        "source_item_ids": ["item-1"],
        "source_urls": ["https://example.com/a"],
        "primary_source_url": "https://example.com/a",
        "relevance_score": 1,
        "importance_score": 0.8,
        "novelty_score": 0.8,
        "credibility_score": 1,
        "source_refs": [
            {
                "raw_item_id": "item-1",
                "title": "Release",
                "source_name": "Example",
                "source_type": "hacker_news",
                "url": "https://example.com/a",
                "author": None,
                "published_at": None,
                "fetched_at": "2026-07-23T00:00:00Z",
                "discussion_url": None,
                **source_ref,
            }
        ],
    }

    with pytest.raises(ValidationError, match=error):
        Story.model_validate(values)


def test_old_brief_item_json_without_story_contexts_loads_with_an_empty_list() -> None:
    legacy = {
        "id": "brief-1",
        "section": "top_stories",
        "title": "Release",
        "what_happened": "A release happened.",
        "why_it_matters": "It matters.",
        "source_urls": ["https://example.com/a"],
        "story_ids": ["story-1"],
    }

    assert BriefItem.model_validate(legacy).story_contexts == []


def _brief_story_context(story_id: str) -> dict[str, object]:
    return {
        "story_id": story_id,
        "canonical_title": f"Title for {story_id}",
        "category": "ai_and_open_source",
        "primary_source_url": "https://example.com/a",
    }


@pytest.mark.parametrize(
    "story_contexts",
    [
        [_brief_story_context("story-a"), _brief_story_context("story-other")],
        [_brief_story_context("story-a")],
        [_brief_story_context("story-b"), _brief_story_context("story-a")],
    ],
    ids=["unknown-story-id", "missing-context", "wrong-order"],
)
def test_nonempty_brief_story_contexts_must_exactly_match_story_ids(
    story_contexts: list[dict[str, object]],
) -> None:
    values = {
        "id": "brief-1",
        "section": "top_stories",
        "title": "Release",
        "what_happened": "A release happened.",
        "why_it_matters": "It matters.",
        "source_urls": ["https://example.com/a"],
        "story_ids": ["story-a", "story-b"],
        "story_contexts": story_contexts,
    }

    with pytest.raises(ValidationError, match="story_contexts must exactly match"):
        BriefItem.model_validate(values)

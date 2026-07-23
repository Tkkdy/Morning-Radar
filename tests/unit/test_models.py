from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from morning_radar.models import RawItem, Story


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


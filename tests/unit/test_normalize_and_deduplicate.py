from datetime import UTC, datetime

from morning_radar.models import RawItem
from morning_radar.processing import (
    deduplicate_items,
    group_items_by_normalized_title,
    normalize_title,
    normalize_url,
    stable_item_id,
)


def item(item_id: str, title: str, url: str, source: str = "Source") -> RawItem:
    return RawItem(
        id=item_id,
        title=title,
        url=url,
        source_name=source,
        source_type="fixture",
        fetched_at=datetime(2026, 7, 23, tzinfo=UTC),
    )


def test_normalize_url_removes_tracking_and_fragment_but_keeps_business_query() -> None:
    original = "HTTPS://Example.COM/story/?id=7&utm_source=rss&fbclid=x#comments"

    assert normalize_url(original) == "https://example.com/story?id=7"


def test_stable_id_ignores_tracking_parameters() -> None:
    base = "https://example.com/story?id=7"
    tracked = "https://example.com/story/?utm_campaign=daily&id=7"

    assert stable_item_id(base) == stable_item_id(tracked)


def test_title_normalization_handles_case_unicode_and_spacing() -> None:
    assert normalize_title("  Agent Ｖ1.3 — Released ") == "agent v1.3 released"


def test_exact_and_tracking_url_duplicates_are_removed() -> None:
    items = [
        item("1", "First title", "https://example.com/a"),
        item("2", "Other title", "https://example.com/a#discussion"),
        item("3", "Third", "https://example.com/a?utm_source=rss"),
    ]

    assert [value.id for value in deduplicate_items(items)] == ["1"]


def test_title_case_and_space_duplicates_from_same_source_are_removed() -> None:
    items = [
        item("1", "Agent release", "https://example.com/a"),
        item("2", " AGENT   RELEASE ", "https://example.com/b"),
    ]

    assert [value.id for value in deduplicate_items(items)] == ["1"]


def test_same_title_from_different_sources_is_kept_for_event_evidence() -> None:
    items = [
        item("1", "Agent release", "https://one.example/a", "One"),
        item("2", "Agent release", "https://two.example/a", "Two"),
    ]

    unique = deduplicate_items(items)
    groups = group_items_by_normalized_title(unique)

    assert len(unique) == 2
    assert len(groups) == 1
    assert {value.id for value in groups[0]} == {"1", "2"}


def test_different_versions_are_not_merged() -> None:
    items = [
        item("1", "Agent v1.2 released", "https://example.com/v1.2"),
        item("2", "Agent v1.3 released", "https://example.com/v1.3"),
    ]

    groups = group_items_by_normalized_title(deduplicate_items(items))

    assert len(groups) == 2


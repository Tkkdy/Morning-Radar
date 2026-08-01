from datetime import UTC, datetime

import pytest

from morning_radar.models import RawItem
from morning_radar.provenance import verified_source_urls

NOW = datetime(2026, 8, 1, tzinfo=UTC)
ORIGINAL_URL = "https://example.com/article"
DISCUSSION_URL = "https://news.ycombinator.com/item?id=49132412"


def item(
    *,
    source_type: str = "hacker_news",
    url: str = ORIGINAL_URL,
    discussion_url: object = DISCUSSION_URL,
) -> RawItem:
    return RawItem(
        id="item-one",
        title="AI story",
        url=url,
        source_name="Hacker News" if source_type == "hacker_news" else "Fixture",
        source_type=source_type,
        fetched_at=NOW,
        metadata={
            "discussion_url": discussion_url,
            "original_url": url,
            "community_signal": True,
        },
    )


def test_ordinary_item_only_verifies_primary_url() -> None:
    ordinary = item(source_type="fixture")

    assert verified_source_urls(ordinary) == (ORIGINAL_URL,)


def test_hn_item_verifies_original_and_exact_discussion_url() -> None:
    assert verified_source_urls(item()) == (ORIGINAL_URL, DISCUSSION_URL)


def test_verified_urls_are_stable_and_deduplicated() -> None:
    discussion_only = item(url=DISCUSSION_URL)

    assert verified_source_urls(discussion_only) == (DISCUSSION_URL,)


def test_metadata_original_url_does_not_add_a_third_source() -> None:
    source = item().model_copy(
        update={
            "metadata": {
                **item().metadata,
                "original_url": "https://unverified.example/other",
            }
        }
    )

    assert verified_source_urls(source) == (ORIGINAL_URL, DISCUSSION_URL)


@pytest.mark.parametrize(
    "discussion_url",
    [
        "http://news.ycombinator.com/item?id=49132412",
        "https://evil.example/item?id=49132412",
        "https://news.ycombinator.com/newest?id=49132412",
        "https://news.ycombinator.com/item?id=not-a-number",
        "https://news.ycombinator.com/item?id=0",
        "https://news.ycombinator.com/item?id=49132412&next=1",
        "https://news.ycombinator.com/item?id=49132412&",
        "https://news.ycombinator.com/item?id=%34%39%31%33%32%34%31%32",
        "https://news.ycombinator.com/item?id=49132412#comments",
        49132412,
    ],
)
def test_invalid_hn_discussion_metadata_is_not_verified(
    discussion_url: object,
) -> None:
    assert verified_source_urls(item(discussion_url=discussion_url)) == (ORIGINAL_URL,)


def test_hn_item_a_does_not_verify_item_b_discussion_url() -> None:
    item_b = "https://news.ycombinator.com/item?id=49130604"

    assert item_b not in verified_source_urls(item())

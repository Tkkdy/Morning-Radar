from datetime import UTC, datetime, timedelta

import pytest

from morning_radar.models import RawItem
from morning_radar.processing.filtering import filter_news_window
from morning_radar.time_utils import (
    collection_window,
    display_date,
    is_within_past_hours,
    to_display_timezone,
)


def test_utc_to_singapore_crosses_date_boundary() -> None:
    utc_value = datetime(2026, 7, 22, 17, 30, tzinfo=UTC)

    shown = to_display_timezone(utc_value)

    assert shown.isoformat() == "2026-07-23T01:30:00+08:00"
    assert display_date(utc_value).isoformat() == "2026-07-23"


def test_past_24_hours_includes_exact_lower_boundary() -> None:
    now = datetime(2026, 7, 23, 1, tzinfo=UTC)

    assert is_within_past_hours(now - timedelta(hours=24), now=now, hours=24)
    assert not is_within_past_hours(
        now - timedelta(hours=24, seconds=1), now=now, hours=24
    )
    assert not is_within_past_hours(now + timedelta(seconds=1), now=now, hours=24)


def test_missing_published_time_is_not_in_news_window() -> None:
    assert not is_within_past_hours(
        None,
        now=datetime(2026, 7, 23, tzinfo=UTC),
        hours=24,
    )


def test_collection_window_includes_six_hour_buffer() -> None:
    now = datetime(2026, 7, 23, 1, tzinfo=UTC)

    start, end = collection_window(now=now, news_hours=24, buffer_hours=6)

    assert end - start == timedelta(hours=30)


def test_naive_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        to_display_timezone(datetime(2026, 7, 23))


def test_news_filter_uses_published_time_and_marks_missing_time() -> None:
    now = datetime(2026, 7, 23, 1, tzinfo=UTC)

    def item(item_id: str, published_at: datetime | None, fetched_at: datetime) -> RawItem:
        return RawItem(
            id=item_id,
            title=item_id,
            url=f"https://example.com/{item_id}",
            source_name="Fixture",
            source_type="fixture",
            published_at=published_at,
            fetched_at=fetched_at,
        )

    result = filter_news_window(
        [
            item("recent", now - timedelta(hours=23), now),
            item("old", now - timedelta(hours=25), now),
            item("missing", None, now - timedelta(hours=1)),
        ],
        now=now,
        hours=24,
    )

    assert [value.id for value in result] == ["recent", "missing"]
    assert result[1].metadata["published_time_missing"] is True


def test_monday_keeps_friday_market_snapshot_but_not_friday_news() -> None:
    now = datetime(2026, 7, 27, 1, tzinfo=UTC)
    friday = datetime(2026, 7, 24, tzinfo=UTC)
    market = RawItem(
        id="market-friday",
        title="Friday close",
        url="https://finance.example/quote",
        source_name="Market",
        source_type="market",
        published_at=friday,
        fetched_at=now,
        metadata={"freshness_policy": "latest_market_trading_day"},
    )
    news = market.model_copy(
        update={
            "id": "news-friday",
            "source_type": "rss",
            "metadata": {},
        }
    )

    result = filter_news_window([market, news], now=now, hours=24)

    assert [item.id for item in result] == ["market-friday"]
    assert result[0].published_at == friday


def test_market_snapshot_older_than_normal_long_weekend_is_not_kept() -> None:
    now = datetime(2026, 7, 28, 1, tzinfo=UTC)
    item = RawItem(
        id="stale-market",
        title="Old close",
        url="https://finance.example/quote",
        source_name="Market",
        source_type="market",
        published_at=datetime(2026, 7, 24, tzinfo=UTC),
        fetched_at=now,
        metadata={"freshness_policy": "latest_market_trading_day"},
    )

    assert filter_news_window([item], now=now, hours=24) == []

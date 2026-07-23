from datetime import UTC, datetime, timedelta

import pytest

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


"""Timezone and collection-window helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

DISPLAY_TIMEZONE = ZoneInfo("Asia/Singapore")


def require_aware(value: datetime) -> datetime:
    """Reject naive datetimes before they can silently shift a collection window."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_display_timezone(value: datetime) -> datetime:
    return require_aware(value).astimezone(DISPLAY_TIMEZONE)


def display_date(value: datetime) -> date:
    return to_display_timezone(value).date()


def hours_ago(value: datetime, *, hours: int) -> datetime:
    if hours < 0:
        raise ValueError("hours must be non-negative")
    return require_aware(value) - timedelta(hours=hours)


def is_within_past_hours(
    published_at: datetime | None,
    *,
    now: datetime,
    hours: int,
) -> bool:
    """Return False for missing/future timestamps and include the lower boundary."""
    if published_at is None:
        return False
    current = require_aware(now).astimezone(UTC)
    published = require_aware(published_at).astimezone(UTC)
    return current - timedelta(hours=hours) <= published <= current


def collection_window(
    *,
    now: datetime,
    news_hours: int,
    buffer_hours: int,
) -> tuple[datetime, datetime]:
    """Return the wider fetch window; news filtering happens after normalization."""
    if news_hours <= 0 or buffer_hours < 0:
        raise ValueError("news_hours must be positive and buffer_hours non-negative")
    end = require_aware(now).astimezone(UTC)
    return end - timedelta(hours=news_hours + buffer_hours), end

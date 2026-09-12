"""Apply the final news window after the wider collection buffer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from morning_radar.models import RawItem
from morning_radar.time_utils import is_within_past_hours


def filter_news_window(
    items: list[RawItem],
    *,
    now: datetime,
    hours: int,
) -> list[RawItem]:
    filtered: list[RawItem] = []
    for item in items:
        if item.metadata.get("date_precision") == "day":
            source_date = item.metadata.get("source_date")
            try:
                start = datetime.fromisoformat(str(source_date)).replace(
                    tzinfo=ZoneInfo("Asia/Singapore")
                )
            except ValueError:
                continue
            end = start + timedelta(days=1)
            lower = now.astimezone(UTC) - timedelta(hours=hours)
            if start.astimezone(UTC) <= now.astimezone(UTC) and end.astimezone(UTC) >= lower:
                filtered.append(item)
            continue
        if item.published_at is not None:
            if is_within_past_hours(
                item.published_at,
                now=now,
                hours=hours,
            ) or _is_recent_market_snapshot(item, now=now):
                filtered.append(item)
            continue
        if is_within_past_hours(item.fetched_at, now=now, hours=hours):
            metadata = {**item.metadata, "published_time_missing": True}
            filtered.append(item.model_copy(update={"metadata": metadata}))
    return filtered


def _is_recent_market_snapshot(item: RawItem, *, now: datetime) -> bool:
    """Keep the latest trading-day snapshot across a normal three-day weekend."""
    if item.metadata.get("freshness_policy") != "latest_market_trading_day":
        return False
    if item.published_at is None:
        return False
    current_day = now.astimezone(UTC).date()
    trading_day = item.published_at.astimezone(UTC).date()
    return 0 <= (current_day - trading_day).days <= 3

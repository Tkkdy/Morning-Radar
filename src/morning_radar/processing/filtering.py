"""Apply the final news window after the wider collection buffer."""

from __future__ import annotations

from datetime import datetime

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
        if item.published_at is not None:
            if is_within_past_hours(item.published_at, now=now, hours=hours):
                filtered.append(item)
            continue
        if is_within_past_hours(item.fetched_at, now=now, hours=hours):
            metadata = {**item.metadata, "published_time_missing": True}
            filtered.append(item.model_copy(update={"metadata": metadata}))
    return filtered


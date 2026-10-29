"""Bounded eligibility for items first discovered after the news window."""

from __future__ import annotations

from datetime import datetime

from morning_radar.intake.models import IntakeRecord
from morning_radar.processing.filtering import filter_news_window
from morning_radar.time_utils import hours_ago


def late_discovery_reason(
    record: IntakeRecord,
    *,
    now: datetime,
    normal_hours: int,
    lookback_days: int,
) -> str:
    """Classify an out-of-window record without treating fetch time as publication.

    A late item remains bounded by both its durable first observation and its
    source publication date. Timely captures remain actionable; newly found
    old articles still require high-priority primary-source provenance.
    Downstream relevance, evidence, and publish decisions remain unchanged.
    """
    item = record.item
    if item.metadata.get("official_page_fetched") and item.metadata.get("event_time_unverified"):
        return "missing_event_time"
    if item.published_at is not None and item.published_at > now:
        return "future_timestamp"
    if filter_news_window([item], now=now, hours=normal_hours):
        return "fresh"
    lookback_hours = lookback_days * 24
    first_seen = record.durable_at or record.first_seen_at
    if first_seen < hours_ago(now, hours=lookback_hours):
        return "expired"
    if item.published_at is None and item.metadata.get("date_precision") != "day":
        return "missing_event_time"
    if not filter_news_window([item], now=now, hours=lookback_hours):
        return "expired"
    # A timely durable capture is unfinished work, not a newly discovered old article.
    # Keep it recoverable within the same bounded publication AND durability window.
    if filter_news_window([item], now=first_seen, hours=normal_hours):
        return "eligible_backlog"
    if item.source_role.value != "official_primary" or item.metadata.get("priority") != "high":
        return "low_value"
    return "eligible_late"

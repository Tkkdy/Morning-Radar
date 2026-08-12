"""Explainable signal detection from structured multi-day evidence."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta

from morning_radar.models import (
    GitHubSnapshot,
    MarketSnapshot,
    Signal,
    SignalType,
    Story,
    StoryStatus,
)
from morning_radar.time_utils import display_date

STATUS_ORDER = {
    StoryStatus.UNKNOWN: 0,
    StoryStatus.RUMOR: 1,
    StoryStatus.OFFICIAL_TEASER: 2,
    StoryStatus.ANNOUNCED: 3,
    StoryStatus.AVAILABLE: 4,
    StoryStatus.UPDATED: 5,
}


def _signal_id(signal_type: SignalType, topic: str, current_date: date) -> str:
    value = f"{signal_type}:{topic}:{current_date}"
    return f"signal-{hashlib.sha256(value.encode()).hexdigest()[:20]}"


def _make_signal(
    *,
    signal_type: SignalType,
    topic: str,
    window_days: int,
    stories: list[Story],
    strength: float,
    explanation: str,
    now: datetime,
    current_date: date,
    metric_history: list[dict[str, object]] | None = None,
    uncertainties: list[str] | None = None,
    supporting_company_count: int | None = None,
) -> Signal:
    source_urls = {url for story in stories for url in story.source_urls}
    companies = {name for story in stories for name in story.entity_names}
    return Signal(
        id=_signal_id(signal_type, topic, current_date),
        signal_type=signal_type,
        topic=topic,
        window_days=window_days,
        supporting_story_ids=list(dict.fromkeys(story.id for story in stories)),
        supporting_source_count=len(source_urls),
        supporting_company_count=(
            len(companies)
            if supporting_company_count is None
            else supporting_company_count
        ),
        metric_history=metric_history or [],
        strength=max(0, min(1, strength)),
        explanation=explanation,
        uncertainties=uncertainties or [],
        created_at=now,
        updated_at=now,
    )


def detect_topic_heating(
    story_history: dict[date, list[Story]],
    *,
    current_date: date,
    now: datetime,
) -> list[Signal]:
    required_dates = [current_date - timedelta(days=offset) for offset in (2, 1, 0)]
    topics: set[str] = set()
    for day in required_dates:
        for story in story_history.get(day, []):
            topics.update(story.topic_names)

    signals: list[Signal] = []
    for topic in topics:
        daily = [
            [story for story in story_history.get(day, []) if topic in story.topic_names]
            for day in required_dates
        ]
        seen_story_ids: set[str] = set()
        unique_daily: list[list[Story]] = []
        for stories in daily:
            new_stories = [
                story for story in stories if story.id not in seen_story_ids
            ]
            seen_story_ids.update(story.id for story in new_stories)
            unique_daily.append(new_stories)
        daily = unique_daily
        if any(not stories for stories in daily):
            continue
        all_stories = [story for stories in daily for story in stories]
        source_count = len({url for story in all_stories for url in story.source_urls})
        if source_count < 2:
            continue
        daily_counts = [len(stories) for stories in daily]
        if not (daily_counts[0] <= daily_counts[1] <= daily_counts[2]):
            continue
        signals.append(
            _make_signal(
                signal_type=SignalType.TOPIC_HEATING,
                topic=topic,
                window_days=3,
                stories=all_stories,
                strength=min(1, 0.5 + 0.1 * source_count),
                explanation=f"{topic} 连续 3 天保持或增加事件量，且有 {source_count} 个来源支持。",
                now=now,
                current_date=current_date,
                metric_history=[
                    {"date": day.isoformat(), "story_count": count}
                    for day, count in zip(required_dates, daily_counts, strict=True)
                ],
            )
        )
    return signals


def detect_multi_company_direction(
    stories: list[Story],
    *,
    now: datetime,
    current_date: date | None = None,
    company_names: set[str] | None = None,
) -> list[Signal]:
    signal_date = current_date or display_date(now)
    by_topic: dict[str, list[Story]] = defaultdict(list)
    for story in stories:
        for topic in story.topic_names:
            by_topic[topic].append(story)

    signals: list[Signal] = []
    for topic, supporting in by_topic.items():
        companies = {
            name
            for story in supporting
            for name in story.entity_names
            if name in (company_names or set())
        }
        sources = {url for story in supporting for url in story.source_urls}
        if len(companies) < 2 or len(sources) < 2:
            continue
        signals.append(
            _make_signal(
                signal_type=SignalType.MULTI_COMPANY_DIRECTION,
                topic=topic,
                window_days=1,
                stories=supporting,
                strength=min(1, 0.5 + len(companies) * 0.1),
                explanation=(
                    f"{len(companies)} 家公司在 {topic} 上出现同向事件，"
                    f"由 {len(sources)} 个来源支持。"
                ),
                now=now,
                current_date=signal_date,
                supporting_company_count=len(companies),
            )
        )
    return signals


def detect_github_growth(
    snapshots: list[GitHubSnapshot],
    stories: list[Story],
    *,
    threshold: float,
    now: datetime,
    current_date: date | None = None,
) -> list[Signal]:
    signal_date = current_date or display_date(now)
    by_repository: dict[str, list[GitHubSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        by_repository[snapshot.repository].append(snapshot)

    signals: list[Signal] = []
    for repository, values in by_repository.items():
        ordered = sorted(values, key=lambda value: value.date)
        if len(ordered) < 2 or ordered[-2].stars <= 0:
            continue
        growth = (ordered[-1].stars - ordered[-2].stars) / ordered[-2].stars
        if growth < threshold:
            continue
        supporting = [
            story
            for story in stories
            if repository in story.product_names or repository in story.canonical_title
        ]
        if not supporting:
            continue
        signals.append(
            _make_signal(
                signal_type=SignalType.GITHUB_GROWTH,
                topic=repository,
                window_days=(ordered[-1].date - ordered[-2].date).days,
                stories=supporting,
                strength=min(1, growth / max(threshold, 0.001) * 0.5),
                explanation=f"{repository} Star 增长 {growth:.1%}，超过阈值 {threshold:.1%}。",
                now=now,
                current_date=signal_date,
                metric_history=[
                    {"date": value.date.isoformat(), "stars": value.stars}
                    for value in ordered[-2:]
                ],
                uncertainties=["Star 增长不等于真实采用。"],
            )
        )
    return signals


def detect_product_transitions(
    story_history: dict[date, list[Story]],
    *,
    current_date: date,
    now: datetime,
) -> list[Signal]:
    current_stories = story_history.get(current_date, [])
    earlier = [
        story
        for day, stories in story_history.items()
        if day < current_date
        for story in stories
    ]
    signals: list[Signal] = []
    for current in current_stories:
        for product in current.product_names:
            previous = next(
                (
                    story
                    for story in sorted(earlier, key=lambda value: value.updated_at, reverse=True)
                    if product in story.product_names
                ),
                None,
            )
            if previous is None:
                continue
            if STATUS_ORDER[current.status] <= STATUS_ORDER[previous.status]:
                continue
            signals.append(
                _make_signal(
                    signal_type=SignalType.PRODUCT_STATUS_TRANSITION,
                    topic=product,
                    window_days=min(7, (current_date - previous.updated_at.date()).days),
                    stories=[previous, current],
                    strength=0.8,
                    explanation=(
                        f"{product} 状态由 {previous.status.value} "
                        f"推进到 {current.status.value}。"
                    ),
                    now=now,
                    current_date=current_date,
                )
            )
    return signals


def detect_market_attention(
    snapshots: list[MarketSnapshot],
    stories: list[Story],
    *,
    threshold: float,
    now: datetime,
    current_date: date | None = None,
) -> list[Signal]:
    signals: list[Signal] = []
    first_snapshot_by_trading_day: dict[tuple[str, date], MarketSnapshot] = {}
    for snapshot in sorted(snapshots, key=lambda value: (value.date, value.captured_at)):
        first_snapshot_by_trading_day.setdefault(
            (snapshot.ticker, snapshot.trading_date),
            snapshot,
        )
    current_product_date = current_date or display_date(now)
    for snapshot in first_snapshot_by_trading_day.values():
        if snapshot.date != current_product_date:
            continue
        if abs(snapshot.change_percent) < threshold:
            continue
        supporting = [
            story for story in stories if snapshot.company in story.entity_names
        ]
        if not supporting:
            continue
        signals.append(
            _make_signal(
                signal_type=SignalType.MARKET_ATTENTION,
                topic=snapshot.company,
                window_days=1,
                stories=supporting,
                strength=min(1, abs(snapshot.change_percent) / threshold * 0.5),
                explanation=(
                    f"{snapshot.company} 最近交易日变动 {snapshot.change_percent:.1%}，"
                    "且当日存在相关事件；两者不构成因果确认。"
                ),
                now=now,
                current_date=current_product_date,
                metric_history=[
                    {
                        "trading_date": snapshot.trading_date.isoformat(),
                        "change_percent": snapshot.change_percent,
                    }
                ],
                uncertainties=["价格变化可能由多种因素共同导致。"],
            )
        )
    return signals


class TrendDetector:
    def __init__(
        self,
        *,
        github_threshold: float,
        market_threshold: float,
        company_names: set[str] | None = None,
    ) -> None:
        self.github_threshold = github_threshold
        self.market_threshold = market_threshold
        self.company_names = company_names or set()

    def detect(
        self,
        *,
        story_history: dict[date, list[Story]],
        github_snapshots: list[GitHubSnapshot],
        market_snapshots: list[MarketSnapshot],
        current_date: date,
        now: datetime,
    ) -> list[Signal]:
        current_stories = story_history.get(current_date, [])
        return [
            *detect_topic_heating(story_history, current_date=current_date, now=now),
            *detect_multi_company_direction(
                current_stories,
                now=now,
                current_date=current_date,
                company_names=self.company_names,
            ),
            *detect_github_growth(
                github_snapshots,
                current_stories,
                threshold=self.github_threshold,
                now=now,
                current_date=current_date,
            ),
            # v0.3 no longer publishes product-name-only transitions as truth.
            # ``detect_product_transitions`` remains importable for legacy tests
            # and candidate feature work, while confirmed reader-facing changes
            # come from validated StoryRelationRecord values.
            *detect_market_attention(
                market_snapshots,
                current_stories,
                threshold=self.market_threshold,
                now=now,
                current_date=current_date,
            ),
        ]

import logging
from datetime import UTC, datetime, timedelta

from morning_radar.collectors import collect_available
from morning_radar.models import RawItem
from morning_radar.processing import filter_news_window

NOW = datetime(2026, 7, 27, 1, 15, tzinfo=UTC)


def item(
    item_id: str,
    *,
    age_hours: int,
    source_name: str,
    source_type: str,
) -> RawItem:
    return RawItem(
        id=item_id,
        title=f"Item {item_id}",
        url=f"https://example.com/{item_id}",
        source_name=source_name,
        source_type=source_type,
        published_at=NOW - timedelta(hours=age_hours),
        fetched_at=NOW,
    )


class StaticCollector:
    def __init__(self, name: str, items: list[RawItem]) -> None:
        self.name = name
        self.items = items

    def collect(self) -> list[RawItem]:
        return self.items


class GoodCollector:
    name = "good"

    def collect(self) -> list[RawItem]:
        return [
            RawItem(
                id="good-1",
                title="Collected",
                url="https://example.com/good",
                source_name="Good",
                source_type="fixture",
                fetched_at=datetime(2026, 7, 23, tzinfo=UTC),
            )
        ]


class BrokenCollector:
    name = "broken"

    def collect(self) -> list[RawItem]:
        raise TimeoutError("secret details should not be copied into result")


def test_collector_failure_is_recorded_without_losing_success(caplog) -> None:
    result = collect_available([BrokenCollector(), GoodCollector()])

    assert [item.id for item in result.items] == ["good-1"]
    assert result.failures == {"broken": "TimeoutError"}
    assert "Collector failed: broken" in caplog.text


def buffered_collection(
    collectors: list[StaticCollector],
    *,
    maximum_items: int,
):
    return collect_available(
        collectors,
        filter_items=lambda items: filter_news_window(
            items,
            now=NOW,
            hours=30,
        ),
        maximum_items=maximum_items,
    )


def test_stale_rss_cannot_starve_fresh_later_collectors() -> None:
    rss = StaticCollector(
        "rss",
        [
            item(
                f"rss-{index}",
                age_hours=26,
                source_name="RSS",
                source_type="rss",
            )
            for index in range(200)
        ],
    )
    github = StaticCollector(
        "github",
        [
            item(
                "github-new",
                age_hours=2,
                source_name="GitHub",
                source_type="github",
            )
        ],
    )
    hacker_news = StaticCollector(
        "hacker_news",
        [
            item(
                "hn-new",
                age_hours=3,
                source_name="Hacker News",
                source_type="hacker_news",
            )
        ],
    )

    collected = buffered_collection(
        [rss, github, hacker_news],
        maximum_items=200,
    )
    recent = filter_news_window(collected.items, now=NOW, hours=24)

    assert {value.id for value in recent} == {"github-new", "hn-new"}
    assert collected.collector_stats["github"].retained == 1
    assert collected.collector_stats["hacker_news"].retained == 1


def test_collection_buffer_keeps_25_to_29_hour_items_but_final_window_excludes_them() -> None:
    rss = StaticCollector(
        "rss",
        [
            item(
                f"buffered-{age}",
                age_hours=age,
                source_name="RSS",
                source_type="rss",
            )
            for age in (25, 27, 29)
        ],
    )

    collected = buffered_collection([rss], maximum_items=10)
    recent = filter_news_window(collected.items, now=NOW, hours=24)

    assert len(collected.items) == 3
    assert collected.collector_stats["rss"].within_buffer == 3
    assert recent == []


def test_round_robin_retains_multiple_collectors_under_global_cap(caplog) -> None:
    caplog.set_level(logging.INFO)
    collectors = [
        StaticCollector(
            name,
            [
                item(
                    f"{name}-{index}",
                    age_hours=1,
                    source_name=name,
                    source_type=name,
                )
                for index in range(5)
            ],
        )
        for name in ("rss", "github", "market")
    ]

    collected = buffered_collection(collectors, maximum_items=6)

    assert len(collected.items) == 6
    assert {value.source_name for value in collected.items} == {
        "rss",
        "github",
        "market",
    }
    assert {
        name: stats.retained
        for name, stats in collected.collector_stats.items()
    } == {"rss": 2, "github": 2, "market": 2}
    assert "collected=5 within_buffer=5 retained=2" in caplog.text

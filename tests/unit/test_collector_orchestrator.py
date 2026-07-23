from datetime import UTC, datetime

from morning_radar.collectors import collect_available
from morning_radar.models import RawItem


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


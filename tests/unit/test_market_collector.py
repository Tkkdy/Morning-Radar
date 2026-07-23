import json
from datetime import UTC, date, datetime
from pathlib import Path

from morning_radar.collectors.market import MarketCollector
from morning_radar.models import MarketSnapshot
from morning_radar.settings import CompanyConfig
from morning_radar.storage import load_models


class FakeMarketProvider:
    def __init__(self, values: dict[str, list[tuple[date, float, float | None]]]) -> None:
        self.values = values

    def history(self, ticker: str) -> list[tuple[date, float, float | None]]:
        if ticker not in self.values:
            raise RuntimeError("fixture ticker failure")
        return self.values[ticker]


def company(ticker: str = "NVDA") -> CompanyConfig:
    return CompanyConfig(
        name="NVIDIA",
        ticker=ticker,
        source_url=f"https://finance.yahoo.com/quote/{ticker}/",
        priority="high",
        topics=["ai_infrastructure"],
    )


def test_market_uses_last_two_trading_days_across_weekend(tmp_path) -> None:
    raw = json.loads(Path("fixtures/market/nvda.json").read_text(encoding="utf-8"))
    values = [
        (date.fromisoformat(row["date"]), row["close"], row["volume"])
        for row in raw
    ]
    collector = MarketCollector(
        [company()],
        provider=FakeMarketProvider({"NVDA": values}),
        snapshot_dir=tmp_path,
        now=datetime(2026, 7, 20, 23, tzinfo=UTC),
    )

    items = collector.collect()

    assert len(items) == 1
    assert round(items[0].metadata["change_percent"], 3) == 0.032
    saved = load_models(tmp_path / "2026-07-21.json", MarketSnapshot)
    assert saved[0].trading_date == date(2026, 7, 20)


def test_missing_market_data_and_one_ticker_failure_do_not_stop_others(
    tmp_path,
    caplog,
) -> None:
    provider = FakeMarketProvider(
        {
            "NVDA": [
                (date(2026, 7, 17), 100, None),
                (date(2026, 7, 20), 98, None),
            ],
            "EMPTY": [],
        }
    )
    collector = MarketCollector(
        [company("EMPTY"), company("BROKEN"), company("NVDA")],
        provider=provider,
        snapshot_dir=tmp_path,
        now=datetime(2026, 7, 20, 23, tzinfo=UTC),
    )

    items = collector.collect()

    assert len(items) == 1
    assert items[0].metadata["change_percent"] == -0.02
    assert "Market ticker failed: EMPTY" in caplog.text
    assert "Market ticker failed: BROKEN" in caplog.text


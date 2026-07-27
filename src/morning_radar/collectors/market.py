"""Small watch-list market collector backed by yfinance."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

import yfinance

from morning_radar.models import MarketSnapshot, RawItem
from morning_radar.processing import stable_item_id
from morning_radar.settings import CompanyConfig
from morning_radar.storage import save_models
from morning_radar.time_utils import display_date, utc_now

LOGGER = logging.getLogger(__name__)


class MarketBar(Protocol):
    trading_date: date
    close: float
    volume: float | None


class MarketHistoryProvider(Protocol):
    def history(self, ticker: str) -> list[tuple[date, float, float | None]]: ...


class YFinanceHistoryProvider:
    def history(self, ticker: str) -> list[tuple[date, float, float | None]]:
        frame = yfinance.Ticker(ticker).history(period="7d", timeout=20)
        rows: list[tuple[date, float, float | None]] = []
        for index, row in frame.iterrows():
            close = float(row["Close"])
            volume_value = row.get("Volume")
            volume = float(volume_value) if volume_value is not None else None
            rows.append((index.date(), close, volume))
        return rows


class MarketCollector:
    name = "market"

    def __init__(
        self,
        companies: list[CompanyConfig],
        *,
        provider: MarketHistoryProvider,
        snapshot_dir: Path,
        now: datetime | None = None,
    ) -> None:
        self.companies = companies
        self.provider = provider
        self.snapshot_dir = snapshot_dir
        self.now = now or utc_now()

    def collect(self) -> list[RawItem]:
        items: list[RawItem] = []
        snapshots: list[MarketSnapshot] = []
        for company in self.companies:
            try:
                item, snapshot = self._collect_company(company)
                items.append(item)
                snapshots.append(snapshot)
            except Exception:
                LOGGER.exception("Market ticker failed: %s", company.ticker)
        if snapshots:
            save_models(self.snapshot_dir / f"{display_date(self.now)}.json", snapshots)
        return items

    def _collect_company(self, company: CompanyConfig) -> tuple[RawItem, MarketSnapshot]:
        bars = self.provider.history(company.ticker)
        valid_bars: list[tuple[date, float, float | None]] = []
        for trading_date, close, volume in bars:
            if not math.isfinite(close) or close <= 0:
                continue
            clean_volume = (
                volume
                if volume is not None and math.isfinite(volume) and volume >= 0
                else None
            )
            valid_bars.append((trading_date, close, clean_volume))
        LOGGER.info(
            "Market data stats: ticker=%s rows_received=%d valid_rows=%d "
            "skipped_invalid_rows=%d",
            company.ticker,
            len(bars),
            len(valid_bars),
            len(bars) - len(valid_bars),
        )
        if len(valid_bars) < 2:
            raise ValueError(f"Need two trading days for {company.ticker}")
        previous, latest = valid_bars[-2], valid_bars[-1]
        change = (latest[1] - previous[1]) / previous[1]
        snapshot = MarketSnapshot(
            date=display_date(self.now),
            captured_at=self.now,
            company=company.name,
            ticker=company.ticker,
            trading_date=latest[0],
            close=latest[1],
            previous_close=previous[1],
            change_percent=change,
            volume=latest[2],
        )
        direction = "上涨" if change >= 0 else "下跌"
        title = f"{company.name} 最近交易日收盘{direction} {abs(change):.2%}"
        item = RawItem(
            id=stable_item_id(f"{company.source_url}?date={latest[0].isoformat()}"),
            title=title,
            url=company.source_url,
            source_name="Yahoo Finance via yfinance",
            source_type="market",
            published_at=datetime.combine(latest[0], datetime.min.time(), tzinfo=self.now.tzinfo),
            fetched_at=self.now,
            language="en",
            summary="市场价格变化仅作信息展示，不代表可确认单一原因。",
            topic_candidates=company.topics,
            company_candidates=[company.name],
            metadata={
                "official": False,
                "ticker": company.ticker,
                "trading_date": latest[0].isoformat(),
                "close": latest[1],
                "previous_close": previous[1],
                "change_percent": change,
                "volume": latest[2],
            },
        )
        return item, snapshot

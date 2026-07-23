"""Daily snapshots used for explainable change calculations."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field, field_validator

from morning_radar.models.core import RadarModel, _validate_aware_datetime


class GitHubSnapshot(RadarModel):
    date: date
    captured_at: datetime
    repository: str
    stars: int = Field(ge=0)
    forks: int = Field(ge=0)
    open_issues: int = Field(ge=0)
    updated_at: datetime

    _captured_is_aware = field_validator("captured_at")(_validate_aware_datetime)
    _updated_is_aware = field_validator("updated_at")(_validate_aware_datetime)


class MarketSnapshot(RadarModel):
    date: date
    captured_at: datetime
    company: str
    ticker: str
    trading_date: date
    close: float = Field(gt=0)
    previous_close: float = Field(gt=0)
    change_percent: float
    volume: float | None = Field(default=None, ge=0)

    _captured_is_aware = field_validator("captured_at")(_validate_aware_datetime)


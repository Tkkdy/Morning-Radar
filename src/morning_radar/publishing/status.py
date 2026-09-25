"""Canonical public status contract shared by workflows and the dashboard."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from morning_radar.models.core import RadarModel
from morning_radar.time_utils import require_aware, utc_now


class RadarStatus(RadarModel):
    """Strict schema for the public ``/status.json`` endpoint."""

    run_date: date
    status: Literal["SUCCESS", "FAILED"]
    updated_at: datetime
    detail: str = Field(min_length=1)

    @field_validator("updated_at")
    @classmethod
    def validate_updated_at(cls, value: datetime) -> datetime:
        return require_aware(value)


def write_radar_status(
    path: Path,
    *,
    run_date: date,
    status: Literal["SUCCESS", "FAILED"],
    detail: str,
    updated_at: datetime | None = None,
) -> RadarStatus:
    """Validate and write one dashboard-compatible status document."""
    document = RadarStatus(
        run_date=run_date,
        status=status,
        updated_at=updated_at or utc_now(),
        detail=detail,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(), encoding="utf-8")
    return document


def read_radar_status(path: Path) -> RadarStatus:
    """Read the canonical status document, rejecting missing or unknown fields."""
    return RadarStatus.model_validate_json(path.read_text(encoding="utf-8"))

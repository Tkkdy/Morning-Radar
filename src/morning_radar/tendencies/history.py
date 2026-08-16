"""Read immutable daily Tendency decisions."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from morning_radar.models import DailyTendencies
from morning_radar.storage import load_model


def load_tendency_history(root: Path, *, current_date: date) -> list[DailyTendencies]:
    directory = root / "data/tendencies"
    if not directory.exists():
        return []
    result: list[DailyTendencies] = []
    for path in sorted(directory.glob("*.json")):
        daily = load_model(path, DailyTendencies)
        if daily.date < current_date:
            result.append(daily)
    return result

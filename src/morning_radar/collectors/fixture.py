"""Offline collector for deterministic demos and tests."""

from __future__ import annotations

from pathlib import Path

from morning_radar.models import RawItem
from morning_radar.storage import load_models


class FixtureCollector:
    name = "fixtures"

    def __init__(self, fixture_path: Path) -> None:
        self.fixture_path = fixture_path

    def collect(self) -> list[RawItem]:
        return load_models(self.fixture_path, RawItem)


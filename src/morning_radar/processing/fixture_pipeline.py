"""Minimal offline collect → deduplicate → group flow."""

from __future__ import annotations

from pathlib import Path

from morning_radar.collectors import FixtureCollector
from morning_radar.models import RawItem
from morning_radar.processing.deduplicate import deduplicate_items
from morning_radar.processing.grouping import group_items_by_normalized_title


def process_fixture_file(path: Path) -> tuple[list[RawItem], list[list[RawItem]]]:
    collected = FixtureCollector(path).collect()
    unique = deduplicate_items(collected)
    return unique, group_items_by_normalized_title(unique)


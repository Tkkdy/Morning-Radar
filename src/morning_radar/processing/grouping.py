"""Offline, conservative grouping before optional AI-assisted clustering."""

from __future__ import annotations

from collections import defaultdict

from morning_radar.models import RawItem
from morning_radar.processing.normalize import normalize_title


def group_items_by_normalized_title(items: list[RawItem]) -> list[list[RawItem]]:
    """Group only exact normalized titles; later stages may merge with stronger evidence."""
    groups: dict[str, list[RawItem]] = defaultdict(list)
    for item in items:
        groups[normalize_title(item.title)].append(item)
    return list(groups.values())


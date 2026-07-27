"""Run independent collectors without allowing one source to collapse the run."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from morning_radar.collectors.base import Collector
from morning_radar.models import RawItem
from morning_radar.processing.deduplicate import deduplicate_items

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CollectorRunStats:
    collected: int
    within_buffer: int
    retained: int


@dataclass(slots=True)
class CollectionResult:
    items: list[RawItem] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    collector_stats: dict[str, CollectorRunStats] = field(default_factory=dict)
    raw_collected: int = 0
    after_buffer: int = 0
    after_dedup: int = 0


def _fair_merge(
    batches: list[tuple[str, list[RawItem]]],
    *,
    maximum_items: int,
) -> list[tuple[str, RawItem]]:
    retained: list[tuple[str, RawItem]] = []
    positions = [0] * len(batches)
    while len(retained) < maximum_items:
        added = False
        for index, (name, items) in enumerate(batches):
            if positions[index] >= len(items):
                continue
            retained.append((name, items[positions[index]]))
            positions[index] += 1
            added = True
            if len(retained) >= maximum_items:
                break
        if not added:
            break
    return retained


def collect_available(
    collectors: list[Collector],
    *,
    filter_items: Callable[[list[RawItem]], list[RawItem]] | None = None,
    maximum_items: int | None = None,
) -> CollectionResult:
    if maximum_items is not None and maximum_items < 0:
        raise ValueError("maximum_items must be non-negative")

    result = CollectionResult()
    batches: list[tuple[str, list[RawItem]]] = []
    counts: dict[str, tuple[int, int]] = {}
    for collector in collectors:
        try:
            collected = collector.collect()
            within_buffer = filter_items(collected) if filter_items else collected
            batches.append((collector.name, within_buffer))
            counts[collector.name] = (len(collected), len(within_buffer))
            result.raw_collected += len(collected)
            result.after_buffer += len(within_buffer)
        except Exception as exc:
            result.failures[collector.name] = type(exc).__name__
            counts[collector.name] = (0, 0)
            LOGGER.exception("Collector failed: %s", collector.name)

    flattened = [item for _, items in batches for item in items]
    if maximum_items is None:
        selected = [(name, item) for name, items in batches for item in items]
        result.after_dedup = len(flattened)
    else:
        deduplicated = deduplicate_items(flattened)
        retained_objects = {id(item) for item in deduplicated}
        deduplicated_batches = [
            (name, [item for item in items if id(item) in retained_objects])
            for name, items in batches
        ]
        result.after_dedup = len(deduplicated)
        selected = _fair_merge(
            deduplicated_batches,
            maximum_items=maximum_items,
        )

    result.items = [item for _, item in selected]
    retained_counts = {
        name: sum(1 for selected_name, _ in selected if selected_name == name)
        for name, _ in batches
    }
    for name, (collected_count, within_buffer_count) in counts.items():
        retained_count = retained_counts.get(name, 0)
        result.collector_stats[name] = CollectorRunStats(
            collected=collected_count,
            within_buffer=within_buffer_count,
            retained=retained_count,
        )
        LOGGER.info(
            "Collector stats: collector=%s collected=%d within_buffer=%d retained=%d",
            name,
            collected_count,
            within_buffer_count,
            retained_count,
        )
    return result

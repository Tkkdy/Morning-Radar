"""Run independent collectors without allowing one source to collapse the run."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from morning_radar.collectors.base import Collector
from morning_radar.models import RawItem

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CollectionResult:
    items: list[RawItem] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)


def collect_available(collectors: list[Collector]) -> CollectionResult:
    result = CollectionResult()
    for collector in collectors:
        try:
            result.items.extend(collector.collect())
        except Exception as exc:
            result.failures[collector.name] = type(exc).__name__
            LOGGER.exception("Collector failed: %s", collector.name)
    return result


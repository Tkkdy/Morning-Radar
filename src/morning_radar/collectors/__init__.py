"""External and fixture data collectors."""

from morning_radar.collectors.aihot import AIHOTCollector
from morning_radar.collectors.base import Collector
from morning_radar.collectors.deepseek_updates import DeepSeekUpdatesCollector
from morning_radar.collectors.fixture import FixtureCollector
from morning_radar.collectors.hn_search import HNSearchCollector
from morning_radar.collectors.orchestrator import CollectionResult, collect_available

__all__ = [
    "AIHOTCollector",
    "CollectionResult",
    "Collector",
    "FixtureCollector",
    "DeepSeekUpdatesCollector",
    "HNSearchCollector",
    "collect_available",
]

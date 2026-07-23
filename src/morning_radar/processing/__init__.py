"""Normalization, deduplication, clustering, and scoring."""

from morning_radar.processing.deduplicate import deduplicate_items
from morning_radar.processing.grouping import group_items_by_normalized_title
from morning_radar.processing.normalize import normalize_title, normalize_url, stable_item_id

__all__ = [
    "deduplicate_items",
    "group_items_by_normalized_title",
    "normalize_title",
    "normalize_url",
    "stable_item_id",
]


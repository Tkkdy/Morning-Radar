"""Normalization, deduplication, clustering, and scoring."""

from morning_radar.processing.deduplicate import deduplicate_items
from morning_radar.processing.filtering import filter_news_window
from morning_radar.processing.grouping import group_items_by_normalized_title
from morning_radar.processing.normalize import normalize_title, normalize_url, stable_item_id
from morning_radar.processing.story_builder import (
    StoryValidationError,
    build_stories,
    build_story,
    choose_primary_source,
    rank_stories,
    ranking_score,
)

__all__ = [
    "deduplicate_items",
    "filter_news_window",
    "group_items_by_normalized_title",
    "StoryValidationError",
    "build_stories",
    "build_story",
    "choose_primary_source",
    "normalize_title",
    "normalize_url",
    "rank_stories",
    "ranking_score",
    "stable_item_id",
]

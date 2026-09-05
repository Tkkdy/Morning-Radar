"""Normalization, deduplication, clustering, and scoring."""

from morning_radar.processing.deduplicate import deduplicate_items
from morning_radar.processing.filtering import filter_news_window
from morning_radar.processing.grouping import group_items_by_normalized_title
from morning_radar.processing.normalize import normalize_title, normalize_url, stable_item_id
from morning_radar.processing.story_builder import (
    StoryValidationError,
    build_candidate_stories,
    build_candidate_story,
    build_story,
    choose_primary_source,
    filter_story_candidate_inputs,
    rank_stories,
    ranking_score,
    story_evidence_integrity_violations,
)

__all__ = [
    "build_candidate_stories",
    "build_candidate_story",
    "deduplicate_items",
    "filter_news_window",
    "group_items_by_normalized_title",
    "StoryValidationError",
    "build_story",
    "choose_primary_source",
    "filter_story_candidate_inputs",
    "normalize_title",
    "normalize_url",
    "rank_stories",
    "ranking_score",
    "story_evidence_integrity_violations",
    "stable_item_id",
]

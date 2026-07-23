"""Structured schemas exchanged with the single AI provider."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from morning_radar.models.core import RadarModel, StoryStatus


class ClassifiedItem(RadarModel):
    item_id: str
    relevant: bool
    relevance_reason: str
    important: bool
    importance_reason: str
    category: str


class ClassificationBatch(RadarModel):
    items: list[ClassifiedItem]


class MergedStoryDraft(RadarModel):
    same_event: bool
    canonical_title: str
    category: str
    entity_names: list[str] = Field(default_factory=list)
    product_names: list[str] = Field(default_factory=list)
    topic_names: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    analysis: list[str] = Field(default_factory=list)
    opinions: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    primary_source_url: str | None = None
    status: StoryStatus = StoryStatus.UNKNOWN


class StoryScore(RadarModel):
    relevance_score: float = Field(ge=0, le=1)
    importance_score: float = Field(ge=0, le=1)
    novelty_score: float = Field(ge=0, le=1)
    credibility_score: float = Field(ge=0, le=1)
    explanation: str


class GeneratedBriefItem(RadarModel):
    story_ids: list[str]
    section: str
    title: str
    what_happened: str
    why_it_matters: str
    market_or_community_reaction: str | None = None
    uncertainty: str | None = None
    source_urls: list[str]


class BriefDraft(RadarModel):
    items: list[GeneratedBriefItem]
    watch_next: list[str] = Field(default_factory=list)
    cognitive_extension: str | None = None


class DirectionObservation(RadarModel):
    observation: str | None = None
    evidence_story_ids: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    uncertainties: list[str] = Field(default_factory=list)


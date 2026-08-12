"""Validated, JSON-serializable domain models used across the pipeline."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_aware_datetime(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("datetime must include timezone information")
    return value


def _validate_http_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be an absolute http(s) URL")
    return value


class RadarModel(BaseModel):
    """Strict base model so invalid external data fails near its boundary."""

    model_config = ConfigDict(extra="forbid")


class StoryStatus(StrEnum):
    RUMOR = "rumor"
    OFFICIAL_TEASER = "official_teaser"
    ANNOUNCED = "announced"
    AVAILABLE = "available"
    UPDATED = "updated"
    UNKNOWN = "unknown"


class PublishedAtRole(StrEnum):
    FEED_ENTRY_TIME = "feed_entry_time"
    HN_SUBMISSION_TIME = "hn_submission_time"
    GITHUB_RELEASE_PUBLISHED_TIME = "github_release_published_time"
    MARKET_TRADING_DAY = "market_trading_day"
    UNKNOWN = "unknown"


class SignalType(StrEnum):
    TOPIC_HEATING = "topic_heating"
    MULTI_COMPANY_DIRECTION = "multi_company_direction"
    GITHUB_GROWTH = "github_growth"
    PRODUCT_STATUS_TRANSITION = "product_status_transition"
    MARKET_ATTENTION = "market_attention"


class RawItem(RadarModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    url: str
    source_name: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    author: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime
    language: str | None = None
    summary: str = Field(default="", max_length=2000)
    content_excerpt: str = Field(default="", max_length=4000)
    topic_candidates: list[str] = Field(default_factory=list)
    company_candidates: list[str] = Field(default_factory=list)
    repository_candidates: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _published_is_aware = field_validator("published_at")(_validate_aware_datetime)
    _fetched_is_aware = field_validator("fetched_at")(_validate_aware_datetime)
    _url_is_http = field_validator("url")(_validate_http_url)


class StorySourceRef(RadarModel):
    """A deterministic snapshot of one collector record supporting a Story.

    ``published_at`` is the publication time supplied by the collected source.
    It is not guaranteed to be the underlying event time or the original
    article publication time; for Hacker News it is the HN submission time.
    """

    raw_item_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    source_name: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    url: str
    author: str | None = None
    published_at: datetime | None = None
    published_at_role: PublishedAtRole = PublishedAtRole.UNKNOWN
    fetched_at: datetime
    discussion_url: str | None = None

    _url_is_http = field_validator("url")(_validate_http_url)
    _published_is_aware = field_validator("published_at")(_validate_aware_datetime)
    _fetched_is_aware = field_validator("fetched_at")(_validate_aware_datetime)

    @field_validator("discussion_url")
    @classmethod
    def discussion_url_requires_hacker_news(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return value
        if info.data.get("source_type") != "hacker_news":
            raise ValueError("discussion_url is only supported for hacker_news sources")
        return _validate_http_url(value)


class Story(RadarModel):
    id: str = Field(min_length=1)
    canonical_title: str = Field(min_length=1, max_length=500)
    category: str
    entity_names: list[str] = Field(default_factory=list)
    product_names: list[str] = Field(default_factory=list)
    topic_names: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    updated_at: datetime
    source_item_ids: list[str] = Field(min_length=1)
    source_urls: list[str] = Field(min_length=1)
    primary_source_url: str
    source_refs: list[StorySourceRef] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    analysis: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    relevance_score: float = Field(ge=0, le=1)
    importance_score: float = Field(ge=0, le=1)
    novelty_score: float = Field(ge=0, le=1)
    credibility_score: float = Field(ge=0, le=1)
    status: StoryStatus = StoryStatus.UNKNOWN

    _published_is_aware = field_validator("published_at")(_validate_aware_datetime)
    _updated_is_aware = field_validator("updated_at")(_validate_aware_datetime)
    _source_urls_are_http = field_validator("source_urls")(
        lambda urls: [_validate_http_url(url) for url in urls]
    )
    _primary_url_is_http = field_validator("primary_source_url")(_validate_http_url)

    @field_validator("primary_source_url")
    @classmethod
    def primary_url_must_be_a_source(cls, value: str, info: Any) -> str:
        if value not in info.data.get("source_urls", []):
            raise ValueError("primary_source_url must be present in source_urls")
        return value

    @field_validator("source_refs")
    @classmethod
    def source_refs_must_match_story_provenance(
        cls,
        values: list[StorySourceRef],
        info: Any,
    ) -> list[StorySourceRef]:
        item_ids = set(info.data.get("source_item_ids", []))
        source_urls = set(info.data.get("source_urls", []))
        for source_ref in values:
            if source_ref.raw_item_id not in item_ids:
                raise ValueError("source_ref raw_item_id must be present in source_item_ids")
            if source_ref.url not in source_urls:
                raise ValueError("source_ref url must be present in source_urls")
            if (
                source_ref.discussion_url is not None
                and source_ref.discussion_url not in source_urls
            ):
                raise ValueError(
                    "source_ref discussion_url must be present in source_urls"
                )
        return values


class Signal(RadarModel):
    id: str = Field(min_length=1)
    signal_type: SignalType
    topic: str = Field(min_length=1)
    window_days: int = Field(ge=1)
    supporting_story_ids: list[str] = Field(min_length=1)
    supporting_source_count: int = Field(ge=1)
    supporting_company_count: int = Field(ge=0)
    metric_history: list[dict[str, Any]] = Field(default_factory=list)
    strength: float = Field(ge=0, le=1)
    explanation: str
    uncertainties: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    _created_is_aware = field_validator("created_at")(_validate_aware_datetime)
    _updated_is_aware = field_validator("updated_at")(_validate_aware_datetime)


class BriefStoryContext(RadarModel):
    """Deterministic Story context embedded in a BriefItem for display.

    ``published_at`` preserves the current Story time semantics. It is not
    guaranteed to be the underlying event time or original article time.
    """

    story_id: str = Field(min_length=1)
    canonical_title: str = Field(min_length=1, max_length=500)
    category: str
    entity_names: list[str] = Field(default_factory=list)
    product_names: list[str] = Field(default_factory=list)
    topic_names: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    facts: list[str] = Field(default_factory=list)
    analysis: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    status: StoryStatus = StoryStatus.UNKNOWN
    primary_source_url: str
    source_refs: list[StorySourceRef] = Field(default_factory=list)

    _published_is_aware = field_validator("published_at")(_validate_aware_datetime)
    _primary_url_is_http = field_validator("primary_source_url")(_validate_http_url)


class BriefJudgementCue(RadarModel):
    judgement_id: str = Field(min_length=1)
    update_kind: str = Field(min_length=1)
    claim: str = Field(min_length=1)


class BriefContinuityContext(RadarModel):
    current_story_id: str = Field(min_length=1)
    relation_type: str | None = None
    what_changed: str | None = None
    previous_story_date: date | None = None
    previous_story_id: str | None = None
    previous_story_title: str | None = None
    watch_matches: list[str] = Field(default_factory=list)
    judgement_cues: list[BriefJudgementCue] = Field(default_factory=list)


class BriefItem(RadarModel):
    id: str = Field(min_length=1)
    section: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    what_happened: str
    why_it_matters: str
    market_or_community_reaction: str | None = None
    uncertainty: str | None = None
    source_urls: list[str] = Field(min_length=1)
    story_ids: list[str] = Field(min_length=1)
    story_contexts: list[BriefStoryContext] = Field(default_factory=list)
    continuity_contexts: list[BriefContinuityContext] = Field(default_factory=list)

    _source_urls_are_http = field_validator("source_urls")(
        lambda urls: [_validate_http_url(url) for url in urls]
    )

    @field_validator("story_contexts")
    @classmethod
    def story_contexts_must_match_story_ids(
        cls,
        values: list[BriefStoryContext],
        info: Any,
    ) -> list[BriefStoryContext]:
        if values and [context.story_id for context in values] != info.data.get(
            "story_ids", []
        ):
            raise ValueError("story_contexts must exactly match story_ids in order")
        return values

    @field_validator("continuity_contexts")
    @classmethod
    def continuity_contexts_must_reference_item_stories(
        cls,
        values: list[BriefContinuityContext],
        info: Any,
    ) -> list[BriefContinuityContext]:
        story_ids = set(info.data.get("story_ids", []))
        if any(context.current_story_id not in story_ids for context in values):
            raise ValueError("continuity_contexts must reference item story_ids")
        return values


class DailyBrief(RadarModel):
    date: date
    timezone: str
    generated_at: datetime
    top_stories: list[BriefItem] = Field(default_factory=list)
    market_and_companies: list[BriefItem] = Field(default_factory=list)
    ai_and_open_source: list[BriefItem] = Field(default_factory=list)
    trend_radar: list[BriefItem] = Field(default_factory=list)
    developer_discussions: list[BriefItem] = Field(default_factory=list)
    other_reading: list[BriefItem] = Field(default_factory=list)
    direction_observation: str | None = None
    cognitive_extension: str | None = None
    watch_next: list[str] = Field(default_factory=list)
    run_stats: dict[str, int | float | str | bool] = Field(default_factory=dict)

    _generated_is_aware = field_validator("generated_at")(_validate_aware_datetime)

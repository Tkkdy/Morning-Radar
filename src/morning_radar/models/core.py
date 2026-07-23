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

    _source_urls_are_http = field_validator("source_urls")(
        lambda urls: [_validate_http_url(url) for url in urls]
    )


class DailyBrief(RadarModel):
    date: date
    timezone: str
    generated_at: datetime
    top_stories: list[BriefItem] = Field(default_factory=list)
    market_and_companies: list[BriefItem] = Field(default_factory=list)
    ai_and_open_source: list[BriefItem] = Field(default_factory=list)
    trend_radar: list[BriefItem] = Field(default_factory=list)
    developer_discussions: list[BriefItem] = Field(default_factory=list)
    direction_observation: str | None = None
    cognitive_extension: str | None = None
    watch_next: list[str] = Field(default_factory=list)
    run_stats: dict[str, int | float | str | bool] = Field(default_factory=dict)

    _generated_is_aware = field_validator("generated_at")(_validate_aware_datetime)


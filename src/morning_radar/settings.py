"""Validated YAML configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Priority = Literal["high", "medium", "low"]


class AIHOTConfig(ConfigModel):
    enabled: bool = False
    url: str = "https://aihot.virxact.com/api/v1/items"
    mode: Literal["selected"] = "selected"
    window: Literal["24h"] = "24h"
    limit: int = Field(default=20, ge=1, le=50)


class EditorialConfig(ConfigModel):
    enabled: bool = True
    shadow_mode: bool = True
    profile_version: str = Field(default="1.0", min_length=1)
    maximum_stories: int = Field(default=20, ge=1, le=40)


class AppConfig(ConfigModel):
    timezone: str = "Asia/Singapore"
    news_window_hours: int = Field(gt=0)
    collection_buffer_hours: int = Field(ge=0)
    trend_window_days: int = Field(ge=3)
    maximum_raw_items: int = Field(gt=0)
    maximum_ai_items: int = Field(gt=0)
    maximum_brief_items: int = Field(gt=0)
    maximum_ai_calls: int = Field(gt=0)
    maximum_ai_input_characters: int = Field(gt=0)
    maximum_ai_network_requests: int = Field(default=60, ge=1)
    fast_continuity_join_timeout_seconds: float = Field(default=60, gt=0)
    continuity_history_days: int = Field(default=14, ge=1, le=90)
    maximum_continuity_candidates: int = Field(default=20, ge=1)
    maximum_continuity_input_characters: int = Field(default=30000, ge=1000)
    maximum_open_watches_considered: int = Field(default=20, ge=1)
    maximum_research_cases: int = Field(default=8, ge=1, le=20)
    maximum_research_input_characters: int = Field(default=12000, ge=1000, le=30000)
    maximum_radar_signals: int = Field(default=3, ge=0, le=3)
    maximum_tendency_candidates: int = Field(default=12, ge=1, le=30)
    maximum_tendency_input_characters: int = Field(default=24000, ge=2000, le=50000)
    tendency_maximum_ai_calls: int = Field(default=3, ge=1, le=10)
    tendency_maximum_network_requests: int = Field(default=4, ge=1, le=10)
    deep_review_window_days: int = Field(default=21, ge=7, le=90)
    deep_review_minimum_stories: int = Field(default=4, ge=2, le=20)
    deep_review_minimum_dates: int = Field(default=3, ge=2, le=10)
    deep_review_minimum_sources: int = Field(default=3, ge=2, le=10)
    aihot: AIHOTConfig = Field(default_factory=AIHOTConfig)
    editorial: EditorialConfig = Field(default_factory=EditorialConfig)
    request_timeout_seconds: float = Field(gt=0)
    request_retry_attempts: int = Field(ge=1, le=5)
    relevance_threshold: float = Field(ge=0, le=1)
    importance_threshold: float = Field(ge=0, le=1)
    github_growth_threshold: float = Field(ge=0)
    market_movement_threshold: float = Field(ge=0)
    enabled_sections: dict[str, bool]


class TopicConfig(ConfigModel):
    id: str
    name: str
    priority: Priority
    keywords: list[str]
    exclude_keywords: list[str] = Field(default_factory=list)


class SourceConfig(ConfigModel):
    id: str
    name: str
    type: Literal["rss", "atom", "hacker_news"]
    url: str
    priority: Priority
    enabled: bool = True
    topics: list[str] = Field(default_factory=list)
    official: bool = False
    source_role: (
        Literal[
            "official_primary",
            "practitioner",
            "editorial",
            "community_discovery",
            "upstream_discovery",
        ]
        | None
    ) = None
    practitioner_id: str | None = None


class CompanyConfig(ConfigModel):
    name: str
    ticker: str
    source_url: str
    priority: Priority
    topics: list[str] = Field(default_factory=list)


class RepositoryConfig(ConfigModel):
    owner: str
    repo: str
    priority: Priority
    topics: list[str] = Field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


class PractitionerChannelConfig(ConfigModel):
    type: Literal["rss", "atom", "github_releases", "x", "blog", "other"]
    url: str | None = None
    enabled: bool = False
    availability: Literal["available", "unavailable", "deferred"]


class PersonConfig(ConfigModel):
    id: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    enabled: bool = True
    channels: list[PractitionerChannelConfig] = Field(default_factory=list)


def active_practitioner_sources(people: list[PersonConfig]) -> list[SourceConfig]:
    """Project reliable practitioner feeds into the existing RSS collector contract."""
    sources: list[SourceConfig] = []
    for person in people:
        if not person.enabled:
            continue
        for index, channel in enumerate(person.channels):
            if (
                not channel.enabled
                or channel.availability != "available"
                or channel.type not in {"rss", "atom"}
                or channel.url is None
            ):
                continue
            sources.append(
                SourceConfig(
                    id=f"practitioner_{person.id}_{index}",
                    name=person.display_name,
                    type=channel.type,
                    url=channel.url,
                    priority="high",
                    enabled=True,
                    topics=person.topics,
                    official=False,
                    source_role="practitioner",
                    practitioner_id=person.id,
                )
            )
    return sources


def practitioner_coverage_stats(people: list[PersonConfig]) -> dict[str, int]:
    active = active_practitioner_sources(people)
    return {
        "configured_seed_count": len(people),
        "active_channel_count": len(active),
        "practitioners_with_active_channels": len(
            {source.practitioner_id for source in active if source.practitioner_id}
        ),
    }


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as source:
            loaded = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return loaded


def load_model[ModelT: BaseModel](path: Path, model_type: type[ModelT]) -> ModelT:
    try:
        return model_type.model_validate(load_yaml(path))
    except ValidationError as exc:
        raise ValueError(f"Invalid configuration in {path}: {exc}") from exc


def load_model_list[ModelT: BaseModel](
    path: Path,
    key: str,
    model_type: type[ModelT],
) -> list[ModelT]:
    data = load_yaml(path)
    if key not in data or not isinstance(data[key], list):
        raise ValueError(f"Expected list key '{key}' in {path}")
    try:
        return [model_type.model_validate(item) for item in data[key]]
    except ValidationError as exc:
        raise ValueError(f"Invalid item under '{key}' in {path}: {exc}") from exc

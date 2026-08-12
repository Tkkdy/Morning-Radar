"""Validated YAML configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Priority = Literal["high", "medium", "low"]


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
    continuity_history_days: int = Field(default=14, ge=1, le=90)
    maximum_continuity_candidates: int = Field(default=20, ge=1)
    maximum_continuity_input_characters: int = Field(default=30000, ge=1000)
    maximum_open_watches_considered: int = Field(default=20, ge=1)
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


class PersonConfig(ConfigModel):
    name: str
    feed_type: str
    url: str
    role: str
    priority: Priority
    enabled: bool = False


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

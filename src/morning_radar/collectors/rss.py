"""RSS/Atom adapter with per-feed isolation and conditional request state."""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import feedparser
from dateutil.parser import parse as parse_datetime

from morning_radar.collectors.http import HttpClient
from morning_radar.models import RawItem
from morning_radar.processing import normalize_url, stable_item_id
from morning_radar.settings import SourceConfig
from morning_radar.storage import read_json, write_json
from morning_radar.time_utils import utc_now

LOGGER = logging.getLogger(__name__)


def _fair_merge_source_batches(batches: list[list[RawItem]]) -> list[RawItem]:
    """Interleave feeds so one long feed cannot hide every later source."""
    merged: list[RawItem] = []
    positions = [0] * len(batches)
    while True:
        added = False
        for index, items in enumerate(batches):
            if positions[index] >= len(items):
                continue
            merged.append(items[positions[index]])
            positions[index] += 1
            added = True
        if not added:
            return merged


def _plain_text(value: str, *, maximum: int) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(no_tags).split())[:maximum]


def _entry_time(entry: Any) -> datetime | None:
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None
    parsed = parse_datetime(raw)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


class RSSCollector:
    name = "rss"

    def __init__(
        self,
        sources: list[SourceConfig],
        *,
        http: HttpClient,
        state_path: Path,
        now: datetime | None = None,
    ) -> None:
        self.sources = [
            source
            for source in sources
            if source.enabled and source.type in {"rss", "atom"}
        ]
        self.http = http
        self.state_path = state_path
        self.now = now or utc_now()

    def collect(self) -> list[RawItem]:
        state = read_json(self.state_path) if self.state_path.exists() else {}
        batches: list[list[RawItem]] = []
        for source in self.sources:
            try:
                batches.append(self._collect_source(source, state))
            except Exception:
                LOGGER.exception("RSS source failed: %s", source.id)
        write_json(self.state_path, state)

        unique: dict[str, RawItem] = {}
        for item in _fair_merge_source_batches(batches):
            unique.setdefault(normalize_url(item.url), item)
        return list(unique.values())

    def _collect_source(
        self,
        source: SourceConfig,
        state: dict[str, dict[str, str]],
    ) -> list[RawItem]:
        cached = state.get(source.id, {})
        headers: dict[str, str] = {}
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("last_modified"):
            headers["If-Modified-Since"] = cached["last_modified"]
        response = self.http.get(source.url, headers=headers)
        if response.status_code == 304:
            return []

        state[source.id] = {
            "etag": response.headers.get("ETag", ""),
            "last_modified": response.headers.get("Last-Modified", ""),
        }
        parsed = feedparser.parse(response.content)
        if parsed.bozo and not parsed.entries:
            raise ValueError(f"Malformed feed: {source.id}")

        result: list[RawItem] = []
        for entry in parsed.entries:
            url = entry.get("link")
            title = _plain_text(entry.get("title", ""), maximum=500)
            if not url or not title:
                continue
            description = entry.get("summary") or entry.get("description") or ""
            excerpt = _plain_text(description, maximum=4000)
            result.append(
                RawItem(
                    id=stable_item_id(url),
                    title=title,
                    url=url,
                    source_name=source.name,
                    source_type=source.type,
                    author=entry.get("author"),
                    published_at=_entry_time(entry),
                    fetched_at=self.now,
                    language=parsed.feed.get("language"),
                    summary=excerpt[:1000],
                    content_excerpt=excerpt,
                    topic_candidates=source.topics,
                    metadata={
                        "official": source.official,
                        "priority": source.priority,
                        "source_id": source.id,
                    },
                )
            )
        return result

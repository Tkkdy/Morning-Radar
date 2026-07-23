"""Hacker News public API adapter used as a community signal."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from morning_radar.collectors.http import HttpClient
from morning_radar.models import RawItem
from morning_radar.processing import stable_item_id
from morning_radar.time_utils import utc_now

LOGGER = logging.getLogger(__name__)


class HackerNewsCollector:
    name = "hacker_news"

    def __init__(
        self,
        *,
        http: HttpClient,
        keywords: list[str],
        maximum_candidates: int = 30,
        base_url: str = "https://hacker-news.firebaseio.com/v0",
        now: datetime | None = None,
    ) -> None:
        self.http = http
        self.keywords = [value.casefold() for value in keywords]
        self.maximum_candidates = maximum_candidates
        self.base_url = base_url.rstrip("/")
        self.now = now or utc_now()

    def collect(self) -> list[RawItem]:
        story_ids: list[int] = []
        for endpoint in ("topstories", "newstories", "beststories"):
            try:
                story_ids.extend(self.http.get(f"{self.base_url}/{endpoint}.json").json())
            except Exception:
                LOGGER.exception("Hacker News list failed: %s", endpoint)
        unique_ids = list(dict.fromkeys(story_ids))[: self.maximum_candidates]

        items: list[RawItem] = []
        for story_id in unique_ids:
            try:
                story = self.http.get(f"{self.base_url}/item/{story_id}.json").json()
                converted = self._convert(story)
                if converted:
                    items.append(converted)
            except Exception:
                LOGGER.exception("Hacker News item failed: %s", story_id)
        return items

    def _convert(self, story: dict[str, object]) -> RawItem | None:
        title = str(story.get("title") or "").strip()
        searchable = f"{title} {story.get('text') or ''}".casefold()
        if not title or not any(keyword in searchable for keyword in self.keywords):
            return None
        story_id = int(story["id"])
        discussion_url = f"https://news.ycombinator.com/item?id={story_id}"
        original_url = str(story.get("url") or discussion_url)
        published_at = (
            datetime.fromtimestamp(int(story["time"]), tz=UTC)
            if story.get("time")
            else None
        )
        return RawItem(
            id=stable_item_id(discussion_url),
            title=title,
            url=original_url,
            source_name="Hacker News",
            source_type="hacker_news",
            author=str(story.get("by") or "") or None,
            published_at=published_at,
            fetched_at=self.now,
            language="en",
            summary="",
            content_excerpt="",
            metadata={
                "official": False,
                "community_signal": True,
                "discussion_url": discussion_url,
                "original_url": story.get("url"),
                "score": int(story.get("score") or 0),
                "comments": int(story.get("descendants") or 0),
            },
        )


"""Hacker News public API adapter used as a community signal."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from morning_radar.collectors.http import HttpClient
from morning_radar.models import RawItem
from morning_radar.processing import stable_item_id
from morning_radar.time_utils import utc_now

LOGGER = logging.getLogger(__name__)
TITLE_SIGNAL_KEYWORDS = ("ai", "llm", "agent", "mcp")
WEAK_BODY_KEYWORDS = {"github"}
MAXIMUM_CANDIDATES = 30
DISCOVERY_SCORE_THRESHOLD = 150
DISCOVERY_COMMENTS_THRESHOLD = 80
SHOW_HN_SCORE_THRESHOLD = 30
SHOW_HN_COMMENTS_THRESHOLD = 15


def _contains_keyword(text: str, keyword: str) -> bool:
    normalized = keyword.casefold().strip()
    if not normalized:
        return False
    if " " not in normalized and len(normalized) <= 5:
        return re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", text) is not None
    return normalized in text


def _select_candidate_ids(
    endpoint_ids: list[list[int]],
    *,
    maximum_candidates: int,
) -> list[int]:
    selected: list[int] = []
    seen: set[int] = set()
    positions = [0] * len(endpoint_ids)
    while len(selected) < maximum_candidates:
        added = False
        for index, story_ids in enumerate(endpoint_ids):
            while (
                positions[index] < len(story_ids)
                and story_ids[positions[index]] in seen
            ):
                positions[index] += 1
            if positions[index] >= len(story_ids):
                continue
            story_id = story_ids[positions[index]]
            positions[index] += 1
            selected.append(story_id)
            seen.add(story_id)
            added = True
            if len(selected) >= maximum_candidates:
                break
        if not added:
            break
    return selected


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
        self.maximum_candidates = min(max(0, maximum_candidates), MAXIMUM_CANDIDATES)
        self.base_url = base_url.rstrip("/")
        self.now = now or utc_now()

    def collect(self) -> list[RawItem]:
        ids_by_endpoint: dict[str, list[int]] = {}
        for endpoint in ("topstories", "newstories", "beststories"):
            try:
                ids_by_endpoint[endpoint] = self.http.get(
                    f"{self.base_url}/{endpoint}.json"
                ).json()
            except Exception:
                ids_by_endpoint[endpoint] = []
                LOGGER.exception("Hacker News list failed: %s", endpoint)
        all_ids = [
            story_id
            for endpoint in ("topstories", "newstories", "beststories")
            for story_id in ids_by_endpoint[endpoint]
        ]
        candidate_ids = _select_candidate_ids(
            [
                ids_by_endpoint["topstories"],
                ids_by_endpoint["newstories"],
                ids_by_endpoint["beststories"],
            ],
            maximum_candidates=self.maximum_candidates,
        )

        items: list[RawItem] = []
        fetched_items = 0
        keyword_matches = 0
        discovery_matches = 0
        for story_id in candidate_ids:
            try:
                story = self.http.get(f"{self.base_url}/item/{story_id}.json").json()
                if not story:
                    continue
                fetched_items += 1
                converted = self._convert(story)
                if converted:
                    if converted.metadata["selection_reason"] == "keyword":
                        keyword_matches += 1
                    else:
                        discovery_matches += 1
                    items.append(converted)
            except Exception:
                LOGGER.exception("Hacker News item failed: %s", story_id)
        LOGGER.info(
            "Hacker News stats: top_ids=%d new_ids=%d best_ids=%d "
            "unique_candidates=%d selected_candidates=%d fetched_items=%d "
            "keyword_matches=%d discovery_matches=%d retained_items=%d",
            len(ids_by_endpoint["topstories"]),
            len(ids_by_endpoint["newstories"]),
            len(ids_by_endpoint["beststories"]),
            len(set(all_ids)),
            len(candidate_ids),
            fetched_items,
            keyword_matches,
            discovery_matches,
            len(items),
        )
        return items

    def _convert(self, story: dict[str, object]) -> RawItem | None:
        title = str(story.get("title") or "").strip()
        title_text = title.casefold()
        body_text = str(story.get("text") or "").casefold()
        title_keywords = [*self.keywords, *TITLE_SIGNAL_KEYWORDS]
        title_match = any(
            _contains_keyword(title_text, keyword) for keyword in title_keywords
        )
        body_match = any(
            keyword not in WEAK_BODY_KEYWORDS
            and _contains_keyword(body_text, keyword)
            for keyword in self.keywords
        )
        score = int(story.get("score") or 0)
        comments = int(story.get("descendants") or 0)
        show_hn = title_text.startswith("show hn:")
        discovery_match = (
            score >= DISCOVERY_SCORE_THRESHOLD
            or comments >= DISCOVERY_COMMENTS_THRESHOLD
            or show_hn
            and (score >= SHOW_HN_SCORE_THRESHOLD or comments >= SHOW_HN_COMMENTS_THRESHOLD)
        )
        if not title or not (title_match or body_match or discovery_match):
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
                "score": score,
                "comments": comments,
                "selection_reason": (
                    "keyword" if title_match or body_match else "high_signal_discovery"
                ),
            },
        )

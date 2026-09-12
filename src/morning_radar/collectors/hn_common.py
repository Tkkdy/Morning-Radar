"""Shared, bounded conversion of Hacker News observations."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from urllib.parse import urlsplit

from morning_radar.models import RawItem, SourceRole, StatementType
from morning_radar.processing import stable_item_id


def clean_hn_text(value: object, *, maximum_characters: int = 1600) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())[:maximum_characters]


def hn_item(
    *,
    story_id: int,
    title: str,
    original_url: object,
    text: object,
    author: object,
    submitted_at: int,
    fetched_at: datetime,
    metadata: dict[str, object],
    maximum_excerpt_characters: int = 1600,
) -> RawItem:
    discussion_url = f"https://news.ycombinator.com/item?id={story_id}"
    excerpt = clean_hn_text(text, maximum_characters=maximum_excerpt_characters)
    return RawItem(
        id=stable_item_id(discussion_url),
        title=clean_hn_text(title, maximum_characters=500),
        url=str(original_url or discussion_url), source_name="Hacker News",
        source_type="hacker_news", author=str(author or "") or None,
        published_at=datetime.fromtimestamp(submitted_at, tz=UTC), fetched_at=fetched_at,
        language="en", summary=excerpt[:280], content_excerpt=excerpt,
        source_role=SourceRole.COMMUNITY_DISCOVERY, statement_type=StatementType.UNVERIFIED_LEAD,
        metadata={"source_id": "hn", "discussion_url": discussion_url,
                  "original_url": original_url, "community_signal": True, **metadata},
    )


def merge_hn_observations(items: list[RawItem]) -> list[RawItem]:
    """Merge equivalent same-discussion observations before content versioning."""
    merged: list[RawItem] = []
    positions: dict[str, list[int]] = {}
    for item in items:
        if item.source_type != "hacker_news":
            merged.append(item)
            continue
        for index in positions.get(item.id, []):
            prior = merged[index]
            if _hn_observations_conflict(prior, item):
                continue
            merged[index] = _merge_hn_observation_pair(prior, item)
            break
        else:
            positions.setdefault(item.id, []).append(len(merged))
            merged.append(item)
    return merged


def _hn_observations_conflict(prior: RawItem, current: RawItem) -> bool:
    """Return whether two observations are distinct content versions."""
    if clean_hn_text(prior.title, maximum_characters=500) != clean_hn_text(
        current.title, maximum_characters=500
    ):
        return True
    prior_url = _observed_hn_target_url(prior)
    current_url = _observed_hn_target_url(current)
    if prior_url and current_url and prior_url != current_url:
        return True
    prior_text = clean_hn_text(prior.content_excerpt)
    current_text = clean_hn_text(current.content_excerpt)
    return bool(prior_text and current_text and prior_text != current_text)


def _observed_hn_target_url(item: RawItem) -> str | None:
    """Return a valid observed external URL, never the HN discussion fallback."""
    value = item.metadata.get("original_url")
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    return None


def _merge_hn_observation_pair(prior: RawItem, current: RawItem) -> RawItem:
    prior_text = clean_hn_text(prior.content_excerpt)
    current_text = clean_hn_text(current.content_excerpt)
    chosen = current if len(current_text) > len(prior_text) else prior
    target_url = _observed_hn_target_url(current) or _observed_hn_target_url(prior)
    paths = list(dict.fromkeys([
        *prior.metadata.get("discovery_paths", []),
        *current.metadata.get("discovery_paths", []),
    ]))
    reasons = list(dict.fromkeys([
        *prior.metadata.get("discovery_reasons", [prior.metadata.get("selection_reason")]),
        *current.metadata.get("discovery_reasons", [current.metadata.get("selection_reason")]),
    ]))
    metadata = {
        **chosen.metadata,
        "discovery_paths": [path for path in paths if path],
        "discovery_reasons": [reason for reason in reasons if reason],
    }
    if target_url:
        metadata["original_url"] = target_url
    for key in ("lab_id", "selection_reason"):
        if not metadata.get(key):
            metadata[key] = prior.metadata.get(key) or current.metadata.get(key)
    if "watchlist_discovery" in metadata["discovery_reasons"]:
        metadata["selection_reason"] = "watchlist_discovery"
    updates: dict[str, object] = {"metadata": metadata}
    if target_url:
        updates["url"] = target_url
    return chosen.model_copy(update=updates)

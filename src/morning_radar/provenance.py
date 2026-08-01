"""Collector-backed source URL provenance rules."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from morning_radar.models import RawItem


def _verified_hn_discussion_url(item: RawItem) -> str | None:
    if item.source_type != "hacker_news":
        return None
    candidate = item.metadata.get("discussion_url")
    if not isinstance(candidate, str):
        return None

    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "news.ycombinator.com"
        or parsed.path != "/item"
        or parsed.fragment
    ):
        return None
    if re.fullmatch(r"id=[1-9][0-9]*", parsed.query) is None:
        return None
    return candidate


def verified_source_urls(item: RawItem) -> tuple[str, ...]:
    """Return stable, deduplicated URLs verified by the item's collector."""
    values = [item.url]
    discussion_url = _verified_hn_discussion_url(item)
    if discussion_url is not None:
        values.append(discussion_url)
    return tuple(dict.fromkeys(values))


def verified_source_urls_for_items(items: Iterable[RawItem]) -> tuple[str, ...]:
    """Combine per-item provenance without broadening any collector's rules."""
    return tuple(
        dict.fromkeys(
            url
            for item in items
            for url in verified_source_urls(item)
        )
    )

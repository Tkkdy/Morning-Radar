"""Conservative two-stage item deduplication."""

from __future__ import annotations

from morning_radar.models import RawItem
from morning_radar.processing.normalize import normalize_title, normalize_url


def deduplicate_items(items: list[RawItem]) -> list[RawItem]:
    """Prefer the first item for equivalent URLs, then equivalent title/source pairs."""
    unique: list[RawItem] = []
    seen_urls: set[str] = set()
    seen_titles_by_source: set[tuple[str, str]] = set()

    for item in items:
        url_key = normalize_url(item.url)
        title_key = (item.source_name.casefold(), normalize_title(item.title))
        if url_key in seen_urls or title_key in seen_titles_by_source:
            continue
        seen_urls.add(url_key)
        seen_titles_by_source.add(title_key)
        unique.append(item)
    return unique


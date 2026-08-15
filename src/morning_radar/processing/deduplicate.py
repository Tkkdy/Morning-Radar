"""Conservative two-stage item deduplication."""

from __future__ import annotations

from morning_radar.models import RawItem, SourceRole
from morning_radar.processing.normalize import normalize_title, normalize_url


def deduplicate_items(
    items: list[RawItem],
    *,
    preserve_discovery_pairs: bool = False,
) -> list[RawItem]:
    """Prefer first equivalents while optionally retaining discovery provenance."""
    unique: list[RawItem] = []
    seen_urls: dict[str, set[SourceRole]] = {}
    seen_titles_by_source: set[tuple[str, str]] = set()

    for item in items:
        url_key = normalize_url(item.url)
        title_key = (item.source_name.casefold(), normalize_title(item.title))
        existing_roles = seen_urls.get(url_key, set())
        has_upstream = SourceRole.UPSTREAM_DISCOVERY in existing_roles
        has_direct = bool(existing_roles - {SourceRole.UPSTREAM_DISCOVERY})
        is_upstream = item.source_role is SourceRole.UPSTREAM_DISCOVERY
        discovery_pair = preserve_discovery_pairs and (
            (is_upstream and has_direct and not has_upstream)
            or (not is_upstream and has_upstream and not has_direct)
        )
        if (existing_roles and not discovery_pair) or (
            title_key in seen_titles_by_source and not discovery_pair
        ):
            continue
        seen_urls.setdefault(url_key, set()).add(item.source_role)
        seen_titles_by_source.add(title_key)
        unique.append(item)
    return unique

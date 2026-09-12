"""Conservative two-stage item deduplication."""

from __future__ import annotations

from urllib.parse import urlsplit

from morning_radar.models import RawItem, SourceRole
from morning_radar.processing.normalize import normalize_title, normalize_url


def _comparison_url(item: RawItem) -> str:
    """Only official page sections retain their observed anchor as identity."""
    if _is_official_section(item):
        return item.url
    return normalize_url(item.url)


def _is_official_section(item: RawItem) -> bool:
    """Whether an official item has a real section anchor, not a URL fragment."""
    return bool(
        item.source_type == "official_changelog"
        and item.metadata.get("source_id")
        and urlsplit(item.url).fragment
    )


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
        url_key = _comparison_url(item)
        title_key = (item.source_name.casefold(), normalize_title(item.title))
        existing_roles = seen_urls.get(url_key, set())
        has_upstream = SourceRole.UPSTREAM_DISCOVERY in existing_roles
        has_direct = bool(existing_roles - {SourceRole.UPSTREAM_DISCOVERY})
        is_upstream = item.source_role is SourceRole.UPSTREAM_DISCOVERY
        has_practitioner = SourceRole.PRACTITIONER in existing_roles
        is_practitioner = item.source_role is SourceRole.PRACTITIONER
        official_practitioner_pair = (
            (is_practitioner and SourceRole.OFFICIAL_PRIMARY in existing_roles)
            or (item.source_role is SourceRole.OFFICIAL_PRIMARY and has_practitioner)
        )
        discovery_pair = preserve_discovery_pairs and (
            (is_upstream and has_direct and not has_upstream)
            or (not is_upstream and has_upstream and not has_direct)
        )
        preserve_pair = discovery_pair or official_practitioner_pair
        if (existing_roles and not preserve_pair) or (
            title_key in seen_titles_by_source
            and not preserve_pair
            and not _is_official_section(item)
        ):
            continue
        seen_urls.setdefault(url_key, set()).add(item.source_role)
        seen_titles_by_source.add(title_key)
        unique.append(item)
    return unique

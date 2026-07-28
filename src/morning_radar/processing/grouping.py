"""Offline, precision-first grouping before AI semantic confirmation."""

from __future__ import annotations

import re
from datetime import UTC

from morning_radar.models import RawItem
from morning_radar.processing.normalize import normalize_title

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "is",
    "its",
    "model",
    "new",
    "newest",
    "now",
    "of",
    "on",
    "the",
    "to",
    "with",
}
_ACTION_GROUPS = (
    frozenset(
        {
            "announce",
            "announced",
            "announces",
            "available",
            "availability",
            "launch",
            "launched",
            "launches",
            "release",
            "released",
            "releases",
            "ships",
            "shipping",
        }
    ),
    frozenset({"update", "updated", "upgrade", "upgraded"}),
    frozenset({"benchmark", "benchmarks", "tested", "testing"}),
)


def _tokens(title: str) -> set[str]:
    return {
        token
        for token in re.split(r"[\W_.\-]+", normalize_title(title))
        if len(token) >= 3 and token not in _STOP_WORDS
    }


def _strong_tokens(item: RawItem) -> set[str]:
    structured = {
        token
        for value in (*item.company_candidates, *item.repository_candidates)
        for token in _tokens(value)
    }
    title_entities = {
        token.casefold()
        for token in re.findall(r"\b[A-Z][A-Z0-9-]{1,}\b", item.title)
    }
    expanded_entities = {
        part
        for token in title_entities
        for part in re.split(r"[-_]", token)
        if len(part) >= 2
    }
    return structured | title_entities | expanded_entities


def _action_kinds(item: RawItem) -> set[int]:
    words = _tokens(item.title)
    return {
        index
        for index, group in enumerate(_ACTION_GROUPS)
        if words.intersection(group)
    }


def _published_distance_hours(left: RawItem, right: RawItem) -> float:
    left_time = left.published_at or left.fetched_at
    right_time = right.published_at or right.fetched_at
    return abs(
        (
            left_time.astimezone(UTC) - right_time.astimezone(UTC)
        ).total_seconds()
    ) / 3600


def _version_markers(title: str) -> dict[str, str]:
    normalized = normalize_title(title)
    return {
        product: version
        for product, version in re.findall(
            r"\b([a-z][a-z0-9]*)[- ]v?(\d+(?:\.\d+)*)\b",
            normalized,
        )
    }


def _has_conflicting_versions(left: RawItem, right: RawItem) -> bool:
    left_versions = _version_markers(left.title)
    right_versions = _version_markers(right.title)
    return any(
        left_versions[product] != right_versions[product]
        for product in left_versions.keys() & right_versions.keys()
    )


def _candidate_match(left: RawItem, right: RawItem) -> bool:
    if normalize_title(left.title) == normalize_title(right.title):
        return True
    if _has_conflicting_versions(left, right):
        return False
    if _published_distance_hours(left, right) > 72:
        return False

    shared_strong = _strong_tokens(left).intersection(_strong_tokens(right))
    shared_actions = _action_kinds(left).intersection(_action_kinds(right))
    if not shared_strong or not shared_actions:
        return False

    shared_title = _tokens(left.title).intersection(_tokens(right.title))
    return bool(shared_title.intersection(shared_strong))


def group_items_by_normalized_title(items: list[RawItem]) -> list[list[RawItem]]:
    """Build conservative candidate groups; AI still decides whether events match."""
    groups: list[list[RawItem]] = []
    for item in items:
        compatible = next(
            (
                group
                for group in groups
                if all(_candidate_match(item, existing) for existing in group)
            ),
            None,
        )
        if compatible is None:
            groups.append([item])
        else:
            compatible.append(item)
    return groups

"""Precision-first deterministic recall for cross-day Story relationships."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import Field

from morning_radar.models import (
    Story,
    StoryEvidenceRef,
    StoryOccurrenceRef,
    StoryRelationRecord,
    StoryRelationType,
    StoryStatus,
)
from morning_radar.models.core import RadarModel

_VERSION = re.compile(
    r"(?<![a-z0-9])v?(\d+(?:\.\d+)+)(?:(?:[- ]?)(rc|beta|alpha|preview)(\d*))?",
    re.IGNORECASE,
)
_STATUS_ORDER = {
    StoryStatus.UNKNOWN: 0,
    StoryStatus.RUMOR: 1,
    StoryStatus.OFFICIAL_TEASER: 2,
    StoryStatus.ANNOUNCED: 3,
    StoryStatus.AVAILABLE: 4,
    StoryStatus.UPDATED: 5,
}


class StoryMemory(RadarModel):
    ref: StoryOccurrenceRef
    story: Story


class RelationCandidate(RadarModel):
    previous: StoryMemory
    current: StoryMemory
    shared_products: list[str] = Field(min_length=1)
    shared_entities: list[str] = Field(default_factory=list)
    shared_topics: list[str] = Field(default_factory=list)
    product_named_in_both_titles: bool = False
    explicit_version_progression: bool = False
    prerelease_to_stable: bool = False
    same_release_series: bool = False
    status_progression: bool = False
    days_apart: int = Field(ge=1)


def _normalized(value: str) -> str:
    return " ".join(re.split(r"[_\s-]+", value.casefold())).strip()


def _shared(left: list[str], right: list[str]) -> list[str]:
    right_by_normalized = {_normalized(value): value for value in right}
    return [
        value
        for value in left
        if _normalized(value) in right_by_normalized
    ]


def _product_key(value: str) -> str:
    return _normalized(_VERSION.sub("", value)).strip()


def _shared_products(left: list[str], right: list[str]) -> list[str]:
    right_keys = {_product_key(value) for value in right}
    return list(
        dict.fromkeys(
            key
            for value in left
            if (key := _product_key(value)) and key in right_keys
        )
    )


def _versions(title: str) -> list[tuple[tuple[int, ...], str | None]]:
    return [
        (tuple(int(part) for part in match.group(1).split(".")), match.group(2))
        for match in _VERSION.finditer(title)
    ]


def _explicit_version_progression(previous: str, current: str) -> bool:
    for old_version, old_prerelease in _versions(previous):
        for new_version, new_prerelease in _versions(current):
            if old_version == new_version:
                if old_prerelease and not new_prerelease:
                    return True
                continue
            if old_prerelease or new_prerelease:
                continue
            if len(old_version) != len(new_version):
                continue
            if old_version[:-1] == new_version[:-1] and new_version[-1] == old_version[-1] + 1:
                return True
    return False


def _prerelease_to_stable(previous: str, current: str) -> bool:
    return any(
        old_version == new_version and old_prerelease and not new_prerelease
        for old_version, old_prerelease in _versions(previous)
        for new_version, new_prerelease in _versions(current)
    )


def _product_in_titles(products: list[str], previous: str, current: str) -> bool:
    old_title = _normalized(previous)
    new_title = _normalized(current)
    return any(
        _normalized(product) in old_title and _normalized(product) in new_title
        for product in products
    )


def _release_identity(story: Story) -> str | None:
    parsed = urlsplit(story.primary_source_url)
    if parsed.hostname != "github.com":
        return None
    parts = [part.casefold() for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    return f"github:{parts[0]}/{parts[1]}"


def generate_relation_candidates(
    current: list[StoryMemory],
    historical: list[StoryMemory],
    *,
    maximum_days: int,
    maximum_candidates: int,
) -> list[RelationCandidate]:
    """Recall bounded candidates; candidate status is never a confirmed relation."""
    candidates: list[RelationCandidate] = []
    for new in current:
        for old in historical:
            days_apart = (new.ref.date - old.ref.date).days
            if days_apart < 1 or days_apart > maximum_days:
                continue
            if new.ref == old.ref or new.ref.story_id == old.ref.story_id:
                continue
            products = _shared_products(
                old.story.product_names,
                new.story.product_names,
            )
            if not products:
                continue
            version_progression = _explicit_version_progression(
                old.story.canonical_title,
                new.story.canonical_title,
            )
            status_progression = (
                _STATUS_ORDER[new.story.status] > _STATUS_ORDER[old.story.status]
            )
            candidates.append(
                RelationCandidate(
                    previous=old,
                    current=new,
                    shared_products=products,
                    shared_entities=_shared(old.story.entity_names, new.story.entity_names),
                    shared_topics=_shared(old.story.topic_names, new.story.topic_names),
                    product_named_in_both_titles=_product_in_titles(
                        products,
                        old.story.canonical_title,
                        new.story.canonical_title,
                    ),
                    explicit_version_progression=version_progression,
                    prerelease_to_stable=_prerelease_to_stable(
                        old.story.canonical_title,
                        new.story.canonical_title,
                    ),
                    same_release_series=(
                        _release_identity(old.story) is not None
                        and _release_identity(old.story) == _release_identity(new.story)
                    ),
                    status_progression=status_progression,
                    days_apart=days_apart,
                )
            )
    candidates.sort(
        key=lambda value: (
            value.explicit_version_progression,
            value.same_release_series,
            value.product_named_in_both_titles,
            value.status_progression,
            -value.days_apart,
            value.previous.ref.story_id,
            value.current.ref.story_id,
        ),
        reverse=True,
    )
    return candidates[: max(0, maximum_candidates)]


def deterministic_relation(
    candidate: RelationCandidate,
    *,
    recorded_at: datetime,
) -> StoryRelationRecord | None:
    """Confirm only explicit version progressions with the product in both titles."""
    if not (
        candidate.explicit_version_progression
        and candidate.product_named_in_both_titles
        and candidate.same_release_series
    ):
        return None
    relation_type = (
        StoryRelationType.STATUS_TRANSITION
        if candidate.prerelease_to_stable
        else StoryRelationType.FOLLOW_UP
    )
    identity = (
        f"{candidate.previous.ref.date}:{candidate.previous.ref.story_id}|"
        f"{candidate.current.ref.date}:{candidate.current.ref.story_id}|{relation_type}"
    )
    relation_id = f"relation-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
    product = candidate.shared_products[0]
    return StoryRelationRecord(
        relation_id=relation_id,
        recorded_at=recorded_at,
        previous_story=candidate.previous.ref,
        current_story=candidate.current.ref,
        relation_type=relation_type,
        change_summary=(
            f"{product} 从“{candidate.previous.story.canonical_title}”"
            f"推进到“{candidate.current.story.canonical_title}”。"
        ),
        rationale="同一明确产品在相邻版本或预发布到稳定版本之间发生可验证推进。",
        evidence_refs=[
            StoryEvidenceRef(story=candidate.previous.ref),
            StoryEvidenceRef(story=candidate.current.ref),
        ],
    )

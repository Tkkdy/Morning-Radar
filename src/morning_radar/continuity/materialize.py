"""Convert validated Brief memory drafts into immutable daily records."""

from __future__ import annotations

import hashlib
from datetime import date, datetime

from morning_radar.ai.models import GeneratedJudgementDraft, GeneratedWatchDraft
from morning_radar.models import (
    DailyContinuity,
    JudgementRecord,
    Story,
    StoryEvidenceRef,
    StoryOccurrenceRef,
    WatchEvent,
    WatchEventType,
)


def _stable_id(prefix: str, *parts: object) -> str:
    identity = "|".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


def materialize_open_watches(
    drafts: list[GeneratedWatchDraft],
    *,
    brief_date: date,
    recorded_at: datetime,
    stories: list[Story],
    maximum_watches: int = 5,
) -> list[WatchEvent]:
    story_by_id = {story.id: story for story in stories}
    result: list[WatchEvent] = []
    seen_ids: set[str] = set()
    for draft in drafts[:maximum_watches]:
        source_refs = [
            StoryOccurrenceRef(date=brief_date, story_id=story_id)
            for story_id in draft.source_story_ids
            if story_id in story_by_id
        ]
        watch_id = _stable_id(
            "watch",
            brief_date,
            draft.expectation,
            *(ref.story_id for ref in source_refs),
        )
        if not source_refs or watch_id in seen_ids:
            continue
        result.append(
            WatchEvent(
                watch_id=watch_id,
                recorded_at=recorded_at,
                event_type=WatchEventType.OPENED,
                expectation=draft.expectation,
                entity_anchors=draft.entity_anchors,
                product_anchors=draft.product_anchors,
                topic_anchors=draft.topic_anchors,
                source_story_refs=source_refs,
            )
        )
        seen_ids.add(watch_id)
    return result


def materialize_judgements(
    drafts: list[GeneratedJudgementDraft],
    *,
    brief_date: date,
    recorded_at: datetime,
    stories: list[Story],
    maximum_judgements: int = 2,
) -> list[JudgementRecord]:
    story_ids = {story.id for story in stories}
    result: list[JudgementRecord] = []
    seen_ids: set[str] = set()
    for draft in drafts[:maximum_judgements]:
        evidence = [
            StoryEvidenceRef(
                story=StoryOccurrenceRef(date=brief_date, story_id=story_id)
            )
            for story_id in draft.evidence_story_ids
            if story_id in story_ids
        ]
        judgement_id = _stable_id(
            "judgement",
            brief_date,
            draft.claim,
            *(item.story.story_id for item in evidence),
        )
        if not evidence or judgement_id in seen_ids:
            continue
        result.append(
            JudgementRecord(
                judgement_id=judgement_id,
                root_judgement_id=judgement_id,
                recorded_at=recorded_at,
                claim=draft.claim,
                rationale=draft.rationale,
                evidence_refs=evidence,
                uncertainty=draft.uncertainty,
            )
        )
        seen_ids.add(judgement_id)
    return result


def merge_daily_continuity(
    existing: DailyContinuity | None,
    new: DailyContinuity,
) -> DailyContinuity:
    """Append new current-day assertions without losing an earlier same-day run."""
    if existing is None:
        return new
    if existing.date != new.date:
        raise ValueError("only continuity records from the same date may be merged")

    def unique(values, key):
        result = []
        seen = set()
        for value in values:
            identity = key(value)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(value)
        return result

    return DailyContinuity(
        date=existing.date,
        generated_at=existing.generated_at,
        relations=unique(
            [*existing.relations, *new.relations],
            lambda value: value.relation_id,
        ),
        watch_events=unique(
            [*existing.watch_events, *new.watch_events],
            lambda value: (
                value.watch_id,
                value.event_type,
                tuple(value.matched_story_refs),
            ),
        ),
        judgements=unique(
            [*existing.judgements, *new.judgements],
            lambda value: value.judgement_id,
        ),
    )

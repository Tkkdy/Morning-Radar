"""Immutable cross-day intelligence records for Morning Radar v0.3."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from morning_radar.models.core import RadarModel, _validate_aware_datetime


class StoryRelationType(StrEnum):
    FOLLOW_UP = "follow_up"
    STATUS_TRANSITION = "status_transition"


class RelationDisposition(StrEnum):
    CONFIRMED = "confirmed"
    RETRACTED = "retracted"


class WatchEventType(StrEnum):
    OPENED = "opened"
    MATCHED = "matched"
    CLOSED = "closed"


class JudgementUpdateKind(StrEnum):
    SUPPORTED = "supported"
    WEAKENED = "weakened"
    REVISED = "revised"
    OVERTURNED = "overturned"


class JudgementViewState(StrEnum):
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"


class StoryOccurrenceRef(RadarModel):
    """Identify one immutable Story occurrence, not merely a reusable Story ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    date: date
    story_id: str = Field(min_length=1)


class StoryEvidenceRef(RadarModel):
    """Fact evidence from an immutable Story occurrence.

    ``fact_indexes`` is optional because early Story history does not always
    provide sufficiently stable fact-level detail. An empty list means the
    validated Story occurrence as a whole is the evidence boundary.
    """

    story: StoryOccurrenceRef
    fact_indexes: list[int] = Field(default_factory=list)

    @field_validator("fact_indexes")
    @classmethod
    def fact_indexes_are_unique_and_nonnegative(cls, values: list[int]) -> list[int]:
        if any(value < 0 for value in values):
            raise ValueError("fact indexes must be nonnegative")
        if len(values) != len(set(values)):
            raise ValueError("fact indexes must be unique")
        return values


class StoryRelationRecord(RadarModel):
    relation_id: str = Field(min_length=1)
    recorded_at: datetime
    previous_story: StoryOccurrenceRef
    current_story: StoryOccurrenceRef
    relation_type: StoryRelationType
    disposition: RelationDisposition = RelationDisposition.CONFIRMED
    change_summary: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[StoryEvidenceRef] = Field(min_length=1)
    retracts_relation_id: str | None = None

    _recorded_is_aware = field_validator("recorded_at")(_validate_aware_datetime)

    @model_validator(mode="after")
    def validate_relation_semantics(self) -> Self:
        if self.previous_story == self.current_story:
            raise ValueError("a Story occurrence cannot be related to itself")
        if self.previous_story.date >= self.current_story.date:
            raise ValueError("previous Story must be from an earlier date")
        if self.disposition is RelationDisposition.RETRACTED:
            if not self.retracts_relation_id:
                raise ValueError("a retraction must reference the retracted relation")
        elif self.retracts_relation_id is not None:
            raise ValueError("only relation retractions may set retracts_relation_id")
        return self


class WatchEvent(RadarModel):
    watch_id: str = Field(min_length=1)
    recorded_at: datetime
    event_type: WatchEventType
    expectation: str = Field(min_length=1, max_length=1000)
    entity_anchors: list[str] = Field(default_factory=list)
    product_anchors: list[str] = Field(default_factory=list)
    topic_anchors: list[str] = Field(default_factory=list)
    source_story_refs: list[StoryOccurrenceRef] = Field(default_factory=list)
    matched_story_refs: list[StoryOccurrenceRef] = Field(default_factory=list)
    rationale: str | None = Field(default=None, max_length=1000)

    _recorded_is_aware = field_validator("recorded_at")(_validate_aware_datetime)

    @model_validator(mode="after")
    def validate_event_semantics(self) -> Self:
        anchors = [*self.entity_anchors, *self.product_anchors, *self.topic_anchors]
        if self.event_type is WatchEventType.OPENED:
            if not self.source_story_refs:
                raise ValueError("an opened Watch requires source Story references")
            if not anchors:
                raise ValueError("an opened Watch requires at least one concrete anchor")
            if self.matched_story_refs:
                raise ValueError("an opened Watch cannot already contain matches")
        elif self.event_type is WatchEventType.MATCHED:
            if not self.matched_story_refs:
                raise ValueError("a matched Watch requires matched Story references")
            if not self.rationale:
                raise ValueError("a matched Watch requires a rationale")
        elif self.matched_story_refs:
            raise ValueError("a closed Watch cannot add matched Story references")
        return self


class JudgementRecord(RadarModel):
    judgement_id: str = Field(min_length=1)
    root_judgement_id: str = Field(min_length=1)
    recorded_at: datetime
    claim: str = Field(min_length=1, max_length=1500)
    rationale: str = Field(min_length=1, max_length=1500)
    evidence_refs: list[StoryEvidenceRef] = Field(min_length=1)
    uncertainty: str | None = Field(default=None, max_length=1000)
    watch_ids: list[str] = Field(default_factory=list)
    depends_on_judgement_ids: list[str] = Field(default_factory=list)
    updates_judgement_id: str | None = None
    update_kind: JudgementUpdateKind | None = None

    _recorded_is_aware = field_validator("recorded_at")(_validate_aware_datetime)

    @model_validator(mode="after")
    def validate_update_chain(self) -> Self:
        is_update = self.updates_judgement_id is not None
        if is_update != (self.update_kind is not None):
            raise ValueError("judgement updates require both target and update kind")
        if not is_update and self.root_judgement_id != self.judgement_id:
            raise ValueError("a root judgement must use its own ID as root_judgement_id")
        if self.judgement_id in self.depends_on_judgement_ids:
            raise ValueError("a judgement cannot depend on itself")
        return self


class DailyContinuity(RadarModel):
    date: date
    generated_at: datetime
    relations: list[StoryRelationRecord] = Field(default_factory=list)
    watch_events: list[WatchEvent] = Field(default_factory=list)
    judgements: list[JudgementRecord] = Field(default_factory=list)

    _generated_is_aware = field_validator("generated_at")(_validate_aware_datetime)


class CurrentWatch(RadarModel):
    watch_id: str
    expectation: str
    opened_at: datetime
    entity_anchors: list[str] = Field(default_factory=list)
    product_anchors: list[str] = Field(default_factory=list)
    topic_anchors: list[str] = Field(default_factory=list)
    source_story_refs: list[StoryOccurrenceRef] = Field(default_factory=list)
    matched_story_refs: list[StoryOccurrenceRef] = Field(default_factory=list)
    is_open: bool = True

    _opened_is_aware = field_validator("opened_at")(_validate_aware_datetime)


class CurrentJudgement(RadarModel):
    root_judgement_id: str
    latest_record: JudgementRecord
    state: JudgementViewState = JudgementViewState.ACTIVE
    review_trigger_ids: list[str] = Field(default_factory=list)

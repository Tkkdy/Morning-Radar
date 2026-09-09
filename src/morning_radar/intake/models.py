"""Minimal intake identity, checkpoint, and processing-ledger models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator

from morning_radar.models.core import RadarModel, RawItem, _validate_aware_datetime

INTAKE_SCHEMA_VERSION = 1
INTAKE_POLICY_VERSION = "intake-v1"


class ProcessingStatus(StrEnum):
    UNPROCESSED = "unprocessed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DEFERRED_BUDGET = "deferred_budget"
    FAILED_RETRY = "failed_retry"
    EXCLUDED = "excluded"
    WAITING_EVIDENCE = "waiting_evidence"


class EvidenceStatus(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    SATISFIED = "satisfied"
    WAITING_EVIDENCE = "waiting_evidence"


class PublishStatus(StrEnum):
    NOT_GENERATED = "not_generated"
    GENERATED = "generated"
    DEPLOY_CONFIRMED = "deploy_confirmed"


class ReasonCode(StrEnum):
    DEFERRED_BUDGET = "deferred_budget"
    INTERRUPTED_RUN = "interrupted_run"
    RESEARCH_OUTPUT_INVALID = "research_output_invalid"
    RESEARCH_OUTPUT_TRUNCATED = "research_output_truncated"
    RESEARCH_CASE_MISSING = "research_case_missing"
    RESEARCH_CASE_UNKNOWN_ID = "research_case_unknown_id"
    RESEARCH_CASE_DUPLICATE_ID = "research_case_duplicate_id"
    WAITING_EVIDENCE = "waiting_evidence"
    BELOW_RELEVANCE_THRESHOLD = "below_relevance_threshold"
    EXCLUDED_STALE = "excluded_stale"
    OUT_OF_SCOPE = "out_of_scope"
    MERGED_INTO = "merged_into"
    GENERATED_NOT_DEPLOYED = "generated_not_deployed"
    SHADOW_DROP = "shadow_drop"
    SOURCE_FAILED = "source_failed"
    CACHE_INCONSISTENT = "cache_inconsistent"
    COMPLETE_CHECKPOINT_MISSING = "complete_checkpoint_missing"
    PROCESSED = "processed"
    ALREADY_EXISTS = "already_exists"
    CLASSIFIED_IRRELEVANT = "classified_irrelevant"
    STORY_BUILD_FAILED = "story_build_failed"
    MERGE_FAILED = "merge_failed"
    SCORE_FAILED = "score_failed"
    RESEARCH_FATAL = "research_fatal"
    RESEARCH_DEFERRED = "research_deferred"
    RESEARCH_OUT_OF_SCOPE = "research_out_of_scope"
    SUPERSEDED = "superseded"


class DiscoveryProvenance(RadarModel):
    source_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    url: str = Field(min_length=1)


class IntakeRecord(RadarModel):
    input_id: str = Field(min_length=1)
    content_version: str = Field(min_length=1)
    source_id: str | None = None
    url: str
    title: str = Field(min_length=1)
    published_at: datetime | None = None
    first_seen_at: datetime
    fetched_at: datetime
    durable_at: datetime | None = None
    item: RawItem
    provenance: list[DiscoveryProvenance] = Field(default_factory=list)
    batch_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)

    _published_is_aware = field_validator("published_at")(_validate_aware_datetime)
    _first_seen_is_aware = field_validator("first_seen_at")(_validate_aware_datetime)
    _fetched_is_aware = field_validator("fetched_at")(_validate_aware_datetime)
    _durable_is_aware = field_validator("durable_at")(_validate_aware_datetime)


class CheckpointManifest(RadarModel):
    schema_version: int = INTAKE_SCHEMA_VERSION
    complete: bool
    batch_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    created_at: datetime
    cutoff_at: datetime
    policy_version: str = INTAKE_POLICY_VERSION
    item_count: int = Field(ge=0)
    collector_stats: dict[str, dict[str, int]] = Field(default_factory=dict)
    failures: dict[str, str] = Field(default_factory=dict)
    truncated: bool = False
    cache_inconsistencies: list[str] = Field(default_factory=list)
    source_state_committed: bool = False

    _created_is_aware = field_validator("created_at")(_validate_aware_datetime)
    _cutoff_is_aware = field_validator("cutoff_at")(_validate_aware_datetime)


class IntakeCheckpoint(RadarModel):
    manifest: CheckpointManifest
    items: list[IntakeRecord] = Field(default_factory=list)
    source_state: dict[str, Any] = Field(default_factory=dict)


class LedgerEntry(RadarModel):
    input_id: str = Field(min_length=1)
    content_version: str = Field(min_length=1)
    processing: ProcessingStatus = ProcessingStatus.UNPROCESSED
    evidence: EvidenceStatus = EvidenceStatus.NOT_EVALUATED
    publish: PublishStatus = PublishStatus.NOT_GENERATED
    stage: str = "intake"
    outcome: str = "pending"
    reason_code: ReasonCode | None = None
    run_id: str = Field(min_length=1)
    batch_id: str | None = None
    updated_at: datetime
    attempt_count: int = Field(default=0, ge=0)
    next_retry_at: datetime | None = None
    last_input_version: str = Field(min_length=1)
    last_policy_version: str = INTAKE_POLICY_VERSION
    story_id: str | None = None
    merged_into: str | None = None
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    importance_score: float | None = Field(default=None, ge=0, le=1)
    relevance_threshold: float | None = Field(default=None, ge=0, le=1)
    importance_threshold: float | None = Field(default=None, ge=0, le=1)
    score_rationale: str | None = None
    brief_date: str | None = None
    brief_hash: str | None = None
    event_published_at: datetime | None = None
    first_seen_at: datetime
    durable_at: datetime | None = None
    processed_at: datetime | None = None
    deployed_at: datetime | None = None
    url: str | None = None
    title: str | None = None
    superseded_by: str | None = None

    _updated_is_aware = field_validator("updated_at")(_validate_aware_datetime)
    _retry_is_aware = field_validator("next_retry_at")(_validate_aware_datetime)
    _event_is_aware = field_validator("event_published_at")(_validate_aware_datetime)
    _first_seen_is_aware = field_validator("first_seen_at")(_validate_aware_datetime)
    _durable_is_aware = field_validator("durable_at")(_validate_aware_datetime)
    _processed_is_aware = field_validator("processed_at")(_validate_aware_datetime)
    _deployed_is_aware = field_validator("deployed_at")(_validate_aware_datetime)


class PublishRecord(RadarModel):
    brief_date: str = Field(min_length=1)
    brief_hash: str = Field(min_length=1)
    generated_at: datetime
    deployed: bool = False
    deployed_at: datetime | None = None
    notified: bool = False
    artifact_path: str | None = None

    _generated_is_aware = field_validator("generated_at")(_validate_aware_datetime)
    _deployed_is_aware = field_validator("deployed_at")(_validate_aware_datetime)

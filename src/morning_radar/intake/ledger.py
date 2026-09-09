"""Minimal processing ledger keyed by input identity and content version."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import Field

from morning_radar.intake.identity import intake_key
from morning_radar.intake.models import (
    INTAKE_POLICY_VERSION,
    EvidenceStatus,
    IntakeCheckpoint,
    IntakeRecord,
    LedgerEntry,
    ProcessingStatus,
    PublishStatus,
    ReasonCode,
)
from morning_radar.models.core import RadarModel
from morning_radar.storage import load_model, save_model


class ProcessingLedger(RadarModel):
    schema_version: int = 1
    entries: dict[str, LedgerEntry] = Field(default_factory=dict)


class ProcessingLedgerStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.ledger = (
            load_model(path, ProcessingLedger) if path.exists() else ProcessingLedger()
        )

    def get(self, input_id: str, content_version: str) -> LedgerEntry | None:
        return self.ledger.entries.get(intake_key(input_id, content_version))

    def find_by_input_id(self, input_id: str) -> list[LedgerEntry]:
        return [
            entry
            for entry in self.ledger.entries.values()
            if entry.input_id == input_id
        ]

    def find_by_url(self, url: str) -> list[LedgerEntry]:
        return [entry for entry in self.ledger.entries.values() if entry.url == url]

    def find_by_story_id(self, story_id: str) -> list[LedgerEntry]:
        return [
            entry
            for entry in self.ledger.entries.values()
            if entry.story_id == story_id or entry.merged_into == story_id
        ]

    def first_seen_at(self, input_id: str, fallback: datetime) -> datetime:
        existing = self.find_by_input_id(input_id)
        if not existing:
            return fallback
        return min(entry.first_seen_at for entry in existing)

    def upsert_checkpoint(
        self,
        checkpoint: IntakeCheckpoint,
        *,
        now: datetime,
    ) -> None:
        durable_at = checkpoint.manifest.created_at
        for record in checkpoint.items:
            self.ensure_record(record, now=now, durable_at=durable_at, stage="checkpoint")

    def ensure_record(
        self,
        record: IntakeRecord,
        *,
        now: datetime,
        durable_at: datetime,
        stage: str,
    ) -> LedgerEntry:
        key = intake_key(record.input_id, record.content_version)
        current = self.ledger.entries.get(key)
        if current is not None:
            if current.durable_at is None:
                current.durable_at = durable_at
            current.batch_id = record.batch_id
            current.url = record.url
            current.title = record.title
            return current
        first_seen = self.first_seen_at(record.input_id, record.first_seen_at)
        entry = LedgerEntry(
            input_id=record.input_id,
            content_version=record.content_version,
            processing=ProcessingStatus.UNPROCESSED,
            evidence=EvidenceStatus.NOT_EVALUATED,
            publish=PublishStatus.NOT_GENERATED,
            stage=stage,
            outcome="pending",
            run_id=record.run_id,
            batch_id=record.batch_id,
            updated_at=now,
            last_input_version=record.content_version,
            last_policy_version=INTAKE_POLICY_VERSION,
            event_published_at=record.published_at,
            first_seen_at=first_seen,
            durable_at=durable_at,
            url=record.url,
            title=record.title,
        )
        self.ledger.entries[key] = entry
        return entry

    def mark_interrupted(self, *, current_run_id: str, now: datetime) -> list[LedgerEntry]:
        interrupted: list[LedgerEntry] = []
        for entry in self.ledger.entries.values():
            if (
                entry.processing is ProcessingStatus.IN_PROGRESS
                and entry.run_id != current_run_id
            ):
                entry.processing = ProcessingStatus.UNPROCESSED
                entry.reason_code = ReasonCode.INTERRUPTED_RUN
                entry.outcome = "interrupted"
                entry.stage = "recovery"
                entry.updated_at = now
                interrupted.append(entry)
        return interrupted

    def update(
        self,
        input_id: str,
        content_version: str,
        *,
        now: datetime,
        **changes: object,
    ) -> LedgerEntry:
        key = intake_key(input_id, content_version)
        current = self.ledger.entries[key]
        payload = current.model_dump()
        payload.update(changes)
        payload["updated_at"] = now
        updated = LedgerEntry.model_validate(payload)
        self.ledger.entries[key] = updated
        return updated

    def unfinished(self) -> list[LedgerEntry]:
        return [
            entry
            for entry in self.ledger.entries.values()
            if entry.processing
            in {
                ProcessingStatus.UNPROCESSED,
                ProcessingStatus.IN_PROGRESS,
                ProcessingStatus.DEFERRED_BUDGET,
                ProcessingStatus.FAILED_RETRY,
            }
        ]

    def save(self) -> None:
        save_model(self.path, self.ledger)

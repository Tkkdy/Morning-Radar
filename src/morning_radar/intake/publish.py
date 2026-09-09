"""Generated-brief vs deployed-site state, independent of processing."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import Field

from morning_radar.intake.models import PublishRecord
from morning_radar.models.core import RadarModel
from morning_radar.storage import load_model, save_model


class PublishState(RadarModel):
    records: dict[str, PublishRecord] = Field(default_factory=dict)


class PublishStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = load_model(path, PublishState) if path.exists() else PublishState()

    def mark_generated(
        self,
        *,
        brief_date: str,
        brief_hash: str,
        generated_at: datetime,
        artifact_path: str,
    ) -> PublishRecord:
        existing = self.state.records.get(brief_date)
        if existing is not None and existing.brief_hash == brief_hash:
            return existing
        record = PublishRecord(
            brief_date=brief_date,
            brief_hash=brief_hash,
            generated_at=generated_at,
            deployed=False,
            notified=False,
            artifact_path=artifact_path,
        )
        self.state.records[brief_date] = record
        self.save()
        return record

    def mark_deployed(
        self,
        brief_date: str,
        *,
        now: datetime,
        brief_hash: str | None = None,
    ) -> PublishRecord:
        record = self.state.records.get(brief_date)
        if record is None:
            raise FileNotFoundError(f"No generated publish record for {brief_date}")
        if brief_hash and brief_hash != record.brief_hash:
            raise ValueError(
                f"Publish hash mismatch for {brief_date}: "
                f"recorded={record.brief_hash} requested={brief_hash}"
            )
        if record.deployed and (brief_hash is None or brief_hash == record.brief_hash):
            return record
        updated = record.model_copy(update={"deployed": True, "deployed_at": now})
        self.state.records[brief_date] = updated
        self.save()
        return updated

    def latest_generated(self) -> PublishRecord | None:
        if not self.state.records:
            return None
        return self.state.records[sorted(self.state.records)[-1]]

    def get(self, brief_date: str) -> PublishRecord | None:
        return self.state.records.get(brief_date)

    def save(self) -> None:
        save_model(self.path, self.state)

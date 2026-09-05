"""Deterministic Official Surface verification with a small JSON trust cache."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from morning_radar.models.core import RadarModel, _validate_aware_datetime
from morning_radar.storage import read_json, write_json

LOGGER = logging.getLogger(__name__)
MULTI_TENANT_ROOTS = {"github.com"}


class SurfaceTrustStatus(StrEnum):
    VERIFIED = "verified"
    STALE = "stale"
    REVOKED = "revoked"


class OfficialSurfaceTrust(RadarModel):
    surface: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    relationship: str = Field(min_length=1)
    verified_via: str = Field(min_length=1)
    verified_at: datetime
    status: SurfaceTrustStatus = SurfaceTrustStatus.VERIFIED
    confidence: float = Field(ge=0, le=1)

    _verified_is_aware = field_validator("verified_at")(_validate_aware_datetime)


class OfficialSurfaceResolver:
    def __init__(
        self,
        *,
        cache_path: Path,
        seeds: dict[str, str],
        now: datetime | None = None,
        stale_after_days: int = 90,
    ) -> None:
        self.cache_path = cache_path
        self.seeds = {
            surface.casefold().rstrip("."): entity
            for surface, entity in seeds.items()
            if surface.casefold().rstrip(".") not in MULTI_TENANT_ROOTS
        }
        self.now = now or datetime.now(UTC)
        self.stale_after = timedelta(days=stale_after_days)
        self.records = self._load()

    def _load(self) -> dict[str, OfficialSurfaceTrust]:
        if not self.cache_path.exists():
            return {}
        return {
            item.surface: item
            for item in (
                OfficialSurfaceTrust.model_validate(value)
                for value in read_json(self.cache_path)
            )
        }

    def _save(self) -> None:
        try:
            write_json(
                self.cache_path,
                [record.model_dump(mode="json") for record in self.records.values()],
            )
        except OSError:
            LOGGER.exception("Official Surface trust cache could not be persisted")

    def verify(self, url: str) -> OfficialSurfaceTrust | None:
        hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
        cached = self.records.get(hostname)
        if cached and cached.status is SurfaceTrustStatus.REVOKED:
            return None
        if cached and cached.status is SurfaceTrustStatus.VERIFIED:
            if self.now - cached.verified_at <= self.stale_after:
                return cached
            cached = cached.model_copy(update={"status": SurfaceTrustStatus.STALE})
            self.records[hostname] = cached
            self._save()
        for root, entity in self.seeds.items():
            if hostname == root or hostname.endswith(f".{root}"):
                relationship = "trusted_official_root" if hostname == root else "verified_subdomain"
                record = OfficialSurfaceTrust(
                    surface=hostname,
                    entity=entity,
                    relationship=relationship,
                    verified_via=root,
                    verified_at=self.now,
                    confidence=1.0 if hostname == root else 0.95,
                )
                self.records[hostname] = record
                self._save()
                return record
        return None

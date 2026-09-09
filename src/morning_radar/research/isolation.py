"""Per-case research result container."""

from __future__ import annotations

from dataclasses import dataclass, field

from morning_radar.ai.models import ResearchResolutionBatch


@dataclass(slots=True)
class IsolatedResearchResult:
    batch: ResearchResolutionBatch
    invalid_ids: list[str] = field(default_factory=list)
    missing_ids: list[str] = field(default_factory=list)
    unknown_ids: list[str] = field(default_factory=list)
    duplicate_ids: list[str] = field(default_factory=list)
    truncated: bool = False
    fatal_kind: str | None = None
    error: str | None = None

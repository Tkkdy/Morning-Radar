"""Bounded evidence acquisition and official-surface trust."""

from morning_radar.evidence.http import (
    EvidenceFetchError,
    EvidenceFetchResult,
    SafeEvidenceFetcher,
)
from morning_radar.evidence.official import (
    OfficialSurfaceResolver,
    OfficialSurfaceTrust,
    SurfaceTrustStatus,
)
from morning_radar.evidence.resolver import EvidenceResolutionResult, resolve_evidence

__all__ = [
    "EvidenceFetchError",
    "EvidenceFetchResult",
    "EvidenceResolutionResult",
    "OfficialSurfaceResolver",
    "OfficialSurfaceTrust",
    "SafeEvidenceFetcher",
    "SurfaceTrustStatus",
    "resolve_evidence",
]

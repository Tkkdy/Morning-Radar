"""Candidate admission and semantic triage."""

from morning_radar.candidates.eligibility import apply_build_eligibility_guard
from morning_radar.candidates.engine import (
    CandidateRunResult,
    admit_candidates,
    attach_official_source_entities,
    candidate_story_inputs,
    radar_signals_from_candidates,
    triage_candidates,
)
from morning_radar.candidates.freshness import apply_freshness_guard

__all__ = [
    "CandidateRunResult",
    "admit_candidates",
    "apply_build_eligibility_guard",
    "attach_official_source_entities",
    "apply_freshness_guard",
    "candidate_story_inputs",
    "radar_signals_from_candidates",
    "triage_candidates",
]

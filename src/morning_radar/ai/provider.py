"""AI provider contract shared by production and fixture implementations."""

from __future__ import annotations

from typing import Protocol

from morning_radar.ai.models import (
    BriefDraft,
    CandidateTriageBatch,
    ContinuityResolution,
    ContinuityResolutionInput,
    DirectionObservation,
    MergedStoryDraft,
    StoryScore,
    TendencyEvaluationBatch,
)
from morning_radar.editorial.models import EditorialDecision, EditorialDecisionBatch
from morning_radar.models import (
    Candidate,
    Signal,
    Story,
    TendencyCurrentView,
    TendencyEvidenceCluster,
)


class AIProvider(Protocol):
    def triage_candidates(self, candidates: list[Candidate]) -> CandidateTriageBatch: ...

    def construct_story(self, candidate: Candidate) -> MergedStoryDraft: ...

    def score_story(self, story: Story) -> StoryScore: ...

    def evaluate_editorial(self, stories: list[Story]) -> EditorialDecisionBatch: ...

    def write_brief(
        self,
        stories: list[Story],
        signals: list[Signal],
        editorial_decisions: list[EditorialDecision] | None = None,
    ) -> BriefDraft: ...

    def write_direction_observation(
        self,
        signals: list[Signal],
    ) -> DirectionObservation: ...

    def resolve_continuity(
        self,
        context: ContinuityResolutionInput,
    ) -> ContinuityResolution: ...

    def evaluate_tendencies(
        self,
        clusters: list[TendencyEvidenceCluster],
        current_views: list[TendencyCurrentView],
    ) -> TendencyEvaluationBatch: ...

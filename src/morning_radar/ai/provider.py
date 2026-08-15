"""AI provider contract shared by production and fixture implementations."""

from __future__ import annotations

from typing import Protocol

from morning_radar.ai.models import (
    BriefDraft,
    ClassificationBatch,
    ContinuityResolution,
    ContinuityResolutionInput,
    DirectionObservation,
    MergedStoryDraft,
    ResearchResolutionBatch,
    StoryScore,
    TendencyEvaluationBatch,
)
from morning_radar.models import (
    RawItem,
    ResearchCase,
    Signal,
    Story,
    TendencyCurrentView,
    TendencyEvidenceCluster,
)


class AIProvider(Protocol):
    def classify_items(self, items: list[RawItem]) -> ClassificationBatch: ...

    def merge_story(self, items: list[RawItem]) -> MergedStoryDraft: ...

    def score_story(self, story: Story) -> StoryScore: ...

    def write_brief(self, stories: list[Story], signals: list[Signal]) -> BriefDraft: ...

    def write_direction_observation(
        self,
        signals: list[Signal],
    ) -> DirectionObservation: ...

    def resolve_continuity(
        self,
        context: ContinuityResolutionInput,
    ) -> ContinuityResolution: ...

    def resolve_research_cases(
        self,
        cases: list[ResearchCase],
    ) -> ResearchResolutionBatch: ...

    def evaluate_tendencies(
        self,
        clusters: list[TendencyEvidenceCluster],
        current_views: list[TendencyCurrentView],
    ) -> TendencyEvaluationBatch: ...

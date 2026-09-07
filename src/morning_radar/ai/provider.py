"""AI provider contract shared by production and fixture implementations."""

from __future__ import annotations

from typing import Protocol

from morning_radar.ai.models import (
    BriefDraft,
    BriefItemRecoveryDraft,
    ClassificationBatch,
    ContinuityResolution,
    ContinuityResolutionInput,
    DirectionObservation,
    MergedStoryDraft,
    ResearchResolutionBatch,
    StoryScore,
    TendencyEvaluationBatch,
)
from morning_radar.editorial.models import EditorialDecision, EditorialDecisionBatch
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

    def evaluate_editorial(self, stories: list[Story]) -> EditorialDecisionBatch: ...

    def write_brief(
        self,
        stories: list[Story],
        signals: list[Signal],
        editorial_decisions: list[EditorialDecision] | None = None,
    ) -> BriefDraft: ...

    def recover_brief_item(
        self,
        story: Story,
        signals: list[Signal],
        editorial_decision: EditorialDecision | None = None,
    ) -> BriefItemRecoveryDraft: ...

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

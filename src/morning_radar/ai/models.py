"""Structured schemas exchanged with the single AI provider."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from morning_radar.models.candidate import (
    CandidateReasonCode,
    ClaimScopeDimensions,
    ClaimType,
    EvidenceState,
    SemanticDisposition,
)
from morning_radar.models.continuity import (
    JudgementUpdateKind,
    StoryEvidenceRef,
    StoryOccurrenceRef,
    StoryRelationType,
)
from morning_radar.models.core import RadarModel, StoryStatus
from morning_radar.models.tendency import (
    TendencyAssessment,
    TendencyStanding,
    TendencyUpdateKind,
)


class CandidateTriageDraft(RadarModel):
    candidate_id: str
    hypothesis: str = Field(min_length=1, max_length=1500)
    potential_novelty: str = Field(default="", max_length=1500)
    potential_impact: str = Field(default="", max_length=1500)
    affected_audiences: list[str] = Field(default_factory=list)
    impact_mechanism: str = Field(default="", max_length=1500)
    alternative_explanation: str | None = Field(default=None, max_length=1500)
    semantic_disposition: SemanticDisposition
    evidence_state: EvidenceState
    reason_codes: list[CandidateReasonCode] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=1500)
    missing_evidence: list[str] = Field(default_factory=list)
    verification_target: str | None = Field(default=None, max_length=1000)
    verification_path: str | None = Field(default=None, max_length=1500)
    investigation_priority: float = Field(default=0, ge=0, le=1)


class CandidateTriageBatch(RadarModel):
    candidates: list[CandidateTriageDraft] = Field(default_factory=list)


class ClassifiedItem(RadarModel):
    item_id: str
    relevant: bool
    relevance_reason: str
    important: bool
    importance_reason: str
    category: str


class ClassificationBatch(RadarModel):
    items: list[ClassifiedItem]


class DraftClaimSupport(RadarModel):
    claim: str = Field(min_length=1, max_length=2000)
    claim_type: ClaimType = ClaimType.OTHER
    evidence_ids: list[str] = Field(min_length=1)
    requested_scope: ClaimScopeDimensions = Field(default_factory=ClaimScopeDimensions)
    evidence_scope: str = Field(min_length=1, max_length=1500)
    claim_scope: str = Field(min_length=1, max_length=1500)
    # Model proposal retained for diagnostics; deterministic validation ignores it.
    scope_supported: bool = False


class MergedStoryDraft(RadarModel):
    same_event: bool
    canonical_title: str
    category: str
    entity_names: list[str] = Field(default_factory=list)
    product_names: list[str] = Field(default_factory=list)
    topic_names: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    fact_supports: list[DraftClaimSupport] = Field(default_factory=list)
    analysis: list[str] = Field(default_factory=list)
    opinions: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    primary_source_url: str | None = None
    status: StoryStatus = StoryStatus.UNKNOWN


class StoryScore(RadarModel):
    relevance_score: float = Field(ge=0, le=1)
    importance_score: float = Field(ge=0, le=1)
    novelty_score: float = Field(ge=0, le=1)
    credibility_score: float = Field(ge=0, le=1)
    explanation: str


class GeneratedBriefItem(RadarModel):
    story_ids: list[str]
    section: str
    title: str
    what_happened: str
    why_it_matters: str
    market_or_community_reaction: str | None = None
    uncertainty: str | None = None
    source_urls: list[str]


class GeneratedWatchDraft(RadarModel):
    expectation: str = Field(min_length=1, max_length=1000)
    source_story_ids: list[str] = Field(min_length=1)
    entity_anchors: list[str] = Field(default_factory=list)
    product_anchors: list[str] = Field(default_factory=list)
    topic_anchors: list[str] = Field(default_factory=list)


class GeneratedJudgementDraft(RadarModel):
    claim: str = Field(min_length=1, max_length=1500)
    rationale: str = Field(min_length=1, max_length=1500)
    evidence_story_ids: list[str] = Field(min_length=1)
    uncertainty: str | None = Field(default=None, max_length=1000)


class BriefDraft(RadarModel):
    items: list[GeneratedBriefItem]
    watch_items: list[GeneratedWatchDraft] = Field(default_factory=list)
    judgements: list[GeneratedJudgementDraft] = Field(default_factory=list)
    watch_next: list[str] = Field(default_factory=list)
    cognitive_extension: str | None = None


class DirectionObservation(RadarModel):
    observation: str | None = None
    evidence_story_ids: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    uncertainties: list[str] = Field(default_factory=list)


class TendencyFormationSupportDraft(RadarModel):
    cluster_id: str = Field(min_length=1)
    directly_supports_direction: bool
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_scope: str = Field(min_length=1, max_length=1000)


class TendencyDecisionDraft(RadarModel):
    existing_tendency_id: str | None = None
    standing_after: TendencyStanding
    update_kind: TendencyUpdateKind | None = None
    claim: str = Field(min_length=1, max_length=1500)
    assessment: TendencyAssessment
    supporting_cluster_ids: list[str] = Field(default_factory=list)
    counterevidence_cluster_ids: list[str] = Field(default_factory=list)
    formation_support: list[TendencyFormationSupportDraft] = Field(default_factory=list)
    claim_scope_supported: bool = False
    scope_alignment_rationale: str = Field(default="", max_length=1000)


class TendencyEvaluationBatch(RadarModel):
    decisions: list[TendencyDecisionDraft] = Field(default_factory=list)


class ContinuityStorySummary(RadarModel):
    ref: StoryOccurrenceRef
    canonical_title: str
    facts: list[str] = Field(default_factory=list)
    entity_names: list[str] = Field(default_factory=list)
    product_names: list[str] = Field(default_factory=list)
    topic_names: list[str] = Field(default_factory=list)
    status: StoryStatus = StoryStatus.UNKNOWN


class ContinuityRelationCandidate(RadarModel):
    previous: ContinuityStorySummary
    current: ContinuityStorySummary
    shared_products: list[str] = Field(default_factory=list)
    shared_entities: list[str] = Field(default_factory=list)
    shared_topics: list[str] = Field(default_factory=list)
    product_named_in_both_titles: bool = False
    explicit_version_progression: bool = False
    prerelease_to_stable: bool = False
    same_release_series: bool = False
    status_progression: bool = False
    days_apart: int = Field(ge=1)


class ContinuityWatchInput(RadarModel):
    watch_id: str
    expectation: str
    entity_anchors: list[str] = Field(default_factory=list)
    product_anchors: list[str] = Field(default_factory=list)
    topic_anchors: list[str] = Field(default_factory=list)
    current_story_candidates: list[ContinuityStorySummary] = Field(default_factory=list)


class PriorJudgementInput(RadarModel):
    """A prior hypothesis, deliberately separate from factual evidence."""

    judgement_id: str
    root_judgement_id: str
    claim: str
    rationale: str
    uncertainty: str | None = None
    current_story_candidates: list[ContinuityStorySummary] = Field(default_factory=list)


class ContinuityResolutionInput(RadarModel):
    relation_candidates: list[ContinuityRelationCandidate] = Field(default_factory=list)
    watch_candidates: list[ContinuityWatchInput] = Field(default_factory=list)
    prior_hypotheses: list[PriorJudgementInput] = Field(default_factory=list)


class ResolvedRelationDraft(RadarModel):
    confirmed: bool
    previous_story: StoryOccurrenceRef
    current_story: StoryOccurrenceRef
    relation_type: StoryRelationType | None = None
    what_changed: str | None = None
    rationale: str
    evidence_refs: list[StoryEvidenceRef] = Field(default_factory=list)


class ResolvedWatchMatchDraft(RadarModel):
    matched: bool
    watch_id: str
    matched_story_refs: list[StoryOccurrenceRef] = Field(default_factory=list)
    rationale: str


class ResolvedJudgementUpdateDraft(RadarModel):
    prior_judgement_id: str
    update_kind: JudgementUpdateKind
    claim: str
    rationale: str
    evidence_refs: list[StoryEvidenceRef] = Field(min_length=1)
    uncertainty: str | None = None


class ContinuityResolution(RadarModel):
    relations: list[ResolvedRelationDraft] = Field(default_factory=list)
    watch_matches: list[ResolvedWatchMatchDraft] = Field(default_factory=list)
    judgement_updates: list[ResolvedJudgementUpdateDraft] = Field(default_factory=list)

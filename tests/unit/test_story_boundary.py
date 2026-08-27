from datetime import UTC, datetime

import pytest

from morning_radar.ai import FakeAIProvider
from morning_radar.ai.models import DraftClaimSupport, MergedStoryDraft
from morning_radar.models import (
    Candidate,
    CandidateEvidence,
    ClaimType,
    EvidenceAuthority,
    EvidenceState,
    ExecutionState,
    RawItem,
    SemanticDisposition,
    SourceRole,
    StatementType,
)
from morning_radar.processing.story_builder import StoryValidationError, build_candidate_story

NOW = datetime(2026, 8, 22, tzinfo=UTC)
URL = "https://docs.example.com/release"


def candidate(authority: EvidenceAuthority) -> Candidate:
    return Candidate(
        id="candidate-one",
        created_at=NOW,
        updated_at=NOW,
        raw_item_ids=["raw-one"],
        hypothesis="Example release",
        semantic_disposition=SemanticDisposition.BUILD,
        evidence_state=EvidenceState.SUFFICIENT,
        execution_state=ExecutionState.NOT_NEEDED,
        evidence=[
            CandidateEvidence(
                evidence_id="evidence-one",
                raw_item_id="raw-one",
                url=URL,
                publisher="Example",
                source_role=(
                    SourceRole.PRACTITIONER
                    if authority is EvidenceAuthority.FIRSTHAND_OBSERVATION
                    else SourceRole.OFFICIAL_PRIMARY
                ),
                statement_type=StatementType.FACTUAL_ANNOUNCEMENT,
                authority=authority,
                scope="Example published the release page.",
            )
        ],
    )


def raw() -> RawItem:
    return RawItem(
        id="raw-one",
        title="Example release",
        url=URL,
        source_name="Example",
        source_type="rss",
        fetched_at=NOW,
    )


class DraftProvider(FakeAIProvider):
    def __init__(self, fact: str, claim_type: ClaimType, *, scope_supported: bool = True):
        self.fact = fact
        self.claim_type = claim_type
        self.scope_supported = scope_supported

    def construct_story(self, candidate):
        return MergedStoryDraft(
            same_event=True,
            canonical_title=self.fact,
            category="ai_and_open_source",
            facts=[self.fact],
            fact_supports=[
                DraftClaimSupport(
                    claim=self.fact,
                    claim_type=self.claim_type,
                    evidence_ids=["evidence-one"],
                    evidence_scope="仅覆盖输入证据明确陈述的范围",
                    claim_scope="输入事实范围",
                    scope_supported=self.scope_supported,
                )
            ],
            source_urls=[URL],
            primary_source_url=URL,
        )


def test_claim_scope_must_be_supported() -> None:
    with pytest.raises(StoryValidationError, match="Claim Scope"):
        build_candidate_story(
            candidate(EvidenceAuthority.SELF_AUTHORITATIVE),
            raw_items=[raw()],
            provider=DraftProvider(
                "Example 发布了版本",
                ClaimType.RELEASE_GA,
                scope_supported=False,
            ),
            now=NOW,
        )


def test_practitioner_observation_cannot_be_expanded_to_ga() -> None:
    with pytest.raises(StoryValidationError, match="official release/GA"):
        build_candidate_story(
            candidate(EvidenceAuthority.FIRSTHAND_OBSERVATION),
            raw_items=[raw()],
            provider=DraftProvider("Example 已全球 GA", ClaimType.RELEASE_GA),
            now=NOW,
        )


def test_official_performance_claim_must_remain_attributed() -> None:
    with pytest.raises(StoryValidationError, match="official claim"):
        build_candidate_story(
            candidate(EvidenceAuthority.SELF_AUTHORITATIVE),
            raw_items=[raw()],
            provider=DraftProvider("Example 性能提升 100×", ClaimType.OTHER),
            now=NOW,
        )

    story = build_candidate_story(
        candidate(EvidenceAuthority.SELF_AUTHORITATIVE),
        raw_items=[raw()],
        provider=DraftProvider("Example 官方宣称性能提升 100×", ClaimType.PERFORMANCE),
        now=NOW,
    )
    assert story.facts == ["Example 官方宣称性能提升 100×"]


def test_discovery_only_input_cannot_cross_story_boundary() -> None:
    with pytest.raises(StoryValidationError, match="discovery-only"):
        build_candidate_story(
            candidate(EvidenceAuthority.DISCOVERY_ONLY),
            raw_items=[raw()],
            provider=DraftProvider("社区称 Example 已发布", ClaimType.RELEASE_GA),
            now=NOW,
        )

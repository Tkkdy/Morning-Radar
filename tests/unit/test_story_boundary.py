from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from morning_radar.ai import FakeAIProvider
from morning_radar.ai.models import DraftClaimSupport, MergedStoryDraft
from morning_radar.models import (
    AssertionScope,
    AvailabilityScope,
    Candidate,
    CandidateEvidence,
    ClaimScopeDimensions,
    ClaimType,
    EvidenceAuthority,
    EvidenceState,
    ExecutionState,
    ObservationQuality,
    RawItem,
    SemanticDisposition,
    SourceRole,
    StatementType,
    TemporalScope,
)
from morning_radar.processing.story_builder import StoryValidationError, build_candidate_story

NOW = datetime(2026, 8, 22, tzinfo=UTC)
URL = "https://docs.example.com/release"


def candidate(
    authority: EvidenceAuthority,
    *,
    support_scope: ClaimScopeDimensions | None = None,
    authoritative_for: list[str] | None = None,
) -> Candidate:
    default_scopes = {
        EvidenceAuthority.SELF_AUTHORITATIVE: ClaimScopeDimensions(
            availability=AvailabilityScope.GA,
            temporal=TemporalScope.NEWLY_RELEASED,
            assertion=AssertionScope.OFFICIALLY_ANNOUNCED,
        ),
        EvidenceAuthority.FIRSTHAND_OBSERVATION: ClaimScopeDimensions(
            availability=AvailabilityScope.ONE_ACCOUNT,
            temporal=TemporalScope.OBSERVED_NOW,
            assertion=AssertionScope.OBSERVED,
        ),
        EvidenceAuthority.INDEPENDENT_REPORTING: ClaimScopeDimensions(
            temporal=TemporalScope.CURRENTLY_EXISTS,
            assertion=AssertionScope.UNKNOWN,
        ),
    }
    return Candidate(
        id="candidate-one",
        created_at=NOW,
        updated_at=NOW,
        raw_item_ids=["raw-one"],
        hypothesis="Example release",
        entity_names=["Example"],
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
                authoritative_for=(
                    authoritative_for
                    if authoritative_for is not None
                    else (["Example"] if authority is EvidenceAuthority.SELF_AUTHORITATIVE else [])
                ),
                subject_entities=["Example"],
                support_scope=(
                    support_scope
                    or default_scopes.get(authority, ClaimScopeDimensions())
                ),
                scope="Example published the release page.",
                observation_quality=(
                    ObservationQuality(
                        firsthandness=True,
                        specificity=True,
                        artifact_support=True,
                    )
                    if authority is EvidenceAuthority.FIRSTHAND_OBSERVATION
                    else None
                ),
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
    def __init__(
        self,
        fact: str,
        claim_type: ClaimType,
        *,
        scope_supported: bool = True,
        requested_scope: ClaimScopeDimensions | None = None,
    ):
        self.fact = fact
        self.claim_type = claim_type
        self.scope_supported = scope_supported
        self.requested_scope = requested_scope or ClaimScopeDimensions()

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
                    requested_scope=self.requested_scope,
                    evidence_scope="仅覆盖输入证据明确陈述的范围",
                    claim_scope="输入事实范围",
                    scope_supported=self.scope_supported,
                )
            ],
            source_urls=[URL],
            primary_source_url=URL,
        )


def test_scope_supported_false_is_diagnostic_and_does_not_veto_valid_scope() -> None:
    story = build_candidate_story(
        candidate(EvidenceAuthority.SELF_AUTHORITATIVE),
        raw_items=[raw()],
        provider=DraftProvider(
            "Example 官方表示新版本已发布",
            ClaimType.OTHER,
            scope_supported=False,
        ),
        now=NOW,
    )

    assert story.facts == ["Example 官方表示新版本已发布"]


def test_scope_supported_true_cannot_expand_practitioner_observation_to_ga() -> None:
    with pytest.raises(StoryValidationError, match="Claim Scope"):
        build_candidate_story(
            candidate(EvidenceAuthority.FIRSTHAND_OBSERVATION),
            raw_items=[raw()],
            provider=DraftProvider("Example 已全球 GA", ClaimType.RELEASE_GA),
            now=NOW,
        )


def test_official_performance_claim_must_remain_attributed() -> None:
    with pytest.raises(StoryValidationError, match="Claim Scope"):
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
    with pytest.raises(StoryValidationError, match="Claim Scope"):
        build_candidate_story(
            candidate(EvidenceAuthority.DISCOVERY_ONLY),
            raw_items=[raw()],
            provider=DraftProvider("社区称 Example 已发布", ClaimType.RELEASE_GA),
            now=NOW,
        )


@pytest.mark.parametrize(
    ("fact", "entities", "authoritative_for"),
    [
        (
            "Ollama 发布了 v0.33.0-rc0 版本。",
            ["ollama/ollama", "ollama"],
            ["ollama/ollama", "ollama", "Ollama"],
        ),
        (
            "pydantic-ai 发布了 v2.33.0 版本。",
            ["pydantic/pydantic-ai", "pydantic-ai"],
            ["pydantic/pydantic-ai", "pydantic-ai", "Pydantic Ai"],
        ),
        (
            "Cloudflare 宣布推出 Bot Preference Sync。",
            ["Cloudflare"],
            ["Cloudflare"],
        ),
        (
            "GitHub 宣布 Copilot 集成进入公开预览。",
            ["GitHub"],
            ["GitHub"],
        ),
    ],
)
def test_grounded_entity_metadata_crosses_subject_boundary(
    fact: str, entities: list[str], authoritative_for: list[str]
) -> None:
    grounded = candidate(
        EvidenceAuthority.SELF_AUTHORITATIVE,
        authoritative_for=authoritative_for,
    ).model_copy(
        update={
            "entity_names": entities,
            "evidence": [
                candidate(
                    EvidenceAuthority.SELF_AUTHORITATIVE,
                    authoritative_for=authoritative_for,
                ).evidence[0].model_copy(update={"excerpt": fact})
            ],
        }
    )

    story = build_candidate_story(
        grounded,
        raw_items=[raw()],
        provider=DraftProvider(fact, ClaimType.OTHER),
        now=NOW,
    )

    assert story.claim_supports[0].claim_subject in authoritative_for


def test_official_authority_is_entity_scoped() -> None:
    with pytest.raises(StoryValidationError, match="Claim Scope"):
        build_candidate_story(
            candidate(
                EvidenceAuthority.SELF_AUTHORITATIVE,
                authoritative_for=["Other Corp"],
            ),
            raw_items=[raw()],
            provider=DraftProvider("Example 官方表示新版本已发布", ClaimType.OTHER),
            now=NOW,
        )


def test_independent_current_existence_does_not_prove_new_release() -> None:
    with pytest.raises(StoryValidationError, match="Claim Scope"):
        build_candidate_story(
            candidate(EvidenceAuthority.INDEPENDENT_REPORTING),
            raw_items=[raw()],
            provider=DraftProvider("Example 今天新发布了功能", ClaimType.OTHER),
            now=NOW,
        )


def test_independent_reporting_cannot_authorize_an_unrelated_subject() -> None:
    with pytest.raises(StoryValidationError, match="Claim subject"):
        build_candidate_story(
            candidate(EvidenceAuthority.INDEPENDENT_REPORTING),
            raw_items=[raw()],
            provider=DraftProvider(
                "Other Corp 当前存在该功能",
                ClaimType.OTHER,
            ),
            now=NOW,
        )


def test_model_draft_cannot_supply_claim_subject() -> None:
    with pytest.raises(ValidationError, match="claim_subject"):
        DraftClaimSupport(
            claim="Other Corp 当前存在该功能",
            claim_subject="Example",
            evidence_ids=["evidence-one"],
            evidence_scope="Example Evidence",
            claim_scope="Other Corp claim",
        )


def test_independent_reporting_does_not_prove_performance_by_default() -> None:
    with pytest.raises(StoryValidationError, match="Claim Scope"):
        build_candidate_story(
            candidate(EvidenceAuthority.INDEPENDENT_REPORTING),
            raw_items=[raw()],
            provider=DraftProvider("Example 性能提升 100×", ClaimType.PERFORMANCE),
            now=NOW,
        )


def test_explicit_independent_verification_can_support_performance() -> None:
    verified_scope = ClaimScopeDimensions(
        temporal=TemporalScope.CURRENTLY_EXISTS,
        assertion=AssertionScope.INDEPENDENTLY_VERIFIED,
    )
    story = build_candidate_story(
        candidate(
            EvidenceAuthority.INDEPENDENT_REPORTING,
            support_scope=verified_scope,
        ),
        raw_items=[raw()],
        provider=DraftProvider("Example 性能提升 100×", ClaimType.PERFORMANCE),
        now=NOW,
    )

    assert story.claim_supports[0].claim_subject == "Example"


def test_low_quality_practitioner_observation_cannot_cross_boundary() -> None:
    low_quality = candidate(EvidenceAuthority.FIRSTHAND_OBSERVATION)
    low_quality.evidence[0].observation_quality = ObservationQuality(
        firsthandness=True,
        specificity=False,
        artifact_support=False,
    )
    with pytest.raises(StoryValidationError, match="Claim Scope"):
        build_candidate_story(
            low_quality,
            raw_items=[raw()],
            provider=DraftProvider(
                "我在 Example 的单个账户观察到该功能",
                ClaimType.FIRSTHAND_BEHAVIOR,
            ),
            now=NOW,
        )


def test_official_current_document_does_not_prove_new_release() -> None:
    current_only = ClaimScopeDimensions(
        temporal=TemporalScope.CURRENTLY_EXISTS,
        assertion=AssertionScope.OFFICIALLY_ANNOUNCED,
    )
    with pytest.raises(StoryValidationError, match="Claim Scope"):
        build_candidate_story(
            candidate(
                EvidenceAuthority.SELF_AUTHORITATIVE,
                support_scope=current_only,
            ),
            raw_items=[raw()],
            provider=DraftProvider("Example 今天新发布了功能", ClaimType.OTHER),
            now=NOW,
        )

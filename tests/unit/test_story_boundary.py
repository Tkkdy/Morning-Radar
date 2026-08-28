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
from morning_radar.processing.story_builder import (
    StoryValidationError,
    _deterministic_claim_subject,
    _requested_scope,
    build_candidate_story,
    story_evidence_integrity_violations,
)

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
        evidence_ids: list[str] | None = None,
    ):
        self.fact = fact
        self.claim_type = claim_type
        self.scope_supported = scope_supported
        self.requested_scope = requested_scope or ClaimScopeDimensions()
        self.evidence_ids = evidence_ids or ["evidence-one"]

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
                    evidence_ids=self.evidence_ids,
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


def test_model_proposed_broad_does_not_expand_unstated_availability() -> None:
    unknown_availability = ClaimScopeDimensions(
        availability=AvailabilityScope.UNKNOWN,
        temporal=TemporalScope.NEWLY_RELEASED,
        assertion=AssertionScope.OFFICIALLY_ANNOUNCED,
    )
    proposed_broad = ClaimScopeDimensions(
        availability=AvailabilityScope.BROAD,
        temporal=TemporalScope.NEWLY_RELEASED,
        assertion=AssertionScope.OFFICIALLY_ANNOUNCED,
    )

    story = build_candidate_story(
        candidate(
            EvidenceAuthority.SELF_AUTHORITATIVE,
            support_scope=unknown_availability,
        ),
        raw_items=[raw()],
        provider=DraftProvider(
            "Example 官方发布了 v0.33.0-rc0 变更日志。",
            ClaimType.OTHER,
            requested_scope=proposed_broad,
        ),
        now=NOW,
    )

    assert story.facts == ["Example 官方发布了 v0.33.0-rc0 变更日志。"]


@pytest.mark.parametrize(
    "proposed_availability",
    [AvailabilityScope.BROAD, AvailabilityScope.SOME_USERS, AvailabilityScope.GA],
)
def test_model_proposal_is_diagnostic_when_fact_has_no_availability_claim(
    proposed_availability: AvailabilityScope,
) -> None:
    requested = _requested_scope(
        "Example 官方发布了变更日志。",
        ClaimType.OTHER,
        ClaimScopeDimensions(availability=proposed_availability),
    )

    assert requested.availability is AvailabilityScope.UNKNOWN


@pytest.mark.parametrize(
    "fact",
    [
        "Example 该功能现已向所有用户开放。",
        "Example 该功能目前仅部分用户可用。",
        "Example 该功能已经 GA 正式可用。",
    ],
)
def test_explicit_availability_claim_rejects_unknown_evidence(fact: str) -> None:
    unknown_availability = ClaimScopeDimensions(
        availability=AvailabilityScope.UNKNOWN,
        temporal=TemporalScope.NEWLY_RELEASED,
        assertion=AssertionScope.OFFICIALLY_ANNOUNCED,
    )

    with pytest.raises(StoryValidationError, match="Claim Scope"):
        build_candidate_story(
            candidate(
                EvidenceAuthority.SELF_AUTHORITATIVE,
                support_scope=unknown_availability,
            ),
            raw_items=[raw()],
            provider=DraftProvider(
                fact,
                ClaimType.OTHER,
                requested_scope=ClaimScopeDimensions(
                    temporal=TemporalScope.NEWLY_RELEASED,
                    assertion=AssertionScope.OFFICIALLY_ANNOUNCED,
                ),
            ),
            now=NOW,
        )


def test_ga_evidence_supports_explicit_ga_claim() -> None:
    story = build_candidate_story(
        candidate(EvidenceAuthority.SELF_AUTHORITATIVE),
        raw_items=[raw()],
        provider=DraftProvider(
            "Example 该功能已经 GA 正式可用。",
            ClaimType.OTHER,
            requested_scope=ClaimScopeDimensions(
                temporal=TemporalScope.NEWLY_RELEASED,
                assertion=AssertionScope.OFFICIALLY_ANNOUNCED,
            ),
        ),
        now=NOW,
    )

    assert story.facts == ["Example 该功能已经 GA 正式可用。"]


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


@pytest.mark.parametrize("fact", ["该版本修复了依赖问题。", "此版本修复了依赖问题。"])
def test_bounded_version_anaphora_uses_one_verified_repository_family(fact: str) -> None:
    aliases = ["pydantic/pydantic-ai", "pydantic-ai", "Pydantic Ai"]
    grounded = candidate(
        EvidenceAuthority.SELF_AUTHORITATIVE,
        authoritative_for=aliases,
    ).model_copy(update={"entity_names": aliases[:2]})

    story = build_candidate_story(
        grounded,
        raw_items=[raw()],
        provider=DraftProvider(fact, ClaimType.OTHER),
        now=NOW,
    )

    assert story.claim_supports[0].claim_subject == "pydantic/pydantic-ai"
    assert story_evidence_integrity_violations(story) == []


def test_bounded_anaphora_rejects_two_authoritative_subject_families() -> None:
    first = candidate(
        EvidenceAuthority.SELF_AUTHORITATIVE,
        authoritative_for=["OpenAI"],
    ).evidence[0]
    second = first.model_copy(
        update={
            "evidence_id": "evidence-two",
            "authoritative_for": ["Anthropic"],
            "subject_entities": ["Anthropic"],
        }
    )
    ambiguous = candidate(EvidenceAuthority.SELF_AUTHORITATIVE).model_copy(
        update={"entity_names": ["OpenAI", "Anthropic"], "evidence": [first, second]}
    )

    with pytest.raises(StoryValidationError, match="Claim subject"):
        build_candidate_story(
            ambiguous,
            raw_items=[raw()],
            provider=DraftProvider(
                "该版本性能提升 100%。",
                ClaimType.OTHER,
                evidence_ids=["evidence-one", "evidence-two"],
            ),
            now=NOW,
        )


def test_unbounded_pronoun_does_not_inherit_evidence_subject() -> None:
    evidence = candidate(
        EvidenceAuthority.SELF_AUTHORITATIVE,
        authoritative_for=["pydantic/pydantic-ai", "pydantic-ai", "Pydantic Ai"],
    ).evidence

    assert (
        _deterministic_claim_subject(
            "它修复了依赖问题。",
            candidate_entities=[],
            evidence=evidence,
        )
        is None
    )


def test_discovery_only_evidence_cannot_resolve_bounded_anaphora() -> None:
    evidence = candidate(EvidenceAuthority.DISCOVERY_ONLY).evidence

    assert (
        _deterministic_claim_subject(
            "该版本修复了依赖问题。",
            candidate_entities=[],
            evidence=evidence,
        )
        is None
    )


def test_independent_reporting_subject_ambiguity_remains_fail_closed() -> None:
    evidence = candidate(EvidenceAuthority.INDEPENDENT_REPORTING).evidence[0].model_copy(
        update={"subject_entities": ["OpenAI", "Anthropic"]}
    )

    assert (
        _deterministic_claim_subject(
            "该模型性能提升 100%。",
            candidate_entities=[],
            evidence=[evidence],
        )
        is None
    )


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

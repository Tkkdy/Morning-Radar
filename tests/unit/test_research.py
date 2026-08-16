from datetime import UTC, datetime

from morning_radar.ai import AIOutputError, FakeAIProvider
from morning_radar.ai.models import ResearchResolutionBatch, ResearchResolutionDraft
from morning_radar.models import (
    PracticeSignalKind,
    RawItem,
    ResearchDisposition,
    SourceRole,
    StatementType,
)
from morning_radar.research.engine import (
    build_research_cases,
    eligible_story_inputs,
    resolve_research,
)

NOW = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)


def item(
    item_id: str,
    *,
    role: SourceRole,
    url: str = "https://example.com/codex-change",
    title: str = "Codex update breaks an established terminal workflow",
) -> RawItem:
    return RawItem(
        id=item_id,
        title=title,
        url=url,
        source_name=item_id,
        source_type="fixture",
        fetched_at=NOW,
        source_role=role,
        statement_type=(
            StatementType.FIRSTHAND_OBSERVATION
            if role is SourceRole.PRACTITIONER
            else StatementType.FACTUAL_ANNOUNCEMENT
        ),
        practice_signal_kind=PracticeSignalKind.FAILURE_CASE,
        topic_candidates=["ai_coding"],
        company_candidates=["OpenAI"],
        product_candidates=["Codex"],
    )


def test_trusted_practitioner_concrete_observation_becomes_research_case() -> None:
    lead = item("practitioner", role=SourceRole.PRACTITIONER)

    [case] = build_research_cases([lead], maximum_cases=8)

    assert case.lead.raw_item_id == lead.id
    assert case.statement_type is StatementType.FIRSTHAND_OBSERVATION
    assert case.practice_signal_kind is PracticeSignalKind.FAILURE_CASE


def test_independent_support_promotes_verified_story_candidate() -> None:
    lead = item("practitioner", role=SourceRole.PRACTITIONER)
    official = item("official", role=SourceRole.OFFICIAL_PRIMARY)

    result = resolve_research(
        [lead, official],
        provider=FakeAIProvider(),
        maximum_cases=8,
        maximum_radar_signals=3,
    )

    assert result.verified_item_ids == {lead.id}
    assert result.radar_signals == []
    assert lead in eligible_story_inputs(
        [lead, official], verified_item_ids=result.verified_item_ids
    )


def test_valuable_unverified_observation_becomes_bounded_radar_signal() -> None:
    lead = item("practitioner", role=SourceRole.PRACTITIONER)

    result = resolve_research(
        [lead],
        provider=FakeAIProvider(),
        maximum_cases=8,
        maximum_radar_signals=3,
    )

    [signal] = result.radar_signals
    assert signal.claim == lead.title
    assert signal.support_refs[0].raw_item_id == lead.id
    assert result.verified_item_ids == set()


def test_aihot_summary_alone_cannot_enter_story_inputs() -> None:
    lead = item("aihot", role=SourceRole.UPSTREAM_DISCOVERY)

    result = resolve_research(
        [lead],
        provider=FakeAIProvider(),
        maximum_cases=8,
        maximum_radar_signals=3,
    )

    assert result.verified_item_ids == set()
    assert eligible_story_inputs([lead], verified_item_ids=frozenset()) == []


def test_grok_like_agent_discovery_with_primary_support_has_story_opportunity() -> None:
    lead = item(
        "grok-discovery",
        role=SourceRole.UPSTREAM_DISCOVERY,
        title="Grok introduces an AI teammate that performs multi-step work",
    )
    official = item(
        "grok-primary",
        role=SourceRole.OFFICIAL_PRIMARY,
        title="Grok introduces an AI teammate that performs multi-step work",
    )

    result = resolve_research(
        [lead, official],
        provider=FakeAIProvider(),
        maximum_cases=8,
        maximum_radar_signals=3,
    )
    story_inputs = eligible_story_inputs(
        [lead, official], verified_item_ids=result.verified_item_ids
    )

    assert result.verified_item_ids == {lead.id}
    assert story_inputs == [official]


def test_vague_ambient_praise_does_not_become_research_case() -> None:
    praise = RawItem(
        id="praise",
        title="第一次用 DeepSeek API，太牛了",
        url="https://example.com/praise",
        source_name="community",
        source_type="hacker_news",
        fetched_at=NOW,
        source_role=SourceRole.COMMUNITY_DISCOVERY,
        metadata={"selection_reason": "high_signal_discovery"},
    )

    assert build_research_cases([praise], maximum_cases=8) == []


def test_product_owner_marketing_claim_is_not_promoted_to_fact_by_identity() -> None:
    marketing = item(
        "product-owner",
        role=SourceRole.PRACTITIONER,
        title="Our coding agent is the best product in the world",
    ).model_copy(update={"statement_type": StatementType.MARKETING_SELF_PROMOTION})

    result = resolve_research(
        [marketing],
        provider=FakeAIProvider(),
        maximum_cases=8,
        maximum_radar_signals=3,
    )

    assert result.verified_item_ids == set()
    assert eligible_story_inputs([marketing], verified_item_ids=frozenset()) == []


class FailingResearchProvider(FakeAIProvider):
    def resolve_research_cases(self, cases):
        raise AIOutputError("unavailable")


class CountingResearchProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.calls = 0

    def resolve_research_cases(self, cases):
        self.calls += 1
        return super().resolve_research_cases(cases)


class SemanticScopeProvider(FakeAIProvider):
    def __init__(self, *, in_scope: bool) -> None:
        self.in_scope = in_scope
        self.calls = 0

    def resolve_research_cases(self, cases):
        self.calls += 1
        return ResearchResolutionBatch(
            cases=[
                ResearchResolutionDraft(
                    case_id=case.id,
                    in_scope=self.in_scope,
                    scope_rationale=(
                        "该观察直接涉及 AI 模型或产品行为。"
                        if self.in_scope
                        else "该功能没有直接、实质的 AI 关联。"
                    ),
                    disposition=(
                        ResearchDisposition.RADAR_SIGNAL
                        if self.in_scope
                        else ResearchDisposition.INTERNAL_ONLY
                    ),
                    statement_type=case.statement_type,
                    practice_signal_kind=case.practice_signal_kind,
                    claim=case.claim,
                    why_notable="该观察可能影响 AI 开发者实践。",
                    missing_evidence=["独立复现"],
                    uncertainty="当前仍需验证。",
                )
                for case in cases
            ]
        )


def test_firefox_ad_blocker_is_resolved_but_cannot_materialize_radar_signal() -> None:
    lead = item(
        "firefox-ad-blocker",
        role=SourceRole.UPSTREAM_DISCOVERY,
        title="Firefox for iOS now includes a built-in native ad blocker",
    ).model_copy(
        update={
            "topic_candidates": [],
            "company_candidates": ["Mozilla"],
            "product_candidates": ["Firefox"],
            "practice_signal_kind": None,
        }
    )
    provider = SemanticScopeProvider(in_scope=False)

    result = resolve_research(
        [lead],
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
    )

    assert len(result.cases) == 1
    assert provider.calls == 1
    assert result.radar_signals == []
    assert result.verified_item_ids == set()


def test_generic_browser_feature_is_not_a_radar_signal() -> None:
    lead = item(
        "browser-tabs",
        role=SourceRole.PRACTITIONER,
        title="Mobile browser adds a new tab organization feature",
    ).model_copy(
        update={
            "topic_candidates": [],
            "company_candidates": [],
            "product_candidates": ["Browser"],
            "practice_signal_kind": None,
        }
    )

    result = resolve_research(
        [lead],
        provider=SemanticScopeProvider(in_scope=False),
        maximum_cases=8,
        maximum_radar_signals=3,
    )

    assert result.radar_signals == []


def test_sparse_deepseek_behavior_report_survives_research_scope_resolution() -> None:
    lead = RawItem(
        id="deepseek-sparse",
        title="DeepSeek model behavior changed under long coding sessions",
        summary="A reproducible report compares output behavior before and after the update.",
        url="https://example.com/deepseek-behavior",
        source_name="community",
        source_type="hacker_news",
        fetched_at=NOW,
        source_role=SourceRole.COMMUNITY_DISCOVERY,
        statement_type=StatementType.FIRSTHAND_OBSERVATION,
        metadata={"selection_reason": "high_signal_discovery", "score": 420},
    )

    [case] = build_research_cases([lead], maximum_cases=8)
    assert case.topic_keys == []

    result = resolve_research(
        [lead],
        provider=SemanticScopeProvider(in_scope=True),
        maximum_cases=8,
        maximum_radar_signals=3,
    )

    assert result.radar_signals[0].claim == lead.title


def test_codex_workflow_and_material_ai_business_changes_are_in_scope() -> None:
    leads = [
        item("codex-workflow", role=SourceRole.PRACTITIONER),
        item(
            "ai-business",
            role=SourceRole.UPSTREAM_DISCOVERY,
            title="AI company changes model access and commercial terms",
            url="https://example.com/ai-business",
        ),
    ]
    provider = SemanticScopeProvider(in_scope=True)

    result = resolve_research(
        leads,
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
    )

    assert provider.calls == 1
    assert len(result.radar_signals) == 2


def test_research_failure_omits_unverified_signal_without_breaking_story_inputs() -> None:
    lead = item("practitioner", role=SourceRole.PRACTITIONER)
    ordinary = item("official", role=SourceRole.OFFICIAL_PRIMARY, url="https://official.test")

    result = resolve_research(
        [lead, ordinary],
        provider=FailingResearchProvider(),
        maximum_cases=8,
        maximum_radar_signals=3,
    )

    assert result.radar_signals == []
    assert eligible_story_inputs(
        [lead, ordinary], verified_item_ids=result.verified_item_ids
    ) == [ordinary]


def test_research_uses_one_batched_logical_call_for_many_cases() -> None:
    provider = CountingResearchProvider()
    leads = [
        item(
            f"lead-{index}",
            role=SourceRole.PRACTITIONER,
            url=f"https://example.com/{index}",
        )
        for index in range(6)
    ]

    result = resolve_research(
        leads,
        provider=provider,
        maximum_cases=6,
        maximum_radar_signals=3,
    )

    assert len(result.cases) == 6
    assert provider.calls == 1
    assert len(result.radar_signals) == 3


def test_research_context_is_trimmed_or_skipped_under_its_own_character_cap() -> None:
    provider = CountingResearchProvider()
    lead = item("large-lead", role=SourceRole.PRACTITIONER).model_copy(
        update={"title": "Concrete workflow change " + "x" * 480}
    )

    result = resolve_research(
        [lead],
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        maximum_input_characters=100,
    )

    assert provider.calls == 0
    assert result.stats["research_budget_skipped"] is True

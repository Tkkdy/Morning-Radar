"""Migrated regression coverage for the unified Candidate/Radar lifecycle."""

from datetime import UTC, datetime

from morning_radar.ai import FakeAIProvider
from morning_radar.ai.models import CandidateTriageBatch, CandidateTriageDraft
from morning_radar.candidates import (
    admit_candidates,
    radar_signals_from_candidates,
    triage_candidates,
)
from morning_radar.models import (
    CandidateReasonCode,
    EvidenceState,
    ExecutionState,
    PracticeSignalKind,
    RawItem,
    SemanticDisposition,
    SourceRole,
    StatementType,
)

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def item(
    item_id: str,
    *,
    role: SourceRole,
    title: str = "Developer observed a concrete AI coding workflow change",
) -> RawItem:
    return RawItem(
        id=item_id,
        title=title,
        summary="A concrete report includes endpoint, response, and reproducible steps.",
        url=f"https://example.com/{item_id}",
        source_name="Example",
        source_type="rss",
        fetched_at=NOW,
        source_role=role,
        statement_type=StatementType.FIRSTHAND_OBSERVATION,
        practice_signal_kind=PracticeSignalKind.WORKFLOW_CHANGE,
    )


def test_practitioner_and_discovery_leads_share_one_candidate_model() -> None:
    candidates = admit_candidates(
        [
            item("practitioner", role=SourceRole.PRACTITIONER),
            item("discovery", role=SourceRole.UPSTREAM_DISCOVERY),
        ],
        now=NOW,
    )

    assert len(candidates) == 1
    assert set(candidates[0].raw_item_ids) == {"practitioner", "discovery"}
    assert candidates[0].evidence


class InvestigateProvider(FakeAIProvider):
    def triage_candidates(self, candidates):
        return CandidateTriageBatch(
            candidates=[
                CandidateTriageDraft(
                    candidate_id=candidate.id,
                    hypothesis=candidate.hypothesis,
                    potential_impact="若成立，将改变 AI 开发者的编码工作流。",
                    semantic_disposition=SemanticDisposition.INVESTIGATE,
                    evidence_state=EvidenceState.PARTIAL,
                    reason_codes=[CandidateReasonCode.POTENTIAL_WORKFLOW_CHANGE],
                    missing_evidence=["独立复现或官方能力说明"],
                    verification_target="现有 endpoint 或官方文档",
                    verification_path="对现有 destination URL 做 bounded fetch",
                    investigation_priority=0.8,
                )
                for candidate in candidates
            ]
        )


def test_unresolved_candidate_projects_to_radar_signal_without_story_fact() -> None:
    admitted = admit_candidates(
        [item("lead", role=SourceRole.PRACTITIONER)], now=NOW
    )
    candidates = triage_candidates(
        admitted,
        provider=InvestigateProvider(),
        maximum_batch_items=40,
        maximum_input_characters=20_000,
    ).candidates

    [signal] = radar_signals_from_candidates(candidates, maximum_signals=3)

    assert signal.claim == candidates[0].hypothesis
    assert signal.missing_evidence
    assert candidates[0].semantic_disposition is SemanticDisposition.INVESTIGATE


def test_budget_deferred_investigation_remains_a_semantic_investigation() -> None:
    admitted = admit_candidates(
        [item("lead", role=SourceRole.PRACTITIONER)], now=NOW
    )
    [candidate] = triage_candidates(
        admitted,
        provider=InvestigateProvider(),
        maximum_batch_items=40,
        maximum_input_characters=20_000,
    ).candidates
    deferred = candidate.model_copy(
        update={"execution_state": ExecutionState.DEFERRED_BY_BUDGET}
    )

    assert deferred.semantic_disposition is SemanticDisposition.INVESTIGATE
    assert deferred.execution_state is ExecutionState.DEFERRED_BY_BUDGET


def test_sparse_deepseek_behavior_report_gets_semantic_triage() -> None:
    lead = item(
        "deepseek-sparse",
        role=SourceRole.COMMUNITY_DISCOVERY,
        title="DeepSeek model behavior changed under long coding sessions",
    ).model_copy(
        update={"metadata": {"selection_reason": "high_signal_discovery", "score": 420}}
    )
    provider = InvestigateProvider()

    result = triage_candidates(
        admit_candidates([lead], now=NOW),
        provider=provider,
        maximum_batch_items=40,
        maximum_input_characters=20_000,
    )

    assert result.stats["candidate_triaged"] == 1
    assert result.candidates[0].semantic_disposition is SemanticDisposition.INVESTIGATE

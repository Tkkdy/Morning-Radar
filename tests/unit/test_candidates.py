from datetime import UTC, datetime

from morning_radar.ai import AIBudgetExceeded, FakeAIProvider
from morning_radar.ai.models import CandidateTriageBatch, CandidateTriageDraft
from morning_radar.candidates import admit_candidates, triage_candidates
from morning_radar.models import (
    CandidateReasonCode,
    EvidenceAuthority,
    EvidenceState,
    ExecutionState,
    RawItem,
    SemanticDisposition,
    SourceRole,
    StatementType,
)

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def item(item_id: str, title: str, *, score: int = 0) -> RawItem:
    return RawItem(
        id=item_id,
        title=title,
        url=f"https://example.com/{item_id}",
        source_name="Hacker News",
        source_type="hacker_news",
        fetched_at=NOW,
        source_role=SourceRole.COMMUNITY_DISCOVERY,
        statement_type=StatementType.UNVERIFIED_LEAD,
        metadata={"score": score, "comments": 100 if score else 0},
    )


def test_high_signal_capability_candidate_is_admitted_and_must_triage() -> None:
    [candidate] = admit_candidates(
        [item("deep-model", "New model API now supports vision", score=448)],
        now=NOW,
    )

    assert candidate.must_triage is True
    assert CandidateReasonCode.HIGH_RECALL_GUARDRAIL in candidate.reason_codes
    assert candidate.semantic_disposition is None


def test_fake_triage_builds_every_admitted_candidate_without_story_cap() -> None:
    candidates = admit_candidates(
        [item(f"item-{index}", f"AI model endpoint {index}") for index in range(33)],
        now=NOW,
    )

    result = triage_candidates(
        candidates,
        provider=FakeAIProvider(),
        maximum_batch_items=40,
        maximum_input_characters=200_000,
    )

    assert result.stats["candidate_triaged"] == 33
    assert all(
        candidate.semantic_disposition is SemanticDisposition.BUILD
        for candidate in result.candidates
    )


class InvestigateProvider(FakeAIProvider):
    def triage_candidates(self, candidates):
        return CandidateTriageBatch(
            candidates=[
                CandidateTriageDraft(
                    candidate_id=candidate.id,
                    hypothesis=candidate.hypothesis,
                    semantic_disposition=SemanticDisposition.INVESTIGATE,
                    evidence_state=EvidenceState.PARTIAL,
                    reason_codes=[CandidateReasonCode.POTENTIAL_CAPABILITY_CHANGE],
                    missing_evidence=["是否正式可用"],
                    verification_target="官方 API 文档",
                    verification_path="检查现有 destination URL",
                    investigation_priority=0.9,
                )
                for candidate in candidates
            ]
        )


def test_investigate_keeps_semantic_and_execution_state_separate() -> None:
    candidates = admit_candidates([item("lead", "AI API preview")], now=NOW)

    [resolved] = triage_candidates(
        candidates,
        provider=InvestigateProvider(),
        maximum_batch_items=40,
        maximum_input_characters=100_000,
    ).candidates

    assert resolved.semantic_disposition is SemanticDisposition.INVESTIGATE
    assert resolved.execution_state is ExecutionState.NOT_STARTED


def test_character_budget_defer_is_not_drop() -> None:
    candidates = admit_candidates([item("lead", "AI API preview")], now=NOW)

    [deferred] = triage_candidates(
        candidates,
        provider=FakeAIProvider(),
        maximum_batch_items=40,
        maximum_input_characters=1,
    ).candidates

    assert deferred.semantic_disposition is None
    assert deferred.execution_state is ExecutionState.DEFERRED_BY_BUDGET


class ExhaustedProvider(FakeAIProvider):
    def triage_candidates(self, candidates):
        raise AIBudgetExceeded("reserved for later stages")


def test_shared_budget_exhaustion_is_deferred_not_failed_ai() -> None:
    candidates = admit_candidates(
        [item("one", "AI model release"), item("two", "AI agent release")],
        now=NOW,
    )

    result = triage_candidates(
        candidates,
        provider=ExhaustedProvider(),
        maximum_batch_items=1,
        maximum_input_characters=100_000,
    )

    assert result.stats["candidate_triage_failed"] == 0
    assert result.stats["candidate_triage_deferred"] == 2
    assert all(
        candidate.execution_state is ExecutionState.DEFERRED_BY_BUDGET
        for candidate in result.candidates
    )


def test_github_authority_is_limited_to_verified_repository_scope() -> None:
    repository_release = RawItem(
        id="repo-release",
        title="Example v1 released",
        url="https://github.com/example/project/releases/tag/v1",
        source_name="example/project",
        source_type="github",
        fetched_at=NOW,
        source_role=SourceRole.OFFICIAL_PRIMARY,
        metadata={"repository": "example/project", "official": True},
    )
    github_root = repository_release.model_copy(
        update={"id": "root", "url": "https://github.com/"}
    )

    [repo_candidate] = admit_candidates([repository_release], now=NOW)
    [root_candidate] = admit_candidates([github_root], now=NOW)

    assert repo_candidate.evidence[0].authority is EvidenceAuthority.SELF_AUTHORITATIVE
    assert repo_candidate.evidence[0].authoritative_for == ["example/project"]
    assert root_candidate.evidence[0].authority is EvidenceAuthority.DISCOVERY_ONLY

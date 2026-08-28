from datetime import UTC, datetime

import pytest

from morning_radar.ai import AIBudgetExceeded, FakeAIProvider
from morning_radar.ai.models import CandidateTriageBatch, CandidateTriageDraft
from morning_radar.candidates import admit_candidates, triage_candidates
from morning_radar.models import (
    AssertionScope,
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


def test_fake_triage_downgrades_empty_discovery_evidence_without_story_cap() -> None:
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
        candidate.semantic_disposition is SemanticDisposition.INVESTIGATE
        for candidate in result.candidates
    )
    assert all(
        CandidateReasonCode.BUILD_DOWNGRADED_EVIDENCE_INSUFFICIENT
        in candidate.reason_codes
        for candidate in result.candidates
    )


@pytest.mark.parametrize(
    ("title", "url"),
    [
        (
            "Felony charges for citizen deleting phone data at US Border",
            "https://www.nytimes.com/example",
        ),
        (
            "Kagi added a setting for removing paywalled links",
            "https://kagi.com/changelog#example",
        ),
        (
            "Meta AI glasses may get creepier",
            "https://arstechnica.com/example",
        ),
    ],
)
def test_destination_fame_does_not_close_empty_hn_evidence(
    title: str, url: str
) -> None:
    lead = item("lead", title, score=500).model_copy(update={"url": url})

    [candidate] = triage_candidates(
        admit_candidates([lead], now=NOW),
        provider=FakeAIProvider(),
        maximum_batch_items=40,
        maximum_input_characters=100_000,
    ).candidates

    assert candidate.semantic_disposition is SemanticDisposition.INVESTIGATE
    assert candidate.evidence_state is EvidenceState.INSUFFICIENT
    assert (
        CandidateReasonCode.BUILD_DOWNGRADED_EVIDENCE_INSUFFICIENT
        in candidate.reason_codes
    )


@pytest.mark.parametrize(
    "raw_item",
    [
        RawItem(
            id="release",
            title="example/project v1 released",
            url="https://github.com/example/project/releases/tag/v1",
            source_name="GitHub · example/project",
            source_type="github",
            fetched_at=NOW,
            source_role=SourceRole.OFFICIAL_PRIMARY,
            statement_type=StatementType.FACTUAL_ANNOUNCEMENT,
            content_excerpt="Version v1 fixes the documented client compatibility issue.",
            repository_candidates=["example/project"],
            metadata={"repository": "example/project", "official": True},
        ),
        RawItem(
            id="official-blog",
            title="Example launches a developer feature",
            url="https://blog.example.com/feature",
            source_name="Example Blog",
            source_type="rss",
            fetched_at=NOW,
            source_role=SourceRole.OFFICIAL_PRIMARY,
            statement_type=StatementType.FACTUAL_ANNOUNCEMENT,
            content_excerpt="Example launched a documented developer feature today.",
            metadata={"official": True, "entity": "Example"},
        ),
        RawItem(
            id="report",
            title="Example reports a narrow product change",
            url="https://publisher.example/report",
            source_name="Independent Publisher",
            source_type="rss",
            fetched_at=NOW,
            source_role=SourceRole.EDITORIAL,
            content_excerpt="Example changed the documented API response field.",
            company_candidates=["Example"],
        ),
    ],
)
def test_supported_evidence_allows_model_build(raw_item: RawItem) -> None:
    [candidate] = triage_candidates(
        admit_candidates([raw_item], now=NOW),
        provider=FakeAIProvider(),
        maximum_batch_items=40,
        maximum_input_characters=100_000,
    ).candidates

    assert candidate.semantic_disposition is SemanticDisposition.BUILD


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
    assert repo_candidate.evidence[0].authoritative_for == [
        "example/project",
        "project",
        "Project",
    ]
    assert repo_candidate.entity_names == ["example/project", "project"]
    assert root_candidate.evidence[0].authority is EvidenceAuthority.DISCOVERY_ONLY


@pytest.mark.parametrize(
    ("repository", "expected_alias"),
    [("pydantic/pydantic-ai", "pydantic-ai"), ("ollama/ollama", "ollama")],
)
def test_verified_repository_identity_has_deterministic_product_alias(
    repository: str, expected_alias: str
) -> None:
    release = RawItem(
        id=repository,
        title=f"{repository} release",
        url=f"https://github.com/{repository}/releases/tag/v1",
        source_name=f"GitHub · {repository}",
        source_type="github",
        fetched_at=NOW,
        source_role=SourceRole.OFFICIAL_PRIMARY,
        content_excerpt="Release notes with concrete fixes.",
        repository_candidates=[repository],
        metadata={"repository": repository, "official": True},
    )

    [candidate] = admit_candidates([release], now=NOW)

    assert candidate.evidence[0].authoritative_for[0] == repository
    assert expected_alias in candidate.evidence[0].authoritative_for[1:]
    assert expected_alias in candidate.entity_names


def test_verified_official_surface_backfills_authoritative_entity() -> None:
    announcement = RawItem(
        id="cloudflare",
        title="Cloudflare launches Bot Preference Sync",
        url="https://blog.cloudflare.com/bot-preference-sync/",
        source_name="Cloudflare Blog",
        source_type="rss",
        fetched_at=NOW,
        source_role=SourceRole.OFFICIAL_PRIMARY,
        content_excerpt="Cloudflare launched Bot Preference Sync.",
        metadata={"official": True, "entity": "Cloudflare"},
    )

    [candidate] = admit_candidates([announcement], now=NOW)
    evidence = candidate.evidence[0]

    assert evidence.publisher == "Cloudflare Blog"
    assert evidence.authoritative_for == ["Cloudflare"]
    assert candidate.entity_names == ["Cloudflare"]


def test_discovery_only_candidate_cannot_backfill_entity_from_destination() -> None:
    lead = item("lead", "GitHub launches something", score=500).model_copy(
        update={"url": "https://github.blog/changelog/example"}
    )

    [candidate] = admit_candidates([lead], now=NOW)

    assert candidate.evidence[0].authority is EvidenceAuthority.DISCOVERY_ONLY
    assert candidate.evidence[0].authoritative_for == []
    assert candidate.entity_names == []


def test_independent_reporting_is_not_independent_verification() -> None:
    report = RawItem(
        id="report",
        title="Example capability report",
        url="https://publisher.example/report",
        source_name="Independent Publisher",
        source_type="rss",
        fetched_at=NOW,
        source_role=SourceRole.EDITORIAL,
        company_candidates=["Example"],
    )

    [candidate] = admit_candidates([report], now=NOW)
    evidence = candidate.evidence[0]

    assert evidence.authority is EvidenceAuthority.INDEPENDENT_REPORTING
    assert evidence.support_scope.assertion is AssertionScope.UNKNOWN

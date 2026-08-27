from datetime import UTC, datetime

import pytest

from morning_radar.ai import FakeAIProvider
from morning_radar.ai.models import CandidateTriageBatch, CandidateTriageDraft
from morning_radar.candidates import admit_candidates, triage_candidates
from morning_radar.evidence import (
    EvidenceFetchError,
    EvidenceFetchResult,
    OfficialSurfaceResolver,
    resolve_evidence,
)
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
from morning_radar.processing.story_builder import build_candidate_story

NOW = datetime(2026, 8, 22, tzinfo=UTC)
DESTINATION = "https://api-docs.deepseek.com/guides/vision/"
DISCUSSION = "https://news.ycombinator.com/item?id=49386163"


def raw() -> RawItem:
    return RawItem(
        id="deepseek-vision",
        title="DeepSeek-v4-flash-vision-exp",
        url=DESTINATION,
        source_name="Hacker News",
        source_type="hacker_news",
        published_at=NOW,
        fetched_at=NOW,
        source_role=SourceRole.COMMUNITY_DISCOVERY,
        statement_type=StatementType.UNVERIFIED_LEAD,
        metadata={
            "score": 448,
            "comments": 141,
            "selection_reason": "high_signal_discovery",
            "discussion_url": DISCUSSION,
        },
    )


class InvestigateProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.rounds = 0

    def triage_candidates(self, candidates):
        self.rounds += 1
        if self.rounds > 1:
            return super().triage_candidates(candidates)
        return CandidateTriageBatch(
            candidates=[
                CandidateTriageDraft(
                    candidate_id=candidate.id,
                    hypothesis="DeepSeek 可能新增 Vision API 能力",
                    potential_impact="若成立，将扩大开发者可用的多模态能力。",
                    semantic_disposition=SemanticDisposition.INVESTIGATE,
                    evidence_state=EvidenceState.PARTIAL,
                    reason_codes=[CandidateReasonCode.POTENTIAL_CAPABILITY_CHANGE],
                    missing_evidence=["官方文档是否明确支持 image input"],
                    verification_target="官方 API 文档",
                    verification_path="读取现有 destination URL",
                    investigation_priority=0.9,
                )
                for candidate in candidates
            ]
        )


class Fetcher:
    def fetch(self, url: str) -> EvidenceFetchResult:
        assert url == DESTINATION
        return EvidenceFetchResult(
            requested_url=url,
            final_url=url,
            content_type="text/html",
            text="DeepSeek Vision API supports image input.",
            canonical_url=url,
            redirect_chain=(),
            response_bytes=100,
        )


class FailingFetcher:
    def fetch(self, url: str):
        raise EvidenceFetchError("NETWORK_FAILED")


def investigated_candidate(provider: InvestigateProvider):
    return triage_candidates(
        admit_candidates([raw()], now=NOW),
        provider=provider,
        maximum_batch_items=40,
        maximum_input_characters=20_000,
    ).candidates


def test_hn_discovery_and_official_evidence_provenance_remain_separate(tmp_path) -> None:
    provider = InvestigateProvider()
    result = resolve_evidence(
        investigated_candidate(provider),
        provider=provider,
        fetcher=Fetcher(),
        official_resolver=OfficialSurfaceResolver(
            cache_path=tmp_path / "trust.json",
            seeds={"deepseek.com": "DeepSeek"},
            now=NOW,
        ),
        now=NOW,
        maximum_investigations=1,
        maximum_triage_input_characters=20_000,
    )
    [candidate] = result.candidates
    story = build_candidate_story(
        candidate,
        raw_items=[raw()],
        provider=FakeAIProvider(),
        now=NOW,
    )

    assert story.source_refs[0].source_name == "Hacker News"
    assert story.source_refs[0].discussion_url == DISCUSSION
    official = [item for item in story.evidence_refs if item.official_surface_verified]
    assert official[0].publisher == "DeepSeek"
    assert story.claim_supports[0].evidence_ids == [official[0].evidence_id]


def test_network_failure_preserves_investigate_semantics(tmp_path) -> None:
    provider = InvestigateProvider()
    [candidate] = resolve_evidence(
        investigated_candidate(provider),
        provider=provider,
        fetcher=FailingFetcher(),
        official_resolver=OfficialSurfaceResolver(
            cache_path=tmp_path / "trust.json", seeds={}, now=NOW
        ),
        now=NOW,
        maximum_investigations=1,
        maximum_triage_input_characters=20_000,
    ).candidates

    assert candidate.semantic_disposition is SemanticDisposition.INVESTIGATE
    assert candidate.execution_state is ExecutionState.FAILED_NETWORK


def test_unknown_fetched_url_remains_unverified_and_cannot_support_story(tmp_path) -> None:
    provider = InvestigateProvider()
    result = resolve_evidence(
        investigated_candidate(provider),
        provider=provider,
        fetcher=Fetcher(),
        official_resolver=OfficialSurfaceResolver(
            cache_path=tmp_path / "trust.json", seeds={}, now=NOW
        ),
        now=NOW,
        maximum_investigations=1,
        maximum_triage_input_characters=20_000,
    )
    fetched = [
        evidence
        for evidence in result.candidates[0].evidence
        if evidence.evidence_id.startswith("evidence-fetch-")
    ]

    assert fetched[0].authority is EvidenceAuthority.UNVERIFIED_EXTERNAL
    with pytest.raises(ValueError, match="Claim"):
        build_candidate_story(
            result.candidates[0],
            raw_items=[raw()],
            provider=FakeAIProvider(),
            now=NOW,
        )

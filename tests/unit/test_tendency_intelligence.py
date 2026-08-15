from datetime import UTC, date, datetime

from morning_radar.ai import AIOutputError, FakeAIProvider
from morning_radar.ai.models import TendencyDecisionDraft, TendencyEvaluationBatch
from morning_radar.continuity.candidates import StoryMemory
from morning_radar.models import (
    DailyContinuity,
    SourceRole,
    Story,
    StoryEvidenceRef,
    StoryOccurrenceRef,
    StoryRelationRecord,
    StoryRelationType,
    StorySourceRef,
    TendencyAssessment,
    TendencyStanding,
    TendencyUpdateKind,
)
from morning_radar.tendencies.clusters import build_evidence_clusters
from morning_radar.tendencies.engine import evaluate_daily_tendencies

NOW = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)


def memory(
    day: date,
    story_id: str,
    actor: str,
    *,
    title: str | None = None,
    source_count: int = 1,
) -> StoryMemory:
    urls = [f"https://{story_id}.example/source-{index}" for index in range(source_count)]
    item_ids = [f"{story_id}-item-{index}" for index in range(source_count)]
    refs = [
        StorySourceRef(
            raw_item_id=item_id,
            title=title or f"{actor} embeds an agent into a workflow",
            source_name=f"source-{index}",
            source_type="rss",
            url=url,
            fetched_at=NOW,
            source_role=SourceRole.OFFICIAL_PRIMARY,
        )
        for index, (item_id, url) in enumerate(zip(item_ids, urls, strict=True))
    ]
    story = Story(
        id=story_id,
        canonical_title=title or f"{actor} embeds an agent into a workflow",
        category="ai_and_open_source",
        entity_names=[actor],
        topic_names=["agents"],
        updated_at=NOW,
        source_item_ids=item_ids,
        source_urls=urls,
        primary_source_url=urls[0],
        source_refs=refs,
        facts=[f"{actor} shipped an observable workflow integration."],
        relevance_score=0.9,
        importance_score=0.8,
        novelty_score=0.8,
        credibility_score=0.9,
    )
    return StoryMemory(ref=StoryOccurrenceRef(date=day, story_id=story_id), story=story)


def assessment(**updates) -> TendencyAssessment:
    values = {
        "shared_mechanism": "Agents are moving from chat into governed organizational workflows.",
        "baseline": "Previously these products primarily exposed isolated chat interfaces.",
        "falsifier": "Vendors remove workflow access and observed usage returns to isolated chat.",
        "observable_impacts": ["Users can complete a real multi-step workflow."],
        "counterevidence_considered": True,
        "decision_rationale": "Independent actors changed behavior across observation dates.",
    }
    values.update(updates)
    return TendencyAssessment(**values)


class EmergingProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.calls = 0

    def evaluate_tendencies(self, clusters, current_views):
        self.calls += 1
        assert not current_views
        return TendencyEvaluationBatch(
            decisions=[
                TendencyDecisionDraft(
                    standing_after=TendencyStanding.EMERGING,
                    claim="Agent competition is shifting toward real workflow integration.",
                    assessment=assessment(),
                    supporting_cluster_ids=[cluster.cluster_id for cluster in clusters[:3]],
                )
            ]
        )


class EmptyProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.calls = 0

    def evaluate_tendencies(self, clusters, current_views):
        self.calls += 1
        return TendencyEvaluationBatch()


class TwoClusterProvider(FakeAIProvider):
    def __init__(self, *, exception: bool) -> None:
        self.exception = exception

    def evaluate_tendencies(self, clusters, current_views):
        return TendencyEvaluationBatch(
            decisions=[
                TendencyDecisionDraft(
                    standing_after=TendencyStanding.EMERGING,
                    claim="Two unusually strong events expose the same mechanism.",
                    assessment=assessment(
                        formation_exception_rationale=(
                            "Different actors have primary evidence and observable impact."
                            if self.exception
                            else None
                        )
                    ),
                    supporting_cluster_ids=[cluster.cluster_id for cluster in clusters[:2]],
                )
            ]
        )


def three_memories() -> list[StoryMemory]:
    return [
        memory(date(2026, 8, 12), "email-agent", "Alpha"),
        memory(date(2026, 8, 14), "meeting-agent", "Beta"),
        memory(date(2026, 8, 16), "crm-agent", "Gamma"),
    ]


def test_ten_media_reposts_of_one_story_are_one_evidence_cluster() -> None:
    clusters = build_evidence_clusters(
        [memory(date(2026, 8, 16), "launch", "Alpha", source_count=10)], []
    )

    assert len(clusters) == 1
    assert clusters[0].source_count == 10


def test_confirmed_continuity_chain_remains_one_evolving_event_cluster() -> None:
    previous = memory(date(2026, 8, 12), "alpha-preview", "Alpha")
    current = memory(date(2026, 8, 14), "alpha-stable", "Alpha")
    relation = StoryRelationRecord(
        relation_id="relation-alpha",
        recorded_at=datetime(2026, 8, 14, 1, tzinfo=UTC),
        previous_story=previous.ref,
        current_story=current.ref,
        relation_type=StoryRelationType.STATUS_TRANSITION,
        change_summary="Preview became stable.",
        rationale="Same product and explicit version progression.",
        evidence_refs=[
            StoryEvidenceRef(story=previous.ref),
            StoryEvidenceRef(story=current.ref),
        ],
    )
    daily = DailyContinuity(
        date=date(2026, 8, 14),
        generated_at=datetime(2026, 8, 14, 1, tzinfo=UTC),
        relations=[relation],
    )

    clusters = build_evidence_clusters([previous, current], [daily])

    assert len(clusters) == 1
    assert clusters[0].story_refs == [previous.ref, current.ref]

    later = memory(date(2026, 8, 16), "alpha-ga", "Alpha")
    later_relation = relation.model_copy(
        update={
            "relation_id": "relation-alpha-later",
            "recorded_at": NOW,
            "previous_story": current.ref,
            "current_story": later.ref,
            "evidence_refs": [
                StoryEvidenceRef(story=current.ref),
                StoryEvidenceRef(story=later.ref),
            ],
        }
    )
    later_daily = DailyContinuity(
        date=date(2026, 8, 16),
        generated_at=NOW,
        relations=[later_relation],
    )

    [extended] = build_evidence_clusters(
        [previous, current, later], [daily, later_daily]
    )

    assert extended.cluster_id == clusters[0].cluster_id


def test_cross_actor_cross_date_shared_mechanism_can_form_emerging() -> None:
    provider = EmergingProvider()

    result = evaluate_daily_tendencies(
        current_date=date(2026, 8, 16),
        generated_at=NOW,
        story_memory=three_memories(),
        continuities=[],
        history=[],
        provider=provider,
        maximum_clusters=12,
    )

    [record] = result.daily.decisions
    assert record.standing_after is TendencyStanding.EMERGING
    assert record.formed_at == date(2026, 8, 16)
    assert len(record.formation_cluster_ids) == 3
    assert record.policy_version == "tendency_policy_v1"
    assert provider.calls == 1
    assert len(result.brief_tendencies) == 1


def test_two_cluster_formation_requires_explicit_strong_exception() -> None:
    stories = three_memories()[:2]

    rejected = evaluate_daily_tendencies(
        current_date=date(2026, 8, 14),
        generated_at=datetime(2026, 8, 14, 1, tzinfo=UTC),
        story_memory=stories,
        continuities=[],
        history=[],
        provider=TwoClusterProvider(exception=False),
        maximum_clusters=12,
    )
    accepted = evaluate_daily_tendencies(
        current_date=date(2026, 8, 14),
        generated_at=datetime(2026, 8, 14, 1, tzinfo=UTC),
        story_memory=stories,
        continuities=[],
        history=[],
        provider=TwoClusterProvider(exception=True),
        maximum_clusters=12,
    )

    assert rejected.daily.decisions == []
    assert accepted.daily.decisions[0].standing_after is TendencyStanding.EMERGING


def test_same_agent_keyword_without_shared_mechanism_is_not_a_tendency() -> None:
    stories = [
        memory(date(2026, 8, 12), "agent-a", "Alpha", title="Alpha Agent benchmark"),
        memory(date(2026, 8, 14), "agent-b", "Beta", title="Beta Agent email plugin"),
        memory(date(2026, 8, 16), "agent-c", "Gamma", title="Gamma Agent game NPC"),
    ]

    result = evaluate_daily_tendencies(
        current_date=date(2026, 8, 16),
        generated_at=NOW,
        story_memory=stories,
        continuities=[],
        history=[],
        provider=EmptyProvider(),
        maximum_clusters=12,
    )

    assert result.daily.decisions == []


def test_persistent_standing_and_weakened_update_are_independent_dimensions() -> None:
    draft = TendencyDecisionDraft(
        existing_tendency_id="tendency-1",
        standing_after=TendencyStanding.PERSISTENT,
        update_kind=TendencyUpdateKind.WEAKENED,
        claim="The mechanism remains plausible but current evidence is mixed.",
        assessment=assessment(),
        counterevidence_cluster_ids=["cluster-contrary"],
    )

    assert draft.standing_after is TendencyStanding.PERSISTENT
    assert draft.update_kind is TendencyUpdateKind.WEAKENED


class PersistentProvider(FakeAIProvider):
    def __init__(self, *, reuse_formation: bool = False) -> None:
        self.reuse_formation = reuse_formation

    def evaluate_tendencies(self, clusters, current_views):
        [view] = current_views
        cluster_ids = (
            view.formation_cluster_ids
            if self.reuse_formation
            else [
                cluster.cluster_id
                for cluster in clusters
                if cluster.cluster_id not in view.formation_cluster_ids
            ][:1]
        )
        return TendencyEvaluationBatch(
            decisions=[
                TendencyDecisionDraft(
                    existing_tendency_id=view.tendency_id,
                    standing_after=TendencyStanding.PERSISTENT,
                    update_kind=TendencyUpdateKind.SUPPORTED,
                    claim=view.claim,
                    assessment=view.assessment,
                    supporting_cluster_ids=cluster_ids,
                )
            ]
        )


def formed_history():
    formed = evaluate_daily_tendencies(
        current_date=date(2026, 8, 16),
        generated_at=NOW,
        story_memory=three_memories(),
        continuities=[],
        history=[],
        provider=EmergingProvider(),
        maximum_clusters=12,
    )
    return formed.daily


def test_post_formation_new_cluster_can_pass_survival_test() -> None:
    history = formed_history()
    stories = [
        *three_memories(),
        memory(date(2026, 8, 18), "identity-agent", "Delta"),
    ]

    result = evaluate_daily_tendencies(
        current_date=date(2026, 8, 18),
        generated_at=datetime(2026, 8, 18, 1, 0, tzinfo=UTC),
        story_memory=stories,
        continuities=[],
        history=[history],
        provider=PersistentProvider(),
        maximum_clusters=12,
    )

    [record] = result.daily.decisions
    assert record.standing_after is TendencyStanding.PERSISTENT
    assert not set(record.supporting_cluster_ids).intersection(record.formation_cluster_ids)


def test_formation_evidence_cannot_prove_persistent_again() -> None:
    history = formed_history()

    result = evaluate_daily_tendencies(
        current_date=date(2026, 8, 18),
        generated_at=datetime(2026, 8, 18, 1, 0, tzinfo=UTC),
        story_memory=three_memories(),
        continuities=[],
        history=[history],
        provider=PersistentProvider(reuse_formation=True),
        maximum_clusters=12,
    )

    assert result.daily.decisions == []


def test_no_new_evidence_does_not_create_weakened_update() -> None:
    result = evaluate_daily_tendencies(
        current_date=date(2026, 8, 18),
        generated_at=datetime(2026, 8, 18, 1, 0, tzinfo=UTC),
        story_memory=three_memories(),
        continuities=[],
        history=[formed_history()],
        provider=EmptyProvider(),
        maximum_clusters=12,
    )

    assert result.daily.decisions == []
    assert result.current_views[0].standing is TendencyStanding.EMERGING


class ContraryProvider(FakeAIProvider):
    def evaluate_tendencies(self, clusters, current_views):
        [view] = current_views
        contrary = next(
            cluster for cluster in clusters if cluster.cluster_id not in view.formation_cluster_ids
        )
        return TendencyEvaluationBatch(
            decisions=[
                TendencyDecisionDraft(
                    existing_tendency_id=view.tendency_id,
                    standing_after=TendencyStanding.OVERTURNED,
                    update_kind=TendencyUpdateKind.OVERTURNED,
                    claim=view.claim,
                    assessment=assessment(core_claim_invalidated=False),
                    counterevidence_cluster_ids=[contrary.cluster_id],
                )
            ]
        )


def test_one_contrary_fact_does_not_automatically_overturn() -> None:
    stories = [
        *three_memories(),
        memory(date(2026, 8, 18), "contrary", "Delta"),
    ]

    result = evaluate_daily_tendencies(
        current_date=date(2026, 8, 18),
        generated_at=datetime(2026, 8, 18, 1, 0, tzinfo=UTC),
        story_memory=stories,
        continuities=[],
        history=[formed_history()],
        provider=ContraryProvider(),
        maximum_clusters=12,
    )

    assert result.daily.decisions == []


class CandidateProvider(FakeAIProvider):
    def evaluate_tendencies(self, clusters, current_views):
        return TendencyEvaluationBatch(
            decisions=[
                TendencyDecisionDraft(
                    standing_after=TendencyStanding.CANDIDATE,
                    claim="Internal candidate only",
                    assessment=assessment(),
                    supporting_cluster_ids=[cluster.cluster_id for cluster in clusters[:2]],
                )
            ]
        )


def test_candidate_is_persisted_but_not_rendered_publicly() -> None:
    result = evaluate_daily_tendencies(
        current_date=date(2026, 8, 16),
        generated_at=NOW,
        story_memory=three_memories(),
        continuities=[],
        history=[],
        provider=CandidateProvider(),
        maximum_clusters=12,
    )

    assert result.daily.decisions[0].standing_after is TendencyStanding.CANDIDATE
    assert result.brief_tendencies == []


class FailingProvider(FakeAIProvider):
    def evaluate_tendencies(self, clusters, current_views):
        raise AIOutputError("unavailable")


def test_tendency_failure_preserves_prior_current_view() -> None:
    result = evaluate_daily_tendencies(
        current_date=date(2026, 8, 18),
        generated_at=datetime(2026, 8, 18, 1, 0, tzinfo=UTC),
        story_memory=three_memories(),
        continuities=[],
        history=[formed_history()],
        provider=FailingProvider(),
        maximum_clusters=12,
    )

    assert result.daily.decisions == []
    assert result.current_views[0].standing is TendencyStanding.EMERGING
    assert result.stats["tendency_unavailable"] is True


def test_tendency_batch_never_exceeds_its_character_cap() -> None:
    provider = EmptyProvider()

    result = evaluate_daily_tendencies(
        current_date=date(2026, 8, 16),
        generated_at=NOW,
        story_memory=three_memories(),
        continuities=[],
        history=[],
        provider=provider,
        maximum_clusters=12,
        maximum_input_characters=100,
    )

    assert provider.calls == 0
    assert result.stats["tendency_budget_skipped"] is True

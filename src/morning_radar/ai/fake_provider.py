"""Deterministic AI replacement for fixtures and tests."""

from __future__ import annotations

import json
from typing import Any

from morning_radar.ai.models import (
    BriefDraft,
    CandidateTriageBatch,
    CandidateTriageDraft,
    ClassificationBatch,
    ClassifiedItem,
    ContinuityResolution,
    ContinuityResolutionInput,
    DirectionObservation,
    DraftClaimSupport,
    GeneratedBriefItem,
    GeneratedWatchDraft,
    MergedStoryDraft,
    ResolvedRelationDraft,
    ResolvedWatchMatchDraft,
    StoryScore,
    TendencyDecisionDraft,
    TendencyEvaluationBatch,
    TendencyFormationSupportDraft,
)
from morning_radar.editorial.models import (
    DecisionReason,
    EditorialDecision,
    EditorialDecisionBatch,
    FactStatus,
    Placement,
    Treatment,
)
from morning_radar.models import (
    Candidate,
    CandidateReasonCode,
    ClaimType,
    EvidenceAuthority,
    EvidenceState,
    RawItem,
    SemanticDisposition,
    Signal,
    SourceRole,
    Story,
    StoryEvidenceRef,
    StoryRelationType,
    TendencyAssessment,
    TendencyCurrentView,
    TendencyEvidenceCluster,
    TendencyStanding,
    TendencyUpdateKind,
)


class FakeAIProvider:
    def __init__(self, *, budget: Any | None = None) -> None:
        self.budget = budget
        self._account_fake_calls = budget is not None

    def _consume(self, task: str, payload: Any, item_count: int) -> None:
        budget = getattr(self, "budget", None)
        if budget is None or not getattr(self, "_account_fake_calls", False):
            return
        stages = {
            "candidate_triage": "triage",
            "construct_story": "story",
            "score_story": "story",
            "evaluate_editorial": "editorial",
            "resolve_continuity": "continuity",
            "evaluate_tendencies": "tendency",
            "write_brief": "brief",
            "direction_observation": "brief",
        }
        budget.consume(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            item_count=item_count,
            stage=stages.get(task, task),
        )

    def triage_candidates(self, candidates: list[Candidate]) -> CandidateTriageBatch:
        self._consume(
            "candidate_triage",
            [candidate.model_dump(mode="json") for candidate in candidates],
            len(candidates),
        )
        return CandidateTriageBatch(
            candidates=[
                CandidateTriageDraft(
                    candidate_id=candidate.id,
                    hypothesis=candidate.hypothesis,
                    potential_novelty="输入可能描述一个产品或能力变化。",
                    potential_impact="若成立，可能影响开发者工作流。",
                    affected_audiences=["AI 开发者"],
                    impact_mechanism="能力或接口变化会改变开发者的实现选择。",
                    semantic_disposition=SemanticDisposition.BUILD,
                    evidence_state=EvidenceState.SUFFICIENT,
                    reason_codes=[CandidateReasonCode.DEVELOPER_IMPACT],
                    rationale="Fixture 证据足以尝试构建 Story。",
                )
                for candidate in candidates
            ]
        )

    def construct_story(self, candidate: Candidate) -> MergedStoryDraft:
        self._consume("construct_story", candidate.model_dump(mode="json"), 1)
        evidence = [
            item
            for item in candidate.evidence
            if item.authority
            not in {
                EvidenceAuthority.DISCOVERY_ONLY,
                EvidenceAuthority.UNVERIFIED_EXTERNAL,
            }
        ] or candidate.evidence
        fact = evidence[0].scope or candidate.hypothesis
        return MergedStoryDraft(
            same_event=True,
            canonical_title=candidate.hypothesis,
            category="ai_and_open_source",
            entity_names=candidate.entity_names,
            product_names=candidate.product_names,
            topic_names=candidate.topic_names,
            facts=[fact],
            fact_supports=[
                DraftClaimSupport(
                    claim=fact,
                    claim_type=ClaimType.OTHER,
                    evidence_ids=[evidence[0].evidence_id],
                    requested_scope=evidence[0].support_scope,
                    evidence_scope=evidence[0].scope or fact,
                    claim_scope=fact,
                    scope_supported=True,
                )
            ],
            analysis=([candidate.potential_impact] if candidate.potential_impact else []),
            uncertainties=candidate.missing_evidence,
            source_urls=[item.url for item in candidate.evidence],
            primary_source_url=evidence[0].url,
        )

    def classify_items(self, items: list[RawItem]) -> ClassificationBatch:
        self._consume(
            "classify",
            [item.model_dump(mode="json") for item in items],
            len(items),
        )
        return ClassificationBatch(
            items=[
                ClassifiedItem(
                    item_id=item.id,
                    relevant=True,
                    relevance_reason="Fixture item matches configured topics.",
                    important=True,
                    importance_reason="Fixture item demonstrates the pipeline.",
                    category=(
                        "market_and_companies"
                        if item.source_type.endswith("market")
                        else "ai_and_open_source"
                    ),
                )
                for item in items
            ]
        )

    def merge_story(self, items: list[RawItem]) -> MergedStoryDraft:
        self._consume(
            "merge_story",
            [item.model_dump(mode="json") for item in items],
            len(items),
        )
        first = items[0]
        return MergedStoryDraft(
            same_event=True,
            canonical_title=first.title,
            category=(
                "developer_discussions"
                if any("hacker_news" in item.source_type for item in items)
                else "ai_and_open_source"
            ),
            entity_names=list(
                dict.fromkeys(name for item in items for name in item.company_candidates)
            ),
            product_names=[],
            topic_names=list(
                dict.fromkeys(name for item in items for name in item.topic_candidates)
            ),
            facts=[item.summary or item.title for item in items],
            analysis=["多个 Fixture 来源在离线流程中被结构化合并。"],
            uncertainties=[],
            source_urls=[item.url for item in items],
            primary_source_url=first.url,
        )

    def score_story(self, story: Story) -> StoryScore:
        self._consume("score_story", story.model_dump(mode="json"), 1)
        return StoryScore(
            relevance_score=0.9,
            importance_score=0.8,
            novelty_score=0.7,
            credibility_score=min(1.0, 0.6 + 0.1 * len(story.source_urls)),
            explanation="Fixture 使用稳定分数以保证测试可重复。",
        )

    def write_brief(
        self,
        stories: list[Story],
        signals: list[Signal],
        editorial_decisions: list[EditorialDecision] | None = None,
    ) -> BriefDraft:
        self._consume(
            "write_brief",
            {
                "stories": [story.model_dump(mode="json") for story in stories],
                "signals": [signal.model_dump(mode="json") for signal in signals],
                "editorial_decisions": [
                    decision.model_dump(mode="json")
                    for decision in editorial_decisions or []
                ],
            },
            len(stories),
        )
        del signals
        del editorial_decisions
        watch_anchor = None
        if stories:
            first = stories[0]
            watch_anchor = next(
                iter([*first.entity_names, *first.product_names]),
                first.canonical_title,
            )
        watch_drafts: list[GeneratedWatchDraft] = []
        if stories:
            first = stories[0]
            anchors = {
                "entity_anchors": first.entity_names[:1],
                "product_anchors": first.product_names[:1],
                "topic_anchors": first.topic_names[:1],
            }
            if any(anchors.values()):
                watch_drafts.append(
                    GeneratedWatchDraft(
                        expectation=(
                            f"继续观察 {watch_anchor} 的后续官方发布与开发者反馈。"
                        ),
                        source_story_ids=[first.id],
                        **anchors,
                    )
                )
        return BriefDraft(
            items=[
                GeneratedBriefItem(
                    story_ids=[story.id],
                    section=story.category,
                    title=story.canonical_title,
                    what_happened=story.facts[0] if story.facts else story.canonical_title,
                    why_it_matters=(
                        story.analysis[0] if story.analysis else "该事件与配置关注主题相关。"
                    ),
                    uncertainty=story.uncertainties[0] if story.uncertainties else None,
                    source_urls=story.source_urls,
                )
                for story in stories
            ],
            watch_items=watch_drafts,
        )

    def evaluate_editorial(self, stories: list[Story]) -> EditorialDecisionBatch:
        self._consume(
            "evaluate_editorial",
            [story.model_dump(mode="json") for story in stories],
            len(stories),
        )
        return EditorialDecisionBatch(
            decisions=[
                EditorialDecision(
                    story_id=story.id,
                    placement=Placement.TOP,
                    treatment=Treatment.SHORT_NEWS,
                    reader_value=4,
                    evidence_value=2,
                    fact_status=FactStatus.CLAIM,
                    editorial_confidence=0.8,
                    causal_confidence=None,
                    news_delta=story.facts[0] if story.facts else story.canonical_title,
                    why_now="Fixture 使用确定性编辑判断验证离线编排。",
                    decision_reasons=[DecisionReason.DEVELOPER_PRODUCTION_IMPACT],
                    retain_for_trends=False,
                    uncertainty="Fixture 不声称存在输入之外的独立验证。",
                )
                for story in stories
            ]
        )

    def write_direction_observation(
        self,
        signals: list[Signal],
    ) -> DirectionObservation:
        self._consume(
            "direction_observation",
            [signal.model_dump(mode="json") for signal in signals],
            len(signals),
        )
        if not signals:
            return DirectionObservation()
        return DirectionObservation(
            observation="Fixture 信号显示多个可追溯来源正在形成同一方向。",
            evidence_story_ids=list(
                dict.fromkeys(
                    story_id for signal in signals for story_id in signal.supporting_story_ids
                )
            ),
            confidence="medium",
        )

    def resolve_continuity(
        self,
        context: ContinuityResolutionInput,
    ) -> ContinuityResolution:
        self._consume(
            "resolve_continuity",
            context.model_dump(mode="json"),
            (
                len(context.relation_candidates)
                + len(context.watch_candidates)
                + len(context.prior_hypotheses)
            ),
        )
        relations: list[ResolvedRelationDraft] = []
        for candidate in context.relation_candidates:
            confirmed = (
                candidate.explicit_version_progression
                and candidate.product_named_in_both_titles
                and candidate.same_release_series
            )
            relations.append(
                ResolvedRelationDraft(
                    confirmed=confirmed,
                    previous_story=candidate.previous.ref,
                    current_story=candidate.current.ref,
                    relation_type=(
                        (
                            StoryRelationType.STATUS_TRANSITION
                            if candidate.prerelease_to_stable
                            else StoryRelationType.FOLLOW_UP
                        )
                        if confirmed
                        else None
                    ),
                    what_changed=(
                        f"{candidate.shared_products[0]} 发布了明确的后续版本。"
                        if confirmed
                        else None
                    ),
                    rationale=(
                        "Fixture 中存在明确版本推进。"
                        if confirmed
                        else "共享产品名称不足以证明发展关系。"
                    ),
                    evidence_refs=(
                        [
                            StoryEvidenceRef(story=candidate.previous.ref),
                            StoryEvidenceRef(story=candidate.current.ref),
                        ]
                        if confirmed
                        else []
                    ),
                )
            )
        watch_matches: list[ResolvedWatchMatchDraft] = []
        for watch in context.watch_candidates:
            matched = [
                story.ref
                for story in watch.current_story_candidates
                if any(
                    anchor.casefold() in story.canonical_title.casefold()
                    for anchor in watch.product_anchors
                )
            ]
            watch_matches.append(
                ResolvedWatchMatchDraft(
                    matched=bool(matched),
                    watch_id=watch.watch_id,
                    matched_story_refs=matched[:1],
                    rationale=(
                        "Fixture Story 明确提及被观察产品。"
                        if matched
                        else "没有足够具体的 Watch fulfillment 证据。"
                    ),
                )
            )
        return ContinuityResolution(relations=relations, watch_matches=watch_matches)

    def evaluate_tendencies(
        self,
        clusters: list[TendencyEvidenceCluster],
        current_views: list[TendencyCurrentView],
    ) -> TendencyEvaluationBatch:
        self._consume(
            "evaluate_tendencies",
            {
                "evidence_clusters": [
                    cluster.model_dump(mode="json") for cluster in clusters
                ],
                "current_views": [
                    view.model_dump(mode="json") for view in current_views
                ],
            },
            len(clusters),
        )
        if current_views:
            view = current_views[0]
            new_clusters = [
                cluster
                for cluster in clusters
                if cluster.cluster_id not in view.formation_cluster_ids
                and view.formed_at is not None
                and max(cluster.observed_dates) > view.formed_at
            ]
            if not new_clusters:
                return TendencyEvaluationBatch()
            return TendencyEvaluationBatch(
                decisions=[
                    TendencyDecisionDraft(
                        existing_tendency_id=view.tendency_id,
                        standing_after=TendencyStanding.PERSISTENT,
                        update_kind=TendencyUpdateKind.SUPPORTED,
                        claim=view.claim,
                        assessment=view.assessment,
                        supporting_cluster_ids=[new_clusters[0].cluster_id],
                    )
                ]
            )
        dates = {day for cluster in clusters for day in cluster.observed_dates}
        actors = {actor for cluster in clusters for actor in cluster.actor_keys}
        if len(clusters) < 2 or len(dates) < 2 or len(actors) < 2:
            return TendencyEvaluationBatch()
        chosen = clusters[:3]
        two_cluster_exception = len(chosen) == 2
        return TendencyEvaluationBatch(
            decisions=[
                TendencyDecisionDraft(
                    standing_after=(
                        TendencyStanding.EMERGING
                        if len(chosen) >= 3
                        or all(
                            SourceRole.OFFICIAL_PRIMARY in cluster.source_roles
                            for cluster in chosen
                        )
                        else TendencyStanding.CANDIDATE
                    ),
                    claim="多个独立参与者正在把 AI 能力嵌入真实工作流。",
                    assessment=TendencyAssessment(
                        shared_mechanism="组织上下文与工作流集成正成为产品能力的一部分。",
                        baseline="此前能力主要停留在独立聊天或单点工具。",
                        falsifier="后续产品持续撤回工作流集成且实际使用没有增加。",
                        observable_impacts=["多个独立参与者出现可观察的工作流变化。"],
                        counterevidence_considered=True,
                        decision_rationale="Fixture 的跨日期独立事件满足形成测试。",
                        formation_exception_rationale=(
                            "两个不同 actor 均有 primary evidence 与可观察影响。"
                            if two_cluster_exception
                            else None
                        ),
                    ),
                    supporting_cluster_ids=[cluster.cluster_id for cluster in chosen],
                    formation_support=[
                        TendencyFormationSupportDraft(
                            cluster_id=cluster.cluster_id,
                            directly_supports_direction=True,
                            rationale="该事件直接体现 AI 能力进入真实工作流。",
                            evidence_scope="AI 产品和开发者工作流",
                        )
                        for cluster in chosen
                    ],
                    claim_scope_supported=True,
                    scope_alignment_rationale=(
                        "Claim 限定于输入证据覆盖的 AI 产品与工作流。"
                    ),
                )
            ]
        )

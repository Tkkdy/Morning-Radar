"""Deterministic AI replacement for fixtures and tests."""

from __future__ import annotations

from morning_radar.ai.models import (
    BriefDraft,
    ClassificationBatch,
    ClassifiedItem,
    ContinuityResolution,
    ContinuityResolutionInput,
    DirectionObservation,
    GeneratedBriefItem,
    GeneratedWatchDraft,
    MergedStoryDraft,
    ResolvedRelationDraft,
    ResolvedWatchMatchDraft,
    StoryScore,
)
from morning_radar.models import (
    RawItem,
    Signal,
    Story,
    StoryEvidenceRef,
    StoryRelationType,
)


class FakeAIProvider:
    def classify_items(self, items: list[RawItem]) -> ClassificationBatch:
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
        return StoryScore(
            relevance_score=0.9,
            importance_score=0.8,
            novelty_score=0.7,
            credibility_score=min(1.0, 0.6 + 0.1 * len(story.source_urls)),
            explanation="Fixture 使用稳定分数以保证测试可重复。",
        )

    def write_brief(self, stories: list[Story], signals: list[Signal]) -> BriefDraft:
        del signals
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

    def write_direction_observation(
        self,
        signals: list[Signal],
    ) -> DirectionObservation:
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

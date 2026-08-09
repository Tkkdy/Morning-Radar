"""Deterministic AI replacement for fixtures and tests."""

from __future__ import annotations

from morning_radar.ai.models import (
    BriefDraft,
    ClassificationBatch,
    ClassifiedItem,
    DirectionObservation,
    GeneratedBriefItem,
    MergedStoryDraft,
    StoryScore,
)
from morning_radar.models import RawItem, Signal, Story


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
            watch_next=(
                [f"继续观察 {watch_anchor} 的后续官方发布与开发者反馈。"]
                if watch_anchor
                else []
            ),
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

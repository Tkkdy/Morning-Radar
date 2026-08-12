"""Project immutable continuity records into the reader-facing DailyBrief."""

from __future__ import annotations

from morning_radar.continuity.candidates import StoryMemory
from morning_radar.models import (
    BriefContinuityContext,
    BriefItem,
    BriefJudgementCue,
    CurrentJudgement,
    DailyBrief,
    DailyContinuity,
    JudgementUpdateKind,
    JudgementViewState,
    RelationDisposition,
    WatchEventType,
)


def apply_continuity_to_brief(
    brief: DailyBrief,
    continuity: DailyContinuity,
    *,
    story_memory: list[StoryMemory],
    current_judgements: dict[str, CurrentJudgement] | None = None,
) -> DailyBrief:
    title_by_ref = {memory.ref: memory.story.canonical_title for memory in story_memory}
    by_story: dict[str, BriefContinuityContext] = {}

    def context(story_id: str) -> BriefContinuityContext:
        existing = by_story.get(story_id)
        if existing is None:
            existing = BriefContinuityContext(current_story_id=story_id)
            by_story[story_id] = existing
        return existing

    for relation in continuity.relations:
        if (
            relation.disposition is not RelationDisposition.CONFIRMED
            or relation.current_story.date != brief.date
        ):
            continue
        current = context(relation.current_story.story_id)
        by_story[relation.current_story.story_id] = current.model_copy(
            update={
                "relation_type": relation.relation_type.value,
                "what_changed": relation.change_summary,
                "previous_story_date": relation.previous_story.date,
                "previous_story_id": relation.previous_story.story_id,
                "previous_story_title": title_by_ref.get(relation.previous_story),
            }
        )
    for event in continuity.watch_events:
        if event.event_type is not WatchEventType.MATCHED:
            continue
        for story_ref in event.matched_story_refs:
            if story_ref.date != brief.date:
                continue
            current = context(story_ref.story_id)
            by_story[story_ref.story_id] = current.model_copy(
                update={"watch_matches": [*current.watch_matches, event.expectation]}
            )
    for judgement in continuity.judgements:
        if judgement.update_kind is JudgementUpdateKind.SUPPORTED:
            continue
        cue = BriefJudgementCue(
            judgement_id=judgement.judgement_id,
            update_kind=(judgement.update_kind.value if judgement.update_kind else "new"),
            claim=judgement.claim,
        )
        for evidence in judgement.evidence_refs:
            if evidence.story.date != brief.date:
                continue
            current = context(evidence.story.story_id)
            by_story[evidence.story.story_id] = current.model_copy(
                update={"judgement_cues": [*current.judgement_cues, cue]}
            )
    daily_judgements = {item.judgement_id: item for item in continuity.judgements}
    for view in (current_judgements or {}).values():
        if view.state is not JudgementViewState.NEEDS_REVIEW:
            continue
        cue = BriefJudgementCue(
            judgement_id=view.latest_record.judgement_id,
            update_kind="needs_review",
            claim=view.latest_record.claim,
        )
        trigger_evidence = [
            evidence
            for trigger_id in view.review_trigger_ids
            if trigger_id in daily_judgements
            for evidence in daily_judgements[trigger_id].evidence_refs
            if evidence.story.date == brief.date
        ]
        for evidence in trigger_evidence:
            current = context(evidence.story.story_id)
            by_story[evidence.story.story_id] = current.model_copy(
                update={"judgement_cues": [*current.judgement_cues, cue]}
            )

    def update_items(items: list[BriefItem]) -> list[BriefItem]:
        return [
            item.model_copy(
                update={
                    "continuity_contexts": [
                        by_story[story_id]
                        for story_id in item.story_ids
                        if story_id in by_story
                    ]
                }
            )
            for item in items
        ]

    return brief.model_copy(
        update={
            "top_stories": update_items(brief.top_stories),
            "market_and_companies": update_items(brief.market_and_companies),
            "ai_and_open_source": update_items(brief.ai_and_open_source),
            "trend_radar": update_items(brief.trend_radar),
            "developer_discussions": update_items(brief.developer_discussions),
            "other_reading": update_items(brief.other_reading),
        }
    )

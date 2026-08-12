"""Epistemic and reference validation for continuity records and AI drafts."""

from __future__ import annotations

from collections.abc import Iterable

from morning_radar.ai.models import (
    ContinuityResolution,
    ContinuityResolutionInput,
    ContinuityStorySummary,
)
from morning_radar.continuity.candidates import StoryMemory
from morning_radar.models import (
    DailyContinuity,
    RelationDisposition,
    StoryEvidenceRef,
    StoryOccurrenceRef,
)


def _story_map(stories: Iterable[StoryMemory]) -> dict[StoryOccurrenceRef, StoryMemory]:
    result: dict[StoryOccurrenceRef, StoryMemory] = {}
    for memory in stories:
        if memory.ref in result:
            raise ValueError(f"duplicate Story occurrence: {memory.ref}")
        result[memory.ref] = memory
    return result


def validate_evidence_refs(
    evidence_refs: Iterable[StoryEvidenceRef],
    stories: dict[StoryOccurrenceRef, StoryMemory],
) -> None:
    for evidence in evidence_refs:
        memory = stories.get(evidence.story)
        if memory is None:
            raise ValueError(f"evidence references unknown Story occurrence: {evidence.story}")
        if any(index >= len(memory.story.facts) for index in evidence.fact_indexes):
            raise ValueError("evidence fact index is outside the referenced Story")


def validate_daily_continuity(
    daily: DailyContinuity,
    *,
    stories: Iterable[StoryMemory],
) -> None:
    story_by_ref = _story_map(stories)
    for relation in daily.relations:
        if relation.previous_story not in story_by_ref:
            raise ValueError("relation references unknown previous Story")
        if relation.current_story not in story_by_ref:
            raise ValueError("relation references unknown current Story")
        validate_evidence_refs(relation.evidence_refs, story_by_ref)
        evidence_story_refs = {item.story for item in relation.evidence_refs}
        if relation.disposition is RelationDisposition.CONFIRMED and not {
            relation.previous_story,
            relation.current_story,
        }.issubset(evidence_story_refs):
            raise ValueError("confirmed relation evidence must include both Story occurrences")
    for event in daily.watch_events:
        for ref in [*event.source_story_refs, *event.matched_story_refs]:
            if ref not in story_by_ref:
                raise ValueError("Watch event references unknown Story occurrence")
    for judgement in daily.judgements:
        validate_evidence_refs(judgement.evidence_refs, story_by_ref)


def validate_continuity_resolution(
    output: ContinuityResolution,
    context: ContinuityResolutionInput,
) -> None:
    relation_pairs = {
        (candidate.previous.ref, candidate.current.ref)
        for candidate in context.relation_candidates
    }
    watch_candidates = {
        item.watch_id: {story.ref for story in item.current_story_candidates}
        for item in context.watch_candidates
    }
    prior_by_id = {item.judgement_id: item for item in context.prior_hypotheses}
    story_summaries = {
        story.ref: story
        for candidate in context.relation_candidates
        for story in (candidate.previous, candidate.current)
    }
    for watch in context.watch_candidates:
        story_summaries.update({story.ref: story for story in watch.current_story_candidates})
    for prior in context.prior_hypotheses:
        story_summaries.update({story.ref: story for story in prior.current_story_candidates})

    for relation in output.relations:
        pair = (relation.previous_story, relation.current_story)
        if pair not in relation_pairs:
            raise ValueError("AI relation is outside the deterministic candidate set")
        if relation.confirmed:
            if relation.relation_type is None or not relation.what_changed:
                raise ValueError("confirmed relation requires type and what_changed")
            refs = {item.story for item in relation.evidence_refs}
            if not set(pair).issubset(refs):
                raise ValueError("confirmed relation evidence must include both Stories")
        elif relation.relation_type is not None or relation.what_changed is not None:
            raise ValueError("rejected relation cannot claim relation semantics")
        _validate_summary_evidence(relation.evidence_refs, story_summaries)

    for match in output.watch_matches:
        allowed = watch_candidates.get(match.watch_id)
        if allowed is None:
            raise ValueError("AI Watch match references an unknown open Watch")
        if match.matched:
            if not match.matched_story_refs or not set(match.matched_story_refs).issubset(allowed):
                raise ValueError("Watch match is outside its deterministic candidate set")
        elif match.matched_story_refs:
            raise ValueError("unmatched Watch cannot claim matched Stories")

    for update in output.judgement_updates:
        prior = prior_by_id.get(update.prior_judgement_id)
        if prior is None:
            raise ValueError("AI Judgement update references an unknown prior hypothesis")
        allowed = {story.ref for story in prior.current_story_candidates}
        if not {item.story for item in update.evidence_refs}.issubset(allowed):
            raise ValueError("Judgement update evidence must be current factual Stories")
        _validate_summary_evidence(update.evidence_refs, story_summaries)


def _validate_summary_evidence(
    evidence_refs: Iterable[StoryEvidenceRef],
    summaries: dict[StoryOccurrenceRef, ContinuityStorySummary],
) -> None:
    for evidence in evidence_refs:
        summary = summaries.get(evidence.story)
        if summary is None:
            raise ValueError("AI evidence references a Story outside its factual input")
        if any(index >= len(summary.facts) for index in evidence.fact_indexes):
            raise ValueError("AI evidence fact index is outside its Story input")

"""Deterministically cluster duplicate coverage and confirmed event chains."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from morning_radar.continuity.candidates import StoryMemory
from morning_radar.models import (
    DailyContinuity,
    RelationDisposition,
    SourceRole,
    StoryOccurrenceRef,
    TendencyEvidenceCluster,
)


def build_evidence_clusters(
    stories: list[StoryMemory],
    continuities: list[DailyContinuity],
) -> list[TendencyEvidenceCluster]:
    by_ref = {memory.ref: memory for memory in stories}
    parent = {ref: ref for ref in by_ref}

    def find(ref: StoryOccurrenceRef) -> StoryOccurrenceRef:
        while parent[ref] != ref:
            parent[ref] = parent[parent[ref]]
            ref = parent[ref]
        return ref

    def union(left: StoryOccurrenceRef, right: StoryOccurrenceRef) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            newest = max(
                left_root,
                right_root,
                key=lambda value: (value.date, value.story_id),
            )
            parent[newest] = min(
                left_root,
                right_root,
                key=lambda value: (value.date, value.story_id),
            )

    relations = [relation for daily in continuities for relation in daily.relations]
    retracted = {
        relation.retracts_relation_id
        for relation in relations
        if relation.disposition is RelationDisposition.RETRACTED
        and relation.retracts_relation_id
    }
    for relation in relations:
        if (
            relation.disposition is RelationDisposition.CONFIRMED
            and relation.relation_id not in retracted
            and relation.previous_story in parent
            and relation.current_story in parent
        ):
            union(relation.previous_story, relation.current_story)

    groups: dict[StoryOccurrenceRef, list[StoryMemory]] = defaultdict(list)
    for ref, memory in by_ref.items():
        groups[find(ref)].append(memory)
    clusters: list[TendencyEvidenceCluster] = []
    for members in groups.values():
        ordered = sorted(members, key=lambda value: (value.ref.date, value.ref.story_id))
        refs = [memory.ref for memory in ordered]
        # A confirmed chain keeps the earliest immutable occurrence as its stable
        # identity so later follow-ups do not masquerade as new independent evidence.
        identity = f"{refs[0].date}:{refs[0].story_id}"
        cluster_id = f"cluster-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
        source_roles = list(
            dict.fromkeys(
                source_ref.source_role
                for memory in ordered
                for source_ref in memory.story.source_refs
            )
        )
        if not source_roles:
            source_roles = [SourceRole.EDITORIAL]
        clusters.append(
            TendencyEvidenceCluster(
                cluster_id=cluster_id,
                story_refs=refs,
                observed_dates=list(dict.fromkeys(ref.date for ref in refs)),
                actor_keys=list(
                    dict.fromkeys(
                        actor
                        for memory in ordered
                        for actor in memory.story.entity_names
                    )
                ),
                event_identity=f"{refs[0].date}:{refs[0].story_id}",
                source_roles=source_roles,
                source_count=len(
                    {
                        url
                        for memory in ordered
                        for url in memory.story.source_urls
                    }
                ),
                titles=[memory.story.canonical_title for memory in ordered],
                facts=[
                    fact[:500]
                    for memory in ordered
                    for fact in memory.story.facts[:3]
                ][:6],
            )
        )
    return sorted(
        clusters,
        key=lambda cluster: (
            max(cluster.observed_dates),
            cluster.source_count,
            cluster.cluster_id,
        ),
        reverse=True,
    )

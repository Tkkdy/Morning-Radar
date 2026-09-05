import json
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from morning_radar.ai import AIBudget, AIOutputError, FakeAIProvider
from morning_radar.ai.models import (
    ContinuityRelationCandidate,
    ContinuityResolution,
    ContinuityResolutionInput,
    ContinuityStorySummary,
    ContinuityWatchInput,
    PriorJudgementInput,
)
from morning_radar.continuity.candidates import (
    StoryMemory,
    deterministic_relation,
    generate_relation_candidates,
)
from morning_radar.continuity.engine import _trim_context, resolve_daily_continuity
from morning_radar.continuity.materialize import (
    materialize_judgements,
    materialize_open_watches,
    merge_daily_continuity,
)
from morning_radar.continuity.reducer import (
    reduce_judgements,
    reduce_relations,
    reduce_watches,
)
from morning_radar.models import (
    DailyContinuity,
    JudgementRecord,
    JudgementUpdateKind,
    JudgementViewState,
    RelationDisposition,
    Story,
    StoryEvidenceRef,
    StoryOccurrenceRef,
    StoryRelationRecord,
    StoryRelationType,
    WatchEvent,
    WatchEventType,
)

NOW = datetime(2026, 8, 12, 1, tzinfo=UTC)


def story(
    story_id: str,
    title: str,
    *,
    product: str = "Example SDK",
    status: str = "available",
    repository: str = "example/sdk",
) -> Story:
    return Story(
        id=story_id,
        canonical_title=title,
        category="ai_and_open_source",
        entity_names=["Example"],
        product_names=[product],
        topic_names=["ai_coding"],
        updated_at=NOW,
        source_item_ids=[f"item-{story_id}"],
        source_urls=[f"https://github.com/{repository}/releases/tag/{story_id}"],
        primary_source_url=f"https://github.com/{repository}/releases/tag/{story_id}",
        facts=[f"{title} 已发布。"],
        relevance_score=0.9,
        importance_score=0.8,
        novelty_score=0.8,
        credibility_score=0.9,
        status=status,
    )


def memory(day: date, value: Story) -> StoryMemory:
    return StoryMemory(
        ref=StoryOccurrenceRef(date=day, story_id=value.id),
        story=value,
    )


def evidence(day: date, story_id: str) -> list[StoryEvidenceRef]:
    return [
        StoryEvidenceRef(story=StoryOccurrenceRef(date=day, story_id=story_id))
    ]


def judgement(
    judgement_id: str,
    *,
    root: str | None = None,
    updates: str | None = None,
    kind: JudgementUpdateKind | None = None,
    dependencies: list[str] | None = None,
    day: int = 1,
) -> JudgementRecord:
    root_id = root or judgement_id
    return JudgementRecord(
        judgement_id=judgement_id,
        root_judgement_id=root_id,
        recorded_at=datetime(2026, 8, day, tzinfo=UTC),
        claim=f"这是足够具体并可在未来复查的判断 {judgement_id}",
        rationale="当前 Story facts 支持这一简短判断。",
        evidence_refs=evidence(date(2026, 8, day), f"story-{judgement_id}"),
        depends_on_judgement_ids=dependencies or [],
        updates_judgement_id=updates,
        update_kind=kind,
    )


def test_story_occurrence_ref_is_composite_and_hashable() -> None:
    first = StoryOccurrenceRef(date=date(2026, 8, 1), story_id="same")
    second = StoryOccurrenceRef(date=date(2026, 8, 2), story_id="same")

    assert first != second
    assert {first, second} == {first, second}


def test_judgement_id_cannot_parse_as_story_evidence() -> None:
    with pytest.raises(ValidationError):
        StoryEvidenceRef.model_validate({"judgement_id": "judgement-1"})


def test_judgement_update_requires_target_and_kind_together() -> None:
    with pytest.raises(ValidationError, match="both target and update kind"):
        judgement("update", root="root", updates="root")


def test_daily_continuity_round_trip() -> None:
    daily = DailyContinuity(
        date=date(2026, 8, 1),
        generated_at=datetime(2026, 8, 1, tzinfo=UTC),
        judgements=[judgement("root")],
    )

    assert DailyContinuity.model_validate_json(daily.model_dump_json()) == daily


def test_watch_reducer_replays_open_and_match() -> None:
    opened = WatchEvent(
        watch_id="watch-1",
        recorded_at=datetime(2026, 8, 1, tzinfo=UTC),
        event_type=WatchEventType.OPENED,
        expectation="观察 Example SDK 是否发布稳定版本。",
        product_anchors=["Example SDK"],
        source_story_refs=[StoryOccurrenceRef(date=date(2026, 8, 1), story_id="rc")],
    )
    matched = opened.model_copy(
        update={
            "recorded_at": datetime(2026, 8, 2, tzinfo=UTC),
            "event_type": WatchEventType.MATCHED,
            "matched_story_refs": [
                StoryOccurrenceRef(date=date(2026, 8, 2), story_id="stable")
            ],
            "rationale": "稳定版已正式发布。",
        }
    )

    current = reduce_watches(
        [
            DailyContinuity(
                date=date(2026, 8, 1),
                generated_at=opened.recorded_at,
                watch_events=[opened],
            ),
            DailyContinuity(
                date=date(2026, 8, 2),
                generated_at=matched.recorded_at,
                watch_events=[matched],
            ),
        ]
    )["watch-1"]

    assert current.is_open is False
    assert current.matched_story_refs[0].story_id == "stable"


def test_judgement_reducer_handles_update_chain_and_needs_review() -> None:
    root = judgement("root")
    supported = judgement(
        "supported",
        root="root",
        updates="root",
        kind=JudgementUpdateKind.SUPPORTED,
        day=2,
    )
    weakened = judgement(
        "weakened",
        root="root",
        updates="supported",
        kind=JudgementUpdateKind.WEAKENED,
        day=3,
    )
    revised = judgement(
        "revised",
        root="root",
        updates="weakened",
        kind=JudgementUpdateKind.REVISED,
        day=4,
    )
    overturned = judgement(
        "overturned",
        root="root",
        updates="revised",
        kind=JudgementUpdateKind.OVERTURNED,
        day=5,
    )
    dependent = judgement("dependent", dependencies=["root"], day=2)
    daily = [
        DailyContinuity(
            date=date(2026, 8, index + 1),
            generated_at=datetime(2026, 8, index + 1, tzinfo=UTC),
            judgements=records,
        )
        for index, records in enumerate(
            ([root], [supported, dependent], [weakened], [revised], [overturned])
        )
    ]

    current = reduce_judgements(daily)

    assert current["root"].latest_record.update_kind is JudgementUpdateKind.OVERTURNED
    assert current["dependent"].state is JudgementViewState.NEEDS_REVIEW
    assert current["dependent"].review_trigger_ids == ["overturned"]


def test_relation_retraction_is_append_only_and_removes_current_relation() -> None:
    previous_ref = StoryOccurrenceRef(date=date(2026, 8, 1), story_id="old")
    current_ref = StoryOccurrenceRef(date=date(2026, 8, 2), story_id="new")
    confirmed = StoryRelationRecord(
        relation_id="relation-root",
        recorded_at=datetime(2026, 8, 2, tzinfo=UTC),
        previous_story=previous_ref,
        current_story=current_ref,
        relation_type=StoryRelationType.FOLLOW_UP,
        change_summary="Example SDK 发布了后续版本。",
        rationale="当时存在明确版本推进证据。",
        evidence_refs=[
            StoryEvidenceRef(story=previous_ref),
            StoryEvidenceRef(story=current_ref),
        ],
    )
    retracted = confirmed.model_copy(
        update={
            "relation_id": "relation-retraction",
            "recorded_at": datetime(2026, 8, 3, tzinfo=UTC),
            "disposition": RelationDisposition.RETRACTED,
            "retracts_relation_id": confirmed.relation_id,
            "change_summary": "后续核验表明两个版本不属于同一发布序列。",
            "rationale": "新的来源身份信息否定了原关系。",
        }
    )
    history = [
        DailyContinuity(
            date=date(2026, 8, 2),
            generated_at=confirmed.recorded_at,
            relations=[confirmed],
        ),
        DailyContinuity(
            date=date(2026, 8, 3),
            generated_at=retracted.recorded_at,
            relations=[retracted],
        ),
    ]

    assert reduce_relations(history) == {}
    assert history[0].relations == [confirmed]


def test_explicit_version_progression_is_confirmed_without_ai() -> None:
    old = memory(date(2026, 8, 11), story("old", "Example SDK v2.0.0rc1"))
    new = memory(date(2026, 8, 12), story("new", "Example SDK v2.0.0"))
    candidates = generate_relation_candidates(
        [new], [old], maximum_days=14, maximum_candidates=10
    )

    relation = deterministic_relation(candidates[0], recorded_at=NOW)

    assert relation is not None
    assert relation.previous_story == old.ref
    assert relation.current_story == new.ref


def test_same_story_id_on_another_date_is_not_a_new_development() -> None:
    old = memory(date(2026, 8, 11), story("same", "Example SDK v2.0.0"))
    new = memory(date(2026, 8, 12), story("same", "Example SDK v2.0.1"))

    assert generate_relation_candidates(
        [new], [old], maximum_days=14, maximum_candidates=10
    ) == []


class RecordingProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.contexts = []
        self.budget = AIBudget(10, 100_000, 40)

    def resolve_continuity(self, context):
        self.calls += 1
        self.contexts.append(context)
        self.budget.consume(context.model_dump_json(), item_count=1)
        self.budget.record_network_request()
        return super().resolve_continuity(context)


class FailingProvider(RecordingProvider):
    def resolve_continuity(self, context):
        self.calls += 1
        self.budget.consume(context.model_dump_json(), item_count=1)
        self.budget.record_network_request()
        raise AIOutputError("fixture failure")


def test_no_candidates_means_no_continuity_ai_call() -> None:
    provider = RecordingProvider()

    result = resolve_daily_continuity(
        current_date=date(2026, 8, 12),
        generated_at=NOW,
        current_stories=[story("new", "Unrelated v1.0", product="Other")],
        historical_stories=[
            memory(date(2026, 8, 11), story("old", "Example SDK v1.0"))
        ],
        continuity_history=[],
        provider=provider,
        history_days=14,
        maximum_candidates=20,
        maximum_open_watches=20,
        maximum_ai_items=40,
        maximum_input_characters=30_000,
    )

    assert provider.calls == 0
    assert result.stats["continuity_logical_ai_calls"] == 0


def test_multiple_ambiguous_candidates_use_one_batch_call() -> None:
    provider = RecordingProvider()
    current = story("new", "Example SDK gains deployment controls")
    historical = [
        memory(
            date(2026, 8, day),
            story(f"old-{day}", f"Example SDK announcement {day}"),
        )
        for day in (9, 10, 11)
    ]

    result = resolve_daily_continuity(
        current_date=date(2026, 8, 12),
        generated_at=NOW,
        current_stories=[current],
        historical_stories=historical,
        continuity_history=[],
        provider=provider,
        history_days=14,
        maximum_candidates=20,
        maximum_open_watches=20,
        maximum_ai_items=40,
        maximum_input_characters=30_000,
    )

    assert provider.calls == 1
    assert len(provider.contexts[0].relation_candidates) == 3
    assert result.stats["continuity_logical_ai_calls"] == 1
    assert result.stats["continuity_network_requests"] == 1
    assert result.stats["relations_rejected"] == 3
    assert result.stats["relations_unresolved"] == 0


def test_relation_candidate_cap_is_applied_before_batch_call() -> None:
    provider = RecordingProvider()
    current = story("new", "Example SDK gains deployment controls")
    historical = [
        memory(
            date(2026, 8, day),
            story(f"old-{day}", f"Example SDK announcement {day}"),
        )
        for day in (7, 8, 9, 10, 11)
    ]

    resolve_daily_continuity(
        current_date=date(2026, 8, 12),
        generated_at=NOW,
        current_stories=[current],
        historical_stories=historical,
        continuity_history=[],
        provider=provider,
        history_days=14,
        maximum_candidates=2,
        maximum_open_watches=20,
        maximum_ai_items=40,
        maximum_input_characters=30_000,
    )

    assert len(provider.contexts[0].relation_candidates) == 2


def test_character_cap_can_remove_oversized_candidates_without_calling_ai() -> None:
    provider = RecordingProvider()
    current = story("new", "Example SDK gains controls").model_copy(
        update={"facts": ["x" * 5000]}
    )

    result = resolve_daily_continuity(
        current_date=date(2026, 8, 12),
        generated_at=NOW,
        current_stories=[current],
        historical_stories=[
            memory(date(2026, 8, 11), story("old", "Example SDK announced"))
        ],
        continuity_history=[],
        provider=provider,
        history_days=14,
        maximum_candidates=20,
        maximum_open_watches=20,
        maximum_ai_items=40,
        maximum_input_characters=1000,
    )

    assert provider.calls == 0
    assert result.stats["continuity_input_chars"] == 0
    assert result.stats["continuity_budget_skipped"] == 1
    assert result.stats["relations_rejected"] == 0
    assert result.stats["relations_unresolved"] == 1


def test_continuity_adapts_to_remaining_brief_character_reserve() -> None:
    provider = RecordingProvider()
    provider.budget.input_characters_used = 80_000
    current = story("new", "Example SDK gains controls").model_copy(
        update={"facts": ["n" * 3500]}
    )

    result = resolve_daily_continuity(
        current_date=date(2026, 8, 12),
        generated_at=NOW,
        current_stories=[current],
        historical_stories=[
            memory(
                date(2026, 8, day),
                story(f"old-{day}", f"Example SDK announcement {day}").model_copy(
                    update={"facts": ["h" * 3500]}
                ),
            )
            for day in (9, 10, 11)
        ],
        continuity_history=[],
        provider=provider,
        history_days=14,
        maximum_candidates=20,
        maximum_open_watches=20,
        maximum_ai_items=40,
        maximum_input_characters=30_000,
        reserved_input_characters=10_000,
    )

    assert provider.calls == 1
    assert 0 < len(provider.contexts[0].relation_candidates) < 3
    assert result.stats["continuity_input_chars"] <= 10_000
    assert result.stats["continuity_budget_skipped"] == 0


def test_lane_aware_context_trimming_preserves_watch_and_judgement_inputs() -> None:
    current = ContinuityStorySummary(
        ref=StoryOccurrenceRef(date=date(2026, 8, 12), story_id="current"),
        canonical_title="Example SDK current",
    )
    relations = [
        ContinuityRelationCandidate(
            previous=ContinuityStorySummary(
                ref=StoryOccurrenceRef(
                    date=date(2026, 8, 11),
                    story_id=f"previous-{index}",
                ),
                canonical_title=f"Example SDK previous {index}",
            ),
            current=current,
            days_apart=1,
        )
        for index in range(10)
    ]
    context = ContinuityResolutionInput(
        relation_candidates=relations,
        watch_candidates=[
            ContinuityWatchInput(
                watch_id="watch-1",
                expectation="观察 Example SDK 的后续发布。",
                current_story_candidates=[current],
            )
        ],
        prior_hypotheses=[
            PriorJudgementInput(
                judgement_id="judgement-1",
                root_judgement_id="judgement-1",
                claim="Example SDK 的发布节奏正在变化。",
                rationale="此前 Story 提供了具体版本证据。",
                current_story_candidates=[current],
            )
        ],
    )

    bounded, _ = _trim_context(
        context,
        maximum_items=5,
        maximum_characters=30_000,
    )

    assert len(bounded.relation_candidates) == 3
    assert len(bounded.watch_candidates) == 1
    assert len(bounded.prior_hypotheses) == 1


def test_old_open_watch_outside_compute_window_stays_open_without_ai_cost() -> None:
    provider = RecordingProvider()
    opened = WatchEvent(
        watch_id="old-watch",
        recorded_at=datetime(2026, 7, 1, tzinfo=UTC),
        event_type=WatchEventType.OPENED,
        expectation="观察 Example SDK 是否发布稳定版本。",
        product_anchors=["Example SDK"],
        source_story_refs=[
            StoryOccurrenceRef(date=date(2026, 7, 1), story_id="old")
        ],
    )
    result = resolve_daily_continuity(
        current_date=date(2026, 8, 12),
        generated_at=NOW,
        current_stories=[story("new", "Example SDK stable")],
        historical_stories=[],
        continuity_history=[
            DailyContinuity(
                date=date(2026, 7, 1),
                generated_at=opened.recorded_at,
                watch_events=[opened],
            )
        ],
        provider=provider,
        history_days=14,
        maximum_candidates=20,
        maximum_open_watches=20,
        maximum_ai_items=40,
        maximum_input_characters=30_000,
    )

    assert provider.calls == 0
    assert result.current_watches["old-watch"].is_open is True


def test_unrelated_story_does_not_match_open_watch() -> None:
    provider = RecordingProvider()
    opened = WatchEvent(
        watch_id="watch-1",
        recorded_at=datetime(2026, 8, 11, tzinfo=UTC),
        event_type=WatchEventType.OPENED,
        expectation="观察 Example SDK 是否发布稳定版本。",
        product_anchors=["Example SDK"],
        source_story_refs=[
            StoryOccurrenceRef(date=date(2026, 8, 11), story_id="old")
        ],
    )

    result = resolve_daily_continuity(
        current_date=date(2026, 8, 12),
        generated_at=NOW,
        current_stories=[story("new", "Other Tool v1.0", product="Other Tool")],
        historical_stories=[],
        continuity_history=[
            DailyContinuity(
                date=date(2026, 8, 11),
                generated_at=opened.recorded_at,
                watch_events=[opened],
            )
        ],
        provider=provider,
        history_days=14,
        maximum_candidates=20,
        maximum_open_watches=20,
        maximum_ai_items=40,
        maximum_input_characters=30_000,
    )

    assert result.daily.watch_events == []
    assert provider.calls == 0


def test_resolver_failure_degrades_to_empty_optional_records() -> None:
    provider = FailingProvider()

    result = resolve_daily_continuity(
        current_date=date(2026, 8, 12),
        generated_at=NOW,
        current_stories=[story("new", "Example SDK gains controls")],
        historical_stories=[
            memory(date(2026, 8, 11), story("old", "Example SDK announced"))
        ],
        continuity_history=[],
        provider=provider,
        history_days=14,
        maximum_candidates=20,
        maximum_open_watches=20,
        maximum_ai_items=40,
        maximum_input_characters=30_000,
    )

    assert result.daily.relations == []
    assert result.daily.watch_events == []
    assert result.stats["relations_rejected"] == 0
    assert result.stats["relations_unresolved"] == 1


def test_brief_memory_materialization_uses_structured_source() -> None:
    from morning_radar.ai.models import GeneratedJudgementDraft, GeneratedWatchDraft

    source = story("source", "Example SDK v2 release")
    watches = materialize_open_watches(
        [
            GeneratedWatchDraft(
                expectation="观察 Example SDK 是否发布补丁。",
                source_story_ids=[source.id],
                product_anchors=["Example SDK"],
            )
        ],
        brief_date=date(2026, 8, 12),
        recorded_at=NOW,
        stories=[source],
    )
    judgements = materialize_judgements(
        [
            GeneratedJudgementDraft(
                claim="Example SDK 的部署瓶颈正在转向完整执行环境控制能力。",
                rationale="当前版本新增了明确的部署控制能力。",
                evidence_story_ids=[source.id],
                falsifiable=True,
                changes_future_interpretation=True,
                expected_lifetime_days=30,
                loss_if_unmentioned_30d="未来解释将失去该迁移成本基线。",
                correction_required_if_false=True,
            )
        ],
        brief_date=date(2026, 8, 12),
        recorded_at=NOW,
        stories=[source],
    )

    assert watches[0].event_type is WatchEventType.OPENED
    assert judgements[0].root_judgement_id == judgements[0].judgement_id


def test_same_day_rerun_merges_instead_of_overwriting_memory() -> None:
    source = story("source", "Example SDK v2 release")
    from morning_radar.ai.models import GeneratedWatchDraft

    first_watch = materialize_open_watches(
        [
            GeneratedWatchDraft(
                expectation="观察 Example SDK 是否发布补丁。",
                source_story_ids=[source.id],
                product_anchors=["Example SDK"],
            )
        ],
        brief_date=date(2026, 8, 12),
        recorded_at=NOW,
        stories=[source],
    )[0]
    existing = DailyContinuity(
        date=date(2026, 8, 12), generated_at=NOW, watch_events=[first_watch]
    )
    merged = merge_daily_continuity(existing, existing)

    assert merged.watch_events == [first_watch]


def test_golden_fixture_is_real_history_not_synthetic_only() -> None:
    fixture = json.loads(
        Path("fixtures/continuity_golden.json").read_text(encoding="utf-8")
    )

    assert {case["name"] for case in fixture["cases"]} >= {
        "mcp_rc_to_stable",
        "vllm_patch_follow_up",
        "kimi_safety_is_not_vllm_release",
        "claude_code_rumor_is_not_ollama_release",
    }


def _valid_judgement_draft(source_id: str):
    from morning_radar.ai.models import GeneratedJudgementDraft

    return GeneratedJudgementDraft(
        claim="Example SDK 的长期部署策略将转向完整执行环境控制能力。",
        rationale="当前版本新增了明确且可复查的部署控制能力。",
        evidence_story_ids=[source_id],
        falsifiable=True,
        changes_future_interpretation=True,
        expected_lifetime_days=30,
        loss_if_unmentioned_30d="未来版本解释会失去这一部署策略基线。",
        correction_required_if_false=True,
    )


def test_new_judgement_missing_all_creation_gate_fields_is_rejected() -> None:
    from morning_radar.ai.models import GeneratedJudgementDraft

    with pytest.raises(ValidationError):
        GeneratedJudgementDraft.model_validate(
            {
                "claim": "Example SDK 的长期部署策略将转向完整执行环境控制能力。",
                "rationale": "当前版本新增了明确且可复查的部署控制能力。",
                "evidence_story_ids": ["source"],
            }
        )


@pytest.mark.parametrize(
    "update",
    [
        {"falsifiable": False},
        {"changes_future_interpretation": False},
        {"correction_required_if_false": False},
        {"loss_if_unmentioned_30d": "   "},
    ],
)
def test_new_judgement_failing_any_creation_gate_is_dropped(update) -> None:
    source = story("source", "Example SDK deployment controls")
    draft = _valid_judgement_draft(source.id).model_copy(update=update)

    assert materialize_judgements(
        [draft], brief_date=date(2026, 8, 12), recorded_at=NOW, stories=[source]
    ) == []


def test_new_judgement_passing_every_creation_gate_can_persist() -> None:
    source = story("source", "Example SDK deployment controls")

    records = materialize_judgements(
        [_valid_judgement_draft(source.id)],
        brief_date=date(2026, 8, 12),
        recorded_at=NOW,
        stories=[source],
    )

    assert len(records) == 1


class DeadlineProvider:
    def __init__(self) -> None:
        self.calls = []
        self.budget = AIBudget(10, 100_000, 40)

    def resolve_continuity(self, context, *, deadline_monotonic=None):
        lane = (
            "relation" if context.relation_candidates else
            "watch" if context.watch_candidates else "judgement"
        )
        self.calls.append(lane)
        if deadline_monotonic is not None:
            time.sleep(max(0, deadline_monotonic - time.monotonic()) + 0.01)
        return ContinuityResolution()


def test_deadline_prevents_later_continuity_lanes_from_starting() -> None:
    provider = DeadlineProvider()
    opened = WatchEvent(
        watch_id="watch-example",
        recorded_at=datetime(2026, 8, 11, tzinfo=UTC),
        event_type=WatchEventType.OPENED,
        expectation="观察 Example SDK 是否增加部署控制。",
        product_anchors=["Example SDK"],
        source_story_refs=[StoryOccurrenceRef(date=date(2026, 8, 11), story_id="old")],
    )

    result = resolve_daily_continuity(
        current_date=date(2026, 8, 12),
        generated_at=NOW,
        current_stories=[story("new", "Example SDK gains deployment controls")],
        historical_stories=[
            memory(date(2026, 8, 11), story("old", "Example SDK announcement"))
        ],
        continuity_history=[DailyContinuity(
            date=date(2026, 8, 11), generated_at=opened.recorded_at, watch_events=[opened]
        )],
        provider=provider,
        history_days=14,
        maximum_candidates=20,
        maximum_open_watches=20,
        maximum_ai_items=40,
        maximum_input_characters=30_000,
        deadline_monotonic=time.monotonic() + 0.03,
        deadline_safe_minimum_seconds=0.001,
    )

    assert provider.calls == ["relation"]
    assert result.stats["fast_continuity_timeout"] == 1
    assert result.stats["continuity_deadline_skipped_lanes"] == 1
    assert result.daily.watch_events == []

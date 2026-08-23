from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from morning_radar.editorial import (
    EditorialDecision,
    EditorialDecisionBatch,
    FactStatus,
    Placement,
    Treatment,
)
from morning_radar.editorial.evaluator import (
    select_reader_stories,
    validate_editorial_batch,
)
from morning_radar.models import SourceRole, StatementType, Story, StorySourceRef

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def story(story_id: str, *, independently_verified: bool = False) -> Story:
    refs = [
        StorySourceRef(
            raw_item_id=f"raw-{story_id}-official",
            title=story_id,
            source_name="Official",
            source_type="rss",
            url=f"https://example.com/{story_id}/official",
            published_at=NOW,
            fetched_at=NOW,
            source_role=SourceRole.OFFICIAL_PRIMARY,
            statement_type=StatementType.FACTUAL_ANNOUNCEMENT,
        )
    ]
    if independently_verified:
        refs.append(
            StorySourceRef(
                raw_item_id=f"raw-{story_id}-test",
                title=f"{story_id} test",
                source_name="Independent practitioner",
                source_type="rss",
                url=f"https://independent.example/{story_id}",
                published_at=NOW,
                fetched_at=NOW,
                source_role=SourceRole.PRACTITIONER,
                statement_type=StatementType.TEST_EXPERIMENT,
            )
        )
    return Story(
        id=story_id,
        canonical_title=story_id,
        category="ai_and_open_source",
        published_at=NOW,
        updated_at=NOW,
        source_item_ids=[ref.raw_item_id for ref in refs],
        source_urls=[ref.url for ref in refs],
        primary_source_url=refs[0].url,
        source_refs=refs,
        facts=["输入中存在的事实。"],
        relevance_score=0.5,
        importance_score=0.5,
        novelty_score=0.5,
        credibility_score=0.5,
    )


def decision(
    story_id: str,
    placement: Placement = Placement.NEWS,
    treatment: Treatment = Treatment.SHORT_NEWS,
    **updates: object,
) -> EditorialDecision:
    values = {
        "story_id": story_id,
        "placement": placement,
        "treatment": treatment,
        "reader_value": 2,
        "evidence_value": 2,
        "fact_status": FactStatus.CLAIM,
        "editorial_confidence": 0.8,
        "causal_confidence": None,
        "news_delta": "今天出现了明确变化。",
        "why_now": "该变化值得现在记录。",
        "decision_reasons": ["minor_news_delta"],
        "retain_for_trends": False,
        "uncertainty": "仅有厂商声明。",
    }
    values.update(updates)
    return EditorialDecision.model_validate(values)


@pytest.mark.parametrize(
    ("placement", "treatment"),
    [
        (Placement.TOP, Treatment.DEEP_STORY),
        (Placement.TOP, Treatment.SHORT_NEWS),
        (Placement.STORY, Treatment.DEEP_STORY),
        (Placement.NEWS, Treatment.ONE_LINER),
        (Placement.ONE_LINER, Treatment.ONE_LINER),
        (Placement.SUPPORT, Treatment.SUPPORT_ONLY),
        (Placement.DROP, Treatment.HIDDEN),
    ],
)
def test_valid_placement_treatment_pairs(
    placement: Placement,
    treatment: Treatment,
) -> None:
    updates = {"support_for_story_id": "target"} if placement is Placement.SUPPORT else {}
    assert decision("story", placement, treatment, **updates).treatment is treatment


def test_invalid_placement_treatment_pair_is_rejected() -> None:
    with pytest.raises(ValidationError, match="invalid for placement"):
        decision("story", Placement.DROP, Treatment.SHORT_NEWS)


def test_retained_evidence_requires_named_trend_link() -> None:
    with pytest.raises(ValidationError, match="trend link"):
        decision("story", retain_for_trends=True)
    retained = decision(
        "story",
        Placement.DROP,
        Treatment.HIDDEN,
        evidence_value=4,
        retain_for_trends=True,
        trend_links=["agent-policy-friction"],
    )
    assert retained.evidence_value == 4


def test_verified_fact_requires_input_evidence_but_not_independent_reproduction() -> None:
    source_story = story("story").model_copy(update={"source_refs": []})
    batch = EditorialDecisionBatch(
        decisions=[decision("story", fact_status=FactStatus.VERIFIED_FACT)]
    )
    with pytest.raises(ValueError, match="input source evidence"):
        validate_editorial_batch(batch, [source_story])

    official_story = story("story")
    assert validate_editorial_batch(batch, [official_story]) == batch

    verified_story = story("story", independently_verified=True)
    assert validate_editorial_batch(batch, [verified_story]) == batch


@pytest.mark.parametrize("objective_change", ["官方修改 API 价格。", "官方修改开源许可证。"])
def test_official_objective_change_can_be_verified_fact(objective_change: str) -> None:
    source_story = story("story").model_copy(update={"facts": [objective_change]})
    batch = EditorialDecisionBatch(
        decisions=[decision("story", fact_status=FactStatus.VERIFIED_FACT)]
    )
    assert validate_editorial_batch(batch, [source_story]) == batch


def test_market_source_can_verify_objective_numbers() -> None:
    source_story = story("market")
    market_ref = source_story.source_refs[0].model_copy(
        update={
            "source_name": "Reliable market data",
            "source_type": "market",
            "source_role": SourceRole.EDITORIAL,
            "statement_type": StatementType.UNKNOWN,
        }
    )
    source_story = source_story.model_copy(
        update={"source_refs": [market_ref], "facts": ["股价收盘上涨 14%。"]}
    )
    batch = EditorialDecisionBatch(
        decisions=[decision("market", fact_status=FactStatus.VERIFIED_FACT)]
    )
    assert validate_editorial_batch(batch, [source_story]) == batch


@pytest.mark.parametrize("target_placement", [Placement.SUPPORT, Placement.DROP])
def test_support_cannot_target_support_or_drop(target_placement: Placement) -> None:
    target_treatment = (
        Treatment.SUPPORT_ONLY if target_placement is Placement.SUPPORT else Treatment.HIDDEN
    )
    target_updates = (
        {"support_for_story_id": "primary"}
        if target_placement is Placement.SUPPORT
        else {}
    )
    batch = EditorialDecisionBatch(
        decisions=[
            decision(
                "support",
                Placement.SUPPORT,
                Treatment.SUPPORT_ONLY,
                support_for_story_id="target",
            ),
            decision("target", target_placement, target_treatment, **target_updates),
            decision("primary"),
        ]
    )
    with pytest.raises(ValueError, match="SUPPORT or DROP"):
        validate_editorial_batch(
            batch,
            [story("support"), story("target"), story("primary")],
        )


def test_batch_requires_exactly_one_decision_per_story() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        validate_editorial_batch(
            EditorialDecisionBatch(decisions=[decision("one")]),
            [story("one"), story("two")],
        )


def test_support_target_must_exist_and_cannot_reference_itself() -> None:
    unknown = EditorialDecisionBatch(
        decisions=[
            decision(
                "support",
                Placement.SUPPORT,
                Treatment.SUPPORT_ONLY,
                support_for_story_id="missing",
            )
        ]
    )
    with pytest.raises(ValueError, match="must exist"):
        validate_editorial_batch(unknown, [story("support")])

    self_reference = EditorialDecisionBatch(
        decisions=[
            decision(
                "support",
                Placement.SUPPORT,
                Treatment.SUPPORT_ONLY,
                support_for_story_id="support",
            )
        ]
    )
    with pytest.raises(ValueError, match="itself"):
        validate_editorial_batch(self_reference, [story("support")])


def test_reader_selection_is_deterministic_and_never_publishes_support_or_drop() -> None:
    stories = [story("later"), story("earlier"), story("top"), story("drop"), story("support")]
    decisions = [
        decision("later", reader_value=3),
        decision("earlier", reader_value=3),
        decision("top", Placement.TOP, Treatment.SHORT_NEWS, reader_value=4),
        decision("drop", Placement.DROP, Treatment.HIDDEN),
        decision(
            "support",
            Placement.SUPPORT,
            Treatment.SUPPORT_ONLY,
            support_for_story_id="top",
        ),
    ]
    selection = select_reader_stories(stories, decisions)
    assert selection.top_story_ids == ["top"]
    assert selection.other_reading_story_ids == ["later", "earlier"]
    assert selection.support_by_story_id == {"top": ["support"]}
    assert "drop" not in selection.visible_story_ids
    assert "support" not in selection.visible_story_ids

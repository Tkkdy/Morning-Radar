from datetime import UTC, datetime

import pytest

from morning_radar.ai.models import BriefDraft, DirectionObservation, MergedStoryDraft
from morning_radar.ai.output_validation import (
    is_suspicious_english_prose,
    validate_direction_evidence,
    validate_editorial_grounding,
    validate_simplified_chinese_output,
)
from morning_radar.models import Signal, SignalType, Story

NOW = datetime(2026, 8, 9, tzinfo=UTC)


def story() -> Story:
    return Story(
        id="story-openai",
        canonical_title="OpenAI 发布新模型",
        category="ai_and_open_source",
        updated_at=NOW,
        source_item_ids=["item-openai"],
        source_urls=["https://example.com/openai"],
        primary_source_url="https://example.com/openai",
        entity_names=["OpenAI"],
        product_names=["GPT-5.6"],
        topic_names=["ai_models"],
        facts=["OpenAI 发布了新模型。"],
        relevance_score=0.9,
        importance_score=0.8,
        novelty_score=0.8,
        credibility_score=0.9,
    )


def signal(signal_id: str, story_ids: list[str]) -> Signal:
    return Signal(
        id=signal_id,
        signal_type=SignalType.TOPIC_HEATING,
        topic="ai_models",
        window_days=3,
        supporting_story_ids=story_ids,
        supporting_source_count=2,
        supporting_company_count=1,
        strength=0.8,
        explanation="模型发布证据连续出现。",
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    "value",
    [
        "The article was published on August 9, 2026 and describes the product launch.",
        "Oracle bans AI-generated code from the next major OpenJDK release process.",
        "The company published [a detailed article] about the new product launch today.",
        "The article was published today; the author explains the new model in detail.",
        "The authors return to the main topic and explain the new model in detail.",
    ],
)
def test_obvious_english_narrative_is_suspicious(value: str) -> None:
    assert is_suspicious_english_prose(value)


@pytest.mark.parametrize(
    "value",
    [
        "OpenAI GPT-5.6 API",
        "Claude Code",
        "OpenAI 的 GPT-5.6 API 现在支持 MCP 工具调用，并改善 Claude Code 集成。",
        "https://example.com/a/long/path/with/english/words",
        "`def process(items: list[RawItem]) -> list[RawItem]: return items`",
        "def process(items: list[RawItem]) -> list[RawItem]: return items",
        "const result = items.map((item) => item.value); return result;",
    ],
)
def test_proper_nouns_technical_chinese_urls_and_code_are_allowed(value: str) -> None:
    assert not is_suspicious_english_prose(value)


def test_language_guard_checks_story_narratives() -> None:
    draft = MergedStoryDraft(
        same_event=True,
        canonical_title="OpenAI 发布新模型",
        category="ai_and_open_source",
        facts=["The author published a detailed article about the new model today."],
    )

    with pytest.raises(ValueError, match="English prose"):
        validate_simplified_chinese_output(draft)


def test_direction_evidence_must_belong_to_one_input_signal() -> None:
    signals = [
        signal("one", ["story-1", "story-2"]),
        signal("two", ["story-3", "story-4"]),
    ]

    with pytest.raises(ValueError, match="one input Signal"):
        validate_direction_evidence(
            DirectionObservation(
                observation="两个无关方向被错误拼接。",
                evidence_story_ids=["story-1", "story-3"],
            ),
            signals,
        )
    with pytest.raises(ValueError, match="must not claim evidence"):
        validate_direction_evidence(
            DirectionObservation(observation=None, evidence_story_ids=["story-1"]),
            signals,
        )


def test_direction_evidence_accepts_two_stories_from_one_signal() -> None:
    evidence = signal("one", ["story-1", "story-2"])

    validate_direction_evidence(
        DirectionObservation(
            observation="同一模型方向获得连续证据。",
            evidence_story_ids=["story-1", "story-2"],
        ),
        [evidence],
    )


def test_editorial_extensions_require_a_specific_input_anchor() -> None:
    source_story = story()

    with pytest.raises(ValueError, match="concrete input"):
        validate_editorial_grounding(
            BriefDraft(items=[], watch_next=["继续关注 AI 行业发展。"]),
            [source_story],
            [],
        )
    validate_editorial_grounding(
        BriefDraft(
            items=[],
            watch_next=["观察 OpenAI 是否公布 GPT-5.6 的开发者开放时间表。"],
            cognitive_extension="OpenAI 的模型发布会如何影响现有 API 集成？",
        ),
        [source_story],
        [],
    )


@pytest.mark.parametrize(
    ("anchor", "narrative"),
    [
        ("Meta", "Watch whether metadata changes after today's release."),
        ("Bee", "Watch whether the team has been changing its strategy."),
    ],
)
def test_editorial_grounding_rejects_latin_anchor_substrings(
    anchor: str,
    narrative: str,
) -> None:
    source_story = story().model_copy(
        update={"entity_names": [anchor], "product_names": [], "topic_names": []}
    )

    with pytest.raises(ValueError, match="concrete input"):
        validate_editorial_grounding(
            BriefDraft(items=[], watch_next=[narrative]),
            [source_story],
            [],
        )


@pytest.mark.parametrize(
    "anchor",
    ["Meta", "Bee", "OpenAI", "GPT-5.6", "Claude Code", "\u901a\u4e49\u5343\u95ee"],
)
def test_editorial_grounding_accepts_bounded_and_chinese_anchors(anchor: str) -> None:
    source_story = story().model_copy(
        update={"entity_names": [], "product_names": [anchor], "topic_names": []}
    )

    validate_editorial_grounding(
        BriefDraft(items=[], watch_next=[f"Watch today's concrete {anchor} changes."]),
        [source_story],
        [],
    )

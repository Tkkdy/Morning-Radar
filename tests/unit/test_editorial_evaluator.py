from datetime import UTC, date, datetime

from morning_radar.ai import FakeAIProvider
from morning_radar.ai.models import BriefDraft, GeneratedBriefItem
from morning_radar.briefing import BriefLimits, generate_daily_brief
from morning_radar.editorial.evaluator import evaluate_editorial
from morning_radar.editorial.models import (
    EditorialDecision,
    EditorialDecisionBatch,
    FactStatus,
    Placement,
    Treatment,
)
from morning_radar.models import Story

NOW = datetime(2026, 8, 23, tzinfo=UTC)
TODAY = date(2026, 8, 23)


def story(story_id: str, *, category: str = "ai_and_open_source") -> Story:
    url = f"https://example.com/{story_id}"
    return Story(
        id=story_id,
        canonical_title=story_id,
        category=category,
        published_at=NOW,
        updated_at=NOW,
        source_item_ids=[f"raw-{story_id}"],
        source_urls=[url],
        primary_source_url=url,
        facts=[f"{story_id} 发生变化。"],
        analysis=[f"{story_id} 影响开发流程。"],
        relevance_score=0.1,
        importance_score=0.1,
        novelty_score=0.1,
        credibility_score=0.1,
    )


def decision(
    story_id: str,
    placement: Placement,
    treatment: Treatment,
    *,
    reader_value: int,
    support_for_story_id: str | None = None,
) -> EditorialDecision:
    return EditorialDecision(
        story_id=story_id,
        placement=placement,
        treatment=treatment,
        reader_value=reader_value,
        evidence_value=2,
        fact_status=FactStatus.CLAIM,
        editorial_confidence=0.8,
        news_delta=f"{story_id} 今天发生变化。",
        why_now="现在值得记录。",
        decision_reasons=["minor_news_delta"],
        retain_for_trends=False,
        support_for_story_id=support_for_story_id,
        uncertainty="仅依据输入来源。",
    )


class ScriptedEditorialProvider(FakeAIProvider):
    def __init__(self, decisions: list[EditorialDecision]) -> None:
        self.decisions = decisions
        self.editorial_calls = 0

    def evaluate_editorial(self, stories: list[Story]) -> EditorialDecisionBatch:
        del stories
        self.editorial_calls += 1
        return EditorialDecisionBatch(decisions=self.decisions)


class AttachedSupportProvider(ScriptedEditorialProvider):
    def write_brief(self, stories, signals, editorial_decisions=None):
        del signals, editorial_decisions
        by_id = {item.id: item for item in stories}
        top = by_id["top"]
        support = by_id["support"]
        return BriefDraft(
            items=[
                GeneratedBriefItem(
                    story_ids=[top.id, support.id],
                    section="top_stories",
                    title=top.canonical_title,
                    what_happened=top.facts[0],
                    why_it_matters=top.analysis[0],
                    source_urls=[*top.source_urls, *support.source_urls],
                )
            ]
        )


class ReversedBriefProvider(ScriptedEditorialProvider):
    def write_brief(self, stories, signals, editorial_decisions=None):
        del signals, editorial_decisions
        return BriefDraft(
            items=[
                GeneratedBriefItem(
                    story_ids=[item.id],
                    section=item.category,
                    title=item.canonical_title,
                    what_happened=item.facts[0],
                    why_it_matters=item.analysis[0],
                    source_urls=item.source_urls,
                )
                for item in reversed(stories)
            ]
        )


def evaluate(stories: list[Story], provider, *, shadow_mode: bool = False, maximum=20):
    return evaluate_editorial(
        stories,
        provider=provider,
        current_date=TODAY,
        generated_at=NOW,
        enabled=True,
        shadow_mode=shadow_mode,
        profile_version="1.0",
        maximum_stories=maximum,
    )


def generate(stories: list[Story], provider, editorial_result=None):
    return generate_daily_brief(
        brief_date=TODAY,
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=provider,
        limits=BriefLimits(maximum_items=12),
        enabled_sections={},
        run_stats={},
        relevance_threshold=0.55,
        importance_threshold=0.60,
        editorial_result=editorial_result,
    )


def test_story_limit_degrades_without_silent_truncation_or_provider_call() -> None:
    stories = [story("one"), story("two")]
    provider = ScriptedEditorialProvider([])
    result = evaluate(stories, provider, maximum=1)
    assert result.daily.degraded is True
    assert result.daily.degradation_reason == "story_limit_exceeded"
    assert result.daily.decisions == []
    assert provider.editorial_calls == 0


def test_fake_provider_keeps_official_benchmark_as_claim() -> None:
    benchmark = story("official-benchmark").model_copy(
        update={"facts": ["厂商公布 benchmark，声称 Coding 能力领先。"]}
    )
    result = evaluate([benchmark], FakeAIProvider(), shadow_mode=True)
    assert result.daily.decisions[0].fact_status is FactStatus.CLAIM


def test_partial_batch_degrades_and_preserves_legacy_brief() -> None:
    stories = [story("one"), story("two")]
    provider = ScriptedEditorialProvider(
        [decision("one", Placement.DROP, Treatment.HIDDEN, reader_value=0)]
    )
    degraded = evaluate(stories, provider)
    assert degraded.daily.degraded is True
    assert generate(stories, provider, degraded) == generate(stories, provider)


def test_active_mode_maps_placements_without_legacy_score_filtering() -> None:
    stories = [
        story("news"),
        story("story", category="developer_discussions"),
        story("top"),
        story("one"),
        story("support"),
        story("drop"),
    ]
    decisions = [
        decision("top", Placement.TOP, Treatment.SHORT_NEWS, reader_value=4),
        decision("story", Placement.STORY, Treatment.DEEP_STORY, reader_value=3),
        decision("news", Placement.NEWS, Treatment.SHORT_NEWS, reader_value=3),
        decision("one", Placement.ONE_LINER, Treatment.ONE_LINER, reader_value=2),
        decision(
            "support",
            Placement.SUPPORT,
            Treatment.SUPPORT_ONLY,
            reader_value=0,
            support_for_story_id="top",
        ),
        decision("drop", Placement.DROP, Treatment.HIDDEN, reader_value=0),
    ]
    provider = ScriptedEditorialProvider(decisions)
    result = generate(stories, provider, evaluate(stories, provider))
    assert [item.story_ids for item in result.top_stories] == [["top"]]
    assert [item.story_ids for item in result.developer_discussions] == [["story"]]
    assert [item.story_ids for item in result.other_reading] == [["news"], ["one"]]
    displayed = {
        story_id
        for item in [
            *result.top_stories,
            *result.developer_discussions,
            *result.other_reading,
        ]
        for story_id in item.story_ids
    }
    assert "drop" not in displayed
    assert "support" not in displayed


def test_support_is_only_published_attached_to_its_target() -> None:
    stories = [story("top"), story("support")]
    decisions = [
        decision("top", Placement.TOP, Treatment.DEEP_STORY, reader_value=4),
        decision(
            "support",
            Placement.SUPPORT,
            Treatment.SUPPORT_ONLY,
            reader_value=0,
            support_for_story_id="top",
        ),
    ]
    provider = AttachedSupportProvider(decisions)
    result = generate(stories, provider, evaluate(stories, provider))
    assert [item.story_ids for item in result.top_stories] == [["top", "support"]]


def test_shadow_mode_keeps_reader_output_unchanged() -> None:
    stories = [story("one"), story("two")]
    decisions = [
        decision("one", Placement.DROP, Treatment.HIDDEN, reader_value=0),
        decision("two", Placement.ONE_LINER, Treatment.ONE_LINER, reader_value=1),
    ]
    provider = ScriptedEditorialProvider(decisions)
    shadow = evaluate(stories, provider, shadow_mode=True)
    assert generate(stories, provider, shadow) == generate(stories, provider)


def test_active_output_reapplies_deterministic_reader_order() -> None:
    stories = [story("original-first"), story("higher-value"), story("original-second")]
    decisions = [
        decision(
            "original-first",
            Placement.NEWS,
            Treatment.SHORT_NEWS,
            reader_value=2,
        ),
        decision(
            "higher-value",
            Placement.NEWS,
            Treatment.SHORT_NEWS,
            reader_value=4,
        ),
        decision(
            "original-second",
            Placement.NEWS,
            Treatment.SHORT_NEWS,
            reader_value=2,
        ),
    ]
    provider = ReversedBriefProvider(decisions)
    result = generate(stories, provider, evaluate(stories, provider))
    assert [item.story_ids for item in result.other_reading] == [
        ["higher-value"],
        ["original-first"],
        ["original-second"],
    ]

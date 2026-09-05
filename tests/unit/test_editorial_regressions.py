from datetime import UTC, date, datetime

from morning_radar.ai import AIBudget, FakeAIProvider
from morning_radar.evaluation.legacy import build_stories
from morning_radar.models import BriefItem, DailyBrief, RawItem
from morning_radar.pipeline import _displayed_item_counts
from morning_radar.processing import filter_story_candidate_inputs

NOW = datetime(2026, 8, 9, tzinfo=UTC)


def item(
    item_id: str,
    *,
    source_type: str,
    official: bool = False,
    change_percent: float | None = None,
) -> RawItem:
    metadata: dict[str, object] = {"official": official, "priority": "high"}
    if change_percent is not None:
        metadata["change_percent"] = change_percent
    return RawItem(
        id=item_id,
        title=f"Unique event {item_id}",
        url=f"https://example.com/{item_id}",
        source_name=item_id,
        source_type=source_type,
        published_at=NOW,
        fetched_at=NOW,
        summary=f"已验证事件 {item_id}",
        metadata=metadata,
    )


class CountingProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.budget = AIBudget(100, 100_000, 40)
        self.classified_source_types: set[str] = set()

    def classify_items(self, items):
        self.budget.consume("classification", item_count=len(items))
        self.classified_source_types = {value.source_type for value in items}
        return super().classify_items(items)

    def merge_story(self, items):
        self.budget.consume("merge", item_count=len(items))
        return super().merge_story(items)

    def score_story(self, story):
        self.budget.consume("score", item_count=1)
        return super().score_story(story)


def test_noisy_market_day_reduces_ai_work_without_losing_source_lanes() -> None:
    valuable = [
        item("official", source_type="rss", official=True),
        item("github", source_type="github", official=True),
        item("secondary", source_type="rss"),
        item("hn", source_type="hacker_news"),
    ]
    routine_market = [
        item(
            f"market-{index}",
            source_type="market",
            change_percent=change,
        )
        for index, change in enumerate(
            (0.0044, 0.0283, 0.0037, 0.0082, 0.0029, -0.0121, 0.0003)
        )
    ]
    noisy = [*valuable, *routine_market]
    before_provider = CountingProvider()
    build_stories(noisy, provider=before_provider, now=NOW, maximum_ai_items=40)

    gated, suppressed = filter_story_candidate_inputs(
        noisy,
        market_movement_threshold=0.03,
    )
    after_provider = CountingProvider()
    build_stories(gated, provider=after_provider, now=NOW, maximum_ai_items=40)

    assert suppressed == 7
    assert after_provider.budget.calls_used < before_provider.budget.calls_used
    assert after_provider.classified_source_types == {
        "rss",
        "github",
        "hacker_news",
    }
    assert len(gated) == len(valuable)


def brief_item(item_id: str, section: str) -> BriefItem:
    return BriefItem(
        id=item_id,
        section=section,
        title=item_id,
        what_happened="发生了已验证事件。",
        why_it_matters="该事件值得关注。",
        source_urls=[f"https://example.com/{item_id}"],
        story_ids=[f"story-{item_id}"],
    )


def test_displayed_count_equals_main_plus_other_reading() -> None:
    brief = DailyBrief(
        date=date(2026, 8, 9),
        timezone="Asia/Singapore",
        generated_at=NOW,
        top_stories=[brief_item("main-1", "top_stories")],
        ai_and_open_source=[brief_item("main-2", "ai_and_open_source")],
        other_reading=[
            brief_item("other-1", "other_reading"),
            brief_item("other-2", "other_reading"),
        ],
    )

    assert _displayed_item_counts(brief) == (2, 2, 4)

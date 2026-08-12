from datetime import UTC, date, datetime

from morning_radar.ai import FakeAIProvider
from morning_radar.models import (
    GitHubSnapshot,
    MarketSnapshot,
    SignalType,
    Story,
    StoryStatus,
)
from morning_radar.trends.detector import (
    TrendDetector,
    detect_github_growth,
    detect_market_attention,
    detect_multi_company_direction,
    detect_product_transitions,
    detect_topic_heating,
)

NOW = datetime(2026, 7, 23, 1, tzinfo=UTC)


def story(
    story_id: str,
    day: date,
    *,
    topic: str = "ai_coding",
    source: str | None = None,
    companies: list[str] | None = None,
    products: list[str] | None = None,
    status: StoryStatus = StoryStatus.UNKNOWN,
) -> Story:
    url = source or f"https://example.com/{story_id}"
    return Story(
        id=story_id,
        canonical_title=f"{products[0] if products else topic} update",
        category="ai_and_open_source",
        entity_names=companies or [],
        product_names=products or [],
        topic_names=[topic],
        published_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
        updated_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
        source_item_ids=[f"item-{story_id}"],
        source_urls=[url],
        primary_source_url=url,
        facts=["Structured evidence"],
        relevance_score=0.9,
        importance_score=0.8,
        novelty_score=0.8,
        credibility_score=0.9,
        status=status,
    )


def test_topic_heating_requires_three_consecutive_days_and_multiple_sources() -> None:
    history = {
        date(2026, 7, 21): [story("one", date(2026, 7, 21))],
        date(2026, 7, 22): [story("two", date(2026, 7, 22))],
        date(2026, 7, 23): [story("three", date(2026, 7, 23))],
    }

    signals = detect_topic_heating(
        history,
        current_date=date(2026, 7, 23),
        now=NOW,
    )

    assert len(signals) == 1
    assert signals[0].supporting_source_count == 3


def test_repeated_single_source_does_not_create_topic_heating() -> None:
    repeated_url = "https://example.com/same-source"
    history = {
        day: [story(str(day), day, source=repeated_url)]
        for day in (date(2026, 7, 21), date(2026, 7, 22), date(2026, 7, 23))
    }

    assert not detect_topic_heating(
        history,
        current_date=date(2026, 7, 23),
        now=NOW,
    )


def test_multi_company_direction_requires_two_companies_and_sources() -> None:
    stories = [
        story("one", date(2026, 7, 23), companies=["Alpha"]),
        story("two", date(2026, 7, 23), companies=["Beta"]),
    ]

    signals = detect_multi_company_direction(
        stories,
        now=NOW,
        company_names={"Alpha", "Beta"},
    )

    assert len(signals) == 1
    assert signals[0].supporting_company_count == 2


def test_multi_company_direction_does_not_treat_generic_entities_as_companies() -> None:
    stories = [
        story("one", date(2026, 7, 23), companies=["Startup"]),
        story("two", date(2026, 7, 23), companies=["Education"]),
    ]

    assert not detect_multi_company_direction(
        stories,
        now=NOW,
        company_names={"Amazon", "Microsoft"},
    )


def test_github_growth_requires_positive_threshold_and_matching_story() -> None:
    base = GitHubSnapshot(
        date=date(2026, 7, 22),
        captured_at=NOW,
        repository="example/agent",
        stars=100,
        forks=1,
        open_issues=1,
        updated_at=NOW,
    )
    current = base.model_copy(update={"date": date(2026, 7, 23), "stars": 110})
    matching = story(
        "release",
        date(2026, 7, 23),
        products=["example/agent"],
    )

    assert len(
        detect_github_growth([base, current], [matching], threshold=0.05, now=NOW)
    ) == 1
    assert not detect_github_growth(
        [base, current.model_copy(update={"stars": 103})],
        [matching],
        threshold=0.05,
        now=NOW,
    )


def test_product_status_only_moves_forward_with_history() -> None:
    announced = story(
        "old",
        date(2026, 7, 22),
        products=["Example Model"],
        status=StoryStatus.ANNOUNCED,
    )
    available = story(
        "new",
        date(2026, 7, 23),
        products=["Example Model"],
        status=StoryStatus.AVAILABLE,
    )
    history = {
        date(2026, 7, 22): [announced],
        date(2026, 7, 23): [available],
    }

    assert len(
        detect_product_transitions(
            history,
            current_date=date(2026, 7, 23),
            now=NOW,
        )
    ) == 1
    history[date(2026, 7, 23)] = [
        available.model_copy(update={"status": StoryStatus.RUMOR})
    ]
    assert not detect_product_transitions(
        history,
        current_date=date(2026, 7, 23),
        now=NOW,
    )


def test_trend_detector_does_not_publish_product_name_only_transition_as_truth() -> None:
    previous = story(
        "old",
        date(2026, 7, 22),
        products=["Shared Product"],
        status=StoryStatus.RUMOR,
    )
    unrelated = story(
        "new",
        date(2026, 7, 23),
        products=["Shared Product"],
        status=StoryStatus.AVAILABLE,
    )

    signals = TrendDetector(
        github_threshold=0.05,
        market_threshold=0.03,
    ).detect(
        story_history={
            date(2026, 7, 22): [previous],
            date(2026, 7, 23): [unrelated],
        },
        github_snapshots=[],
        market_snapshots=[],
        current_date=date(2026, 7, 23),
        now=NOW,
    )

    assert all(signal.signal_type is not SignalType.PRODUCT_STATUS_TRANSITION for signal in signals)


def test_market_attention_requires_related_story_and_keeps_causality_uncertain() -> None:
    snapshot = MarketSnapshot(
        date=date(2026, 7, 23),
        captured_at=NOW,
        company="NVIDIA",
        ticker="NVDA",
        trading_date=date(2026, 7, 22),
        close=105,
        previous_close=100,
        change_percent=0.05,
    )
    related = story(
        "nvidia",
        date(2026, 7, 23),
        companies=["NVIDIA"],
    )

    signals = detect_market_attention(
        [snapshot],
        [related],
        threshold=0.03,
        now=NOW,
    )

    assert len(signals) == 1
    assert "不构成因果确认" in signals[0].explanation
    assert not detect_market_attention([snapshot], [], threshold=0.03, now=NOW)


def test_direction_observation_is_empty_without_signals() -> None:
    assert FakeAIProvider().write_direction_observation([]).observation is None


def test_signal_id_uses_singapore_product_date_across_utc_boundary() -> None:
    stories = [
        story("one", date(2026, 7, 29), companies=["Alpha"]),
        story("two", date(2026, 7, 29), companies=["Beta"]),
    ]
    before_utc_midnight = datetime(2026, 7, 28, 23, 37, tzinfo=UTC)
    after_utc_midnight = datetime(2026, 7, 29, 0, 5, tzinfo=UTC)

    before = detect_multi_company_direction(
        stories,
        now=before_utc_midnight,
        company_names={"Alpha", "Beta"},
    )
    after = detect_multi_company_direction(
        stories,
        now=after_utc_midnight,
        company_names={"Alpha", "Beta"},
    )

    assert before[0].id == after[0].id


def test_repeated_market_trading_day_does_not_create_a_new_attention_signal() -> None:
    old_capture = MarketSnapshot(
        date=date(2026, 7, 25),
        captured_at=datetime(2026, 7, 24, 23, 37, tzinfo=UTC),
        company="NVIDIA",
        ticker="NVDA",
        trading_date=date(2026, 7, 24),
        close=105,
        previous_close=100,
        change_percent=0.05,
    )
    repeated = old_capture.model_copy(
        update={
            "date": date(2026, 7, 27),
            "captured_at": datetime(2026, 7, 26, 23, 37, tzinfo=UTC),
        }
    )
    related = story(
        "nvidia-repeat",
        date(2026, 7, 27),
        companies=["NVIDIA"],
    )

    assert not detect_market_attention(
        [old_capture, repeated],
        [related],
        threshold=0.03,
        now=datetime(2026, 7, 26, 23, 37, tzinfo=UTC),
    )


def test_repeated_story_identity_does_not_manufacture_topic_heating() -> None:
    repeated = story("same-market-close", date(2026, 7, 25))
    history = {
        day: [
            repeated.model_copy(
                update={
                    "updated_at": datetime.combine(
                        day,
                        datetime.min.time(),
                        tzinfo=UTC,
                    )
                }
            )
        ]
        for day in (date(2026, 7, 25), date(2026, 7, 26), date(2026, 7, 27))
    }

    assert not detect_topic_heating(
        history,
        current_date=date(2026, 7, 27),
        now=datetime(2026, 7, 26, 23, 37, tzinfo=UTC),
    )

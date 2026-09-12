from datetime import UTC, datetime
from pathlib import Path

import httpx

from morning_radar.collectors.http import HttpClient
from morning_radar.collectors.official_listing import OfficialListingCollector
from morning_radar.collectors.orchestrator import collect_available
from morning_radar.intake.candidates import select_process_candidates
from morning_radar.intake.checkpoint import build_intake_records
from morning_radar.models import RawItem, SourceRole, StatementType
from morning_radar.processing.deduplicate import deduplicate_items
from morning_radar.settings import LabUpdateRule, LabWatchConfig, SourceConfig

NOW = datetime(2026, 9, 12, tzinfo=UTC)
FIXTURES = Path("tests/fixtures/coverage")


def _collect(source: SourceConfig, fixture_name: str):
    body = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    http = HttpClient(client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, text=body)
    )))
    return OfficialListingCollector(http=http, source=source, now=NOW).collect()


def test_anthropic_listing_preserves_observed_link_and_day_precision() -> None:
    source = SourceConfig(
        id="anthropic_threat_intelligence", name="Anthropic Threat Intelligence",
        type="official_listing", url="https://www.anthropic.com/threat-intelligence",
        priority="high", official=True,
    )
    item = _collect(source, "anthropic_listing.html")[0]

    assert item.url == "https://www.anthropic.com/threat-intelligence-report-september-2026"
    assert item.metadata["source_date"] == "2026-09-10"
    assert item.metadata["date_precision"] == "day"
    assert item.published_at is None
    assert "malicious use" in item.content_excerpt


def test_same_official_event_deduplicates_across_anthropic_entries() -> None:
    news = SourceConfig(
        id="anthropic_news", name="Anthropic News", type="official_listing",
        url="https://www.anthropic.com/news", priority="high", official=True,
    )
    threat = news.model_copy(update={
        "id": "anthropic_threat_intelligence",
        "name": "Anthropic Threat Intelligence",
        "url": "https://www.anthropic.com/threat-intelligence",
    })

    assert len(deduplicate_items([
        *_collect(news, "anthropic_listing.html"),
        *_collect(threat, "anthropic_listing.html"),
    ])) == 1


def test_xai_listing_keeps_distinct_events_and_missing_date() -> None:
    source = SourceConfig(
        id="xai_news", name="xAI News", type="official_listing", url="https://x.ai/news",
        priority="high", official=True,
    )
    items = _collect(source, "xai_listing.html")
    by_title = {item.title: item for item in items}

    assert {item.url for item in items} == {
        "https://x.ai/news/grok-bot-more-plans", "https://x.ai/news/grok-4-7-delay"
    }
    assert (
        by_title["Grok Bot is now included with more plans"].metadata["source_date"]
        == "2026-08-26"
    )
    assert "source_date" not in by_title["Grok 4.7 release delayed"].metadata


def test_invalid_listing_is_source_scoped_and_records_audit() -> None:
    source = SourceConfig(
        id="xai_news", name="xAI News", type="official_listing", url="https://x.ai/news",
        priority="high", official=True,
    )
    http = HttpClient(client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, text="<main>no article cards</main>")
    )))
    collector = OfficialListingCollector(http=http, source=source, now=NOW)

    result = collect_available([collector])

    assert result.items == []
    assert result.failures == {collector.name: "RuntimeError"}
    assert collector.discovery_audit[-1]["reason"] == "parse_no_entries"


def test_update_rules_protect_reports_plans_and_delays_without_promoting_noise() -> None:
    lab = LabWatchConfig(id="xai", aliases=["xAI", "Grok"], official_source_id="xai_news")
    rules = [
        LabUpdateRule(
            rule_id="plan_availability",
            include=[r"\b(included|available)\b.*\bplans?\b"],
        ),
        LabUpdateRule(rule_id="announced_or_delayed", include=[r"\b(delayed|announced)\b"]),
    ]
    items = [
        RawItem(
            id="plan", title="Grok Bot included with more plans", url="https://x.ai/news/plan",
            source_name="xAI News", source_type="official_listing", fetched_at=NOW,
            source_role=SourceRole.OFFICIAL_PRIMARY,
            statement_type=StatementType.FACTUAL_ANNOUNCEMENT,
            metadata={"source_id": "xai_news"},
        ),
        RawItem(
            id="delay", title="Grok 4.7 release delayed", url="https://example.test/delay",
            source_name="Archive", source_type="rss", fetched_at=NOW,
            source_role=SourceRole.COMMUNITY_DISCOVERY,
            statement_type=StatementType.UNVERIFIED_LEAD,
        ),
        RawItem(
            id="noise", title="Grok tutorial for beginners", url="https://example.test/tutorial",
            source_name="Blog", source_type="rss", fetched_at=NOW,
            source_role=SourceRole.EDITORIAL,
            statement_type=StatementType.UNVERIFIED_LEAD,
        ),
    ]
    selection = select_process_candidates(
        fresh=build_intake_records(items, batch_id="listing", run_id="listing"), recovery=[],
        maximum_items=3, reserved_recovery_slots=0, reserved_fresh_slots=2,
        labs=[lab], update_rules=rules,
    )

    assert {record.item.id for record in selection.records[:2]} == {"plan", "delay"}
    matched_rules = {value["rule_id"] for value in selection.candidate_matches.values()}
    assert {"plan_availability", "announced_or_delayed"} <= matched_rules
    assert items[1].statement_type is StatementType.UNVERIFIED_LEAD

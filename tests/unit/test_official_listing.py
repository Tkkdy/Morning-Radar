from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from morning_radar.collectors.http import HttpClient
from morning_radar.collectors.official_listing import OfficialListingCollector
from morning_radar.collectors.orchestrator import CollectionResult, collect_available
from morning_radar.intake.candidates import select_process_candidates
from morning_radar.intake.checkpoint import build_intake_records, load_checkpoint_by_batch_id
from morning_radar.intake.freshness import late_discovery_reason
from morning_radar.intake.ledger import ProcessingLedgerStore
from morning_radar.intake.models import ProcessingStatus, ReasonCode
from morning_radar.intake.service import collect_intake, prepare_process
from morning_radar.models import RawItem, SourceRole, StatementType
from morning_radar.processing.deduplicate import deduplicate_items
from morning_radar.settings import (
    AppConfig,
    LabUpdateRule,
    LabWatchConfig,
    SourceConfig,
    load_model,
)
from tests.unit.test_phase1_patch import copy_project

NOW = datetime(2026, 9, 12, tzinfo=UTC)
FIXTURES = Path("tests/fixtures/coverage")


def _collect(source: SourceConfig, fixture_name: str, *, now: datetime = NOW):
    body = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    http = HttpClient(client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, text=body)
    )))
    return OfficialListingCollector(http=http, source=source, now=now).collect()


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
    delayed = by_title["Grok 4.7 release delayed"]
    assert "source_date" not in delayed.metadata
    assert delayed.metadata["event_time_unverified"] is True
    assert delayed.source_role is SourceRole.OFFICIAL_PRIMARY
    assert delayed.statement_type is StatementType.UNVERIFIED_LEAD


def test_undated_official_listing_waits_for_event_time_across_reload_and_refetch(
    tmp_path, monkeypatch
) -> None:
    project = copy_project(tmp_path)
    app = load_model(project / "config/app.yaml", AppConfig)
    source = SourceConfig(
        id="xai_news", name="xAI News", type="official_listing", url="https://x.ai/news",
        priority="high", official=True,
    )

    def fake_production(*args, **kwargs):
        clock = kwargs.get("now") or args[-1]
        delayed = next(
            item
            for item in _collect(source, "xai_listing.html", now=clock)
            if item.title == "Grok 4.7 release delayed"
        )
        return (
            CollectionResult(
                items=[delayed], raw_collected=1, after_buffer=1, after_dedup=1
            ),
            [],
            [],
        )

    monkeypatch.setattr("morning_radar.intake.service._production_collect", fake_production)
    intake = collect_intake(project, app, now=NOW)
    checkpoint = load_checkpoint_by_batch_id(project, intake.checkpoint.manifest.batch_id)
    record = checkpoint.items[0]
    assert record.item.url == "https://x.ai/news/grok-4-7-delay"
    assert record.item.source_role is SourceRole.OFFICIAL_PRIMARY
    assert record.item.statement_type is StatementType.UNVERIFIED_LEAD
    assert record.item.published_at is None
    assert record.item.fetched_at == NOW
    assert (
        late_discovery_reason(record, now=NOW, normal_hours=24, lookback_days=7)
        == "missing_event_time"
    )

    prepared = prepare_process(project, app, batch_id=checkpoint.manifest.batch_id, now=NOW)
    assert prepared.selection.records == []
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    entry = ledger.get(record.input_id, record.content_version)
    assert entry.processing is ProcessingStatus.WAITING_EVIDENCE
    assert entry.reason_code is ReasonCode.WAITING_EVIDENCE
    assert entry.candidate_diagnostics == {"selected": False, "reason": "missing_event_time"}

    next_day = NOW + timedelta(days=1)
    refetched = collect_intake(project, app, now=next_day)
    next_checkpoint = load_checkpoint_by_batch_id(project, refetched.checkpoint.manifest.batch_id)
    next_record = next_checkpoint.items[0]
    assert late_discovery_reason(
        next_record, now=next_day, normal_hours=24, lookback_days=7
    ) == "missing_event_time"
    next_prepared = prepare_process(
        project, app, batch_id=next_checkpoint.manifest.batch_id, now=next_day
    )
    assert next_prepared.selection.records == []
    reloaded = ProcessingLedgerStore(project / "data/intake/ledger.json")
    reloaded_entry = reloaded.get(next_record.input_id, next_record.content_version)
    assert reloaded_entry.first_seen_at == NOW
    assert reloaded_entry.durable_at == NOW
    assert reloaded_entry.candidate_diagnostics == {
        "selected": False,
        "reason": "missing_event_time",
    }


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

"""Historical input replay and explicitly synthetic failure/transport regressions."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from morning_radar.ai import AIBudget, AIBudgetExceeded, AIOutputError, FakeAIProvider
from morning_radar.ai.budget import AITaskPriority
from morning_radar.ai.models import ClassificationBatch, ClassifiedItem
from morning_radar.ai.usage import PriceQuote, UsageRecorder, attach_usage, usage_summary
from morning_radar.collectors.http import HttpClient
from morning_radar.collectors.official_listing import OfficialListingCollector
from morning_radar.collectors.rss import RSSCollector
from morning_radar.health import module_run, publish_health
from morning_radar.intake.candidates import select_process_candidates
from morning_radar.intake.freshness import late_discovery_reason
from morning_radar.intake.models import IntakeRecord, ProcessingStatus
from morning_radar.intake.review import review_candidates
from morning_radar.models import RawItem, SourceRole
from morning_radar.processing.grouping import group_items_by_normalized_title
from morning_radar.processing.story_builder import build_stories
from morning_radar.settings import AppConfig, SourceConfig, load_model
from tests.unit.test_ai_provider import provider as openai_provider
from tests.unit.test_ai_provider import raw_item

HISTORY = json.loads(Path("tests/fixtures/batch_a/history.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 10, tzinfo=UTC)


def test_historical_timely_capture_remains_bounded_recoverable():
    # Exact saved checkpoint records. This replays eligibility, not historical AI output.
    for case in HISTORY:
        record = IntakeRecord.model_validate(case["records"][0])
        observed = record.durable_at or record.first_seen_at
        initial = late_discovery_reason(record, now=observed, normal_hours=24, lookback_days=7)
        if initial != "fresh":
            continue
        original = record.item.model_dump()
        later = observed + timedelta(days=2)
        assert (
            late_discovery_reason(record, now=later, normal_hours=24, lookback_days=7)
            == "eligible_backlog"
        ), case["case_id"]
        assert record.item.model_dump() == original
        assert (
            late_discovery_reason(
                record, now=observed + timedelta(days=8), normal_hours=24, lookback_days=7
            )
            == "expired"
        )


def test_synthetic_timely_nonofficial_is_distinct_from_late_discovery():
    record = IntakeRecord.model_validate(HISTORY[0]["records"][0])
    record.item.source_role = SourceRole.EDITORIAL
    record.item.metadata["priority"] = "low"
    now = record.first_seen_at + timedelta(days=2)
    assert (
        late_discovery_reason(record, now=now, normal_hours=24, lookback_days=7)
        == "eligible_backlog"
    )
    record.durable_at = now
    assert late_discovery_reason(record, now=now, normal_hours=24, lookback_days=7) == "low_value"


def test_historical_textgrain_is_one_candidate_group_but_versions_stay_distinct():
    inputs = [
        RawItem.model_validate(c["raw"])
        for c in HISTORY
        if c["term"] in {"eu-text-provenance", "watermarking-chatgpts"}
    ]
    assert len(inputs) == 2
    assert len(group_items_by_normalized_title(inputs)) == 1
    # Synthetic follow-up: preserve existing version and time separation safeguards.
    later = inputs[1].model_copy(
        update={"published_at": inputs[0].published_at + timedelta(days=4)}
    )
    assert len(group_items_by_normalized_title([inputs[0], later])) == 2
    a = inputs[0].model_copy(update={"title": "OpenAI TextGrain 1 release"})
    b = inputs[1].model_copy(update={"title": "OpenAI TextGrain 2 release"})
    assert len(group_items_by_normalized_title([a, b])) == 2


@pytest.mark.parametrize("always_invalid", [False, True])
def test_synthetic_invalid_url_local_recovery_preserves_original(always_invalid):
    class InvalidURL(FakeAIProvider):
        calls = 0
        sizes = []

        def merge_story(self, items):
            self.calls += 1
            self.sizes.append(len(items[0].content_excerpt))
            if self.calls == 1 or always_invalid:
                raise AIOutputError("URL absent from verified source set")
            return super().merge_story(items)

    item = raw_item().model_copy(update={"content_excerpt": "evidence " * 300})
    snapshot = item.model_dump()
    ai = InvalidURL()
    outcomes = {}
    stories = build_stories([item], provider=ai, now=NOW, item_outcomes=outcomes)
    assert ai.calls == 2 and ai.sizes[1] == 600
    assert item.model_dump() == snapshot
    if always_invalid:
        assert stories == [] and outcomes[item.id] == "merge_failed"
    else:
        assert stories[0].source_urls == [item.url]


def test_synthetic_early_budget_cannot_consume_later_brief_and_continuity():
    budget = AIBudget(10, 1000, 20)
    budget.plan_tail(calls=3, input_characters=400)
    budget.consume("x" * 600, item_count=1, task="merge_story")
    for task in ["merge_story", "resolve_research_cases", "evaluate_editorial"]:
        with pytest.raises(AIBudgetExceeded):
            budget.consume("x", item_count=1, task=task, priority=AITaskPriority.EXPERIMENTAL)
    budget.plan_tail(calls=0, input_characters=0)
    budget.reserve_core(calls=2, input_characters=300)
    budget.consume(
        "x" * 100, item_count=1, task="resolve_continuity", priority=AITaskPriority.IMPORTANT
    )
    with pytest.raises(AIBudgetExceeded):
        budget.consume(
            "x", item_count=1, task="evaluate_editorial", priority=AITaskPriority.EXPERIMENTAL
        )
    budget.consume("x" * 300, item_count=1, task="write_brief")
    assert budget.input_characters_used == 1000


def test_synthetic_metadata_review_promotes_deferred_without_raw_loss():
    records = [IntakeRecord.model_validate(c["records"][0]) for c in HISTORY[:4]]
    # Same production evidence with synthetic relevance judgements; not an AI replay.
    selection = select_process_candidates(
        fresh=records, recovery=[], maximum_items=1, reserved_recovery_slots=0
    )
    important = selection.deferred[0].item.id
    updates = {}

    class Ledger:
        def update(self, identity, version, **kwargs):
            updates[(identity, version)] = kwargs

        def save(self):
            pass

    class Reviewer:
        def classify_items(self, items):
            assert all(i.metadata == {"review_only": True} for i in items)
            assert all(len(i.summary) <= 120 for i in items)
            return ClassificationBatch(
                items=[
                    ClassifiedItem(
                        item_id=i.id,
                        relevant=True,
                        important=i.id == important,
                        relevance_reason="synthetic",
                        importance_reason="synthetic",
                        category="ai",
                    )
                    for i in items
                ]
            )

    prepared = SimpleNamespace(selection=selection, ledger=Ledger(), process_now=NOW)
    snapshots = {r.input_id: r.item.title for r in records}
    review = review_candidates(
        prepared,
        provider=Reviewer(),
        app=load_model(Path("config/app.yaml"), AppConfig).model_copy(
            update={
                "maximum_ai_items": 1,
                "maximum_ai_calls": 10,
                "candidate_review_limit": 200,
            }
        ),
    )
    assert selection.items[0].id == important
    assert len(review.items) == 1 and len(selection.deferred) == 3
    assert all(r.item.title == snapshots[r.input_id] for r in records)
    assert sum(v["processing"] == ProcessingStatus.DEFERRED_BUDGET for v in updates.values()) == 3


def test_synthetic_stream_download_stops_at_limit():
    seen = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for i in range(100):
                seen.append(i)
                yield b"x" * 1024

    client = HttpClient(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream()))
        )
    )
    with pytest.raises(RuntimeError, match="response_too_large"):
        client.get("https://example.com/news", maximum_response_bytes=2048)
    assert len(seen) == 3 and client.request_attempts == 1


def test_synthetic_dated_anchor_listing_and_priority():
    source = SourceConfig(
        id="official",
        name="Official",
        type="official_listing",
        url="https://example.com/news",
        official=True,
        priority="high",
    )
    html = (
        '<a href="/news/release"><span class="Card_title">Release</span>'
        '<time datetime="2026-10-09">Oct 9, 2026</time></a>'
    )
    collector = OfficialListingCollector(
        source=source,
        now=NOW,
        http=HttpClient(
            client=httpx.Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, text=html))
            )
        ),
    )
    item = collector.collect()[0]
    assert item.metadata["priority"] == "high" and item.metadata["official"]
    assert item.published_at is None and item.metadata["source_date"] == "2026-10-09"
    assert collector.discovery_audit[0]["status"] == "ok"


def test_synthetic_rss_failure_and_304_staleness_are_not_success(tmp_path):
    sources = [
        SourceConfig(id=i, name=i, type="rss", priority="low", url=f"https://example.com/{i}")
        for i in ["blocked", "cached"]
    ]
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({"cached": {"etag": "x", "latest_published_at": "2026-09-01T00:00:00Z"}})
    )
    collector = RSSCollector(
        sources,
        state_path=state,
        now=NOW,
        http=HttpClient(
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(403 if request.url.path == "/blocked" else 304)
                )
            )
        ),
    )
    assert collector.collect() == []
    assert collector.failures == {"rss:blocked": "HTTPStatusError"}
    assert collector.discovery_audit[1]["freshness"] == "stale_or_unavailable"
    assert collector.discovery_audit[0]["http_status"] == 403


def test_synthetic_usage_import_retry_rerun_and_unavailable(tmp_path):
    recorder = UsageRecorder(tmp_path, business_date="2026-10-10", module="daily")
    record = recorder.start(provider="mock", model="mock", task="classify", request_characters=20)
    # Synthetic quote tests arithmetic only; it is not a production price declaration.
    quote = PriceQuote(
        model="mock",
        input_per_million=2,
        output_per_million=3,
        source_url="https://example.com/synthetic-price",
        verified_on="2026-10-10",
    )
    done = recorder.finish(
        record,
        usage={"prompt_tokens": 1000, "completion_tokens": 100},
        state="response_received",
        price=quote,
    )
    recorder.import_record(done)
    assert usage_summary(tmp_path, "2026-10-10")["physical_calls"] == 1
    assert usage_summary(tmp_path, "2026-10-10")["total_cost_cny"] == "0.0023"
    recorder.start(provider="mock", model="mock", task="classify", request_characters=20)
    summary = usage_summary(tmp_path, "2026-10-10")
    assert summary["physical_calls"] == 2 and summary["total_cost_cny"] is None
    assert summary["incomplete_calls"] == 1
    assert summary["by_module"]["daily"]["tokens"]["prompt_tokens"] == {
        "observed": 1000,
        "missing_calls": 1,
    }


def test_synthetic_provider_structured_retry_is_two_physical_calls(tmp_path):
    ai = openai_provider([None, ClassificationBatch(items=[])])
    attach_usage(ai, tmp_path, business_date="2026-10-10", module="daily")
    ai.classify_items([raw_item()])
    summary = usage_summary(tmp_path, "2026-10-10")
    assert ai.budget.calls_used == 1 and summary["physical_calls"] == 2
    assert summary["cost_status"] == "unavailable"


@pytest.mark.parametrize(
    "failure,expected", [(ValueError, "failed"), (KeyboardInterrupt, "cancelled")]
)
def test_synthetic_health_failure_and_cancel_are_durable(tmp_path, failure, expected):
    with pytest.raises(failure), module_run(tmp_path, "2026-10-10", "daily"):
        raise failure()
    report = publish_health(tmp_path, "2026-10-10")
    assert report["modules"]["daily"]["status"] == expected
    assert report["not_run"] == ["tendency"]
    assert report["delivery_status"] == "partial_or_unavailable"
    assert not (tmp_path / "site/status.json").exists()
    with module_run(tmp_path, "2026-10-10", "daily"):
        pass
    with module_run(tmp_path, "2026-10-10", "tendency") as health:
        health["status"] = "no_changes"
    assert publish_health(tmp_path, "2026-10-10")["delivery_status"] == "complete"
    assert len(list((tmp_path / "data/health/2026-10-10").glob("*.json"))) == 3


@pytest.mark.parametrize("source_id", ["anthropic_news", "anthropic_research", "xai_news"])
def test_observed_current_listing_cards_parse_offline(source_id):
    observations = json.loads(Path("tests/fixtures/batch_a/source_observations.json").read_text())
    entry = next(o for o in observations if o["source_id"] == source_id)
    path = Path("tests/fixtures/batch_a") / (source_id + "-observed-listing.html")
    body = path.read_bytes()
    source = SourceConfig(
        id=source_id,
        name=source_id,
        url=entry["url"],
        type="official_listing",
        priority="high",
        official=True,
    )
    client = HttpClient(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
        )
    )
    collector = OfficialListingCollector(http=client, source=source, now=NOW)
    items = collector.collect()
    assert len(items) == 2
    assert all(i.metadata["source_date"] and i.metadata["priority"] == "high" for i in items)


def test_historical_mistral_input_synthetic_url_failure_survives_ledger(tmp_path, monkeypatch):
    from morning_radar.intake.ledger import ProcessingLedgerStore
    from morning_radar.pipeline import MorningRadarPipeline
    from tests.unit.test_phase1_patch import copy_project, save_checkpoint

    case = next(c for c in HISTORY if c["case_id"] == "2026-10-07:item-03489dc62549134a73a6")
    item = RawItem.model_validate(case["raw"])
    record = IntakeRecord.model_validate(case["records"][0])
    project = copy_project(tmp_path)
    checkpoint = save_checkpoint(
        project, [item], now=record.first_seen_at, batch_id="mistral-replay"
    )
    ledger = ProcessingLedgerStore(project / "data/intake/ledger.json")
    ledger.upsert_checkpoint(checkpoint, now=record.first_seen_at)
    ledger.save()

    class InvalidURL(FakeAIProvider):
        def merge_story(self, items):
            raise AIOutputError("Synthetic URL outside collected evidence")

    monkeypatch.setattr(
        "morning_radar.pipeline.DeepSeekProvider.from_environment", lambda **kwargs: InvalidURL()
    )
    MorningRadarPipeline(project).process(
        batch_id="mistral-replay", now=record.first_seen_at, notify=False
    )
    after = ProcessingLedgerStore(project / "data/intake/ledger.json")
    saved = after.get(checkpoint.items[0].input_id, checkpoint.items[0].content_version)
    assert saved.processing is ProcessingStatus.FAILED_RETRY
    assert saved.reason_code.value == "merge_failed"
    assert saved.url == item.url
    assert checkpoint.items[0].item.model_dump() == item.model_dump()


def test_synthetic_tendency_empty_is_different_from_not_configured(tmp_path, monkeypatch):
    from morning_radar.ai import AIConfigurationError
    from morning_radar.tendencies.workflow import run_tendency_workflow
    from tests.unit.test_phase1_patch import copy_project

    project = copy_project(tmp_path)
    run_tendency_workflow(project, provider=FakeAIProvider(), current_date=NOW.date())
    assert (
        publish_health(project, str(NOW.date()))["modules"]["tendency"]["status"] == "no_candidates"
    )

    def missing(**kwargs):
        raise AIConfigurationError("Missing model configuration")

    monkeypatch.setattr(
        "morning_radar.tendencies.workflow.DeepSeekProvider.from_environment", missing
    )
    with pytest.raises(AIConfigurationError):
        run_tendency_workflow(project, current_date=NOW.date())
    report = publish_health(project, str(NOW.date()))
    assert report["modules"]["tendency"]["status"] == "not_configured"


def test_observation_workflow_wiring_preserves_failed_attempts():
    for name in ["daily-brief", "post-daily"]:
        workflow = Path(f".github/workflows/{name}.yml").read_text(encoding="utf-8")
        observation = workflow.split(
            "- name: Preserve module health and physical call observations"
        )[1]
        assert "always()" in observation and "github.run_attempt" in observation
        assert "data/usage/" in observation and "data/health/" in observation


def test_health_counts_recovered_failure_outside_current_batch():
    from morning_radar.health import daily_details
    from morning_radar.intake.models import ReasonCode

    record = IntakeRecord.model_validate(HISTORY[0]["records"][0])
    entry = SimpleNamespace(
        batch_id="older-batch",
        input_id=record.input_id,
        content_version=record.content_version,
        processing=ProcessingStatus.FAILED_RETRY,
        reason_code=ReasonCode.CLASSIFICATION_RESPONSE_MISSING,
    )
    prepared = SimpleNamespace(
        batch_id="current-batch",
        selection=SimpleNamespace(records=[record], deferred=[]),
        ledger=SimpleNamespace(ledger=SimpleNamespace(entries={"key": entry})),
        intake=SimpleNamespace(
            collection=SimpleNamespace(failures={}),
            checkpoint=SimpleNamespace(manifest=SimpleNamespace(discovery_audit=[])),
        ),
    )
    status, details = daily_details(prepared, SimpleNamespace(run_stats={}))
    assert status == "partial" and details["pending_candidates"] == 1
    assert details["candidate_outcomes"] == {"classification_response_missing": 1}

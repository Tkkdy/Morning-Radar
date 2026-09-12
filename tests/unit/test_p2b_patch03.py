"""PATCH-03 offline regression coverage.

Behavior -> formal test node ID:
- HN title revision: ``test_hn_title_revision_keeps_two_versions``.
- HN target revision and checkpoint reload: ``test_hn_target_revision_survives_checkpoint``.
- HN equivalent/missing-field merge and discovery provenance:
  ``test_hn_equivalents_merge_order_independently``.
- HN V1/V2/V2 grouping: ``test_hn_body_versions_keep_shorter_change_and_merge_v2``.
- Official anchored-section identity and ordinary URL fallback:
  ``test_official_anchor_identity_survives_parser_dedup_and_checkpoint``.
- Physical request cap, first-request deadline, and redirect boundary:
  ``test_discovery_budget_deadline_and_redirect_are_physical_boundaries``.
- Paging order, failed-page diagnostics, and response byte cap:
  ``test_hn_paging_order_and_invalid_pagination_preserve_hits``.
- Protected slots/noise exclusion: ``test_candidate_protection_excludes_noise_hints``.
- Date evidence and checkpoint diagnostics:
  ``test_collector_date_evidence_and_diagnostics_reach_research_story_and_disk``.
"""

import json
from datetime import UTC, datetime

import httpx

from morning_radar.ai import FakeAIProvider
from morning_radar.ai.request_payload import research_request_payload
from morning_radar.collectors.deepseek_updates import DeepSeekUpdatesCollector
from morning_radar.collectors.hn_common import hn_item, merge_hn_observations
from morning_radar.collectors.hn_search import HNSearchCollector
from morning_radar.collectors.http import HttpClient, RequestBudgetExceeded, RequestStartBudget
from morning_radar.collectors.orchestrator import CollectionResult
from morning_radar.intake.candidates import select_process_candidates
from morning_radar.intake.checkpoint import (
    build_intake_records,
    load_checkpoint_by_batch_id,
    write_intake_checkpoint,
)
from morning_radar.models import RawItem, SourceRole, StatementType
from morning_radar.processing.deduplicate import deduplicate_items
from morning_radar.processing.filtering import filter_news_window
from morning_radar.processing.story_builder import build_story
from morning_radar.research.engine import build_research_cases
from morning_radar.settings import SourceConfig

NOW = datetime(2026, 9, 11, 1, 0, tzinfo=UTC)


def _hn(*, title: str, url: object, body: object, path: str, reason: str) -> RawItem:
    return hn_item(
        story_id=42,
        title=title,
        original_url=url,
        text=body,
        author="alice",
        submitted_at=int(NOW.timestamp()),
        fetched_at=NOW,
        metadata={"discovery_paths": [path], "discovery_reasons": [reason]},
    )


def _checkpoint(root, items: list[RawItem], batch_id: str):
    return write_intake_checkpoint(
        root,
        items=items,
        now=NOW,
        cutoff_at=NOW,
        collection=CollectionResult(
            items=items,
            raw_collected=len(items),
            after_buffer=len(items),
            after_dedup=len(items),
        ),
        source_state={},
        run_id="run-p2b-patch03",
        batch_id=batch_id,
    )


def test_hn_title_revision_keeps_two_versions() -> None:
    preview = _hn(
        title="DeepSeek preview", url="https://example.test/v1", body="same body",
        path="search", reason="watchlist_discovery",
    )
    released = _hn(
        title="DeepSeek released", url="https://example.test/v1", body="same body",
        path="top", reason="community_discovery",
    )

    records = build_intake_records(
        [preview, released], batch_id="batch-title", run_id="run-p2b-patch03"
    )

    assert len(records) == 2
    assert {record.title for record in records} == {"DeepSeek preview", "DeepSeek released"}


def test_hn_target_revision_survives_checkpoint(tmp_path) -> None:
    first_target = _hn(
        title="DeepSeek preview", url="https://example.test/v1", body="same body",
        path="search", reason="watchlist_discovery",
    )
    second_target = _hn(
        title="DeepSeek preview", url="https://example.test/v2", body="same body",
        path="best", reason="community_discovery",
    )

    records = build_intake_records(
        [first_target, second_target],
        batch_id="batch-versions",
        run_id="run-p2b-patch03",
    )

    assert len(records) == 2
    checkpoint = _checkpoint(
        tmp_path, [first_target, second_target], "batch-versions"
    )
    reloaded = load_checkpoint_by_batch_id(tmp_path, checkpoint.manifest.batch_id)
    assert len(reloaded.items) == 2
    assert {record.item.url for record in reloaded.items} == {
        "https://example.test/v1", "https://example.test/v2"
    }


def test_hn_equivalents_merge_order_independently() -> None:
    complete = _hn(
        title=" DeepSeek  update ", url="https://example.test/update", body="full body",
        path="search", reason="watchlist_discovery",
    )
    equivalent = _hn(
        title="DeepSeek update", url="https://example.test/update", body=" full body ",
        path="best", reason="community_discovery",
    )
    missing_body = _hn(
        title="DeepSeek update", url=None, body=None, path="new", reason="community_discovery",
    )

    observations_by_order = [
        [complete, equivalent],
        [missing_body, complete],
        [complete, missing_body],
    ]
    for observations in observations_by_order:
        merged = merge_hn_observations(observations)
        assert len(merged) == 1
        assert merged[0].content_excerpt == "full body"
        assert merged[0].url == "https://example.test/update"

    merged = merge_hn_observations([complete, equivalent])
    assert merged[0].metadata["discovery_paths"] == ["search", "best"]
    assert merged[0].metadata["discovery_reasons"] == [
        "watchlist_discovery", "community_discovery"
    ]


def test_hn_body_versions_keep_shorter_change_and_merge_v2() -> None:
    v1 = _hn(
        title="DeepSeek update", url="https://example.test/update", body="long original body",
        path="search", reason="watchlist_discovery",
    )
    v2 = _hn(
        title="DeepSeek update", url="https://example.test/update", body="short correction",
        path="top", reason="community_discovery",
    )
    v2_again = _hn(
        title="DeepSeek update", url="https://example.test/update", body="short correction",
        path="best", reason="community_discovery",
    )

    merged = merge_hn_observations([v1, v2, v2_again])

    assert len(merged) == 2
    by_body = {item.content_excerpt: item for item in merged}
    assert by_body["long original body"].metadata["discovery_paths"] == ["search"]
    assert by_body["short correction"].metadata["discovery_paths"] == ["top", "best"]


def test_official_anchor_identity_survives_parser_dedup_and_checkpoint(tmp_path) -> None:
    html = (
        "<article><h2>时间：2026-09-11</h2>"
        "<h3 id='alpha'>API update</h3><p>first section</p>"
        "<h3 id='beta'>API update</h3><p>second section</p></article>"
    )
    source = SourceConfig(
        id="deepseek_updates", name="DeepSeek Updates", type="official_changelog",
        url="https://api-docs.deepseek.com/zh-cn/updates/", priority="high", official=True,
    )
    http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=html)))
    parsed = DeepSeekUpdatesCollector(
        http=HttpClient(client=http), source=source, now=NOW
    ).collect()

    retained = deduplicate_items(parsed)
    assert len(retained) == 2
    assert len(deduplicate_items([parsed[0], parsed[0]])) == 1
    checkpoint = _checkpoint(tmp_path, retained, "batch-official-anchors")
    assert len(load_checkpoint_by_batch_id(tmp_path, checkpoint.manifest.batch_id).items) == 2

    ordinary = [
        RawItem(
            id="ordinary-a", title="same", url="https://example.test/page#a",
            source_name="Feed", source_type="rss", fetched_at=NOW,
            source_role=SourceRole.EDITORIAL,
            statement_type=StatementType.FACTUAL_ANNOUNCEMENT,
        ),
        RawItem(
            id="ordinary-b", title="same", url="https://example.test/page#b",
            source_name="Feed", source_type="rss", fetched_at=NOW,
            source_role=SourceRole.EDITORIAL,
            statement_type=StatementType.FACTUAL_ANNOUNCEMENT,
        ),
    ]
    assert len(deduplicate_items(ordinary)) == 1


def test_official_and_practitioner_evidence_pair_remains_retained() -> None:
    official = RawItem(
        id="official", title="API update", url="https://example.test/update",
        source_name="Example", source_type="official_changelog", fetched_at=NOW,
        source_role=SourceRole.OFFICIAL_PRIMARY,
        statement_type=StatementType.FACTUAL_ANNOUNCEMENT,
        metadata={"source_id": "example"},
    )
    practitioner = RawItem(
        id="practitioner", title="API update", url="https://example.test/update",
        source_name="Example", source_type="blog", fetched_at=NOW,
        source_role=SourceRole.PRACTITIONER,
        statement_type=StatementType.FIRSTHAND_OBSERVATION,
    )

    assert [item.id for item in deduplicate_items([official, practitioner])] == [
        "official", "practitioner"
    ]


def test_discovery_budget_deadline_and_redirect_are_physical_boundaries(monkeypatch) -> None:
    calls = []
    budget = RequestStartBudget(maximum_requests=1, deadline_seconds=5)
    redirecting = HttpClient(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: calls.append(request) or httpx.Response(
                    302, headers={"location": "https://example.test/final"}
                )
            )
        ),
        before_attempt=budget.before_attempt,
    )

    assert budget.deadline_at is None
    try:
        redirecting.get("https://example.test/redirect")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 302
    else:
        raise AssertionError("discovery redirect must not be followed")
    assert len(calls) == budget.used == redirecting.request_attempts == 1

    ticks = iter([100.0, 100.0, 106.0])
    monkeypatch.setattr("time.monotonic", lambda: next(ticks))
    deadline = RequestStartBudget(maximum_requests=2, deadline_seconds=5)
    deadline.before_attempt()
    assert deadline.deadline_at == 105.0
    try:
        deadline.before_attempt()
    except RequestBudgetExceeded as exc:
        assert "deadline" in str(exc)
    else:
        raise AssertionError("deadline must reject a late second physical request")


def test_hn_paging_order_and_invalid_pagination_preserve_hits() -> None:
    lab = type(
        "Lab",
        (),
        {"id": "deepseek", "aliases": ["DeepSeek"], "hn_queries": ["primary", "alias"]},
    )()
    config = type(
        "Watchlist",
        (),
        {
            "enabled": True,
            "maximum_queries_per_run": 2,
            "maximum_pages_per_query": 2,
            "maximum_network_requests": 2,
            "hits_per_page": 10,
            "maximum_excerpt_characters": 1600,
            "maximum_response_bytes": 10_000,
            "labs": [lab],
        },
    )()
    seen_pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = str(request.url.params["query"])
        page = int(request.url.params["page"])
        seen_pages.append((query, page))
        if query == "primary" and page == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(
            200,
            json={
                "nbPages": "invalid" if query == "alias" else 2,
                "hits": [{
                    "objectID": "42" if query == "primary" else "43",
                    "title": "DeepSeek release",
                    "story_text": "kept",
                    "created_at_i": int(NOW.timestamp()),
                }],
            },
        )

    collector = HNSearchCollector(
        http=HttpClient(attempts=1, client=httpx.Client(transport=httpx.MockTransport(handler))),
        watchlist=config,
        now=NOW,
    )
    items = collector.collect()

    assert seen_pages == [("primary", 0), ("alias", 0), ("primary", 1)]
    assert len(items) == 2
    audits = {entry["query"]: entry for entry in collector.discovery_audit}
    assert audits["primary"]["status"] == "partial"
    assert audits["primary"]["reason"] == "ConnectError"
    assert audits["alias"]["status"] == "partial"
    assert audits["alias"]["reason"] == "invalid_pagination"
    assert audits["alias"]["accepted"] == 1

    config.maximum_queries_per_run = 1
    config.maximum_pages_per_query = 1
    config.maximum_response_bytes = 100
    raw_response = (b" " * 200) + b'{"nbPages":1,"hits":[]}'
    assert len(raw_response) > config.maximum_response_bytes
    assert len(json.dumps({"nbPages": 1, "hits": []})) < config.maximum_response_bytes
    oversized = HNSearchCollector(
        http=HttpClient(client=httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=raw_response)
        ))),
        watchlist=config,
        now=NOW,
    )
    assert oversized.collect() == []
    assert oversized.discovery_audit[0]["reason"] == "response_too_large"


def test_candidate_protection_excludes_noise_hints() -> None:
    def record(label: str, summary: str):
        item = RawItem(
            id=label, title=f"DeepSeek {label}", url=f"https://example.test/{label}",
            source_name="Test", source_type="rss", fetched_at=NOW, summary=summary,
            content_excerpt=summary, metadata={"lab_id": "deepseek"},
        )
        return build_intake_records([item], batch_id=f"batch-{label}", run_id="run")[0]

    selection = select_process_candidates(
        fresh=[record("benchmark", "benchmark release"), record("release", "release available")],
        recovery=[],
        maximum_items=1,
        reserved_recovery_slots=0,
        reserved_fresh_slots=1,
    )

    assert [record.item.id for record in selection.records] == ["release"]


def test_collector_date_evidence_and_diagnostics_reach_research_story_and_disk(tmp_path) -> None:
    html = (
        "<article><h2>时间：2026-09-11</h2>"
        "<h3 id='api-release'>DeepSeek API update release</h3>"
        "<p>Concrete official release details for the API.</p></article>"
    )
    source = SourceConfig(
        id="deepseek_updates", name="DeepSeek Updates", type="official_changelog",
        url="https://api-docs.deepseek.com/zh-cn/updates/", priority="high", official=True,
    )
    collector = DeepSeekUpdatesCollector(
        http=HttpClient(client=httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=html)
        ))),
        source=source,
        now=NOW,
    )
    [item] = collector.collect()
    research_lead = item.model_copy(
        update={
            "source_role": SourceRole.COMMUNITY_DISCOVERY,
            "metadata": {**item.metadata, "selection_reason": "watchlist_discovery"},
        }
    )
    [case] = build_research_cases([research_lead], maximum_cases=1)
    research_payload = research_request_payload([case], None)
    evidence = research_payload["payload"][0]["lead"]
    story = build_story([item], provider=FakeAIProvider(), now=NOW)

    checkpoint = write_intake_checkpoint(
        tmp_path,
        items=[item],
        now=NOW,
        cutoff_at=NOW,
        collection=CollectionResult(items=[item], raw_collected=1, after_buffer=1, after_dedup=1),
        source_state={},
        batch_id="batch-diagnostics",
        run_id="run-p2b-patch03",
        discovery_audit=collector.discovery_audit,
    )
    loaded = load_checkpoint_by_batch_id(tmp_path, checkpoint.manifest.batch_id)

    retained = filter_news_window(
        [record.item for record in loaded.items], now=NOW, hours=24
    )
    assert retained[0].metadata["source_date"] == "2026-09-11"
    assert evidence["source_date"] == "2026-09-11"
    assert evidence["date_precision"] == "day"
    assert story.source_refs[0].source_date == "2026-09-11"
    assert story.source_refs[0].date_precision == "day"
    assert loaded.manifest.discovery_audit == collector.discovery_audit
    assert loaded.manifest.discovery_audit[0]["persisted"] == 1

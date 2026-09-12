from datetime import UTC, datetime
from types import SimpleNamespace

import httpx

from morning_radar.collectors.deepseek_updates import DeepSeekUpdatesCollector
from morning_radar.collectors.hn_search import HNSearchCollector
from morning_radar.collectors.http import HttpClient, RequestBudgetExceeded, RequestStartBudget
from morning_radar.intake.candidates import select_process_candidates
from morning_radar.intake.checkpoint import build_intake_records
from morning_radar.models import RawItem
from morning_radar.settings import SourceConfig

NOW = datetime(2026, 9, 10, 1, 26, tzinfo=UTC)


def test_deepseek_date_sections_keep_anchor_and_date_precision() -> None:
    html = "<article><h2>时间：2026-09-10</h2><h3 id='v41'>V4.1 Flash 发布</h3><p>模型能力更新。</p><h3 id='price'>价格变更</h3><p>API 价格更新。</p><h2>时间：2026-09-01</h2><h3 id='old'>旧版本</h3><p>旧内容。</p></article>"  # noqa: E501
    source = SourceConfig(id="deepseek_updates", name="DeepSeek Updates", type="official_changelog", url="https://api-docs.deepseek.com/zh-cn/updates/", priority="high", official=True)  # noqa: E501
    def response(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)
    http = HttpClient(client=httpx.Client(transport=httpx.MockTransport(response)))
    items = DeepSeekUpdatesCollector(http=http, source=source, now=NOW).collect()
    assert [item.url.rsplit("#", 1)[-1] for item in items] == ["v41", "price", "old"]
    assert items[0].published_at is None
    assert items[0].metadata["source_date"] == "2026-09-10"
    assert len({item.id for item in items}) == 3


def test_hn_search_keeps_no_url_story_and_stops_at_page_budget() -> None:
    config = SimpleNamespace(
        enabled=True,
        maximum_queries_per_run=12,
        maximum_pages_per_query=2,
        maximum_network_requests=1,
        hits_per_page=20,
        maximum_excerpt_characters=1600,
        labs=[SimpleNamespace(id="deepseek", aliases=["DeepSeek"], hn_queries=["DeepSeek"])],
    )
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        return httpx.Response(200, json={"nbPages": 2, "hits": [{"objectID": "49624603", "title": "DeepSeek V4.1 preview", "story_text": "A short preview", "created_at_i": int(NOW.timestamp()) - 60}]})  # noqa: E501
    http = HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    items = HNSearchCollector(http=http, watchlist=config, now=NOW).collect()
    assert len(items) == 1 and items[0].url.endswith("49624603")
    assert items[0].content_excerpt == "A short preview"
    assert calls[0]["tags"] == "story" and calls[0]["page"] == "0"


def test_hn_search_keeps_conflicting_discussion_versions_across_queries() -> None:
    config = SimpleNamespace(
        enabled=True,
        maximum_queries_per_run=2,
        maximum_pages_per_query=1,
        maximum_network_requests=2,
        hits_per_page=20,
        maximum_excerpt_characters=1600,
        labs=[SimpleNamespace(
            id="deepseek", aliases=["DeepSeek"], hn_queries=["DeepSeek", "DeepSeek V4"]
        )],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        hit = {
            "objectID": "49624603",
            "title": "DeepSeek V4 preview" if query == "DeepSeek" else "DeepSeek V4 released",
            "url": "https://example.test/preview" if query == "DeepSeek" else "https://example.test/release",
            "story_text": "preview details" if query == "DeepSeek" else "release details",
            "created_at_i": int(NOW.timestamp()) - 60,
        }
        return httpx.Response(200, json={"nbPages": 1, "hits": [hit]})

    http = HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    items = HNSearchCollector(http=http, watchlist=config, now=NOW).collect()

    assert len(items) == 2
    assert {item.title for item in items} == {"DeepSeek V4 preview", "DeepSeek V4 released"}
    assert {item.url for item in items} == {
        "https://example.test/preview", "https://example.test/release"
    }


def test_discovery_request_budget_counts_retries_and_hard_stops() -> None:
    attempts = []
    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url)
        if len(attempts) == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={})

    budget = RequestStartBudget(maximum_requests=2, deadline_seconds=120)
    http = HttpClient(
        attempts=2,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        before_attempt=budget.before_attempt,
    )
    assert http.get("https://example.test").status_code == 200
    assert budget.used == http.request_attempts == 2
    try:
        http.get("https://example.test")
    except RequestBudgetExceeded:
        pass
    else:
        raise AssertionError("budget must reject a third physical request")


def test_protected_fresh_slots_do_not_displace_recovery_or_regular_selection() -> None:
    def item(suffix: str, *, lab: str | None = None) -> RawItem:
        return RawItem(
            id=f"item-{suffix}", title=f"{suffix} release", url=f"https://e.test/{suffix}",
            source_name="Test", source_type="rss", published_at=NOW, fetched_at=NOW,
            summary="model release", content_excerpt="model release",
            source_role="official_primary",
            statement_type="factual_announcement", metadata={"lab_id": lab} if lab else {},
        )
    fresh = build_intake_records(
        [item("a", lab="deepseek"), item("b"), item("c")], batch_id="b", run_id="r"
    )
    recovery = build_intake_records([item("old")], batch_id="old", run_id="old")
    selected = select_process_candidates(
        fresh=fresh, recovery=recovery, maximum_items=3, reserved_recovery_slots=1,
        reserved_fresh_slots=1,
    )
    assert [record.item.id for record in selected.records] == ["item-old", "item-a", "item-b"]
    assert len(selected.protected_fresh_keys) == 1

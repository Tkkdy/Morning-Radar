"""B1 acceptance using synthetic transports and explicit demonstration evidence."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from morning_radar.ai import FakeAIProvider
from morning_radar.collectors.http import HttpClient, RequestBudgetExceeded
from morning_radar.models import RawItem
from morning_radar.models.core import GroundedPoint, TechnicalReading
from morning_radar.processing.story_builder import build_stories
from morning_radar.research.materials import enrich_materials, load_material_evidence
from morning_radar.settings import MaterialEnrichmentConfig

NOW = datetime(2026, 10, 10, tzinfo=UTC)
HTML = Path("tests/fixtures/b1/technical-demo.html").read_text(encoding="utf-8")


def raw(suffix="paper"):
    return RawItem(
        id=suffix,
        title="演示技术报告",
        url="https://example.org/" + suffix,
        source_name="演示作者",
        source_type="rss",
        fetched_at=NOW,
        published_at=NOW,
        summary="演示摘要，缺少方法与评测条件。",
        metadata={"priority": "high"},
    )


def policy(**changes):
    return MaterialEnrichmentConfig(enabled=True, allowed_hosts=["example.org"], **changes)


def transport(handler):
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), attempts=1)


def test_bounded_enrichment_does_not_mutate_raw_and_is_visible_without_news(tmp_path):
    first, second = raw(), raw("other")
    before = first.model_dump()
    http = transport(
        lambda _: httpx.Response(200, text=HTML, headers={"Content-Type": "text/html"})
    )
    result = enrich_materials(
        [first, second],
        root=tmp_path,
        now=NOW,
        policy=policy(maximum_items=1),
        important_ids={first.id, second.id},
        http=http,
        network_allowed=True,
    )
    assert first.model_dump() == before
    assert result.snapshots[0]["status"] == "fetched"
    assert result.snapshots[1]["status"] == "budget_skipped"
    assert result.stats["material_http_requests"] == 1 and result.stats["material_ai_calls"] == 0
    saved = load_material_evidence(tmp_path, through=NOW)
    assert {x["input_id"] for x in saved} == {first.id, second.id}
    assert not (tmp_path / "data/briefs").exists()
    assert all(len(x["excerpt"]) <= 1600 for x in saved)
    assert all(x["source_role"] == first.source_role.value for x in saved)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/paper",
        "https://example.org:bad/paper",
        "https://127.0.0.1/paper",
        "https://example.org/paper?token=secret",
    ],
)
def test_nonallowed_original_url_is_not_requested(tmp_path, url):
    http = transport(lambda _: (_ for _ in ()).throw(AssertionError("must not request")))
    result = enrich_materials(
        [raw().model_copy(update={"url": url})],
        root=tmp_path,
        now=NOW,
        policy=policy(),
        important_ids={"paper"},
        http=http,
        network_allowed=True,
    )
    assert result.snapshots[0]["status"] == "not_allowed" and http.request_attempts == 0


@pytest.mark.parametrize(
    "response,status",
    [
        (httpx.Response(403), "failed"),
        (httpx.Response(302, headers={"Location": "https://example.net"}), "failed"),
        (
            httpx.Response(200, content=b"pdf", headers={"Content-Type": "application/pdf"}),
            "unsupported_format",
        ),
        (
            httpx.Response(200, text="<html></html>", headers={"Content-Type": "text/html"}),
            "parse_empty",
        ),
    ],
)
def test_source_failures_are_durable_and_local(tmp_path, response, status):
    result = enrich_materials(
        [raw()],
        root=tmp_path,
        now=NOW,
        policy=policy(),
        important_ids={"paper"},
        http=transport(lambda _: response),
        network_allowed=True,
    )
    assert result.snapshots[0]["status"] == status
    assert load_material_evidence(tmp_path, through=NOW)[0]["status"] == status


def test_technical_quotes_are_validated_against_attributed_material(tmp_path):
    result = enrich_materials(
        [raw()],
        root=tmp_path,
        now=NOW,
        policy=policy(),
        important_ids={"paper"},
        http=transport(
            lambda _: httpx.Response(200, text=HTML, headers={"Content-Type": "text/html"})
        ),
        network_allowed=True,
    )
    item = result.items[0]
    point = GroundedPoint(
        text="减少长文档处理中的重复读取。",
        source_url=item.url,
        quote="作者将重复片段分块缓存，并只对新片段执行处理",
    )
    reading = TechnicalReading(kind="technical_report", problem=point, method=point)

    class Demo(FakeAIProvider):
        def merge_story(self, items):
            return super().merge_story(items).model_copy(update={"technical_reading": reading})

    stories = build_stories([item], provider=Demo(), now=NOW)
    assert stories[0].technical_reading.method.text == point.text
    assert stories[0].evidence_ids == [result.snapshots[0]["evidence_id"]]
    reading.method = point.model_copy(
        update={"quote": "这是一段不在材料中的编造实验依据，必须拒绝"}
    )
    outcomes = {}
    assert build_stories([item], provider=Demo(), now=NOW, item_outcomes=outcomes) == []
    assert outcomes[item.id] == "merge_failed"


def test_download_deadline_stops_stream(monkeypatch):
    from morning_radar.collectors import http as module

    monkeypatch.setattr(module, "monotonic", lambda: 11)
    http = transport(lambda _: httpx.Response(200, content=b"evidence"))
    with pytest.raises(RequestBudgetExceeded):
        http.get("https://example.org/paper", maximum_response_bytes=1000, deadline_monotonic=10)


def test_cancel_preserves_started_candidate_diagnostic(tmp_path):
    def cancel(_):
        raise KeyboardInterrupt()

    http = transport(cancel)
    with pytest.raises(KeyboardInterrupt):
        enrich_materials(
            [raw()],
            root=tmp_path,
            now=NOW,
            policy=policy(),
            important_ids={"paper"},
            http=http,
            network_allowed=True,
        )
    assert load_material_evidence(tmp_path, through=NOW)[0]["status"] == "cancelled"
    assert http.before_attempt is None


def test_article_body_is_not_displaced_by_long_page_chrome(tmp_path):
    # Reduced synthetic regression of the observed R1 failure, not saved page HTML.
    page = Path("tests/fixtures/b1/news-body-after-chrome.html").read_text(encoding="utf-8")
    item = raw().model_copy(update={"title": "后续新闻", "summary": "", "content_excerpt": ""})
    result = enrich_materials(
        [item], root=tmp_path, now=NOW, policy=policy(),
        important_ids={item.id},
        http=transport(lambda _: httpx.Response(
            200, text=page, headers={"Content-Type": "text/html"},
        )),
        network_allowed=True,
    )
    excerpt = result.snapshots[0]["excerpt"]
    assert "The company said it suspended internet access" in excerpt
    assert "REGISTER NOW" not in excerpt
    assert "Image Credits" not in excerpt
    assert "unrelated sidebar" not in excerpt
    assert result.items[0].summary == ""
    assert len(excerpt) <= 1600 and result.stats["material_http_requests"] == 1


def test_title_and_metadata_only_page_does_not_close_source_gap(tmp_path):
    page = "<html><head><title>" + "News headline " * 40 + "</title></head>"
    page += "<main><h1>" + "News headline " * 40 + "</h1>"
    page += "<div>Image Credits: Example. Author Name. Publication time.</div></main></html>"
    item = raw().model_copy(update={"title": "后续新闻", "summary": "", "content_excerpt": ""})
    result = enrich_materials(
        [item], root=tmp_path, now=NOW, policy=policy(),
        important_ids={item.id},
        http=transport(lambda _: httpx.Response(
            200, text=page, headers={"Content-Type": "text/html"},
        )),
        network_allowed=True,
    )
    assert result.snapshots[0]["status"] == "parse_empty"
    assert "source_detail" in result.snapshots[0]["remaining_gaps"]
    assert result.items[0].content_excerpt == ""

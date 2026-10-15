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
    rejected = build_stories([item], provider=Demo(), now=NOW, item_outcomes=outcomes)
    assert len(rejected) == 1
    assert rejected[0].facts == [] and rejected[0].technical_reading is None
    assert rejected[0].source_urls == [item.url]
    assert "编造实验依据" not in rejected[0].model_dump_json()
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


def test_long_feed_excerpt_with_terminal_ellipsis_still_needs_material():
    from morning_radar.research.materials import material_gaps
    item = raw().model_copy(update={"title": "Chip release", "url": "https://example.org/chip",
                                    "summary": "Release details. " * 30 + "[…]"})
    assert "source_detail" in material_gaps(item)
    item.summary = "Release details. " * 30
    assert "source_detail" not in material_gaps(item)


def test_fetched_page_provenance_reaches_story_and_payload_without_relabeling_hn(tmp_path):
    from datetime import timedelta

    from morning_radar.ai.request_payload import editorial_story_payload, evidence_snapshot
    from morning_radar.models import SourceRole
    from morning_radar.processing.story_builder import build_story
    item = raw().model_copy(update={
        "source_name": "Hacker News", "source_type": "hacker_news", "author": "submitter",
        "source_role": SourceRole.COMMUNITY_DISCOVERY,
    })
    before = item.model_dump()
    observed = NOW + timedelta(hours=1)
    html = ('<head><meta property="og:site_name" content="Page publisher">'
            '<meta name="author" content="Page author"></head>' + HTML)
    result = enrich_materials([item], root=tmp_path, now=observed, policy=policy(),
        important_ids={item.id}, http=transport(lambda _: httpx.Response(
            200, text=html, headers={"Content-Type": "text/html"})), network_allowed=True)
    material = result.items[0]
    ref = evidence_snapshot(material, association_basis="same_input")
    assert item.model_dump() == before
    assert material.source_name == "Hacker News" and material.author == "submitter"
    assert ref.source_type == "original_page" and ref.source_name == "Page publisher"
    assert ref.fetched_at == observed and ref.content_version
    explicit = evidence_snapshot(material, association_basis="same_input",
                                 content_version="collector-version")
    assert explicit.content_version == "collector-version"
    assert explicit.material_provenance.content_version != "collector-version"
    assert ref.material_provenance.author == "Page author"
    assert ref.source_role.value == "unknown" and not ref.official_page_fetched
    assert ref.discovery_source["author_or_submitter"] == "submitter"
    story = build_story([material], provider=FakeAIProvider(), now=observed)
    assert story.source_refs[0].material_provenance == ref.material_provenance
    packed = editorial_story_payload(story)["source_refs"][0]["material_provenance"]
    assert packed["author"] == "Page author"
    from morning_radar.ai.request_payload import merge_story_payload, writing_story_payload
    material.metadata["excerpt_truncated"] = True
    material.metadata["material_information_gaps"] = ["source_detail"]
    merge = merge_story_payload([material])[0]
    assert merge["excerpt_truncated"] is True
    assert merge["information_gaps"] == ["source_detail"]
    assert "metadata" not in merge and "metadata" not in merge["discovery_source"]
    assert merge["title"] == material.title and merge["summary"] == material.summary
    assert merge["content_excerpt"] == material.content_excerpt
    assert merge["source_type"] == "original_page" and merge["author"] == "Page author"
    assert merge["source_role"] == "unknown" and merge["published_at"] is None
    assert merge["discovery_source"]["author"] == "submitter"
    writing = writing_story_payload(story)["source_refs"][0]
    assert writing["source_type"] == "original_page"
    assert writing["discovery_source"]["source_type"] == "hacker_news"
    from morning_radar.ai.request_payload import (
        brief_item_recovery_request_payload,
        brief_request_payload,
        classify_items_payload,
        research_case_payload,
        score_story_payload,
    )
    from morning_radar.models.core import ResearchCase
    before_story = story.model_dump(mode="json")
    case = ResearchCase(id="case-boundary", observed_at=observed, claim="Source claim",
                        statement_type=ref.statement_type, lead=ref,
                        supporting_evidence=[ref])
    packed_requests = [merge_story_payload([material]), classify_items_payload([material], None),
                       score_story_payload(story, None), editorial_story_payload(story),
                       brief_request_payload([story], []),
                       brief_item_recovery_request_payload(story, []), research_case_payload(case)]
    import json
    for request in packed_requests:
        wire = json.dumps(request)
        assert "official_page_fetched" not in wire
        assert "identity_basis" not in wire
        assert "material_status" not in wire
        assert "Page author" in wire and "Hacker News" in wire and "submitter" in wire
    projected = research_case_payload(case)["lead"]
    assert projected["content_excerpt"] == ref.content_excerpt
    assert projected["content_missing"] == ref.content_missing
    assert projected["excerpt_truncated"] == ref.excerpt_truncated
    assert projected["text_omission_reason"] == ref.text_omission_reason
    assert projected["published_at"] is None
    assert projected["discovery_source"]["published_at_role"] == ref.published_at_role.value
    assert projected["fetched_at"] == ref.model_dump(mode="json")["fetched_at"]
    assert story.model_dump(mode="json") == before_story
    assert case.lead.material_provenance.official_page_fetched is False
    from morning_radar.tendencies.core_store import CoreStore
    from morning_radar.tendencies.inputs import read_evidence
    evidence, _ = read_evidence(tmp_path, CoreStore(tmp_path), through=observed)
    assert evidence[0].observed_at == observed and evidence[0].source_role == "unknown"
    assert evidence[0].status == "fetched"


def test_reused_snapshot_does_not_invent_publisher_author_or_observation_time():
    from morning_radar.models import SourceRole
    from morning_radar.research.materials import apply_material_snapshot
    item = raw().model_copy(update={"source_type": "hacker_news", "author": "submitter",
                                  "source_role": SourceRole.COMMUNITY_DISCOVERY})
    snap = {"input_id": item.id, "source_url": item.url, "status": "fetched",
            "observed_at": NOW.isoformat(), "excerpt": "A preserved original page excerpt."}
    material = apply_material_snapshot(item, snap)
    origin = material.metadata["material_provenance"]
    assert origin["publisher"] is None and origin["author"] is None
    assert origin["observed_at"] == NOW.isoformat().replace("+00:00", "Z")
    with pytest.raises(ValueError):
        apply_material_snapshot(item, {**snap, "source_url": "https://other.example/"})

"""Minimal confirmed A-review regressions; synthetic unless stated otherwise."""

import json
from datetime import UTC, datetime

import httpx

from morning_radar.collectors.http import HttpClient
from morning_radar.collectors.official_listing import OfficialListingCollector
from morning_radar.health import module_run, publish_health
from morning_radar.models import RawItem, SourceRole
from morning_radar.processing.grouping import group_items_by_normalized_title
from morning_radar.settings import SourceConfig


def test_generic_generation_pricing_does_not_identify_same_event():
    a = RawItem(
        id="a",
        title="OpenAI image generation pricing",
        url="https://example.com/a",
        source_name="Official",
        source_type="rss",
        source_role=SourceRole.OFFICIAL_PRIMARY,
        fetched_at=datetime(2026, 10, 10, tzinfo=UTC),
    )
    b = a.model_copy(
        update={
            "id": "b",
            "title": "OpenAI code generation pricing",
            "source_name": "Media",
            "url": "https://example.com/b",
        }
    )
    assert len(group_items_by_normalized_title([a, b])) == 2


def test_article_excerpt_not_erased_by_anchor_parser():
    html = (
        '<article><a href="/news/event"><h2>Event</h2>'
        '<time datetime="2026-10-10"></time><p>Observed evidence</p></a></article>'
    )
    source = SourceConfig(
        id="s",
        name="s",
        type="official_listing",
        priority="high",
        url="https://example.com/news",
        official=True,
    )
    collector = OfficialListingCollector(
        source=source,
        now=datetime(2026, 10, 10, tzinfo=UTC),
        http=HttpClient(
            client=httpx.Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, text=html))
            )
        ),
    )
    assert collector.collect()[0].content_excerpt == "Observed evidence"


def test_public_health_omits_error_urls_and_unexpected_audit_fields(tmp_path):
    with module_run(tmp_path, "2026-10-10", "daily") as health:
        health["details"] = {
            "sources": [
                {
                    "source_id": "public",
                    "status": "failed",
                    "reason": "HTTP https://example.com?token=SECRET",
                    "response": "PRIVATE",
                }
            ]
        }
    report = publish_health(tmp_path, "2026-10-10")
    serialized = json.dumps(report)
    assert "SECRET" not in serialized and "PRIVATE" not in serialized
    assert report["modules"]["daily"]["details"]["sources"][0]["status"] == "failed"

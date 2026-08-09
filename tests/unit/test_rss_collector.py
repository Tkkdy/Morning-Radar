from datetime import UTC, datetime
from pathlib import Path

import httpx

from morning_radar.collectors.http import HttpClient
from morning_radar.collectors.rss import RSSCollector
from morning_radar.settings import SourceConfig
from morning_radar.storage import read_json


def source(source_id: str = "fixture") -> SourceConfig:
    return SourceConfig(
        id=source_id,
        name="Fixture Feed",
        type="rss",
        url=f"https://feeds.example/{source_id}.xml",
        priority="high",
        topics=["ai_models"],
        official=True,
    )


def test_rss_collects_atom_or_rss_and_deduplicates_urls(tmp_path) -> None:
    xml = Path("fixtures/rss/example.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"].startswith("MorningRadar/")
        return httpx.Response(
            200,
            content=xml,
            headers={"ETag": '"fixture-v1"', "Last-Modified": "Wed, 22 Jul 2026 23:00:00 GMT"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    collector = RSSCollector(
        [source()],
        http=HttpClient(client=client),
        state_path=tmp_path / "rss.json",
        now=datetime(2026, 7, 23, 1, tzinfo=UTC),
    )

    items = collector.collect()

    assert len(items) == 1
    assert items[0].published_at == datetime(2026, 7, 22, 23, 10, tzinfo=UTC)
    assert items[0].content_excerpt == "A short official release description."
    assert read_json(tmp_path / "rss.json")["fixture"]["etag"] == '"fixture-v1"'


def test_rss_uses_cache_headers_and_accepts_not_modified(tmp_path) -> None:
    state = tmp_path / "rss.json"
    state.write_text(
        '{"fixture": {"etag": "\\"fixture-v1\\"", "last_modified": ""}}',
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-None-Match"] == '"fixture-v1"'
        return httpx.Response(304)

    collector = RSSCollector(
        [source()],
        http=HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler))),
        state_path=state,
    )

    assert collector.collect() == []


def test_one_feed_failure_does_not_drop_successful_feed(tmp_path, caplog) -> None:
    xml = Path("fixtures/rss/example.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if "broken" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, content=xml)

    collector = RSSCollector(
        [source("broken"), source("working")],
        http=HttpClient(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            attempts=1,
        ),
        state_path=tmp_path / "rss.json",
    )

    assert len(collector.collect()) == 1
    assert "RSS source failed: broken" in caplog.text


def test_rss_round_robin_prevents_a_long_first_feed_from_starving_later_feeds(
    tmp_path,
) -> None:
    def feed(source_id: str, count: int) -> bytes:
        entries = "".join(
            f"<entry><title>{source_id}-{index}</title>"
            f"<link href='https://feeds.example/{source_id}/{index}'/>"
            "<updated>2026-07-23T00:00:00Z</updated></entry>"
            for index in range(count)
        )
        return (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<feed xmlns='http://www.w3.org/2005/Atom'>"
            f"<title>{source_id}</title>{entries}</feed>"
        ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        source_id = request.url.path.rsplit("/", 1)[-1].removesuffix(".xml")
        return httpx.Response(
            200,
            content=feed(source_id, 5 if source_id == "long" else 2),
        )

    collector = RSSCollector(
        [source("long"), source("short")],
        http=HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler))),
        state_path=tmp_path / "rss.json",
    )

    items = collector.collect()

    assert [item.source_name for item in items[:4]] == [
        "Fixture Feed",
        "Fixture Feed",
        "Fixture Feed",
        "Fixture Feed",
    ]
    assert [item.title for item in items[:5]] == [
        "long-0",
        "short-0",
        "long-1",
        "short-1",
        "long-2",
    ]

from datetime import UTC, datetime

import httpx

from morning_radar.collectors.hacker_news import HackerNewsCollector
from morning_radar.collectors.http import HttpClient


def test_hacker_news_filters_keywords_limits_candidates_and_keeps_both_links() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(("topstories.json", "newstories.json", "beststories.json")):
            return httpx.Response(200, json=[1, 2, 3])
        story_id = int(path.split("/")[-1].split(".")[0])
        stories = {
            1: {
                "id": 1,
                "title": "New AI coding agent released",
                "url": "https://example.com/agent",
                "by": "dev",
                "time": 1784764800,
                "score": 120,
                "descendants": 30,
            },
            2: {"id": 2, "title": "Unrelated gardening", "time": 1784764800},
        }
        return httpx.Response(200, json=stories[story_id])

    collector = HackerNewsCollector(
        http=HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler))),
        keywords=["AI coding"],
        maximum_candidates=2,
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )

    items = collector.collect()

    assert len(items) == 1
    assert items[0].url == "https://example.com/agent"
    assert items[0].metadata["discussion_url"] == "https://news.ycombinator.com/item?id=1"
    assert items[0].metadata["community_signal"] is True


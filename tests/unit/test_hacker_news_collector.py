import logging
from datetime import UTC, datetime

import httpx

from morning_radar.collectors.hacker_news import HackerNewsCollector
from morning_radar.collectors.http import HttpClient
from morning_radar.provenance import verified_source_urls


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
    assert verified_source_urls(items[0]) == (
        "https://example.com/agent",
        "https://news.ycombinator.com/item?id=1",
    )


def test_candidate_budget_is_shared_across_all_three_lists(caplog) -> None:
    caplog.set_level(logging.INFO)
    requested_items: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("topstories.json"):
            return httpx.Response(200, json=list(range(1, 21)))
        if path.endswith("newstories.json"):
            return httpx.Response(200, json=list(range(101, 121)))
        if path.endswith("beststories.json"):
            return httpx.Response(200, json=list(range(201, 221)))
        story_id = int(path.split("/")[-1].split(".")[0])
        requested_items.append(story_id)
        return httpx.Response(
            200,
            json={
                "id": story_id,
                "title": f"AI agent release {story_id}",
                "time": 1785110400,
            },
        )

    collector = HackerNewsCollector(
        http=HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler))),
        keywords=["AI coding"],
        maximum_candidates=30,
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    items = collector.collect()

    assert len(items) == 30
    assert len([item_id for item_id in requested_items if item_id < 100]) == 10
    assert len([item_id for item_id in requested_items if 100 < item_id < 200]) == 10
    assert len([item_id for item_id in requested_items if item_id > 200]) == 10
    assert "top_ids=20 new_ids=20 best_ids=20" in caplog.text
    assert "selected_candidates=30 fetched_items=30 keyword_matches=30" in caplog.text


def test_weak_github_body_match_does_not_retain_unrelated_story() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(
            ("topstories.json", "newstories.json", "beststories.json")
        ):
            return httpx.Response(200, json=[1])
        return httpx.Response(
            200,
            json={
                "id": 1,
                "title": "A physically accurate black hole",
                "text": "Source code mirror available on github.",
                "time": 1785110400,
            },
        )

    collector = HackerNewsCollector(
        http=HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler))),
        keywords=["github"],
    )

    assert collector.collect() == []


def test_clear_ai_llm_agent_and_mcp_title_signals_are_retained() -> None:
    titles = {
        1: "AI changes developer workflows",
        2: "New LLM runtime released",
        3: "Agent framework reaches v1",
        4: "MCP server implementation",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("topstories.json"):
            return httpx.Response(200, json=[1, 2, 3, 4])
        if path.endswith(("newstories.json", "beststories.json")):
            return httpx.Response(200, json=[])
        story_id = int(path.split("/")[-1].split(".")[0])
        return httpx.Response(
            200,
            json={
                "id": story_id,
                "title": titles[story_id],
                "time": 1785110400,
            },
        )

    collector = HackerNewsCollector(
        http=HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler))),
        keywords=["github"],
    )

    assert {item.title for item in collector.collect()} == set(titles.values())

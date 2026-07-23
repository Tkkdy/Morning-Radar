import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from morning_radar.collectors.github import GitHubCollector, calculate_github_growth
from morning_radar.collectors.http import HttpClient
from morning_radar.models import GitHubSnapshot
from morning_radar.settings import RepositoryConfig
from morning_radar.storage import load_models, save_models

NOW = datetime(2026, 7, 23, 1, tzinfo=UTC)


def snapshot(snapshot_date: date, stars: int) -> GitHubSnapshot:
    return GitHubSnapshot(
        date=snapshot_date,
        captured_at=NOW,
        repository="example/agent",
        stars=stars,
        forks=1,
        open_issues=2,
        updated_at=NOW,
    )


def test_growth_calculates_24_hour_and_7_day_changes() -> None:
    current = snapshot(date(2026, 7, 23), 125)
    history = [
        snapshot(date(2026, 7, 22), 100),
        snapshot(date(2026, 7, 16), 80),
    ]

    result = calculate_github_growth(current, history)

    assert result["stars_delta_24h"] == 25
    assert result["stars_growth_24h"] == 0.25
    assert result["stars_delta_7d"] == 45


def test_growth_handles_missing_history_and_star_decline() -> None:
    current = snapshot(date(2026, 7, 23), 90)
    result = calculate_github_growth(
        current,
        [snapshot(date(2026, 7, 22), 100)],
    )

    assert result["stars_delta_24h"] == -10
    assert result["stars_delta_7d"] is None
    assert result["data_anomaly"] is True


def test_github_collects_release_and_saves_snapshot(tmp_path) -> None:
    repository_data = json.loads(
        Path("fixtures/github/repository.json").read_text(encoding="utf-8")
    )
    releases_data = json.loads(Path("fixtures/github/releases.json").read_text(encoding="utf-8"))
    snapshots = tmp_path / "github"
    save_models(snapshots / "2026-07-22.json", [snapshot(date(2026, 7, 22), 12000)])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.url.path.endswith("/releases"):
            return httpx.Response(200, json=releases_data)
        return httpx.Response(200, json=repository_data)

    collector = GitHubCollector(
        [
            RepositoryConfig(
                owner="example",
                repo="agent",
                priority="high",
                topics=["ai_coding"],
            )
        ],
        http=HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler))),
        snapshot_dir=snapshots,
        token="test-token",
        now=NOW,
    )

    items = collector.collect()

    assert len(items) == 1
    assert items[0].url == releases_data[0]["html_url"]
    assert items[0].metadata["stars_delta_24h"] == 500
    saved = load_models(snapshots / "2026-07-23.json", GitHubSnapshot)
    assert saved[0].stars == 12500

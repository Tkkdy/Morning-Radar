"""GitHub repository/release collection and daily snapshot growth."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dateutil.parser import parse as parse_datetime

from morning_radar.collectors.http import HttpClient
from morning_radar.models import GitHubSnapshot, RawItem
from morning_radar.processing import stable_item_id
from morning_radar.settings import RepositoryConfig
from morning_radar.storage import load_models, save_models
from morning_radar.time_utils import display_date, utc_now

LOGGER = logging.getLogger(__name__)
API_ROOT = "https://api.github.com"


def _history_by_repository(
    snapshot_dir: Path,
    current: GitHubSnapshot,
) -> list[GitHubSnapshot]:
    history: list[GitHubSnapshot] = []
    for days in (1, 7):
        path = snapshot_dir / f"{current.date - timedelta(days=days)}.json"
        if path.exists():
            history.extend(load_models(path, GitHubSnapshot))
    return [item for item in history if item.repository == current.repository]


def calculate_github_growth(
    current: GitHubSnapshot,
    history: list[GitHubSnapshot],
) -> dict[str, int | float | bool | None]:
    by_date = {item.date: item for item in history}
    result: dict[str, int | float | bool | None] = {
        "stars_delta_24h": None,
        "stars_delta_7d": None,
        "stars_growth_24h": None,
        "stars_growth_7d": None,
        "data_anomaly": False,
    }
    for days, suffix in ((1, "24h"), (7, "7d")):
        previous = by_date.get(current.date - timedelta(days=days))
        if not previous:
            continue
        delta = current.stars - previous.stars
        result[f"stars_delta_{suffix}"] = delta
        result[f"stars_growth_{suffix}"] = delta / previous.stars if previous.stars else None
        if delta < 0:
            result["data_anomaly"] = True
    return result


class GitHubCollector:
    name = "github"

    def __init__(
        self,
        repositories: list[RepositoryConfig],
        *,
        http: HttpClient,
        snapshot_dir: Path,
        token: str | None = None,
        now: datetime | None = None,
    ) -> None:
        self.repositories = repositories
        self.http = http
        self.snapshot_dir = snapshot_dir
        self.token = token
        self.now = now or utc_now()

    def collect(self) -> list[RawItem]:
        items: list[RawItem] = []
        snapshots: list[GitHubSnapshot] = []
        for repository in self.repositories:
            try:
                repo_items, snapshot = self._collect_repository(repository)
                items.extend(repo_items)
                snapshots.append(snapshot)
            except Exception:
                LOGGER.exception("GitHub repository failed: %s", repository.full_name)
        if snapshots:
            save_models(self.snapshot_dir / f"{display_date(self.now)}.json", snapshots)
        return items

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get_json(self, url: str) -> Any:
        response = self.http.get(url, headers=self._headers())
        if response.headers.get("X-RateLimit-Remaining") == "0":
            LOGGER.warning("GitHub rate limit exhausted; remaining repositories may fail")
        return response.json()

    def _collect_repository(
        self,
        repository: RepositoryConfig,
    ) -> tuple[list[RawItem], GitHubSnapshot]:
        repo_url = f"{API_ROOT}/repos/{repository.full_name}"
        metadata = self._get_json(repo_url)
        snapshot = GitHubSnapshot(
            date=display_date(self.now),
            captured_at=self.now,
            repository=repository.full_name,
            stars=metadata["stargazers_count"],
            forks=metadata["forks_count"],
            open_issues=metadata["open_issues_count"],
            updated_at=parse_datetime(metadata["updated_at"]),
        )
        growth = calculate_github_growth(
            snapshot,
            _history_by_repository(self.snapshot_dir, snapshot),
        )
        releases = self._get_json(f"{repo_url}/releases?per_page=10")
        items: list[RawItem] = []
        for release in releases:
            url = release.get("html_url")
            title = release.get("name") or release.get("tag_name")
            if not url or not title:
                continue
            body = " ".join((release.get("body") or "").split())[:4000]
            items.append(
                RawItem(
                    id=stable_item_id(url),
                    title=f"{repository.full_name}: {title}",
                    url=url,
                    source_name=f"GitHub · {repository.full_name}",
                    source_type="github",
                    author=(release.get("author") or {}).get("login"),
                    published_at=(
                        parse_datetime(release["published_at"])
                        if release.get("published_at")
                        else None
                    ),
                    fetched_at=self.now,
                    language="en",
                    summary=body[:1000],
                    content_excerpt=body,
                    topic_candidates=repository.topics,
                    repository_candidates=[repository.full_name],
                    metadata={
                        "official": True,
                        "priority": repository.priority,
                        "repository": repository.full_name,
                        "stars": snapshot.stars,
                        "forks": snapshot.forks,
                        "open_issues": snapshot.open_issues,
                        **growth,
                    },
                )
            )
        return items, snapshot


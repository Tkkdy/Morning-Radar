"""Main Morning Radar orchestration flow."""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path

from morning_radar.ai import AIBudget, DeepSeekProvider, FakeAIProvider
from morning_radar.briefing import BriefLimits, generate_daily_brief
from morning_radar.collectors import CollectionResult, FixtureCollector, collect_available
from morning_radar.collectors.github import GitHubCollector
from morning_radar.collectors.hacker_news import HackerNewsCollector
from morning_radar.collectors.http import HttpClient
from morning_radar.collectors.market import MarketCollector, YFinanceHistoryProvider
from morning_radar.collectors.rss import RSSCollector
from morning_radar.models import DailyBrief, GitHubSnapshot, MarketSnapshot, Story
from morning_radar.notifications import WxPusherConfig, WxPusherNotifier
from morning_radar.processing import build_stories, filter_news_window
from morning_radar.publishing import SiteBuilder
from morning_radar.settings import (
    AppConfig,
    CompanyConfig,
    RepositoryConfig,
    SourceConfig,
    TopicConfig,
    load_model,
    load_model_list,
)
from morning_radar.storage import load_model as load_json_model
from morning_radar.storage import load_models, save_model, save_models
from morning_radar.time_utils import display_date, utc_now
from morning_radar.trends import TrendDetector

LOGGER = logging.getLogger(__name__)


class MorningRadarPipeline:
    def __init__(self, project_root: Path = Path(".")) -> None:
        self.root = project_root.resolve()
        self.app = load_model(self.root / "config/app.yaml", AppConfig)

    def run(
        self,
        *,
        fixtures: bool = False,
        dry_run: bool = False,
        force_notify: bool = False,
    ) -> DailyBrief:
        output_root = self.root / ".tmp/dry-run" if dry_run else self.root
        if fixtures:
            raw_items = FixtureCollector(self.root / "fixtures/sample_items.json").collect()
            now = max(item.fetched_at for item in raw_items)
            provider = FakeAIProvider()
            collection = CollectionResult(
                items=raw_items,
                raw_collected=len(raw_items),
                after_buffer=len(raw_items),
                after_dedup=len(raw_items),
            )
        else:
            now = utc_now()
            collection = self._production_collectors(output_root, now)
            raw_items = collection.items
            provider = DeepSeekProvider.from_environment(
                budget=AIBudget(
                    self.app.maximum_ai_calls,
                    self.app.maximum_ai_input_characters,
                    self.app.maximum_ai_items,
                ),
                prompt_dir=self.root / "prompts",
            )

        recent = filter_news_window(
            raw_items,
            now=now,
            hours=self.app.news_window_hours,
        )
        stories = build_stories(recent, provider=provider, now=now)
        brief_date = display_date(now)
        story_history = self._story_history(output_root, brief_date)
        story_history[brief_date] = stories
        signals = TrendDetector(
            github_threshold=self.app.github_growth_threshold,
            market_threshold=self.app.market_movement_threshold,
        ).detect(
            story_history=story_history,
            github_snapshots=self._snapshots(
                output_root / "data/snapshots/github", GitHubSnapshot
            ),
            market_snapshots=self._snapshots(
                output_root / "data/snapshots/market", MarketSnapshot
            ),
            current_date=brief_date,
            now=now,
        )
        brief = generate_daily_brief(
            brief_date=brief_date,
            generated_at=now,
            timezone=self.app.timezone,
            stories=stories,
            signals=signals,
            provider=provider,
            limits=BriefLimits(maximum_items=self.app.maximum_brief_items),
            enabled_sections=self.app.enabled_sections,
            run_stats={
                "raw_items": len(raw_items),
                "recent_items": len(recent),
                "stories": len(stories),
                "signals": len(signals),
                "fixture_mode": fixtures,
                "dry_run": dry_run,
            },
        )
        selected_brief_items = sum(
            len(items)
            for items in (
                brief.top_stories,
                brief.market_and_companies,
                brief.ai_and_open_source,
                brief.trend_radar,
                brief.developer_discussions,
            )
        )
        ai_calls = getattr(getattr(provider, "budget", None), "calls_used", 0)
        LOGGER.info(
            "Pipeline stats: raw_collected=%d after_buffer=%d after_dedup=%d "
            "after_global_cap=%d recent_24h=%d stories=%d signals=%d "
            "selected_brief_items=%d ai_calls=%d",
            collection.raw_collected,
            collection.after_buffer,
            collection.after_dedup,
            len(raw_items),
            len(recent),
            len(stories),
            len(signals),
            selected_brief_items,
            ai_calls,
        )
        self._save_outputs(output_root, brief_date, raw_items, stories, signals, brief)
        self.build_site(output_root=output_root)
        if not fixtures and not dry_run:
            self._notifier(output_root).notify(brief, force=force_notify)
        return brief

    def _production_collectors(self, output_root: Path, now) -> CollectionResult:
        sources = load_model_list(self.root / "config/sources.yaml", "sources", SourceConfig)
        topics = load_model_list(self.root / "config/topics.yaml", "topics", TopicConfig)
        repositories = load_model_list(
            self.root / "config/repositories.yaml", "repositories", RepositoryConfig
        )
        companies = load_model_list(
            self.root / "config/companies.yaml", "companies", CompanyConfig
        )
        http = HttpClient(
            timeout_seconds=self.app.request_timeout_seconds,
            attempts=self.app.request_retry_attempts,
        )
        keywords = list(dict.fromkeys(word for topic in topics for word in topic.keywords))
        collectors = [
            RSSCollector(
                sources,
                http=http,
                state_path=output_root / "data/state/rss.json",
                now=now,
            ),
            GitHubCollector(
                repositories,
                http=http,
                snapshot_dir=output_root / "data/snapshots/github",
                token=os.getenv("GITHUB_TOKEN"),
                now=now,
            ),
            HackerNewsCollector(http=http, keywords=keywords, now=now),
            MarketCollector(
                companies,
                provider=YFinanceHistoryProvider(),
                snapshot_dir=output_root / "data/snapshots/market",
                now=now,
            ),
        ]
        collection_hours = (
            self.app.news_window_hours + self.app.collection_buffer_hours
        )
        return collect_available(
            collectors,
            filter_items=lambda items: filter_news_window(
                items,
                now=now,
                hours=collection_hours,
            ),
            maximum_items=self.app.maximum_raw_items,
        )

    def _save_outputs(self, root, brief_date, raw, stories, signals, brief) -> None:
        name = f"{brief_date}.json"
        save_models(root / "data/raw" / name, raw)
        save_models(root / "data/stories" / name, stories)
        save_models(root / "data/signals" / name, signals)
        save_model(root / "data/briefs" / name, brief)

    def _story_history(self, root: Path, current: date) -> dict[date, list[Story]]:
        result = {}
        for offset in range(1, self.app.trend_window_days + 1):
            day = current - timedelta(days=offset)
            path = root / "data/stories" / f"{day}.json"
            if path.exists():
                result[day] = load_models(path, Story)
        return result

    def _snapshots(self, directory: Path, model_type):
        values = []
        if directory.exists():
            for path in sorted(directory.glob("*.json"))[-self.app.trend_window_days :]:
                values.extend(load_models(path, model_type))
        return values

    def build_site(self, *, output_root: Path | None = None) -> None:
        root = output_root or self.root
        brief_dir = root / "data/briefs"
        briefs = [
            load_json_model(path, DailyBrief)
            for path in sorted(brief_dir.glob("*.json"))
        ]
        SiteBuilder(
            template_dir=self.root / "templates",
            output_dir=root / "site",
        ).build(briefs, stylesheet=self.root / "site/assets/style.css")

    def _notifier(self, root: Path) -> WxPusherNotifier:
        return WxPusherNotifier(
            config=WxPusherConfig(
                os.getenv("WXPUSHER_APP_TOKEN", ""),
                [
                    value.strip()
                    for value in os.getenv("WXPUSHER_UIDS", "").split(",")
                    if value.strip()
                ],
                os.getenv("PUBLIC_SITE_URL", ""),
            ),
            state_path=root / "data/state/notifications.json",
        )

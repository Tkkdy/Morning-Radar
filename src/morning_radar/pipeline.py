"""Main Morning Radar orchestration flow."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import date, timedelta
from pathlib import Path

from morning_radar.ai import AIBudget, DeepSeekProvider, FakeAIProvider
from morning_radar.briefing import (
    BriefLimits,
    generate_daily_brief_with_memory,
    ranked_eligible_stories,
)
from morning_radar.collectors import (
    AIHOTCollector,
    CollectionResult,
    FixtureCollector,
    collect_available,
)
from morning_radar.collectors.github import GitHubCollector
from morning_radar.collectors.hacker_news import HackerNewsCollector
from morning_radar.collectors.http import HttpClient
from morning_radar.collectors.market import MarketCollector, YFinanceHistoryProvider
from morning_radar.collectors.rss import RSSCollector
from morning_radar.continuity.candidates import StoryMemory
from morning_radar.continuity.engine import ContinuityRunResult, resolve_daily_continuity
from morning_radar.continuity.history import (
    load_continuity_history,
    load_story_memory,
)
from morning_radar.continuity.materialize import (
    materialize_judgements,
    materialize_open_watches,
    merge_daily_continuity,
)
from morning_radar.continuity.projection import apply_continuity_to_brief
from morning_radar.continuity.validation import validate_daily_continuity
from morning_radar.editorial.evaluator import evaluate_editorial
from morning_radar.models import (
    DailyBrief,
    DailyContinuity,
    DailyTendencies,
    GitHubSnapshot,
    MarketSnapshot,
    Story,
    StoryOccurrenceRef,
)
from morning_radar.notifications import WxPusherConfig, WxPusherNotifier
from morning_radar.processing import (
    build_stories,
    filter_news_window,
    filter_story_candidate_inputs,
)
from morning_radar.publishing import SiteBuilder
from morning_radar.research import resolve_research
from morning_radar.research.engine import eligible_story_inputs
from morning_radar.settings import (
    AppConfig,
    CompanyConfig,
    PersonConfig,
    RepositoryConfig,
    SourceConfig,
    TopicConfig,
    active_practitioner_sources,
    load_model,
    load_model_list,
    practitioner_coverage_stats,
)
from morning_radar.storage import load_model as load_json_model
from morning_radar.storage import load_models, save_model, save_models
from morning_radar.tendencies import (
    TendencyRunResult,
    load_tendency_history,
    project_tendencies,
    reduce_tendencies,
)
from morning_radar.time_utils import display_date, utc_now
from morning_radar.trends import TrendDetector

LOGGER = logging.getLogger(__name__)
RESERVED_LOGICAL_AI_TASKS = 7


def _displayed_item_counts(brief: DailyBrief) -> tuple[int, int, int]:
    main_items = sum(
        len(items)
        for items in (
            brief.top_stories,
            brief.market_and_companies,
            brief.ai_and_open_source,
            brief.trend_radar,
            brief.developer_discussions,
        )
    )
    other_items = len(brief.other_reading)
    return main_items, other_items, main_items + other_items


def _call_safe_story_candidate_limit(
    *,
    maximum_calls: int,
    maximum_items: int,
) -> int:
    remaining_story_calls = max(0, maximum_calls - RESERVED_LOGICAL_AI_TASKS)
    return min(maximum_items, remaining_story_calls * 2 // 5)


def _resolve_fast_continuity(
    app: AppConfig,
    *,
    current_date,
    generated_at,
    stories,
    historical_story_memory,
    continuity_history,
    provider,
    brief_ai_stories,
    enable_ai: bool = True,
    deadline_monotonic: float | None = None,
) -> ContinuityRunResult:
    return resolve_daily_continuity(
        current_date=current_date,
        generated_at=generated_at,
        current_stories=stories,
        historical_stories=historical_story_memory,
        continuity_history=continuity_history,
        provider=provider,
        history_days=app.continuity_history_days,
        maximum_candidates=app.maximum_continuity_candidates,
        maximum_open_watches=app.maximum_open_watches_considered,
        maximum_ai_items=app.maximum_ai_items,
        maximum_input_characters=app.maximum_continuity_input_characters,
        reserved_input_characters=(
            sum(len(story.model_dump_json()) for story in brief_ai_stories) + 5000
        ),
        enable_ai=enable_ai,
        deadline_monotonic=deadline_monotonic,
    )


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
        notify: bool = True,
    ) -> DailyBrief:
        history_root = self.root
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
            people = load_model_list(self.root / "config/people.yaml", "people", PersonConfig)
        else:
            now = utc_now()
            collection = self._production_collectors(output_root, history_root, now)
            raw_items = collection.items
            people = load_model_list(self.root / "config/people.yaml", "people", PersonConfig)
            provider = DeepSeekProvider.from_environment(
                budget=AIBudget(
                    self.app.maximum_ai_calls,
                    self.app.maximum_ai_input_characters,
                    self.app.maximum_ai_items,
                    self.app.maximum_ai_network_requests,
                ),
                prompt_dir=self.root / "prompts",
            )

        recent = filter_news_window(
            raw_items,
            now=now,
            hours=self.app.news_window_hours,
        )
        story_candidate_items, routine_market_suppressed = filter_story_candidate_inputs(
            recent,
            market_movement_threshold=self.app.market_movement_threshold,
        )
        research_result = resolve_research(
            recent,
            provider=provider,
            maximum_cases=self.app.maximum_research_cases,
            maximum_radar_signals=self.app.maximum_radar_signals,
            maximum_input_characters=(self.app.maximum_research_input_characters),
        )
        story_candidate_items = eligible_story_inputs(
            story_candidate_items,
            verified_item_ids=research_result.verified_item_ids,
        )
        # Reserve calls for classification, continuity, brief, direction, research,
        # and tendency. A rejected two-item candidate group costs five calls:
        # one group merge plus merge + score for each resulting Story.
        ai_candidate_limit = _call_safe_story_candidate_limit(
            maximum_calls=self.app.maximum_ai_calls,
            maximum_items=self.app.maximum_ai_items,
        )
        stories = build_stories(
            story_candidate_items,
            provider=provider,
            now=now,
            maximum_ai_items=ai_candidate_limit,
        )
        brief_date = display_date(now)
        editorial_result = evaluate_editorial(
            stories,
            provider=provider,
            current_date=brief_date,
            generated_at=now,
            enabled=self.app.editorial.enabled,
            shadow_mode=self.app.editorial.shadow_mode,
            profile_version=self.app.editorial.profile_version,
            maximum_stories=self.app.editorial.maximum_stories,
        )
        brief_limits = BriefLimits(maximum_items=self.app.maximum_brief_items)
        if editorial_result.active:
            assert editorial_result.selection is not None
            story_by_id = {story.id: story for story in stories}
            brief_ai_stories = [
                story_by_id[story_id]
                for story_id in editorial_result.selection.visible_story_ids[
                    : brief_limits.maximum_items
                ]
            ]
        else:
            brief_ai_stories = ranked_eligible_stories(
                stories,
                relevance_threshold=self.app.relevance_threshold,
                importance_threshold=self.app.importance_threshold,
            )[: brief_limits.maximum_items]
        current_story_memory = [
            StoryMemory(
                ref=StoryOccurrenceRef(date=brief_date, story_id=story.id),
                story=story,
            )
            for story in stories
        ]
        try:
            historical_story_memory = load_story_memory(
                history_root,
                current_date=brief_date,
                history_days=self.app.continuity_history_days,
            )
            continuity_history = load_continuity_history(
                history_root,
                current_date=brief_date,
            )
            if dry_run:
                continuity_history = [
                    daily for daily in continuity_history if daily.date < brief_date
                ]
            continuity_deadline = (
                time.monotonic() + self.app.fast_continuity_join_timeout_seconds
            )
            continuity_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="fast-continuity"
            )
            continuity_future = continuity_executor.submit(
                _resolve_fast_continuity,
                self.app,
                current_date=brief_date,
                generated_at=now,
                stories=stories,
                historical_story_memory=historical_story_memory,
                continuity_history=continuity_history,
                provider=provider,
                brief_ai_stories=brief_ai_stories,
                deadline_monotonic=continuity_deadline,
            )
        except (OSError, ValueError):
            LOGGER.exception("Continuity degradation: history could not be loaded or reduced")
            historical_story_memory = []
            continuity_history = []
            continuity_executor = None
            continuity_future = None
            continuity_deadline = None
            continuity_result = ContinuityRunResult(
                daily=DailyContinuity(date=brief_date, generated_at=now),
                stats={"continuity_unavailable": 1},
            )
        story_history = self._story_history(history_root, brief_date)
        story_history[brief_date] = stories
        signals = TrendDetector(
            github_threshold=self.app.github_growth_threshold,
            market_threshold=self.app.market_movement_threshold,
            company_names={
                company.name
                for company in load_model_list(
                    self.root / "config/companies.yaml",
                    "companies",
                    CompanyConfig,
                )
            },
        ).detect(
            story_history=story_history,
            github_snapshots=self._snapshots(
                history_root / "data/snapshots/github",
                output_root / "data/snapshots/github",
                GitHubSnapshot,
                brief_date,
            ),
            market_snapshots=self._snapshots(
                history_root / "data/snapshots/market",
                output_root / "data/snapshots/market",
                MarketSnapshot,
                brief_date,
            ),
            current_date=brief_date,
            now=now,
        )
        brief_result = generate_daily_brief_with_memory(
            brief_date=brief_date,
            generated_at=now,
            timezone=self.app.timezone,
            stories=stories,
            signals=signals,
            provider=provider,
            limits=brief_limits,
            enabled_sections=self.app.enabled_sections,
            relevance_threshold=self.app.relevance_threshold,
            importance_threshold=self.app.importance_threshold,
            maximum_ai_items=self.app.maximum_ai_items,
            editorial_result=editorial_result,
            run_stats={
                "after_global_cap": len(raw_items),
                "recent_24h": len(recent),
                "story_candidate_input": len(story_candidate_items),
                "routine_market_suppressed": routine_market_suppressed,
                "stories": len(stories),
                "signals": len(signals),
                "fixture_mode": fixtures,
                "dry_run": dry_run,
                "editorial_enabled": editorial_result.daily.enabled,
                "editorial_shadow_mode": editorial_result.daily.shadow_mode,
                "editorial_degraded": editorial_result.daily.degraded,
                "editorial_decisions": len(editorial_result.daily.decisions),
                "aihot_enabled": self.app.aihot.enabled,
                **practitioner_coverage_stats(people),
                **research_result.stats,
            },
        )
        if continuity_future is not None:
            try:
                assert continuity_deadline is not None
                continuity_result = continuity_future.result(
                    timeout=max(0, continuity_deadline - time.monotonic())
                )
            except FuturesTimeoutError:
                LOGGER.warning("Fast Continuity timed out; publishing deterministic backbone")
                continuity_future.cancel()
                continuity_result = _resolve_fast_continuity(
                    self.app,
                    current_date=brief_date,
                    generated_at=now,
                    stories=stories,
                    historical_story_memory=historical_story_memory,
                    continuity_history=continuity_history,
                    provider=provider,
                    brief_ai_stories=brief_ai_stories,
                    enable_ai=False,
                )
                continuity_result.stats["fast_continuity_timeout"] = 1
                continuity_result.stats["fast_continuity_degraded"] = 1
            finally:
                assert continuity_executor is not None
                continuity_executor.shutdown(wait=False, cancel_futures=True)
        brief_result = brief_result.__class__(
            brief=brief_result.brief.model_copy(
                update={
                    "run_stats": {
                        **brief_result.brief.run_stats,
                        **continuity_result.stats,
                    }
                }
            ),
            watch_drafts=brief_result.watch_drafts,
            judgement_drafts=brief_result.judgement_drafts,
        )
        opened_watches = materialize_open_watches(
            brief_result.watch_drafts,
            brief_date=brief_date,
            recorded_at=now,
            stories=stories,
        )
        new_judgements = materialize_judgements(
            brief_result.judgement_drafts,
            brief_date=brief_date,
            recorded_at=now,
            stories=stories,
        )
        new_daily_continuity = continuity_result.daily.model_copy(
            update={
                "watch_events": [
                    *continuity_result.daily.watch_events,
                    *opened_watches,
                ],
                "judgements": [
                    *continuity_result.daily.judgements,
                    *new_judgements,
                ],
            }
        )
        existing_daily_continuity = next(
            (daily for daily in continuity_history if daily.date == brief_date),
            None,
        )
        daily_continuity = merge_daily_continuity(
            existing_daily_continuity,
            new_daily_continuity,
        )
        try:
            validate_daily_continuity(
                daily_continuity,
                stories=[*historical_story_memory, *current_story_memory],
            )
        except ValueError:
            LOGGER.exception(
                "Continuity degradation: final records failed validation; records omitted"
            )
            daily_continuity = existing_daily_continuity or DailyContinuity(
                date=brief_date,
                generated_at=now,
            )
            opened_watches = []
            new_judgements = []
        try:
            tendency_history = load_tendency_history(history_root, current_date=brief_date)
            tendency_views = reduce_tendencies(tendency_history)
            tendency_result = TendencyRunResult(
                daily=DailyTendencies(date=brief_date, generated_at=now),
                current_views=tendency_views,
                brief_tendencies=project_tendencies(tendency_views),
                stats={
                    "tendency_workflow_status": "persisted_projection",
                    "tendency_logical_ai_calls": 0,
                },
            )
        except (OSError, ValueError):
            LOGGER.exception("Tendency projection unavailable; main workflow continues")
            tendency_result = TendencyRunResult(
                daily=DailyTendencies(date=brief_date, generated_at=now),
                stats={
                    "tendency_workflow_status": "unavailable",
                    "tendency_logical_ai_calls": 0,
                },
            )
        brief = apply_continuity_to_brief(
            brief_result.brief,
            daily_continuity,
            story_memory=[*historical_story_memory, *current_story_memory],
            current_judgements=continuity_result.current_judgements,
        )
        brief = brief.model_copy(
            update={
                "radar_signals": research_result.radar_signals,
                "tendencies": tendency_result.brief_tendencies,
                "run_stats": {
                    **brief.run_stats,
                    **tendency_result.stats,
                    "judgements_created": len(new_judgements),
                    "judgement_created": len(new_judgements),
                    "judgement_deep_review_triggers": 0,
                    "judgement_deep_review_calls": 0,
                    "structured_watches_opened": len(opened_watches),
                },
            }
        )
        (
            main_brief_items,
            other_reading_items,
            total_displayed_items,
        ) = _displayed_item_counts(brief)
        budget = getattr(provider, "budget", None)
        logical_ai_calls = getattr(budget, "calls_used", 0)
        network_ai_requests = getattr(budget, "network_requests_used", 0)
        ai_input_characters = getattr(budget, "input_characters_used", 0)
        usage_stats = (
            budget.usage_run_stats()
            if budget is not None and hasattr(budget, "usage_run_stats")
            else {}
        )
        brief = brief.model_copy(
            update={
                "run_stats": {
                    **brief.run_stats,
                    "main_brief_items": main_brief_items,
                    "other_reading_items": other_reading_items,
                    "total_displayed_items": total_displayed_items,
                    "logical_ai_calls": logical_ai_calls,
                    "network_ai_requests": network_ai_requests,
                    "ai_input_characters": ai_input_characters,
                    "ai_maximum_input_characters": (self.app.maximum_ai_input_characters),
                    "ai_provider": getattr(provider, "provider_name", "fake"),
                    "task": "daily_pipeline",
                    "provider": getattr(provider, "provider_name", "fake"),
                    "ai_model": getattr(provider, "model", "fixture"),
                    "model": getattr(provider, "model", "fixture"),
                    "provider_circuit_opened": bool(getattr(provider, "circuit_open", False)),
                    "provider_circuit_reason": (getattr(provider, "circuit_reason", None) or ""),
                    **usage_stats,
                }
            }
        )
        threshold_eligible_stories = int(brief.run_stats.get("threshold_eligible_stories", 0))
        LOGGER.info(
            "Pipeline stats: raw_collected=%d after_buffer=%d after_dedup=%d "
            "after_global_cap=%d recent_24h=%d story_candidate_input=%d "
            "routine_market_suppressed=%d stories=%d threshold_eligible_stories=%d "
            "signals=%d main_brief_items=%d other_reading_items=%d "
            "total_displayed_items=%d logical_ai_calls=%d network_ai_requests=%d",
            collection.raw_collected,
            collection.after_buffer,
            collection.after_dedup,
            len(raw_items),
            len(recent),
            len(story_candidate_items),
            routine_market_suppressed,
            len(stories),
            threshold_eligible_stories,
            len(signals),
            main_brief_items,
            other_reading_items,
            total_displayed_items,
            logical_ai_calls,
            network_ai_requests,
        )
        LOGGER.info(
            "AI budget stats: input_characters=%d maximum_input_characters=%d "
            "logical_calls=%d maximum_logical_calls=%d",
            ai_input_characters,
            self.app.maximum_ai_input_characters,
            logical_ai_calls,
            self.app.maximum_ai_calls,
        )
        LOGGER.info(
            "v0.35 intelligence stats: configured_seed_count=%s "
            "active_channel_count=%s practitioners_with_active_channels=%s "
            "aihot_enabled=%s research_cases=%s radar_signals=%s "
            "research_logical_ai_calls=%s tendency_clusters=%s "
            "tendency_decisions=%s tendency_logical_ai_calls=%s",
            brief.run_stats.get("configured_seed_count", 0),
            brief.run_stats.get("active_channel_count", 0),
            brief.run_stats.get("practitioners_with_active_channels", 0),
            self.app.aihot.enabled,
            brief.run_stats.get("research_cases", 0),
            brief.run_stats.get("radar_signals", 0),
            brief.run_stats.get("research_logical_ai_calls", 0),
            brief.run_stats.get("tendency_clusters", 0),
            brief.run_stats.get("tendency_decisions", 0),
            brief.run_stats.get("tendency_logical_ai_calls", 0),
        )
        LOGGER.info(
            "Continuity stats: historical_story_candidates=%s "
            "continuity_candidates=%s relations_confirmed=%s relations_rejected=%s "
            "relations_unresolved=%s "
            "open_watches_considered=%s watch_matches=%s judgements_created=%s "
            "judgement_updates=%s revised=%s overturned=%s needs_review=%s "
            "continuity_logical_ai_calls=%s continuity_network_requests=%s "
            "continuity_input_chars=%s continuity_relation_inputs=%s "
            "continuity_watch_inputs=%s continuity_judgement_inputs=%s "
            "continuity_character_budget_available=%s continuity_budget_skipped=%s",
            brief.run_stats.get("historical_story_candidates", 0),
            brief.run_stats.get("continuity_candidates", 0),
            brief.run_stats.get("relations_confirmed", 0),
            brief.run_stats.get("relations_rejected", 0),
            brief.run_stats.get("relations_unresolved", 0),
            brief.run_stats.get("open_watches_considered", 0),
            brief.run_stats.get("watch_matches", 0),
            brief.run_stats.get("judgements_created", 0),
            brief.run_stats.get("judgement_updates", 0),
            brief.run_stats.get("revised", 0),
            brief.run_stats.get("overturned", 0),
            brief.run_stats.get("needs_review", 0),
            brief.run_stats.get("continuity_logical_ai_calls", 0),
            brief.run_stats.get("continuity_network_requests", 0),
            brief.run_stats.get("continuity_input_chars", 0),
            brief.run_stats.get("continuity_relation_inputs", 0),
            brief.run_stats.get("continuity_watch_inputs", 0),
            brief.run_stats.get("continuity_judgement_inputs", 0),
            brief.run_stats.get("continuity_character_budget_available", 0),
            brief.run_stats.get("continuity_budget_skipped", 0),
        )
        if usage_stats:
            LOGGER.info("AI token usage by task: %s", usage_stats)
        self._save_outputs(
            output_root,
            brief_date,
            raw_items,
            stories,
            signals,
            brief,
            daily_continuity,
            research_result.radar_signals,
            tendency_result.daily,
            editorial_result.daily,
        )
        self.build_site(output_root=output_root, history_root=history_root)
        if notify and not fixtures and not dry_run:
            self._notifier(output_root).notify(brief, force=force_notify)
        return brief

    def notify_latest(self, *, force: bool = False) -> bool:
        brief_paths = sorted((self.root / "data/briefs").glob("*.json"))
        if not brief_paths:
            raise FileNotFoundError("No saved DailyBrief is available for notification")
        brief = load_json_model(brief_paths[-1], DailyBrief)
        return self._notifier(self.root).notify(brief, force=force)

    def _production_collectors(
        self,
        output_root: Path,
        history_root: Path,
        now,
    ) -> CollectionResult:
        sources = load_model_list(self.root / "config/sources.yaml", "sources", SourceConfig)
        people = load_model_list(self.root / "config/people.yaml", "people", PersonConfig)
        sources.extend(active_practitioner_sources(people))
        topics = load_model_list(self.root / "config/topics.yaml", "topics", TopicConfig)
        repositories = load_model_list(
            self.root / "config/repositories.yaml", "repositories", RepositoryConfig
        )
        companies = load_model_list(self.root / "config/companies.yaml", "companies", CompanyConfig)
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
                history_snapshot_dir=history_root / "data/snapshots/github",
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
            AIHOTCollector(
                self.app.aihot,
                http=http,
                state_path=output_root / "data/state/aihot.json",
                now=now,
            ),
        ]
        collection_hours = self.app.news_window_hours + self.app.collection_buffer_hours
        return collect_available(
            collectors,
            filter_items=lambda items: filter_news_window(
                items,
                now=now,
                hours=collection_hours,
            ),
            maximum_items=self.app.maximum_raw_items,
        )

    def _save_outputs(
        self,
        root,
        brief_date,
        raw,
        stories,
        signals,
        brief,
        continuity,
        radar_signals,
        tendencies,
        editorial,
    ) -> None:
        name = f"{brief_date}.json"
        save_models(root / "data/raw" / name, raw)
        save_models(root / "data/stories" / name, stories)
        save_models(root / "data/signals" / name, signals)
        save_model(root / "data/briefs" / name, brief)
        save_model(root / "data/continuity" / name, continuity)
        save_models(root / "data/radar_signals" / name, radar_signals)
        if brief.run_stats.get("fixture_mode"):
            save_model(root / "data/tendencies" / name, tendencies)
        try:
            save_model(root / "data/editorial" / name, editorial)
        except (OSError, TypeError, ValueError):
            LOGGER.exception(
                "Editorial degradation: decision artifact could not be saved; "
                "daily brief remains available"
            )

    def _story_history(self, root: Path, current: date) -> dict[date, list[Story]]:
        result = {}
        for offset in range(1, self.app.trend_window_days + 1):
            day = current - timedelta(days=offset)
            path = root / "data/stories" / f"{day}.json"
            if path.exists():
                result[day] = load_models(path, Story)
        return result

    def _snapshots(
        self,
        history_directory: Path,
        output_directory: Path,
        model_type,
        current_date: date,
    ):
        values = []
        paths_by_name: dict[str, Path] = {}
        if history_directory.exists():
            for path in sorted(history_directory.glob("*.json"))[-self.app.trend_window_days :]:
                paths_by_name[path.name] = path
        current_path = output_directory / f"{current_date}.json"
        if current_path.exists():
            paths_by_name[current_path.name] = current_path
        for path in sorted(paths_by_name.values(), key=lambda value: value.name):
            values.extend(load_models(path, model_type))
        return values

    def build_site(
        self,
        *,
        output_root: Path | None = None,
        history_root: Path | None = None,
    ) -> None:
        output = output_root or self.root
        history = history_root or self.root
        brief_by_date: dict[date, DailyBrief] = {}
        for root in dict.fromkeys((history, output)):
            for path in sorted((root / "data/briefs").glob("*.json")):
                brief = load_json_model(path, DailyBrief)
                brief_by_date[brief.date] = brief
        tendency_by_date: dict[date, DailyTendencies] = {}
        for root in dict.fromkeys((history, output)):
            tendency_dir = root / "data/tendencies"
            for path in sorted(tendency_dir.glob("*.json")):
                try:
                    tendency = load_json_model(path, DailyTendencies)
                    tendency_by_date[tendency.date] = tendency
                except (OSError, ValueError):
                    LOGGER.exception("Tendency projection: skipping invalid state file %s", path)
        for brief_date, brief in list(brief_by_date.items()):
            tendency_history = [
                item for day, item in sorted(tendency_by_date.items()) if day <= brief_date
            ]
            if tendency_history:
                brief_by_date[brief_date] = brief.model_copy(
                    update={"tendencies": project_tendencies(reduce_tendencies(tendency_history))}
                )
        continuity_by_date: dict[date, DailyContinuity] = {}
        for root in dict.fromkeys((history, output)):
            continuity_dir = root / "data/continuity"
            for path in sorted(continuity_dir.glob("*.json")):
                try:
                    continuity = load_json_model(path, DailyContinuity)
                    continuity_by_date[continuity.date] = continuity
                except (OSError, ValueError):
                    LOGGER.exception(
                        "Continuity degradation: skipping invalid site annotation file %s",
                        path,
                    )
        SiteBuilder(
            template_dir=self.root / "templates",
            output_dir=output / "site",
        ).build(
            list(brief_by_date.values()),
            stylesheet=self.root / "site/assets/style.css",
            continuities=list(continuity_by_date.values()),
        )

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

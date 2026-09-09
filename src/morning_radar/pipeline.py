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
from morning_radar.time_utils import display_date
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






def _brief_hash(brief) -> str:
    import hashlib
    import json

    payload = json.dumps(
        brief.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _artifact_digest(path: Path) -> str:
    from morning_radar.intake.generation import artifact_digest

    return artifact_digest(path)


def _merge_same_day_stories(output_root, brief_date, stories, *, replaced_item_ids: set[str]):
    from morning_radar.models import Story
    from morning_radar.processing.story_builder import rank_stories
    from morning_radar.storage import load_models

    path = output_root / "data/stories" / f"{brief_date}.json"
    previous = load_models(path, Story) if path.exists() else []
    kept = []
    for story in previous:
        if replaced_item_ids.intersection(story.source_item_ids):
            continue
        kept.append(story)
    merged = {story.id: story for story in kept}
    for story in stories:
        merged[story.id] = story
    return rank_stories(list(merged.values()))


def _record_site_build(output_root, brief) -> None:
    from morning_radar.storage import write_json

    write_json(
        output_root / "data/state/site_build.json",
        {
            "brief_date": str(brief.date),
            "brief_hash": _brief_hash(brief),
        },
    )


def _site_matches_brief(output_root, brief) -> bool:
    from morning_radar.storage import read_json

    index = output_root / "site/index.html"
    marker = output_root / "data/state/site_build.json"
    if not index.exists() or not marker.exists():
        return False
    try:
        payload = read_json(marker)
    except (OSError, ValueError):
        return False
    return (
        payload.get("brief_date") == str(brief.date)
        and payload.get("brief_hash") == _brief_hash(brief)
    )

def _displayed_story_ids(brief) -> set[str]:
    ids: set[str] = set()
    for name in (
        "top_stories",
        "market_and_companies",
        "ai_and_open_source",
        "trend_radar",
        "developer_discussions",
        "other_reading",
    ):
        for item in getattr(brief, name, []) or []:
            ids.update(getattr(item, "story_ids", []) or [])
    return ids


def _record_processing_outcomes(
    prepared,
    *,
    stories,
    story_candidate_items,
    research_result,
    story_item_outcomes,
    brief,
    relevance_threshold,
    importance_threshold,
    persist: bool = True,
) -> None:
    from morning_radar.intake.models import ProcessingStatus, PublishStatus, ReasonCode
    from morning_radar.models import SourceRole

    selected_item_ids = {item.id for item in story_candidate_items}
    stories_by_item: dict[str, object] = {}
    for story in stories:
        for item_id in story.source_item_ids:
            stories_by_item[item_id] = story
    displayed = _displayed_story_ids(brief)
    now = prepared.process_now
    outcomes = getattr(research_result, "item_outcomes", {}) or {}
    for record in prepared.selection.records:
        story = stories_by_item.get(record.item.id)
        research_reason = outcomes.get(record.item.id)
        if story is None:
            if research_reason in {
                ReasonCode.RESEARCH_OUTPUT_INVALID,
                ReasonCode.RESEARCH_OUTPUT_TRUNCATED,
                ReasonCode.RESEARCH_CASE_MISSING,
            }:
                processing = ProcessingStatus.FAILED_RETRY
                reason = research_reason
                evidence = None
            elif research_reason is ReasonCode.WAITING_EVIDENCE:
                processing = ProcessingStatus.WAITING_EVIDENCE
                reason = ReasonCode.WAITING_EVIDENCE
                evidence = None
            elif story_item_outcomes.get(record.item.id) == ReasonCode.CLASSIFIED_IRRELEVANT.value:
                processing = ProcessingStatus.EXCLUDED
                reason = ReasonCode.CLASSIFIED_IRRELEVANT
                evidence = None
            elif story_item_outcomes.get(record.item.id) == ReasonCode.MERGE_FAILED.value:
                processing = ProcessingStatus.FAILED_RETRY
                reason = ReasonCode.MERGE_FAILED
                evidence = None
            elif story_item_outcomes.get(record.item.id) == ReasonCode.SCORE_FAILED.value:
                processing = ProcessingStatus.FAILED_RETRY
                reason = ReasonCode.SCORE_FAILED
                evidence = None
            elif research_reason is ReasonCode.RESEARCH_OUT_OF_SCOPE:
                processing = ProcessingStatus.EXCLUDED
                reason = ReasonCode.RESEARCH_OUT_OF_SCOPE
                evidence = None
            elif research_reason is ReasonCode.RESEARCH_DEFERRED:
                processing = ProcessingStatus.DEFERRED_BUDGET
                reason = ReasonCode.RESEARCH_DEFERRED
                evidence = None
            elif research_reason is ReasonCode.RESEARCH_FATAL:
                processing = ProcessingStatus.FAILED_RETRY
                reason = ReasonCode.RESEARCH_FATAL
                evidence = None
            elif record.item.id not in selected_item_ids:
                if record.item.source_role in {
                    SourceRole.PRACTITIONER,
                    SourceRole.UPSTREAM_DISCOVERY,
                }:
                    processing = ProcessingStatus.WAITING_EVIDENCE
                    reason = ReasonCode.WAITING_EVIDENCE
                else:
                    processing = ProcessingStatus.EXCLUDED
                    reason = ReasonCode.CLASSIFIED_IRRELEVANT
                evidence = None
            else:
                processing = ProcessingStatus.FAILED_RETRY
                reason = ReasonCode.STORY_BUILD_FAILED
                evidence = None
            current = prepared.ledger.get(record.input_id, record.content_version)
            attempts = (current.attempt_count if current else 0)
            if processing is ProcessingStatus.FAILED_RETRY:
                attempts += 1
            updates = {
                "processing": processing,
                "stage": "story",
                "outcome": reason.value if reason else "not_promoted",
                "reason_code": reason,
                "processed_at": now,
                "attempt_count": attempts,
            }
            if evidence is not None:
                updates["evidence"] = evidence
            prepared.ledger.update(
                record.input_id,
                record.content_version,
                now=now,
                **updates,
            )
            continue
        below = story.relevance_score < relevance_threshold
        merged = story.id if len(story.source_item_ids) > 1 else None
        shown = story.id in displayed
        prepared.ledger.update(
            record.input_id,
            record.content_version,
            now=now,
            processing=ProcessingStatus.COMPLETED,
            stage="story",
            outcome="below_relevance_threshold" if below else "processed",
            reason_code=(
                ReasonCode.BELOW_RELEVANCE_THRESHOLD if below else ReasonCode.PROCESSED
            ),
            story_id=story.id,
            merged_into=merged,
            relevance_score=story.relevance_score,
            importance_score=story.importance_score,
            relevance_threshold=relevance_threshold,
            importance_threshold=importance_threshold,
            score_rationale=(
                f"relevance={story.relevance_score:.2f} "
                f"threshold={relevance_threshold:.2f}"
            ),
            processed_at=now,
            publish=PublishStatus.GENERATED if shown else PublishStatus.NOT_GENERATED,
            brief_date=str(brief.date) if shown else None,
        )
    if persist:
        prepared.ledger.save()



def _mark_superseded_versions(prepared) -> None:
    from morning_radar.intake.models import ProcessingStatus, ReasonCode

    now = prepared.process_now
    unfinished = {
        ProcessingStatus.UNPROCESSED,
        ProcessingStatus.IN_PROGRESS,
        ProcessingStatus.DEFERRED_BUDGET,
        ProcessingStatus.FAILED_RETRY,
    }
    for record in prepared.selection.records:
        current = prepared.ledger.get(record.input_id, record.content_version)
        if current is None or current.processing is not ProcessingStatus.COMPLETED:
            continue
        for other in prepared.ledger.find_by_input_id(record.input_id):
            if other.content_version == record.content_version:
                continue
            if other.processing not in unfinished:
                continue
            winner_obs = current.durable_at or current.first_seen_at
            other_obs = other.durable_at or other.first_seen_at
            if other_obs >= winner_obs:
                continue
            prepared.ledger.update(
                other.input_id,
                other.content_version,
                now=now,
                processing=ProcessingStatus.EXCLUDED,
                reason_code=ReasonCode.SUPERSEDED,
                outcome="superseded",
                superseded_by=record.content_version,
                stage="version",
            )



def _generation_result_keys(prepared) -> set[str]:
    from morning_radar.intake.identity import intake_key
    from morning_radar.intake.models import ReasonCode

    keys = {
        intake_key(record.input_id, record.content_version)
        for record in prepared.selection.records
    }
    selected_inputs = {record.input_id for record in prepared.selection.records}
    for entry in prepared.ledger.ledger.entries.values():
        if entry.reason_code is ReasonCode.SUPERSEDED and entry.input_id in selected_inputs:
            keys.add(intake_key(entry.input_id, entry.content_version))
    return keys


def _mark_brief_generated(output_root, brief) -> None:
    from morning_radar.intake.publish import PublishStore

    artifact_path = output_root / "data/briefs" / f"{brief.date}.json"
    digest = _artifact_digest(artifact_path)
    PublishStore(output_root / "data/state/publish.json").mark_generated(
        brief_date=str(brief.date),
        brief_hash=digest,
        generated_at=brief.generated_at,
        artifact_path=f"data/briefs/{brief.date}.json",
    )



def _finalize_publish_status(
    prepared,
    brief,
    *,
    persist: bool = True,
    brief_hash: str | None = None,
) -> None:
    from morning_radar.intake.models import ProcessingStatus, PublishStatus, ReasonCode
    from morning_radar.intake.publish import PublishStore

    store_path = prepared.intake.output_root / "data/state/publish.json"
    record = PublishStore(store_path).get(str(brief.date))
    if brief_hash is None:
        brief_hash = record.brief_hash if record else None
    now = prepared.process_now
    displayed = _displayed_story_ids(brief)
    shown_keys = {
        (entry.input_id, entry.content_version)
        for entry in prepared.ledger.ledger.entries.values()
        if entry.story_id in displayed
        and entry.processing is ProcessingStatus.COMPLETED
        and entry.reason_code is not ReasonCode.SUPERSEDED
    }
    for entry in list(prepared.ledger.ledger.entries.values()):
        key = (entry.input_id, entry.content_version)
        if key not in shown_keys:
            continue
        prepared.ledger.update(
            entry.input_id,
            entry.content_version,
            now=now,
            publish=PublishStatus.GENERATED,
            brief_date=str(brief.date),
            brief_hash=brief_hash,
            reason_code=entry.reason_code or ReasonCode.GENERATED_NOT_DEPLOYED,
        )
    if persist:
        prepared.ledger.save()


class MorningRadarPipeline:
    def __init__(self, project_root: Path = Path(".")) -> None:
        self.root = project_root.resolve()
        self.app = load_model(self.root / "config/app.yaml", AppConfig)

    def collect(
        self,
        *,
        fixtures: bool = False,
        dry_run: bool = False,
        now=None,
    ):
        from morning_radar.intake.service import collect_intake

        return collect_intake(
            self.root,
            self.app,
            fixtures=fixtures,
            dry_run=dry_run,
            now=now,
        )

    def process(
        self,
        *,
        fixtures: bool = False,
        dry_run: bool = False,
        force_notify: bool = False,
        notify: bool = True,
        intake=None,
        batch_id: str | None = None,
        now=None,
    ) -> DailyBrief:
        return self.run(
            fixtures=fixtures,
            dry_run=dry_run,
            force_notify=force_notify,
            notify=notify,
            intake=intake,
            collect_first=False,
            batch_id=batch_id,
            now=now,
        )

    def run(
        self,
        *,
        fixtures: bool = False,
        dry_run: bool = False,
        force_notify: bool = False,
        notify: bool = True,
        intake=None,
        collect_first: bool | None = None,
        batch_id: str | None = None,
        now=None,
    ) -> DailyBrief:
        from morning_radar.intake.generation import heal_incomplete_generation
        from morning_radar.intake.service import (
            collect_intake,
            isolated_output_root,
            prepare_process,
        )

        if collect_first is None:
            collect_first = intake is None and batch_id is None
        if collect_first:
            intake = collect_intake(
                self.root,
                self.app,
                fixtures=fixtures,
                dry_run=dry_run,
                now=now,
            )
        heal_incomplete_generation(
            isolated_output_root(self.root, fixtures=fixtures, dry_run=dry_run)
        )
        prepared = prepare_process(
            self.root,
            self.app,
            fixtures=fixtures,
            dry_run=dry_run,
            intake=intake,
            batch_id=batch_id,
            now=now,
        )
        history_root = self.root
        output_root = prepared.intake.output_root
        now = prepared.process_now
        if not fixtures and not dry_run and not prepared.selection.records:
            existing_brief = output_root / "data/briefs" / f"{display_date(now)}.json"
            if existing_brief.exists():
                from morning_radar.intake.generation import generation_is_complete
                from morning_radar.models import DailyBrief as SavedBrief
                from morning_radar.storage import load_model as load_saved_brief

                if not generation_is_complete(output_root, str(display_date(now))):
                    raise RuntimeError("Incomplete generation cannot be reused")
                brief = load_saved_brief(existing_brief, SavedBrief)
                self._ensure_site_built(
                    output_root=output_root,
                    history_root=history_root,
                    brief=brief,
                )
                return brief
        collection = prepared.intake.collection
        people = prepared.people
        raw_items = [record.item for record in prepared.intake.checkpoint.items]
        recent = filter_news_window(
            raw_items,
            now=now,
            hours=self.app.news_window_hours,
        )
        if fixtures:
            provider = FakeAIProvider()
        else:
            provider = DeepSeekProvider.from_environment(
                budget=AIBudget(
                    self.app.maximum_ai_calls,
                    self.app.maximum_ai_input_characters,
                    self.app.maximum_ai_items,
                    self.app.maximum_ai_network_requests,
                ),
                prompt_dir=self.root / "prompts",
            )
        process_items = prepared.selection.items
        story_candidate_items, routine_market_suppressed = filter_story_candidate_inputs(
            process_items,
            market_movement_threshold=self.app.market_movement_threshold,
        )
        research_result = resolve_research(
            process_items,
            provider=provider,
            maximum_cases=self.app.maximum_research_cases,
            maximum_radar_signals=self.app.maximum_radar_signals,
            maximum_input_characters=(self.app.maximum_research_input_characters),
            item_retry_attempts=self.app.research_item_retry_attempts,
            split_retry_attempts=self.app.research_split_retry_attempts,
        )
        story_candidate_items = eligible_story_inputs(
            story_candidate_items,
            verified_item_ids=research_result.verified_item_ids,
        )
        brief_date = display_date(now)
        story_item_outcomes: dict[str, str] = {}
        stories = build_stories(
            story_candidate_items,
            provider=provider,
            now=now,
            maximum_ai_items=None,
            item_outcomes=story_item_outcomes,
        )
        new_stories = stories
        stories = _merge_same_day_stories(
            output_root,
            brief_date,
            new_stories,
            replaced_item_ids={
                item_id
                for story in new_stories
                for item_id in story.source_item_ids
            },
        )
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
        _record_processing_outcomes(
            prepared,
            stories=new_stories,
            story_candidate_items=story_candidate_items,
            research_result=research_result,
            story_item_outcomes=story_item_outcomes,
            brief=brief,
            relevance_threshold=self.app.relevance_threshold,
            importance_threshold=self.app.importance_threshold,
            persist=False,
        )
        _mark_superseded_versions(prepared)
        self._commit_generation(
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
            ledger=prepared.ledger,
            result_keys=_generation_result_keys(prepared),
        )
        digest = _artifact_digest(output_root / "data/briefs" / f"{brief_date}.json")
        _mark_brief_generated(output_root, brief)
        _finalize_publish_status(prepared, brief, brief_hash=digest)
        self.build_site(output_root=output_root, history_root=history_root)
        _record_site_build(output_root, brief)
        if notify and not fixtures and not dry_run:
            self._notifier(output_root).notify(brief, force=force_notify)
        return brief

    def notify_latest(self, *, force: bool = False) -> bool:
        from morning_radar.intake.generation import (
            generation_is_complete,
            heal_incomplete_generation,
        )

        heal_incomplete_generation(self.root)
        brief_paths = sorted((self.root / "data/briefs").glob("*.json"))
        if not brief_paths:
            raise FileNotFoundError("No saved DailyBrief is available for notification")
        brief = load_json_model(brief_paths[-1], DailyBrief)
        if not generation_is_complete(self.root, str(brief.date)):
            raise RuntimeError(f"Incomplete or untrusted generation for {brief.date}")
        brief_paths = sorted((self.root / "data/briefs").glob("*.json"))
        brief = load_json_model(brief_paths[-1], DailyBrief)
        return self._notifier(self.root).notify(brief, force=force)

    def _commit_generation(
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
        *,
        ledger,
        result_keys: set[str] | None = None,
    ) -> None:
        from morning_radar.intake.generation import (
            commit_prepared_generation,
            compute_generation_id,
            save_prepared_generation,
        )
        outputs = {
                "raw": [item.model_dump(mode="json") for item in raw],
                "stories": [item.model_dump(mode="json") for item in stories],
                "signals": [item.model_dump(mode="json") for item in signals],
                "brief": brief.model_dump(mode="json"),
                "continuity": continuity.model_dump(mode="json"),
                "radar_signals": [item.model_dump(mode="json") for item in radar_signals],
                "tendencies": (
                    tendencies.model_dump(mode="json")
                    if brief.run_stats.get("fixture_mode")
                    else None
                ),
                "editorial": editorial.model_dump(mode="json") if editorial is not None else None,
        }
        payload = {
            "brief_date": str(brief_date),
            "generation_id": compute_generation_id(str(brief_date), outputs),
            "result_keys": sorted(result_keys or []),
            "selection_keys": sorted(result_keys or []),
            "outputs": outputs,
            "ledger": ledger.ledger.model_dump(mode="json"),
        }
        save_prepared_generation(root, payload)
        commit_prepared_generation(root, payload)

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

    def _ensure_site_built(self, *, output_root, history_root, brief) -> None:
        if _site_matches_brief(output_root, brief):
            return
        self.build_site(output_root=output_root, history_root=history_root)
        _record_site_build(output_root, brief)
        if not _site_matches_brief(output_root, brief):
            raise RuntimeError("Site build did not produce the current brief")

    def build_site(
        self,
        *,
        output_root: Path | None = None,
        history_root: Path | None = None,
    ) -> None:
        output = output_root or self.root
        history = history_root or self.root
        from morning_radar.intake.generation import heal_incomplete_generation

        heal_incomplete_generation(output)
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

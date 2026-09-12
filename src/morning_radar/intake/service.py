"""Collect durable intake without creating an AI provider."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from morning_radar.collectors import (
    AIHOTCollector,
    CollectionResult,
    DeepSeekUpdatesCollector,
    FixtureCollector,
    HNSearchCollector,
    collect_available,
)
from morning_radar.collectors.github import GitHubCollector
from morning_radar.collectors.hacker_news import HackerNewsCollector
from morning_radar.collectors.http import HttpClient, RequestStartBudget
from morning_radar.collectors.market import MarketCollector, YFinanceHistoryProvider
from morning_radar.collectors.rss import RSSCollector
from morning_radar.intake.candidates import (
    ProcessSelection,
    select_process_candidates,
    split_latest_versions,
)
from morning_radar.intake.checkpoint import (
    IntakeRun,
    commit_collector_state,
    inconsistent_cache_sources,
    latest_complete_checkpoint,
    load_checkpoint_by_batch_id,
    mark_source_state_committed,
    pending_source_state,
    write_intake_checkpoint,
)
from morning_radar.intake.identity import intake_key
from morning_radar.intake.ledger import ProcessingLedgerStore
from morning_radar.intake.models import (
    INTAKE_POLICY_VERSION,
    ProcessingStatus,
    ReasonCode,
)
from morning_radar.intake.recovery import recover_unfinished_records
from morning_radar.processing import filter_news_window
from morning_radar.settings import (
    AppConfig,
    CompanyConfig,
    LabWatchlistConfig,
    PersonConfig,
    RepositoryConfig,
    SourceConfig,
    TopicConfig,
    active_practitioner_sources,
    load_model,
    load_model_list,
)
from morning_radar.storage import save_models
from morning_radar.time_utils import display_date, hours_ago, utc_now

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PreparedProcess:
    intake: IntakeRun
    selection: ProcessSelection
    ledger: ProcessingLedgerStore
    people: list[PersonConfig]
    process_now: datetime
    batch_id: str


def isolated_output_root(project_root: Path, *, fixtures: bool, dry_run: bool) -> Path:
    if fixtures or dry_run:
        return project_root / ".tmp/dry-run"
    return project_root


def ledger_path(root: Path) -> Path:
    return root / "data/intake/ledger.json"


def collect_intake(
    project_root: Path,
    app: AppConfig,
    *,
    fixtures: bool = False,
    dry_run: bool = False,
    now: datetime | None = None,
) -> IntakeRun:
    """Parse and persist inputs. Never constructs an AI provider."""
    output_root = isolated_output_root(project_root, fixtures=fixtures, dry_run=dry_run)
    collectors: list[object] = []
    if fixtures:
        raw_items = FixtureCollector(project_root / "fixtures/sample_items.json").collect()
        clock = now or max(item.fetched_at for item in raw_items)
        collection = CollectionResult(
            items=raw_items,
            raw_collected=len(raw_items),
            after_buffer=len(raw_items),
            after_dedup=len(raw_items),
        )
        source_state: dict = {}
        cache_inconsistencies: list[str] = []
    else:
        clock = now or utc_now()
        collection, collectors, cache_inconsistencies = _production_collect(
            project_root,
            output_root,
            app,
            clock,
        )
        raw_items = collection.items
        source_state = pending_source_state(collectors)

    ledger = ProcessingLedgerStore(ledger_path(output_root))
    first_seen = {
        entry.input_id: ledger.first_seen_at(entry.input_id, clock)
        for entry in ledger.ledger.entries.values()
    }
    cutoff_at = hours_ago(
        clock,
        hours=app.news_window_hours + app.collection_buffer_hours,
    )
    checkpoint = write_intake_checkpoint(
        output_root,
        items=raw_items,
        now=clock,
        cutoff_at=cutoff_at,
        collection=collection,
        source_state=source_state,
        cache_inconsistencies=cache_inconsistencies,
        first_seen=first_seen,
        discovery_audit=[
            entry for collector in collectors for entry in getattr(collector, "discovery_audit", [])
        ],
    )
    for record in checkpoint.items:
        record.durable_at = checkpoint.manifest.created_at
    commit_collector_state(collectors)
    checkpoint = mark_source_state_committed(output_root, checkpoint)
    ledger.upsert_checkpoint(checkpoint, now=clock)
    ledger.save()
    save_models(output_root / "data/raw" / f"{display_date(clock)}.json", raw_items)
    LOGGER.info(
        "Collect complete without AI: items=%d failures=%s isolated=%s",
        len(checkpoint.items),
        sorted(collection.failures),
        fixtures or dry_run,
    )
    return IntakeRun(
        checkpoint=checkpoint,
        collection=collection,
        now=clock,
        output_root=output_root,
        path=output_root / "data/intake/checkpoints" / f"{checkpoint.manifest.batch_id}.json",
        collected_at=clock,
    )


def _actionable_record(
    ledger: ProcessingLedgerStore,
    record,
    *,
    recompute_completed: bool = False,
) -> bool:
    entry = ledger.get(record.input_id, record.content_version)
    if entry is None:
        return True
    if recompute_completed:
        return True
    terminal = {
        ProcessingStatus.COMPLETED,
        ProcessingStatus.EXCLUDED,
        ProcessingStatus.WAITING_EVIDENCE,
    }
    same_version = entry.content_version == record.content_version
    same_policy = entry.last_policy_version == INTAKE_POLICY_VERSION
    return not (entry.processing in terminal and same_version and same_policy)


def _intake_run_from_checkpoint(output_root: Path, checkpoint) -> IntakeRun:
    from morning_radar.collectors.orchestrator import CollectorRunStats

    stats = {
        name: CollectorRunStats(
            collected=values.get("collected", 0),
            within_buffer=values.get("within_buffer", 0),
            retained=values.get("retained", 0),
        )
        for name, values in checkpoint.manifest.collector_stats.items()
    }
    return IntakeRun(
        checkpoint=checkpoint,
        collection=CollectionResult(
            items=[record.item for record in checkpoint.items],
            failures=dict(checkpoint.manifest.failures),
            collector_stats=stats,
            raw_collected=sum(stat.collected for stat in stats.values()),
            after_buffer=sum(stat.within_buffer for stat in stats.values()),
            after_dedup=len(checkpoint.items),
        ),
        now=checkpoint.manifest.created_at,
        output_root=output_root,
        path=output_root / "data/intake/checkpoints" / f"{checkpoint.manifest.batch_id}.json",
        collected_at=checkpoint.manifest.created_at,
    )


def prepare_process(
    project_root: Path,
    app: AppConfig,
    *,
    fixtures: bool = False,
    dry_run: bool = False,
    intake: IntakeRun | None = None,
    batch_id: str | None = None,
    now: datetime | None = None,
) -> PreparedProcess:
    output_root = isolated_output_root(project_root, fixtures=fixtures, dry_run=dry_run)
    if intake is None:
        checkpoint = (
            load_checkpoint_by_batch_id(output_root, batch_id)
            if batch_id
            else latest_complete_checkpoint(output_root)
        )
        if checkpoint is None:
            raise FileNotFoundError("No complete intake checkpoint is available to process")
        intake = _intake_run_from_checkpoint(output_root, checkpoint)
    elif batch_id and intake.checkpoint.manifest.batch_id != batch_id:
        checkpoint = load_checkpoint_by_batch_id(output_root, batch_id)
        intake = _intake_run_from_checkpoint(output_root, checkpoint)
    if now is not None:
        process_now = now
    elif fixtures:
        process_now = intake.collected_at or intake.now
    else:
        process_now = utc_now()
    intake.now = process_now
    ledger = ProcessingLedgerStore(ledger_path(output_root))
    ledger.upsert_checkpoint(intake.checkpoint, now=process_now)
    ledger.mark_interrupted(
        current_run_id=intake.checkpoint.manifest.run_id,
        now=process_now,
    )
    people = load_model_list(project_root / "config/people.yaml", "people", PersonConfig)
    current_keys = {
        intake_key(record.input_id, record.content_version) for record in intake.checkpoint.items
    }
    fresh = []
    same_batch_old = []
    collection_hours = app.news_window_hours + app.collection_buffer_hours
    save_now = intake.checkpoint.manifest.created_at
    lookback_cutoff = hours_ago(process_now, hours=app.intake_recovery_lookback_days * 24)
    for record in intake.checkpoint.items:
        if not _actionable_record(ledger, record, recompute_completed=fixtures):
            continue
        entry = ledger.get(record.input_id, record.content_version)
        first_saved = (
            (entry.durable_at if entry is not None else None) or record.durable_at or save_now
        )
        eligible_at_save = bool(
            filter_news_window(
                [record.item],
                now=first_saved,
                hours=collection_hours,
            )
        )
        if not eligible_at_save:
            ledger.update(
                record.input_id,
                record.content_version,
                now=process_now,
                processing=ProcessingStatus.EXCLUDED,
                reason_code=ReasonCode.EXCLUDED_STALE,
                stage="window",
                outcome="excluded_stale",
            )
            continue
        durable = record.durable_at or record.first_seen_at
        if durable < lookback_cutoff:
            continue
        in_window = bool(
            filter_news_window(
                [record.item],
                now=process_now,
                hours=app.news_window_hours,
            )
        )
        if in_window:
            fresh.append(record)
        else:
            same_batch_old.append(record)
    recovered, aged = recover_unfinished_records(
        output_root,
        ledger=ledger,
        now=process_now,
        lookback_days=app.intake_recovery_lookback_days,
        maximum_items=None,
        exclude_keys=current_keys,
        collection_hours=collection_hours,
    )
    recovered = [
        record
        for record in recovered
        if _actionable_record(ledger, record, recompute_completed=fixtures)
    ]
    for entry in aged:
        if entry.processing not in {
            ProcessingStatus.EXCLUDED,
            ProcessingStatus.WAITING_EVIDENCE,
            ProcessingStatus.COMPLETED,
        }:
            ledger.update(
                entry.input_id,
                entry.content_version,
                now=process_now,
                stage="recovery",
                outcome="aged_unresolved",
            )
    fresh, recovery = split_latest_versions(fresh, [*same_batch_old, *recovered])
    recovery = recovery[: app.intake_maximum_recovery_items]
    call_safe_limit = min(
        app.maximum_ai_items,
        app.maximum_raw_items,
        max(0, app.maximum_ai_calls - 7) * 2 // 5,
    )
    watchlist_path = project_root / "config/lab_watchlist.yaml"
    watchlist = load_model(watchlist_path, LabWatchlistConfig) if watchlist_path.exists() else None
    protected_slots = (
        watchlist.reserved_fresh_candidate_slots if watchlist and watchlist.enabled else 0
    )
    selection = select_process_candidates(
        fresh=fresh,
        recovery=recovery,
        maximum_items=call_safe_limit,
        reserved_recovery_slots=app.intake_reserved_candidate_slots,
        reserved_fresh_slots=protected_slots,
        labs=watchlist.labs if watchlist else None,
        update_rules=watchlist.update_rules if watchlist else None,
    )
    for record in selection.deferred:
        if not _actionable_record(ledger, record, recompute_completed=fixtures):
            continue
        ledger.update(
            record.input_id,
            record.content_version,
            now=process_now,
            processing=ProcessingStatus.DEFERRED_BUDGET,
            reason_code=ReasonCode.DEFERRED_BUDGET,
            stage="candidate_select",
            outcome="deferred_budget",
            run_id=intake.checkpoint.manifest.run_id,
            candidate_diagnostics={
                "selected": False,
                "reason": "deferred_budget",
                "cap": call_safe_limit,
                "candidate_policy_hash": selection.candidate_policy_hash,
                **selection.candidate_matches.get(
                    intake_key(record.input_id, record.content_version), {}
                ),
            },
        )
    for record in selection.records:
        ledger.update(
            record.input_id,
            record.content_version,
            now=process_now,
            processing=ProcessingStatus.IN_PROGRESS,
            stage="process",
            outcome="in_progress",
            run_id=intake.checkpoint.manifest.run_id,
            candidate_diagnostics={
                "selected": True,
                "cap": call_safe_limit,
                "candidate_policy_hash": selection.candidate_policy_hash,
                **selection.candidate_matches.get(
                    intake_key(record.input_id, record.content_version), {}
                ),
                "protected_fresh": intake_key(record.input_id, record.content_version)
                in selection.protected_fresh_keys,
            },
        )
    ledger.save()
    return PreparedProcess(
        intake=intake,
        selection=selection,
        ledger=ledger,
        people=people,
        process_now=process_now,
        batch_id=intake.checkpoint.manifest.batch_id,
    )


def _production_collect(
    project_root: Path,
    output_root: Path,
    app: AppConfig,
    now: datetime,
) -> tuple[CollectionResult, list[object], list[str]]:
    sources = load_model_list(project_root / "config/sources.yaml", "sources", SourceConfig)
    watchlist_path = project_root / "config/lab_watchlist.yaml"
    watchlist = load_model(watchlist_path, LabWatchlistConfig) if watchlist_path.exists() else None
    people = load_model_list(project_root / "config/people.yaml", "people", PersonConfig)
    sources.extend(active_practitioner_sources(people))
    topics = load_model_list(project_root / "config/topics.yaml", "topics", TopicConfig)
    repositories = load_model_list(
        project_root / "config/repositories.yaml",
        "repositories",
        RepositoryConfig,
    )
    companies = load_model_list(
        project_root / "config/companies.yaml",
        "companies",
        CompanyConfig,
    )
    http = HttpClient(
        timeout_seconds=app.request_timeout_seconds,
        attempts=app.request_retry_attempts,
    )
    keywords = list(dict.fromkeys(word for topic in topics for word in topic.keywords))
    rss_inconsistent = inconsistent_cache_sources(
        output_root,
        state_name="rss",
        state_path=output_root / "data/state/rss.json",
    )
    aihot_inconsistent = inconsistent_cache_sources(
        output_root,
        state_name="aihot_discovery",
        state_path=output_root / "data/state/aihot.json",
    )
    collectors: list[object] = [
        RSSCollector(
            [source for source in sources if source.type in {"rss", "atom"}],
            http=http,
            state_path=output_root / "data/state/rss.json",
            now=now,
            unconditional_source_ids=set(rss_inconsistent),
        ),
        GitHubCollector(
            repositories,
            http=http,
            snapshot_dir=output_root / "data/snapshots/github",
            history_snapshot_dir=project_root / "data/snapshots/github",
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
            app.aihot,
            http=http,
            state_path=output_root / "data/state/aihot.json",
            now=now,
        ),
    ]
    if watchlist is not None and watchlist.enabled:
        discovery_budget = RequestStartBudget(
            maximum_requests=watchlist.maximum_network_requests,
            deadline_seconds=watchlist.request_start_deadline_seconds,
        )
        discovery_http = HttpClient(
            timeout_seconds=watchlist.request_timeout_seconds,
            attempts=watchlist.request_attempts,
            before_attempt=discovery_budget.before_attempt,
        )
        special_sources = [
            source for source in sources if source.type == "official_changelog" and source.enabled
        ]
        collectors.extend(
            DeepSeekUpdatesCollector(
                http=discovery_http,
                source=source,
                now=now,
                maximum_response_bytes=watchlist.maximum_response_bytes,
                maximum_excerpt_characters=watchlist.maximum_excerpt_characters,
            )
            for source in special_sources
        )
        collectors.append(
            HNSearchCollector(
                http=discovery_http,
                watchlist=watchlist,
                now=now,
                window_hours=app.news_window_hours + app.collection_buffer_hours,
            )
        )
    if aihot_inconsistent:
        collectors[-1].unconditional_refresh = True
    collection = collect_available(
        collectors,  # type: ignore[arg-type]
        filter_items=None,
        maximum_items=None,
    )
    return collection, collectors, rss_inconsistent + aihot_inconsistent

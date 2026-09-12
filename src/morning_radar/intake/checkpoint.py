"""Atomic intake checkpoints and cache-consistency helpers."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from morning_radar.collectors.hn_common import merge_hn_observations
from morning_radar.collectors.orchestrator import CollectionResult
from morning_radar.intake.identity import (
    content_version,
    input_id_for,
    provenance_from_item,
)
from morning_radar.intake.models import (
    INTAKE_POLICY_VERSION,
    INTAKE_SCHEMA_VERSION,
    CheckpointManifest,
    IntakeCheckpoint,
    IntakeRecord,
)
from morning_radar.models import RawItem
from morning_radar.storage import load_model, save_model
from morning_radar.time_utils import hours_ago

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class IntakeRun:
    checkpoint: IntakeCheckpoint
    collection: CollectionResult
    now: datetime
    output_root: Path
    path: Path
    collected_at: datetime | None = None


def checkpoint_dir(root: Path) -> Path:
    return root / "data" / "intake" / "checkpoints"


def checkpoint_path(root: Path, batch_id: str) -> Path:
    return checkpoint_dir(root) / f"{batch_id}.json"


def new_run_ids(now: datetime) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:10]
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    run_id = f"run-{stamp}-{suffix}"
    return run_id, f"batch-{stamp}-{suffix}"


def build_intake_records(
    items: list[RawItem],
    *,
    batch_id: str,
    run_id: str,
    first_seen: dict[str, datetime] | None = None,
) -> list[IntakeRecord]:
    items = merge_hn_observations(items)
    seen_versions: dict[tuple[str, str], IntakeRecord] = {}
    for item in items:
        input_id = input_id_for(item)
        version = content_version(item)
        key = (input_id, version)
        provenance = provenance_from_item(item)
        existing = seen_versions.get(key)
        if existing is None:
            seen_versions[key] = IntakeRecord(
                input_id=input_id,
                content_version=version,
                source_id=provenance.source_id,
                url=item.url,
                title=item.title,
                published_at=item.published_at,
                first_seen_at=(first_seen or {}).get(input_id, item.fetched_at),
                fetched_at=item.fetched_at,
                item=item,
                provenance=[provenance],
                batch_id=batch_id,
                run_id=run_id,
            )
            continue
        urls = {entry.url for entry in existing.provenance}
        if provenance.url not in urls:
            existing.provenance.append(provenance)
    return list(seen_versions.values())


def write_intake_checkpoint(
    root: Path,
    *,
    items: list[RawItem],
    now: datetime,
    cutoff_at: datetime,
    collection: CollectionResult,
    source_state: dict[str, Any],
    cache_inconsistencies: list[str] | None = None,
    run_id: str | None = None,
    batch_id: str | None = None,
    first_seen: dict[str, datetime] | None = None,
    discovery_audit: list[dict[str, Any]] | None = None,
) -> IntakeCheckpoint:
    assigned_run_id, assigned_batch_id = new_run_ids(now)
    run_id = run_id or assigned_run_id
    batch_id = batch_id or assigned_batch_id
    records = build_intake_records(
        items,
        batch_id=batch_id,
        run_id=run_id,
        first_seen=first_seen,
    )
    truncated = bool(collection.after_dedup and len(collection.items) < collection.after_dedup)
    stats = {
        name: {
            "collected": stat.collected,
            "within_buffer": stat.within_buffer,
            "retained": stat.retained,
        }
        for name, stat in collection.collector_stats.items()
    }
    checkpoint = IntakeCheckpoint(
        manifest=CheckpointManifest(
            schema_version=INTAKE_SCHEMA_VERSION,
            complete=True,
            batch_id=batch_id,
            run_id=run_id,
            created_at=now,
            cutoff_at=cutoff_at,
            policy_version=INTAKE_POLICY_VERSION,
            item_count=len(records),
            collector_stats=stats,
            failures=dict(collection.failures),
            truncated=truncated,
            cache_inconsistencies=list(cache_inconsistencies or []),
            source_state_committed=False,
            discovery_audit=list(discovery_audit or []),
        ),
        items=records,
        source_state=source_state,
    )
    path = checkpoint_path(root, batch_id)
    save_model(path, checkpoint)
    loaded = load_complete_checkpoint(path)
    if loaded is None:
        raise RuntimeError(f"Intake checkpoint was not complete after write: {path}")
    LOGGER.info(
        "Intake checkpoint saved: batch=%s items=%d complete=%s truncated=%s",
        batch_id,
        len(records),
        True,
        truncated,
    )
    return loaded


def load_complete_checkpoint(path: Path) -> IntakeCheckpoint | None:
    if not path.exists():
        return None
    try:
        checkpoint = load_model(path, IntakeCheckpoint)
    except (OSError, ValueError):
        LOGGER.exception("Ignoring unreadable intake checkpoint %s", path)
        return None
    if not checkpoint.manifest.complete:
        LOGGER.warning("Refusing incomplete intake checkpoint %s", path)
        return None
    if checkpoint.manifest.item_count != len(checkpoint.items):
        LOGGER.warning(
            "Refusing inconsistent intake checkpoint %s: item_count=%s actual=%s",
            path,
            checkpoint.manifest.item_count,
            len(checkpoint.items),
        )
        return None
    return checkpoint


def load_checkpoint_by_batch_id(root: Path, batch_id: str) -> IntakeCheckpoint:
    path = checkpoint_path(root, batch_id)
    checkpoint = load_complete_checkpoint(path)
    if checkpoint is None:
        raise FileNotFoundError(f"Complete intake checkpoint not found for batch_id={batch_id}")
    return checkpoint


def _checkpoint_sort_key(checkpoint: IntakeCheckpoint) -> tuple[datetime, str]:
    return (checkpoint.manifest.created_at, checkpoint.manifest.batch_id)


def load_recent_complete_checkpoints(
    root: Path,
    *,
    now: datetime,
    lookback_days: int,
) -> list[IntakeCheckpoint]:
    directory = checkpoint_dir(root)
    if not directory.exists():
        return []
    cutoff = hours_ago(now, hours=lookback_days * 24)
    loaded: list[IntakeCheckpoint] = []
    for path in directory.glob("*.json"):
        checkpoint = load_complete_checkpoint(path)
        if checkpoint is None:
            continue
        if checkpoint.manifest.created_at < cutoff:
            continue
        loaded.append(checkpoint)
    loaded.sort(key=_checkpoint_sort_key)
    return loaded


def latest_complete_checkpoint(root: Path) -> IntakeCheckpoint | None:
    directory = checkpoint_dir(root)
    if not directory.exists():
        return None
    loaded = [
        checkpoint
        for path in directory.glob("*.json")
        if (checkpoint := load_complete_checkpoint(path)) is not None
    ]
    if not loaded:
        return None
    return max(loaded, key=_checkpoint_sort_key)


def mark_source_state_committed(root: Path, checkpoint: IntakeCheckpoint) -> IntakeCheckpoint:
    updated = checkpoint.model_copy(
        update={"manifest": checkpoint.manifest.model_copy(update={"source_state_committed": True})}
    )
    save_model(checkpoint_path(root, checkpoint.manifest.batch_id), updated)
    return updated


def pending_source_state(collectors: list[object]) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for collector in collectors:
        pending = getattr(collector, "pending_source_state", None)
        name = getattr(collector, "name", collector.__class__.__name__)
        if pending is not None:
            state[name] = pending
    return state


def commit_collector_state(collectors: list[object]) -> None:
    for collector in collectors:
        commit = getattr(collector, "commit_source_state", None)
        if callable(commit):
            commit()


def inconsistent_cache_sources(
    root: Path,
    *,
    state_name: str,
    state_path: Path,
) -> list[str]:
    """Return source IDs whose HTTP cache markers have no matching inputs."""
    if not state_path.exists():
        return []
    from morning_radar.storage import read_json

    try:
        current = read_json(state_path)
    except (OSError, ValueError):
        LOGGER.exception("Source cache state unreadable: %s", state_path)
        return []
    if not isinstance(current, dict):
        return []
    checkpoints: list[IntakeCheckpoint] = []
    directory = checkpoint_dir(root)
    if directory.exists():
        for path in directory.glob("*.json"):
            loaded = load_complete_checkpoint(path)
            if loaded is not None:
                checkpoints.append(loaded)
    if not checkpoints:
        return [str(source_id) for source_id in current]

    def _source_entry(checkpoint: IntakeCheckpoint, source_id: str) -> dict[str, Any]:
        saved = checkpoint.source_state.get(state_name, {})
        if not isinstance(saved, dict):
            return {}
        entry = saved.get(source_id, {})
        return entry if isinstance(entry, dict) else {}

    inconsistent: list[str] = []
    for source_id, cached in current.items():
        if not isinstance(cached, dict):
            continue
        marker = cached.get("etag") or cached.get("last_modified")
        if not marker:
            continue
        matching = []
        for checkpoint in checkpoints:
            entry = _source_entry(checkpoint, str(source_id))
            saved_marker = entry.get("etag") or entry.get("last_modified")
            if saved_marker == marker:
                matching.append(checkpoint)
        if not matching:
            inconsistent.append(str(source_id))
            continue
        current_source = str(source_id)

        def _payload_has(
            checkpoint: IntakeCheckpoint,
            item_id: str,
            expected_source: str = current_source,
        ) -> bool:
            for record in checkpoint.items:
                if record.item.id != item_id and record.input_id != item_id:
                    continue
                if record.source_id == expected_source or any(
                    entry.source_id == expected_source for entry in record.provenance
                ):
                    return True
            return False

        statuses = {
            str(_source_entry(checkpoint, str(source_id)).get("status") or "")
            for checkpoint in matching
        }
        declared_ids = [
            item_id
            for checkpoint in matching
            for item_id in _source_entry(checkpoint, str(source_id)).get("item_ids") or []
        ]
        missing_declared = [
            item_id
            for item_id in declared_ids
            if not any(_payload_has(checkpoint, str(item_id)) for checkpoint in matching)
        ]
        has_items = any(
            record.source_id == source_id
            or any(entry.source_id == source_id for entry in record.provenance)
            for checkpoint in matching
            for record in checkpoint.items
        )
        if "failed" in statuses or missing_declared:
            inconsistent.append(str(source_id))
            continue
        if "empty" in statuses and not declared_ids:
            continue
        if has_items:
            continue
        if "not_modified" in statuses:
            # 304 is consistent only when an earlier complete checkpoint already
            # stored this marker with items or an explicit empty success.
            prior_ok = False
            for checkpoint in checkpoints:
                entry = _source_entry(checkpoint, str(source_id))
                saved_marker = entry.get("etag") or entry.get("last_modified")
                if saved_marker != marker:
                    continue
                if entry.get("status") == "empty":
                    prior_ok = True
                    break
                declared = entry.get("item_ids") or []
                if declared and all(_payload_has(checkpoint, str(item_id)) for item_id in declared):
                    prior_ok = True
                    break
                if any(
                    record.source_id == source_id
                    or any(item.source_id == source_id for item in record.provenance)
                    for record in checkpoint.items
                ):
                    prior_ok = True
                    break
            if prior_ok:
                continue
        inconsistent.append(str(source_id))
    return inconsistent

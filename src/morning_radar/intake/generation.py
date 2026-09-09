"""Committed generation snapshots for multi-file brief outputs."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from morning_radar.storage import read_json, write_json

LOGGER = logging.getLogger(__name__)
PREPARED_NAME = "prepared_generation.json"
GENERATION_NAME = "generation.json"
CONTROL_MISSING = "missing"
CONTROL_VALID = "valid"
CONTROL_CORRUPT = "corrupt"


class GenerationControlError(RuntimeError):
    """A generation control file exists but cannot be trusted."""


def artifact_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def compute_generation_id(brief_date: str, outputs: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "brief_date": brief_date,
            "brief": outputs.get("brief"),
            "stories": outputs.get("stories"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def prepared_path(root: Path) -> Path:
    return root / "data/state" / PREPARED_NAME


def generation_path(root: Path) -> Path:
    return root / "data/state" / GENERATION_NAME


def _read_control(path: Path) -> tuple[str, dict[str, Any] | None]:
    if not path.exists():
        return CONTROL_MISSING, None
    try:
        payload = read_json(path)
    except (OSError, ValueError):
        LOGGER.exception("Generation control unreadable: %s", path)
        return CONTROL_CORRUPT, None
    if not isinstance(payload, dict):
        return CONTROL_CORRUPT, None
    return CONTROL_VALID, payload


def save_prepared_generation(root: Path, payload: dict[str, Any]) -> None:
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    brief_date = str(payload.get("brief_date") or "")
    payload = {
        **payload,
        "generation_id": payload.get("generation_id")
        or compute_generation_id(brief_date, outputs),
    }
    write_json(prepared_path(root), payload)


def load_prepared_generation(root: Path) -> dict[str, Any] | None:
    state, payload = _read_control(prepared_path(root))
    if state is CONTROL_CORRUPT:
        raise GenerationControlError("prepared_generation.json is corrupt")
    return payload


def save_generation_commit(
    root: Path,
    *,
    brief_date: str,
    brief_hash: str,
    stories_digest: str,
    generation_id: str = "",
) -> None:
    write_json(
        generation_path(root),
        {
            "complete": True,
            "brief_date": brief_date,
            "brief_hash": brief_hash,
            "stories_digest": stories_digest,
            "generation_id": generation_id,
        },
    )


def load_generation_commit(root: Path) -> dict[str, Any] | None:
    state, payload = _read_control(generation_path(root))
    if state is CONTROL_CORRUPT:
        raise GenerationControlError("generation.json is corrupt")
    return payload


def generation_is_complete(
    root: Path,
    brief_date: str,
    *,
    expected_hash: str | None = None,
    generation_id: str | None = None,
) -> bool:
    brief_file = root / "data/briefs" / f"{brief_date}.json"
    stories_file = root / "data/stories" / f"{brief_date}.json"
    commit = load_generation_commit(root)
    if commit is not None:
        if not commit.get("complete"):
            return False
        if commit.get("brief_date") != brief_date:
            return False
        if generation_id and commit.get("generation_id") != generation_id:
            return False
        if not brief_file.exists() or not stories_file.exists():
            return False
        actual = artifact_digest(brief_file)
        if commit.get("brief_hash") != actual:
            return False
        if expected_hash is not None and actual != expected_hash:
            return False
        recorded_stories = commit.get("stories_digest")
        return bool(recorded_stories) and artifact_digest(stories_file) == recorded_stories
    prepared = load_prepared_generation(root)
    if prepared is not None and prepared.get("brief_date") == brief_date:
        return False
    if expected_hash is not None and brief_file.exists():
        return artifact_digest(brief_file) == expected_hash
    return brief_file.exists() and stories_file.exists()


def prepared_is_committed(root: Path, prepared: dict[str, Any]) -> bool:
    brief_date = str(prepared.get("brief_date") or "")
    generation_id = str(prepared.get("generation_id") or "")
    if not brief_date or not generation_id:
        return False
    return generation_is_complete(
        root,
        brief_date,
        generation_id=generation_id,
    )


def commit_prepared_generation(root: Path, payload: dict[str, Any] | None = None) -> bool:
    """Write public artifacts from the prepared snapshot. Returns True if committed."""
    prepared = payload or load_prepared_generation(root)
    if not prepared:
        return False
    brief_date = str(prepared.get("brief_date") or "")
    outputs = prepared.get("outputs") or {}
    if not brief_date or not isinstance(outputs, dict) or "brief" not in outputs:
        return False
    generation_id = str(
        prepared.get("generation_id") or compute_generation_id(brief_date, outputs)
    )
    from morning_radar.models import (
        DailyBrief,
        DailyContinuity,
        DailyTendencies,
        RadarSignal,
        RawItem,
        Signal,
        Story,
    )
    from morning_radar.storage import save_model, save_models

    name = f"{brief_date}.json"
    raw_items = [RawItem.model_validate(item) for item in outputs.get("raw") or []]
    save_models(root / "data/raw" / name, raw_items)
    save_models(
        root / "data/stories" / name,
        [Story.model_validate(item) for item in outputs.get("stories") or []],
    )
    save_models(
        root / "data/signals" / name,
        [Signal.model_validate(item) for item in outputs.get("signals") or []],
    )
    save_model(root / "data/briefs" / name, DailyBrief.model_validate(outputs["brief"]))
    if outputs.get("continuity") is not None:
        save_model(
            root / "data/continuity" / name,
            DailyContinuity.model_validate(outputs["continuity"]),
        )
    save_models(
        root / "data/radar_signals" / name,
        [RadarSignal.model_validate(item) for item in outputs.get("radar_signals") or []],
    )
    if outputs.get("tendencies") is not None:
        save_model(
            root / "data/tendencies" / name,
            DailyTendencies.model_validate(outputs["tendencies"]),
        )
    editorial = outputs.get("editorial")
    if editorial is not None:
        try:
            from morning_radar.editorial.models import DailyEditorialDecisions

            editorial_model = DailyEditorialDecisions.model_validate(editorial)
            save_model(root / "data/editorial" / name, editorial_model)
        except (OSError, TypeError, ValueError):
            LOGGER.exception("Editorial degradation: prepared editorial could not be saved")
    brief_file = root / "data/briefs" / name
    stories_file = root / "data/stories" / name
    digest = artifact_digest(brief_file)
    save_generation_commit(
        root,
        brief_date=brief_date,
        brief_hash=digest,
        stories_digest=artifact_digest(stories_file),
        generation_id=generation_id,
    )
    apply_generation_effects(root, prepared, brief_hash=digest)
    return True


def heal_incomplete_generation(root: Path) -> bool:
    prepared = load_prepared_generation(root)
    if prepared is None:
        return False
    brief_date = str(prepared.get("brief_date") or "")
    if not brief_date:
        return False
    if prepared_is_committed(root, prepared):
        digest = artifact_digest(root / "data/briefs" / f"{brief_date}.json")
        apply_generation_effects(root, prepared, brief_hash=digest)
        return False
    return commit_prepared_generation(root, prepared)


def apply_generation_effects(
    root: Path,
    prepared: dict[str, Any],
    *,
    brief_hash: str,
) -> None:
    _complement_ledger(root, prepared, brief_hash=brief_hash)
    _ensure_publish_record(root, prepared, brief_hash=brief_hash)


def _complement_ledger(root: Path, prepared: dict[str, Any], *, brief_hash: str) -> None:
    ledger_payload = prepared.get("ledger")
    if not isinstance(ledger_payload, dict):
        return
    path = root / "data/intake/ledger.json"
    try:
        current = read_json(path) if path.exists() else {"entries": {}}
    except (OSError, ValueError):
        current = {"entries": {}}
    if not isinstance(current, dict):
        current = {"entries": {}}
    current_entries = current.get("entries") or {}
    prepared_entries = ledger_payload.get("entries") or {}
    result_keys = set(prepared.get("result_keys") or prepared.get("selection_keys") or [])
    brief_date = str(prepared.get("brief_date") or "")
    displayed = _displayed_story_ids_from_prepared(prepared)
    if not isinstance(prepared_entries, dict) or not isinstance(current_entries, dict):
        return
    for key, entry in prepared_entries.items():
        if result_keys and key not in result_keys:
            continue
        if not isinstance(entry, dict):
            continue
        stamped = dict(entry)
        if (
            stamped.get("processing") == "completed"
            and stamped.get("story_id") in displayed
            and brief_hash
        ):
            stamped["brief_hash"] = stamped.get("brief_hash") or brief_hash
            stamped["brief_date"] = stamped.get("brief_date") or brief_date
            if stamped.get("publish") in {None, "not_generated"}:
                stamped["publish"] = "generated"
        existing = current_entries.get(key)
        existing_status = existing.get("processing") if isinstance(existing, dict) else None
        existing_publish = existing.get("publish") if isinstance(existing, dict) else None
        if existing_publish == "deploy_confirmed":
            continue
        if existing is None or existing_status in {"in_progress", "unprocessed"}:
            current_entries[key] = stamped
            continue
        if existing_status == "completed" and isinstance(existing, dict):
            if not existing.get("brief_hash") and stamped.get("brief_hash"):
                existing["brief_hash"] = stamped["brief_hash"]
                existing["brief_date"] = existing.get("brief_date") or stamped.get("brief_date")
                if existing.get("publish") in {None, "not_generated"}:
                    existing["publish"] = stamped.get("publish") or "generated"
            current_entries[key] = existing
    current["entries"] = current_entries
    if "schema_version" in ledger_payload:
        current["schema_version"] = ledger_payload["schema_version"]
    write_json(path, current)


def _displayed_story_ids_from_prepared(prepared: dict[str, Any]) -> set[str]:
    outputs = prepared.get("outputs") if isinstance(prepared.get("outputs"), dict) else {}
    brief = outputs.get("brief") if isinstance(outputs, dict) else {}
    if not isinstance(brief, dict):
        return set()
    ids: set[str] = set()
    for name in (
        "top_stories",
        "market_and_companies",
        "ai_and_open_source",
        "trend_radar",
        "developer_discussions",
        "other_reading",
    ):
        for item in brief.get(name) or []:
            if isinstance(item, dict):
                ids.update(str(value) for value in (item.get("story_ids") or []) if value)
    return ids


def _ensure_publish_record(root: Path, prepared: dict[str, Any], *, brief_hash: str) -> None:
    from datetime import UTC, datetime

    from morning_radar.intake.publish import PublishStore

    brief_date = str(prepared.get("brief_date") or "")
    if not brief_date:
        return
    store = PublishStore(root / "data/state/publish.json")
    existing = store.get(brief_date)
    if existing is not None and existing.brief_hash == brief_hash:
        return
    generated_at = datetime.now(tz=UTC)
    outputs = prepared.get("outputs") if isinstance(prepared.get("outputs"), dict) else {}
    brief_payload = outputs.get("brief") if isinstance(outputs, dict) else None
    if isinstance(brief_payload, dict) and brief_payload.get("generated_at"):
        raw_time = str(brief_payload["generated_at"]).replace("Z", "+00:00")
        try:
            generated_at = datetime.fromisoformat(raw_time)
        except ValueError:
            generated_at = generated_at
    store.mark_generated(
        brief_date=brief_date,
        brief_hash=brief_hash,
        generated_at=generated_at,
        artifact_path=f"data/briefs/{brief_date}.json",
    )


def require_complete_generation(
    root: Path,
    brief_date: str,
    *,
    expected_hash: str | None = None,
) -> None:
    heal_incomplete_generation(root)
    if not generation_is_complete(root, brief_date, expected_hash=expected_hash):
        raise RuntimeError(f"Incomplete or untrusted generation for {brief_date}")

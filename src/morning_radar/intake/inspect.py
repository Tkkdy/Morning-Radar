"""Maintainer lookup for a saved input's processing fate."""

from __future__ import annotations

from pathlib import Path

from morning_radar.intake.checkpoint import load_checkpoint_by_batch_id
from morning_radar.intake.identity import intake_key
from morning_radar.intake.ledger import ProcessingLedgerStore
from morning_radar.processing.normalize import normalize_url, stable_item_id


def inspect_collection(root: Path, *, batch_id: str) -> dict[str, object]:
    checkpoint = load_checkpoint_by_batch_id(root, batch_id)
    return {
        "batch_id": batch_id,
        "complete": checkpoint.manifest.complete,
        "discovery_audit": checkpoint.manifest.discovery_audit,
    }


def inspect_intake(
    root: Path,
    *,
    input_id: str | None = None,
    url: str | None = None,
    story_id: str | None = None,
) -> dict[str, object]:
    store = ProcessingLedgerStore(root / "data/intake/ledger.json")
    matches = []
    if input_id:
        matches.extend(store.find_by_input_id(input_id))
    if url:
        normalized = normalize_url(url)
        matches.extend(store.find_by_url(url))
        matches.extend(store.find_by_url(normalized))
        matches.extend(store.find_by_input_id(stable_item_id(url)))
    if story_id:
        matches.extend(store.find_by_story_id(story_id))
    unique = {intake_key(entry.input_id, entry.content_version): entry for entry in matches}
    records = [entry.model_dump(mode="json") for entry in unique.values()]
    if not records:
        return {
            "found": False,
            "status": "not_found",
            "message": "未找到采集证据／原因未确定",
            "coverage_gap": False,
            "records": [],
        }
    statuses = {str(record.get("reason_code") or record.get("processing")) for record in records}
    if "superseded" in statuses:
        message = "found superseded content version(s); not model-processed as complete"
    elif any(str(record.get("processing")) == "failed_retry" for record in records):
        message = "found failed version awaiting retry"
    else:
        message = f"found {len(records)} intake record(s)"
    return {
        "found": True,
        "status": "found",
        "message": message,
        "coverage_gap": False,
        "records": records,
    }


def format_inspect_summary(payload: dict[str, object]) -> str:
    if not payload.get("found"):
        return str(payload.get("message") or "未找到采集证据／原因未确定")
    lines = []
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        details = record.get("decision_details") or {}
        classification = details.get("classification") or {}
        score = details.get("score") or {}
        class_reason = (
            classification.get("relevance_reason")
            or classification.get("status")
            or "legacy_unavailable"
        )
        model_expl = score.get("model_explanation") or score.get("status") or "legacy_unavailable"
        rule_reason = score.get("rule_reason") or record.get("score_rationale") or "-"
        lines.append(
            f"{record.get('input_id')} {record.get('content_version')} "
            f"processing={record.get('processing')} reason={record.get('reason_code')} "
            f"story={record.get('story_id') or record.get('merged_into') or '-'} "
            f"score={record.get('relevance_score')}/{record.get('relevance_threshold')} "
            f"class_reason={class_reason} model_explanation={model_expl} rule={rule_reason}"
        )
    return "\n".join(lines) if lines else "found records"

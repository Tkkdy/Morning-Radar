"""Versioned module health, separate from the existing public readiness contract."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from morning_radar.ai.errors import AIConfigurationError
from morning_radar.ai.usage import atomic_json, usage_summary


def publish_health(root: Path, day: str) -> dict:
    modules = {}
    for path in sorted((root / "data/health" / day).glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        name = record["module"]
        if name not in modules or record["started_at"] > modules[name]["started_at"]:
            modules[name] = record
    required = {"daily", "tendency"}
    successful = {"completed", "no_changes", "no_candidates"}
    complete = required <= modules.keys() and all(
        modules[name]["status"] in successful for name in required
    )
    report = {
        "schema_version": 1,
        "business_date": day,
        "delivery_status": "complete" if complete else "partial_or_unavailable",
        "modules": _public_modules(modules),
        "cost": usage_summary(root, day),
        "not_run": sorted(required - modules.keys()),
    }
    atomic_json(root / "site/health-v1.json", report)
    return report


def _public_modules(modules: dict) -> dict:
    """Do not publish collector error text/URLs from current or historical audits."""
    result = json.loads(json.dumps(modules))
    allowed = {
        "source_id", "status", "freshness", "requests", "persisted", "truncated",
        "response_bytes", "http_status", "latest_source_date", "latest_published_at",
    }
    for record in result.values():
        details = record.get("details", {})
        if "sources" in details:
            details["sources"] = [
                {key: value for key, value in source.items() if key in allowed}
                for source in details["sources"]
            ]
    return result


@contextmanager
def module_run(root: Path, day: str, module: str):
    record = {
        "schema_version": 1,
        "run_id": uuid4().hex,
        "business_date": day,
        "module": module,
        "started_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "details": {},
    }
    path = root / "data/health" / day / (module + "-" + record["run_id"] + ".json")
    atomic_json(path, record)
    try:
        yield record
    except BaseException as exc:
        record["status"] = (
            "cancelled"
            if isinstance(exc, (KeyboardInterrupt, SystemExit))
            else "not_configured"
            if isinstance(exc, AIConfigurationError)
            else "failed"
        )
        record["details"] = {"error_type": type(exc).__name__}
        raise
    finally:
        if record["status"] == "running":
            record["status"] = "completed"
        record["finished_at"] = datetime.now(UTC).isoformat()
        atomic_json(path, record)
        publish_health(root, day)


def daily_details(prepared, brief) -> tuple[str, dict]:
    counts = {}
    pending = 0
    selected = {
        (r.input_id, r.content_version)
        for r in [*prepared.selection.records, *prepared.selection.deferred]
    }
    for entry in prepared.ledger.ledger.entries.values():
        if (
            entry.batch_id == prepared.batch_id
            or (entry.input_id, entry.content_version) in selected
        ):
            key = entry.reason_code.value if entry.reason_code else entry.processing.value
            counts[key] = counts.get(key, 0) + 1
            pending += entry.processing.value in {
                "unprocessed",
                "in_progress",
                "failed_retry",
                "deferred_budget",
                "waiting_evidence",
            }
    stats = brief.run_stats
    gaps = {
        "source_failures": prepared.intake.collection.failures,
        "candidate_outcomes": counts,
        "pending_candidates": pending,
        "sources": prepared.intake.checkpoint.manifest.discovery_audit,
        "brief_fallbacks": stats.get("ai_brief_item_fallbacks", 0),
        "continuity": (
            "budget_skipped"
            if stats.get("continuity_budget_skipped")
            else "failed"
            if stats.get("continuity_unavailable")
            else "completed"
        ),
    }
    incomplete = (
        any(
            source.get("status") in {"failed", "empty", "stale_or_undated"}
            or source.get("freshness") == "stale_or_unavailable"
            for source in gaps["sources"]
        )
        or bool(gaps["source_failures"])
        or bool(gaps["brief_fallbacks"])
        or gaps["continuity"] != "completed"
        or pending > 0
    )
    return ("partial" if incomplete else "completed"), gaps

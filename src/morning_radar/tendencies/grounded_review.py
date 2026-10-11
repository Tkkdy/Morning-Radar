"""Bounded semantic review; machine checks bind the judgment, not its truth."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from morning_radar.ai import AIBudgetExceeded
from morning_radar.ai.errors import AIError
from morning_radar.models.core import RadarModel
from morning_radar.tendencies.core_models import Revision


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class Finding(RadarModel):
    field_path: str
    text: str = Field(min_length=1, max_length=1500)
    category: Literal[
        "unsupported_fact",
        "attribution_scope_shift",
        "internal_contradiction",
        "event_only",
        "conditional_inference",
    ]
    evidence_id: str | None = None
    quote: str | None = Field(default=None, min_length=8, max_length=280)
    explanation: str = Field(min_length=1, max_length=1200)


class Assessment(RadarModel):
    candidate_id: str
    body_sha256: str
    evidence_sha256: str
    analysis_version: str
    expected_previous_revision_id: str | None
    verdict: Literal["approved", "rejected", "uncertain"]
    checked_fields: list[str]
    findings: list[Finding] = Field(default_factory=list, max_length=30)
    rationale: str = Field(min_length=1, max_length=1500)
    early_reason: str = Field(min_length=1, max_length=1200)


class ReviewOutput(RadarModel):
    # One malformed assessment must not discard valid siblings.
    assessments: list[dict[str, Any]] = Field(default_factory=list, max_length=12)


def narratives(decision: dict) -> dict[str, str]:
    result = {}

    def visit(value, path):
        if isinstance(value, str) and value:
            result[path] = value
        elif isinstance(value, dict):
            for key, child in value.items():
                if key not in {"evidence_ids", "condition_key", "key", "scope_basis"}:
                    visit(child, path + "." + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + "." + str(index))

    visit(decision["hypothesis"], "hypothesis")
    for key in ["reason", "relinquished", "changed_parts"]:
        visit(decision[key], key)
    return result


def review_version(prompt_dir: Path) -> str:
    return sha(
        {
            "prompt": (prompt_dir / "tendency_review.md").read_text(encoding="utf8"),
            "schema": Assessment.model_json_schema(),
            "version": 1,
        }
    )


def candidate(revision: Revision, previous: Revision | None, version: str) -> dict:
    body = revision.decision.model_dump(mode="json")
    evidence = [e.model_dump(mode="json") for e in revision.evidence]
    return {
        "candidate_id": revision.revision_id,
        "body_sha256": sha(body),
        "evidence_sha256": sha(evidence),
        "analysis_version": version,
        "expected_previous_revision_id": revision.previous_revision_id,
        "decision": body,
        "evidence": evidence,
        "narratives": list(narratives(body)),
        "previous": previous.decision.model_dump(mode="json") if previous else None,
        "new_evidence_ids": [
            e.evidence_id
            for e in revision.evidence
            if previous is None or e.evidence_id not in {x.evidence_id for x in previous.evidence}
        ],
    }


def validate_assessment(value: dict, request: dict) -> Assessment:
    result = Assessment.model_validate(value)
    for key in [
        "candidate_id",
        "body_sha256",
        "evidence_sha256",
        "analysis_version",
        "expected_previous_revision_id",
    ]:
        if getattr(result, key) != request[key]:
            raise ValueError("Review binding mismatch: " + key)
    if set(result.checked_fields) != set(request["narratives"]):
        raise ValueError("Review must cover all public narrative fields")
    fields = narratives(request["decision"])
    if set(request["narratives"]) != set(fields):
        raise ValueError("Candidate narrative coverage changed")
    evidence = {e["evidence_id"]: e for e in request["evidence"]}
    for finding in result.findings:
        if (
            finding.field_path not in request["narratives"]
            or finding.text not in fields[finding.field_path]
        ):
            raise ValueError("Review points to nonexistent candidate text")
        if bool(finding.evidence_id) != bool(finding.quote):
            raise ValueError("Evidence reference and quote must be paired")
        if finding.evidence_id:
            item = evidence.get(finding.evidence_id)
            if not item or not any(
                finding.quote in part for part in item["excerpt"].split("\n[…]\n")
            ):
                raise ValueError("Review invents an evidence quote")
    blockers = [f for f in result.findings if f.category != "conditional_inference"]
    if result.verdict == "approved" and (blockers or not result.findings):
        raise ValueError("Approval needs an explained conditional inference and no blockers")
    if result.verdict == "rejected" and not blockers:
        raise ValueError("Rejection requires located factual/structural problems")
    return result


def validate_admission(event: dict, revision: Revision) -> None:
    request = event["request"]
    if request["candidate_id"] != revision.revision_id:
        raise ValueError("Review belongs to another revision")
    if request["body_sha256"] != sha(revision.decision.model_dump(mode="json")):
        raise ValueError("Candidate changed after review")
    if request["evidence_sha256"] != sha([e.model_dump(mode="json") for e in revision.evidence]):
        raise ValueError("Evidence changed after review")
    if request["expected_previous_revision_id"] != revision.previous_revision_id:
        raise ValueError("Review expected revision changed")
    if request["narratives"] != list(narratives(revision.decision.model_dump(mode="json"))):
        raise ValueError("Review omitted candidate fields")
    if validate_assessment(event["assessment"], request).verdict != "approved":
        raise ValueError("Only approved content may be publicly admitted")


def pending_candidates(store) -> list[dict]:
    pending = {}
    for event in store.events():
        payload = event["payload"]
        if payload.get("type") == "review_candidate":
            pending[payload["request"]["candidate_id"]] = payload
        if payload.get("type") == "content_review" and payload["status"] not in {
            "unavailable",
            "budget_skipped",
        }:
            pending.pop(payload["request"]["candidate_id"], None)
    return list(pending.values())


def review_candidates(
    store,
    provider,
    candidates: list[dict],
    *,
    now: datetime,
    prompt_dir: Path,
    maximum_characters: int,
) -> dict:
    """At most one batch attempt; retries happen in a later run, never regenerate."""
    from morning_radar.tendencies.core_store import StaleRevision

    result = {"status": "no_input", "items": [], "revision_ids": [], "target_ids": []}
    requests, ready = [], []
    version = review_version(prompt_dir)

    def finish(item, status, *, assessment=None, error=None):
        request = item["request"]
        event = {
            "type": "content_review",
            "recorded_at": now.isoformat(),
            "request": request,
            "status": status,
            "assessment": assessment,
            "error": error,
            "physical_call_id": result.get("physical_call_id"),
            "revisions": [],
        }
        revision = Revision.model_validate(item["revision"])
        if status == "approved":
            event["revisions"] = [revision.model_dump(mode="json")]
        try:
            store.append(event, operation_id="review:" + sha(event))
        except (ValueError, StaleRevision) as exc:
            event.update(status="stale_or_invalid", error=str(exc)[:300], revisions=[])
            store.append(event, operation_id="review:" + sha(event))
        result["items"].append(
            {
                "candidate_id": request["candidate_id"],
                "phase": item["phase"],
                "status": event["status"],
                "error": event["error"],
            }
        )
        if event["status"] == "approved":
            result["revision_ids"].append(revision.revision_id)

    for item in candidates:
        revision = Revision.model_validate(item["revision"])
        old = store.current().get(revision.hypothesis_id)
        if (old.revision_id if old else None) != revision.previous_revision_id:
            finish(item, "stale_or_invalid", error="Target changed before review")
            continue
        if item["request"]["analysis_version"] != version:
            finish(
                item,
                "stale_or_invalid",
                error="Review contract changed; explicit re-evaluation required",
            )
            continue
        proposal = {"candidates": requests + [item["request"]]}
        if len(encoded(proposal)) > maximum_characters or len(ready) >= 12:
            finish(item, "budget_skipped", error="Bounded review input exhausted")
            continue
        requests.append(item["request"])
        ready.append(item)
    if ready:
        context = {"candidates": requests}
        result["request"] = context
        # Durable intent and original candidates exist before any paid transport.
        store.append(
            {"type": "review_attempt", "recorded_at": now.isoformat(), "request": context},
            operation_id="review-intent:" + sha([now.isoformat(), context]),
        )
        try:
            output = ReviewOutput.model_validate(provider.review_hypotheses(context))
            result["output"] = output.model_dump(mode="json")
            result["unexpected_candidate_ids"] = [
                row.get("candidate_id") for row in output.assessments
                if row.get("candidate_id") not in {c["candidate_id"] for c in requests}
            ]
            result["physical_call_id"] = getattr(provider, "last_core_call_id", None)
            for item in ready:
                rows = [
                    a
                    for a in output.assessments
                    if a.get("candidate_id") == item["request"]["candidate_id"]
                ]
                try:
                    if len(rows) != 1:
                        raise ValueError("Missing or duplicate candidate assessment")
                    assessment = validate_assessment(rows[0], item["request"])
                    finish(item, assessment.verdict, assessment=assessment.model_dump(mode="json"))
                except ValueError as exc:
                    finish(item, "invalid_review", error=str(exc)[:300])
        except (AIError, AIBudgetExceeded, ValueError, AttributeError) as exc:
            result["physical_call_id"] = getattr(provider, "last_core_call_id", None)
            for item in ready:
                finish(
                    item,
                    "budget_skipped" if isinstance(exc, AIBudgetExceeded) else "unavailable",
                    error=type(exc).__name__,
                )
        result["raw_output"] = getattr(provider, "last_core_raw_output", None)
        result["raw_response"] = getattr(provider, "last_review_response", None)
        store.append(
            {
                "type": "review_response",
                "recorded_at": now.isoformat(),
                "output": result.get("output"),
                "raw_response": result["raw_response"],
                "physical_call_id": result.get("physical_call_id"),
            },
            operation_id="review-response:" + sha([now.isoformat(), result]),
        )
        result["response_digest"] = getattr(provider, "last_core_response", None)
    if result["items"]:
        result["status"] = (
            "completed" if all(r["status"] == "approved" for r in result["items"])
            and not result.get("unexpected_candidate_ids") else "partial"
        )
    return result

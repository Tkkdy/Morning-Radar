"""Durable multi-hypothesis submissions and receipts, usable by CLI and later UI."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import Field

from morning_radar.models.core import RadarModel, _validate_http_url
from morning_radar.tendencies.core_models import SplitOutput
from morning_radar.tendencies.core_store import CoreStore
from morning_radar.tendencies.inputs import digest


class SeedDraft(RadarModel):
    text: str = Field(min_length=1, max_length=4000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


def submit(
    store: CoreStore,
    *,
    raw_text: str,
    author: str,
    now: datetime,
    entries: list[dict] | None = None,
    materials: list[dict] | None = None,
    submission_id: str | None = None,
    synthetic: bool = False,
    correction_of: str | None = None,
    expected_revision: str | None = None,
) -> dict:
    if now.tzinfo is None:
        raise ValueError("Submission time must include timezone")
    if not raw_text.strip() or len(raw_text) > 32000 or not author.strip():
        raise ValueError("Submission requires bounded original text and author")
    if correction_of and not expected_revision:
        raise ValueError("A correction must identify its expected revision")
    materials = materials or []
    if len(materials) > 20:
        raise ValueError("Too many materials")
    for material in materials:
        if material.get("kind") not in {"personal_observation", "user_hypothesis", "source_claim"}:
            raise ValueError("User input cannot declare independently verified facts")
        if not material.get("scope") or not 1 <= len(material.get("text", "")) <= 2000:
            raise ValueError("Materials require bounded excerpts and explicit scope")
        if material.get("url"):
            _validate_http_url(material["url"])
        for key in ["observed_at", "published_at"]:
            if material.get(key):
                parsed = datetime.fromisoformat(material[key])
                if parsed.tzinfo is None:
                    raise ValueError("Material times must be aware")
    identity = submission_id or uuid4().hex
    event = {
        "type": "submission",
        "submission_id": identity,
        "raw_text": raw_text,
        "author": author,
        "submitted_at": now.isoformat(),
        "entries": entries,
        "materials": materials,
        "synthetic": synthetic,
        "correction_of": correction_of,
        "expected_revision": expected_revision,
    }
    # Repeated imports use the original timestamp; intentional resubmissions get a new ID.
    existing = store.submissions().get(identity)
    if existing:
        event["submitted_at"] = existing["submitted_at"]
    store.append(event, operation_id="submission:" + identity)
    if identity not in store.receipts():
        store.append(
            {
                "type": "receipt",
                "submission_id": identity,
                "status": "pending",
                "items": [],
                "gaps": ["awaiting_split"],
            },
            operation_id="receipt-pending:" + identity,
        )
    if entries is not None:
        persist_split(store, event, {"items": entries, "gaps": []})
    return receipt(store, identity)


def persist_split(store: CoreStore, submission: dict, output: dict) -> dict:
    parsed = SplitOutput.model_validate(output)
    items, covered = [], set()
    original = submission["raw_text"]
    old = store.receipts().get(submission["submission_id"], {})
    accepted = {row["seed_id"]: row for row in old.get("items", []) if row["status"] == "accepted"}
    for index, value in enumerate(parsed.items):
        seed_id = f"seed-{submission['submission_id']}-{index}"
        try:
            item = SeedDraft.model_validate(value)
            seed_id = (
                "seed-" + submission["submission_id"] + "-" + digest(f"{item.start}:{item.end}")
            )
            if original[item.start : item.end] != item.text or item.end > len(original):
                raise ValueError("Split must preserve original character span")
            if any(i in covered for i in range(item.start, item.end)):
                raise ValueError("Overlapping split spans")
            covered.update(range(item.start, item.end))
            row = {
                "seed_id": seed_id,
                "status": "accepted",
                **item.model_dump(),
                "submission_id": submission["submission_id"],
            }
            if seed_id in accepted and accepted[seed_id] != row:
                raise ValueError("A processed seed cannot be silently rewritten")
            items.append(row)
        except ValueError:
            items.append(
                accepted.get(
                    seed_id, {"seed_id": seed_id, "status": "invalid", "input_index": index}
                )
            )
    # Retain all accepted original spans across partial retries.
    present = {r["seed_id"] for r in items}
    items.extend(row for key, row in accepted.items() if key not in present)
    covered = {
        i for row in items if row["status"] == "accepted" for i in range(row["start"], row["end"])
    }
    uncovered = [i for i, char in enumerate(original) if not char.isspace() and i not in covered]
    complete = bool(items) and not uncovered and all(r["status"] == "accepted" for r in items)
    result = {
        "type": "receipt",
        "submission_id": submission["submission_id"],
        "status": "processed" if complete else "partial",
        "items": items,
        "uncovered_characters": len(uncovered),
        "gaps": parsed.gaps,
    }
    import json

    store.append(result, operation_id="receipt:" + digest(json.dumps(result, sort_keys=True)))
    return result


def receipt(store: CoreStore, identity: str) -> dict:
    original = store.submissions()[identity]
    result = store.receipts().get(identity, {"status": "pending", "items": []})
    mapped = {
        seed: r.hypothesis_id
        for r in store.draft_revisions()
        for seed in [r.seed_id, r.decision.seed_id]
        if seed
    }
    return {
        "submission": original,
        "receipt": result,
        "analysis": {
            item["seed_id"]: {
                "hypothesis_id": mapped.get(item["seed_id"]),
                "status": "recorded" if item["seed_id"] in mapped else "unassessed",
            }
            for item in result["items"]
            if item["status"] == "accepted"
        },
    }

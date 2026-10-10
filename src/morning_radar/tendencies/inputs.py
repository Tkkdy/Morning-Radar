"""Bounded independent evidence adapter; no Brief or news-score gate."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from morning_radar.research.materials import load_material_evidence as load_material_evidence
from morning_radar.tendencies.core_models import CoreEvidence
from morning_radar.tendencies.core_store import CoreStore


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def read_evidence(
    root: Path,
    store: CoreStore,
    *,
    through: datetime,
    maximum_files: int = 200,
    maximum_evidence: int = 200,
) -> tuple[list, dict]:
    # Read B1 versions rather than only the latest: a later failed fetch must not
    # hide an earlier useful excerpt. Preserve its real availability time.
    paths = sorted((root / "data/evidence/v1").glob("*/*.json"), reverse=True)
    checkpoints = sorted((root / "data/intake/checkpoints").glob("*.json"), reverse=True)
    relations, records, errors = {}, [], []
    checkpoint_truncated = False
    for path in sorted((root / "data/evidence/events").glob("*.json"), reverse=True)[
        :maximum_files
    ]:
        try:
            for event in json.loads(path.read_text(encoding="utf-8")).get("events", []):
                if _time(event["recorded_at"]) <= through:
                    for identity in event["input_ids"]:
                        relations.setdefault(identity, set()).add(event["event_id"])
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({"path": str(path.relative_to(root)), "error": type(exc).__name__})
    scan = [(p, "b1") for p in paths[:maximum_files]] + [
        (p, "checkpoint") for p in checkpoints[:maximum_files]
    ]
    for path, kind in scan:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if kind == "b1":
                rows = [payload]
            else:
                manifest = payload["manifest"]
                if not manifest["complete"] or _time(manifest["created_at"]) > through:
                    continue
                rows = []
                checkpoint_rows = payload.get("items", payload.get("records", []))
                checkpoint_truncated |= len(checkpoint_rows) > maximum_evidence
                for record in checkpoint_rows[:maximum_evidence]:
                    raw = record["item"]
                    rows.append(
                        {
                            "input_id": raw["id"],
                            "content_version": record["content_version"],
                            "source_url": raw["url"],
                            "title": raw["title"],
                            "excerpt": (raw.get("content_excerpt") or raw.get("summary", ""))[
                                :2000
                            ],
                            "source_role": raw.get("source_role", "unknown"),
                            "statement_type": raw.get("statement_type", "unknown"),
                            "published_at": raw.get("published_at"),
                            "captured_at": raw["fetched_at"],
                            "observed_at": record.get("durable_at") or manifest["created_at"],
                            "status": "captured",
                            "synthetic": raw.get("metadata", {}).get("synthetic", False),
                        }
                    )
            for row in rows:
                captured, ingested = _time(row["captured_at"]), _time(row["observed_at"])
                if max(captured, ingested) > through:
                    continue
                excerpt = row.get("excerpt", "")[:2000]
                version = digest(row["content_version"] + excerpt)
                url = row["source_url"]
                scope = row.get("scope") or [urlsplit(url).hostname or "unknown-source"]
                records.append(
                    CoreEvidence(
                        evidence_id="ev-" + digest(url + version),
                        input_id=row["input_id"],
                        content_version=version,
                        event_ids=sorted(relations.get(row["input_id"], [])),
                        source_url=url,
                        source_role=row.get("source_role", "unknown"),
                        title=row["title"],
                        excerpt=excerpt,
                        scope=scope,
                        published_at=_time(row.get("published_at")),
                        observed_at=captured,
                        ingested_at=ingested,
                        status="unverified"
                        if row.get("statement_type")
                        in {
                            "unverified_lead",
                            "opinion",
                            "marketing_self_promotion",
                            "analysis_judgement",
                        }
                        or (
                            row.get("source_role") == "upstream_discovery"
                            and row.get("status") != "fetched"
                        )
                        else row.get("status", "captured"),
                        information_gaps=row.get("remaining_gaps", row.get("information_gaps", [])),
                        synthetic=row.get("synthetic", False),
                        provenance=str(path.relative_to(root)),
                    )
                )
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({"path": str(path.relative_to(root)), "error": type(exc).__name__})
    for submission in store.submissions().values():
        if _time(submission["submitted_at"]) > through:
            continue
        for index, material in enumerate(submission["materials"]):
            records.append(
                CoreEvidence(
                    evidence_id=f"user-{submission['submission_id']}-{index}",
                    input_id=submission["submission_id"],
                    content_version=digest(material["text"]),
                    source_url=material.get("url"),
                    source_kind=material["kind"],
                    title="用户投递材料",
                    excerpt=material["text"],
                    scope=["personal:" + submission["author"] + ":" + s for s in material["scope"]]
                    if material["kind"] == "personal_observation"
                    else material["scope"],
                    observed_at=_time(material.get("observed_at"))
                    or _time(submission["submitted_at"]),
                    published_at=_time(material.get("published_at")),
                    ingested_at=_time(submission["submitted_at"]),
                    synthetic=submission["synthetic"],
                    provenance="submission:" + submission["submission_id"],
                )
            )
    # Same content re-imported from a checkpoint/B1 or mirrored at another URL is
    # one observation. Event identity alone does NOT collapse real follow-ups.
    unique = {}
    for record in sorted(records, key=lambda r: (r.ingested_at, r.evidence_id)):
        if max(record.observed_at, record.ingested_at) > through:
            continue
        key = (
            digest(" ".join(record.excerpt.split()))
            if record.excerpt.strip()
            else record.evidence_id
        )
        key += ":" + record.source_kind
        if key not in unique or (
            unique[key].status not in {"captured", "fetched"}
            and record.status in {"captured", "fetched"}
        ):
            unique[key] = record
    result = sorted(unique.values(), key=lambda r: (r.ingested_at, r.evidence_id), reverse=True)
    return result[:maximum_evidence], {
        "scanned_files": len(scan),
        "scan_truncated": len(paths) > maximum_files or len(checkpoints) > maximum_files,
        "evidence_truncated": checkpoint_truncated or len(result) > maximum_evidence,
        "errors": errors,
    }


def terms(text: str) -> set[str]:
    words = set(re.findall(r"[a-z]{3,}", text.lower()))
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(part[i : i + 2] for i in range(len(part) - 1))
    return words


def select_evidence(
    evidence: list[CoreEvidence], *, query: str, maximum: int, novel_ids: set[str]
) -> tuple[list[CoreEvidence], list[dict]]:
    # Content retrieval over constraints/methods/observations, not company tags.
    query_terms = terms(query)
    ranked = sorted(
        evidence,
        key=lambda e: (
            e.evidence_id in novel_ids,
            len(terms(e.title + " " + e.excerpt) & query_terms),
            bool(re.search(r"fail|limit|cost|无法|限制|失败|成本|反例", e.excerpt, re.I)),
            e.ingested_at,
        ),
        reverse=True,
    )
    # Reserve background/counter-context space even with many new observations.
    seeds = ranked[: max(1, maximum // 2)]
    remaining = sorted(
        [e for e in ranked if e not in seeds],
        key=lambda e: (
            len(terms(e.excerpt) & query_terms),
            bool(re.search(r"fail|limit|无法|限制|失败|反例", e.excerpt, re.I)),
            e.ingested_at,
        ),
        reverse=True,
    )
    selected = (seeds + remaining)[:maximum]
    return selected, [
        {
            "evidence_id": e.evidence_id,
            "role": "new_condition_candidate"
            if e.evidence_id in novel_ids
            else "background_or_counter_context",
            "status": e.status,
        }
        for e in selected
    ]

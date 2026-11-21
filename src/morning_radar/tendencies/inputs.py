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
    maximum_checkpoint_rows: int = 5000,
    checkpoint_offsets: dict[str, int] | None = None,
) -> tuple[list, dict]:
    # Read B1 versions rather than only the latest: a later failed fetch must not
    # hide an earlier useful excerpt. Preserve its real availability time.
    paths = sorted((root / "data/evidence/v1").glob("*/*.json"), reverse=True)
    checkpoints = sorted((root / "data/intake/checkpoints").glob("*.json"), reverse=True)
    relations, records, errors = {}, [], []
    checkpoint_truncated = False
    next_offsets, rows_scanned = {}, 0
    checkpoint_offsets = checkpoint_offsets or {}
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
                checkpoint_truncated |= len(checkpoint_rows) > maximum_checkpoint_rows
                key = path.name
                start = checkpoint_offsets.get(key, 0) % max(1, len(checkpoint_rows))
                page = checkpoint_rows[start : start + maximum_checkpoint_rows]
                next_offsets[key] = (
                    0 if start + len(page) >= len(checkpoint_rows) else start + len(page)
                )
                rows_scanned += len(page)
                for record in page:
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
                scope = row.get("scope") or []
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
    # Interleave publisher queues before the visible-evidence cap. This is read
    # fairness, not publication quotas or an assertion of independent support.
    queues = {}
    for record in result:
        queues.setdefault(
            urlsplit(record.source_url or "").hostname or record.source_kind, []
        ).append(record)
    result = []
    while any(queues.values()):
        for queue in queues.values():
            if queue:
                result.append(queue.pop(0))
    return result[:maximum_evidence], {
        "scanned_files": len(scan),
        "checkpoint_rows_scanned": rows_scanned,
        "checkpoint_next_offsets": next_offsets,
        "unique_evidence": len(result),
        "visible_evidence": min(len(result), maximum_evidence),
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
    # Current observations are not scored by overlap with a query made from
    # themselves: that rewards long old prose. Calibration still uses its target.
    query_terms = terms(query)
    ranked = sorted(
        evidence,
        key=lambda e: (
            bool(e.excerpt.strip()),
            e.evidence_id in novel_ids,
            min(12, len(terms(e.title + " " + e.excerpt) & query_terms)) if query else 0,
            e.ingested_at,
        ),
        reverse=True,
    )
    seeds = ranked[: max(1, maximum // 2)]
    related_terms = query_terms or terms(" ".join(e.title + " " + e.excerpt[:240] for e in seeds))
    remaining = sorted(
        [e for e in ranked if e not in seeds],
        key=lambda e: (
            min(12, len(terms(e.title + " " + e.excerpt) & related_terms)),
            bool(re.search(r"fail|limit|cost|无法|限制|失败|成本|反例", e.excerpt, re.I)),
            bool(e.excerpt.strip()),
            e.ingested_at,
        ),
        reverse=True,
    )
    selected = []
    for index in range(maximum):
        pool = seeds if index % 2 == 0 and seeds else remaining
        if not pool:
            pool = seeds
        if pool:
            selected.append(pool.pop(0))
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


def excerpt_segments(text: str, maximum: int = 700) -> tuple[str, list[tuple[int, int]]]:
    """Select verbatim condition/limitation sentences; offsets refer to frozen excerpt."""
    if len(text) <= maximum:
        return text, [(0, len(text))] if text else []
    sentences = []
    boundary = re.compile(r"(?<=[.!?。！？])\s+|\n+")
    start = 0
    for match in boundary.finditer(text):
        sentences.append((start, match.start()))
        start = match.end()
    sentences.append((start, len(text)))

    def rank(span):
        sentence = text[span[0] : span[1]]
        # Prefer qualifying conditions and counter-paths over introductory prose.
        limitation = bool(
            re.search(
                r"in theory|limitation|cannot|can't|tricky|crude|however|but |除非|限制|无法|但是",
                sentence,
                re.I,
            )
        )
        condition = bool(
            re.search(r"\bif\b|because|requires?|need[s]?|依赖|如果|因为", sentence, re.I)
        )
        alternative = bool(re.search(r"in theory|theoretically|理论上|理论可行", sentence, re.I))
        return (limitation + condition + 2 * alternative, limitation, -span[0])

    chosen, used = [], 0
    for start, end in sorted(sentences, key=rank, reverse=True):
        index = sentences.index((start, end))
        if index and re.search(
            r"in theory|theoretically|理论上|理论可行", text[start:end], re.I
        ):
            # A counter-path often refers back to the previous sentence's subject.
            # Keep both verbatim, instead of assigning the limit to another condition.
            start = sentences[index - 1][0]
        if any(start < old_end and end > old_start for old_start, old_end in chosen):
            continue
        if end > start and used + end - start + (5 if chosen else 0) <= maximum:
            chosen.append((start, end))
            used += end - start + (5 if len(chosen) > 1 else 0)
    if not chosen:
        chosen = [(0, maximum)]
    chosen.sort()
    return "\n[…]\n".join(text[a:b] for a, b in chosen), chosen


def pack_evidence(
    context: dict, selected: list[CoreEvidence], evidence: list[CoreEvidence], limit: int
):
    """Short traceable overview, then quote-bearing excerpts under the same cap."""
    import json

    def size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    overview = []
    for item in evidence[:40]:
        row = {
            "evidence_id": item.evidence_id,
            "title": item.title[:70],
            "observed_at": item.observed_at.isoformat(),
            "summary": item.excerpt[:100],
        }
        if size(overview + [row]) > limit // 5:
            break
        overview.append(row)
    context["evidence_overview"] = overview
    context["overview_role"] = "discovery_leads_only; not citable evidence"
    context["evidence"] = []
    included = []
    diagnostics = []
    for item in selected:
        row = item.model_dump(mode="json", exclude={"schema_version", "provenance", "synthetic"})
        row["excerpt"], row["excerpt_spans"] = excerpt_segments(item.excerpt)
        row["excerpt_source_sha256"] = hashlib.sha256(item.excerpt.encode()).hexdigest()
        fits = size({**context, "evidence": context["evidence"] + [row]}) <= limit
        if fits:
            context["evidence"].append(row)
            # The validator sees exactly the excerpt given to the model.
            included.append(
                item.model_copy(
                    update={
                        "excerpt": row["excerpt"],
                        "excerpt_spans": row["excerpt_spans"],
                        "excerpt_source_sha256": row["excerpt_source_sha256"],
                    }
                )
            )
        diagnostics.append(
            {
                "evidence_id": item.evidence_id,
                "input_id": item.input_id,
                "content_version": item.content_version,
                "ingested_at": item.ingested_at.isoformat(),
                "reason": "quoted_excerpt" if fits else "request_character_cap",
                "characters": size(row),
                "utf8_bytes": len(json.dumps(row, ensure_ascii=False).encode()),
                "excerpt_truncated": len(item.excerpt) > len(row["excerpt"]),
                "empty_excerpt": not bool(item.excerpt.strip()),
            }
        )
    chosen = {e.evidence_id for e in selected}
    diagnostics.extend(
        {
            "evidence_id": e.evidence_id,
            "input_id": e.input_id,
            "content_version": e.content_version,
            "ingested_at": e.ingested_at.isoformat(),
            "reason": "selection_cap",
            "empty_excerpt": not bool(e.excerpt.strip()),
        }
        for e in evidence
        if e.evidence_id not in chosen
    )
    return included, diagnostics

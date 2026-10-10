"""Bounded original-material excerpts, independent of final news placement.

No provider calls, search, URL invention, recursive browsing, PDF ingestion or full-text storage.
Immutable snapshots retain source/input identity and observed time separately.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from time import monotonic
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from morning_radar.ai.usage import atomic_json
from morning_radar.collectors.http import HttpClient, RequestBudgetExceeded, RequestStartBudget
from morning_radar.intake.identity import content_version
from morning_radar.models import RawItem
from morning_radar.settings import MaterialEnrichmentConfig
from morning_radar.time_utils import display_date


class MaterialParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.blocks: list[str] = []
        self.current: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "header", "footer", "form"}:
            self.skip += 1
        if self.skip:
            return
        if tag in {"p", "h1", "h2", "h3", "li", "blockquote"}:
            self.flush()
        if tag == "a" and len(self.links) < 100:
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "header", "footer", "form"}:
            self.skip = max(0, self.skip - 1)
        if not self.skip and tag in {"p", "h1", "h2", "h3", "li", "blockquote"}:
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.current.append(data)

    def flush(self):
        text = " ".join(" ".join(self.current).split())
        self.current = []
        if len(text) >= 20 and len(self.blocks) < 1000:
            self.blocks.append(text)


def material_gaps(item: RawItem) -> list[str]:
    text = item.content_excerpt or item.summary
    gaps = []
    if len(text.strip()) < 300 or item.metadata.get("excerpt_truncated"):
        gaps.append("source_detail")
    if re.search(r"paper|technical report|论文|技术报告|arxiv", item.title + " " + item.url, re.I):
        for name, pattern in {
            "method": r"method|approach|方法",
            "author_evidence": r"experiment|benchmark|evaluation|实验|评测",
            "conditions": r"dataset|hardware|setting|数据集|硬件|条件",
            "limitations": r"limitation|failure|局限|限制",
        }.items():
            if not re.search(pattern, text, re.I):
                gaps.append(name)
    return gaps


@dataclass
class MaterialResult:
    items: list[RawItem]
    snapshots: list[dict] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _input_version(item: RawItem) -> str:
    if item.metadata.get("content_version"):
        return str(item.metadata["content_version"])
    try:
        return content_version(item)
    except ValueError:
        return hashlib.sha256(item.model_dump_json().encode()).hexdigest()[:20]


def _identity(item: RawItem) -> str:
    version = _input_version(item)
    return hashlib.sha256(f"{item.id}:{version}".encode()).hexdigest()[:24]


def enrich_materials(
    items: list[RawItem],
    *,
    root: Path,
    now: datetime,
    policy: MaterialEnrichmentConfig,
    important_ids: set[str],
    http: HttpClient | None = None,
    network_allowed: bool = False,
) -> MaterialResult:
    """Reuse bounded HTTP and retain failures; only fetch existing input URLs."""
    started = monotonic()
    request_budget = RequestStartBudget(
        maximum_requests=policy.maximum_requests, deadline_seconds=policy.deadline_seconds
    )
    client = http or HttpClient(timeout_seconds=min(10, policy.deadline_seconds), attempts=2)
    previous_hook = client.before_attempt

    def before_attempt():
        request_budget.before_attempt()
        if previous_hook:
            previous_hook()

    client.before_attempt = before_attempt
    result = MaterialResult(items=[])
    fetched = 0
    observed_urls = {i.url for i in items}
    run_id = uuid4().hex
    try:
        for item in items:
            identity = _identity(item)
            gaps = material_gaps(item)
            snapshot = {
                "schema_version": 1,
                "evidence_id": identity,
                "run_id": run_id,
                "input_id": item.id,
                "content_version": _input_version(item),
                "source_url": item.url,
                "title": item.title,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "captured_at": item.fetched_at.isoformat(),
                "observed_at": now.isoformat(),
                "source_role": item.source_role.value,
                "statement_type": item.statement_type.value,
                "information_gaps": gaps,
                "status": "captured",
                "http_requests": 0,
                "excerpt": (item.content_excerpt or item.summary)[
                    : policy.maximum_excerpt_characters
                ],
                "linked_input_urls": [],
                "ai_calls": 0,
            }
            material = item.model_copy(deep=True)
            if gaps and item.id in important_ids and policy.enabled:
                try:
                    parsed = urlsplit(item.url)
                    allowed = (
                        parsed.scheme == "https"
                        and parsed.hostname in policy.allowed_hosts
                        and not parsed.username
                        and not parsed.password
                        and parsed.port in {None, 443}
                        and not parsed.query
                    )
                except ValueError:
                    allowed = False
                if not allowed:
                    snapshot["status"] = "not_allowed"
                elif not network_allowed:
                    snapshot["status"] = "offline"
                elif (
                    fetched >= policy.maximum_items
                    or request_budget.used >= policy.maximum_requests
                    or monotonic() - started >= policy.deadline_seconds
                ):
                    snapshot["status"] = "budget_skipped"
                else:
                    fetched += 1
                    requests_before = client.request_attempts
                    snapshot["status"] = "started"
                    # The durable started record survives process interruption.
                    path = (
                        root
                        / "data/evidence/v1"
                        / str(display_date(now))
                        / f"{identity}-{run_id}.json"
                    )
                    atomic_json(path, snapshot)
                    try:
                        response = client.get(
                            item.url,
                            maximum_response_bytes=policy.maximum_response_bytes,
                            follow_redirects=False,
                            deadline_monotonic=started + policy.deadline_seconds,
                            timeout=min(
                                10, max(0.1, policy.deadline_seconds - (monotonic() - started))
                            ),
                        )
                        if response.status_code != 200:
                            raise ValueError("http_not_ok")
                        snapshot["http_status"] = response.status_code
                        if "html" not in response.headers.get("content-type", "").lower():
                            snapshot["status"] = "unsupported_format"
                        else:
                            parser = MaterialParser()
                            parser.feed(response.text)
                            parser.flush()
                            needles = (
                                r"method|approach|experiment|benchmark|limitation|dataset|hardware"
                                r"|方法|实验|限制"
                            )
                            blocks = sorted(
                                enumerate(parser.blocks),
                                key=lambda pair: (
                                    not bool(re.search(needles, pair[1], re.I)),
                                    pair[0],
                                ),
                            )
                            excerpt = "\n".join(text[:280] for _, text in blocks[:6])[
                                : policy.maximum_excerpt_characters
                            ]
                            if not excerpt:
                                snapshot["status"] = "parse_empty"
                            else:
                                snapshot.update(
                                    status="fetched",
                                    excerpt=excerpt,
                                    response_sha256=hashlib.sha256(response.content).hexdigest(),
                                )
                                # Only links already collected as inputs can establish a relation.
                                snapshot["linked_input_urls"] = list(
                                    dict.fromkeys(
                                        urljoin(item.url, link)
                                        for link in parser.links
                                        if urljoin(item.url, link) in observed_urls
                                        and urljoin(item.url, link) != item.url
                                    )
                                )
                                material.content_excerpt = excerpt
                                material.summary = item.summary  # Keep collector summary unchanged.
                    except BaseException as exc:
                        if not isinstance(exc, Exception):
                            snapshot["status"] = "cancelled"
                            raise
                        snapshot["status"] = (
                            "budget_skipped" if isinstance(exc, RequestBudgetExceeded) else "failed"
                        )
                        snapshot["error_type"] = type(exc).__name__
                    finally:
                        snapshot["http_requests"] = client.request_attempts - requests_before
                        atomic_json(path, snapshot)
            snapshot["remaining_gaps"] = material_gaps(material)
            material.metadata["evidence_id"] = identity
            material.metadata["material_links"] = snapshot["linked_input_urls"]
            material.metadata["material_information_gaps"] = snapshot["information_gaps"]
            material.metadata["material_status"] = snapshot["status"]
            result.items.append(material)
            result.snapshots.append(snapshot)
            path = root / "data/evidence/v1" / str(display_date(now)) / f"{identity}-{run_id}.json"
            atomic_json(path, snapshot)
    finally:
        client.before_attempt = previous_hook
    result.stats = {
        "material_http_requests": request_budget.used,
        "material_candidates": len(items),
        "material_fetched": sum(s["status"] == "fetched" for s in result.snapshots),
        "material_incomplete": sum(
            s["status"] not in {"captured", "fetched"} for s in result.snapshots
        ),
        "material_ai_calls": 0,
    }
    return result


def load_material_evidence(root: Path, *, through: datetime, maximum: int = 200) -> list[dict]:
    """Tendency input adapter: no news score/placement filter and no mutation."""
    latest = {}
    for path in sorted((root / "data/evidence/v1").glob("*/*.json")):
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if datetime.fromisoformat(snapshot["observed_at"]) > through:
            continue
        key = snapshot["evidence_id"]
        if key not in latest or snapshot["observed_at"] > latest[key]["observed_at"]:
            latest[key] = snapshot
    return sorted(latest.values(), key=lambda s: s["observed_at"], reverse=True)[:maximum]


def validate_technical_reading(reading, items: list[RawItem]) -> None:
    """Quotes must occur in the attributed source excerpt; no invented evidence URLs."""
    from morning_radar.ai import AIOutputError

    by_url = {}
    for item in items:
        by_url.setdefault(item.url, []).extend([item.summary, item.content_excerpt])
    if reading.problem is None or reading.method is None:
        raise AIOutputError("Technical reading requires a sourced problem and method")
    for name in [
        "problem",
        "method",
        "engineering_value",
        "author_evidence",
        "conditions",
        "limitations",
    ]:
        point = getattr(reading, name)
        if point is None:
            continue
        texts = by_url.get(point.source_url, [])
        quote = " ".join(point.quote.split())
        if not any(quote in " ".join(text.split()) for text in texts):
            raise AIOutputError("Technical reading quote is absent from its attributed source")


def save_event_evidence(root: Path, stories, *, now: datetime) -> None:
    records = [
        {
            "event_id": story.id,
            "evidence_ids": story.evidence_ids,
            "input_ids": story.source_item_ids,
            "source_urls": story.source_urls,
            "recorded_at": now.isoformat(),
        }
        for story in stories
    ]
    atomic_json(
        root / "data/evidence/events" / f"{display_date(now)}-{uuid4().hex}.json",
        {"schema_version": 1, "events": records},
    )

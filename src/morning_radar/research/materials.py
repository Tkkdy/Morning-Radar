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
from morning_radar.models.core import (
    GroundedAnalysis,
    GroundedPoint,
    ResearchEvidenceRef,
    TechnicalQuoteNormalization,
    TechnicalReading,
)
from morning_radar.settings import MaterialEnrichmentConfig
from morning_radar.time_utils import display_date


class MaterialParser(HTMLParser):
    """Collect body paragraphs, preferring the document's semantic content region."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.blocks: list[str] = []
        self.block_scopes: list[int] = []
        self.current: list[str] = []
        self.links: list[str] = []
        self.body_tag: str | None = None
        self.block_scope = 0
        self.main_depth = 0
        self.article_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"head", "script", "style", "nav", "header", "footer", "form", "aside"}:
            self.skip += 1
        if self.skip:
            return
        if tag == "main":
            self.main_depth += 1
        elif tag == "article":
            self.article_depth += 1
        if tag in {"p", "blockquote", "li"}:
            self.flush()
            self.body_tag = tag
            self.block_scope = 2 if self.article_depth else 1 if self.main_depth else 0
        if tag == "a" and len(self.links) < 100:
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in {"head", "script", "style", "nav", "header", "footer", "form", "aside"}:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in {"p", "blockquote", "li"}:
            self.flush()
            self.body_tag = None
        if tag == "main":
            self.main_depth = max(0, self.main_depth - 1)
        elif tag == "article":
            self.article_depth = max(0, self.article_depth - 1)

    def handle_data(self, data):
        if not self.skip and self.body_tag is not None:
            self.current.append(data)

    def flush(self):
        text = " ".join(" ".join(self.current).split())
        self.current = []
        if len(text) >= 20 and len(self.blocks) < 1000:
            self.blocks.append(text)
            self.block_scopes.append(self.block_scope)

    def content_blocks(self) -> list[str]:
        if not self.blocks:
            return []
        scope = max(self.block_scopes)
        return [
            text for text, block_scope in zip(self.blocks, self.block_scopes, strict=True)
            if block_scope == scope
        ]

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
                                enumerate(parser.content_blocks()),
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


# Deliberately finite: no NFKC, case folding, punctuation removal or fuzzy matching.
_QUOTATION_MARKS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def _quote_text(text: str, *, typography: bool) -> tuple[str, list[tuple[int, int]]]:
    """Preserve offsets while applying the existing whitespace equivalence."""
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    for word in re.finditer(r"\S+", text):
        if chars:
            chars.append(" ")
            spans.append((spans[-1][1], word.start()))
        for index in range(word.start(), word.end()):
            char = text[index]
            chars.append(char.translate(_QUOTATION_MARKS) if typography else char)
            spans.append((index, index + 1))
    return "".join(chars), spans


def validate_technical_reading(
    reading: TechnicalReading, items: list[RawItem],
) -> tuple[TechnicalReading | None, list[TechnicalQuoteNormalization]]:
    """Return a validated copy with source-original quotes and conversion evidence.

    Search every attributed fragment for an exact match before allowing whitespace
    and then the four listed quotation marks. Never concatenate source fragments.
    The input draft and collected material remain unchanged, including on failure.
    """
    points = {name: getattr(reading, name) for name in (
        "problem", "method", "engineering_value", "author_evidence", "evaluation_results",
        "conditions", "limitations",
    ) if getattr(reading, name) is not None}
    updates, conversions = validate_grounded_points(points, items)
    # Validate supplied evidence before omitting an incomplete optional card.
    if reading.problem is None or reading.method is None:
        return None, []
    return reading.model_copy(update=updates), conversions


def validate_analysis(
    analyses: list[GroundedAnalysis], items: list[RawItem],
) -> tuple[list[GroundedAnalysis], list[TechnicalQuoteNormalization]]:
    points = {f"analysis.{i}.premises.{j}": point
              for i, analysis in enumerate(analyses) for j, point in enumerate(analysis.premises)}
    restored, conversions = validate_grounded_points(points, items)
    return [analysis.model_copy(update={"premises": [
        restored[f"analysis.{i}.premises.{j}"] for j in range(len(analysis.premises))
    ]}) for i, analysis in enumerate(analyses)], conversions


@dataclass(frozen=True)
class QuoteSource:
    """A previously validated premise exposed verbatim in the current request."""

    id: str
    url: str
    summary: str
    content_excerpt: str = ""


def validate_grounded_points(
    points: dict[str, GroundedPoint], items: list[RawItem | ResearchEvidenceRef | QuoteSource],
) -> tuple[dict[str, GroundedPoint], list[TechnicalQuoteNormalization]]:
    """Validate literal attribution, not semantic entailment or inference truth."""
    from morning_radar.ai import AIOutputError

    updates = {}
    conversions = []
    for name, point in points.items():
        fragments = [
            (item, source_field, getattr(item, source_field))
            for item in items if item.url == point.source_url
            for source_field in ("summary", "content_excerpt")
        ]
        match = None
        for mode in ("exact", "whitespace-v1", "quotation-marks-v1"):
            for item, source_field, text in fragments:
                if mode == "exact":
                    start = text.find(point.quote)
                    end = start + len(point.quote)
                else:
                    normalized, spans = _quote_text(text, typography=mode == "quotation-marks-v1")
                    quote, _ = _quote_text(point.quote, typography=mode == "quotation-marks-v1")
                    offset = normalized.find(quote) if quote else -1
                    start, end = (-1, -1) if offset < 0 else (
                        spans[offset][0], spans[offset + len(quote) - 1][1])
                if start >= 0 and 12 <= end - start <= 280:
                    match = (item, source_field, text[start:end], start, end, mode)
                    break
            if match is not None:
                break
        if match is None:
            raise AIOutputError("Technical reading quote is absent from its attributed source")
        item, source_field, source_quote, start, end, mode = match
        updates[name] = point.model_copy(update={"quote": source_quote})
        if source_quote != point.quote:
            conversions.append(TechnicalQuoteNormalization(
                point=name, original_quote=point.quote, source_url=point.source_url,
                raw_item_id=item.raw_item_id if isinstance(item, ResearchEvidenceRef) else item.id,
                source_field=source_field, source_start=start, source_end=end,
                rule=mode,
            ))
    return updates, conversions


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

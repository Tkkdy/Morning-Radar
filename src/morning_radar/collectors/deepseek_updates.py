"""Bounded parser for the configured DeepSeek updates page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

from morning_radar.collectors.http import HttpClient
from morning_radar.models import RawItem, SourceRole, StatementType
from morning_radar.processing import stable_item_id

_ZERO_WIDTH = re.compile(r"[\u200b-\u200d\ufeff]")
_DATE = re.compile(r"(?:时间\s*[:：]\s*)?(\d{4}-\d{1,2}-\d{1,2})")


def _clean(value: str) -> str:
    return " ".join(_ZERO_WIDTH.sub("", value).split())


def _source_date(value: str) -> str | None:
    match = _DATE.search(_clean(value))
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class UpdateSection:
    source_date: str
    title: str
    anchor: str
    text: str


class _UpdatesParser(HTMLParser):
    """Parse only article h2/h3 sections and retain all body blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[UpdateSection] = []
        self.rejected: list[str] = []
        self._article_depth = 0
        self._tag: str | None = None
        self._text: list[str] = []
        self._date: str | None = None
        self._title: str | None = None
        self._anchor = ""
        self._blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "article":
            self._article_depth += 1
            return
        if not self._article_depth:
            return
        if tag in {"h2", "h3", "p", "li"}:
            self._tag, self._text = tag, []
            if tag == "h3":
                self._finish_section()
                self._anchor = dict(attrs).get("id") or ""
        elif self._tag == "h3" and not self._anchor:
            self._anchor = dict(attrs).get("id") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "article" and self._article_depth:
            self._article_depth -= 1
            if not self._article_depth:
                self._finish_section()
            return
        if not self._article_depth or tag != self._tag:
            return
        value = _clean("".join(self._text))
        self._tag = None
        if tag == "h2":
            self._finish_section()
            self._date = _source_date(value)
            if value and self._date is None:
                self.rejected.append("invalid_date")
        elif tag == "h3":
            self._title = value or None
            if not self._title:
                self.rejected.append("missing_title")
        elif tag in {"p", "li"} and value and self._title:
            self._blocks.append(value)

    def handle_data(self, data: str) -> None:
        if self._article_depth and self._tag is not None:
            self._text.append(data)

    def _finish_section(self) -> None:
        if self._title is not None:
            if self._date and self._anchor and self._blocks:
                self.sections.append(
                    UpdateSection(self._date, self._title, self._anchor, "\n".join(self._blocks))
                )
            else:
                self.rejected.append(
                    "missing_anchor" if not self._anchor else "missing_section_content"
                )
        self._title, self._anchor, self._blocks = None, "", []


class DeepSeekUpdatesCollector:
    name = "deepseek_updates"

    def __init__(self, *, http: HttpClient, source, now, maximum_response_bytes=262144,
                 maximum_excerpt_characters=1600) -> None:
        self.http, self.source, self.now = http, source, now
        self.maximum_response_bytes = maximum_response_bytes
        self.maximum_excerpt_characters = maximum_excerpt_characters
        self.discovery_audit: list[dict[str, object]] = []

    def collect(self) -> list[RawItem]:
        request_start = self.http.request_attempts
        try:
            response = self.http.get(self.source.url)
            if response.status_code == 304:
                raise RuntimeError("unexpected_not_modified")
            if len(response.content) > self.maximum_response_bytes:
                raise RuntimeError("response_too_large")
            parser = _UpdatesParser()
            parser.feed(response.content.decode(response.encoding or "utf-8", errors="replace"))
            items = [self._item(section) for section in parser.sections]
            if not items:
                raise RuntimeError("parse_rejected" if parser.rejected else "parse_no_sections")
        except Exception as exc:
            self.discovery_audit.append(
                {
                    "source_id": self.source.id,
                    "status": "failed",
                    "reason": str(exc),
                    "requests": self.http.request_attempts - request_start,
                }
            )
            raise
        self.discovery_audit.append(
            {
                "source_id": self.source.id,
                "status": "partial" if parser.rejected else "ok",
                "requests": self.http.request_attempts - request_start,
                "persisted": len(items),
                "rejected": sorted(set(parser.rejected)),
            }
        )
        return items

    def _item(self, section: UpdateSection) -> RawItem:
        excerpt = section.text[: self.maximum_excerpt_characters]
        return RawItem(
            id=stable_item_id(f"{self.source.id}:{section.anchor}"), title=section.title,
            url=urljoin(self.source.url, f"#{section.anchor}"), source_name=self.source.name,
            source_type=self.source.type, fetched_at=self.now, language="zh", summary=excerpt[:280],
            content_excerpt=excerpt, source_role=SourceRole.OFFICIAL_PRIMARY,
            statement_type=StatementType.FACTUAL_ANNOUNCEMENT,
            metadata={"source_id": self.source.id, "source_date": section.source_date,
                      "date_precision": "day", "source_timezone": "Asia/Singapore",
                      "published_date_role": "official_update_date", "official_page_fetched": True,
                      "excerpt_truncated": len(section.text) > len(excerpt)},
        )

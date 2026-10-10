"""Bounded adapters for official HTML listing pages without reliable feeds."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from morning_radar.collectors.http import HttpClient
from morning_radar.models import RawItem, SourceRole, StatementType
from morning_radar.processing import stable_item_id

_DATES = (
    re.compile(r"\b(\d{4}-\d{1,2}-\d{1,2})\b"),
    re.compile(r"\b([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})\b"),
)


def _clean(value: str) -> str:
    return " ".join(value.split())


def _source_date(value: str) -> str | None:
    for pattern in _DATES:
        match = pattern.search(value)
        if match is None:
            continue
        for date_format in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(match.group(1), date_format).date().isoformat()
            except ValueError:
                pass
    return None


@dataclass(frozen=True, slots=True)
class ListingEntry:
    url: str
    title: str
    source_date: str | None
    excerpt: str


class _ListingParser(HTMLParser):
    """Read compact article cards; never follow article links or parse documents."""

    def __init__(self, *, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.entries: list[ListingEntry] = []
        self._article_depth = 0
        self._href: str | None = None
        self._tag: str | None = None
        self._text: list[str] = []
        self._title = ""
        self._date = ""
        self._excerpt: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "article":
            self._article_depth += 1
            if self._article_depth == 1:
                self._href, self._title, self._date, self._excerpt = None, "", "", []
            return
        if not self._article_depth:
            return
        values = dict(attrs)
        if tag == "a" and self._href is None:
            self._href = values.get("href")
        if tag in {"h1", "h2", "h3", "time", "p"}:
            self._tag, self._text = tag, []
            if tag == "time" and values.get("datetime"):
                self._date = values["datetime"] or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "article" and self._article_depth:
            self._article_depth -= 1
            if not self._article_depth:
                self._finish_entry()
            return
        if not self._article_depth or tag != self._tag:
            return
        value = _clean("".join(self._text))
        self._tag = None
        if tag in {"h1", "h2", "h3"} and not self._title:
            self._title = value
        elif tag == "time" and not self._date:
            self._date = value
        elif tag == "p" and value:
            self._excerpt.append(value)

    def handle_data(self, data: str) -> None:
        if self._article_depth and self._tag is not None:
            self._text.append(data)

    def _finish_entry(self) -> None:
        if not self._href or not self._title:
            return
        url = urljoin(self.base_url, self._href)
        parsed, base = urlsplit(url), urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != base.netloc:
            return
        self.entries.append(
            ListingEntry(
                url,
                self._title,
                _source_date(self._date),
                _clean(" ".join(self._excerpt)),
            )
        )


class _AnchorListingParser(HTMLParser):
    """Read observed dated link cards, including heading/span based modern listings."""

    def __init__(self, *, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.entries: list[ListingEntry] = []
        self.href: str | None = None
        self.text: list[str] = []
        self.title: list[str] = []
        self.title_depth = 0
        self.depth = 0
        self.date = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a":
            href = urljoin(self.base_url, values.get("href") or "")
            parsed, base = urlsplit(href), urlsplit(self.base_url)
            if (
                parsed.scheme in {"http", "https"} and parsed.netloc == base.netloc
                and parsed.path.startswith(base.path.rstrip("/") + "/")
            ):
                self.href, self.text, self.title, self.date = href, [], [], ""
                self.depth, self.title_depth = 0, 0
        if self.href is None:
            return
        if tag not in {"img", "br", "hr", "input", "meta", "link", "source"}:
            self.depth += 1
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or (
            "title" in str(values.get("class") or "").lower()
        ):
            self.title_depth = self.depth
        if tag == "time" and values.get("datetime"):
            self.date = values["datetime"] or ""

    def handle_endtag(self, tag: str) -> None:
        if self.href is None:
            return
        if tag == "a":
            date = _source_date(self.date or " ".join(self.text))
            title = _clean("".join(self.title))
            # Navigation links have neither an observed date nor a card heading.
            if title and date and len(self.entries) < 500:
                self.entries.append(ListingEntry(self.href, title, date, ""))
            self.href = None
            return
        if self.depth == self.title_depth:
            self.title_depth = 0
        self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.href is not None:
            self.text.append(data)
            if self.title_depth:
                self.title.append(data)


class OfficialListingCollector:
    """Collect observed metadata from a configured official card-list endpoint."""

    def __init__(
        self,
        *,
        http: HttpClient,
        source,
        now,
        maximum_response_bytes: int = 262144,
        maximum_excerpt_characters: int = 1600,
    ) -> None:
        self.name = f"official_listing:{source.id}"
        self.http, self.source, self.now = http, source, now
        self.maximum_response_bytes = maximum_response_bytes
        self.maximum_excerpt_characters = maximum_excerpt_characters
        self.discovery_audit: list[dict[str, object]] = []

    def collect(self) -> list[RawItem]:
        request_start = self.http.request_attempts
        try:
            response = self.http.get(
                self.source.url, maximum_response_bytes=self.maximum_response_bytes
            )
            if response.status_code == 304:
                raise RuntimeError("unexpected_not_modified")
            if len(response.content) > self.maximum_response_bytes:
                raise RuntimeError("response_too_large")
            parser = _ListingParser(base_url=self.source.url)
            parser.feed(response.content.decode(response.encoding or "utf-8", errors="replace"))
            anchors = _AnchorListingParser(base_url=self.source.url)
            anchors.feed(response.content.decode(response.encoding or "utf-8", errors="replace"))
            by_url = {entry.url: entry for entry in [*parser.entries, *anchors.entries]}
            if not by_url:
                raise RuntimeError("parse_no_entries")
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
        items = [self._item(entry) for entry in list(by_url.values())[:500]]
        latest = max((entry.source_date for entry in by_url.values() if entry.source_date),
                     default=None)
        age = (self.now.date() - datetime.fromisoformat(latest).date()).days if latest else None
        self.discovery_audit.append(
            {
                "source_id": self.source.id,
                "status": "ok" if age is not None and age * 24 <= (
                    self.source.freshness_hours
                ) else "stale_or_undated",
                "latest_source_date": latest,
                "response_bytes": len(response.content),
                "truncated": len(by_url) > 500,
                "requests": self.http.request_attempts - request_start,
                "persisted": len(items),
            }
        )
        return items

    def _item(self, entry: ListingEntry) -> RawItem:
        excerpt = entry.excerpt[: self.maximum_excerpt_characters]
        metadata: dict[str, object] = {
            "source_id": self.source.id,
            "priority": self.source.priority,
            "official": self.source.official,
            "official_page_fetched": True,
            "excerpt_truncated": len(entry.excerpt) > len(excerpt),
        }
        if entry.source_date:
            metadata.update({"source_date": entry.source_date, "date_precision": "day"})
        else:
            # The page is authoritative about its own contents, but without an
            # observed event date it cannot establish when the event happened.
            # Keep the source identity and fetched time for later verification
            # without promoting the entry to a factual news event.
            metadata["event_time_unverified"] = True
        return RawItem(
            id=stable_item_id(entry.url),
            title=entry.title,
            url=entry.url,
            source_name=self.source.name,
            source_type=self.source.type,
            fetched_at=self.now,
            language="en",
            summary=excerpt[:280],
            content_excerpt=excerpt,
            source_role=SourceRole.OFFICIAL_PRIMARY,
            statement_type=(
                StatementType.FACTUAL_ANNOUNCEMENT
                if entry.source_date
                else StatementType.UNVERIFIED_LEAD
            ),
            metadata=metadata,
        )

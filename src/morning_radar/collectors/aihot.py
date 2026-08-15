"""Bounded AIHOT v1 discovery adapter.

AIHOT is an upstream discovery source, never a factual authority.  The adapter
keeps the original URL as the RawItem URL and preserves AIHOT attribution in
metadata so later research can resolve the lead against independently collected
evidence.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from dateutil.parser import parse as parse_datetime

from morning_radar.collectors.http import HttpClient
from morning_radar.models import RawItem, SourceRole, StatementType
from morning_radar.processing import stable_item_id
from morning_radar.settings import AIHOTConfig
from morning_radar.storage import read_json, write_json

LOGGER = logging.getLogger(__name__)


class AIHOTCollector:
    name = "aihot_discovery"

    def __init__(
        self,
        config: AIHOTConfig,
        *,
        http: HttpClient,
        state_path: Path,
        now: datetime,
    ) -> None:
        self.config = config
        self.http = http
        self.state_path = state_path
        self.now = now

    def collect(self) -> list[RawItem]:
        if not self.config.enabled:
            LOGGER.info("AIHOT discovery disabled by configuration")
            return []
        state = read_json(self.state_path) if self.state_path.exists() else {}
        headers: dict[str, str] = {}
        if isinstance(state, dict) and state.get("etag"):
            headers["If-None-Match"] = str(state["etag"])
        response = self.http.get(
            self.config.url,
            params={
                "mode": self.config.mode,
                "window": self.config.window,
                "limit": self.config.limit,
            },
            headers=headers,
        )
        if response.status_code == 304:
            LOGGER.info("AIHOT discovery unchanged (ETag)")
            return []
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("AIHOT v1 response is missing items")
        write_json(
            self.state_path,
            {"etag": response.headers.get("ETag", ""), "url": str(response.url)},
        )
        result = [converted for item in items if (converted := self._convert(item))]
        LOGGER.info("AIHOT discovery stats: received=%d retained=%d", len(items), len(result))
        return result

    def _convert(self, value: Any) -> RawItem | None:
        if not isinstance(value, dict):
            return None
        links = value.get("links")
        source = value.get("source")
        if not isinstance(links, dict) or not isinstance(source, dict):
            return None
        public_id = str(value.get("id") or "").strip()
        title = str(value.get("title") or "").strip()
        original_url = str(links.get("original") or "").strip()
        aihot_url = str(links.get("aihot") or "").strip()
        discovered_raw = value.get("discoveredAt")
        source_name = str(source.get("name") or "").strip()
        if not all((public_id, title, original_url, aihot_url, discovered_raw, source_name)):
            return None
        discovered_at = parse_datetime(str(discovered_raw))
        published_raw = value.get("publishedAt")
        published_at = parse_datetime(str(published_raw)) if published_raw else None
        summary = str(value.get("summary") or "").strip()[:2000]
        return RawItem(
            id=stable_item_id(f"aihot:{public_id}"),
            title=title,
            url=original_url,
            source_name=source_name,
            source_type="aihot_discovery",
            published_at=published_at,
            fetched_at=self.now,
            summary=summary,
            content_excerpt=summary,
            source_role=SourceRole.UPSTREAM_DISCOVERY,
            statement_type=StatementType.UNVERIFIED_LEAD,
            metadata={
                "official": False,
                "priority": "medium",
                "aihot_public_id": public_id,
                "aihot_attribution": value.get("attribution") or "AIHOT",
                "aihot_url": aihot_url,
                "original_url": original_url,
                "original_source_name": source_name,
                "observed_at": discovered_at.isoformat(),
                "selected": bool(value.get("selected")),
                "reason": value.get("reason"),
                "discovery_only": True,
            },
        )

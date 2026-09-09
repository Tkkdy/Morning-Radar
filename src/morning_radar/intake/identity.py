"""Stable input identity and content versions for intake records."""

from __future__ import annotations

import hashlib
import json

from morning_radar.intake.models import DiscoveryProvenance
from morning_radar.models import RawItem
from morning_radar.processing.normalize import normalize_url, stable_item_id


def content_version(item: RawItem) -> str:
    """Fingerprint meaningful content, ignoring fetch time and heat counters."""
    payload = {
        "url": normalize_url(item.url),
        "title": item.title.strip(),
        "summary": item.summary.strip(),
        "excerpt": item.content_excerpt.strip(),
        "published_at": item.published_at.isoformat() if item.published_at else None,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return digest[:20]


def intake_key(input_id: str, version: str) -> str:
    return f"{input_id}::{version}"


def provenance_from_item(item: RawItem) -> DiscoveryProvenance:
    source_id = str(item.metadata.get("source_id") or item.source_name)
    return DiscoveryProvenance(
        source_id=source_id,
        source_name=item.source_name,
        source_type=item.source_type,
        url=item.url,
    )


def input_id_for(item: RawItem) -> str:
    return item.id or stable_item_id(item.url)

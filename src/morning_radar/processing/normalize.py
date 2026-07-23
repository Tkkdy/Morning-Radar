"""Deterministic URL, title, and identifier normalization."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref_src",
}
TRACKING_QUERY_PREFIXES = ("utm_",)


def normalize_url(url: str) -> str:
    """Remove fragments and known tracking params without inventing a new URL."""
    parsed = urlsplit(url.strip())
    host = parsed.hostname.lower() if parsed.hostname else ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
        and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    normalized_path = parsed.path or "/"
    if normalized_path != "/":
        normalized_path = normalized_path.rstrip("/")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            host,
            normalized_path,
            urlencode(sorted(query_items)),
            "",
        )
    )


def normalize_title(title: str) -> str:
    """Normalize presentation differences while preserving letters and versions."""
    normalized = unicodedata.normalize("NFKC", title).casefold()
    normalized = re.sub(r"[^\w.\-]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def stable_item_id(url: str) -> str:
    digest = hashlib.sha256(normalize_url(url).encode()).hexdigest()
    return f"item-{digest[:20]}"


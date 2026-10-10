"""Trusted adapters share C transactions; no browser-owned current state."""

from __future__ import annotations

import ipaddress
import json
import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from morning_radar.models.core import RadarModel
from morning_radar.tendencies.core_store import CoreStore
from morning_radar.tendencies.inputs import digest
from morning_radar.tendencies.submissions import receipt, submit
from morning_radar.time_utils import utc_now


class WebRequest(RadarModel):
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    raw_text: str = Field(min_length=1, max_length=32000)
    mode: Literal["free", "lines"] = "free"
    action: Literal["hypotheses", "personal_observation", "source_claim", "correction"] = (
        "hypotheses"
    )
    hypothesis_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,150}$")
    expected_revision: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,150}$")
    source_url: str | None = Field(default=None, max_length=2000)
    scope: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def check(self):
        if not self.raw_text.strip():
            raise ValueError("Empty submission")
        if bool(self.hypothesis_id) != bool(self.expected_revision):
            raise ValueError("Target and expected revision must travel together")
        if self.action == "correction" and not self.hypothesis_id:
            raise ValueError("Correction requires target")
        if self.action in {"personal_observation", "source_claim"} and (
            not self.scope.strip() or len(self.raw_text) > 2000
        ):
            raise ValueError("Materials need scope and a short excerpt (2000 characters)")
        if self.source_url:
            safe_source_url(self.source_url)
        if self.action == "source_claim" and not self.source_url:
            raise ValueError("External material needs its original URL")
        return self


def safe_source_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username is not None:
        raise ValueError("Unsupported source URL")
    host = parsed.hostname.lower()
    if host == "localhost" or "." not in host or host.endswith((".local", ".internal")):
        raise ValueError("Private source URL")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Private source URL")
    # No fetching here. A future fetch must still pass B1 DNS/redirect boundaries.
    return value


def line_entries(text: str) -> list[dict]:
    """Only an explicitly chosen one-judgment-per-line mode skips model splitting."""
    return [
        {"text": match.group(), "start": match.start(), "end": match.end()}
        for match in re.finditer(r"[^\r\n]+", text)
        if match.group().strip()
    ][:20]


def accept_input(
    store: CoreStore,
    request: WebRequest,
    *,
    author: str,
    source: dict,
    synthetic: bool = False,
    now: datetime | None = None,
) -> dict:
    """Caller authenticates author; request fields cannot set identity or provenance."""
    now = now or utc_now()
    identity = "web-" + digest(author + ":" + request.operation_id)
    previous = next((e for e in store.events() if e["operation_id"] == identity), None)
    event = {
        "type": "web_input",
        "submission_id": identity,
        "author": author,
        "request": request.model_dump(),
        "source": source,
        "synthetic": synthetic,
        "received_at": previous["payload"]["received_at"] if previous else now.isoformat(),
    }
    # Lock-held version check AND durable original precede splitting. Retrying also
    # repairs an interruption between this transaction and C's submission/receipt.
    stored = store.append(event, operation_id=identity)
    if stored["acceptance"]["status"] == "accepted":
        materials = []
        if request.action in {"personal_observation", "source_claim"}:
            materials = [
                {
                    "kind": request.action,
                    "text": request.raw_text,
                    "scope": [request.scope],
                    "url": request.source_url,
                }
            ]
        elif request.source_url:
            materials = [
                {
                    "kind": "user_hypothesis",
                    "text": request.raw_text[:2000],
                    "scope": [request.scope or "用户未声明范围"],
                    "url": request.source_url,
                }
            ]
        submit(
            store,
            raw_text=request.raw_text,
            author=author,
            now=datetime.fromisoformat(event["received_at"]),
            submission_id=identity,
            entries=line_entries(request.raw_text) if request.mode == "lines" else None,
            materials=materials,
            synthetic=synthetic,
            correction_of=request.hypothesis_id if request.action == "correction" else None,
            expected_revision=request.expected_revision if request.action == "correction" else None,
        )
    return input_receipt(store, identity)


def input_receipt(store: CoreStore, identity: str) -> dict:
    event = next(e for e in store.events() if e["operation_id"] == identity)
    payload = event["payload"]
    core = receipt(store, identity) if identity in store.submissions() else None
    target = payload["request"].get("hypothesis_id")
    current = store.current().get(target)
    return {
        "submission_id": identity,
        "input": payload,
        "acceptance": event["acceptance"],
        "core": core,
        "latest_revision": current.revision_id if current else None,
        "publication": "not_confirmed",  # Only the release coordinator can confirm publishing.
    }


def input_fingerprint(value: dict) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True))


def recover_inputs(store: CoreStore, *, maximum: int = 100) -> dict:
    """Resume a crash between durable ingress and C submission/receipt creation."""
    submissions, receipts = store.submissions(), store.receipts()
    pending = []
    for event in store.events():
        payload = event["payload"]
        if payload.get("type") != "web_input" or event["acceptance"]["status"] != "accepted":
            continue
        identity = payload["submission_id"]
        received = receipts.get(identity)
        if (
            identity not in submissions
            or received is None
            or (payload["request"]["mode"] == "lines" and received.get("status") == "pending")
        ):
            pending.append(payload)
    recovered = 0
    for payload in pending[:maximum]:
        accept_input(
            store,
            WebRequest.model_validate(payload["request"]),
            author=payload["author"],
            source=payload["source"],
            synthetic=payload["synthetic"],
            now=datetime.fromisoformat(payload["received_at"]),
        )
        recovered += 1
    return {"recovered": recovered, "pending": len(pending) - recovered}

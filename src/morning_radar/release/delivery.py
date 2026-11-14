"""External delivery adapters. Unknown outcomes never imply success or safe retry."""

from __future__ import annotations

import hashlib
import time
from urllib.parse import quote
from uuid import uuid4

import httpx

from morning_radar.ai.usage import atomic_json
from morning_radar.notifications.wxpusher import WXPUSHER_ENDPOINT
from morning_radar.release.coordinator import read
from morning_radar.time_utils import utc_now


class Pages:
    def __init__(self, repository: str, token: str, public_url: str, *, transport=None):
        from morning_radar.tendencies.github_input import InboxConfig

        InboxConfig(repository=repository, authorized_logins=["validation"])
        self.api = "https://api.github.com/repos/" + repository
        from morning_radar.tendencies.web_input import safe_source_url

        self.public_url = safe_source_url(public_url).rstrip("/")
        self.client = httpx.Client(
            transport=transport,
            timeout=20,
            follow_redirects=False,
            headers={"User-Agent": "Morning-Radar/0.1"},
        )
        self.headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"}

    def create(self, artifact_id: int, release_id: str, oidc: str) -> dict:
        reply = self.client.post(
            self.api + "/pages/deployments",
            headers=self.headers,
            json={
                "artifact_id": artifact_id,
                "pages_build_version": release_id,
                "oidc_token": oidc,
                "environment": "github-pages",
            },
        )
        reply.raise_for_status()
        result = reply.json()
        if not isinstance(result.get("id"), (str, int)) or not result["id"]:
            raise ValueError("Missing deployment ID")
        return {"id": str(result["id"])}

    def status(self, identity: str) -> str:
        reply = self.client.get(
            self.api + "/pages/deployments/" + quote(identity, safe=""), headers=self.headers
        )
        reply.raise_for_status()
        return reply.json()["status"]

    def verify_public(self, manifest: dict) -> None:
        # No bearer token is ever forwarded to the public domain.
        for name, expected in manifest["files"].items():
            with self.client.stream(
                "GET",
                self.public_url + "/" + quote(name, safe="/"),
                params={"release_verify": manifest["release_id"]},
                headers={"Cache-Control": "no-cache"},
            ) as reply:
                reply.raise_for_status()
                hasher, size = hashlib.sha256(), 0
                for chunk in reply.iter_bytes():
                    size += len(chunk)
                    if size > 4_000_000:
                        raise ValueError("Public file exceeds verification bound")
                    hasher.update(chunk)
            if hasher.hexdigest() != expected:
                raise ValueError("Public content version mismatch")


def deploy(
    coordinator,
    identity: str,
    pages: Pages,
    *,
    artifact_id: int | None = None,
    oidc: str | None = None,
    polls: int = 12,
    delay: float = 5,
    reconcile_id: str | None = None,
    redeploy: bool = False,
) -> dict:
    manifest = coordinator.verify(identity)
    if manifest["synthetic"] and not coordinator.synthetic:
        raise ValueError("Synthetic deployment refused")
    context = coordinator.context(identity)
    state = context["deployment"]
    if redeploy and state["status"] == "confirmed":
        context.setdefault("deployment_history", []).append(dict(state))
        state = {"status": "not_started"}
        context["deployment"] = state
        coordinator.save(context, "explicit frozen-package redeployment")
    if state["status"] == "confirmed":
        return state
    if reconcile_id:
        # Operator supplies the actual Pages deployment ID after an unknown create response.
        state["id"] = reconcile_id
        coordinator.save(context, "deployment reconciliation identity")
    if state["status"] in {"not_started", "failed"} and not reconcile_id:
        if not artifact_id or not oidc:
            raise ValueError("New deployment requires artifact ID and OIDC")
        context["upload"] = {
            "status": "uploaded",
            "artifact_id": artifact_id,
            "package_hash": manifest["package_hash"],
        }
        coordinator.save(context, "artifact upload identity")
        state = {
            "status": "unknown",
            "artifact_id": artifact_id,
            "package_hash": manifest["package_hash"],
            "intent_id": uuid4().hex,
            "started_at": utc_now().isoformat(),
        }
        context["deployment"] = state
        coordinator.save(context, "deployment intent before external call")
        try:
            state.update(pages.create(artifact_id, identity, oidc))
            state["status"] = "pending"
        except (httpx.HTTPError, ValueError):
            coordinator.save(context, "deployment outcome unknown")
            return state
        coordinator.save(context, "deployment identity received")
    if not state.get("id"):
        return state  # unknown; cannot invent an identity or blindly redeploy
    for attempt in range(polls):
        try:
            status = pages.status(state["id"])
            if status == "succeed":
                pages.verify_public(manifest)
                state.update(status="confirmed", confirmed_at=utc_now().isoformat())
                project_intake_deployment(coordinator, manifest)
                coordinator.save(context, "verified public deployment")
                return state
            if status in {
                "deployment_failed",
                "deployment_content_failed",
                "deployment_cancelled",
                "failed",
                "cancelled",
            }:
                state["status"] = "failed"
                coordinator.save(context, "deployment failed")
                return state
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        if attempt + 1 < polls:
            time.sleep(delay)
    state["status"] = "unknown"
    coordinator.save(context, "deployment not yet verified")
    return state


class WxSender:
    def __init__(self, token: str, *, transport=None):
        self.token = token
        self.client = httpx.Client(
            transport=transport, timeout=20, headers={"User-Agent": "Morning-Radar/0.1"}
        )

    def send(self, uid: str, *, day: str, url: str) -> dict:
        reply = self.client.post(
            WXPUSHER_ENDPOINT,
            json={
                "appToken": self.token,
                "uids": [uid],
                "contentType": 1,
                "summary": "Morning Radar｜" + day,
                "content": "本期晨报与 Tendency 已发布：" + url,
            },
        )
        reply.raise_for_status()
        body = reply.json()
        # Only an explicit rejection is safe to retry.
        if body.get("success") is False and body.get("code") != 1000:
            return {"status": "failed"}
        records = [r for r in body.get("data", []) if r.get("uid") == uid]
        if len(records) == 1:
            record = records[0]
            if record.get("code") == 1000 and isinstance(record.get("sendRecordId"), int):
                return {"status": "sent", "send_record_id": record["sendRecordId"]}
            if record.get("code") not in {None, 1000}:
                return {"status": "failed"}
        return {"status": "unknown"}


def notify(
    coordinator,
    identity: str,
    *,
    sender: WxSender,
    recipients: list[str],
    public_url: str,
    authorization_id: str | None = None,
) -> dict:
    from morning_radar.tendencies.web_input import safe_source_url

    public_url = safe_source_url(public_url)
    manifest = coordinator.verify(identity)
    context = coordinator.context(identity)
    if context["deployment"]["status"] != "confirmed":
        raise ValueError("Notification requires confirmed exact deployment")
    if manifest["synthetic"] and not coordinator.synthetic:
        raise ValueError("Synthetic notification refused")
    if coordinator.previous_release() != identity:
        raise ValueError("A later release is active; do not notify an older homepage")
    day = context["business_date"]
    legacy = coordinator.root / "data/state/notifications.json"
    if authorization_id is None and legacy.exists() and read(legacy).get(day) == "sent":
        return {"status": "already_sent_legacy"}
    path = coordinator.directory / "notifications" / (day + ".json")
    if authorization_id is not None:
        from morning_radar.release.coordinator import valid_id

        path = (
            coordinator.directory / "resends" / (day + "-" + valid_id(authorization_id) + ".json")
        )
    state = (
        read(path)
        if path.exists()
        else {"business_date": day, "authorization_id": authorization_id, "recipients": {}}
    )
    for uid in dict.fromkeys(recipients):
        key = hashlib.sha256(uid.encode()).hexdigest()
        previous = state["recipients"].get(key, {})
        if previous.get("status") in {"sent", "unknown"}:
            continue
        intent = {"status": "unknown", "release_id": identity, "intent_id": uuid4().hex}
        state["recipients"][key] = intent
        atomic_json(path, state)
        coordinator.archive("notification intent before external call")
        try:
            result = sender.send(uid, day=day, url=public_url)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            result = {"status": "unknown"}
        intent.update(result)
        atomic_json(path, state)
        coordinator.archive("notification per-recipient result")
    return state


def oidc_token() -> str:
    import os

    with httpx.Client(timeout=20, headers={"User-Agent": "Morning-Radar/0.1"}) as client:
        reply = client.get(
            os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"],
            headers={"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]},
        )
        reply.raise_for_status()
        return reply.json()["value"]


def project_intake_deployment(coordinator, manifest: dict) -> None:
    """Update existing A inspection state only for the exact generated artifact."""
    from morning_radar.intake.ledger import ProcessingLedgerStore
    from morning_radar.intake.models import PublishStatus
    from morning_radar.intake.publish import PublishStore

    store = PublishStore(coordinator.root / "data/state/publish.json")
    ledger = ProcessingLedgerStore(coordinator.root / "data/intake/ledger.json")
    now = utc_now()
    for day, version in manifest.get("brief_artifact_hashes", {}).items():
        record = store.get(day)
        # An older package must not label a newer same-day generation as deployed.
        if record is not None and record.brief_hash == version:
            store.mark_deployed(day, now=now, brief_hash=version)
        for entry in list(ledger.ledger.entries.values()):
            if entry.brief_date == day and entry.brief_hash == version:
                ledger.update(
                    entry.input_id,
                    entry.content_version,
                    now=now,
                    publish=PublishStatus.DEPLOY_CONFIRMED,
                    deployed_at=now,
                )
    ledger.save()

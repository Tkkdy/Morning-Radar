"""Read-only GitHub inbox ingestion. No remote mutations, comments or model calls."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import yaml
from pydantic import Field

from morning_radar.ai.usage import atomic_json
from morning_radar.models.core import RadarModel
from morning_radar.tendencies.core_store import CoreStore, writer_lock
from morning_radar.tendencies.inputs import digest
from morning_radar.tendencies.web_input import WebRequest, accept_input

QUERY = """query($owner:String!,$name:String!,$after:String){
 repository(owner:$owner,name:$name){nameWithOwner
 issues(first:25,after:$after,orderBy:{field:CREATED_AT,direction:ASC}){
 pageInfo{endCursor hasNextPage} nodes{id number title body url createdAt lastEditedAt
 author{login} editor{login}}}}}"""


class InboxConfig(RadarModel):
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    authorized_logins: list[str] = Field(min_length=1, max_length=20)
    maximum_pages: int = Field(default=2, ge=1, le=10)
    issue_title_prefix: str = "[Tendency]"


def load_inbox(path: Path) -> InboxConfig:
    return InboxConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def parse_form(body: str, operation_id: str) -> WebRequest:
    labels = {
        "原始输入": "raw_text",
        "操作": "action",
        "拆分方式": "mode",
        "假设 ID": "hypothesis_id",
        "期望修订 ID": "expected_revision",
        "原始链接": "source_url",
        "适用范围": "scope",
    }
    sections = re.split(r"^### (.+)\r?$", body, flags=re.MULTILINE)
    fields = {}
    for label, value in zip(sections[1::2], sections[2::2], strict=True):
        if label not in labels or label in fields:
            raise ValueError("Unknown or duplicated form heading")
        fields[label] = value.strip()
    if "原始输入" not in fields:
        raise ValueError("Missing original input")
    values = {labels[k]: v for k, v in fields.items() if v and v != "_No response_"}
    values["action"] = {
        "新假设": "hypotheses",
        "我的观察": "personal_observation",
        "外部材料": "source_claim",
        "纠偏": "correction",
    }.get(values.get("action"), values.get("action", "hypotheses"))
    values["mode"] = {"自由文本": "free", "每行一个判断": "lines"}.get(
        values.get("mode"), values.get("mode", "free")
    )
    return WebRequest(operation_id=operation_id, **values)


def ingest_issue(store: CoreStore, issue: dict, config: InboxConfig, *, synthetic=False) -> dict:
    allowed = {name.casefold() for name in config.authorized_logins}
    author = (issue.get("author") or {}).get("login", "")
    editor = (issue.get("editor") or {}).get("login", "")
    if author.casefold() not in allowed or (
        issue.get("lastEditedAt") and editor.casefold() not in allowed
    ):
        return {"number": issue.get("number"), "status": "unauthorized"}
    if not issue.get("title", "").startswith(config.issue_title_prefix):
        return {"number": issue.get("number"), "status": "not_input"}
    # Trusted API supplies IDs/actors. Title, labels and body never grant authority.
    body = issue.get("body") or ""
    if len(body) > 38000:
        return {"number": issue["number"], "status": "invalid_input"}
    version = issue.get("lastEditedAt") or issue["createdAt"]
    operation = "github-" + digest(config.repository + issue["id"] + version)
    source = {
        "channel": "github",
        "repository": config.repository,
        "source_id": issue["id"],
        "number": issue["number"],
        "body": body,
        "fingerprint": digest(body),
        "created_at": issue["createdAt"],
        "source_version": version,
        "author": author,
        "editor": editor,
    }
    try:
        request = parse_form(body, operation)
    except ValueError:
        # Authenticated malformed originals are durable and recoverable too.
        store.append(
            {"type": "inbox_rejected", "source": source, "status": "invalid_input"},
            operation_id="rejected:" + operation + ":" + digest(body),
        )
        return {"number": issue["number"], "status": "invalid_input"}
    try:
        result = accept_input(
            store, request, author="github:" + author.casefold(), source=source, synthetic=synthetic
        )
    except ValueError:
        return {"number": issue["number"], "status": "operation_conflict"}
    return {
        "number": issue["number"],
        "status": result["acceptance"]["status"],
        "submission_id": result["submission_id"],
    }


def poll_github(
    store: CoreStore, config: InboxConfig, *, token: str, transport=None, synthetic=False
) -> dict:
    """Bounded round-robin full scan includes closed/edited issues; cursor survives restart.

    No event delivery dependency. A complete cycle revisits every issue; source-version
    operation IDs deduplicate replays. Edits become new inputs, never overwrite history.
    The source before its first observation is unknowable and is not reconstructed.
    """
    state_path = store.directory / "github-cursor.json"
    with writer_lock(store.directory / "github-poll.lock"):
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        after = state.get("after") if state.get("repository") == config.repository else None
        owner, name = config.repository.split("/")
        results = []
        with httpx.Client(
            transport=transport,
            timeout=20,
            follow_redirects=False,
            headers={
                "Authorization": "Bearer " + token,
                "User-Agent": "Morning-Radar/0.1",
                "Accept": "application/json",
            },
        ) as client:
            for _ in range(config.maximum_pages):
                response = None
                for attempt in range(2):
                    try:
                        # Stream enforces a download bound before JSON parsing.
                        with client.stream(
                            "POST",
                            "https://api.github.com/graphql",
                            json={
                                "query": QUERY,
                                "variables": {"owner": owner, "name": name, "after": after},
                            },
                        ) as reply:
                            reply.raise_for_status()
                            chunks, size = [], 0
                            for chunk in reply.iter_bytes():
                                size += len(chunk)
                                if size > 2_000_000:
                                    raise ValueError("Inbox response exceeds limit")
                                chunks.append(chunk)
                            response = json.loads(b"".join(chunks))
                        break
                    except (httpx.TimeoutException, httpx.NetworkError):
                        if attempt == 1:
                            raise
                if response is None or response.get("errors"):
                    raise ValueError("GitHub inbox unavailable")
                repository = response["data"]["repository"]
                if (
                    not repository
                    or repository["nameWithOwner"].casefold() != config.repository.casefold()
                ):
                    raise ValueError("Unexpected repository")
                page = repository["issues"]
                for issue in page["nodes"]:
                    results.append(ingest_issue(store, issue, config, synthetic=synthetic))
                after = page["pageInfo"]["endCursor"] if page["pageInfo"]["hasNextPage"] else None
                atomic_json(state_path, {"repository": config.repository, "after": after})
                if after is None:
                    break
        return {
            "results": results,
            "scan_complete": after is None,
            "next_scan_pending": after is not None,
        }

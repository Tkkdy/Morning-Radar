"""Single-writer append-only transactions; current view is derived, never overwritten."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from morning_radar.ai.usage import atomic_json
from morning_radar.tendencies.core_models import Revision


class StaleRevision(ValueError):
    pass


@contextmanager
def writer_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Persistent advisory lock file. OS releases the lock if the process dies.
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class CoreStore:
    def __init__(self, root: Path, *, after_commit=None):
        self.after_commit = after_commit
        self.root = root
        self.directory = root / "data/tendency-core/v2"

    def events(self) -> list[dict]:
        return [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((self.directory / "transactions").glob("*.json"))
        ]

    def revisions(self, *, through: datetime | None = None) -> list[Revision]:
        return [
            Revision.model_validate(row)
            for event in self.events()
            for row in event.get("revisions", [])
            if through is None or datetime.fromisoformat(row["recorded_at"]) <= through
        ]

    def current(self, *, through: datetime | None = None) -> dict[str, Revision]:
        return {r.hypothesis_id: r for r in self.revisions(through=through)}

    def append(self, event: dict, *, operation_id: str) -> dict:
        with writer_lock(self.directory / "writer.lock"):
            events = self.events()
            for previous in events:
                if previous["operation_id"] == operation_id:
                    if previous["payload"] != event:
                        raise ValueError("Conflicting idempotency key")
                    return previous
            current = self.current()
            acceptance = None
            if event.get("type") == "web_input":
                target = event["request"].get("hypothesis_id")
                previous = current.get(target)
                expected = event["request"].get("expected_revision")
                acceptance = {
                    "status": "conflict" if target and (
                        previous is None or previous.revision_id != expected
                    ) else "accepted",
                    "current_revision": previous.revision_id if previous else None,
                }
            revisions = [Revision.model_validate(r) for r in event.get("revisions", [])]
            seen = set()
            for revision in revisions:
                if revision.hypothesis_id in seen:
                    raise ValueError("Duplicate revision target")
                seen.add(revision.hypothesis_id)
                previous = current.get(revision.hypothesis_id)
                if previous is None:
                    from morning_radar.tendencies.core_validation import fingerprint

                    if any(
                        fingerprint(existing.decision.hypothesis.claim)
                        == fingerprint(revision.decision.hypothesis.claim)
                        for existing in current.values()
                    ):
                        raise StaleRevision("An equivalent proposition was already committed")
                if revision.previous_revision_id != (previous.revision_id if previous else None):
                    raise StaleRevision("Expected previous revision is no longer current")
                if previous and (
                    revision.recorded_at < previous.recorded_at
                    or revision.first_recorded_at != previous.first_recorded_at
                ):
                    raise StaleRevision("Revision time or first-recorded identity changed")
                if previous and (
                    revision.parent_hypothesis_id != previous.parent_hypothesis_id
                    or revision.parent_revision_id != previous.parent_revision_id
                ):
                    raise StaleRevision("Branch ancestry cannot be rewritten")
                if revision.parent_hypothesis_id and not previous:
                    parent = current.get(revision.parent_hypothesis_id)
                    if not parent or parent.revision_id != revision.parent_revision_id:
                        raise StaleRevision("Branch parent changed")
            record = {
                "acceptance": acceptance,
                "schema_version": 2,
                "sequence": len(events) + 1,
                "operation_id": operation_id,
                "payload": event,
                "revisions": event.get("revisions", []),
            }
            # Unique destination and atomic replace: orphan .tmp files are never read.
            path = self.directory / "transactions" / f"{len(events) + 1:09d}-{uuid4().hex}.json"
            atomic_json(path, record)
            if self.after_commit:
                self.after_commit("C immutable transaction")
            return record

    def submissions(self) -> dict[str, dict]:
        return {
            e["payload"]["submission_id"]: e["payload"]
            for e in self.events()
            if e["payload"].get("type") == "submission"
        }

    def receipts(self) -> dict[str, dict]:
        return {
            e["payload"]["submission_id"]: e["payload"]
            for e in self.events()
            if e["payload"].get("type") == "receipt"
        }

    def seeds(self) -> list[dict]:
        return [
            item
            for receipt in self.receipts().values()
            for item in receipt["items"]
            if item["status"] == "accepted"
        ]

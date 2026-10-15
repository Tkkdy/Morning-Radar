"""Single-writer append-only transactions; current view is derived, never overwritten."""

from __future__ import annotations

import hashlib
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

    def draft_revisions(self, *, through: datetime | None = None) -> list[Revision]:
        """Structurally captured candidates remain readable regardless of model verdict."""
        revisions = {}
        for event in self.events():
            payload = event["payload"]
            rows = event.get("revisions", [])
            if payload.get("type") == "review_candidate":
                rows = [payload["revision"]]
            for row in rows:
                revision = Revision.model_validate(row)
                if through is None or revision.recorded_at <= through:
                    existing = revisions.get(revision.revision_id)
                    if existing is not None and existing != revision:
                        raise ValueError("Revision identity changed")
                    revisions[revision.revision_id] = revision
        return list(revisions.values())

    def current(self, *, through: datetime | None = None) -> dict[str, Revision]:
        return {r.hypothesis_id: r for r in self.draft_revisions(through=through)}

    @staticmethod
    def confirmation_binding(revision: Revision) -> dict:
        from morning_radar.tendencies.grounded_review import sha
        return {
            "revision_id": revision.revision_id,
            "body_sha256": sha(revision.decision.model_dump(mode="json")),
            "evidence_sha256": sha([e.model_dump(mode="json") for e in revision.evidence]),
        }

    def human_decisions(self) -> dict[str, dict]:
        return {e["payload"]["revision_id"]: e["payload"] for e in self.events()
                if e["payload"].get("type") == "human_confirmation"}

    @staticmethod
    def event_digest(event: dict) -> str:
        return hashlib.sha256(json.dumps(event, sort_keys=True,
                            ensure_ascii=False).encode()).hexdigest()

    @classmethod
    def prefix_checkpoint(cls, events: list[dict]) -> dict:
        if any(e.get("sequence") != n for n, e in enumerate(events, 1)):
            raise ValueError("Private transaction sequence is incomplete")
        return {"format": "event-prefix-v1", "event_count": len(events),
                "sha256": cls.event_digest({"events": events})}

    def private_state_available(self) -> bool:
        path = self.root / "data/tendency-public/v1/confirmed.json"
        events = self.events()
        if any(e.get("sequence") != n for n, e in enumerate(events, 1)):
            return False
        if not path.exists():
            return True
        projection = json.loads(path.read_text(encoding="utf-8"))
        if not projection.get("private_state_requires_restore"):
            return True
        anchor = projection.get("private_state_checkpoint")
        # Legacy last-event hashes cannot prove that the history prefix was restored.
        if not isinstance(anchor, dict) or anchor.get("format") != "event-prefix-v1":
            return False
        count = anchor.get("event_count")
        if type(count) is not int or count < 1 or count > len(events):
            return False
        return self.prefix_checkpoint(events[:count]) == anchor

    def public_revisions(self) -> list[Revision]:
        decisions = self.human_decisions()
        # Public-only checkouts can rebuild confirmed pages without private events.
        # They cannot recover or resume the private workbench state.
        projection = self.root / "data/tendency-public/v1/confirmed.json"
        archived = []
        if projection.exists() and (not self.private_state_available()
                                    or not any((self.directory / "transactions").glob("*.json"))):
            value = json.loads(projection.read_text(encoding="utf-8"))
            bindings = {r["revision_id"]: r for r in value["confirmations"]}
            for row in value["history"]:
                revision = Revision.model_validate(row)
                binding = bindings.get(revision.revision_id, {})
                if any(binding.get(k) != v for k, v in self.confirmation_binding(revision).items()):
                    raise ValueError("Public projection confirmation mismatch")
                through = getattr(self, "through", None)
                if through is not None and (not binding.get("confirmed_at") or
                        datetime.fromisoformat(binding["confirmed_at"]) > through
                        or revision.recorded_at > through):
                    continue
                archived.append(revision)
            if not self.private_state_available():
                return archived  # Incomplete local state cannot override the public snapshot.
        drafts = {r.revision_id: r for r in archived}
        drafts.update({r.revision_id: r for r in self.draft_revisions()})
        archived_ids = {r.revision_id for r in archived}
        return [r for r in drafts.values()
                if (r.revision_id in archived_ids and r.revision_id not in decisions)
                or ((decision := decisions.get(r.revision_id))
                and decision["action"] == "confirm"
                and all(decision.get(k) == v for k, v in self.confirmation_binding(r).items()))]

    def public_current(self) -> dict[str, Revision]:
        return {r.hypothesis_id: r for r in self.public_revisions()}

    def append(self, event: dict, *, operation_id: str) -> dict:
        with writer_lock(self.directory / "writer.lock"):
            events = self.events()
            for previous in events:
                if previous["operation_id"] == operation_id:
                    if previous["payload"] != event:
                        raise ValueError("Conflicting idempotency key")
                    return previous
            current = self.current()
            if event.get("type") == "human_confirmation":
                revision = next((r for r in current.values()
                                 if r.revision_id == event.get("revision_id")), None)
                if revision is None or any(event.get(k) != v for k, v in
                                           self.confirmation_binding(revision).items()):
                    raise StaleRevision("Draft or evidence changed; read the latest version")
                if event.get("action") not in {"confirm", "return"}:
                    raise ValueError("Unknown human decision")
                if not event.get("author") or event.get("revisions"):
                    raise ValueError("Confirmation cannot replace content")
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
            if event.get("type") == "review_candidate":
                revisions = [Revision.model_validate(event["revision"])]
                from morning_radar.tendencies.grounded_review import candidate
                from morning_radar.tendencies.web_input import safe_source_url
                refs = {e.evidence_id: e for e in revisions[0].evidence}
                for evidence in refs.values():
                    if evidence.source_url:
                        safe_source_url(evidence.source_url)
                for citation in revisions[0].decision.citations:
                    evidence = refs.get(citation.evidence_id)
                    if evidence is None or not any(
                        citation.quote in segment for segment in evidence.excerpt.split("\n[…]\n")
                    ):
                        raise ValueError("Invalid draft evidence quote")
                previous = current.get(revisions[0].hypothesis_id)
                if event["request"] != candidate(revisions[0], previous,
                                                  event["request"]["analysis_version"]):
                    raise ValueError("Candidate content/evidence binding mismatch")
            seen = set()
            for revision in revisions:
                if event.get("type") == "content_review":
                    from morning_radar.tendencies.grounded_review import validate_admission
                    validate_admission(event, revision)
                    candidates = [e["payload"] for e in events
                                  if e["payload"].get("type") == "review_candidate"
                                  and e["payload"]["request"] == event["request"]
                                  and e["payload"]["revision"] == revision.model_dump(mode="json")]
                    if not candidates:
                        raise ValueError("Reviewed candidate was not durably captured")
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
                if event.get("type") == "content_review" and previous == revision:
                    continue  # Auxiliary opinion on an already captured draft.
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

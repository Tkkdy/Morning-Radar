"""Read-only cutoff view over existing C transactions, never a second store."""

from __future__ import annotations

from datetime import datetime

from morning_radar.tendencies.core_store import CoreStore


class CutoffStore(CoreStore):
    def __init__(self, root, through):
        super().__init__(root)
        self.through = through

    def append(self, event, *, operation_id):
        raise TypeError("Frozen view is read-only")

    def events(self):
        events = super().events()
        submissions = {
            e["payload"]["submission_id"]
            for e in events
            if e["payload"].get("type") == "submission"
            and datetime.fromisoformat(e["payload"]["submitted_at"]) <= self.through
        }
        result = []
        for event in events:
            payload = event["payload"]
            if payload.get("type") == "receipt" and (payload["submission_id"] not in submissions):
                continue
            times = [
                payload[k] for k in ["received_at", "submitted_at", "recorded_at"] if payload.get(k)
            ]
            times += [r["recorded_at"] for r in event.get("revisions", [])]
            if any(datetime.fromisoformat(t.replace("Z", "+00:00")) > self.through for t in times):
                continue
            result.append(event)
        return result

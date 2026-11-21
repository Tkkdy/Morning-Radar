"""Narrow, loss-aware adaptation for metadata candidate review only."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import PrivateAttr, ValidationError, model_validator

from morning_radar.ai.models import ClassificationBatch, ClassifiedItem


class CandidateClassificationBatch(ClassificationBatch):
    _candidate_diagnostics: list[dict[str, Any]] = PrivateAttr(default_factory=list)

    @model_validator(mode="wrap")
    @classmethod
    def isolate_candidate_items(cls, value: Any, handler: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("items"), list):
            return handler(value)
        rows = value["items"]
        counts = Counter(
            row["item_id"] for row in rows
            if isinstance(row, dict) and isinstance(row.get("item_id"), str)
        )
        valid = []
        diagnostics = []
        for index, row in enumerate(rows):
            item_id = row.get("item_id") if isinstance(row, dict) else None
            note = {"index": index, "item_id": item_id if isinstance(item_id, str) else None}
            if isinstance(item_id, str) and counts[item_id] > 1:
                diagnostics.append({**note, "reason": "duplicate_id"})
                continue
            candidate = dict(row) if isinstance(row, dict) else row
            ignored_title = isinstance(candidate, dict) and isinstance(candidate.get("title"), str)
            if ignored_title:
                candidate.pop("title")
            try:
                item = ClassifiedItem.model_validate(candidate, strict=True)
            except ValidationError as exc:
                diagnostics.append({**note, "reason": "invalid_item", "errors": [
                    {"location": list(error["loc"]), "type": error["type"]}
                    for error in exc.errors()
                ]})
                continue
            valid.append(item)
            if ignored_title:
                diagnostics.append({**note, "reason": "echo_title_ignored"})
        # Envelope extras still fail; the original object and response are never mutated.
        result = handler({**value, "items": valid})
        result._candidate_diagnostics = diagnostics
        return result


def validate_candidate_ids(
    output: ClassificationBatch, allowed_ids: set[str],
) -> ClassificationBatch:
    """Discard unknown/ambiguous IDs without discarding unrelated valid decisions."""
    counts = Counter(item.item_id for item in output.items)
    valid = []
    diagnostics = list(getattr(output, "_candidate_diagnostics", []))
    for item in output.items:
        reason = "unknown_id" if item.item_id not in allowed_ids else (
            "duplicate_id" if counts[item.item_id] > 1 else None
        )
        if reason:
            diagnostics.append({"item_id": item.item_id, "reason": reason})
        else:
            valid.append(item)
    result = CandidateClassificationBatch(items=valid)
    result._candidate_diagnostics = diagnostics
    return result

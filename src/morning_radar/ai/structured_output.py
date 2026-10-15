"""Separate undeclared annotations from business JSON, without repairing business values."""
from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from typing import Any

from pydantic import BaseModel, ValidationError

from morning_radar.ai.usage import atomic_json

# Mutable compatibility attributes remain observable, but never identify an
# in-flight audit: concurrent writing and continuity calls need separate records.
_CURRENT_AUDIT: ContextVar[tuple[Any, dict] | None] = ContextVar("output_audit", default=None)


def _active_audit(provider):
    current = _CURRENT_AUDIT.get()
    return current[1] if current is not None and current[0] is provider else None


def separate_annotations(
    value: Any, schema: dict, *, allowed_paths: frozenset[str] = frozenset(),
) -> tuple[Any, list[dict]]:
    """Project only unambiguous schema objects; never fill, coerce or resolve unions."""
    annotations: list[dict] = []

    def resolve(rule):
        if "$ref" not in rule:
            return rule
        pointer = rule["$ref"]
        if not pointer.startswith("#/"):
            return rule
        target = schema
        for part in pointer[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return target

    def kind_matches(node, rule):
        kind = resolve(rule).get("type")
        return ((kind == "object" and isinstance(node, dict))
                or (kind == "array" and isinstance(node, list))
                or (kind == "null" and node is None)
                or (kind == "string" and isinstance(node, str))
                or (kind == "boolean" and isinstance(node, bool))
                or (kind in {"integer", "number"} and type(node) in {int, float}))

    def walk(node, rule, path):
        rule = resolve(rule)
        if "anyOf" in rule or "oneOf" in rule:
            choices = [r for r in rule.get("anyOf", rule.get("oneOf", []))
                       if kind_matches(node, r)]
            return walk(node, choices[0], path) if len(choices) == 1 else node
        if isinstance(node, list) and isinstance(rule.get("items"), dict):
            return [walk(v, rule["items"], f"{path}/{i}") for i, v in enumerate(node)]
        if not isinstance(node, dict):
            return node
        properties = rule.get("properties", {})
        extra = rule.get("additionalProperties", True)
        result = {}
        for key, child in node.items():
            child_path = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key in properties:
                result[key] = walk(child, properties[key], child_path)
            elif extra is False and child_path in allowed_paths:
                annotations.append({"path": child_path, "value": child})
            else:
                result[key] = walk(child, extra, child_path) if isinstance(extra, dict) else child
        return result

    return walk(value, schema, ""), annotations


def _save_audit(provider, record: dict) -> None:
    recorder = getattr(provider, "usage_recorder", None)
    call_id = getattr(recorder, "last_call_id", None)
    record["physical_call_id"] = call_id
    if recorder is not None and call_id:
        data = recorder.directory.parent.parent
        path = data / "ai-responses" / recorder.business_date / (call_id + ".json")
        record["audit_path"] = str(path)
        atomic_json(path, record)
        # Raw output may contain private research. Only a content-free receipt
        # joins the existing health archive/artifact; never publish the original.
        receipt = {key: record.get(key) for key in (
            "schema_version", "task", "schema", "physical_call_id", "raw_sha256",
            "capture_kind", "finish_reason", "state", "error_type", "json_valid",
            "business_values_changed",
        )}
        receipt.update(
            raw_retention="local_private_only",
            annotation_count=len(record.get("annotations", [])),
            rejected_field_count=len(record.get("rejected_fields", [])),
            semantic_verification="not_performed",
        )
        atomic_json(data / "health/ai-output" / recorder.business_date / (call_id + ".json"),
                    receipt)
    provider.last_output_adaptation = record


def retain_response(
    provider, schema: type[BaseModel], raw: str, *, task: str,
    finish_reason: str = "unknown", capture_kind: str = "message_content",
) -> dict:
    """Save returned text before finish-reason or schema rejection; no request/headers."""
    record = {
        "schema_version": 2, "task": task, "schema": schema.__name__,
        "raw_response": raw, "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "business_values_changed": False, "annotations": [], "rejected_fields": [],
        "json_valid": None, "finish_reason": finish_reason,
        "capture_kind": capture_kind, "state": "received",
    }
    _save_audit(provider, record)
    _CURRENT_AUDIT.set((provider, record))
    if getattr(provider, "output_adaptations", None) is None:
        provider.output_adaptations = []
    provider.output_adaptations.append(record)
    return record


def finish_output_audit(provider, *, task: str, state: str,
                        error_type: str | None = None) -> None:
    record = _active_audit(provider)
    recorder = getattr(provider, "usage_recorder", None)
    if (record and record["task"] == task
            and record.get("physical_call_id") == getattr(recorder, "last_call_id", None)):
        record.update(state=state, error_type=error_type)
        _save_audit(provider, record)


def prepare_structured_output(provider, schema: type[BaseModel], raw: str, *, task: str):
    """Only the two observed score annotations are compatible; all other contracts stay strict."""
    from morning_radar.ai.models import StoryScore

    record = _active_audit(provider)
    if not (record and record["state"] == "received" and record["task"] == task
            and record["raw_response"] == raw):
        record = retain_response(provider, schema, raw, task=task)
    try:
        parsed = json.loads(raw)
        allowed = frozenset()
        if task == "score_story" and schema is StoryScore and isinstance(parsed, dict):
            allowed = frozenset(
                "/" + key for key, kind in {"source_checks": dict, "_note": str}.items()
                if isinstance(parsed.get(key), kind)
            )
        projected, annotations = separate_annotations(
            parsed, schema.model_json_schema(), allowed_paths=allowed,
        )
        projected, rejected = isolate_optional_fields(projected, task=task)
        record.update(json_valid=True, annotations=annotations, state="parsed",
                      rejected_fields=rejected, business_values_changed=bool(rejected))
        return projected
    except (ValueError, TypeError):
        record.update(state="invalid_json", json_valid=False)
        raise
    finally:
        _save_audit(provider, record)


def isolate_optional_fields(value: Any, *, task: str) -> tuple[Any, list[dict]]:
    """Omit invalid optional fields/items, never truncate quotes or repair links."""
    from copy import deepcopy

    from morning_radar.editorial.models import EditorialDecision
    from morning_radar.models.core import GroundedPoint, TechnicalReading
    result = deepcopy(value)
    rejected = []
    if not isinstance(result, dict):
        return result, rejected
    def reject(path, raw, exc):
        rejected.append({"path": path, "value": raw, "reason": str(exc)})
    if task == "merge_story" and isinstance(result.get("technical_reading"), dict):
        reading = result["technical_reading"]
        for name in TechnicalReading.model_fields:
            if name == "kind" or reading.get(name) is None:
                continue
            try:
                GroundedPoint.model_validate(reading[name])
            except ValidationError as exc:
                reject("technical_reading." + name, reading[name], exc)
                reading[name] = None
        try:
            TechnicalReading.model_validate(reading)
        except ValidationError as exc:
            reject("technical_reading", reading, exc)
            result["technical_reading"] = None
    if task == "evaluate_editorial" and isinstance(result.get("decisions"), list):
        accepted = []
        for index, row in enumerate(result["decisions"]):
            try:
                EditorialDecision.model_validate(row)
            except ValidationError as exc:
                reject(f"decisions.{index}", row, exc)
            else:
                accepted.append(row)
        result["decisions"] = accepted
    return result, rejected

"""Merge fresh inputs with recovered work under a reserved candidate quota."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from morning_radar.intake.identity import intake_key
from morning_radar.intake.models import IntakeRecord
from morning_radar.models import RawItem
from morning_radar.processing.story_builder import preselect_ai_candidates


@dataclass(slots=True)
class ProcessSelection:
    items: list[RawItem]
    records: list[IntakeRecord]
    recovery_items: list[RawItem]
    fresh_items: list[RawItem]
    deferred: list[IntakeRecord] = field(default_factory=list)
    selected_keys: set[str] = field(default_factory=set)
    protected_fresh_keys: set[str] = field(default_factory=set)
    candidate_policy_hash: str | None = None
    candidate_matches: dict[str, dict[str, str | None]] = field(default_factory=dict)


def select_process_candidates(
    *,
    fresh: list[IntakeRecord],
    recovery: list[IntakeRecord],
    maximum_items: int,
    reserved_recovery_slots: int,
    reserved_fresh_slots: int = 0,
    labs: list[object] | None = None,
    update_rules: list[object] | None = None,
) -> ProcessSelection:
    maximum_items = max(0, maximum_items)
    reserved = min(max(0, reserved_recovery_slots), len(recovery), maximum_items)
    recovery_reserved = recovery[:reserved]
    leftover_recovery = recovery[reserved:]
    remaining_slots = maximum_items - reserved
    fresh_items = [record.item for record in fresh]
    policy_hash = _candidate_policy_hash(labs or [], update_rules or [], reserved_fresh_slots)
    hints = [
        record for record in fresh if _is_update_hint(record.item, labs or [], update_rules or [])
    ]
    candidate_matches = {
        intake_key(record.input_id, record.content_version): {
            "lab_id": _lab_id(record.item, labs or []),
            "rule_id": _matching_rule_id(record.item, update_rules or []),
        }
        for record in fresh
    }
    protected: list[IntakeRecord] = []
    seen_labs: set[str] = set()
    for record in sorted(
        hints,
        key=lambda value: (
            value.item.source_role.value != "official_primary",
            -(value.item.published_at or value.item.fetched_at).timestamp(),
            value.item.id,
        ),
    ):
        lab = _lab_id(record.item, labs or [])
        if (
            lab
            and lab not in seen_labs
            and len(protected) < min(reserved_fresh_slots, remaining_slots)
        ):
            protected.append(record)
            seen_labs.add(lab)
    protected_keys = {intake_key(record.input_id, record.content_version) for record in protected}
    remaining_slots -= len(protected)
    selectable_fresh = [
        record.item
        for record in fresh
        if intake_key(record.input_id, record.content_version) not in protected_keys
    ]
    fresh_selected_items = preselect_ai_candidates(
        selectable_fresh,
        maximum_items=remaining_slots,
    )
    selected_fresh_ids = {item.id for item in fresh_selected_items}
    unused_fresh_slots = max(0, remaining_slots - len(fresh_selected_items))
    extra_recovery = leftover_recovery[:unused_fresh_slots]
    selected_fresh = [
        record
        for record in fresh
        if record.item.id in selected_fresh_ids
        and intake_key(record.input_id, record.content_version) not in protected_keys
    ]
    selected_records = [*recovery_reserved, *extra_recovery, *protected, *selected_fresh]
    selected_keys = {
        intake_key(record.input_id, record.content_version) for record in selected_records
    }
    deferred = [
        record
        for record in [*recovery, *fresh]
        if intake_key(record.input_id, record.content_version) not in selected_keys
    ]
    return ProcessSelection(
        items=[record.item for record in selected_records],
        records=selected_records,
        recovery_items=[record.item for record in recovery],
        fresh_items=fresh_items,
        deferred=deferred,
        selected_keys=selected_keys,
        protected_fresh_keys=protected_keys,
        candidate_policy_hash=policy_hash,
        candidate_matches=candidate_matches,
    )


_UPDATE_HINT = re.compile(
    r"\b(release|released|preview|launch|availability|api|migration|deprecat|pricing)\b"
    r"|发布|预告|上线|迁移|价格",
    re.I,
)
_UPDATE_EXCLUSIONS = re.compile(r"\b(sdk|typo|cache|benchmark|ipo|stock)\b", re.I)


def _lab_id(item: RawItem, labs: list[object]) -> str:
    direct = str(item.metadata.get("lab_id") or "")
    if direct:
        return direct
    source_id = str(item.metadata.get("source_id") or "")
    text = f"{item.title} {item.summary}".casefold()
    for lab in labs:
        if source_id and source_id == getattr(lab, "official_source_id", None):
            return str(lab.id)
        aliases = getattr(lab, "aliases", [])
        if any(len(alias) >= 4 and alias.casefold() in text for alias in aliases):
            return str(lab.id)
    return ""


def _is_update_hint(item: RawItem, labs: list[object], rules: list[object]) -> bool:
    text = f"{item.title} {item.summary}"
    if not _lab_id(item, labs):
        return False
    if rules:
        return any(
            any(re.search(pattern, text, re.I) for pattern in rule.include)
            and not any(re.search(pattern, text, re.I) for pattern in rule.exclude)
            for rule in rules
        )
    return bool(_UPDATE_HINT.search(text)) and not bool(_UPDATE_EXCLUSIONS.search(text))


def _matching_rule_id(item: RawItem, rules: list[object]) -> str | None:
    text = f"{item.title} {item.summary}"
    for rule in rules:
        if any(re.search(pattern, text, re.I) for pattern in rule.include) and not any(
            re.search(pattern, text, re.I) for pattern in rule.exclude
        ):
            return rule.rule_id
    return None


def _candidate_policy_hash(labs: list[object], rules: list[object], reserved_slots: int) -> str:
    payload = {
        "version": "p2b-candidate-v1", "reserved_slots": reserved_slots,
        "update_hint": _UPDATE_HINT.pattern, "exclusions": _UPDATE_EXCLUSIONS.pattern,
        "labs": [
            {
                "id": lab.id,
                "aliases": list(getattr(lab, "aliases", [])),
                "official_source_id": getattr(lab, "official_source_id", None),
            }
            for lab in labs
        ],
        "rules": [
            {"id": rule.rule_id, "include": rule.include, "exclude": rule.exclude}
            for rule in rules
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:20]


def version_observation_key(record: IntakeRecord) -> tuple:
    """Order content versions by durable observation time, not publish time or hash."""
    return (
        record.durable_at or record.first_seen_at,
        record.fetched_at,
        record.batch_id,
        record.content_version,
    )


def choose_latest_content_versions(records: list[IntakeRecord]) -> list[IntakeRecord]:
    """Keep one actionable version per input_id: the latest observed content."""
    grouped: dict[str, list[IntakeRecord]] = {}
    for record in records:
        grouped.setdefault(record.input_id, []).append(record)
    chosen: list[IntakeRecord] = []
    for versions in grouped.values():
        versions.sort(key=version_observation_key)
        chosen.append(versions[-1])
    return chosen


def split_latest_versions(
    fresh: list[IntakeRecord],
    recovery: list[IntakeRecord],
) -> tuple[list[IntakeRecord], list[IntakeRecord]]:
    latest_keys = {
        intake_key(record.input_id, record.content_version)
        for record in choose_latest_content_versions([*recovery, *fresh])
    }
    fresh_latest = [
        record
        for record in fresh
        if intake_key(record.input_id, record.content_version) in latest_keys
    ]
    recovery_latest = [
        record
        for record in recovery
        if intake_key(record.input_id, record.content_version) in latest_keys
    ]
    return fresh_latest, recovery_latest

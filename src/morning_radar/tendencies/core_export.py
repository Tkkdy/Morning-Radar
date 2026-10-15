"""Read-only current/history export; legacy records remain explicitly legacy."""

from __future__ import annotations

import html
import json
from pathlib import Path

from morning_radar.tendencies.core_store import CoreStore


def export_core(store: CoreStore, *, legacy_root: Path | None = None,
                private: bool = False) -> dict:
    revisions = store.draft_revisions() if private else store.public_revisions()
    current = store.current() if private else store.public_current()
    legacy = []
    if private and legacy_root:
        for path in sorted((legacy_root / "data/tendencies").glob("*.json")):
            legacy.append(
                {
                    "provenance": "legacy_v1_unmigrated",
                    "path": str(path),
                    "record": json.loads(path.read_text(encoding="utf-8")),
                }
            )
    return {
        "schema_version": 2,
        "history_scope": "private drafts" if private else "human-confirmed revisions only",
        "public_revision_ids": [r.revision_id for r in store.public_revisions()],
        "current": [r.model_dump(mode="json") for r in current.values()],
        "history": [r.model_dump(mode="json") for r in revisions],
        "submissions": list(store.submissions().values()) if private else [],
        "receipts": list(store.receipts().values()) if private else [],
        "legacy": legacy,
    }


def render_markdown(payload: dict) -> str:
    def safe(value):
        return html.escape(str(value))

    lines = ["# Tendency 当前判断与独立历史", "", "本地核心导出；不是正式发布或 D 批编辑器。", ""]
    for section in ["current", "history"]:
        lines += ["## " + ("当前视图" if section == "current" else "不可变修订历史"), ""]
        for revision in payload[section]:
            decision, hypothesis = revision["decision"], revision["decision"]["hypothesis"]
            lines += [
                "### " + safe(hypothesis["claim"]),
                "",
                f"身份：{revision['hypothesis_id']}；修订：{revision['revision_id']}",
                f"首次记录：{revision['first_recorded_at']}；本次：{revision['recorded_at']}",
                f"前一版：{revision['previous_revision_id']}；父方向：{revision['parent_hypothesis_id']}",
                f"来源：{revision['origin']}；synthetic={revision['synthetic']}；状态：{revision['standing']}",
                "内容准入：" + ("用户确认（非事实认证）"
                            if revision["revision_id"] in payload["public_revision_ids"]
                            else "未审查，不可作为公开当前方向"),
                "范围：" + safe("、".join(hypothesis["scope"]))
                + ("（待验证外推）" + safe(hypothesis.get("scope_reason", ""))
                   if hypothesis.get("scope_basis") == "inferred" else ""),
                "迁移：" + safe(hypothesis["from_state"] + " → " + hypothesis["to_state"]),
                "机制（系统推断）：" + safe(hypothesis["mechanism"]),
                "",
            ]
            for baseline in hypothesis["baseline"]:
                lines += [
                    "- 原有限制"
                    + ("（待验证假定）" if baseline["assumption"] else "（有来源）")
                    + "："
                    + safe(baseline["text"])
                ]
            for condition in hypothesis["conditions"]:
                lines += [
                    "- 观察：" + safe(condition["observation"]),
                    "  推断：" + safe(condition["interpretation"]),
                ]
            lines += [
                "",
                "缺口：" + safe("；".join(hypothesis["unresolved"])),
                "其他解释：" + safe("；".join(hypothesis["alternatives"])),
            ]
            for test in hypothesis["tests"]:
                lines += [
                    "- 改变判断的观察：" + safe(test["observe"]),
                    "  若看到：" + safe(test["if_seen"]),
                    "  若确实完成观察而未看到：" + safe(test["if_absent"]),
                ]
            lines += [
                "",
                f"本次：{decision['kind']} / {decision['basis']}",
                "原因：" + safe(decision["reason"]),
                "放弃部分：" + safe("；".join(decision["relinquished"])),
                "",
            ]
            evidence = {e["evidence_id"]: e for e in revision["evidence"]}
            for ref in decision["citations"]:
                source = evidence[ref["evidence_id"]]
                lines += [
                    f"- {ref['role']} / {safe(source['source_kind'])} / {ref['evidence_id']}",
                    "  引文：" + safe(ref["quote"]),
                    "  来源：" + safe(source["source_url"] or source["provenance"]),
                ]
            lines.append("")
    lines += ["## 旧格式历史（只读，未升级成熟度）", ""]
    for record in payload["legacy"]:
        lines.append("- legacy_v1_unmigrated：" + safe(record["path"]))
    return "\n".join(lines)


def save_public_projection(store: CoreStore) -> Path:
    """Publish only confirmed revision content, never transactions or private notes."""
    from morning_radar.ai.usage import atomic_json
    path = store.root / "data/tendency-public/v1/confirmed.json"
    if not store.private_state_available():
        if path.exists():
            return path  # Preserve the last complete projection byte for byte.
        raise ValueError("Private transaction sequence is incomplete")
    revisions = store.public_revisions()
    events = store.events()
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    old_times = {c["revision_id"]: c.get("confirmed_at")
                 for c in previous.get("confirmations", [])}
    decisions = store.human_decisions()
    atomic_json(path, {
        "schema_version": 1,
        "private_state_requires_restore": bool(events)
            or previous.get("private_state_requires_restore", False),
        "private_state_checkpoint": (store.prefix_checkpoint(events)
            if events
            else previous.get("private_state_checkpoint")),
        "history": [r.model_dump(mode="json") for r in revisions],
        "confirmations": [{**store.confirmation_binding(r),
            "confirmed_at": decisions.get(r.revision_id, {}).get("recorded_at")
                or old_times.get(r.revision_id)} for r in revisions],
    })
    return path

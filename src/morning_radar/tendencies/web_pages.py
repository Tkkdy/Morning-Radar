"""Escaped static views over C's immutable records; no public diagnostic dump."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from urllib.parse import urlencode

from jinja2 import Environment, FileSystemLoader, select_autoescape

from morning_radar.tendencies.core_store import CoreStore
from morning_radar.tendencies.inputs import digest
from morning_radar.tendencies.web_input import input_receipt, safe_source_url
from morning_radar.time_utils import utc_now

LABELS = {
    "early": "早期假设 Early",
    "active": "活跃",
    "archived": "已归档",
    "overturned": "已推翻",
    "supported": "得到支持",
    "strengthened": "增强",
    "weakened": "削弱",
    "narrowed": "收窄",
    "split": "分支",
    "user_correction": "用户纠偏",
    "analysis_reassessment": "分析重新评估",
    "external_evidence": "外部证据更新",
    "system": "系统",
    "user": "用户",
    "no_changes": "已检查，无实质变化",
    "no_input": "没有可处理输入",
    "insufficient_evidence": "依据不足",
    "completed": "处理完成",
    "partial": "部分完成，仍有缺口",
    "provider_or_validation_failed": "处理失败，原文保留",
    "validation_failed": "校验未通过",
    "budget_skipped": "额度不足，待处理",
    "running": "处理尚未完成",
    "failed": "处理失败",
    "cancelled": "处理未完成",
    "pending": "等待拆分",
    "processed": "拆分完成，非判断采纳",
    "accepted": "C 已持久接收",
    "conflict": "版本冲突，原文已保留",
    "unassessed": "尚未形成判断",
    "recorded": "已形成修订",
    "invalid": "该项拆分失败",
    "source_claim": "来源声称",
    "original_fact": "原始事实",
    "personal_observation": "个人观察",
    "user_hypothesis": "用户观点",
    "system_inference": "系统推断",
    "support": "支持",
    "counter": "反证",
    "boundary": "适用边界 / 替代方案",
    "observation": "直接观察",
    "context": "背景",
}


def page_name(kind: str, identity: str) -> str:
    return kind + "-" + digest(identity) + ".html"


def source_link(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return safe_source_url(value)
    except ValueError:
        return None


def public_runs(store: CoreStore) -> tuple[list[dict], bool]:
    runs, failed = [], False
    for path in (store.directory / "runs").glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            stages = {
                key: {
                    "status": value.get("status", "failed"),
                    "target_ids": value.get("target_ids", []),
                    "unaddressed_targets": value.get("unaddressed_targets", []),
                    "submission_id": value.get("submission_id"),
                }
                for key, value in raw.get("stages", {}).items()
            }
            runs.append(
                {
                    "recorded_at": raw["recorded_at"],
                    "run_id": raw["run_id"],
                    "status": raw["status"],
                    "stages": stages,
                }
            )
        except (ValueError, KeyError, OSError):
            failed = True
    return sorted(runs, key=lambda r: r["recorded_at"]), failed


def build_pages(
    store: CoreStore,
    output: Path,
    *,
    repository: Path,
    local_write: bool = False,
    github_repository: str | None = None,
    synthetic: bool = False,
) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    assets = output / "assets"
    assets.mkdir(exist_ok=True)
    for name in ["tendency.css", "tendency.js"]:
        shutil.copyfile(repository / "templates/tendency" / name, assets / name)
    environment = Environment(
        loader=FileSystemLoader(repository / "templates/tendency"),
        autoescape=select_autoescape(default=True),
    )
    environment.filters["label"] = lambda x: LABELS.get(x, "待处理 / 未知状态")
    environment.globals.update(page=page_name, source_link=source_link)
    template = environment.get_template("page.html.j2")
    try:
        events, current = store.events(), store.current()
        history = store.revisions()
        runs, run_error = public_runs(store)
        if getattr(store, "through", None):
            from datetime import datetime
            runs = [r for r in runs if datetime.fromisoformat(r["recorded_at"]) <= store.through]
        receipts = [
            input_receipt(store, e["operation_id"])
            for e in events
            if e["payload"].get("type") == "web_input"
        ]
        reading_error = False
    except (ValueError, KeyError, OSError):
        events, current, history, runs, receipts = [], {}, [], [], []
        reading_error, run_error = True, True
    version = digest(json.dumps([events, runs], sort_keys=True, ensure_ascii=False))
    common = dict(
        current=current,
        history=history,
        receipts=receipts,
        runs=runs,
        built_at=utc_now().isoformat(),
        version=version,
        local_write=local_write,
        synthetic=synthetic
        or any(r.synthetic for r in history)
        or any(e["payload"].get("synthetic") for e in events),
        reading_error=reading_error,
        run_error=run_error,
        github_repository=github_repository,
        revision=None,
        received=None,
        kind="list",
        github_url=None,
        inbox_failures=[
            e["payload"] for e in events if e["payload"].get("type") == "inbox_rejected"
        ],
    )

    def render(filename, **values):
        (output / filename).write_text(template.render(**(common | values)), encoding="utf-8")

    def github_url(revision=None):
        if not github_repository:
            return None
        values = {"template": "tendency.yml"}
        if revision:
            values.update(
                hypothesis_id=revision.hypothesis_id, expected_revision=revision.revision_id
            )
        return "https://github.com/" + github_repository + "/issues/new?" + urlencode(values)

    render("index.html", github_url=github_url())
    render("edit.html", kind="edit", github_url=github_url())
    render("receipts.html", kind="receipts")
    render("history.html", kind="history")
    render("login.html", kind="login")
    for revision in history:
        check = next(
            (
                r
                for r in reversed(runs)
                if any(
                    revision.hypothesis_id in stage["target_ids"] for stage in r["stages"].values()
                )
            ),
            None,
        )
        render(
            page_name("r", revision.revision_id),
            kind="detail",
            revision=revision,
            check=check,
            github_url=github_url(revision),
        )
    for identity, revision in current.items():
        check = next(
            (
                r
                for r in reversed(runs)
                if any(identity in stage["target_ids"] for stage in r["stages"].values())
            ),
            None,
        )
        render(
            page_name("h", identity),
            kind="detail",
            revision=revision,
            check=check,
            github_url=github_url(revision),
        )
        render(
            page_name("edit", identity),
            kind="edit",
            revision=revision,
            github_url=github_url(revision),
        )
    for received in receipts:
        received["last_processing"] = next(
            (
                r
                for r in reversed(runs)
                if any(
                    s.get("submission_id") == received["submission_id"]
                    for s in r["stages"].values()
                )
            ),
            None,
        )
        render(page_name("s", received["submission_id"]), kind="receipt", received=received)
    # A narrow read-only legacy section: no diagnostic paths or implicit semantic migration.
    legacy = []
    for path in sorted((store.root / "data/tendencies").glob("*.json")):
        try:
            from morning_radar.models.tendency import DailyTendencies

            raw = DailyTendencies.model_validate_json(path.read_text(encoding="utf-8"))
            for decision in raw.decisions:
                legacy.append(
                    {
                        "date": str(raw.date),
                        "status": "旧格式，未升级成熟度",
                        "claim": decision.claim,
                        "reason": decision.assessment.decision_rationale,
                        "record_id": decision.record_id,
                    }
                )
            continue
        except (ValueError, OSError):
            legacy.append({"date": "未知日期", "status": "旧材料读取失败"})
    render("legacy.html", kind="legacy", legacy=legacy)
    return {
        "build_version": version,
        "reading_error": reading_error,
        "current": len(current),
        "history": len(history),
        "receipts": len(receipts),
    }

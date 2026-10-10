"""One release context; durable input first, independent analysis, immutable package."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from html import escape
from pathlib import Path
from uuid import uuid4

from morning_radar.ai import AIBudget, DeepSeekProvider
from morning_radar.ai.usage import atomic_json, usage_summary
from morning_radar.continuity.deep_workflow import run_deep_continuity_workflow
from morning_radar.models import DailyBrief, DailyContinuity
from morning_radar.pipeline import MorningRadarPipeline
from morning_radar.publishing.site_builder import SiteBuilder
from morning_radar.release.accounting import Accounting, ReleasePolicy
from morning_radar.release.archive import ArchiveConflict, contains_synthetic, git
from morning_radar.storage import load_model
from morning_radar.tendencies.core import run_core
from morning_radar.tendencies.core_store import CoreStore
from morning_radar.tendencies.github_input import load_inbox, poll_github
from morning_radar.tendencies.web_pages import build_pages
from morning_radar.time_utils import display_date, utc_now


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def valid_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("Invalid release identity")
    return value


class ReleaseCoordinator:
    def __init__(self, root: Path, *, archive, synthetic: bool = False, provider=None):
        self.root, self.archive = root.resolve(), archive
        self.directory = self.root / "data/releases/v1"
        self.synthetic, self.provider = synthetic, provider

    def save(self, context: dict, reason: str) -> None:
        atomic_json(self.directory / "runs" / (context["release_id"] + ".json"), context)
        self.archive(reason)

    def context(self, identity: str) -> dict:
        return read(self.directory / "runs" / (valid_id(identity) + ".json"))

    def ingest(self, *, token: str, transport=None) -> dict:
        try:
            result = poll_github(
                CoreStore(self.root),
                load_inbox(self.root / "config/tendency_web.yaml"),
                token=token,
                transport=transport,
                synthetic=self.synthetic,
            )
        finally:
            # Even partial successful pages/objects survive a later fetch failure.
            self.archive("inbox originals and cursor")
        return result

    def generate(
        self,
        *,
        token: str,
        transport=None,
        now: datetime | None = None,
        identity: str | None = None,
        attempt: str = "local",
    ) -> dict:
        identity = valid_id(identity or uuid4().hex)
        if (self.directory / "runs" / (identity + ".json")).exists():
            context = self.context(identity)
            if context.get("package_hash"):
                self.verify(identity)
                return context
            # An interrupted generation must be resumed explicitly as a new attempt;
            # valid transactions and physical-call accounting are already durable.
            raise ValueError("Generation interrupted; use a new run ID over saved state")
        try:
            inbox = self.ingest(token=token, transport=transport)
        except ArchiveConflict:
            raise
        except Exception as exc:
            inbox = {"scan_complete": False, "status": "failed", "error_type": type(exc).__name__}
        clock = now or utc_now()  # cutoff after input snapshots, before daily collection
        if clock.tzinfo is None:
            raise ValueError("Aware cutoff required")
        day = str(display_date(clock))
        context = {
            "schema_version": 1,
            "release_id": identity,
            "business_date": day,
            "timezone": "Asia/Singapore",
            "cutoff_at": clock.isoformat(),
            "code_sha": git(self.root, "rev-parse", "HEAD"),
            "config_hash": digest(hashes(self.root / "config")),
            "attempt": attempt,
            "synthetic": self.synthetic,
            "inbox": inbox,
            "stages": {},
            "deployment": {"status": "not_started"},
            "previous_valid_release": self.previous_release(),
            "submission_versions": {
                key: digest(value)
                for key, value in CoreStore(self.root).submissions().items()
                if datetime.fromisoformat(value["submitted_at"]) <= clock
            },
        }
        self.save(context, "release context")
        pipeline = MorningRadarPipeline(self.root)
        try:
            intake = pipeline.collect(now=clock)
            context["batch_id"] = intake.checkpoint.manifest.batch_id
            context["stages"]["collection"] = {
                "status": "completed" if not intake.collection.failures else "partial",
                "failed_sources": sorted(intake.collection.failures),
            }
        except Exception as exc:
            context["stages"]["collection"] = {"status": "failed", "error_type": type(exc).__name__}
        self.save(context, "raw checkpoint before analysis")
        accounting = Accounting(self.root, day, self.archive, ReleasePolicy.load(self.root))

        def provider():
            if self.provider is not None:
                return self.provider
            app = pipeline.app
            active = DeepSeekProvider.from_environment(
                budget=AIBudget(
                    app.maximum_ai_calls,
                    app.maximum_ai_input_characters,
                    app.maximum_ai_items,
                    app.maximum_ai_network_requests,
                ),
                prompt_dir=self.root / "prompts",
            )
            active.budget.plan_tail(
                calls=7,
                input_characters=(app.planned_brief_characters + app.planned_continuity_characters),
            )
            active.release_accounting = accounting
            return active

        def stage(name, action):
            accounting.phase = name
            context["stages"][name] = {"status": "running"}
            self.save(context, name + " intent")
            try:
                context["stages"][name] = action()
            except Exception as exc:
                context["stages"][name] = {"status": "failed", "error_type": type(exc).__name__}
            self.save(context, name + " results")

        def daily():
            if "batch_id" not in context:
                return {"status": "not_run", "reason": "collection_failed"}
            pipeline.provider = provider()
            brief = pipeline.process(batch_id=context["batch_id"], now=clock, notify=False)
            health = [read(p) for p in (self.root / "data/health" / day).glob("daily-*.json")]
            latest = max(health, key=lambda row: row["started_at"])
            return {"status": latest["status"], "brief_date": str(brief.date)}

        def tendency():
            result = run_core(self.root, provider=provider(), now=clock, after_commit=self.archive)
            run_ids = [result["run_id"]]
            if result["stages"].get("submissions", {}).get("status") == "completed":
                result = run_core(
                    self.root, provider=provider(), now=clock, after_commit=self.archive
                )
                run_ids.append(result["run_id"])
            return {
                "status": result["status"],
                "run_id": result["run_id"],
                "run_ids": run_ids,
                "stages": {key: value["status"] for key, value in result["stages"].items()},
            }

        def deep():
            result = run_deep_continuity_workflow(
                self.root,
                provider=provider(),
                current_date=display_date(clock),
                generated_at=clock,
            )
            health = [
                read(p) for p in (self.root / "data/health" / day).glob("deep_continuity-*.json")
            ]
            latest = max(health, key=lambda row: row["started_at"])
            status = latest["status"]
            if status == "no_candidates":
                status = "no_ai_trigger"
            return {"status": status, "result": result.stats}

        stage("daily", daily)
        stage("tendency", tendency)  # independent of daily success/selection
        stage("deep_continuity", deep)
        context["cost"] = usage_summary(self.root, day)
        self.freeze(context)
        self.save(context, "frozen release package")
        return context

    def previous_release(self):
        rows = [read(p) for p in (self.directory / "runs").glob("*.json")]
        rows = [r for r in rows if r["deployment"]["status"] == "confirmed"]
        return (
            max(rows, key=lambda r: r["deployment"]["confirmed_at"])["release_id"] if rows else None
        )

    def freeze(self, context: dict) -> None:
        identity = context["release_id"]
        package = self.directory / "packages" / identity
        if package.exists():
            raise ValueError("Frozen package already exists")
        from morning_radar.release.snapshot import CutoffStore

        store = CutoffStore(self.root, datetime.fromisoformat(context["cutoff_at"]))
        if not self.synthetic and any(contains_synthetic(e) for e in store.events()):
            raise ValueError("Synthetic evidence refused in public release")
        output = package / "site"
        previous = context.get("previous_valid_release")
        if previous:
            self.verify(previous)
            shutil.copytree(self.directory / "packages" / previous / "site", output)
        briefs = [
            load_model(p, DailyBrief)
            for p in (self.root / "data/briefs").glob("*.json")
            if p.stem <= context["business_date"]
        ]
        continuities = [
            load_model(p, DailyContinuity)
            for p in (self.root / "data/continuity").glob("*.json")
            if p.stem <= context["business_date"]
        ]
        if briefs:
            SiteBuilder(template_dir=self.root / "templates", output_dir=output).build(
                briefs,
                stylesheet=self.root / "site/assets/style.css",
                continuities=continuities,
                tendency_workspace=None,
                preview_notice=(
                    "synthetic：假模型响应，只验证结构及恢复，不验证分析质量"
                    if self.synthetic
                    else None
                ),
            )
        else:
            output.mkdir(parents=True, exist_ok=True)
            (output / "index.html").write_text(
                '<meta charset="utf-8"><h1>Morning Radar</h1><p>晨报尚不可用</p>'
                '<a href="tendency/index.html">Tendency</a>',
                encoding="utf-8",
            )
        from morning_radar.health import publish_health
        from morning_radar.publishing.status import write_radar_status

        atomic_json(output / "health-v1.json", publish_health(self.root, context["business_date"]))
        if context["stages"]["daily"]["status"] in {"failed", "not_run"} or not any(
            str(brief.date) == context["business_date"] for brief in briefs
        ):
            write_radar_status(
                output / "status.json",
                run_date=display_date(datetime.fromisoformat(context["cutoff_at"])),
                status="FAILED",
                detail="Current brief unavailable",
                updated_at=datetime.fromisoformat(context["cutoff_at"]),
            )
        inbox = load_inbox(self.root / "config/tendency_web.yaml")
        build_pages(
            store,
            output / "tendency",
            repository=self.root,
            github_repository=None if self.synthetic else inbox.repository,
            synthetic=self.synthetic,
        )
        successful = {
            "completed",
            "no_changes",
            "no_input",
            "insufficient_evidence",
            "no_ai_trigger",
        }
        complete = context["inbox"]["scan_complete"] and all(
            row["status"] in successful for row in context["stages"].values()
        )
        manifest = {
            key: context[key]
            for key in [
                "schema_version",
                "release_id",
                "business_date",
                "timezone",
                "cutoff_at",
                "code_sha",
                "config_hash",
                "attempt",
                "synthetic",
                "submission_versions",
                "stages",
                "previous_valid_release",
                "cost",
            ]
        }
        from morning_radar.intake.generation import artifact_digest

        manifest.update(
            batch_id=context.get("batch_id"),
            delivery_status="complete" if complete else "partial",
            brief_versions={
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (self.root / "data/briefs").glob("*.json")
                if p.stem <= context["business_date"]
            },
            brief_artifact_hashes={
                p.stem: artifact_digest(p)
                for p in (self.root / "data/briefs").glob("*.json")
                if p.stem <= context["business_date"]
            },
            tendency_revisions={key: value.revision_id for key, value in store.current().items()},
            receipt_ids=sorted(store.receipts()),
            evidence_index=hashes(self.root / "data/evidence"),
            health_versions=hashes(self.root / "data/health" / context["business_date"]),
            transaction_versions=hashes(store.directory / "transactions"),
        )
        atomic_json(output / "release.json", manifest)
        names = {
            "collection": "来源采集",
            "daily": "晨报",
            "tendency": "Tendency",
            "deep_continuity": "连续性检查",
        }
        statuses = {
            "completed": "已完成",
            "no_changes": "已检查，无变化",
            "no_input": "没有新输入",
            "no_ai_trigger": "无须深入复核",
            "insufficient_evidence": "证据不足",
            "partial": "部分完成",
            "not_run": "未运行",
            "failed": "失败",
        }
        explanation = " · ".join(
            names.get(name, name) + "：" + statuses.get(row["status"], row["status"])
            for name, row in manifest["stages"].items()
        )
        banner = (
            '<aside data-release-banner="true" style="padding:12px;border:1px solid #81938a">'
            + ("<strong>synthetic 隔离演示</strong> · " if self.synthetic else "")
            + "生成版 "
            + escape(identity)
            + " · "
            + manifest["delivery_status"]
            + ' · <a href="{prefix}release.json">发布清单</a>'
            + "<details><summary>本期处理情况（生成不等于部署）</summary><p>"
            + escape(explanation)
            + "</p></details></aside>"
        )
        for page in output.rglob("*.html"):
            prefix = "../" * (len(page.relative_to(output).parts) - 1)
            text = page.read_text(encoding="utf-8")
            if page.parent == output or page.parent == output / "briefs":
                entry = (
                    '<a href="'
                    + ("../" if page.parent.name == "briefs" else "")
                    + ('tendency/index.html">Tendency</a>')
                )
                text = text.replace("</nav>", entry + "</nav>", 1)
            text = re.sub(
                r'<aside data-release-banner="true".*?</aside>', "", text, flags=re.DOTALL
            )
            marker = banner.replace("{prefix}", prefix)
            text = (
                text.replace("<body>", "<body>" + marker, 1) if "<body>" in text else marker + text
            )
            page.write_text(text, encoding="utf-8")
        from morning_radar.release.preflight import check_links

        check_links(output)
        files = hashes(output)
        context["package_hash"] = digest(files)
        atomic_json(
            package / "manifest.json",
            {**manifest, "files": files, "package_hash": context["package_hash"]},
        )
        context["delivery_status"] = manifest["delivery_status"]
        context["build"] = {"status": "built", "package_hash": context["package_hash"]}

    def verify(self, identity: str) -> dict:
        package = self.directory / "packages" / valid_id(identity)
        manifest = read(package / "manifest.json")
        actual = hashes(package / "site")
        if actual != manifest["files"] or digest(actual) != manifest["package_hash"]:
            raise ValueError("Frozen package content mismatch")
        if self.context(identity).get("package_hash") != manifest["package_hash"]:
            raise ValueError("Release identity mismatch")
        return manifest

    def export(self, identity: str, destination: Path) -> None:
        self.verify(identity)
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("Export destination must be empty")
        shutil.copytree(
            self.directory / "packages" / identity / "site", destination, dirs_exist_ok=True
        )

"""Controlled local entry points; no web credentials or production workflow invocation."""

from __future__ import annotations

import json
from pathlib import Path

from morning_radar.ai import FakeAIProvider
from morning_radar.tendencies.core import run_core
from morning_radar.tendencies.core_export import export_core, render_markdown
from morning_radar.tendencies.core_store import CoreStore
from morning_radar.tendencies.submissions import receipt, submit
from morning_radar.time_utils import utc_now


def add_core_parser(commands) -> None:
    core = commands.add_parser("tendency-core", help="versioned standalone Tendency core")
    actions = core.add_subparsers(dest="core_action", required=True)
    for name in ["submit", "receipt", "run", "show", "demo"]:
        action = actions.add_parser(name)
        action.add_argument("--workspace", required=True, type=Path)
        if name == "submit":
            action.add_argument("--file", required=True, type=Path)
        if name == "receipt":
            action.add_argument("--submission-id", required=True)
        if name in {"run", "show"}:
            action.add_argument("--evidence-root", type=Path, default=Path("."))
        if name == "run":
            action.add_argument("--provider", choices=["fake", "deepseek"], default="fake")
            action.add_argument("--reanalyze", action="store_true")
        if name == "show":
            action.add_argument("--format", choices=["json", "markdown"], default="markdown")
            action.add_argument("--hypothesis-id")
            action.add_argument("--private", action="store_true",
                                help="include private drafts and local submissions")


def dispatch(args) -> int:
    workspace = args.workspace.resolve()
    repository = Path(".").resolve()
    if workspace == repository or any(
        path == workspace or path in workspace.parents or workspace in path.parents
        for path in [repository / "data", repository / "site"]
    ):
        raise ValueError("Local core workspace must be isolated from production data/site")
    store = CoreStore(workspace)
    if args.core_action == "submit":
        values = json.loads(args.file.read_text(encoding="utf-8"))
        print(json.dumps(submit(store, now=utc_now(), **values), ensure_ascii=False, indent=2))
    elif args.core_action == "receipt":
        print(json.dumps(receipt(store, args.submission_id), ensure_ascii=False, indent=2))
    elif args.core_action == "run":
        result = run_core(
            workspace,
            evidence_root=args.evidence_root,
            config_root=repository,
            provider=FakeAIProvider() if args.provider == "fake" else None,
            reanalyze=args.reanalyze,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.core_action == "demo":
        from morning_radar.tendencies.core_demo import build_core_demo

        print(build_core_demo(repository, workspace))
    else:
        payload = export_core(store, legacy_root=args.evidence_root, private=args.private)
        if args.hypothesis_id:
            for name in ["current", "history"]:
                payload[name] = [
                    r for r in payload[name] if r["hypothesis_id"] == args.hypothesis_id
                ]
        print(
            json.dumps(payload, ensure_ascii=False, indent=2)
            if args.format == "json"
            else render_markdown(payload)
        )
    return 0

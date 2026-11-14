"""D adapter commands; ingestion never instantiates a model or mutates GitHub."""

from __future__ import annotations

import json
import os
from pathlib import Path

from morning_radar.tendencies.core_store import CoreStore
from morning_radar.tendencies.github_input import load_inbox, poll_github
from morning_radar.tendencies.web_pages import build_pages


def add_web_parser(commands):
    web = commands.add_parser("tendency-web", help="Tendency pages and controlled inbox")
    actions = web.add_subparsers(dest="web_action", required=True)
    for name in ["build", "serve", "ingest-github"]:
        action = actions.add_parser(name)
        action.add_argument("--workspace", type=Path, required=True)
        action.add_argument("--config", type=Path, default=Path("config/tendency_web.yaml"))
        if name in {"build", "serve"}:
            action.add_argument("--synthetic", action="store_true")
        if name == "build":
            action.add_argument("--output", type=Path, required=True)
            action.add_argument("--github-entry", action="store_true")
        if name == "serve":
            action.add_argument("--port", type=int, default=8768)


def ensure_isolated(path: Path, repository: Path) -> Path:
    path = path.resolve()
    for protected in [repository / "data", repository / "site"]:
        if path == protected or protected in path.parents or path in protected.parents:
            raise ValueError("D CLI output must be isolated from production data/site")
    return path


def dispatch(args):
    repository = Path.cwd().resolve()
    store = CoreStore(ensure_isolated(args.workspace, repository))
    if args.web_action == "serve":
        from morning_radar.tendencies.web_server import serve

        serve(store, repository, port=args.port, synthetic=args.synthetic)
    elif args.web_action == "build":
        config = load_inbox(args.config)
        print(
            json.dumps(
                build_pages(
                    store,
                    ensure_isolated(args.output, repository),
                    repository=repository,
                    github_repository=config.repository if args.github_entry else None,
                    synthetic=args.synthetic,
                ),
                ensure_ascii=False,
            )
        )
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ValueError("Read-only GitHub credential required in trusted runtime")
        print(
            json.dumps(poll_github(store, load_inbox(args.config), token=token), ensure_ascii=False)
        )
    return 0

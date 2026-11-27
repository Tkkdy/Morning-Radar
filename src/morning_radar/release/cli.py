"""Release commands; live boundaries require the fixed repository Actions context."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from morning_radar.release.archive import GitArchive, git
from morning_radar.release.coordinator import ReleaseCoordinator


def parser(commands):
    command = commands.add_parser("release", help="durable coordinated release")
    command.add_argument(
        "action",
        choices=[
            "generate",
            "ingest",
            "export",
            "upload-intent",
            "deploy",
            "reconcile",
            "notify",
            "resend",
            "rollback",
            "status",
            "demo",
        ],
    )
    command.add_argument("--release-id")
    command.add_argument("--deployment-id")
    command.add_argument("--artifact-id", type=int)
    command.add_argument("--authorization-id")
    command.add_argument("--output", type=Path)
    command.add_argument("--production", action="store_true")


def dispatch(args: argparse.Namespace) -> int:
    if args.action == "demo":
        from morning_radar.release.demo import demo

        if args.output is None:
            raise ValueError("--output required")
        print(json.dumps(demo(Path.cwd(), args.output), ensure_ascii=False))
        return 0
    root = Path.cwd()
    live = args.action in {
        "generate",
        "ingest",
        "upload-intent",
        "deploy",
        "reconcile",
        "notify",
        "resend",
        "rollback",
    }
    if live:
        if not args.production or os.getenv("GITHUB_ACTIONS") != "true":
            raise ValueError("Live release requires --production in authorized GitHub Actions")
        if os.getenv("GITHUB_REPOSITORY", "").casefold() != "tkkdy/morning-radar":
            raise ValueError("Unexpected repository")
        origin = git(root, "remote", "get-url", "origin").casefold()
        if origin not in {
            "https://github.com/tkkdy/morning-radar.git",
            "https://github.com/tkkdy/morning-radar",
            "git@github.com:tkkdy/morning-radar.git",
        }:
            raise ValueError("Unexpected origin")
        if os.getenv("GITHUB_REF") != "refs/heads/master":
            raise ValueError("Production writer requires master")
    archive = GitArchive(root, remote="origin" if live else None)
    coordinator = ReleaseCoordinator(root, archive=archive)
    token = os.getenv("GITHUB_TOKEN", "")
    if args.action == "ingest":
        result = coordinator.ingest(token=token)
    elif args.action == "generate":
        result = coordinator.generate(
            token=token,
            identity=args.release_id,
            attempt=os.getenv("GITHUB_RUN_ID", "") + "/" + os.getenv("GITHUB_RUN_ATTEMPT", ""),
        )
        output = os.getenv("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a", encoding="utf-8") as stream:
                stream.write("release_id=" + result["release_id"] + "\n")
    elif args.action == "status":
        result = (
            coordinator.context(args.release_id)
            if args.release_id
            else {"runs": [p.stem for p in (coordinator.directory / "runs").glob("*.json")]}
        )
    elif args.action == "export":
        if args.output is None:
            raise ValueError("--output required")
        coordinator.export(args.release_id, args.output)
        result = coordinator.verify(args.release_id)
    elif args.action == "upload-intent":
        manifest = coordinator.verify(args.release_id)
        result = coordinator.context(args.release_id)
        result["upload"] = {"status": "unknown", "package_hash": manifest["package_hash"]}
        coordinator.save(result, "artifact upload intent")
    elif args.action in {"deploy", "reconcile", "rollback"}:
        from morning_radar.release.delivery import Pages, deploy, oidc_token

        pages = Pages("Tkkdy/Morning-Radar", token, os.environ["PUBLIC_SITE_URL"])
        result = deploy(
            coordinator,
            args.release_id,
            pages,
            artifact_id=args.artifact_id,
            oidc=oidc_token() if args.action in {"deploy", "rollback"} else None,
            reconcile_id=args.deployment_id,
            redeploy=args.action == "rollback",
        )
        if result["status"] != "confirmed":
            print(json.dumps(result))
            return 2
    else:
        from morning_radar.release.delivery import WxSender, notify

        if args.action == "resend" and (
            not args.authorization_id or os.getenv("GITHUB_EVENT_NAME") != "workflow_dispatch"
        ):
            raise ValueError("Resend requires explicit manual authorization identity")
        recipients = [uid.strip() for uid in os.environ["WXPUSHER_UIDS"].split(",") if uid.strip()]
        if not recipients:
            raise ValueError("No notification recipients configured")
        result = notify(
            coordinator,
            args.release_id,
            sender=WxSender(os.environ["WXPUSHER_APP_TOKEN"]),
            recipients=recipients,
            public_url=os.environ["PUBLIC_SITE_URL"],
            authorization_id=args.authorization_id if args.action == "resend" else None,
        )
    print(json.dumps(result, ensure_ascii=False))
    if any(r["status"] != "sent" for r in result.get("recipients", {}).values()):
        return 2
    return 0

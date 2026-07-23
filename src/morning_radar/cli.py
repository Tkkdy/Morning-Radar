"""Command-line entry points for local use and GitHub Actions."""

from __future__ import annotations

import argparse
from pathlib import Path

from morning_radar.logging_config import configure_logging
from morning_radar.pipeline import MorningRadarPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="morning-radar")
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run the full morning brief pipeline")
    run.add_argument("--fixtures", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--force-notify", action="store_true")
    commands.add_parser("build-site", help="rebuild pages from saved brief JSON")
    commands.add_parser("collect", help="collect and process data without notification")
    commands.add_parser("test-notification", help="send a safe WxPusher test")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    pipeline = MorningRadarPipeline(Path("."))
    if args.command == "run":
        pipeline.run(
            fixtures=args.fixtures,
            dry_run=args.dry_run,
            force_notify=args.force_notify,
        )
    elif args.command == "build-site":
        pipeline.build_site()
    elif args.command == "collect":
        pipeline.run(dry_run=True)
    elif (
        args.command == "test-notification"
        and not pipeline._notifier(pipeline.root).send_test()
    ):
        raise SystemExit("WxPusher test failed or configuration is missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

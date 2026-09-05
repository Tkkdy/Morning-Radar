"""Command-line entry points for local use and GitHub Actions."""

from __future__ import annotations

import argparse
import json
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
    run.add_argument("--skip-notify", action="store_true")
    commands.add_parser("build-site", help="rebuild pages from saved brief JSON")
    commands.add_parser("run-tendency", help="run standalone Tendency evaluation")
    commands.add_parser("run-deep-continuity", help="run triggered deep Judgement review")
    commands.add_parser("run-model-ab", help="run frozen-input model A/B experiment")
    commands.add_parser("collect", help="collect and process data without notification")
    commands.add_parser("test-notification", help="send a safe WxPusher test")
    notify = commands.add_parser(
        "notify-latest",
        help="notify the latest saved brief after Pages deployment",
    )
    notify.add_argument("--force", action="store_true")
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
            notify=not args.skip_notify,
        )
    elif args.command == "build-site":
        pipeline.build_site()
    elif args.command == "run-tendency":
        from morning_radar.tendencies import run_tendency_workflow

        run_tendency_workflow(pipeline.root)
        pipeline.build_site()
    elif args.command == "run-deep-continuity":
        from morning_radar.continuity.deep_workflow import (
            run_deep_continuity_workflow,
        )

        run_deep_continuity_workflow(pipeline.root)
        pipeline.build_site()
    elif args.command == "run-model-ab":
        from morning_radar.evaluation import run_model_ab_experiment

        result = run_model_ab_experiment(pipeline.root)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    elif args.command == "collect":
        pipeline.run(dry_run=True)
    elif args.command == "test-notification" and not pipeline._notifier(pipeline.root).send_test():
        raise SystemExit("WxPusher test failed or configuration is missing")
    elif args.command == "notify-latest":
        pipeline.notify_latest(force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

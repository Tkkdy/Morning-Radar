"""Command-line entry points for local use and GitHub Actions."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from morning_radar.logging_config import configure_logging
from morning_radar.pipeline import MorningRadarPipeline
from morning_radar.time_utils import utc_now


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="morning-radar")
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="collect a durable checkpoint then process it")
    run.add_argument("--fixtures", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--force-notify", action="store_true")
    run.add_argument("--skip-notify", action="store_true")
    collect = commands.add_parser(
        "collect",
        help="collect inputs and save a durable checkpoint without creating an AI provider",
    )
    collect.add_argument("--fixtures", action="store_true")
    collect.add_argument("--dry-run", action="store_true")
    collect.add_argument("--now", help="ISO-8601 clock override for tests")
    process = commands.add_parser(
        "process",
        help="process a complete intake checkpoint and unfinished recovered inputs",
    )
    process.add_argument("--fixtures", action="store_true")
    process.add_argument("--dry-run", action="store_true")
    process.add_argument("--force-notify", action="store_true")
    process.add_argument("--skip-notify", action="store_true")
    process.add_argument("--batch-id", dest="batch_id")
    process.add_argument("--now", help="ISO-8601 clock override for tests")
    inspect = commands.add_parser(
        "inspect",
        help="look up the processing fate of a saved input, URL, or Story",
    )
    inspect.add_argument("--input-id")
    inspect.add_argument("--url")
    inspect.add_argument("--story-id")
    inspect.add_argument("--json", action="store_true", dest="as_json")
    collection_inspect = commands.add_parser(
        "inspect-collection", help="read saved collection diagnostics"
    )
    collection_inspect.add_argument("--batch-id", required=True)
    collection_inspect.add_argument("--json", action="store_true", dest="as_json")
    commands.add_parser("build-site", help="rebuild pages from saved brief JSON")
    commands.add_parser("run-tendency", help="run standalone Tendency evaluation")
    commands.add_parser("run-deep-continuity", help="run triggered deep Judgement review")
    commands.add_parser("run-model-ab", help="run frozen-input model A/B experiment")
    commands.add_parser("test-notification", help="send a safe WxPusher test")
    notify = commands.add_parser(
        "notify-latest",
        help="notify the latest saved brief after Pages deployment",
    )
    notify.add_argument("--force", action="store_true")
    record = commands.add_parser(
        "record-deploy",
        help="record that a generated brief was deployed without regenerating",
    )
    record.add_argument("--date")
    record.add_argument("--brief-hash", dest="brief_hash")
    return parser


def _parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SystemExit("--now must include timezone information")
    return parsed


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
    elif args.command == "collect":
        intake = pipeline.collect(
            fixtures=args.fixtures,
            dry_run=args.dry_run,
            now=_parse_now(args.now),
        )
        print(
            json.dumps(
                {
                    "batch_id": intake.checkpoint.manifest.batch_id,
                    "complete": intake.checkpoint.manifest.complete,
                    "item_count": intake.checkpoint.manifest.item_count,
                    "path": str(intake.path),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    elif args.command == "process":
        brief = pipeline.process(
            fixtures=args.fixtures,
            dry_run=args.dry_run,
            force_notify=args.force_notify,
            notify=not args.skip_notify,
            batch_id=args.batch_id,
            now=_parse_now(args.now),
        )
        from morning_radar.intake.service import isolated_output_root
        from morning_radar.pipeline import _artifact_digest

        artifact = (
            isolated_output_root(
                pipeline.root,
                fixtures=args.fixtures,
                dry_run=args.dry_run,
            )
            / "data/briefs"
            / f"{brief.date}.json"
        )
        print(
            json.dumps(
                {
                    "brief_date": str(brief.date),
                    "brief_hash": _artifact_digest(artifact),
                    "artifact_path": f"data/briefs/{brief.date}.json",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    elif args.command == "inspect":
        from morning_radar.intake.inspect import format_inspect_summary, inspect_intake

        if not (args.input_id or args.url or args.story_id):
            raise SystemExit("inspect requires --input-id, --url, or --story-id")
        payload = inspect_intake(
            pipeline.root,
            input_id=args.input_id,
            url=args.url,
            story_id=args.story_id,
        )
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(format_inspect_summary(payload))
    elif args.command == "inspect-collection":
        from morning_radar.intake.inspect import inspect_collection

        payload = inspect_collection(pipeline.root, batch_id=args.batch_id)
        print(
            json.dumps(
                payload, ensure_ascii=False, indent=2 if args.as_json else None, sort_keys=True
            )
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
    elif args.command == "test-notification" and not pipeline._notifier(pipeline.root).send_test():
        raise SystemExit("WxPusher test failed or configuration is missing")
    elif args.command == "notify-latest":
        pipeline.notify_latest(force=args.force)
    elif args.command == "record-deploy":
        from morning_radar.intake.ledger import ProcessingLedgerStore
        from morning_radar.intake.models import PublishStatus
        from morning_radar.intake.publish import PublishStore

        if not args.date or not args.brief_hash:
            raise SystemExit("record-deploy requires --date and --brief-hash")
        from morning_radar.models import DailyBrief
        from morning_radar.pipeline import _artifact_digest
        from morning_radar.storage import load_model as load_json_model

        store = PublishStore(pipeline.root / "data/state/publish.json")
        brief_date = args.date
        brief_path = pipeline.root / "data/briefs" / f"{brief_date}.json"
        if not brief_path.exists():
            raise FileNotFoundError(f"Brief artifact missing: {brief_path}")
        brief = load_json_model(brief_path, DailyBrief)
        if str(brief.date) != brief_date:
            raise SystemExit(f"Brief date mismatch: file={brief.date} requested={brief_date}")
        from morning_radar.intake.generation import generation_is_complete

        actual_hash = _artifact_digest(brief_path)
        if not generation_is_complete(pipeline.root, brief_date, expected_hash=args.brief_hash):
            raise SystemExit(f"Generation is incomplete for {brief_date}")
        if actual_hash != args.brief_hash:
            raise SystemExit(
                "Brief hash mismatch for "
                f"{brief_date}: file={actual_hash} requested={args.brief_hash}"
            )
        recorded = store.get(brief_date)
        if recorded is None or recorded.brief_hash != args.brief_hash:
            raise SystemExit(
                f"Publish record does not match artifact {brief_date}/{args.brief_hash}"
            )
        record = store.mark_deployed(
            brief_date,
            now=utc_now(),
            brief_hash=args.brief_hash,
        )
        ledger = ProcessingLedgerStore(pipeline.root / "data/intake/ledger.json")
        now = utc_now()
        for entry in list(ledger.ledger.entries.values()):
            if entry.brief_hash == record.brief_hash and entry.brief_date == brief_date:
                ledger.update(
                    entry.input_id,
                    entry.content_version,
                    now=now,
                    publish=PublishStatus.DEPLOY_CONFIRMED,
                    deployed_at=record.deployed_at,
                )
        ledger.save()
        print(json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

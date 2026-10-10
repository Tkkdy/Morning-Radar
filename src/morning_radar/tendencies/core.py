"""Independent discovery, calibration and submission processing over saved evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from morning_radar.ai import AIBudget, AIBudgetExceeded, DeepSeekProvider
from morning_radar.ai.errors import AIError
from morning_radar.ai.usage import atomic_json, attach_usage, usage_summary
from morning_radar.health import module_run
from morning_radar.settings import AppConfig, load_model
from morning_radar.tendencies.core_models import CoreDecision, CoreOutput
from morning_radar.tendencies.core_store import CoreStore, StaleRevision
from morning_radar.tendencies.core_validation import materialize
from morning_radar.tendencies.inputs import digest, read_evidence, select_evidence
from morning_radar.tendencies.submissions import persist_split
from morning_radar.time_utils import display_date, utc_now


def encoded(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def run_core(
    root: Path,
    *,
    evidence_root: Path | None = None,
    config_root: Path | None = None,
    provider=None,
    now: datetime | None = None,
    reanalyze: bool = False,
) -> dict:
    root = root.resolve()
    config_root = (config_root or root).resolve()
    now = now or utc_now()
    if now.tzinfo is None:
        raise ValueError("Aware processing time required")
    now = now.astimezone(UTC)
    app = load_model(config_root / "config/app.yaml", AppConfig)
    policy = app.tendency_core
    store = CoreStore(root)
    run_id = uuid4().hex
    result = {
        "schema_version": 2,
        "run_id": run_id,
        "recorded_at": now.isoformat(),
        "stages": {},
        "revision_ids": [],
        "status": "running",
    }
    path = store.directory / "runs" / (run_id + ".json")
    atomic_json(path, result)
    with module_run(root, str(display_date(now)), "tendency") as health:
        try:
            active = provider or DeepSeekProvider.from_environment(
                budget=AIBudget(
                    app.tendency_maximum_ai_calls,
                    app.maximum_tendency_input_characters,
                    app.maximum_ai_items,
                    app.tendency_maximum_network_requests,
                ),
                prompt_dir=config_root / "prompts",
            )
            attach_usage(active, root, business_date=str(display_date(now)), module="tendency")
            evidence, scan = read_evidence(
                evidence_root or root,
                store,
                through=now,
                maximum_files=policy.maximum_scan_files,
                maximum_evidence=policy.maximum_evidence,
            )
            result["input_scan"] = scan
            previous_runs = [
                json.loads(p.read_text(encoding="utf-8"))
                for p in (store.directory / "runs").glob("*.json")
                if p != path
            ]
            checked, seen, checked_seeds = {}, set(), set()
            for run in previous_runs:
                if datetime.fromisoformat(run["recorded_at"]) > now:
                    continue
                for stage in run.get("stages", {}).values():
                    if stage.get("status") not in {
                        "completed",
                        "no_changes",
                        "insufficient_evidence",
                    }:
                        continue
                    checked_seeds.update(stage.get("seed_ids", []))
                    for entry in stage.get("selection", []):
                        seen.add(entry["evidence_id"])
                    for target in stage.get("target_ids", []):
                        checked[target] = max(checked.get(target, ""), run["recorded_at"])
            novel = {e.evidence_id for e in evidence} - seen
            submissions = store.submissions()
            seeds = {
                row["seed_id"]: {
                    **row,
                    **{
                        key: submissions[row["submission_id"]][key]
                        for key in ["correction_of", "expected_revision"]
                    },
                }
                for row in store.seeds()
                if datetime.fromisoformat(submissions[row["submission_id"]]["submitted_at"]) <= now
            }
            current = store.current(through=now)
            targets = sorted(
                current.values(),
                key=lambda r: (checked.get(r.hypothesis_id, ""), r.recorded_at, r.hypothesis_id),
            )[: policy.maximum_targets]
            shared_characters = 0
            analysis_version = digest(
                "|".join(
                    (config_root / "prompts" / name).read_text(encoding="utf-8")
                    for name in ["tendency_discover.md", "tendency_calibrate.md"]
                )
            )
            analysis_version += ":" + str(getattr(active, "model", type(active).__name__))
            # Discovery always has first access; calibration and splitting get separate outcomes.
            for phase in ["discovery", "calibration"]:
                phase_targets = [] if phase == "discovery" else targets
                stage = {
                    "status": "running",
                    "target_ids": [r.hypothesis_id for r in phase_targets],
                    "selection": [],
                    "gaps": [],
                    "rejected": [],
                }
                result["stages"][phase] = stage
                if phase == "calibration" and not phase_targets:
                    stage["status"] = "no_input"
                    continue
                if not evidence:
                    stage["status"] = "insufficient_evidence" if seeds else "no_input"
                    stage["gaps"] = ["No available source observations"] if seeds else []
                    continue
                if (
                    phase == "discovery"
                    and not novel
                    and not reanalyze
                    and not any(key not in {r.seed_id for r in current.values()} for key in seeds)
                ):
                    stage["status"] = "no_changes"
                    continue
                query = " ".join(e.excerpt for e in evidence if e.evidence_id in novel)
                if phase_targets:
                    query += " ".join(
                        r.decision.hypothesis.model_dump_json() for r in phase_targets
                    )
                selected, selection = select_evidence(
                    evidence,
                    query=query,
                    maximum=min(policy.maximum_selected, app.maximum_ai_items),
                    novel_ids=novel,
                )
                context = {
                    "as_of": now.isoformat(),
                    "evidence": [],
                    "registry": [
                        {
                            "hypothesis_id": r.hypothesis_id,
                            "claim": r.decision.hypothesis.claim,
                            "scope": r.decision.hypothesis.scope,
                        }
                        for r in list(current.values())[-200:]
                    ],
                    "targets": [
                        r.model_dump(mode="json", exclude={"evidence", "observed_signatures"})
                        for r in phase_targets
                    ],
                    "seeds": sorted(
                        seeds.values(),
                        key=lambda seed: (seed["seed_id"] in checked_seeds, seed["seed_id"]),
                    )[:20],
                    "reanalyze": reanalyze,
                }
                limit = min(
                    policy.stage_characters,
                    app.maximum_tendency_input_characters - shared_characters,
                )
                # Pack each responsibility before consuming transport budget. Large
                # registries/old histories cannot crowd all new evidence out forever.
                while (
                    context["registry"]
                    and len(encoded({"registry": context["registry"]})) > limit // 4
                ):
                    context["registry"].pop()
                context["seeds"] = [
                    seed
                    for seed in context["seeds"]
                    if (
                        not seed.get("correction_of")
                        if phase == "discovery"
                        else seed.get("correction_of") in stage["target_ids"]
                    )
                ]
                context["evidence"] = [e.model_dump(mode="json") for e in selected]
                while len(encoded(context)) > limit and len(context["seeds"]) > 1:
                    context["seeds"].pop()
                while len(encoded(context)) > limit and len(context["targets"]) > 1:
                    context["targets"].pop()
                while len(encoded(context)) > limit and len(selected) > 1:
                    selected.pop()
                    context["evidence"] = [e.model_dump(mode="json") for e in selected]
                stage["target_ids"] = [r["hypothesis_id"] for r in context["targets"]]
                stage["seed_ids"] = [r["seed_id"] for r in context["seeds"]]
                stage["registry_truncated"] = len(context["registry"]) < len(current)
                stage["selection"] = [
                    s for s in selection if s["evidence_id"] in {e.evidence_id for e in selected}
                ]
                stage["truncated"] = len(selected) < len(evidence) or len(current) > 200
                stage["input_characters"] = len(encoded(context))
                stage["request"] = context
                if not selected or len(encoded(context)) > limit:
                    stage["status"] = "budget_skipped"
                    continue
                shared_characters += len(encoded(context))
                atomic_json(path, result)
                try:
                    method = (
                        active.discover_hypotheses
                        if phase == "discovery"
                        else active.calibrate_hypotheses
                    )
                    output = CoreOutput.model_validate(method(context))
                    stage["output"] = output.model_dump(mode="json")
                    stage["gaps"] = output.gaps
                    accepted = 0
                    for index, proposal in enumerate(output.decisions):
                        try:
                            draft = CoreDecision.model_validate(proposal)
                            if phase == "discovery" and (
                                draft.hypothesis_id or draft.kind != "early"
                            ):
                                raise ValueError(
                                    "Discovery must not silently update existing hypotheses"
                                )
                            if (
                                phase == "calibration"
                                and (draft.hypothesis_id or draft.parent_hypothesis_id)
                                not in stage["target_ids"]
                            ):
                                raise ValueError("Update outside selected targets")
                            revision = materialize(
                                draft,
                                evidence={e.evidence_id: e for e in selected},
                                current=current,
                                seeds=seeds,
                                now=now,
                                run_id=run_id,
                                analysis_version=analysis_version,
                            )
                            if revision is not None:
                                store.append(
                                    {
                                        "type": "revision",
                                        "revisions": [revision.model_dump(mode="json")],
                                    },
                                    operation_id=f"{run_id}:{phase}:{index}",
                                )
                                current[revision.hypothesis_id] = revision
                                result["revision_ids"].append(revision.revision_id)
                                accepted += 1
                        except (ValueError, StaleRevision) as exc:
                            stage["rejected"].append({"index": index, "reason": str(exc)[:300]})
                    stage["status"] = (
                        ("partial" if accepted else "validation_failed")
                        if stage["rejected"]
                        else (
                            "completed"
                            if accepted
                            else "insufficient_evidence"
                            if output.outcome == "insufficient_evidence"
                            else "no_changes"
                        )
                    )
                    if phase == "calibration":
                        addressed = {
                            d.get("hypothesis_id") or d.get("parent_hypothesis_id")
                            for d in output.decisions
                        }
                        missing = set(stage["target_ids"]) - addressed
                        if missing:
                            stage["unaddressed_targets"] = sorted(missing)
                            stage["status"] = "partial"
                except (AIError, AIBudgetExceeded, ValueError) as exc:
                    stage["status"] = (
                        "budget_skipped"
                        if isinstance(exc, AIBudgetExceeded)
                        else "provider_or_validation_failed"
                    )
                    stage["error_type"] = type(exc).__name__
                stage["physical_call_id"] = getattr(active, "last_core_call_id", None)
                stage["response_digest"] = getattr(active, "last_core_response", None)
                atomic_json(path, result)
            pending = [
                s
                for s in submissions.values()
                if store.receipts().get(s["submission_id"], {}).get("status") != "processed"
                and datetime.fromisoformat(s["submitted_at"]) <= now
            ]
            split_stage = {"status": "no_input", "pending": len(pending)}
            result["stages"]["submissions"] = split_stage
            split_called = False
            if pending:
                submission = pending[0]
                context = {"raw_text": submission["raw_text"]}
                try:
                    if (
                        len(encoded(context)) + shared_characters
                        > app.maximum_tendency_input_characters
                    ):
                        raise AIBudgetExceeded("No remaining input budget")
                    split_stage["status"] = "running"
                    split_stage["submission_id"] = submission["submission_id"]
                    atomic_json(path, result)
                    split_called = True
                    output = active.split_hypotheses(context)
                    split_stage["output"] = output.model_dump(mode="json")
                    saved = persist_split(store, submission, split_stage["output"])
                    split_stage["status"] = (
                        "completed" if saved["status"] == "processed" else "partial"
                    )
                    if len(pending) > 1:
                        split_stage["status"] = "partial"
                except (AIError, AIBudgetExceeded, ValueError) as exc:
                    split_stage["status"] = (
                        "budget_skipped"
                        if isinstance(exc, AIBudgetExceeded)
                        else "provider_or_validation_failed"
                    )
                    split_stage["error_type"] = type(exc).__name__
            split_stage["physical_call_id"] = (
                getattr(active, "last_core_call_id", None) if split_called else None
            )
            addressed_targets = set(result["stages"]["calibration"].get("target_ids", []))
            result["pending_target_ids"] = sorted(
                {r.hypothesis_id for r in store.current(through=now).values()}
                - addressed_targets
                - {r.hypothesis_id for r in store.revisions() if r.run_id == run_id}
            )
            statuses = [s["status"] for s in result["stages"].values()]
            result["status"] = (
                "partial"
                if scan["errors"]
                or result["pending_target_ids"]
                or any(s not in {"completed", "no_changes", "no_input"} for s in statuses)
                else ("completed" if result["revision_ids"] else "no_changes")
            )
            result["cost"] = usage_summary(root, str(display_date(now)))
            health["status"] = result["status"]
            health["details"] = {
                "core_version": 2,
                "run_id": run_id,
                "stages": {k: v["status"] for k, v in result["stages"].items()},
            }
            return result
        except BaseException as exc:
            result["status"] = (
                "cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed"
            )
            result["error_type"] = type(exc).__name__
            raise
        finally:
            atomic_json(path, result)

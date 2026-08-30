"""Controlled B0.5 semantic shadow evaluation using the production pipeline."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import TypeAdapter

from morning_radar.ai import AIBudget, AIBudgetExceeded, FakeAIProvider
from morning_radar.ai.deepseek_provider import DeepSeekProvider
from morning_radar.ai.models import (
    BriefDraft,
    CandidateTriageBatch,
    ContinuityResolution,
    ContinuityResolutionInput,
    DirectionObservation,
    MergedStoryDraft,
    StoryScore,
    TendencyEvaluationBatch,
)
from morning_radar.diagnostics import DailyDecisionTrace, DecisionStage
from morning_radar.editorial.models import EditorialDecision, EditorialDecisionBatch
from morning_radar.models import (
    Candidate,
    DailyBrief,
    RawItem,
    SemanticDisposition,
    Signal,
    Story,
    TendencyCurrentView,
    TendencyEvidenceCluster,
)
from morning_radar.pipeline import MorningRadarPipeline
from morning_radar.processing import (
    filter_news_window,
    filter_story_candidate_inputs,
    story_evidence_integrity_violations,
)
from morning_radar.processing.story_builder import (
    StoryValidationError,
    _validate_candidate_story_draft,
)
from morning_radar.storage import load_model, load_models, write_json

GOLDEN_RAW_ID = "item-4d7b9f9d11a89fb3b930"
REGRESSION_DATE = date(2026, 8, 22)
REAL_SEMANTIC_TASKS = ("candidate_triage", "construct_story", "score_story")
FAKE_DOWNSTREAM_TASKS = (
    "evaluate_editorial",
    "resolve_continuity",
    "evaluate_tendencies",
    "write_brief",
    "direction_observation",
)
REQUIRED_DEEPSEEK_ENV = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_BASE_URL",
)


class EvaluationSafetyError(RuntimeError):
    """The controlled evaluation boundary could not be guaranteed."""


class EvidenceNetworkAttempted(EvaluationSafetyError):
    """Evidence resolution attempted an outbound fetch during semantic evaluation."""


class EvidenceIntegrityViolation(EvaluationSafetyError):
    """A persisted Story failed the deterministic evidence integrity check."""


class SemanticProviderStopped(EvaluationSafetyError):
    """A real semantic task exhausted its built-in retries or returned invalid output."""


class SemanticEvaluationMode(StrEnum):
    """Explicit safety boundary between a known regression and a clean holdout."""

    REGRESSION = "regression"
    HOLDOUT = "holdout"


@dataclass(frozen=True, slots=True)
class DeepSeekEvaluationConfig:
    model: str
    base_url: str
    api_key: str = field(repr=False)

    @property
    def base_host(self) -> str:
        return urlsplit(self.base_url).hostname or "INVALID_HOST"


@dataclass(slots=True)
class TaskObservation:
    method_attempts: int = 0
    logical_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    network_requests: int = 0
    runtime_seconds: float = 0.0


class ForbiddenEvidenceFetcher:
    """Fail closed if production Evidence resolution attempts any HTTP request."""

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, url: str) -> Any:
        self.calls += 1
        del url
        raise EvidenceNetworkAttempted(
            "Evidence HTTP is disabled for the semantic shadow evaluation"
        )


class SemanticShadowProvider:
    """Route only the B0.5 semantic core to the selected semantic Provider."""

    def __init__(
        self,
        *,
        semantic_provider: Any,
        downstream_provider: FakeAIProvider,
        budget: AIBudget,
        secret_values: tuple[str, ...] = (),
    ) -> None:
        if getattr(semantic_provider, "budget", None) is not budget:
            raise EvaluationSafetyError("semantic Provider must share the evaluation budget")
        if getattr(downstream_provider, "budget", None) is not budget:
            raise EvaluationSafetyError("downstream Provider must share the evaluation budget")
        self.semantic_provider = semantic_provider
        self.downstream_provider = downstream_provider
        self.budget = budget
        self.secret_values = secret_values
        self.task_observations = {task: TaskObservation() for task in REAL_SEMANTIC_TASKS}
        self.triage_inputs: dict[str, Candidate] = {}
        self.triage_drafts: dict[str, Any] = {}
        self.story_drafts: dict[str, MergedStoryDraft] = {}
        self.score_results: dict[str, StoryScore] = {}
        self.semantic_errors: list[dict[str, str]] = []

    def _call(self, task: str, function: Any, *args: Any) -> Any:
        observation = self.task_observations[task]
        before_calls = self.budget.calls_used
        before_network = self.budget.network_requests_used
        started = time.perf_counter()
        observation.method_attempts += 1
        try:
            result = function(*args)
        except Exception as exc:
            observation.failed_calls += 1
            self.semantic_errors.append(
                {
                    "task": task,
                    "error_type": type(exc).__name__,
                    "message": _sanitize(str(exc), self.secret_values),
                }
            )
            if isinstance(exc, AIBudgetExceeded):
                raise
            raise SemanticProviderStopped(
                f"{task} stopped after Provider retries: "
                f"{type(exc).__name__}: {_sanitize(str(exc), self.secret_values)}"
            ) from exc
        else:
            observation.successful_calls += 1
            return result
        finally:
            observation.logical_calls += self.budget.calls_used - before_calls
            observation.network_requests += self.budget.network_requests_used - before_network
            observation.runtime_seconds += time.perf_counter() - started

    def triage_candidates(self, candidates: list[Candidate]) -> CandidateTriageBatch:
        self.triage_inputs.update((candidate.id, candidate) for candidate in candidates)
        output = self._call(
            "candidate_triage", self.semantic_provider.triage_candidates, candidates
        )
        self.triage_drafts.update((draft.candidate_id, draft) for draft in output.candidates)
        return output

    def construct_story(self, candidate: Candidate) -> MergedStoryDraft:
        draft = self._call("construct_story", self.semantic_provider.construct_story, candidate)
        self.story_drafts[candidate.id] = draft
        return draft

    def score_story(self, story: Story) -> StoryScore:
        score = self._call("score_story", self.semantic_provider.score_story, story)
        for candidate_id in story.candidate_ids:
            self.score_results[candidate_id] = score
        return score

    def evaluate_editorial(self, stories: list[Story]) -> EditorialDecisionBatch:
        return self.downstream_provider.evaluate_editorial(stories)

    def write_brief(
        self,
        stories: list[Story],
        signals: list[Signal],
        editorial_decisions: list[EditorialDecision] | None = None,
    ) -> BriefDraft:
        return self.downstream_provider.write_brief(stories, signals, editorial_decisions)

    def write_direction_observation(self, signals: list[Signal]) -> DirectionObservation:
        return self.downstream_provider.write_direction_observation(signals)

    def resolve_continuity(self, context: ContinuityResolutionInput) -> ContinuityResolution:
        return self.downstream_provider.resolve_continuity(context)

    def evaluate_tendencies(
        self,
        clusters: list[TendencyEvidenceCluster],
        current_views: list[TendencyCurrentView],
    ) -> TendencyEvaluationBatch:
        return self.downstream_provider.evaluate_tendencies(clusters, current_views)


def deepseek_configuration_from_environment() -> DeepSeekEvaluationConfig:
    missing = [name for name in REQUIRED_DEEPSEEK_ENV if not os.getenv(name)]
    if missing:
        raise EvaluationSafetyError(
            "Missing required DeepSeek environment variable(s): " + ", ".join(missing)
        )
    return DeepSeekEvaluationConfig(
        model=os.environ["DEEPSEEK_MODEL"],
        base_url=os.environ["DEEPSEEK_BASE_URL"],
        api_key=os.environ["DEEPSEEK_API_KEY"],
    )


def _load_frozen_workload(
    root: Path,
    evaluation_date: date,
    *,
    evaluation_mode: SemanticEvaluationMode = SemanticEvaluationMode.REGRESSION,
) -> tuple[list[RawItem], datetime]:
    raw_path = root / "data/raw" / f"{evaluation_date}.json"
    brief_path = root / "data/briefs" / f"{evaluation_date}.json"
    if not raw_path.is_file() or not brief_path.is_file():
        raise EvaluationSafetyError(
            f"Frozen Raw and Brief artifacts are required for {evaluation_date}"
        )
    raw = TypeAdapter(list[RawItem]).validate_json(raw_path.read_text(encoding="utf-8"))
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    frozen_now = datetime.fromisoformat(str(brief["generated_at"]).replace("Z", "+00:00"))
    if evaluation_mode is SemanticEvaluationMode.REGRESSION:
        if evaluation_date != REGRESSION_DATE:
            raise EvaluationSafetyError(
                f"Regression mode requires the frozen {REGRESSION_DATE} workload"
            )
        if not any(item.id == GOLDEN_RAW_ID for item in raw):
            raise EvaluationSafetyError(f"DeepSeek golden Raw ID not found: {GOLDEN_RAW_ID}")
    return raw, frozen_now


def _budget_for_pipeline(pipeline: MorningRadarPipeline) -> AIBudget:
    app = pipeline.app
    required = {
        "maximum_ai_calls": 50,
        "maximum_ai_input_characters": 120_000,
        "maximum_ai_items": 40,
        "maximum_triage_input_characters": 80_000,
        "maximum_story_candidates": 17,
    }
    mismatches = [
        f"{name}={getattr(app, name)!r} (expected {expected!r})"
        for name, expected in required.items()
        if getattr(app, name) != expected
    ]
    if mismatches:
        raise EvaluationSafetyError(
            "Production resource envelope changed: " + "; ".join(mismatches)
        )
    return AIBudget(
        maximum_calls=app.maximum_ai_calls,
        maximum_input_characters=app.maximum_ai_input_characters,
        maximum_items=app.maximum_ai_items,
        protected_minimums=dict(app.protected_ai_calls),
        protected_input_minimums=dict(app.protected_ai_input_characters),
    )


def _prepare_history(root: Path, history_root: Path, evaluation_date: date) -> None:
    for category in ("stories", "continuity", "tendencies"):
        source = root / "data" / category
        destination = history_root / "data" / category
        destination.mkdir(parents=True, exist_ok=True)
        for path in source.glob("*.json"):
            try:
                artifact_date = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if artifact_date < evaluation_date:
                shutil.copy2(path, destination / path.name)


def _safe_run_directory(root: Path, output_root: Path, evaluation_date: date) -> Path:
    resolved_root = root.resolve()
    run_directory = (output_root / str(evaluation_date)).resolve()
    production_data = (resolved_root / "data").resolve()
    if run_directory in (resolved_root, production_data):
        raise EvaluationSafetyError("evaluation output may not be the production root")
    if production_data in run_directory.parents:
        raise EvaluationSafetyError("evaluation output may not be inside production data/")
    if run_directory.exists() and any(run_directory.iterdir()):
        raise EvaluationSafetyError(
            f"Evaluation output already exists; whole-run rerun is blocked: {run_directory}"
        )
    run_directory.mkdir(parents=True, exist_ok=True)
    return run_directory


def _sanitize(value: str, secrets: tuple[str, ...] = ()) -> str:
    sanitized = value
    for secret in secrets:
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    sanitized = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", sanitized)
    return sanitized[:2000]


def _story_dispositions(trace: DailyDecisionTrace) -> dict[str, str]:
    dispositions: dict[str, str] = {}
    for record in trace.records:
        for transition in record.transitions:
            if transition.stage is DecisionStage.STORY_CONSTRUCTION and transition.candidate_id:
                dispositions[transition.candidate_id] = transition.decision
    return dispositions


def _candidate_row(
    candidate: Candidate,
    *,
    initial: Candidate | None,
    raw_by_id: dict[str, RawItem],
    story_result: str,
    model_disposition: str | None,
) -> dict[str, Any]:
    evidence = candidate.evidence
    return {
        "candidate_id": candidate.id,
        "raw_item_ids": candidate.raw_item_ids,
        "title": candidate.hypothesis,
        "hypothesis": candidate.hypothesis,
        "source_summary": [
            {
                "raw_item_id": raw_id,
                "title": raw_by_id[raw_id].title,
                "source_name": raw_by_id[raw_id].source_name,
                "source_role": raw_by_id[raw_id].source_role.value,
                "community_score": raw_by_id[raw_id].metadata.get("score"),
            }
            for raw_id in candidate.raw_item_ids
            if raw_id in raw_by_id
        ],
        "must_triage": candidate.must_triage,
        "initial_evidence_authorities": [
            item.authority.value for item in (initial.evidence if initial else evidence)
        ],
        "initial_evidence_state": (initial.evidence_state.value if initial else "unknown"),
        "semantic_disposition": (
            candidate.semantic_disposition.value
            if candidate.semantic_disposition is not None
            else None
        ),
        "model_semantic_disposition": model_disposition,
        "reason_codes": [code.value for code in candidate.reason_codes],
        "potential_novelty": candidate.potential_novelty,
        "potential_impact": candidate.potential_impact,
        "affected_audiences": candidate.affected_audiences,
        "impact_mechanism": candidate.impact_mechanism,
        "alternative_explanation": candidate.alternative_explanation,
        "missing_evidence": candidate.missing_evidence,
        "verification_target": candidate.verification_target,
        "investigation_priority": candidate.investigation_priority,
        "evidence_state": candidate.evidence_state.value,
        "execution_state": candidate.execution_state.value,
        "evaluation_investigation_state": (
            "NOT_EXECUTED_FOR_EVAL"
            if candidate.semantic_disposition is SemanticDisposition.INVESTIGATE
            else "NOT_APPLICABLE"
        ),
        "story_result": story_result,
    }


def _partial_candidate_rows(
    provider: SemanticShadowProvider, raw_by_id: dict[str, RawItem]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in provider.triage_inputs.values():
        draft = provider.triage_drafts.get(candidate.id)
        if draft is None:
            rows.append(
                _candidate_row(
                    candidate,
                    initial=candidate,
                    raw_by_id=raw_by_id,
                    story_result="NOT_REACHED",
                    model_disposition=None,
                )
            )
            continue
        updated = candidate.model_copy(
            update={
                "hypothesis": draft.hypothesis,
                "potential_novelty": draft.potential_novelty,
                "potential_impact": draft.potential_impact,
                "affected_audiences": draft.affected_audiences,
                "impact_mechanism": draft.impact_mechanism,
                "alternative_explanation": draft.alternative_explanation,
                "semantic_disposition": draft.semantic_disposition,
                "evidence_state": draft.evidence_state,
                "reason_codes": list(dict.fromkeys([*candidate.reason_codes, *draft.reason_codes])),
                "rationale": draft.rationale,
                "missing_evidence": draft.missing_evidence,
                "verification_target": draft.verification_target,
                "verification_path": draft.verification_path,
                "investigation_priority": draft.investigation_priority,
            }
        )
        rows.append(
            _candidate_row(
                updated,
                initial=candidate,
                raw_by_id=raw_by_id,
                story_result="NOT_REACHED",
                model_disposition=draft.semantic_disposition.value,
            )
        )
    return rows


def _story_rows(
    candidates: list[Candidate],
    stories: list[Story],
    provider: SemanticShadowProvider,
    dispositions: dict[str, str],
) -> list[dict[str, Any]]:
    story_by_candidate = {
        candidate_id: story for story in stories for candidate_id in story.candidate_ids
    }
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        disposition = dispositions.get(candidate.id)
        if candidate.semantic_disposition is not SemanticDisposition.BUILD:
            continue
        story = story_by_candidate.get(candidate.id)
        draft = provider.story_drafts.get(candidate.id)
        validation_outcome = "NOT_ATTEMPTED"
        rejection_reason: str | None = None
        derived_subjects: dict[str, str] = {}
        if draft is not None:
            try:
                derived_subjects = _validate_candidate_story_draft(candidate, draft)
            except StoryValidationError as exc:
                validation_outcome = "REJECTED"
                rejection_reason = str(exc)
            else:
                validation_outcome = "ACCEPTED"
        supports = []
        if story is not None:
            supports = [item.model_dump(mode="json") for item in story.claim_supports]
        elif draft is not None:
            supports = [
                {
                    **item.model_dump(mode="json"),
                    "claim_subject": derived_subjects.get(item.claim),
                }
                for item in draft.fact_supports
            ]
        rows.append(
            {
                "candidate_id": candidate.id,
                "story_id": story.id if story else None,
                "story_result": disposition or "NOT_ATTEMPTED",
                "canonical_title": (
                    story.canonical_title if story else (draft.canonical_title if draft else None)
                ),
                "facts": story.facts if story else (draft.facts if draft else []),
                "analysis": story.analysis if story else (draft.analysis if draft else []),
                "uncertainties": (
                    story.uncertainties if story else (draft.uncertainties if draft else [])
                ),
                "claim_supports": supports,
                "evidence_ids": sorted(
                    {
                        evidence_id
                        for support in supports
                        for evidence_id in support.get("evidence_ids", [])
                    }
                ),
                "deterministic_validation_outcome": validation_outcome,
                "rejection_reason": rejection_reason,
                "scores": (
                    {
                        "relevance": story.relevance_score,
                        "importance": story.importance_score,
                        "novelty": story.novelty_score,
                        "credibility": story.credibility_score,
                    }
                    if story
                    else None
                ),
            }
        )
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            output.write("\n")


def _markdown_cell(value: Any) -> str:
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    return str(value if value not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _write_candidate_review(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = (
        "Candidate",
        "Source",
        "MUST_TRIAGE?",
        "Disposition",
        "Reason",
        "Potential Impact",
        "Evidence State",
        "Story Result",
        "Human Review",
    )
    lines = ["# Candidate Human Review", "", "| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        sources = ", ".join(
            f"{item['source_name']} ({item['source_role']})" for item in row["source_summary"]
        )
        values = (
            row["candidate_id"],
            sources,
            "YES" if row["must_triage"] else "NO",
            (f"model={row['model_semantic_disposition']} → final={row['semantic_disposition']}"),
            row["reason_codes"],
            row["potential_impact"],
            row["evidence_state"],
            row["story_result"],
            "UNREVIEWED",
        )
        lines.append("| " + " | ".join(_markdown_cell(value) for value in values) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_story_review(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = (
        "Candidate",
        "Story Result",
        "Boundary",
        "Title",
        "Facts",
        "Rejection Reason",
        "Human Review",
    )
    lines = ["# Story Human Review", "", "| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        values = (
            row["candidate_id"],
            row["story_result"],
            row["deterministic_validation_outcome"],
            row["canonical_title"],
            row["facts"],
            row["rejection_reason"],
            "UNREVIEWED",
        )
        lines.append("| " + " | ".join(_markdown_cell(value) for value in values) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sample_priority(row: dict[str, Any]) -> tuple[int, float, str]:
    roles = {item["source_role"] for item in row["source_summary"]}
    scores = [
        float(item["community_score"] or 0)
        for item in row["source_summary"]
        if item["community_score"] is not None
    ]
    diversity = len(
        roles
        & {
            "official_primary",
            "editorial",
            "practitioner",
            "community_discovery",
            "upstream_discovery",
        }
    )
    return (-int(row["must_triage"]) - diversity, -max(scores, default=0), row["candidate_id"])


def _write_semantic_sample(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Semantic Review Sample",
        "",
        "Behavioral samples only. Human quality labels remain UNREVIEWED.",
        "",
    ]
    by_disposition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["semantic_disposition"]:
            by_disposition[row["semantic_disposition"]].append(row)
    for disposition in ("drop", "build", "investigate"):
        lines.extend([f"## {disposition.upper()}", ""])
        selected = sorted(by_disposition[disposition], key=_sample_priority)[:5]
        if not selected:
            lines.extend(["No candidates.", ""])
            continue
        for row in selected:
            source = ", ".join(
                f"{item['source_name']} ({item['source_role']}, score={item['community_score']})"
                for item in row["source_summary"]
            )
            lines.extend(
                [
                    f"- `{row['candidate_id']}` — {row['title']}",
                    f"  - Source: {source}",
                    f"  - MUST_TRIAGE: {row['must_triage']}",
                    f"  - Reason: {', '.join(row['reason_codes']) or '—'}",
                    f"  - Potential impact: {row['potential_impact'] or '—'}",
                    "  - Human Review: UNREVIEWED",
                ]
            )
        lines.append("")
    unknown_unknown = [
        row
        for row in rows
        if not row["must_triage"] and row["semantic_disposition"] in {"build", "investigate"}
    ]
    lines.extend(["## Unknown-Unknown Review", ""])
    if unknown_unknown:
        for row in sorted(unknown_unknown, key=_sample_priority):
            lines.append(
                f"- `{row['candidate_id']}` — {row['title']} "
                f"({str(row['semantic_disposition']).upper()}); Human Review: UNREVIEWED"
            )
    else:
        lines.append("No non-MUST_TRIAGE BUILD/INVESTIGATE candidates.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _usage_summary(provider: SemanticShadowProvider, budget: AIBudget) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    for task in REAL_SEMANTIC_TASKS:
        observation = provider.task_observations[task]
        usage = budget.task_usage.get(task, {})
        finish_reasons = budget.task_finish_reasons.get(task, {})
        tasks[task] = {
            "method_attempts": observation.method_attempts,
            "logical_calls": observation.logical_calls,
            "successful_calls": observation.successful_calls,
            "failed_calls": observation.failed_calls,
            "network_requests": observation.network_requests,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "reasoning_tokens": usage.get("reasoning_tokens", 0),
            "finish_reasons": finish_reasons,
            "runtime_seconds": round(observation.runtime_seconds, 6),
            "retry_breakdown": "NOT_SEPARATELY_OBSERVABLE",
        }
    return {
        "by_task": tasks,
        "total": {
            "logical_calls": sum(row["logical_calls"] for row in tasks.values()),
            "network_requests": sum(row["network_requests"] for row in tasks.values()),
            "prompt_tokens": sum(row["prompt_tokens"] for row in tasks.values()),
            "completion_tokens": sum(row["completion_tokens"] for row in tasks.values()),
            "reasoning_tokens": sum(row["reasoning_tokens"] for row in tasks.values()),
            "finish_reasons": dict(
                sum((Counter(row["finish_reasons"]) for row in tasks.values()), Counter())
            ),
        },
    }


def _golden_summary(
    candidate_rows: list[dict[str, Any]], trace: DailyDecisionTrace | None
) -> dict[str, Any]:
    row = next((row for row in candidate_rows if GOLDEN_RAW_ID in row["raw_item_ids"]), None)
    transitions: list[dict[str, Any]] = []
    if trace is not None:
        record = next((item for item in trace.records if item.raw_item_id == GOLDEN_RAW_ID), None)
        if record is not None:
            transitions = [item.model_dump(mode="json") for item in record.transitions]
    reached_triage = (
        any(
            transition["stage"] == DecisionStage.SEMANTIC_TRIAGE.value for transition in transitions
        )
        or row is not None
    )
    disposition = row["model_semantic_disposition"] if row else None
    return {
        "raw_item_id": GOLDEN_RAW_ID,
        "candidate_admitted": row is not None,
        "must_triage": row["must_triage"] if row else None,
        "entered_semantic_triage": reached_triage,
        "semantic_disposition": disposition,
        "final_pipeline_disposition": row["semantic_disposition"] if row else None,
        "potential_novelty": row["potential_novelty"] if row else None,
        "potential_impact": row["potential_impact"] if row else None,
        "missing_evidence": row["missing_evidence"] if row else None,
        "evidence_state": row["evidence_state"] if row else None,
        "story_attempted": (
            row is not None
            and row["story_result"] in {"STORY_BUILT", "STORY_REJECTED", "STORY_FAILED_AI"}
        ),
        "story_result": row["story_result"] if row else None,
        "semantic_recall_concern": disposition == "drop",
        "regression_passed": reached_triage,
        "trace": transitions,
    }


def _build_summary(
    *,
    root: Path,
    evaluation_date: date,
    evaluation_mode: SemanticEvaluationMode,
    provider_kind: str,
    model_name: str,
    base_host: str,
    raw: list[RawItem],
    frozen_now: datetime,
    run_directory: Path,
    budget: AIBudget,
    provider: SemanticShadowProvider,
    fetcher: ForbiddenEvidenceFetcher,
    runtime_seconds: float,
    candidate_rows: list[dict[str, Any]],
    story_rows: list[dict[str, Any]],
    stories: list[Story],
    trace: DailyDecisionTrace | None,
    brief: DailyBrief | None,
    violations: list[dict[str, str]],
    status: str,
    problems: list[str],
) -> dict[str, Any]:
    dispositions = Counter(row["semantic_disposition"] for row in candidate_rows)
    model_dispositions = Counter(row["model_semantic_disposition"] for row in candidate_rows)
    recent = filter_news_window(raw, now=frozen_now, hours=24)
    eligible, _ = filter_story_candidate_inputs(recent, market_movement_threshold=0.03)
    story_results = Counter(row["story_result"] for row in story_rows)
    must_rows = [row for row in candidate_rows if row["must_triage"]]
    non_must = [row for row in candidate_rows if not row["must_triage"]]
    golden = (
        _golden_summary(candidate_rows, trace)
        if evaluation_mode is SemanticEvaluationMode.REGRESSION
        else {
            "status": "NOT_APPLICABLE",
            "reason": "Holdout mode does not evaluate the 2026-08-22 golden case",
        }
    )
    return {
        "status": status,
        "environment": {
            "git_sha": _git_sha(root),
            "date": str(evaluation_date),
            "evaluation_mode": evaluation_mode.value,
            "frozen_now": frozen_now.isoformat(),
            "provider": provider_kind,
            "model": model_name,
            "base_url_host": base_host,
            "pipeline": "MorningRadarPipeline.run",
            "real_semantic_stages": list(REAL_SEMANTIC_TASKS),
            "fake_downstream_stages": list(FAKE_DOWNSTREAM_TASKS),
            "evidence_network": "DISABLED",
            "live_collectors": "DISABLED",
            "notification": "DISABLED",
            "production_writes": "DISABLED",
        },
        "candidate_funnel": {
            "raw": len(raw),
            "recent": len(recent),
            "eligible": len(eligible),
            "admitted": len(candidate_rows),
            "must_triage": len(must_rows),
            "triaged": sum(value for key, value in dispositions.items() if key),
            "drop": dispositions["drop"],
            "build": dispositions["build"],
            "investigate": dispositions["investigate"],
            "ai_failed": sum(row["execution_state"] == "failed_ai" for row in candidate_rows),
            "budget_deferred": sum(
                row["execution_state"] == "deferred_by_budget"
                and row["model_semantic_disposition"] is None
                for row in candidate_rows
            ),
            "investigations_not_executed_for_eval": sum(
                row["evaluation_investigation_state"] == "NOT_EXECUTED_FOR_EVAL"
                for row in candidate_rows
            ),
        },
        "model_triage_distribution": {
            key: {
                "count": model_dispositions[key],
                "ratio_of_triaged": (
                    model_dispositions[key]
                    / max(
                        1,
                        sum(value for name, value in model_dispositions.items() if name),
                    )
                ),
            }
            for key in ("drop", "build", "investigate")
        },
        "final_routing_distribution": {
            key: dispositions[key] for key in ("drop", "build", "investigate")
        },
        "guardrail_dependence": {
            "must_triage_count": len(must_rows),
            "must_triage_model_dispositions": dict(
                Counter(row["model_semantic_disposition"] for row in must_rows)
            ),
            "must_triage_final_dispositions": dict(
                Counter(row["semantic_disposition"] for row in must_rows)
            ),
            "non_must_model_build_or_investigate": sum(
                row["model_semantic_disposition"] in {"build", "investigate"} for row in non_must
            ),
            "non_must_final_build_or_investigate": sum(
                row["semantic_disposition"] in {"build", "investigate"} for row in non_must
            ),
        },
        "deepseek_golden": golden,
        "story_funnel": {
            "build_candidates": dispositions["build"],
            "attempted": sum(
                row["story_result"] in {"STORY_BUILT", "STORY_REJECTED", "STORY_FAILED_AI"}
                for row in story_rows
            ),
            "accepted": story_results["STORY_BUILT"],
            "boundary_rejected": story_results["STORY_REJECTED"],
            "ai_failed": story_results["STORY_FAILED_AI"],
            "budget_deferred": story_results["STORY_DEFERRED_BY_BUDGET"],
        },
        "integrity": {
            "persisted_stories": len(stories),
            "evidence_integrity_violations": len(violations),
            "details": violations,
            "checker": "story_evidence_integrity_violations",
        },
        "usage": _usage_summary(provider, budget),
        "resource_envelope": {
            "logical_calls_used": budget.calls_used,
            "logical_calls_cap": budget.maximum_calls,
            "serialized_input_characters_used": budget.input_characters_used,
            "serialized_input_characters_cap": budget.maximum_input_characters,
            "network_requests_total": budget.network_requests_used,
            "stage_calls": dict(budget.stage_calls),
            "stage_input_characters": dict(budget.stage_input_characters),
            "provider_prompt_tokens_are_separate_from_serialized_characters": True,
        },
        "runtime": {"total_seconds": round(runtime_seconds, 6)},
        "structured_output_stability": {
            "finish_reasons": dict(budget.task_finish_reasons),
            "semantic_errors": provider.semantic_errors,
            "retry_breakdown": "NOT_SEPARATELY_OBSERVABLE",
        },
        "quality_status": {
            "major_golden_recall": (
                (
                    "SEMANTIC_RECALL_CONCERN"
                    if golden["semantic_recall_concern"]
                    else "NO_DROP_OBSERVED"
                )
                if evaluation_mode is SemanticEvaluationMode.REGRESSION
                else "NOT_APPLICABLE"
            ),
            "reader_precision": "NOT_EVALUATED",
            "false_positive_rate": "NOT_EVALUATED",
            "false_negative_rate": "NOT_EVALUATED",
            "actual_currency_cost": "NOT_EVALUATED",
        },
        "review_artifacts": {
            "candidates": str(run_directory / "candidates.jsonl"),
            "candidate_review": str(run_directory / "candidate_review.md"),
            "stories": str(run_directory / "stories.jsonl"),
            "story_review": str(run_directory / "story_review.md"),
            "semantic_review_sample": str(run_directory / "semantic_review_sample.md"),
            "summary": str(run_directory / "summary.json"),
        },
        "safety_observations": {
            "evidence_fetch_calls": fetcher.calls,
            "notify_argument": False,
            "output_root": str(run_directory),
            "pipeline_brief_completed": brief is not None,
            "whole_run_attempts": 1,
        },
        "problems": problems,
    }


def _git_sha(root: Path) -> str:
    head = root / ".git" / "HEAD"
    if not head.exists():
        return "UNKNOWN"
    value = head.read_text(encoding="utf-8").strip()
    if value.startswith("ref: "):
        ref = root / ".git" / value[5:]
        if ref.exists():
            return ref.read_text(encoding="utf-8").strip()
        packed = root / ".git" / "packed-refs"
        if packed.exists():
            suffix = " " + value[5:]
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(suffix):
                    return line.split(" ", 1)[0]
        return "UNKNOWN"
    return value


def run_semantic_evaluation(
    *,
    root: Path,
    evaluation_date: date,
    provider_kind: Literal["fake", "deepseek"],
    output_root: Path,
    evaluation_mode: SemanticEvaluationMode = SemanticEvaluationMode.REGRESSION,
    confirm_real_provider_eval: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    config: DeepSeekEvaluationConfig | None = None
    if provider_kind == "deepseek":
        if not confirm_real_provider_eval:
            raise EvaluationSafetyError(
                "Real Provider evaluation requires --confirm-real-provider-eval"
            )
        config = deepseek_configuration_from_environment()
    raw, frozen_now = _load_frozen_workload(
        root,
        evaluation_date,
        evaluation_mode=evaluation_mode,
    )
    run_directory = _safe_run_directory(root, output_root, evaluation_date)
    raw_by_id = {item.id: item for item in raw}
    pipeline = MorningRadarPipeline(root)
    budget = _budget_for_pipeline(pipeline)
    pipeline.app = pipeline.app.model_copy(update={"maximum_investigations": 0})
    if provider_kind == "deepseek":
        assert config is not None
        semantic_provider: Any = DeepSeekProvider(
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            budget=budget,
            prompt_dir=root / "prompts",
        )
        model_name = config.model
        base_host = config.base_host
        secrets = (config.api_key,)
    else:
        semantic_provider = FakeAIProvider(budget=budget)
        model_name = "FakeAIProvider"
        base_host = "NONE"
        secrets = ()
    downstream = FakeAIProvider(budget=budget)
    provider = SemanticShadowProvider(
        semantic_provider=semantic_provider,
        downstream_provider=downstream,
        budget=budget,
        secret_values=secrets,
    )
    fetcher = ForbiddenEvidenceFetcher()
    started = time.perf_counter()
    brief: DailyBrief | None = None
    trace: DailyDecisionTrace | None = None
    stories: list[Story] = []
    candidates: list[Candidate] = []
    candidate_rows: list[dict[str, Any]] = []
    story_rows: list[dict[str, Any]] = []
    violations: list[dict[str, str]] = []
    problems: list[str] = []
    status = "FAILED"
    caught: Exception | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="morning-radar-semantic-history-") as temp:
            history_root = Path(temp)
            _prepare_history(root, history_root, evaluation_date)
            brief = pipeline.run(
                dry_run=True,
                notify=False,
                offline_raw_items=raw,
                offline_provider=provider,
                offline_now=frozen_now,
                offline_evidence_fetcher=fetcher,  # type: ignore[arg-type]
                offline_output_root=run_directory,
                offline_history_root=history_root,
            )
        candidates = load_models(
            run_directory / "data/candidates" / f"{evaluation_date}.json", Candidate
        )
        stories = load_models(run_directory / "data/stories" / f"{evaluation_date}.json", Story)
        trace = load_model(
            run_directory / "data/diagnostics" / f"{evaluation_date}.json",
            DailyDecisionTrace,
        )
        dispositions = _story_dispositions(trace)
        candidate_rows = [
            _candidate_row(
                candidate,
                initial=provider.triage_inputs.get(candidate.id),
                raw_by_id=raw_by_id,
                story_result=dispositions.get(candidate.id, "NOT_ATTEMPTED"),
                model_disposition=(
                    provider.triage_drafts[candidate.id].semantic_disposition.value
                    if candidate.id in provider.triage_drafts
                    else None
                ),
            )
            for candidate in candidates
        ]
        story_rows = _story_rows(candidates, stories, provider, dispositions)
        violations = [
            {"story_id": story.id, "violation": violation}
            for story in stories
            for violation in story_evidence_integrity_violations(story)
        ]
        if fetcher.calls:
            raise EvidenceNetworkAttempted(f"Evidence fetcher was invoked {fetcher.calls} time(s)")
        if evaluation_mode is SemanticEvaluationMode.REGRESSION:
            golden = _golden_summary(candidate_rows, trace)
            if not golden["regression_passed"]:
                raise EvaluationSafetyError(
                    "DeepSeek golden candidate did not reach Semantic Triage"
                )
        if violations:
            raise EvidenceIntegrityViolation(
                f"{len(violations)} persisted Story integrity violation(s)"
            )
        status = "COMPLETED"
    except Exception as exc:
        caught = exc
        problems.append(f"{type(exc).__name__}: {_sanitize(str(exc), secrets)}")
        if not candidate_rows:
            candidate_rows = _partial_candidate_rows(provider, raw_by_id)
        status = "STOPPED"
    runtime_seconds = time.perf_counter() - started
    _write_jsonl(run_directory / "candidates.jsonl", candidate_rows)
    _write_candidate_review(run_directory / "candidate_review.md", candidate_rows)
    _write_jsonl(run_directory / "stories.jsonl", story_rows)
    _write_story_review(run_directory / "story_review.md", story_rows)
    _write_semantic_sample(run_directory / "semantic_review_sample.md", candidate_rows)
    summary = _build_summary(
        root=root,
        evaluation_date=evaluation_date,
        evaluation_mode=evaluation_mode,
        provider_kind=provider_kind,
        model_name=model_name,
        base_host=base_host,
        raw=raw,
        frozen_now=frozen_now,
        run_directory=run_directory,
        budget=budget,
        provider=provider,
        fetcher=fetcher,
        runtime_seconds=runtime_seconds,
        candidate_rows=candidate_rows,
        story_rows=story_rows,
        stories=stories,
        trace=trace,
        brief=brief,
        violations=violations,
        status=status,
        problems=problems,
    )
    write_json(run_directory / "summary.json", summary)
    if caught is not None:
        raise caught
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--date", type=date.fromisoformat, default=date(2026, 8, 22))
    parser.add_argument(
        "--mode",
        type=SemanticEvaluationMode,
        choices=tuple(SemanticEvaluationMode),
        default=SemanticEvaluationMode.REGRESSION,
    )
    parser.add_argument("--provider", choices=("fake", "deepseek"), default="fake")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-real-provider-eval", action="store_true")
    args = parser.parse_args()
    try:
        summary = run_semantic_evaluation(
            root=args.root,
            evaluation_date=args.date,
            evaluation_mode=args.mode,
            provider_kind=args.provider,
            output_root=args.output,
            confirm_real_provider_eval=args.confirm_real_provider_eval,
        )
    except Exception as exc:
        print(f"Semantic evaluation stopped: {type(exc).__name__}: {_sanitize(str(exc))}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

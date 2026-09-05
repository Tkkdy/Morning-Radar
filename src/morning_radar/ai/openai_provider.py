"""Official OpenAI Responses API adapter with structured output and budgets."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from morning_radar.ai.models import (
    BriefDraft,
    CandidateTriageBatch,
    ClassificationBatch,
    ContinuityResolution,
    ContinuityResolutionInput,
    DirectionObservation,
    MergedStoryDraft,
    StoryScore,
    TendencyEvaluationBatch,
)
from morning_radar.ai.output_validation import (
    validate_and_sanitize_brief,
    validate_core_simplified_chinese_output,
    validate_direction_evidence,
)
from morning_radar.continuity.validation import validate_continuity_resolution
from morning_radar.editorial.models import EditorialDecision, EditorialDecisionBatch
from morning_radar.models import (
    Candidate,
    RawItem,
    Signal,
    Story,
    TendencyCurrentView,
    TendencyEvidenceCluster,
)
from morning_radar.provenance import verified_source_urls_for_items


class AIConfigurationError(RuntimeError):
    pass


class AIOutputError(RuntimeError):
    pass


class AIBudgetExceeded(RuntimeError):
    pass


def _budget_stage(task: str) -> str:
    return {
        "candidate_triage": "triage",
        "construct_story": "story",
        "merge_story": "story",
        "score_story": "story",
        "evaluate_editorial": "editorial",
        "resolve_continuity": "continuity",
        "evaluate_tendencies": "tendency",
        "write_brief": "brief",
        "direction_observation": "brief",
    }.get(task, task)


@dataclass(slots=True)
class AIBudget:
    maximum_calls: int
    maximum_input_characters: int
    maximum_items: int
    calls_used: int = 0
    input_characters_used: int = 0
    network_requests_used: int = 0
    task_usage: dict[str, dict[str, int]] = field(default_factory=dict)
    task_finish_reasons: dict[str, dict[str, int]] = field(default_factory=dict)
    protected_minimums: dict[str, int] = field(default_factory=dict)
    protected_input_minimums: dict[str, int] = field(default_factory=dict)
    stage_calls: dict[str, int] = field(default_factory=dict)
    stage_input_characters: dict[str, int] = field(default_factory=dict)
    completed_stages: set[str] = field(default_factory=set)

    def available_input_characters(self, *, stage: str | None = None) -> int:
        """Return the characters currently available without consuming reservations."""
        available = self.maximum_input_characters - self.input_characters_used
        if stage is not None and self.protected_input_minimums:
            reserved_for_others = sum(
                max(
                    0,
                    minimum - self.stage_input_characters.get(other_stage, 0),
                )
                for other_stage, minimum in self.protected_input_minimums.items()
                if other_stage != stage and other_stage not in self.completed_stages
            )
            available = min(
                available,
                self.maximum_input_characters
                - self.input_characters_used
                - reserved_for_others,
            )
        return max(0, available)

    def consume(
        self,
        payload: str,
        *,
        item_count: int,
        stage: str | None = None,
    ) -> None:
        if item_count > self.maximum_items:
            raise AIBudgetExceeded(
                f"AI item limit exceeded: {item_count} > {self.maximum_items}"
            )
        if self.calls_used + 1 > self.maximum_calls:
            raise AIBudgetExceeded("AI daily call limit exceeded")
        if stage is not None and self.protected_minimums:
            reserved_for_others = sum(
                max(0, minimum - self.stage_calls.get(other_stage, 0))
                for other_stage, minimum in self.protected_minimums.items()
                if other_stage != stage and other_stage not in self.completed_stages
            )
            if self.calls_used + 1 > self.maximum_calls - reserved_for_others:
                raise AIBudgetExceeded(
                    f"AI shared pool unavailable while protecting later stages: {stage}"
                )
        payload_characters = len(payload)
        if self.input_characters_used + payload_characters > self.maximum_input_characters:
            raise AIBudgetExceeded("AI daily input character limit exceeded")
        if (
            stage is not None
            and self.protected_input_minimums
            and payload_characters > self.available_input_characters(stage=stage)
        ):
            raise AIBudgetExceeded(
                "AI character pool unavailable while protecting later stages: "
                f"{stage}"
            )
        self.calls_used += 1
        self.input_characters_used += payload_characters
        if stage is not None:
            self.stage_calls[stage] = self.stage_calls.get(stage, 0) + 1
            self.stage_input_characters[stage] = (
                self.stage_input_characters.get(stage, 0) + payload_characters
            )

    def complete_stage(self, stage: str) -> None:
        """Release an unused protected minimum into the shared pool."""
        self.completed_stages.add(stage)

    def record_network_request(self) -> None:
        """Record an actual outbound AI API request, including retries."""
        self.network_requests_used += 1

    def record_response_usage(
        self,
        task: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        reasoning_tokens: int = 0,
        finish_reason: str = "unknown",
    ) -> None:
        usage = self.task_usage.setdefault(
            task,
            {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0},
        )
        usage["prompt_tokens"] += prompt_tokens
        usage["completion_tokens"] += completion_tokens
        usage["reasoning_tokens"] += reasoning_tokens
        reasons = self.task_finish_reasons.setdefault(task, {})
        reasons[finish_reason] = reasons.get(finish_reason, 0) + 1

    def usage_run_stats(self) -> dict[str, int]:
        stats: dict[str, int] = {
            "ai_prompt_tokens": 0,
            "ai_completion_tokens": 0,
            "ai_reasoning_tokens": 0,
        }
        for task, usage in sorted(self.task_usage.items()):
            for name, value in usage.items():
                stats[f"ai_{task}_{name}"] = value
                stats[f"ai_{name}"] += value
        for task, reasons in sorted(self.task_finish_reasons.items()):
            for reason, count in sorted(reasons.items()):
                stats[f"ai_{task}_finish_{reason}"] = count
        if self.protected_minimums or self.protected_input_minimums:
            for stage, value in sorted(self.stage_calls.items()):
                stats[f"ai_stage_{stage}_calls"] = value
            for stage, value in sorted(self.stage_input_characters.items()):
                stats[f"ai_stage_{stage}_input_characters"] = value
        return stats


def _collect_urls(value: Any, field_name: str = "") -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            urls.extend(_collect_urls(child, key))
    elif isinstance(value, list):
        for child in value:
            urls.extend(_collect_urls(child, field_name))
    elif isinstance(value, str) and "url" in field_name:
        urls.append(value)
    return urls


def validate_output_urls(output: BaseModel, allowed_urls: set[str]) -> None:
    returned = _collect_urls(output.model_dump(mode="json"))
    invented = sorted(set(returned) - allowed_urls)
    if invented:
        raise AIOutputError(
            f"AI returned URL not present in verified source set: {invented[0]}"
        )


class OpenAIProvider:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        budget: AIBudget,
        prompt_dir: Path,
        client: Any | None = None,
        network_attempts: int = 3,
        timeout_seconds: float = 60,
    ) -> None:
        if not model:
            raise AIConfigurationError("OPENAI_MODEL is required for production AI")
        if not api_key:
            raise AIConfigurationError("OPENAI_API_KEY is required for production AI")
        self.model = model
        self.budget = budget
        self.prompt_dir = prompt_dir
        self.network_attempts = network_attempts
        self.client = client or OpenAI(
            api_key=api_key,
            timeout=httpx.Timeout(timeout_seconds),
            max_retries=0,
        )

    @classmethod
    def from_environment(
        cls,
        *,
        budget: AIBudget,
        prompt_dir: Path = Path("prompts"),
    ) -> OpenAIProvider:
        return cls(
            model=os.getenv("OPENAI_MODEL", ""),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            budget=budget,
            prompt_dir=prompt_dir,
        )

    def _parse[OutputT: BaseModel](
        self,
        *,
        task: str,
        schema: type[OutputT],
        payload_data: Any,
        item_count: int,
        allowed_urls: set[str],
        output_validator: Callable[[OutputT], OutputT | None] | None = None,
    ) -> OutputT:
        payload = json.dumps(payload_data, ensure_ascii=False, separators=(",", ":"))
        self.budget.consume(payload, item_count=item_count, stage=_budget_stage(task))
        instructions = (self.prompt_dir / f"{task}.md").read_text(encoding="utf-8")

        @retry(
            retry=retry_if_exception_type(
                (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
            ),
            stop=stop_after_attempt(self.network_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            reraise=True,
        )
        def invoke() -> Any:
            self.budget.record_network_request()
            return self.client.responses.parse(
                model=self.model,
                instructions=instructions,
                input=payload,
                text_format=schema,
            )

        last_error: Exception | None = None
        for _ in range(2):
            try:
                parsed = invoke().output_parsed
            except (
                APIConnectionError,
                APITimeoutError,
                RateLimitError,
                InternalServerError,
            ) as exc:
                raise AIOutputError(
                    f"OpenAI API unavailable after network retries: {type(exc).__name__}"
                ) from exc
            try:
                if parsed is None:
                    raise AIOutputError("OpenAI response contained no parsed structured output")
                validated = schema.model_validate(parsed)
                validate_output_urls(validated, allowed_urls)
                validate_core_simplified_chinese_output(validated)
                if output_validator is not None:
                    transformed = output_validator(validated)
                    if transformed is not None:
                        validated = transformed
                return validated
            except (AIOutputError, ValidationError, TypeError, ValueError) as exc:
                last_error = exc
        raise AIOutputError(f"Invalid structured AI output after retry: {last_error}")

    def triage_candidates(self, candidates: list[Candidate]) -> CandidateTriageBatch:
        candidate_ids = {candidate.id for candidate in candidates}

        def validate(output: CandidateTriageBatch) -> CandidateTriageBatch:
            returned = [candidate.candidate_id for candidate in output.candidates]
            if len(returned) != len(set(returned)) or set(returned) != candidate_ids:
                raise AIOutputError("Candidate triage must return every input exactly once")
            return output

        return self._parse(
            task="candidate_triage",
            schema=CandidateTriageBatch,
            payload_data=[candidate.model_dump(mode="json") for candidate in candidates],
            item_count=len(candidates),
            allowed_urls={
                evidence.url for candidate in candidates for evidence in candidate.evidence
            },
            output_validator=validate,
        )

    def construct_story(self, candidate: Candidate) -> MergedStoryDraft:
        return self._parse(
            task="construct_story",
            schema=MergedStoryDraft,
            payload_data=candidate.model_dump(mode="json"),
            item_count=1,
            allowed_urls={evidence.url for evidence in candidate.evidence},
        )

    def classify_items(self, items: list[RawItem]) -> ClassificationBatch:
        return self._parse(
            task="classify",
            schema=ClassificationBatch,
            payload_data=[item.model_dump(mode="json") for item in items],
            item_count=len(items),
            allowed_urls={item.url for item in items},
        )

    def merge_story(self, items: list[RawItem]) -> MergedStoryDraft:
        return self._parse(
            task="merge_story",
            schema=MergedStoryDraft,
            payload_data=[item.model_dump(mode="json") for item in items],
            item_count=len(items),
            allowed_urls=set(verified_source_urls_for_items(items)),
        )

    def score_story(self, story: Story) -> StoryScore:
        return self._parse(
            task="score_story",
            schema=StoryScore,
            payload_data=story.model_dump(mode="json"),
            item_count=1,
            allowed_urls=set(story.source_urls),
        )

    def write_brief(
        self,
        stories: list[Story],
        signals: list[Signal],
        editorial_decisions: list[EditorialDecision] | None = None,
    ) -> BriefDraft:
        return self._parse(
            task="write_brief",
            schema=BriefDraft,
            payload_data={
                "stories": [story.model_dump(mode="json") for story in stories],
                "signals": [signal.model_dump(mode="json") for signal in signals],
                "editorial_decisions": [
                    decision.model_dump(mode="json")
                    for decision in editorial_decisions or []
                ],
            },
            item_count=len(stories),
            allowed_urls={url for story in stories for url in story.source_urls},
            output_validator=lambda output: validate_and_sanitize_brief(
                output,
                stories,
                signals,
            ),
        )

    def write_direction_observation(
        self,
        signals: list[Signal],
    ) -> DirectionObservation:
        return self._parse(
            task="direction_observation",
            schema=DirectionObservation,
            payload_data=[signal.model_dump(mode="json") for signal in signals],
            item_count=len(signals),
            allowed_urls=set(),
            output_validator=lambda output: validate_direction_evidence(
                output,
                signals,
            ),
        )

    def evaluate_editorial(self, stories: list[Story]) -> EditorialDecisionBatch:
        editorial_dir = self.prompt_dir / "editorial"
        return self._parse(
            task="evaluate_editorial",
            schema=EditorialDecisionBatch,
            payload_data={
                "profile": (editorial_dir / "profile.md").read_text(encoding="utf-8"),
                "golden_cases": [
                    json.loads(line)
                    for line in (editorial_dir / "golden_cases.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ],
                "stories": [story.model_dump(mode="json") for story in stories],
            },
            item_count=len(stories),
            allowed_urls={url for story in stories for url in story.source_urls},
        )

    def resolve_continuity(
        self,
        context: ContinuityResolutionInput,
    ) -> ContinuityResolution:
        item_count = (
            len(context.relation_candidates)
            + len(context.watch_candidates)
            + len(context.prior_hypotheses)
        )
        return self._parse(
            task="resolve_continuity",
            schema=ContinuityResolution,
            payload_data=context.model_dump(mode="json"),
            item_count=item_count,
            allowed_urls=set(),
            output_validator=lambda output: validate_continuity_resolution(output, context),
        )

    def evaluate_tendencies(
        self,
        clusters: list[TendencyEvidenceCluster],
        current_views: list[TendencyCurrentView],
    ) -> TendencyEvaluationBatch:
        cluster_ids = {cluster.cluster_id for cluster in clusters}
        tendency_ids = {view.tendency_id for view in current_views}

        def validate(output: TendencyEvaluationBatch) -> TendencyEvaluationBatch:
            for decision in output.decisions:
                if decision.existing_tendency_id not in tendency_ids | {None}:
                    raise AIOutputError("Tendency output references an unknown tendency")
                if not {
                    *decision.supporting_cluster_ids,
                    *decision.counterevidence_cluster_ids,
                    *(item.cluster_id for item in decision.formation_support),
                }.issubset(cluster_ids):
                    raise AIOutputError("Tendency output references an unknown cluster")
            return output

        return self._parse(
            task="evaluate_tendencies",
            schema=TendencyEvaluationBatch,
            payload_data={
                "evidence_clusters": [cluster.model_dump(mode="json") for cluster in clusters],
                "current_views": [view.model_dump(mode="json") for view in current_views],
            },
            item_count=len(clusters),
            allowed_urls=set(),
            output_validator=validate,
        )

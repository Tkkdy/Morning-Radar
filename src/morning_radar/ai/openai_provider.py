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
    ClassificationBatch,
    ContinuityResolution,
    ContinuityResolutionInput,
    DirectionObservation,
    MergedStoryDraft,
    ResearchResolutionBatch,
    StoryScore,
    TendencyEvaluationBatch,
)
from morning_radar.ai.output_validation import (
    validate_and_sanitize_brief,
    validate_core_simplified_chinese_output,
    validate_direction_evidence,
)
from morning_radar.continuity.validation import validate_continuity_resolution
from morning_radar.models import (
    RawItem,
    ResearchCase,
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

    def consume(self, payload: str, *, item_count: int) -> None:
        if item_count > self.maximum_items:
            raise AIBudgetExceeded(
                f"AI item limit exceeded: {item_count} > {self.maximum_items}"
            )
        if self.calls_used + 1 > self.maximum_calls:
            raise AIBudgetExceeded("AI daily call limit exceeded")
        if self.input_characters_used + len(payload) > self.maximum_input_characters:
            raise AIBudgetExceeded("AI daily input character limit exceeded")
        self.calls_used += 1
        self.input_characters_used += len(payload)

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
        self.budget.consume(payload, item_count=item_count)
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

    def write_brief(self, stories: list[Story], signals: list[Signal]) -> BriefDraft:
        return self._parse(
            task="write_brief",
            schema=BriefDraft,
            payload_data={
                "stories": [story.model_dump(mode="json") for story in stories],
                "signals": [signal.model_dump(mode="json") for signal in signals],
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

    def resolve_research_cases(
        self,
        cases: list[ResearchCase],
    ) -> ResearchResolutionBatch:
        case_ids = {case.id for case in cases}

        def validate(output: ResearchResolutionBatch) -> ResearchResolutionBatch:
            if any(item.case_id not in case_ids for item in output.cases):
                raise AIOutputError("Research output references an unknown case ID")
            return output

        return self._parse(
            task="resolve_research_cases",
            schema=ResearchResolutionBatch,
            payload_data=[case.model_dump(mode="json") for case in cases],
            item_count=len(cases),
            allowed_urls={
                evidence.url
                for case in cases
                for evidence in [case.lead, *case.supporting_evidence]
            },
            output_validator=validate,
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

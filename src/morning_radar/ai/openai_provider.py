"""Official OpenAI Responses API adapter with structured output and budgets."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from openai import (
    OpenAI,
)
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from morning_radar.ai.budget import AIBudget, AIBudgetExceeded, AITaskPriority
from morning_radar.ai.errors import (
    AIAuthenticationError,
    AIBillingUnavailable,
    AIConfigurationError,
    AIOutputError,
    AIProviderUnavailable,
    AIRetryableTransportError,
    normalize_provider_error,
)
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
from morning_radar.editorial.models import EditorialDecision, EditorialDecisionBatch
from morning_radar.models import (
    RawItem,
    ResearchCase,
    Signal,
    Story,
    TendencyCurrentView,
    TendencyEvidenceCluster,
)
from morning_radar.provenance import verified_source_urls_for_items

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OpenAITaskPolicy:
    max_output_tokens: int
    priority: AITaskPriority
    max_network_attempts: int


OPENAI_TASK_POLICIES = {
    "classify": OpenAITaskPolicy(4096, AITaskPriority.CORE, 3),
    "merge_story": OpenAITaskPolicy(4096, AITaskPriority.CORE, 3),
    "score_story": OpenAITaskPolicy(2048, AITaskPriority.CORE, 3),
    "write_brief": OpenAITaskPolicy(8192, AITaskPriority.CORE, 3),
    "resolve_continuity": OpenAITaskPolicy(4096, AITaskPriority.IMPORTANT, 2),
    "direction_observation": OpenAITaskPolicy(4096, AITaskPriority.OPTIONAL, 1),
    "resolve_research_cases": OpenAITaskPolicy(4096, AITaskPriority.IMPORTANT, 2),
    "evaluate_tendencies": OpenAITaskPolicy(6000, AITaskPriority.OPTIONAL, 1),
    "evaluate_editorial": OpenAITaskPolicy(4096, AITaskPriority.EXPERIMENTAL, 1),
}


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


def _usage_value(value: Any, name: str) -> int:
    return int(getattr(value, name, 0) or 0) if value is not None else 0


def validate_output_urls(output: BaseModel, allowed_urls: set[str]) -> None:
    returned = _collect_urls(output.model_dump(mode="json"))
    invented = sorted(set(returned) - allowed_urls)
    if invented:
        raise AIOutputError(f"AI returned URL not present in verified source set: {invented[0]}")


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
        self.provider_name = "openai"
        self.circuit_open = False
        self.circuit_reason: str | None = None
        self.client = client or OpenAI(
            api_key=api_key,
            timeout=httpx.Timeout(timeout_seconds),
            max_retries=0,
            default_headers={"User-Agent": "morning-radar/0.1"},
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
        self.budget.reset_task_attempts(task)
        instructions = (self.prompt_dir / f"{task}.md").read_text(encoding="utf-8")
        policy = OPENAI_TASK_POLICIES[task]
        LOGGER.info(
            "AI task start: provider=%s model=%s task=%s max_output_tokens=%d priority=%s",
            self.provider_name,
            self.model,
            task,
            policy.max_output_tokens,
            policy.priority.value,
        )

        @retry(
            retry=retry_if_exception_type((AIRetryableTransportError,)),
            stop=stop_after_attempt(min(self.network_attempts, policy.max_network_attempts, 3)),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            reraise=True,
        )
        def invoke() -> Any:
            if self.circuit_open:
                raise AIBillingUnavailable("openai circuit is open")
            self.budget.record_network_request(
                task, maximum_task_attempts=policy.max_network_attempts
            )
            try:
                return self.client.responses.parse(
                    model=self.model,
                    instructions=instructions,
                    input=payload,
                    text_format=schema,
                    max_output_tokens=policy.max_output_tokens,
                )
            except Exception as exc:
                normalized = normalize_provider_error(exc, self.provider_name)
                if isinstance(normalized, AIBillingUnavailable):
                    self.circuit_open = True
                    self.circuit_reason = normalized.kind.value
                raise normalized from exc

        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = invoke()
                usage = getattr(response, "usage", None)
                details = getattr(usage, "output_tokens_details", None)
                self.budget.record_response_usage(
                    task,
                    prompt_tokens=_usage_value(usage, "input_tokens"),
                    completion_tokens=_usage_value(usage, "output_tokens"),
                    reasoning_tokens=_usage_value(details, "reasoning_tokens"),
                    finish_reason=str(getattr(response, "status", None) or "unknown"),
                )
                parsed = response.output_parsed
            except (AIAuthenticationError, AIBillingUnavailable, AIBudgetExceeded):
                raise
            except AIProviderUnavailable as exc:
                raise AIOutputError("OpenAI API unavailable after bounded retries") from exc
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
                    decision.model_dump(mode="json") for decision in editorial_decisions or []
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
                    .splitlines()[:4]
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

"""Official OpenAI Responses API adapter with structured output and budgets."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
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
    DirectionObservation,
    MergedStoryDraft,
    StoryScore,
)
from morning_radar.ai.output_validation import (
    validate_direction_evidence,
    validate_editorial_grounding,
    validate_simplified_chinese_output,
)
from morning_radar.models import RawItem, Signal, Story
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
        output_validator: Callable[[OutputT], None] | None = None,
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
                validate_simplified_chinese_output(validated)
                if output_validator is not None:
                    output_validator(validated)
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
            output_validator=lambda output: validate_editorial_grounding(
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

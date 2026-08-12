"""DeepSeek OpenAI-compatible Chat Completions adapter."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
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
from morning_radar.ai.openai_provider import (
    AIBudget,
    AIConfigurationError,
    AIOutputError,
    validate_output_urls,
)
from morning_radar.ai.output_validation import (
    validate_and_sanitize_brief,
    validate_core_simplified_chinese_output,
    validate_direction_evidence,
)
from morning_radar.models import RawItem, Signal, Story
from morning_radar.provenance import verified_source_urls_for_items


class DeepSeekProvider:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        budget: AIBudget,
        prompt_dir: Path,
        client: Any | None = None,
        network_attempts: int = 3,
        timeout_seconds: float = 60,
    ) -> None:
        if not model:
            raise AIConfigurationError("DEEPSEEK_MODEL is required for production AI")
        if not api_key:
            raise AIConfigurationError("DEEPSEEK_API_KEY is required for production AI")
        if not base_url:
            raise AIConfigurationError("DEEPSEEK_BASE_URL is required for production AI")
        self.model = model
        self.budget = budget
        self.prompt_dir = prompt_dir
        self.network_attempts = network_attempts
        self.client = client or OpenAI(
            api_key=api_key,
            base_url=base_url,
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
    ) -> DeepSeekProvider:
        return cls(
            model=os.getenv("DEEPSEEK_MODEL", ""),
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", ""),
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
        schema_json = json.dumps(
            schema.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system_prompt = (
            f"{instructions}\n\n"
            "Return only a valid json object matching this json schema. "
            "Do not wrap the json in Markdown fences.\n"
            f"{schema_json}"
        )

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
            return self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": payload},
                ],
                response_format={"type": "json_object"},
                max_tokens=8192,
            )

        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = invoke()
            except (
                APIConnectionError,
                APITimeoutError,
                RateLimitError,
                InternalServerError,
            ) as exc:
                raise AIOutputError(
                    f"DeepSeek API unavailable after network retries: {type(exc).__name__}"
                ) from exc
            try:
                content = response.choices[0].message.content
                if not isinstance(content, str) or not content.strip():
                    raise AIOutputError("DeepSeek response contained no JSON content")
                validated = schema.model_validate(json.loads(content))
                validate_output_urls(validated, allowed_urls)
                validate_core_simplified_chinese_output(validated)
                if output_validator is not None:
                    transformed = output_validator(validated)
                    if transformed is not None:
                        validated = transformed
                return validated
            except (
                AIOutputError,
                ValidationError,
                json.JSONDecodeError,
                IndexError,
                TypeError,
                ValueError,
            ) as exc:
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

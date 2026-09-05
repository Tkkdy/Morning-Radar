"""Qwen OpenAI-compatible Chat Completions adapter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from morning_radar.ai.budget import AIBudget
from morning_radar.ai.deepseek_provider import DeepSeekProvider, DeepSeekTaskPolicy
from morning_radar.ai.errors import AIConfigurationError


class QwenProvider(DeepSeekProvider):
    """Thin Qwen request-policy adapter sharing validated structured-output behavior."""

    provider_name = "qwen"

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
            raise AIConfigurationError("QWEN_MODEL is required for the Qwen experiment lane")
        if not api_key:
            raise AIConfigurationError("QWEN_API_KEY is required for the Qwen experiment lane")
        if not base_url:
            raise AIConfigurationError("QWEN_BASE_URL is required for the Qwen experiment lane")
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url,
            budget=budget,
            prompt_dir=prompt_dir,
            client=client,
            network_attempts=network_attempts,
            timeout_seconds=timeout_seconds,
        )

    @classmethod
    def from_environment(
        cls,
        *,
        budget: AIBudget,
        prompt_dir: Path = Path("prompts"),
    ) -> QwenProvider:
        return cls(
            model=os.getenv("QWEN_MODEL", ""),
            api_key=os.getenv("QWEN_API_KEY", ""),
            base_url=os.getenv("QWEN_BASE_URL", ""),
            budget=budget,
            prompt_dir=prompt_dir,
        )

    def _build_request(
        self,
        *,
        policy: DeepSeekTaskPolicy,
        system_prompt: str,
        payload: str,
        max_tokens: int,
        request_timeout: float | None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": payload},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            # Alibaba Model Studio exposes this Qwen extension through extra_body.
            "extra_body": {"enable_thinking": policy.thinking == "enabled"},
        }
        if request_timeout is not None:
            request["timeout"] = request_timeout
        return request

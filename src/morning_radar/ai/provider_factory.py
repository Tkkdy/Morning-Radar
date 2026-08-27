"""Explicit production AI provider selection."""

from __future__ import annotations

import os
from pathlib import Path

from morning_radar.ai.deepseek_provider import DeepSeekProvider
from morning_radar.ai.openai_provider import AIBudget, AIConfigurationError
from morning_radar.ai.provider import AIProvider
from morning_radar.ai.sensenova_provider import SenseNovaGatewayProvider


def production_provider_from_environment(
    *,
    budget: AIBudget,
    prompt_dir: Path = Path("prompts"),
) -> AIProvider:
    """Create the explicitly selected production provider without fallback."""
    provider_name = os.getenv("AI_PROVIDER", "deepseek").strip().lower()
    if provider_name == "deepseek":
        return DeepSeekProvider.from_environment(budget=budget, prompt_dir=prompt_dir)
    if provider_name == "sensenova":
        return SenseNovaGatewayProvider.from_environment(budget=budget, prompt_dir=prompt_dir)
    raise AIConfigurationError(
        "AI_PROVIDER must be one of: deepseek, sensenova "
        f"(received {provider_name or '<empty>'})"
    )

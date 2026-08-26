from pathlib import Path

import pytest

from morning_radar.ai import AIBudget, AIConfigurationError, provider_factory


def test_default_and_explicit_deepseek_select_deepseek(monkeypatch) -> None:
    expected = object()
    monkeypatch.setattr(
        provider_factory.DeepSeekProvider,
        "from_environment",
        lambda **_: expected,
    )
    monkeypatch.delenv("AI_PROVIDER", raising=False)

    assert provider_factory.production_provider_from_environment(
        budget=AIBudget(1, 1000, 1), prompt_dir=Path("prompts")
    ) is expected

    monkeypatch.setenv("AI_PROVIDER", "deepseek")
    assert provider_factory.production_provider_from_environment(
        budget=AIBudget(1, 1000, 1), prompt_dir=Path("prompts")
    ) is expected


def test_sensenova_selects_sensenova(monkeypatch) -> None:
    expected = object()
    monkeypatch.setattr(
        provider_factory.SenseNovaGatewayProvider,
        "from_environment",
        lambda **_: expected,
    )
    monkeypatch.setenv("AI_PROVIDER", "sensenova")

    assert provider_factory.production_provider_from_environment(
        budget=AIBudget(1, 1000, 1), prompt_dir=Path("prompts")
    ) is expected


def test_unknown_provider_fails_without_fallback(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "invalid")

    with pytest.raises(AIConfigurationError, match="deepseek, sensenova"):
        provider_factory.production_provider_from_environment(
            budget=AIBudget(1, 1000, 1), prompt_dir=Path("prompts")
        )

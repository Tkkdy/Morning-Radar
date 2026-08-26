import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from morning_radar.ai import (
    AIBudget,
    AIConfigurationError,
    AIOutputError,
    SenseNovaGatewayProvider,
)
from morning_radar.ai.models import ClassificationBatch, ClassifiedItem
from morning_radar.models import RawItem


def raw_item() -> RawItem:
    return RawItem(
        id="item-1",
        title="Fixture event",
        url="https://example.com/real",
        source_name="Fixture",
        source_type="fixture",
        fetched_at=datetime(2026, 7, 23, tzinfo=UTC),
    )


def classification_json() -> str:
    return ClassificationBatch(
        items=[
            ClassifiedItem(
                item_id="item-1",
                relevant=True,
                relevance_reason="match",
                important=True,
                importance_reason="material",
                category="ai_and_open_source",
            )
        ]
    ).model_dump_json()


class FakeChatCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.responses[len(self.requests) - 1]),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )


def provider(responses: list[str]) -> tuple[SenseNovaGatewayProvider, FakeChatCompletions]:
    completions = FakeChatCompletions(responses)
    configured = SenseNovaGatewayProvider(
        model="configured-model",
        api_key="test-key",
        base_url="https://token.sensenova.test/v1",
        budget=AIBudget(10, 100_000, 20),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        network_attempts=1,
    )
    return configured, completions


@pytest.mark.parametrize(
    ("kwargs", "expected_message"),
    [
        ({"model": "", "api_key": "key", "base_url": "https://example.test/v1"}, "MODEL"),
        ({"model": "model", "api_key": "", "base_url": "https://example.test/v1"}, "API_KEY"),
        ({"model": "model", "api_key": "key", "base_url": ""}, "BASE_URL"),
    ],
)
def test_missing_configuration_fails_clearly(
    kwargs: dict[str, str], expected_message: str
) -> None:
    with pytest.raises(AIConfigurationError, match=expected_message):
        SenseNovaGatewayProvider(
            **kwargs,
            budget=AIBudget(1, 1000, 1),
            prompt_dir=Path("prompts"),
        )


def test_environment_configuration_uses_documented_default_base_url(monkeypatch) -> None:
    captured: dict[str, object] = {}
    fake_client = SimpleNamespace()

    def fake_openai(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return fake_client

    monkeypatch.setenv("SENSENOVA_MODEL", "environment-model")
    monkeypatch.setenv("SENSENOVA_API_KEY", "environment-key")
    monkeypatch.delenv("SENSENOVA_BASE_URL", raising=False)
    monkeypatch.setattr("morning_radar.ai.sensenova_provider.OpenAI", fake_openai)

    configured = SenseNovaGatewayProvider.from_environment(budget=AIBudget(1, 1000, 1))

    assert configured.model == "environment-model"
    assert configured.client is fake_client
    assert captured["api_key"] == "environment-key"
    assert captured["base_url"] == "https://token.sensenova.cn/v1"


def test_structured_json_is_validated_and_request_stays_openai_compatible() -> None:
    configured, completions = provider([classification_json()])

    result = configured.classify_items([raw_item()])

    assert result.items[0].relevant is True
    request = completions.requests[0]
    assert request["model"] == "configured-model"
    assert "response_format" not in request
    assert "extra_body" not in request
    assert "reasoning_effort" not in request
    assert "test-key" not in json.dumps(request)


def test_invalid_json_is_rejected_after_the_existing_structured_output_retry() -> None:
    configured, completions = provider(["not-json", "still-not-json"])

    with pytest.raises(AIOutputError, match="after retry"):
        configured.classify_items([raw_item()])

    assert len(completions.requests) == 2

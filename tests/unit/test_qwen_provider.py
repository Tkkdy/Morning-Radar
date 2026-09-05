from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from morning_radar.ai import AIBudget, AIConfigurationError, QwenProvider
from morning_radar.ai.models import ClassificationBatch, ClassifiedItem
from morning_radar.models import RawItem


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.response


def classification_json() -> str:
    return ClassificationBatch(items=[ClassifiedItem(
        item_id="item-1", relevant=True, relevance_reason="相关。", important=True,
        importance_reason="重要。", category="ai_and_open_source"
    )]).model_dump_json()


def configured_qwen(*, usage=True):
    response = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=classification_json()), finish_reason="stop"
        )],
        usage=(SimpleNamespace(
            prompt_tokens=100, completion_tokens=40,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=25),
        ) if usage else None),
    )
    completions = FakeCompletions(response)
    provider = QwenProvider(
        model="qwen3.7-flash", api_key="test-key",
        base_url="https://dashscope.test/compatible-mode/v1",
        budget=AIBudget(4, 10_000, 10), prompt_dir=Path("prompts"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    return provider, completions


def item() -> RawItem:
    return RawItem(
        id="item-1", title="测试事件", url="https://example.com/item",
        source_name="Fixture", source_type="fixture",
        fetched_at=datetime(2026, 9, 5, tzinfo=UTC),
    )


def test_qwen_uses_qwen_thinking_mapping_without_deepseek_only_fields() -> None:
    provider, completions = configured_qwen()

    result = provider.classify_items([item()])

    assert result.items[0].relevant is True
    request = completions.requests[0]
    assert request["extra_body"] == {"enable_thinking": False}
    assert "reasoning_effort" not in request
    assert "thinking" not in request["extra_body"]


def test_qwen_semantic_task_enables_thinking() -> None:
    provider, completions = configured_qwen()

    provider._parse(
        task="write_brief", schema=ClassificationBatch, payload_data={},
        item_count=1, allowed_urls=set(),
    )

    assert completions.requests[0]["extra_body"] == {"enable_thinking": True}
    assert "reasoning_effort" not in completions.requests[0]


def test_qwen_parses_structured_response_and_records_usage() -> None:
    provider, _ = configured_qwen()

    provider.classify_items([item()])

    usage = provider.budget.usage_run_stats()
    assert usage["ai_prompt_tokens"] == 100
    assert usage["ai_completion_tokens"] == 40
    assert usage["ai_reasoning_tokens"] == 25


@pytest.mark.parametrize("name", ["QWEN_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL"])
def test_qwen_missing_configuration_fails_with_qwen_name(monkeypatch, name: str) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "key")
    monkeypatch.setenv("QWEN_BASE_URL", "https://dashscope.test")
    monkeypatch.setenv("QWEN_MODEL", "qwen3.7-flash")
    monkeypatch.delenv(name)

    with pytest.raises(AIConfigurationError, match=name):
        QwenProvider.from_environment(budget=AIBudget(1, 1000, 1))

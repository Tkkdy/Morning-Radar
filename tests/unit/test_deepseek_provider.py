import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

from morning_radar.ai import (
    AIBudget,
    AIBudgetExceeded,
    AIConfigurationError,
    AIOutputError,
    DeepSeekProvider,
)
from morning_radar.ai.models import ClassificationBatch, ClassifiedItem, MergedStoryDraft
from morning_radar.models import RawItem


def raw_item(url: str = "https://example.com/real") -> RawItem:
    return RawItem(
        id="item-1",
        title="Fixture event",
        url=url,
        source_name="Fixture",
        source_type="fixture",
        fetched_at=datetime(2026, 7, 23, tzinfo=UTC),
    )


def hn_raw_item() -> RawItem:
    return raw_item("https://example.com/real").model_copy(
        update={
            "source_name": "Hacker News",
            "source_type": "hacker_news",
            "metadata": {
                "discussion_url": "https://news.ycombinator.com/item?id=49132412",
                "original_url": "https://example.com/real",
                "community_signal": True,
            },
        }
    )


class FakeChatCompletions:
    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.calls = 0
        self.last_request: dict[str, object] = {}

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.last_request = kwargs
        result = self.results[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=result))]
        )


def provider(results: list[object], *, calls: int = 10) -> DeepSeekProvider:
    completions = FakeChatCompletions(results)
    return DeepSeekProvider(
        model="configured-test-model",
        api_key="test-key",
        base_url="https://api.deepseek.test",
        budget=AIBudget(calls, 100_000, 20),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        network_attempts=2,
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


@pytest.mark.parametrize(
    ("missing_name", "expected_message"),
    [
        ("DEEPSEEK_MODEL", "DEEPSEEK_MODEL"),
        ("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
        ("DEEPSEEK_BASE_URL", "DEEPSEEK_BASE_URL"),
    ],
)
def test_missing_environment_configuration_fails_clearly(
    monkeypatch,
    missing_name: str,
    expected_message: str,
) -> None:
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.test")
    monkeypatch.delenv(missing_name, raising=False)

    with pytest.raises(AIConfigurationError, match=expected_message):
        DeepSeekProvider.from_environment(budget=AIBudget(1, 1000, 1))


def test_environment_configuration_builds_openai_compatible_client(monkeypatch) -> None:
    captured: dict[str, object] = {}
    fake_client = SimpleNamespace()

    def fake_openai(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return fake_client

    monkeypatch.setenv("DEEPSEEK_MODEL", "environment-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.test")
    monkeypatch.setattr("morning_radar.ai.deepseek_provider.OpenAI", fake_openai)

    configured = DeepSeekProvider.from_environment(budget=AIBudget(1, 1000, 1))

    assert configured.model == "environment-model"
    assert configured.client is fake_client
    assert captured["api_key"] == "environment-key"
    assert captured["base_url"] == "https://api.deepseek.test"


def test_structured_json_result_is_validated_and_returned() -> None:
    configured = provider([classification_json()])

    result = configured.classify_items([raw_item()])

    assert result.items[0].relevant is True
    request = configured.client.chat.completions.last_request
    assert request["model"] == "configured-test-model"
    assert request["response_format"] == {"type": "json_object"}
    assert "json schema" in request["messages"][0]["content"]


def test_invalid_or_missing_json_output_retries_once() -> None:
    configured = provider(["not-json", classification_json()])

    assert configured.classify_items([raw_item()]).items[0].relevant is True
    assert configured.client.chat.completions.calls == 2
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2


def test_invalid_output_after_retry_fails_clearly() -> None:
    configured = provider(["not-json", "still-not-json"])

    with pytest.raises(AIOutputError, match="after retry"):
        configured.classify_items([raw_item()])


def test_timeout_retries_with_bounded_attempts() -> None:
    timeout = APITimeoutError(
        request=httpx.Request("POST", "https://api.deepseek.test/chat/completions")
    )
    configured = provider([timeout, classification_json()])

    assert configured.classify_items([raw_item()]).items[0].relevant is True
    assert configured.client.chat.completions.calls == 2
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2


def test_continuous_network_failure_becomes_a_degradable_ai_error() -> None:
    timeout = APITimeoutError(
        request=httpx.Request("POST", "https://api.deepseek.test/chat/completions")
    )
    configured = provider([timeout, timeout])

    with pytest.raises(AIOutputError, match="API unavailable"):
        configured.classify_items([raw_item()])

    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2


def test_ai_cannot_return_a_url_missing_from_input() -> None:
    draft = MergedStoryDraft(
        same_event=True,
        canonical_title="Event",
        category="top_stories",
        source_urls=["https://invented.example/story"],
        primary_source_url="https://invented.example/story",
    )
    output = draft.model_dump_json()
    configured = provider([output, output])

    with pytest.raises(AIOutputError, match="not present in verified source set"):
        configured.merge_story([raw_item()])


def test_deepseek_merge_accepts_verified_hn_discussion_url() -> None:
    discussion_url = "https://news.ycombinator.com/item?id=49132412"
    draft = MergedStoryDraft(
        same_event=True,
        canonical_title="HN event",
        category="developer_discussions",
        source_urls=["https://example.com/real", discussion_url],
        primary_source_url="https://example.com/real",
    )
    configured = provider([draft.model_dump_json()])

    assert configured.merge_story([hn_raw_item()]) == draft


def test_hn_item_cannot_return_another_items_discussion_url() -> None:
    item_b_url = "https://news.ycombinator.com/item?id=49130604"
    draft = MergedStoryDraft(
        same_event=True,
        canonical_title="Wrong HN event",
        category="developer_discussions",
        source_urls=[item_b_url],
        primary_source_url=item_b_url,
    )
    output = draft.model_dump_json()
    configured = provider([output, output])

    with pytest.raises(AIOutputError, match="not present in verified source set"):
        configured.merge_story([hn_raw_item()])


def test_budget_rejects_excess_candidates_before_call() -> None:
    configured = DeepSeekProvider(
        model="test",
        api_key="test",
        base_url="https://api.deepseek.test",
        budget=AIBudget(1, 1000, 0),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeChatCompletions([]))
        ),
    )

    with pytest.raises(AIBudgetExceeded, match="item limit"):
        configured.classify_items([raw_item()])


def test_user_payload_is_serialized_as_json() -> None:
    configured = provider([classification_json()])

    configured.classify_items([raw_item()])

    request = configured.client.chat.completions.last_request
    payload = request["messages"][1]["content"]
    assert json.loads(payload)[0]["url"] == "https://example.com/real"

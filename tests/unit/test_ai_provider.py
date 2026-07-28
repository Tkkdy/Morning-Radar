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
    FakeAIProvider,
    OpenAIProvider,
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


class FakeResponses:
    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.calls = 0

    def parse(self, **kwargs: object) -> SimpleNamespace:
        del kwargs
        result = self.results[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(output_parsed=result)


def provider(results: list[object], *, calls: int = 10) -> OpenAIProvider:
    return OpenAIProvider(
        model="configured-test-model",
        api_key="test-key",
        budget=AIBudget(calls, 100_000, 20),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(responses=FakeResponses(results)),
        network_attempts=2,
    )


def test_missing_environment_configuration_fails_clearly(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    with pytest.raises(AIConfigurationError, match="OPENAI_MODEL"):
        OpenAIProvider.from_environment(
            budget=AIBudget(1, 1000, 1),
        )


def test_structured_result_is_returned() -> None:
    expected = ClassificationBatch(
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
    )
    configured = provider([expected])

    assert configured.classify_items([raw_item()]) == expected


def test_invalid_or_missing_structured_output_retries_once() -> None:
    expected = ClassificationBatch(items=[])
    responses = [None, expected]
    configured = provider(responses)

    assert configured.classify_items([raw_item()]) == expected
    assert configured.client.responses.calls == 2


def test_invalid_output_after_retry_is_skipped_with_error() -> None:
    configured = provider([None, None])

    with pytest.raises(AIOutputError, match="after retry"):
        configured.classify_items([raw_item()])


def test_timeout_retries_with_bounded_attempts() -> None:
    timeout = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    expected = ClassificationBatch(items=[])
    configured = provider([timeout, expected])

    assert configured.classify_items([raw_item()]) == expected
    assert configured.client.responses.calls == 2
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
    configured = provider([draft, draft])

    with pytest.raises(AIOutputError, match="not present in input"):
        configured.merge_story([raw_item()])


def test_budget_rejects_excess_candidates_before_call() -> None:
    configured = OpenAIProvider(
        model="test",
        api_key="test",
        budget=AIBudget(1, 1000, 0),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(responses=FakeResponses([])),
    )

    with pytest.raises(AIBudgetExceeded, match="item limit"):
        configured.classify_items([raw_item()])


def test_fake_provider_works_without_api_configuration() -> None:
    result = FakeAIProvider().classify_items([raw_item()])

    assert result.items[0].relevant is True

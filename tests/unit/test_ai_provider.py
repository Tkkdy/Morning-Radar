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
from morning_radar.ai.models import (
    BriefDraft,
    ClassificationBatch,
    ClassifiedItem,
    MergedStoryDraft,
)
from morning_radar.models import RawItem, Story


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


def test_english_story_narrative_retries_without_extra_logical_call() -> None:
    url = "https://example.com/real"
    english = MergedStoryDraft(
        same_event=True,
        canonical_title="OpenAI 发布新模型",
        category="ai_and_open_source",
        facts=["The company published a detailed article about the new model today."],
        source_urls=[url],
        primary_source_url=url,
    )
    chinese = english.model_copy(update={"facts": ["公司今天发布了新模型的详细说明。"]})
    configured = provider([english, chinese])

    assert configured.merge_story([raw_item()]) == chinese
    assert configured.client.responses.calls == 2
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2


def test_repeated_english_story_narrative_fails_after_existing_retry() -> None:
    url = "https://example.com/real"
    english = MergedStoryDraft(
        same_event=True,
        canonical_title="OpenAI 发布新模型",
        category="ai_and_open_source",
        facts=["The company published a detailed article about the new model today."],
        source_urls=[url],
        primary_source_url=url,
    )
    configured = provider([english, english])

    with pytest.raises(AIOutputError, match="after retry"):
        configured.merge_story([raw_item()])

    assert configured.client.responses.calls == 2
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2


def test_editorial_grounding_violation_uses_existing_output_retry() -> None:
    source_story = Story(
        id="story-openai",
        canonical_title="OpenAI 发布新模型",
        category="ai_and_open_source",
        updated_at=datetime(2026, 7, 23, tzinfo=UTC),
        source_item_ids=["item-1"],
        source_urls=["https://example.com/real"],
        primary_source_url="https://example.com/real",
        entity_names=["OpenAI"],
        relevance_score=0.9,
        importance_score=0.8,
        novelty_score=0.8,
        credibility_score=0.9,
    )
    generic = BriefDraft(items=[], watch_next=["继续关注 AI 行业发展。"])
    grounded = BriefDraft(
        items=[],
        watch_next=["观察 OpenAI 是否公布后续开放时间表。"],
    )
    configured = provider([generic, grounded])

    assert configured.write_brief([source_story], []) == grounded
    assert configured.client.responses.calls == 2


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

    with pytest.raises(AIOutputError, match="not present in verified source set"):
        configured.merge_story([raw_item()])


def test_openai_merge_accepts_verified_hn_discussion_url() -> None:
    discussion_url = "https://news.ycombinator.com/item?id=49132412"
    draft = MergedStoryDraft(
        same_event=True,
        canonical_title="HN event",
        category="developer_discussions",
        source_urls=["https://example.com/real", discussion_url],
        primary_source_url="https://example.com/real",
    )
    configured = provider([draft])

    assert configured.merge_story([hn_raw_item()]) == draft


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

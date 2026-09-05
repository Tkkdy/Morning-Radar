import json
from datetime import UTC, date, datetime
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
from morning_radar.ai.models import (
    BriefDraft,
    ClassificationBatch,
    ClassifiedItem,
    DirectionObservation,
    GeneratedBriefItem,
    MergedStoryDraft,
)
from morning_radar.briefing import BriefLimits, generate_daily_brief
from morning_radar.models import RawItem, Signal, SignalType, Story


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


def brief_story(story_id: str, url: str) -> Story:
    return Story(
        id=story_id,
        canonical_title="OpenAI 新模型",
        category="ai_and_open_source",
        updated_at=datetime(2026, 7, 23, tzinfo=UTC),
        source_item_ids=[f"item-{story_id}"],
        source_urls=[url],
        primary_source_url=url,
        entity_names=["OpenAI"],
        relevance_score=0.9,
        importance_score=0.8,
        novelty_score=0.8,
        credibility_score=0.9,
    )


def brief_json(story_ids: list[str], source_urls: list[str]) -> str:
    return BriefDraft(
        items=[
            GeneratedBriefItem(
                story_ids=story_ids,
                section="top_stories",
                title="OpenAI 新模型",
                what_happened="OpenAI 发布了新模型。",
                why_it_matters="开发者需要评估兼容性。",
                source_urls=source_urls,
            )
        ]
    ).model_dump_json()


class FakeChatCompletions:
    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.calls = 0
        self.last_request: dict[str, object] = {}
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.last_request = kwargs
        self.requests.append(kwargs)
        result = self.results[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        if isinstance(result, SimpleNamespace):
            return result
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=result),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )


def chat_response(
    content: str,
    *,
    finish_reason: str = "stop",
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    reasoning_tokens: int | None = None,
) -> SimpleNamespace:
    usage = None
    if prompt_tokens is not None or completion_tokens is not None:
        details = (
            SimpleNamespace(reasoning_tokens=reasoning_tokens)
            if reasoning_tokens is not None
            else None
        )
        usage = SimpleNamespace(
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
            completion_tokens_details=details,
        )
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
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


@pytest.mark.parametrize(
    ("task", "max_tokens"),
    [("classify", 4096), ("merge_story", 4096), ("score_story", 2048)],
)
def test_mechanical_tasks_disable_thinking_and_use_small_output_caps(
    task: str,
    max_tokens: int,
) -> None:
    configured = provider([classification_json()])

    configured._parse(
        task=task,
        schema=ClassificationBatch,
        payload_data={},
        item_count=1,
        allowed_urls=set(),
    )

    request = configured.client.chat.completions.last_request
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in request
    assert request["max_tokens"] == max_tokens


@pytest.mark.parametrize(
    ("task", "max_tokens", "effort"),
    [
        ("write_brief", 8192, "high"),
        ("resolve_continuity", 4096, "medium"),
        ("direction_observation", 4096, "medium"),
        ("resolve_research_cases", 4096, "medium"),
        ("evaluate_tendencies", 6000, "medium"),
        ("evaluate_editorial", 4096, "medium"),
    ],
)
def test_semantic_tasks_use_bounded_policy(
    task: str,
    max_tokens: int,
    effort: str,
) -> None:
    configured = provider([classification_json()])

    configured._parse(
        task=task,
        schema=ClassificationBatch,
        payload_data={},
        item_count=1,
        allowed_urls=set(),
    )

    request = configured.client.chat.completions.last_request
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert request["reasoning_effort"] == effort
    assert request["max_tokens"] == max_tokens


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


def test_direction_evidence_violation_retries_with_network_counting() -> None:
    signal = Signal(
        id="signal-1",
        signal_type=SignalType.TOPIC_HEATING,
        topic="ai_models",
        window_days=3,
        supporting_story_ids=["story-1", "story-2"],
        supporting_source_count=2,
        supporting_company_count=1,
        strength=0.8,
        explanation="模型方向连续出现。",
        created_at=datetime(2026, 7, 23, tzinfo=UTC),
        updated_at=datetime(2026, 7, 23, tzinfo=UTC),
    )
    invalid = DirectionObservation(
        observation="模型方向获得更多证据。",
        evidence_story_ids=["story-1", "unknown-story"],
    )
    valid = invalid.model_copy(update={"evidence_story_ids": ["story-1", "story-2"]})
    configured = provider([invalid.model_dump_json(), valid.model_dump_json()])

    with pytest.raises(AIBudgetExceeded, match="attempt limit"):
        configured.write_direction_observation([signal])
    assert configured.client.chat.completions.calls == 1
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 1


def test_invalid_optional_brief_extensions_are_dropped_without_retry(caplog) -> None:
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
    draft = BriefDraft(
        items=[],
        watch_next=["继续关注 AI 行业发展。"],
        cognitive_extension="OpenAI 将改变所有现有 API 集成。",
    )
    configured = provider([draft.model_dump_json()])

    assert configured.write_brief([source_story], []) == BriefDraft(items=[])
    assert configured.client.chat.completions.calls == 1
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 1
    assert "watch_next=grounding:1" in caplog.text
    assert "cognitive_extension=question_contract" in caplog.text


def test_unknown_brief_story_id_retries_then_accepts_valid_output(caplog) -> None:
    source_story = brief_story("story-openai", "https://example.com/openai")
    invalid = brief_json(["invented-story"], source_story.source_urls)
    valid = brief_json([source_story.id], source_story.source_urls)
    configured = provider([invalid, valid])

    result = configured.write_brief([source_story], [])

    assert result.items[0].story_ids == [source_story.id]
    assert configured.client.chat.completions.calls == 2
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2
    assert "unknown Story IDs: invented-story" in caplog.text


def test_write_brief_malformed_json_retry_regenerates_complete_output(caplog) -> None:
    source_story = brief_story("story-openai", "https://example.com/openai")
    configured = provider(
        [
            '{"items":[{"story_ids":["story-openai"],"title":"unterminated',
            brief_json([source_story.id], source_story.source_urls),
        ]
    )

    result = configured.write_brief([source_story], [])

    assert result.items[0].story_ids == [source_story.id]
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2
    requests = configured.client.chat.completions.requests
    assert "previous structured response was invalid" not in requests[0]["messages"][0]["content"]
    assert "previous structured response was invalid" in requests[1]["messages"][0]["content"]
    assert "task=write_brief attempt=1 error_type=JSONDecodeError" in caplog.text


def test_write_brief_length_finish_reason_retries_with_bounded_larger_cap() -> None:
    source_story = brief_story("story-openai", "https://example.com/openai")
    configured = provider(
        [
            chat_response("{truncated", finish_reason="length"),
            chat_response(brief_json([source_story.id], source_story.source_urls)),
        ]
    )

    result = configured.write_brief([source_story], [])

    assert result.items[0].story_ids == [source_story.id]
    requests = configured.client.chat.completions.requests
    assert [request["max_tokens"] for request in requests] == [8192, 8192]
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2


def test_write_brief_repeated_length_finish_reason_falls_back() -> None:
    source_story = brief_story("story-openai", "https://example.com/openai")
    configured = provider(
        [
            chat_response("{truncated", finish_reason="length"),
            chat_response("{still-truncated", finish_reason="length"),
        ]
    )

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=datetime(2026, 7, 23, tzinfo=UTC),
        timezone="Asia/Singapore",
        stories=[source_story],
        signals=[],
        provider=configured,
        limits=BriefLimits(maximum_items=3),
        enabled_sections={},
        run_stats={},
    )

    assert result.run_stats["ai_brief_fallback"] is True
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2


def test_deepseek_usage_records_reasoning_tokens_and_finish_reason() -> None:
    configured = provider(
        [
            chat_response(
                classification_json(),
                prompt_tokens=120,
                completion_tokens=45,
                reasoning_tokens=30,
            )
        ]
    )

    configured.classify_items([raw_item()])

    assert configured.budget.usage_run_stats() == {
        "ai_completion_tokens": 45,
        "ai_classify_completion_tokens": 45,
        "ai_classify_prompt_tokens": 120,
        "ai_classify_reasoning_tokens": 30,
        "ai_classify_finish_stop": 1,
        "ai_prompt_tokens": 120,
        "ai_reasoning_tokens": 30,
        "prompt_tokens": 120,
        "completion_tokens": 45,
        "reasoning_tokens": 30,
        "logical_ai_tasks": 1,
        "network_ai_requests": 1,
    }


def test_deepseek_missing_usage_is_a_zero_safe_default() -> None:
    configured = provider([chat_response(classification_json())])

    configured.classify_items([raw_item()])

    assert configured.budget.usage_run_stats()["ai_classify_reasoning_tokens"] == 0


def test_write_brief_repeated_malformed_json_keeps_graceful_fallback() -> None:
    source_story = brief_story("story-openai", "https://example.com/openai")
    configured = provider(["{not-json", '{"still":"unterminated'])

    result = generate_daily_brief(
        brief_date=date(2026, 7, 23),
        generated_at=datetime(2026, 7, 23, tzinfo=UTC),
        timezone="Asia/Singapore",
        stories=[source_story],
        signals=[],
        provider=configured,
        limits=BriefLimits(maximum_items=3),
        enabled_sections={},
        run_stats={},
    )

    assert result.run_stats["ai_brief_fallback"] is True
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2


def test_write_brief_schema_violation_is_not_repaired_or_accepted() -> None:
    source_story = brief_story("story-openai", "https://example.com/openai")
    configured = provider(['{"items":"invalid"}', '{"items":"invalid"}'])

    with pytest.raises(AIOutputError, match="validation error"):
        configured.write_brief([source_story], [])

    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2


def test_repeated_unknown_brief_story_id_becomes_ai_output_error() -> None:
    source_story = brief_story("story-openai", "https://example.com/openai")
    invalid = brief_json(["invented-story"], source_story.source_urls)
    configured = provider([invalid, invalid])

    with pytest.raises(AIOutputError, match="unknown Story IDs: invented-story"):
        configured.write_brief([source_story], [])

    assert configured.client.chat.completions.calls == 2
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2


def test_brief_url_must_belong_to_the_item_referenced_stories() -> None:
    first = brief_story("story-openai", "https://example.com/openai")
    second = brief_story("story-claude", "https://example.com/claude")
    invalid = brief_json([first.id], second.source_urls)
    configured = provider([invalid, invalid])

    with pytest.raises(AIOutputError, match="source URLs do not match its Story IDs"):
        configured.write_brief([first, second], [])


def test_valid_multi_story_brief_item_passes_reference_validation() -> None:
    first = brief_story("story-openai", "https://example.com/openai")
    second = brief_story("story-claude", "https://example.com/claude")
    valid = brief_json([first.id, second.id], [*first.source_urls, *second.source_urls])
    configured = provider([valid])

    result = configured.write_brief([first, second], [])

    assert result.items[0].story_ids == [first.id, second.id]
    assert result.items[0].source_urls == [*first.source_urls, *second.source_urls]
    assert configured.client.chat.completions.calls == 1


def test_deepseek_core_brief_url_violation_remains_hard() -> None:
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
    invalid = BriefDraft(
        items=[
            GeneratedBriefItem(
                story_ids=[source_story.id],
                section="top_stories",
                title="OpenAI 发布新模型",
                what_happened="OpenAI 发布了新模型。",
                why_it_matters="开发者需要评估 API 兼容性。",
                source_urls=["https://invented.example/brief"],
            )
        ]
    ).model_dump_json()
    configured = provider([invalid, invalid])

    with pytest.raises(AIOutputError, match="not present in verified source set"):
        configured.write_brief([source_story], [])

    assert configured.client.chat.completions.calls == 2
    assert configured.budget.calls_used == 1
    assert configured.budget.network_requests_used == 2


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
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeChatCompletions([]))),
    )

    with pytest.raises(AIBudgetExceeded, match="item limit"):
        configured.classify_items([raw_item()])


def test_user_payload_is_serialized_as_json() -> None:
    configured = provider([classification_json()])

    configured.classify_items([raw_item()])

    request = configured.client.chat.completions.last_request
    payload = request["messages"][1]["content"]
    assert json.loads(payload)[0]["url"] == "https://example.com/real"

"""Offline provider-contract regressions for the R1 personal pilot."""
import copy
import json
from pathlib import Path

import httpx
import pytest
from openai import OpenAI

from morning_radar.ai import AIBudget, AIOutputError, DeepSeekProvider
from morning_radar.ai.candidate_classification import (
    CandidateClassificationBatch,
    StrictClassificationBatch,
)
from morning_radar.ai.models import StoryScore
from morning_radar.ai.usage import UsageRecorder
from morning_radar.release.archive import archive_files

CAPTURED = json.loads(Path("tests/fixtures/structured_output/score-responses.json").read_text(
    encoding="utf-8"
))


def provider_for(tmp_path, content, *, finish="stop"):
    response = copy.deepcopy(CAPTURED[0])
    response["choices"][0]["message"]["content"] = content
    response["choices"][0]["finish_reason"] = finish
    calls = []

    def send(request):
        calls.append(request)
        return httpx.Response(200, json=response)

    client = OpenAI(api_key="offline-test-secret", base_url="https://example.test",
                    max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(send)))
    provider = DeepSeekProvider(
        api_key="offline-test-secret", model="test", client=client,
        base_url="https://example.test", prompt_dir=Path("prompts"),
        budget=AIBudget(1, 100000, 4, 1),
    )
    provider.usage_recorder = UsageRecorder(tmp_path, business_date="2026-10-14", module="offline")
    return provider, client, calls


@pytest.mark.parametrize("schema", [CandidateClassificationBatch, StrictClassificationBatch])
def test_classification_wire_preserves_task_contract_and_sibling_isolation(tmp_path, schema):
    fixture = json.loads(Path("tests/fixtures/real_acceptance/candidate-review-01.json").read_text(
        encoding="utf-8"
    ))
    row = json.loads(fixture["choices"][0]["message"]["content"])["items"][0]
    row = {k: v for k, v in row.items() if k != "title"}
    raw = json.dumps({"items": [
        {**row, "item_id": "valid"},
        {**row, "item_id": "echo", "title": "source title"},
        {**row, "item_id": "object", "title": {"not": "a string"}},
        {**row, "item_id": "extra", "score": 1},
    ]}, ensure_ascii=False)
    provider, client, calls = provider_for(tmp_path, raw)
    try:
        batch = provider._parse(task="classify", schema=schema, payload_data={}, item_count=4,
                                allowed_urls=set(), maximum_structured_attempts=1)
        expected = ["valid", "echo"] if schema is CandidateClassificationBatch else ["valid"]
        assert [row.item_id for row in batch.items] == expected
        assert provider.last_output_adaptation["raw_response"] == raw
        assert provider.last_output_adaptation["annotations"] == []
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize("change", [
    {"decision": "approved"},
    {"relevance_score": "0.7"},
    {"importance_score": 1.1},
])
def test_score_adapter_does_not_accept_unknown_business_or_invalid_values(tmp_path, change):
    value = json.loads(CAPTURED[0]["choices"][0]["message"]["content"])
    value.update(change)
    provider, client, calls = provider_for(tmp_path, json.dumps(value, ensure_ascii=False))
    try:
        with pytest.raises(AIOutputError):
            provider._parse(task="score_story", schema=StoryScore, payload_data={}, item_count=1,
                            allowed_urls=set(), maximum_structured_attempts=1)
        assert len(calls) == 1
    finally:
        client.close()


def test_truncation_keeps_private_original_and_archivable_safe_receipt(tmp_path):
    raw = '{"relevance_score": 0.7, "explanation": "PRIVATE_UNFINISHED_CLAIM'
    provider, client, calls = provider_for(tmp_path, raw, finish="length")
    try:
        with pytest.raises(AIOutputError, match="truncated"):
            provider._parse(task="score_story", schema=StoryScore, payload_data={}, item_count=1,
                            allowed_urls=set(), maximum_structured_attempts=1)
        originals = list((tmp_path / "data/ai-responses").rglob("*.json"))
        assert len(originals) == 1
        audit = json.loads(originals[0].read_text(encoding="utf-8"))
        assert audit["raw_response"] == raw
        assert audit["finish_reason"] == "length"
        assert audit["state"] == "rejected"
        archived = archive_files(tmp_path)
        assert any(p.startswith("data/health/") for p in archived)
        assert not any(p.startswith("data/ai-responses/") for p in archived)
        public_text = "\n".join((tmp_path / p).read_text(encoding="utf-8") for p in archived)
        assert "PRIVATE_UNFINISHED_CLAIM" not in public_text
        assert "offline-test-secret" not in public_text
        assert "offline-test-secret" not in originals[0].read_text(encoding="utf-8")
        assert audit["raw_sha256"] in public_text
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize("valid", [False, True])
def test_openai_sdk_keeps_returned_text_and_observed_usage(tmp_path, valid):
    from morning_radar.ai.openai_provider import OpenAIProvider

    raw = '{"importance_score": 0.5, "explanation": "PRIVATE_MISSING_REQUIRED_SCORES"}'
    if valid:
        raw = json.dumps({"relevance_score": 0.7, "importance_score": 0.5,
                          "novelty_score": 0.4, "credibility_score": 0.8,
                          "explanation": "材料中的评分说明。"})
    response = {
        "id": "resp_offline", "object": "response", "created_at": 1,
        "status": "completed", "model": "test", "error": None, "incomplete_details": None,
        "instructions": None, "metadata": {}, "parallel_tool_calls": False,
        "temperature": 1, "tool_choice": "auto", "tools": [], "top_p": 1,
        "output": [{"id": "msg_offline", "type": "message", "role": "assistant",
                    "status": "completed", "content": [
                        {"type": "output_text", "text": raw, "annotations": []}]}],
        "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20,
                  "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}},
    }
    calls = []

    def send(request):
        calls.append(request)
        return httpx.Response(200, json=response)

    client = OpenAI(api_key="offline-test-secret", base_url="https://example.test",
                    max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(send)))
    provider = OpenAIProvider(api_key="offline-test-secret", model="test", client=client,
                              prompt_dir=Path("prompts"), budget=AIBudget(1, 100000, 1, 1))
    provider.usage_recorder = UsageRecorder(tmp_path, business_date="2026-10-14", module="offline")
    try:
        if valid:
            result = provider._parse(task="score_story", schema=StoryScore, payload_data={},
                                     item_count=1, allowed_urls=set(),
                                     maximum_structured_attempts=1)
            assert result.model_dump() == json.loads(raw)
        else:
            with pytest.raises(AIOutputError):
                provider._parse(
                    task="score_story", schema=StoryScore, payload_data={}, item_count=1,
                    allowed_urls=set(), maximum_structured_attempts=1,
                )
        audit = json.loads(next((tmp_path / "data/ai-responses").rglob("*.json")).read_text(
            encoding="utf-8"))
        assert audit["raw_response"] == raw
        assert audit["state"] == ("business_validated" if valid else "rejected")
        if not valid:
            assert audit["error_type"] == "ValidationError"
            assert audit["json_valid"] is None  # SDK rejected before our JSON check.
        usage = json.loads(next((tmp_path / "data/usage").rglob("*.json")).read_text(
            encoding="utf-8"))
        assert usage["state"] == "response_received"
        assert usage["usage"]["prompt_tokens"] == 12
        assert len(calls) == 1
    finally:
        client.close()


def test_concurrent_responses_keep_distinct_physical_call_records(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    response = copy.deepcopy(CAPTURED[0])
    raw = response["choices"][0]["message"]["content"]

    def send(request):
        barrier.wait(timeout=5)
        return httpx.Response(200, json=response)

    client = OpenAI(api_key="offline-test-secret", base_url="https://example.test",
                    max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(send)))
    provider = DeepSeekProvider(
        api_key="offline-test-secret", model="test", client=client,
        base_url="https://example.test", prompt_dir=Path("prompts"),
        budget=AIBudget(2, 200000, 2, 2),
    )
    provider.usage_recorder = UsageRecorder(tmp_path, business_date="2026-10-14", module="offline")

    def parse():
        return provider._parse(task="score_story", schema=StoryScore, payload_data={},
                               item_count=1, allowed_urls=set(), maximum_structured_attempts=1)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(parse) for _ in range(2)]
            for future in futures:
                assert future.result().relevance_score == 0.7
        usage = {p.stem for p in (tmp_path / "data/usage").rglob("*.json")}
        originals = list((tmp_path / "data/ai-responses").rglob("*.json"))
        assert len(usage) == 2 and {p.stem for p in originals} == usage
        for original in originals:
            audit = json.loads(original.read_text(encoding="utf-8"))
            assert audit["physical_call_id"] == original.stem
            assert audit["raw_response"] == raw
            assert audit["state"] == "business_validated"
    finally:
        client.close()

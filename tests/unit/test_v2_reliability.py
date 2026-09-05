from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from morning_radar.ai import AIBillingUnavailable, AIBudget
from morning_radar.ai.deepseek_provider import DeepSeekProvider
from morning_radar.ai.models import (
    ClassificationBatch,
    ClassifiedItem,
    ResolvedJudgementUpdateDraft,
    ResolvedRelationDraft,
    ResolvedWatchMatchDraft,
)
from morning_radar.collectors.http import HttpClient
from morning_radar.continuity.deep_review import scan_deep_review_triggers
from morning_radar.editorial.models import EditorialDecision
from morning_radar.evaluation import stable_label_mapping
from morning_radar.models import JudgementUpdateKind, StoryOccurrenceRef


class _PaymentRequired(Exception):
    status_code = 402


class _FailingCompletions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        del kwargs
        self.calls += 1
        raise _PaymentRequired("Insufficient Balance; secret=must-not-be-logged")


def test_402_opens_circuit_and_second_call_never_reaches_network() -> None:
    completions = _FailingCompletions()
    provider = DeepSeekProvider(
        model="deepseek-v4-flash",
        api_key="test-secret",
        base_url="https://example.invalid",
        budget=AIBudget(5, 10_000, 10),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    batch = ClassificationBatch(
        items=[
            ClassifiedItem(
                item_id="x",
                relevant=True,
                relevance_reason="相关。",
                important=False,
                importance_reason="一般。",
                category="ai_and_open_source",
            )
        ]
    )
    for _ in range(2):
        with pytest.raises(AIBillingUnavailable):
            provider._parse(
                task="classify",
                schema=ClassificationBatch,
                payload_data=batch.model_dump(mode="json"),
                item_count=1,
                allowed_urls=set(),
            )
    assert provider.circuit_open is True
    assert provider.circuit_reason == "billing_unavailable"
    assert completions.calls == 1
    assert provider.budget.network_requests_used == 1


def test_global_network_cap_is_hard_and_does_not_fabricate_usage() -> None:
    budget = AIBudget(10, 10_000, 10, maximum_network_requests=1)
    budget.record_network_request("research", maximum_task_attempts=2)
    with pytest.raises(Exception, match="global network request limit"):
        budget.record_network_request("brief", maximum_task_attempts=2)
    assert budget.network_requests_used == 1
    assert budget.usage_run_stats()["ai_prompt_tokens"] == 0


def test_http_304_is_returned_as_normal_response() -> None:
    response = httpx.Response(304, request=httpx.Request("GET", "https://example.com/feed"))
    client = SimpleNamespace(headers={}, get=lambda *args, **kwargs: response)
    assert HttpClient(client=client, attempts=1).get("https://example.com/feed").status_code == 304


def test_editorial_runtime_schema_is_lean() -> None:
    fields = EditorialDecision.model_json_schema()["properties"]
    assert set(fields) == {
        "story_id",
        "placement",
        "reader_value",
        "evidence_value",
        "fact_status",
        "retain_for_trends",
        "trend_links",
        "reason",
        "support_for_story_id",
    }


def test_negative_relation_and_watch_are_sparse() -> None:
    previous = StoryOccurrenceRef(date=date(2026, 8, 1), story_id="old")
    current = StoryOccurrenceRef(date=date(2026, 8, 2), story_id="new")
    assert ResolvedRelationDraft(
        confirmed=False,
        previous_story=previous,
        current_story=current,
        reason_code="NO_DIRECT_FOLLOW_UP",
    ).rationale is None
    assert ResolvedWatchMatchDraft(watch_id="watch", matched=False).rationale is None


def test_v2_judgement_output_cannot_write_supported() -> None:
    with pytest.raises(ValidationError):
        ResolvedJudgementUpdateDraft(
            prior_judgement_id="prior",
            update_kind=JudgementUpdateKind.SUPPORTED,
            claim="判断仍然成立。",
            rationale="没有认知变化。",
            evidence_refs=[],
        )


def test_deep_review_no_judgements_has_zero_triggers() -> None:
    assert scan_deep_review_triggers(
        current_date=date(2026, 8, 2), judgements={}, story_memory=[]
    ) == []


def test_ab_labels_are_stable_per_date_but_not_permanently_bound() -> None:
    start = date(2026, 8, 1)
    mappings = [stable_label_mapping(start + timedelta(days=value)) for value in range(14)]
    assert stable_label_mapping(start) == stable_label_mapping(start)
    assert len({mapping["A"] for mapping in mappings}) == 2

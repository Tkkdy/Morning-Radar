import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from morning_radar.ai import AIBudget, DeepSeekProvider
from morning_radar.ai.candidate_classification import (
    CandidateClassificationBatch,
    StrictClassificationBatch,
    validate_candidate_ids,
)
from morning_radar.ai.models import ClassificationBatch
from morning_radar.ai.openai_provider import OpenAIProvider
from morning_radar.models import RawItem

FIXTURE = Path("tests/fixtures/real_acceptance/candidate-review-01.json")


def real_payload():
    response = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return json.loads(response["choices"][0]["message"]["content"])


def test_real_candidate_batch_recovers_all_twenty_without_changing_business_fields():
    original = real_payload()
    before = copy.deepcopy(original)
    result = CandidateClassificationBatch.model_validate(original)
    assert len(result.items) == 20
    assert len(result._candidate_diagnostics) == 18
    assert all(d["reason"] == "echo_title_ignored" for d in result._candidate_diagnostics)
    assert result.model_dump() == {
        "items": [{k: v for k, v in row.items() if k != "title"} for row in original["items"]]
    }
    assert original == before
    with pytest.raises(ValidationError):
        ClassificationBatch.model_validate(original)


def test_invalid_business_fields_duplicate_and_unknown_ids_keep_valid_siblings():
    row = {k: v for k, v in real_payload()["items"][0].items() if k != "title"}
    payload = {"items": [
        {**row, "item_id": "valid", "title": "ignored"},
        {**row, "item_id": "missing", "important": None},
        {**row, "item_id": "coercion", "relevant": "true"},
        {**row, "item_id": "extra", "score": 1},
        {**row, "item_id": "duplicate"},
        {**row, "item_id": "duplicate", "important": None},
        {**row, "item_id": "unknown"},
        {**row, "item_id": "bad-title", "title": {"nested": "not a string echo"}},
    ]}
    batch = CandidateClassificationBatch.model_validate(payload)
    result = validate_candidate_ids(
        batch, {"valid", "missing", "coercion", "extra", "duplicate", "bad-title"}
    )
    assert [i.item_id for i in result.items] == ["valid"]
    reasons = {d["reason"] for d in result._candidate_diagnostics}
    assert reasons == {"echo_title_ignored", "invalid_item", "duplicate_id", "unknown_id"}
    with pytest.raises(ValidationError):
        CandidateClassificationBatch.model_validate({**payload, "unexpected": True})


@pytest.mark.parametrize("kind", ["deepseek", "openai"])
def test_providers_use_candidate_adapter_only_for_metadata_review(kind):
    payload = real_payload()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if kind == "deepseek":
            message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
            return SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="stop", message=message)], usage=None
            )
        return SimpleNamespace(
            output_parsed=kwargs["text_format"].model_validate(payload), usage=None
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                             responses=SimpleNamespace(parse=create))
    cls = DeepSeekProvider if kind == "deepseek" else OpenAIProvider
    kwargs = {"base_url": "https://example.test"} if kind == "deepseek" else {}
    provider = cls(model="test", api_key="test", budget=AIBudget(5, 100000, 40),
                   prompt_dir=Path("prompts"), client=client, **kwargs)
    items = [RawItem(id=row["item_id"], title="metadata only", url="https://example.test/item",
                     source_name="Fixture", source_type="fixture", fetched_at=datetime.now(UTC),
                     metadata={"review_only": True}) for row in payload["items"][:-1]]
    output = provider.classify_items(items)
    assert len(output.items) == 19  # Last returned ID was not in this request.
    assert len(calls) == 1
    assert any(x["reason"] == "unknown_id" for x in output._candidate_diagnostics)
    seen = []
    provider._parse = lambda **kw: seen.append(kw)
    provider.classify_items([items[0].model_copy(update={"metadata": {}})])
    assert seen[0]["schema"] is StrictClassificationBatch
    assert callable(seen[0]["output_validator"])


def test_full_classification_missing_fields_does_not_discard_valid_sibling():
    row = {k: v for k, v in real_payload()["items"][0].items() if k != "title"}
    broken = {k: v for k, v in row.items() if k not in {"important", "relevance_reason"}}
    original = {"items": [row, {**broken, "item_id": "broken"},
                           {**row, "item_id": "echo", "title": "not accepted in deep review"}]}
    before = copy.deepcopy(original)
    result = StrictClassificationBatch.model_validate(original)
    assert result.model_dump() == {"items": [row]}
    assert {d["item_id"] for d in result._candidate_diagnostics} == {"broken", "echo"}
    assert original == before

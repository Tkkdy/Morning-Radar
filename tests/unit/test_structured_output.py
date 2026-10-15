"""Replay preserved real JSON; test structure adaptation, never infer semantic quality."""
import copy
import json
from pathlib import Path

import httpx
import pytest
from openai import OpenAI

from morning_radar.ai import AIBudget, AIOutputError, DeepSeekProvider
from morning_radar.ai.models import StoryScore
from morning_radar.ai.structured_output import separate_annotations
from morning_radar.ai.usage import UsageRecorder
from morning_radar.tendencies.grounded_review import ReviewOutput, validate_assessment

FIXTURE = Path("tests/fixtures/structured_output")
RESPONSES = json.loads((FIXTURE / "score-responses.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("response", RESPONSES)
def test_real_score_annotations_are_audited_without_changing_business_values(tmp_path, response):
    original = response["choices"][0]["message"]["content"]
    calls = []

    def send(request):
        calls.append(request)
        return httpx.Response(200, json=response)

    client = OpenAI(api_key="test", base_url="https://example.org", max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(send)))
    provider = DeepSeekProvider(api_key="test", model="test", client=client,
                                base_url="https://example.org", prompt_dir=Path("prompts"),
                                budget=AIBudget(1, 100000, 1, 1))
    provider.usage_recorder = UsageRecorder(tmp_path, business_date="2026-10-14", module="test")
    try:
        result = provider._parse(task="score_story", schema=StoryScore, payload_data={},
                                 item_count=1, allowed_urls=set(), maximum_structured_attempts=1)
        original_values = json.loads(original)
        assert result.model_dump(mode="json") == {
            key: value for key, value in original_values.items() if key in StoryScore.model_fields
        }
        assert len(calls) == 1
        audit = json.loads(Path(provider.last_output_adaptation["audit_path"]).read_text())
        assert audit["raw_response"] == original
        assert audit["annotations"] == [
            {"path": "/" + key, "value": value}
            for key, value in original_values.items() if key not in StoryScore.model_fields
        ]
        assert json.loads(response["choices"][0]["message"]["content"]) == original_values
    finally:
        client.close()


def test_nested_review_annotations_do_not_override_verdict_consistency():
    fixture = json.loads((FIXTURE / "review-annotations.json").read_text(encoding="utf-8"))
    raw = json.loads(fixture["raw_content"])
    # This historical fixture demonstrates explicit projection only. Production
    # Tendency tasks no longer get a generic undeclared-field exemption.
    allowed = frozenset(
        f"/assessments/0/findings/{i}/evidence_id_missing"
        for i in range(len(raw["assessments"][0]["findings"]))
    )
    projected, notes = separate_annotations(
        raw, ReviewOutput.model_json_schema(), allowed_paths=allowed,
    )
    assert len(notes) == 4
    assert all(note["path"].startswith("/assessments/0/findings/") for note in notes)
    assessment = validate_assessment(projected["assessments"][0], fixture["request"])
    assert assessment.verdict == "approved"
    contradicted = copy.deepcopy(projected["assessments"][0])
    contradicted["findings"][0]["category"] = "unsupported_fact"
    with pytest.raises(ValueError, match="no blockers"):
        validate_assessment(contradicted, fixture["request"])
    assert raw == json.loads(fixture["raw_content"])


def test_missing_business_field_is_not_repaired_by_annotation_adapter():
    response = copy.deepcopy(RESPONSES[0])
    raw = json.loads(response["choices"][0]["message"]["content"])
    raw.pop("relevance_score")
    response["choices"][0]["message"]["content"] = json.dumps(raw)
    client = OpenAI(api_key="test", base_url="https://example.org", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response))))
    provider = DeepSeekProvider(api_key="test", model="test", client=client,
                                base_url="https://example.org", prompt_dir=Path("prompts"),
                                budget=AIBudget(1, 100000, 1, 1))
    try:
        with pytest.raises(AIOutputError, match="relevance_score"):
            provider._parse(task="score_story", schema=StoryScore, payload_data={},
                            item_count=1, allowed_urls=set(), maximum_structured_attempts=1)
        assert provider.last_output_adaptation["raw_response"] == json.dumps(raw)
    finally:
        client.close()

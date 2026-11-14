"""Real failure replay and synthetic boundary responses; no semantic accuracy claim."""
import copy
import hashlib
import json
import socket
from datetime import datetime
from pathlib import Path

import pytest
from test_grounded_review import assessment, provider

from morning_radar.ai import AIOutputError
from morning_radar.tendencies.core_models import CoreOutput, Revision
from morning_radar.tendencies.core_store import CoreStore
from morning_radar.tendencies.grounded_review import candidate, review_candidates, review_version

FIXTURE = Path("tests/fixtures/tendency_closeout")
RAW = (FIXTURE / "response.raw").read_bytes()
RESPONSE = json.loads(RAW)
CONTEXT = json.loads((FIXTURE / "context.json").read_text(encoding="utf-8"))
NEGATIVE = json.loads((FIXTURE / "negative-candidate.json").read_text(encoding="utf-8"))
NOW = datetime.fromisoformat(CONTEXT["as_of"])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("No real network")
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.mark.parametrize("method", ["discover_hypotheses", "calibrate_hypotheses"])
def test_original_missing_outcome_is_rejected_with_root_schema_on_wire(method):
    wires = []
    active = provider(None, discovery=RESPONSE, wires=wires)
    with pytest.raises(AIOutputError):
        getattr(active, method)(CONTEXT)
    assert len(wires) == 1
    system = wires[0]["messages"][0]["content"]
    assert json.loads(system.split("Do not wrap the json in Markdown fences.\n")[-1]) == (
        CoreOutput.model_json_schema()
    )
    assert "outcome" in CoreOutput.model_json_schema()["required"]
    assert hashlib.sha256((FIXTURE / "response.raw").read_bytes()).digest() == (
        hashlib.sha256(RAW).digest()
    )


@pytest.mark.parametrize("method", ["discover_hypotheses", "calibrate_hypotheses"])
def test_separate_synthetic_empty_root_is_accepted(method):
    response = copy.deepcopy(RESPONSE)
    response["choices"][0]["message"]["content"] = CoreOutput(
        outcome="insufficient_evidence", gaps=["合成接线：没有足够材料"]
    ).model_dump_json()
    result = getattr(provider(None, discovery=response), method)(CONTEXT)
    assert result.outcome == "insufficient_evidence" and result.decisions == []


def test_unedited_negative_fields_and_evidence_reach_existing_review(tmp_path):
    value = copy.deepcopy(NEGATIVE)
    revision = Revision.model_validate(value["revision"])
    value["request"] = candidate(revision, None, review_version(Path("prompts")))
    original = json.loads(RESPONSE["choices"][0]["message"]["content"])["decisions"][0]
    # Pydantic adds only the existing observation-completion default; no prose edits.
    projected = copy.deepcopy(value["request"]["decision"])
    for row in projected["hypothesis"]["tests"]:
        assert row.pop("absence_requires_completed_observation") is True
    assert projected == original
    source = {e["evidence_id"]: e for e in CONTEXT["evidence"]}
    for evidence in value["request"]["evidence"]:
        assert evidence["excerpt"] == source[evidence["evidence_id"]]["excerpt"]
    wires = []
    active = provider(lambda ctx: {"assessments": [
        assessment(c, verdict="rejected", field="hypothesis.from_state")
        for c in ctx["candidates"]
    ]}, wires=wires)
    store = CoreStore(tmp_path)
    result = review_candidates(store, active, [value], now=NOW,
                               prompt_dir=Path("prompts"), maximum_characters=24000)
    sent = json.loads(wires[0]["messages"][-1]["content"])["candidates"][0]
    for field in ["from_state", "to_state", "claim", "mechanism", "unresolved"]:
        assert sent["decision"]["hypothesis"][field] == original["hypothesis"][field]
        assert any(p.startswith("hypothesis." + field) for p in sent["narratives"])
    assert result["items"][0]["status"] == "rejected"
    assert not store.public_current()

"""Synthetic boundary responses, formal Provider/model/validator/append-only transactions."""

import copy
import hashlib
import json
import socket
from datetime import timedelta

import pytest
from test_real_acceptance_repair import NOW, REQUEST, RESPONSE, formal

from morning_radar.tendencies.core_models import CoreDecision, CoreEvidence
from morning_radar.tendencies.core_store import CoreStore
from morning_radar.tendencies.core_validation import content_issues, materialize
from morning_radar.tendencies.inputs import excerpt_segments, pack_evidence


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("No network in validation follow-up")

    monkeypatch.setattr(socket.socket, "connect", denied)


def source(index):
    return CoreEvidence(
        evidence_id=f"synthetic-{index}",
        input_id=f"input-{index}",
        content_version=str(index),
        title="synthetic 测量",
        source_url=f"https://example.org/{index}",
        excerpt=f"第{index}次独立测试观察：间歇采样时供电持续满足设备采集需求。",
        source_kind="original_fact",
        scope=[],
        observed_at=NOW,
        ingested_at=NOW,
        synthetic=True,
        provenance="synthetic test fixture",
    )


def decision(e):
    return dict(
        kind="early",
        basis="external_evidence",
        reason="测试观察支持有条件的采集方式。",
        hypothesis=dict(
            claim="在测试港区，间歇采样可能适用于有储能支持的设备。",
            scope=["测试港区储能设备"],
            scope_basis="inferred",
            scope_reason="测试观察的有界外推。",
            from_state="需要持续供电",
            to_state="部分条件下可间歇采样",
            baseline=[dict(text="持续供电是过去的工作假设", assumption=True)],
            conditions=[
                dict(
                    key="energy",
                    observation=e.excerpt,
                    interpretation="供电可能足够。",
                    evidence_ids=[e.evidence_id],
                )
            ],
            mechanism="储能缓冲可能支撑间歇采样。",
            unresolved=["冬季条件未检验", "长期可靠性未检验"],
            tests=[
                dict(
                    condition_key="energy",
                    observe="记录冬季供电",
                    if_seen="增加供电支持",
                    if_absent="检查储能条件",
                )
            ],
        ),
        citations=[
            dict(
                evidence_id=e.evidence_id,
                quote=e.excerpt,
                role="observation",
                condition_key="energy",
            )
        ],
    )


def execute(store, proposal, evidence, current=None):
    response = copy.deepcopy(RESPONSE)
    response["choices"][0]["message"]["content"] = json.dumps(
        {"outcome": "proposed", "decisions": [proposal]}, ensure_ascii=False
    )
    provider = formal(response, [])
    current = current or {}
    ctx = {
        "evidence": [e.model_dump(mode="json") for e in evidence],
        "targets": [r.model_dump(mode="json") for r in current.values()],
    }
    result = provider.calibrate_hypotheses(ctx) if current else provider.discover_hypotheses(ctx)
    draft = CoreDecision.model_validate(result.decisions[0])
    revision = materialize(
        draft,
        evidence={e.evidence_id: e for e in evidence},
        current=current,
        seeds={},
        now=NOW + timedelta(hours=1),
        run_id="synthetic",
        analysis_version="followup-synthetic",
    )
    if revision:
        store.append(
            {"type": "revision", "revisions": [revision.model_dump(mode="json")]},
            operation_id=revision.revision_id,
        )
    return revision


@pytest.mark.parametrize("kind", ["supported", "strengthened"])
def test_inferred_gains_support_without_becoming_observed_or_verified(tmp_path, kind):
    store = CoreStore(tmp_path)
    first = execute(store, decision(source(1)), [source(1)])
    original = store.events()[0]
    update = decision(source(2))
    update.update(
        kind=kind,
        hypothesis_id=first.hypothesis_id,
        previous_revision_id=first.revision_id,
        changed_parts=["energy"],
    )
    second = execute(store, update, [source(2)], {first.hypothesis_id: first})
    reopened = CoreStore(tmp_path)
    assert len(reopened.revisions()) == 2
    assert reopened.events()[0] == original
    assert second.previous_revision_id == first.revision_id
    assert second.standing == "early"
    assert second.decision.hypothesis.scope_basis == "inferred"
    assert second.decision.hypothesis.scope == first.decision.hypothesis.scope
    assert second.decision.hypothesis.unresolved == first.decision.hypothesis.unresolved


@pytest.mark.parametrize(
    "mutation",
    [
        "observed",
        "scope",
        "gaps",
        "drop_gap",
        "stale",
        "quote",
        "unverified",
        "duplicate_id",
        "duplicate_text",
        "counter_only",
    ],
)
def test_support_still_enforces_scope_sources_references_versions_and_novelty(tmp_path, mutation):
    store = CoreStore(tmp_path)
    first = execute(store, decision(source(1)), [source(1)])
    new = source(2)
    if mutation == "unverified":
        new.status = "unverified"
    if mutation == "duplicate_id":
        new = source(1)
    if mutation == "duplicate_text":
        new.excerpt = source(1).excerpt
    update = decision(new)
    update.update(
        kind="strengthened",
        hypothesis_id=first.hypothesis_id,
        previous_revision_id=first.revision_id,
        changed_parts=["energy"],
    )
    if mutation == "observed":
        update["hypothesis"]["scope_basis"] = "observed"
        new.scope = update["hypothesis"]["scope"]
    if mutation == "scope":
        update["hypothesis"]["scope"] = ["全部港区"]
    if mutation == "gaps":
        update["hypothesis"]["unresolved"] = []
    if mutation == "drop_gap":
        update["hypothesis"]["unresolved"] = update["hypothesis"]["unresolved"][:1]
    if mutation == "stale":
        update["previous_revision_id"] = "obsolete"
    if mutation == "quote":
        update["citations"][0]["quote"] = "这是一段完全不存在于来源中的引文。"
    if mutation == "counter_only":
        update["citations"][0]["role"] = "counter"
    if mutation in {"duplicate_id", "duplicate_text"}:
        assert execute(store, update, [new], {first.hypothesis_id: first}) is None
    else:
        with pytest.raises(ValueError):
            execute(store, update, [new], {first.hypothesis_id: first})
    assert len(CoreStore(tmp_path).revisions()) == 1


@pytest.mark.parametrize(
    "claim",
    [
        "目前不能断言正确性不会下降。",
        "子 agent 不应被一概判定为无法替代。",
    ],
)
def test_explicit_denial_is_not_the_assertion(tmp_path, claim):
    e = source(1)
    e.excerpt += " In theory a CLI could invoke the harness."
    proposal = decision(e)
    proposal["hypothesis"]["claim"] = claim
    proposal["hypothesis"]["unresolved"].append("正确性尚待独立验证")
    # Add actual quoted feasibility so the old rule's condition is exercised.
    proposal["citations"].append(
        dict(
            evidence_id=e.evidence_id,
            quote="In theory a CLI could invoke the harness.",
            role="boundary",
            condition_key="energy",
        )
    )
    assert execute(CoreStore(tmp_path), proposal, [e]) is not None


def test_original_overstatements_and_mixed_clauses_remain_rejected():
    provider = formal(RESPONSE, [])
    for row in provider.discover_hypotheses(REQUEST).decisions:
        assert content_issues(CoreDecision.model_validate(row))
    draft = CoreDecision.model_validate(provider.discover_hypotheses(REQUEST).decisions[0])
    draft.hypothesis.claim = "目前不能断言正确性不会下降。但是正确性不会下降。"
    assert content_issues(draft)


def test_codemode_pack_contains_conditions_and_limits_with_exact_offsets():
    e = CoreEvidence.model_validate(REQUEST["evidence"][0])
    excerpt, spans = excerpt_segments(e.excerpt)
    assert len(excerpt) <= 700
    assert "inject the actual image payload" in excerpt
    assert "While in theory the agent could provide a CLI tool" in excerpt
    assert "rather crude process" in excerpt
    assert "In order to spawn and orchestrate sub agents" in excerpt
    assert all(
        segment == e.excerpt[a:b]
        for segment, (a, b) in zip(excerpt.split("\n[…]\n"), spans, strict=True)
    )
    ctx = {}
    included, _ = pack_evidence(ctx, [e], [e], 8000)
    row = ctx["evidence"][0]
    assert row["content_version"] == e.content_version
    assert row["excerpt_source_sha256"] == hashlib.sha256(e.excerpt.encode()).hexdigest()
    assert included[0].excerpt_spans == spans
    assert ctx["overview_role"].startswith("discovery_leads_only")


def test_cross_segment_quote_is_rejected(tmp_path):
    e = source(1)
    e.excerpt = "第一段真实的支持观察。\n[…]\n第二段不同的边界观察。"
    e.excerpt_spans = [(0, 12), (80, 92)]
    proposal = decision(e)
    # Entire packed string is present in excerpt, but not in any contiguous source span.
    with pytest.raises(ValueError, match="fabricated"):
        execute(CoreStore(tmp_path), proposal, [e])

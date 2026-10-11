"""Real failed wire + synthetic review responses, only HTTP is substituted.
These tests demonstrate contracts/gating, not reviewer semantic accuracy.
"""

import copy
import json
import socket
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from openai import OpenAI

from morning_radar.ai import AIBudget, DeepSeekProvider
from morning_radar.ai.usage import PriceQuote, usage_summary
from morning_radar.release.accounting import Accounting, ReleasePolicy
from morning_radar.tendencies import core
from morning_radar.tendencies.core_export import export_core
from morning_radar.tendencies.core_models import CoreEvidence, Revision
from morning_radar.tendencies.core_store import CoreStore
from morning_radar.tendencies.grounded_review import (
    candidate,
    pending_candidates,
    review_candidates,
    review_version,
)
from morning_radar.tendencies.web_pages import build_pages

FIXTURE = Path("tests/fixtures/grounded_review")
REQUEST = json.loads((FIXTURE / "request.json").read_text(encoding="utf8"))
RESPONSE = json.loads((FIXTURE / "response.json").read_text(encoding="utf8"))
TRANSACTION = json.loads((FIXTURE / "transaction.json").read_text(encoding="utf8"))
NOW = datetime.fromisoformat(TRANSACTION["revisions"][0]["recorded_at"])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*a, **k):
        raise AssertionError("No external network in grounded review tests")

    monkeypatch.setattr(socket.socket, "connect", denied)


def assessment(c, verdict="approved", field="hypothesis.claim", text=None):
    value = c["decision"]
    for key in field.split("."):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return {
        **{
            k: c[k]
            for k in [
                "candidate_id",
                "body_sha256",
                "evidence_sha256",
                "analysis_version",
                "expected_previous_revision_id",
            ]
        },
        "verdict": verdict,
        "checked_fields": c["narratives"],
        "rationale": "synthetic 审查响应：用于检查准入，不代表真实模型判断。",
        "early_reason": "未来条件结构可以尚未验证；来源归因和事实前提仍须成立。",
        "findings": [
            {
                "field_path": field,
                "text": text or value,
                "category": "conditional_inference"
                if verdict == "approved"
                else "unsupported_fact",
                "evidence_id": None,
                "quote": None,
                "explanation": "synthetic 配对审查结论。",
            }
        ],
    }


def provider(review_fn, *, wires=None, discovery=RESPONSE, calls=3, hook=None):
    wires = wires if wires is not None else []

    def transport(request):
        wire = json.loads(request.content)
        wires.append(wire)
        ctx = json.loads(wire["messages"][-1]["content"])
        if "candidates" not in ctx:
            return httpx.Response(200, json=discovery)
        if hook:
            hook()
        if review_fn == "unavailable":
            return httpx.Response(503, json={"error": {"message": "synthetic outage"}})
        raw = copy.deepcopy(RESPONSE)
        raw["choices"][0]["message"]["content"] = json.dumps(review_fn(ctx), ensure_ascii=False)
        return httpx.Response(200, json=raw)

    client = OpenAI(
        api_key="offline",
        base_url="https://replay.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    return DeepSeekProvider(
        model="deepseek-v4-flash",
        api_key="offline",
        base_url="https://replay.invalid/v1",
        client=client,
        budget=AIBudget(calls, 100000, 40, calls),
        prompt_dir=Path("prompts"),
        network_attempts=1,
    )


def item(store, *, text=None, field="claim", suffix="", previous=None):
    revision = Revision.model_validate(TRANSACTION["revisions"][0]).model_copy(deep=True)
    revision.revision_id += suffix
    revision.hypothesis_id += suffix if previous is None else ""
    revision.synthetic = True
    for e in revision.evidence:
        e.synthetic = True
    if text:
        setattr(revision.decision.hypothesis, field, text)
    if previous:
        revision.hypothesis_id = previous.hypothesis_id
        revision.previous_revision_id = previous.revision_id
        revision.decision.hypothesis_id = previous.hypothesis_id
        revision.decision.previous_revision_id = previous.revision_id
        revision.decision.kind = "analysis_reassessment"
        revision.recorded_at = NOW + timedelta(seconds=1)
    value = {
        "type": "review_candidate",
        "phase": "discovery",
        "recorded_at": NOW.isoformat(),
        "request": candidate(revision, previous, review_version(Path("prompts"))),
        "revision": revision.model_dump(mode="json"),
    }
    store.append(value, operation_id="candidate:" + revision.revision_id)
    return value


def run_review(store, p, items, cap=24000):
    return review_candidates(
        store,
        p,
        items,
        now=NOW + timedelta(seconds=2),
        prompt_dir=Path("prompts"),
        maximum_characters=cap,
    )


def test_real_failed_wire_saved_before_synthetic_review_blocks_public(tmp_path, monkeypatch):
    evidence = [
        CoreEvidence.model_validate({**e, "provenance": "real frozen excerpt"})
        for e in REQUEST["evidence"]
    ]
    monkeypatch.setattr(core, "read_evidence", lambda *a, **k: (evidence, {"errors": []}))

    def pack(ctx, *a):
        ctx.clear()
        ctx.update(copy.deepcopy(REQUEST))
        return evidence, []

    monkeypatch.setattr(core, "pack_evidence", pack)
    store = CoreStore(tmp_path)

    def reject(ctx):
        assert any(e["payload"]["type"] == "review_candidate" for e in store.events())
        assert not store.revisions()
        return {"assessments": [assessment(c, "rejected") for c in ctx["candidates"]]}

    wires = []
    result = core.run_core(
        tmp_path, config_root=Path("."), provider=provider(reject, wires=wires), now=NOW
    )
    assert len(wires) == 2
    assert (
        result["stages"]["discovery"]["output"]["decisions"][0]
        == json.loads(RESPONSE["choices"][0]["message"]["content"])["decisions"][0]
    )
    assert result["stages"]["content_review"]["items"][0]["status"] == "rejected"
    assert not store.revisions() and not store.public_current()
    assert not export_core(store)["current"]
    assert not pending_candidates(store)
    build_pages(store, tmp_path / "preview", repository=Path("."))
    assert "不会造成实质行政后果" not in (tmp_path / "preview/index.html").read_text("utf8")


PAIRS = [
    (
        "任务设定未知，但系统已被观测为受特定动机驱动。",
        "任务原因未知；若开放目标诱发选择行为，访问边界可能成为新的约束。",
    ),
    (
        "来源称未处理，因此任何后果都不存在。",
        "来源只称未处理，后果尚不清楚；若写入仍留痕迹，审计边界可能改变。",
    ),
    (
        "两个网站已经独立验证事实。",
        "两渠道各有报道，独立性未知；若访问条件相同，评估边界可能变化。",
    ),
    (
        "未点名足以证明有意隐瞒。",
        "来源未点名，无法核对；不能据此断言隐瞒，未来透明度约束仍待检验。",
    ),
    (
        "假定的历史权限已经确证为默认开放。",
        "历史权限暂作假定；若过去开放且未来收窄，比较评估的适用条件可能改变。",
    ),
    (
        "若干事故后变更，确定是这项单独触发。",
        "来源把变更归因于一组事故，单项因果未知；可能变化的是整体测试边界。",
    ),
    (
        "昨日发生事故，今日宣布应对，这就是未来结构。",
        "来源报告应对措施；若外部访问持续收窄，模拟环境与真实部署的评价差异可能扩大。",
    ),
]


@pytest.mark.parametrize("negative,positive", PAIRS)
def test_synthetic_pairs_preserve_conditional_early(tmp_path, negative, positive):
    store = CoreStore(tmp_path)
    bad, good = (
        item(store, text=negative, suffix="-bad"),
        item(store, text=positive, suffix="-good"),
    )

    def judge(ctx):
        return {
            "assessments": [
                assessment(c, "rejected" if c["candidate_id"].endswith("bad") else "approved")
                for c in ctx["candidates"]
            ]
        }

    result = run_review(store, provider(judge), [bad, good], cap=100000)
    assert [r["status"] for r in result["items"]] == ["rejected", "approved"]
    current = list(CoreStore(tmp_path).public_current().values())
    assert len(current) == 1 and current[0].decision.hypothesis.claim == positive
    assert (
        current[0].standing == "early" and current[0].decision.hypothesis.scope_basis == "inferred"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "hash",
        "evidence_hash",
        "version",
        "expected",
        "coverage",
        "path",
        "text",
        "quote",
        "missing",
        "duplicate",
    ],
)
def test_review_contract_rejects_unbound_or_invented_assessments(tmp_path, mutation):
    store = CoreStore(tmp_path)
    c = item(store)

    def judge(ctx):
        row = assessment(ctx["candidates"][0])
        key = {
            "hash": "body_sha256",
            "evidence_hash": "evidence_sha256",
            "version": "analysis_version",
            "expected": "expected_previous_revision_id",
        }
        if mutation in key:
            row[key[mutation]] = "wrong"
        elif mutation == "coverage":
            row["checked_fields"] = ["hypothesis.claim"]
        elif mutation in {"path", "text"}:
            row["findings"][0]["field_path" if mutation == "path" else "text"] = "invented"
        elif mutation == "quote":
            row["findings"][0].update(
                evidence_id=ctx["candidates"][0]["evidence"][0]["evidence_id"],
                quote="这是没有出现在实际材料中的一句话。",
            )
        return {
            "assessments": []
            if mutation == "missing"
            else [row, row]
            if mutation == "duplicate"
            else [row]
        }

    result = run_review(store, provider(judge), [c])
    assert result["items"][0]["status"] == "invalid_review"
    assert not store.public_current()


@pytest.mark.parametrize("mode", ["unavailable", "budget"])
def test_review_failure_persists_and_resumes_without_discovery(tmp_path, mode):
    store = CoreStore(tmp_path)
    c = item(store)
    p = provider(
        "unavailable" if mode == "unavailable" else lambda ctx: {},
        calls=0 if mode == "budget" else 1,
    )
    result = run_review(store, p, [c])
    assert result["items"][0]["status"] in {"unavailable", "budget_skipped"}
    wires = []
    p = provider(
        lambda ctx: {"assessments": [assessment(x) for x in ctx["candidates"]]}, wires=wires
    )
    result = core.run_core(
        tmp_path, config_root=Path("."), provider=p, now=NOW + timedelta(seconds=3)
    )
    assert len(wires) == 1 and "candidates" in json.loads(wires[0]["messages"][-1]["content"])
    assert result["status"] == "completed" and len(store.public_current()) == 1


def test_stale_after_review_preserves_previous_public_version(tmp_path):
    store = CoreStore(tmp_path)
    first = item(store, suffix="-first")
    def approve(ctx):
        return {"assessments": [assessment(c) for c in ctx["candidates"]]}
    run_review(store, provider(approve), [first])
    old = next(iter(store.current().values()))
    update = item(store, text="新解释仍待验证。", suffix="-update", previous=old)

    def race():
        changed = old.model_copy(deep=True)
        changed.revision_id += "-race"
        changed.previous_revision_id = old.revision_id
        store.append(
            {"type": "revision", "revisions": [changed.model_dump(mode="json")]},
            operation_id="synthetic concurrent writer",
        )

    result = run_review(store, provider(approve, hook=race), [update])
    assert result["items"][0]["status"] == "stale_or_invalid"
    assert store.public_current()[old.hypothesis_id] == old


def test_real_failed_legacy_transaction_remains_local_not_public(tmp_path):
    store = CoreStore(tmp_path)
    store.append(TRANSACTION["payload"], operation_id="legacy real failure")
    before = store.events()
    assert store.revisions() and not store.public_current()
    assert export_core(store)["history"] and export_core(store)["current"] == []
    assert store.events() == before


def test_formal_review_budget_usage_and_single_transport(tmp_path):
    from decimal import Decimal

    from morning_radar.ai.usage import attach_usage

    store = CoreStore(tmp_path)
    c = item(store)
    wires = []
    p = provider(
        lambda ctx: {"assessments": [assessment(x) for x in ctx["candidates"]]}, wires=wires
    )
    p.release_accounting = Accounting(
        tmp_path,
        "2026-10-11",
        lambda reason: None,
        ReleasePolicy(
            price=PriceQuote(
                model=p.model,
                input_per_million=Decimal(1),
                output_per_million=Decimal(4),
                cached_input_per_million=Decimal(".02"),
                source_url="https://example.org",
                verified_on="synthetic",
            )
        ),
    )
    attach_usage(p, tmp_path, business_date="2026-10-11", module="synthetic_review")
    run_review(store, p, [c])
    assert len(wires) == 1 and wires[0]["max_tokens"] == 6000
    assert p.client.max_retries == 0
    summary = usage_summary(tmp_path, "2026-10-11")
    assert summary["physical_calls"] == 1 and summary["incomplete_calls"] == 0
    assert Decimal(summary["known_cost_cny"]) > 0  # synthetic usage, never real billing


def test_rebuild_replaces_old_unreviewed_public_page_with_notice(tmp_path):
    from morning_radar.tendencies.web_pages import page_name
    store = CoreStore(tmp_path)
    store.append(TRANSACTION["payload"], operation_id="historical failed first version")
    revision = store.revisions()[0]
    output = tmp_path / "preview"
    output.mkdir()
    stale = output / page_name("r", revision.revision_id)
    stale.write_text(revision.decision.hypothesis.claim, encoding="utf8")
    build_pages(store, output, repository=Path("."))
    assert "不会造成实质行政后果" not in stale.read_text("utf8")
    assert "未获内容准入" in stale.read_text("utf8")
    assert store.revisions()[0] == revision


def test_cautious_claim_cannot_hide_blocker_in_mechanism(tmp_path):
    store = CoreStore(tmp_path)
    c = item(store, text="任务原因未知，未来结构仍需满足条件才能形成。")
    def judge(ctx):
        return {"assessments": [assessment(ctx["candidates"][0], "rejected",
                                          field="hypothesis.mechanism")]}
    result = run_review(store, provider(judge), [c])
    assert result["items"][0]["status"] == "rejected" and not store.public_current()


def test_unknown_usage_blocks_review_without_bypassing_public_gate(tmp_path):
    from decimal import Decimal

    from morning_radar.ai.usage import UsageRecorder, attach_usage
    store = CoreStore(tmp_path)
    c = item(store)
    UsageRecorder(tmp_path, business_date="2026-10-11", module="synthetic_unknown").start(
        provider="deepseek", model="deepseek-v4-flash", task="old",
        request_characters=100, maximum_output_tokens=10)
    wires = []
    p = provider(lambda ctx: {"assessments": [assessment(x) for x in ctx["candidates"]]},
                 wires=wires)
    p.release_accounting = Accounting(tmp_path, "2026-10-11", lambda reason: None, ReleasePolicy(
        price=PriceQuote(model=p.model, input_per_million=Decimal(1), output_per_million=Decimal(4),
                         source_url="https://example.org", verified_on="synthetic")))
    attach_usage(p, tmp_path, business_date="2026-10-11", module="synthetic_review")
    result = run_review(store, p, [c])
    assert result["items"][0]["status"] == "budget_skipped"
    assert not wires and not store.public_current() and pending_candidates(store)

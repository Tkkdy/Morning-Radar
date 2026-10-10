"""Captured real wire replay; mutated responses below are explicitly synthetic regressions."""

import copy
import json
import socket
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from openai import OpenAI
from pydantic import ValidationError

from morning_radar.ai import AIBudget, DeepSeekProvider
from morning_radar.ai.tendency_core import core_instructions
from morning_radar.tendencies import core
from morning_radar.tendencies.core_models import CoreDecision, CoreEvidence
from morning_radar.tendencies.core_store import CoreStore, StaleRevision
from morning_radar.tendencies.core_validation import content_issues, materialize
from morning_radar.tendencies.inputs import pack_evidence, read_evidence, select_evidence

FIXTURE = Path("tests/fixtures/real_acceptance")
REQUEST = json.loads((FIXTURE / "request-019.json").read_text(encoding="utf8"))
RESPONSE = json.loads((FIXTURE / "response-019.json").read_text(encoding="utf8"))
NOW = datetime.fromisoformat(REQUEST["as_of"])


def formal(response, wires):
    def transport(request):
        wires.append(json.loads(request.content))
        return httpx.Response(200, json=response)

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
        budget=AIBudget(3, 24000, 40, 4),
        prompt_dir=Path("prompts"),
    )


@pytest.fixture(autouse=True)
def no_socket(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("real network forbidden")

    monkeypatch.setattr(socket.socket, "connect", denied)


def apply(draft, evidence=None, current=None):
    return materialize(
        draft,
        evidence=evidence
        or {row["evidence_id"]: CoreEvidence.model_validate(row) for row in REQUEST["evidence"]},
        current=current or {},
        seeds={},
        now=NOW,
        run_id="replay",
        analysis_version="replay",
    )


def test_actual_019_parser_exact_request_normalization_and_semantic_rejection(
    tmp_path, monkeypatch
):
    wires = []
    provider = formal(RESPONSE, wires)
    evidence = [CoreEvidence.model_validate(row) for row in REQUEST["evidence"]]
    monkeypatch.setattr(core, "read_evidence", lambda *a, **k: (evidence, {"errors": []}))
    monkeypatch.setattr(core, "select_evidence", lambda *a, **k: (evidence, []))

    # Reproduce the captured business payload exactly, not the repaired retrieval policy.
    def frozen_pack(context, *args):
        context.clear()
        context.update(copy.deepcopy(REQUEST))
        return evidence, []

    monkeypatch.setattr(core, "pack_evidence", frozen_pack)
    monkeypatch.setattr(core, "attach_usage", lambda *a, **k: None)  # replay is not a physical call
    result = core.run_core(tmp_path, config_root=Path("."), provider=provider, now=NOW)
    assert json.loads(wires[0]["messages"][1]["content"]) == REQUEST
    assert wires[0]["response_format"] == {"type": "json_object"} and "tools" not in wires[0]
    stage = result["stages"]["discovery"]
    assert len(stage["normalization"]["changes"]) == 4
    assert stage["raw_output"] == json.loads(RESPONSE["choices"][0]["message"]["content"])
    assert len(stage["rejected"]) == 2
    assert all("hostname" in x["reason"] for x in stage["rejected"])
    assert not CoreStore(tmp_path).revisions()
    assert not (tmp_path / "data/usage").exists()
    for proposal in stage["output"]["decisions"]:
        assert content_issues(CoreDecision.model_validate(proposal))


@pytest.mark.parametrize("kind", ["nonnull", "unknown", "bad_id", "bad_quote"])
def test_strict_siblings_and_exact_normalization(kind):
    response = copy.deepcopy(RESPONSE)
    raw = json.loads(response["choices"][0]["message"]["content"])
    bad = raw["decisions"][0]
    if kind == "nonnull":
        bad["hypothesis"]["conditions_note"] = "cannot discard semantics"
    elif kind == "unknown":
        bad["hypothesis"]["invented"] = None
    elif kind == "bad_id":
        bad["citations"][0]["evidence_id"] = "invented"
    else:
        bad["citations"][0]["quote"] = "this quote never existed in the evidence"
    response["choices"][0]["message"]["content"] = json.dumps(raw, ensure_ascii=False)
    provider = formal(response, [])
    parsed = provider.discover_hypotheses(REQUEST)
    sibling = CoreDecision.model_validate(parsed.decisions[1])
    assert sibling.hypothesis.claim == raw["decisions"][1]["hypothesis"]["claim"]
    if kind in {"nonnull", "unknown"}:
        with pytest.raises(ValidationError):
            CoreDecision.model_validate(parsed.decisions[0])
    else:
        # Synthetic mutation removes the known earlier semantic blockers to isolate references.
        draft = CoreDecision.model_validate(parsed.decisions[0])
        draft.hypothesis.scope = ["Claude Code 长会话"]
        draft.hypothesis.scope_basis = "inferred"
        draft.hypothesis.scope_reason = "仅为作者所述长会话机制的外推"
        draft.hypothesis.claim = "作者所述摘要可能改善长会话，尚待验证。"
        draft.hypothesis.mechanism = "摘要保留失败详情并减少日志占用，效果待检验。"
        with pytest.raises(ValueError, match="Unknown evidence|fabricated"):
            apply(draft)


def test_inferred_early_transaction_and_stale_revision_synthetic():
    provider = formal(RESPONSE, [])
    draft = CoreDecision.model_validate(provider.discover_hypotheses(REQUEST).decisions[1])
    draft.hypothesis.scope = ["支持 CLI 的 agent harness"]
    draft.hypothesis.scope_basis = "inferred"
    draft.hypothesis.scope_reason = "作者关于图像协议和 CLI 回调的观察，尚不代表普遍采用。"
    draft.hypothesis.claim = "代码与原生工具的混合接口可能在需要图像协议注入的 harness 中持续存在。"
    for citation in draft.citations:
        citation.role = "boundary" if citation.role == "counter" else "source_claim"
    first = apply(draft)
    assert first.standing == "early"
    update = draft.model_copy(deep=True)
    update.hypothesis_id = first.hypothesis_id
    update.previous_revision_id = "obsolete"
    update.kind = "supported"
    with pytest.raises(StaleRevision):
        apply(update, current={first.hypothesis_id: first})


def test_schema_instructions_have_single_runtime_contract():
    for task in ["tendency_discover", "tendency_calibrate"]:
        text = core_instructions(
            task, (Path("prompts") / (task + ".md")).read_text(encoding="utf8")
        )
        assert (
            json.loads(text.split("单条 decision 的完整字段契约：\n")[1])
            == CoreDecision.model_json_schema()
        )


def test_checkpoint_scan_separate_cap_and_resume(tmp_path):
    directory = tmp_path / "data/intake/checkpoints"
    directory.mkdir(parents=True)
    rows = []
    for index in range(5):
        rows.append(
            {
                "content_version": str(index),
                "durable_at": NOW.isoformat(),
                "item": {
                    "id": str(index),
                    "url": f"https://example.org/{index}",
                    "title": str(index),
                    "summary": f"distinct observation {index}",
                    "fetched_at": NOW.isoformat(),
                },
            }
        )
    (directory / "page.json").write_text(
        json.dumps({"manifest": {"complete": True, "created_at": NOW.isoformat()}, "items": rows}),
        encoding="utf8",
    )
    first, scan = read_evidence(
        tmp_path, CoreStore(tmp_path), through=NOW, maximum_evidence=1, maximum_checkpoint_rows=3
    )
    second, later = read_evidence(
        tmp_path,
        CoreStore(tmp_path),
        through=NOW,
        maximum_evidence=5,
        maximum_checkpoint_rows=3,
        checkpoint_offsets=scan["checkpoint_next_offsets"],
    )
    assert scan["checkpoint_rows_scanned"] == 3 and len(first) == 1
    assert {e.input_id for e in second} == {"3", "4"}
    assert later["checkpoint_next_offsets"]["page.json"] == 0
    assert all(not e.scope for e in first + second)


def test_current_and_background_selection_compact_quotes_do_not_exceed_cap():
    old = [CoreEvidence.model_validate(row) for row in REQUEST["evidence"]]
    new = old[0].model_copy(
        update={"evidence_id": "new", "input_id": "new", "ingested_at": NOW + timedelta(seconds=1)}
    )
    selected, _ = select_evidence([*old, new], query="", maximum=4, novel_ids={"new"})
    assert selected[0].evidence_id == "new"
    ctx = {"as_of": NOW.isoformat(), "registry": [], "seeds": [], "targets": []}
    included, diagnostic = pack_evidence(ctx, selected, [*old, new], 4000)
    assert new.evidence_id in {e.evidence_id for e in included}
    assert len(core.encoded(ctx)) <= 4000 and len(included) < len(selected)
    assert any(x["reason"] == "request_character_cap" for x in diagnostic)
    for row in ctx["evidence"]:
        original = next(e.excerpt for e in selected if e.evidence_id == row["evidence_id"])
        assert all(
            segment == original[start:end]
            for segment, (start, end) in zip(
                row["excerpt"].split("\n[…]\n"), row["excerpt_spans"], strict=True
            )
        )


def test_captured_fact_empty_stories_retained_as_leads_not_headlines():
    from morning_radar.briefing.generator import BriefLimits, generate_daily_brief
    from morning_radar.models import Story

    stories = [
        Story.model_validate(r)
        for r in json.loads((FIXTURE / "stories.json").read_text(encoding="utf8"))
    ]
    response = json.loads((FIXTURE / "response-017.json").read_text(encoding="utf8"))
    provider = formal(response, [])
    saved = provider.write_brief(stories, [])  # real stored response, formal parsing/validation
    requested = []

    def captured_layout(selected, signals):
        requested.extend(s.id for s in selected)
        return saved  # untouched historical draft, layout replay only

    provider.write_brief = captured_layout
    result = generate_daily_brief(
        brief_date=NOW.date(),
        generated_at=NOW,
        timezone="Asia/Singapore",
        stories=stories,
        signals=[],
        provider=provider,
        limits=BriefLimits(12),
        enabled_sections={},
        run_stats={},
        importance_threshold=0.6,
    )
    empty = {s.id for s in stories if not s.facts}
    assert len(empty) == 2 and not (empty & set(requested))
    assert not (empty & {i for b in result.top_stories for i in b.story_ids})
    leads = [b for b in result.other_reading if empty & set(b.story_ids)]
    assert len(leads) == 2 and all("尚缺足够正文" in b.what_happened for b in leads)
    assert all(b.why_it_matters is None for b in leads)
    assert any(s.facts for s in stories) and result.run_stats["ai_brief_story_inputs"] == 1


def test_one_invalid_actual_sibling_does_not_erase_synthetic_valid_transaction(
    tmp_path, monkeypatch
):
    response = copy.deepcopy(RESPONSE)
    raw = json.loads(response["choices"][0]["message"]["content"])
    good = raw["decisions"][1]["hypothesis"]
    good.update(
        scope=["支持 CLI 的 agent harness"],
        scope_basis="inferred",
        scope_reason="作者的协议观察外推，尚无普遍采用证据。",
        claim="代码与原生工具的混合接口可能在需要图像协议注入的 harness 中持续存在。",
    )
    response["choices"][0]["message"]["content"] = json.dumps(raw, ensure_ascii=False)
    provider = formal(response, [])
    evidence = [CoreEvidence.model_validate(row) for row in REQUEST["evidence"]]
    monkeypatch.setattr(core, "read_evidence", lambda *a, **k: (evidence, {"errors": []}))
    monkeypatch.setattr(core, "select_evidence", lambda *a, **k: (evidence, []))

    def frozen(context, *a):
        context.clear()
        context.update(copy.deepcopy(REQUEST))
        return evidence, []

    monkeypatch.setattr(core, "pack_evidence", frozen)
    monkeypatch.setattr(core, "attach_usage", lambda *a, **k: None)
    result = core.run_core(tmp_path, config_root=Path("."), provider=provider, now=NOW)
    assert len(result["stages"]["discovery"]["rejected"]) == 1
    assert len(CoreStore(tmp_path).revisions()) == 1
    assert len(CoreStore(tmp_path).events()) == 1


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("https://techcrunch.com/public-article", True),
        ("https://platform.claude.com/docs/example", True),
        ("https://github.com/example/repo", True),
        ("https://www.nbcphiladelphia.com/example", True),
        ("https://techcrunch.com.evil.invalid/example", False),
        ("http://techcrunch.com/example", False),
        ("https://techcrunch.com/example?token=private", False),
        ("https://127.0.0.1/example", False),
    ],
)
def test_proposed_hosts_only_in_local_synthetic_network_policy(tmp_path, url, allowed):
    from morning_radar.collectors.http import HttpClient
    from morning_radar.models import RawItem
    from morning_radar.research.materials import enrich_materials
    from morning_radar.settings import MaterialEnrichmentConfig

    requests = []

    def respond(req):
        requests.append(str(req.url))
        return httpx.Response(
            200,
            text="<p>Synthetic public excerpt for policy tests only.</p>",
            headers={"content-type": "text/html"},
        )

    item = RawItem(
        id="synthetic",
        title="synthetic",
        url=url,
        source_name="synthetic",
        source_type="rss",
        fetched_at=NOW,
    )
    result = enrich_materials(
        [item],
        root=tmp_path,
        now=NOW,
        important_ids={"synthetic"},
        network_allowed=True,
        policy=MaterialEnrichmentConfig(
            enabled=True,
            allowed_hosts=[
                "techcrunch.com",
                "platform.claude.com",
                "github.com",
                "www.nbcphiladelphia.com",
            ],
        ),
        http=HttpClient(client=httpx.Client(transport=httpx.MockTransport(respond)), attempts=1),
    )
    assert bool(requests) == allowed
    assert result.snapshots[0]["status"] == ("fetched" if allowed else "not_allowed")


def test_same_evidence_new_excerpt_length_is_not_external_update():
    provider = formal(RESPONSE, [])
    draft = CoreDecision.model_validate(provider.discover_hypotheses(REQUEST).decisions[1])
    draft.hypothesis.scope = ["支持 CLI 的 agent harness"]
    draft.hypothesis.scope_basis = "inferred"
    draft.hypothesis.scope_reason = "作者观察的有界外推"
    draft.hypothesis.claim = "代码与原生工具的混合接口可能持续存在。"
    first = apply(draft)
    revised = draft.model_copy(deep=True)
    revised.hypothesis_id = first.hypothesis_id
    revised.previous_revision_id = first.revision_id
    revised.kind = "weakened"
    revised.changed_parts = ["mechanism"]
    revised.hypothesis.mechanism += " 归因尚不确定。"
    evidence = {
        e.evidence_id: e.model_copy(update={"excerpt": e.excerpt + "\n"}) for e in first.evidence
    }
    assert apply(revised, evidence=evidence, current={first.hypothesis_id: first}) is None


def test_real_historical_technical_excerpt_manual_projection_and_bad_quote():
    from morning_radar.ai import AIOutputError
    from morning_radar.models import RawItem
    from morning_radar.models.core import GroundedPoint, TechnicalReading
    from morning_radar.research.materials import validate_technical_reading

    row = json.loads((FIXTURE / "technical-source.json").read_text(encoding="utf8"))
    raw = RawItem(
        id=row["input_id"],
        title=row["title"],
        url=row["url"],
        source_name="OpenAI",
        source_type="rss",
        fetched_at=NOW,
        published_at=datetime.fromisoformat(row["published_at"].replace("Z", "+00:00")),
        summary=row["excerpt"],
    )
    point = GroundedPoint(
        text="报告讨论按提供的政策对内容打标签的推理模型。",
        source_url=raw.url,
        quote=("trained to reason from a provided policy "
               "in order to label content under that policy"),
    )
    card = TechnicalReading(kind="technical_report", problem=point, method=point)
    validate_technical_reading(card, [raw])
    assert card.conditions is None and card.limitations is None
    card.method = point.model_copy(
        update={"quote": "No real evidence supports this fabricated quote."}
    )
    with pytest.raises(AIOutputError, match="absent"):
        validate_technical_reading(card, [raw])



def test_no_change_does_not_bypass_citation_validation():
    provider = formal(RESPONSE, [])
    draft = CoreDecision.model_validate(provider.discover_hypotheses(REQUEST).decisions[1])
    draft.hypothesis.scope = ["支持 CLI 的 agent harness"]
    draft.hypothesis.scope_basis = "inferred"
    draft.hypothesis.scope_reason = "作者观察的有界外推"
    draft.hypothesis.claim = "代码与原生工具的混合接口可能持续存在。"
    first = apply(draft)
    draft.hypothesis_id = first.hypothesis_id
    draft.previous_revision_id = first.revision_id
    draft.kind = "no_change"
    draft.citations[0].quote = "Fabricated quote must fail even when the claim stays the same."
    with pytest.raises(ValueError, match="fabricated"):
        apply(draft, current={first.hypothesis_id: first})

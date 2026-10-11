import copy
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from morning_radar.ai import AIBudget, DeepSeekProvider, FakeAIProvider
from morning_radar.ai.openai_provider import OpenAIProvider
from morning_radar.ai.usage import UsageRecorder, usage_summary
from morning_radar.tendencies.core import run_core
from morning_radar.tendencies.core_demo import (
    DEMO_TIME,
    build_core_demo,
    demo_decision,
    write_demo_evidence,
)
from morning_radar.tendencies.core_export import export_core
from morning_radar.tendencies.core_models import CoreDecision, CoreOutput
from morning_radar.tendencies.core_store import CoreStore, StaleRevision
from morning_radar.tendencies.core_validation import materialize
from morning_radar.tendencies.inputs import read_evidence
from morning_radar.tendencies.submissions import persist_split, receipt, submit


@pytest.fixture
def context(tmp_path):
    write_demo_evidence(tmp_path, 0, DEMO_TIME)
    write_demo_evidence(tmp_path, 1, DEMO_TIME)
    evidence, _ = read_evidence(tmp_path, CoreStore(tmp_path), through=DEMO_TIME)
    return {
        "evidence": [e.model_dump(mode="json") for e in evidence],
        "registry": [],
        "targets": [],
    }


def apply(context, draft=None, current=None, now=DEMO_TIME, seeds=None):
    from morning_radar.tendencies.core_models import CoreEvidence

    return materialize(
        CoreDecision.model_validate(draft or demo_decision(context)),
        evidence={r["evidence_id"]: CoreEvidence.model_validate(r) for r in context["evidence"]},
        current=current or {},
        seeds=seeds or {},
        now=now,
        run_id="test-run",
        analysis_version="synthetic",
    )


def test_early_needs_no_news_or_event_count_and_retains_source_nature(context, tmp_path):
    context["evidence"] = context["evidence"][:1]
    revision = apply(context)
    assert revision.standing == "early"
    assert revision.origin == "system"
    assert revision.evidence[0].source_kind == "source_claim"
    assert revision.decision.hypothesis.conditions[0].interpretation
    assert revision.evidence[0].published_at is None
    assert revision.first_recorded_at == DEMO_TIME
    assert not (tmp_path / "data/briefs").exists()


def test_continuous_demo_retains_initial_scope_and_all_reasons(tmp_path):
    path = build_core_demo(Path("."), tmp_path / "demo")
    payload = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    first, second, third = payload["history"]
    assert payload["synthetic"] and all(r["synthetic"] for r in payload["history"])
    assert first["hypothesis_id"] == third["hypothesis_id"]
    assert second["previous_revision_id"] == first["revision_id"]
    assert third["previous_revision_id"] == second["revision_id"]
    assert first["decision"]["hypothesis"]["scope"] == ["港区甲", "港区乙"]
    assert third["decision"]["hypothesis"]["scope"] == ["港区甲"]
    assert third["decision"]["relinquished"]
    assert len({r["first_recorded_at"] for r in payload["history"]}) == 1
    assert any(c["role"] == "counter" for c in third["decision"]["citations"])


@pytest.mark.parametrize("mutation", ["quote", "unknown", "scope", "baseline", "condition", "test"])
def test_reference_structure_scope_rejections(context, mutation):
    draft = demo_decision(context)
    if mutation == "quote":
        draft["citations"][0]["quote"] = "不存在于材料中的编造引用必须拒绝"
    elif mutation == "unknown":
        draft["citations"][0]["evidence_id"] = "invented"
    elif mutation == "scope":
        draft["hypothesis"]["scope"] = ["全球行业"]
    elif mutation == "baseline":
        draft["hypothesis"]["baseline"][0]["assumption"] = False
    elif mutation == "condition":
        draft["hypothesis"]["conditions"][0]["evidence_ids"] = ["invented"]
    else:
        draft["hypothesis"]["tests"][0]["condition_key"] = "invented"
    with pytest.raises(ValueError):
        apply(context, draft)


@pytest.mark.parametrize(
    "kind,status",
    [
        ("user_hypothesis", "captured"),
        ("system_inference", "captured"),
        ("source_claim", "failed"),
        ("source_claim", "offline"),
    ],
)
def test_old_judgment_user_opinion_and_unavailable_source_are_not_facts(context, kind, status):
    for row in context["evidence"]:
        row.update(source_kind=kind, status=status)
    with pytest.raises(ValueError, match="Unverified"):
        apply(context)


def test_duplicate_mirrors_and_same_event_real_followup(context, tmp_path):
    source = tmp_path / "data/evidence/v1/2026-10-10/synthetic-0.json"
    row = json.loads(source.read_text(encoding="utf-8"))
    row["source_url"] = "https://example.org/mirror"
    (source.parent / "mirror.json").write_text(json.dumps(row), encoding="utf-8")
    write_demo_evidence(tmp_path, 2, DEMO_TIME + timedelta(hours=1))
    early, _ = read_evidence(tmp_path, CoreStore(tmp_path), through=DEMO_TIME)
    later, _ = read_evidence(tmp_path, CoreStore(tmp_path), through=DEMO_TIME + timedelta(hours=2))
    assert len(early) == 2 and len(later) == 3
    assert len({event for item in later for event in item.event_ids}) == 1


def test_same_evidence_cannot_strengthen_but_reanalysis_is_labeled(context):
    first = apply(context)
    context["targets"] = [first.model_dump(mode="json")]
    draft = demo_decision(context, "strengthened")
    assert apply(context, draft, {first.hypothesis_id: first}) is None
    draft.update(kind="analysis_reassessment", basis="analysis_reassessment")
    draft["hypothesis"]["mechanism"] += " 本次分析重评估认为维护成本需要单独检验。"
    second = apply(context, draft, {first.hypothesis_id: first})
    assert second.decision.basis == "analysis_reassessment"
    assert second.observed_signatures == first.observed_signatures
    assert second.standing == "early"


def test_stale_store_append_and_interrupted_write_preserve_history(context, tmp_path, monkeypatch):
    import morning_radar.tendencies.core_store as module

    store = CoreStore(tmp_path)
    first = apply(context)
    event = {"type": "revision", "revisions": [first.model_dump(mode="json")]}
    store.append(event, operation_id="first")
    store.append(event, operation_id="first")
    assert len(store.revisions()) == 1
    with pytest.raises(StaleRevision):
        store.append(event, operation_id="stale")
    original = module.atomic_json
    monkeypatch.setattr(module, "atomic_json", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        store.append(
            {"type": "receipt", "submission_id": "pending", "items": []}, operation_id="io"
        )
    assert CoreStore(tmp_path).revisions()[0] == first
    monkeypatch.setattr(module, "atomic_json", original)
    store.append({"type": "receipt", "submission_id": "pending", "items": []}, operation_id="io")
    assert CoreStore(tmp_path).receipts()["pending"]["items"] == []


def test_user_batch_partial_retry_and_new_process_receipt(tmp_path):
    store = CoreStore(tmp_path)
    raw = "甲港可以间歇采样。乙港需要常在线。"
    split = raw.index("乙")
    result = submit(
        store,
        raw_text=raw,
        author="owner",
        now=DEMO_TIME,
        submission_id="test-batch",
        entries=[
            {"text": raw[:split], "start": 0, "end": split},
            {"text": "incorrect", "start": split, "end": len(raw)},
        ],
    )
    assert result["receipt"]["status"] == "partial"
    seed = result["receipt"]["items"][0]["seed_id"]
    persist_split(
        store,
        result["submission"],
        {
            "items": [
                {"text": raw[split:], "start": split, "end": len(raw)},
                {"text": raw[:split], "start": 0, "end": split},
            ],
            "gaps": [],
        },
    )
    saved = receipt(CoreStore(tmp_path), "test-batch")
    assert saved["receipt"]["status"] == "processed"
    assert seed in {r["seed_id"] for r in saved["receipt"]["items"]}
    # Identical text submitted intentionally again receives a different identity.
    again = submit(store, raw_text=raw, author="owner", now=DEMO_TIME)
    assert again["submission"]["submission_id"] != "test-batch"
    code = "import sys;sys.path.insert(0,'src');from pathlib import Path;" + (
        "from morning_radar.tendencies.core_store import CoreStore;"
        "print(CoreStore(Path(sys.argv[1])).receipts()['test-batch']['status'])"
    )
    out = subprocess.check_output([sys.executable, "-B", "-c", code, str(tmp_path)], text=True)
    assert out.strip() == "processed"


def test_personal_observation_keeps_personal_scope(tmp_path):
    store = CoreStore(tmp_path)
    submit(
        store,
        raw_text="这是个人体验，不是全球结论。",
        author="owner",
        now=DEMO_TIME,
        materials=[
            {
                "kind": "personal_observation",
                "scope": ["one trial"],
                "text": "在我的一次试验中看到了这个变化。",
            }
        ],
    )
    evidence, _ = read_evidence(tmp_path, store, through=DEMO_TIME)
    assert evidence[0].source_kind == "personal_observation"
    assert evidence[0].scope == ["personal:owner:one trial"]


def test_correction_requires_actual_submission_and_expected_revision(context):
    first = apply(context)
    context["targets"] = [first.model_dump(mode="json")]
    draft = demo_decision(context, "narrowed")
    draft.update(kind="user_correction", basis="user_correction", seed_id="user-seed")
    seeds = {
        "user-seed": {"correction_of": first.hypothesis_id, "expected_revision": first.revision_id}
    }
    revised = apply(context, draft, {first.hypothesis_id: first}, seeds=seeds)
    assert revised.decision.basis == "user_correction"
    assert revised.previous_revision_id == first.revision_id
    with pytest.raises(StaleRevision):
        apply(context, draft, {first.hypothesis_id: revised}, seeds=seeds)


def test_branch_retains_parent_and_narrowed_child(context):
    first = apply(context)
    draft = demo_decision(context, "narrowed")
    draft.update(
        kind="split", parent_hypothesis_id=first.hypothesis_id, parent_revision_id=first.revision_id
    )
    child = apply(context, draft, {first.hypothesis_id: first})
    assert child.hypothesis_id != first.hypothesis_id
    assert child.parent_revision_id == first.revision_id
    assert first.decision.hypothesis.scope == ["港区甲", "港区乙"]


class WireClient:
    def __init__(self, output_factory):
        self.output_factory, self.requests = output_factory, []
        self.chat = SimpleNamespace(completions=self)
        self.responses = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        context = json.loads(kwargs["messages"][-1]["content"])
        from test_grounded_review import assessment
        output = ({"assessments": [assessment(c) for c in context["candidates"]]}
                  if "candidates" in context else self.output_factory(context))
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=json.dumps(output, ensure_ascii=False)),
                )
            ],
            usage=None,
        )

    def parse(self, **kwargs):
        self.requests.append(kwargs)
        from test_grounded_review import assessment
        context = json.loads(kwargs["input"])
        output = ({"assessments": [assessment(c) for c in context["candidates"]]}
                  if "candidates" in context else self.output_factory(context))
        return SimpleNamespace(output_parsed=output, usage=None, status="completed")


def wire_provider(client, backend="deepseek", calls=3):
    cls = DeepSeekProvider if backend == "deepseek" else OpenAIProvider
    kwargs = {"base_url": "https://example.org/mock"} if backend == "deepseek" else {}
    return cls(
        model="test-model",
        api_key="fixture-key",
        client=client,
        budget=AIBudget(calls, 24000, 20, 4),
        prompt_dir=Path("prompts"),
        **kwargs,
    )


@pytest.mark.parametrize("backend", ["deepseek", "openai"])
def test_formal_provider_constructs_requests_and_core_validates_items(context, tmp_path, backend):
    def output(ctx):
        draft = demo_decision(ctx)
        invalid = copy.deepcopy(draft)
        invalid["citations"][0]["quote"] = "网页指令不能替代不存在的引用事实"
        return {"decisions": [invalid, draft], "gaps": [], "outcome": "proposed"}

    client = WireClient(output)
    result = run_core(
        tmp_path, config_root=Path("."), provider=wire_provider(client, backend), now=DEMO_TIME
    )
    assert result["stages"]["discovery"]["status"] == "partial"
    assert len(result["revision_ids"]) == 1
    revision = CoreStore(tmp_path).revisions()[0]
    assert all(
        c.quote in next(e.excerpt for e in revision.evidence if e.evidence_id == c.evidence_id)
        for c in revision.decision.citations
    )
    request = client.requests[0]
    text = request["messages"][0]["content"] if backend == "deepseek" else request["instructions"]
    assert "忽略" in text and "过去" in text and "CoreDecision" in text
    assert usage_summary(tmp_path, "2026-10-10")["total_cost_cny"] is None


def test_budget_stages_and_billable_reruns_accumulate(context, tmp_path):
    store = CoreStore(tmp_path)
    first = apply(context)
    store.append(
        {"type": "revision", "revisions": [first.model_dump(mode="json")]}, operation_id="first"
    )
    submit(store, raw_text="等待解析的多条投递文本。", author="owner", now=DEMO_TIME)

    def empty(_):
        return {"decisions": [], "outcome": "no_changes", "gaps": []}

    for _ in range(2):
        result = run_core(
            tmp_path,
            config_root=Path("."),
            provider=wire_provider(WireClient(empty), calls=1),
            now=DEMO_TIME,
            reanalyze=True,
        )
        assert result["stages"]["discovery"]["status"] == "no_changes"
        assert result["stages"]["calibration"]["status"] == "budget_skipped"
        assert result["stages"]["submissions"]["status"] == "budget_skipped"
    summary = usage_summary(tmp_path, "2026-10-10")
    assert summary["physical_calls"] == 2
    assert summary["incomplete_calls"] == 2
    path = next((tmp_path / "data/usage/2026-10-10").glob("*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    UsageRecorder(tmp_path, business_date="2026-10-10", module="tendency").import_record(record)
    assert usage_summary(tmp_path, "2026-10-10")["physical_calls"] == 2
    assert list(store.receipts().values())[0]["status"] == "pending"


def test_topic_repetition_can_return_no_direction_and_legacy_is_read_only(context, tmp_path):
    legacy = tmp_path / "data/tendencies/2026-10-09.json"
    legacy.parent.mkdir(parents=True)
    old = b'{"date":"2026-10-09","decisions":[]}'
    legacy.write_bytes(old)
    result = run_core(tmp_path, config_root=Path("."), provider=FakeAIProvider(), now=DEMO_TIME)
    assert result["stages"]["discovery"]["status"] == "insufficient_evidence"
    assert not CoreStore(tmp_path).revisions()
    assert (
        export_core(CoreStore(tmp_path), legacy_root=tmp_path)["legacy"][0]["provenance"]
        == "legacy_v1_unmigrated"
    )
    assert legacy.read_bytes() == old
    assert not (tmp_path / "site/status.json").exists()


def test_failed_split_is_durable_and_free_text_uses_formal_provider(tmp_path):
    store = CoreStore(tmp_path)
    submitted = submit(store, raw_text="第一条假设。第二条假设。", author="owner", now=DEMO_TIME)
    client = WireClient(
        lambda ctx: {
            "items": [
                {"text": ctx["raw_text"][:6], "start": 0, "end": 6},
                {"text": "wrong", "start": 6, "end": 12},
            ],
            "gaps": [],
        }
    )
    result = run_core(
        tmp_path, config_root=Path("."), provider=wire_provider(client), now=DEMO_TIME
    )
    assert result["stages"]["submissions"]["status"] == "partial"
    assert CoreStore(tmp_path).seeds()[0]["text"] == "第一条假设。"
    assert (
        receipt(CoreStore(tmp_path), submitted["submission"]["submission_id"])["submission"][
            "raw_text"
        ]
        == "第一条假设。第二条假设。"
    )


def test_cancelled_run_keeps_diagnostics_and_raw_submission(context, tmp_path):
    class Cancelled(FakeAIProvider):
        def discover_hypotheses(self, context):
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_core(tmp_path, config_root=Path("."), provider=Cancelled(), now=DEMO_TIME)
    result = json.loads(next((tmp_path / "data/tendency-core/v2/runs").glob("*.json")).read_text())
    assert result["status"] == "cancelled"
    assert not CoreStore(tmp_path).revisions()


@pytest.mark.parametrize("backend", ["deepseek", "openai"])
def test_formal_calibration_and_split_request_paths(context, tmp_path, backend):
    first = apply(context)
    store = CoreStore(tmp_path)
    store.append(
        {"type": "revision", "revisions": [first.model_dump(mode="json")]}, operation_id="first"
    )
    write_demo_evidence(tmp_path, 2, DEMO_TIME + timedelta(hours=1))
    submitted = submit(store, raw_text="第一条假设。第二条假设。", author="owner", now=DEMO_TIME)

    def respond(ctx):
        if "raw_text" in ctx:
            return {
                "items": [
                    {"text": ctx["raw_text"][:6], "start": 0, "end": 6},
                    {"text": ctx["raw_text"][6:], "start": 6, "end": 12},
                ],
                "gaps": [],
            }
        if ctx["targets"]:
            return {"decisions": [demo_decision(ctx, "strengthened")], "outcome": "proposed"}
        return {"decisions": [], "outcome": "no_changes"}

    client = WireClient(respond)
    result = run_core(
        tmp_path,
        config_root=Path("."),
        provider=wire_provider(client, backend, calls=4),
        now=DEMO_TIME + timedelta(hours=2),
    )
    assert result["stages"]["calibration"]["status"] == "completed"
    assert result["stages"]["submissions"]["status"] == "completed"
    latest = store.current()[first.hypothesis_id]
    assert latest.previous_revision_id == first.revision_id
    assert len(latest.observed_signatures) == 3
    assert len(client.requests) == 4
    assert len({s["physical_call_id"] for s in result["stages"].values()}) == 4
    assert (
        receipt(store, submitted["submission"]["submission_id"])["receipt"]["status"] == "processed"
    )


def test_corrupt_event_file_and_later_source_failure_do_not_hide_valid_excerpt(context, tmp_path):
    directory = tmp_path / "data/evidence/v1/2026-10-10"
    row = json.loads((directory / "synthetic-0.json").read_text(encoding="utf-8"))
    row["observed_at"] = (DEMO_TIME + timedelta(hours=1)).isoformat()
    row["status"] = "failed"
    (directory / "later-failure.json").write_text(json.dumps(row), encoding="utf-8")
    (tmp_path / "data/evidence/events/corrupt.json").write_text("{", encoding="utf-8")
    evidence, diagnostics = read_evidence(
        tmp_path, CoreStore(tmp_path), through=DEMO_TIME + timedelta(hours=2)
    )
    assert len(evidence) == 2
    assert all(e.status == "captured" for e in evidence)
    assert diagnostics["errors"][0]["error"] == "JSONDecodeError"


def test_unverified_intake_role_remains_a_lead(context, tmp_path):
    path = tmp_path / "data/evidence/v1/2026-10-10/synthetic-0.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["statement_type"] = "unverified_lead"
    payload["remaining_gaps"] = ["source_detail"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    evidence, _ = read_evidence(tmp_path, CoreStore(tmp_path), through=DEMO_TIME)
    lead = next(e for e in evidence if e.input_id == "demo-input-0")
    assert lead.status == "unverified"
    assert lead.information_gaps == ["source_detail"]


def test_critical_counterevidence_overturns_and_archive_is_not_refutation(context, tmp_path):
    first = apply(context)
    write_demo_evidence(tmp_path, 3, DEMO_TIME + timedelta(hours=1))
    evidence, _ = read_evidence(
        tmp_path, CoreStore(tmp_path), through=DEMO_TIME + timedelta(hours=2)
    )
    context["evidence"] = [e.model_dump(mode="json") for e in evidence]
    context["targets"] = [first.model_dump(mode="json")]
    draft = demo_decision(context, "narrowed")
    draft.update(kind="overturned")
    overturned = apply(
        context, draft, {first.hypothesis_id: first}, now=DEMO_TIME + timedelta(hours=2)
    )
    assert overturned.standing == "overturned"
    archived = copy.deepcopy(draft)
    archived.update(kind="archived", basis="analysis_reassessment")
    archived["hypothesis"] = first.decision.hypothesis.model_dump(mode="json")
    result = apply(
        context, archived, {first.hypothesis_id: first}, now=DEMO_TIME + timedelta(hours=2)
    )
    assert result.standing == "archived" and result.decision.basis == "analysis_reassessment"


def test_large_registry_does_not_starve_discovery(context, tmp_path):
    store = CoreStore(tmp_path)
    first = apply(context)
    for i in range(35):
        revision = first.model_copy(deep=True)
        revision.hypothesis_id = f"old-{i}"
        revision.revision_id = f"revision-{i}"
        revision.decision.hypothesis.claim = "旧方向长摘要" * 100 + str(i)
        store.append(
            {"type": "revision", "revisions": [revision.model_dump(mode="json")]},
            operation_id=f"old-{i}",
        )
    client = WireClient(lambda _: {"decisions": [], "outcome": "no_changes"})
    result = run_core(
        tmp_path, config_root=Path("."), provider=wire_provider(client), now=DEMO_TIME
    )
    stage = result["stages"]["discovery"]
    assert stage["status"] == "no_changes"
    assert stage["selection"] and stage["registry_truncated"]
    assert result["pending_target_ids"] and result["status"] == "partial"
    assert stage["input_characters"] <= 8000


def test_invalid_provider_json_retains_request_call_and_response_digest(context, tmp_path):
    class InvalidWire(WireClient):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            response.choices[0].message.content = "{invalid response"
            return response

    client = InvalidWire(lambda _: {"decisions": [], "outcome": "no_changes"})
    result = run_core(
        tmp_path, config_root=Path("."), provider=wire_provider(client), now=DEMO_TIME
    )
    stage = result["stages"]["discovery"]
    assert stage["status"] == "provider_or_validation_failed"
    assert stage["physical_call_id"] and stage["response_digest"]["sha256"]
    assert stage["response_digest"]["raw_retained"] is False
    assert stage["request"]["evidence"]
    assert not CoreStore(tmp_path).revisions()


def test_cli_submission_receipt_run_and_read_are_independent(tmp_path, monkeypatch, capsys):
    import morning_radar.cli as cli

    def forbidden_pipeline(*args):
        raise AssertionError("Must not instantiate the daily pipeline")

    monkeypatch.setattr(cli, "MorningRadarPipeline", forbidden_pipeline)
    workspace = tmp_path / "local"
    source = tmp_path / "submission.json"
    raw = "这一判断尚待证据验证。"
    source.write_text(
        json.dumps(
            {
                "raw_text": raw,
                "author": "owner",
                "submission_id": "cli-test",
                "entries": [{"text": raw, "start": 0, "end": len(raw)}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert (
        cli.main(["tendency-core", "submit", "--workspace", str(workspace), "--file", str(source)])
        == 0
    )
    capsys.readouterr()
    assert (
        cli.main(
            [
                "tendency-core",
                "receipt",
                "--workspace",
                str(workspace),
                "--submission-id",
                "cli-test",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["receipt"]["status"] == "processed"
    assert next(iter(result["analysis"].values()))["status"] == "unassessed"
    assert (
        cli.main(
            [
                "tendency-core",
                "run",
                "--workspace",
                str(workspace),
                "--evidence-root",
                str(workspace),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["stages"]["discovery"]["status"] == "insufficient_evidence"
    assert (
        cli.main(
            [
                "tendency-core",
                "show",
                "--workspace",
                str(workspace),
                "--evidence-root",
                str(workspace),
                "--format",
                "json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["submissions"][0]["raw_text"] == raw


def test_user_correction_through_runner_has_real_receipt_and_keeps_original(context, tmp_path):
    store = CoreStore(tmp_path)
    first = apply(context)
    store.append(
        {"type": "revision", "revisions": [first.model_dump(mode="json")]}, operation_id="first"
    )
    text = "我主动收窄到港区甲，原先范围过宽。"
    submission = submit(
        store,
        raw_text=text,
        author="owner",
        now=DEMO_TIME,
        correction_of=first.hypothesis_id,
        expected_revision=first.revision_id,
        entries=[{"text": text, "start": 0, "end": len(text)}],
    )

    class Correction(FakeAIProvider):
        from morning_radar.tendencies.core_demo import synthetic_review
        review_hypotheses = staticmethod(synthetic_review)

        def discover_hypotheses(self, context):
            return CoreOutput(outcome="no_changes")

        def calibrate_hypotheses(self, context):
            draft = demo_decision(context, "narrowed")
            draft.update(
                kind="user_correction",
                basis="user_correction",
                seed_id=context["seeds"][0]["seed_id"],
            )
            return CoreOutput(outcome="proposed", decisions=[draft])

    result = run_core(tmp_path, config_root=Path("."), provider=Correction(), now=DEMO_TIME)
    assert result["stages"]["calibration"]["status"] == "completed"
    saved = receipt(CoreStore(tmp_path), submission["submission"]["submission_id"])
    assert next(iter(saved["analysis"].values()))["status"] == "recorded"
    assert CoreStore(tmp_path).revisions()[0] == first
    assert CoreStore(tmp_path).current()[first.hypothesis_id].decision.basis == "user_correction"


def test_demo_api_cannot_write_inside_production():
    with pytest.raises(ValueError, match="isolated"):
        build_core_demo(Path("."), Path("data/tendency-core-demo"))


def test_stale_parallel_discovery_cannot_duplicate_committed_proposition(context, tmp_path):
    first, concurrent = apply(context), apply(context)
    assert first.hypothesis_id != concurrent.hypothesis_id
    store = CoreStore(tmp_path)
    store.append(
        {"type": "revision", "revisions": [first.model_dump(mode="json")]}, operation_id="one"
    )
    with pytest.raises(StaleRevision, match="already committed"):
        store.append(
            {"type": "revision", "revisions": [concurrent.model_dump(mode="json")]},
            operation_id="two",
        )
    assert len(CoreStore(tmp_path).revisions()) == 1


def test_checkpoint_row_cap_reports_truncation_even_after_deduplication(tmp_path):
    directory = tmp_path / "data/intake/checkpoints"
    directory.mkdir(parents=True)
    row = {
        "content_version": "synthetic-version",
        "durable_at": DEMO_TIME.isoformat(),
        "item": {
            "id": "synthetic-input",
            "url": "https://example.org/synthetic",
            "title": "synthetic source",
            "summary": "synthetic bounded evidence",
            "fetched_at": DEMO_TIME.isoformat(),
        },
    }
    payload = {
        "manifest": {"complete": True, "created_at": DEMO_TIME.isoformat()},
        "items": [row, copy.deepcopy(row)],
    }
    (directory / "synthetic.json").write_text(json.dumps(payload), encoding="utf-8")
    evidence, diagnostics = read_evidence(
        tmp_path, CoreStore(tmp_path), through=DEMO_TIME, maximum_evidence=1,
        maximum_checkpoint_rows=1
    )
    assert len(evidence) == 1
    assert diagnostics["evidence_truncated"] is True

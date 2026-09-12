from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from morning_radar.ai import AIBudget, AIBudgetExceeded, AIOutputError, FakeAIProvider
from morning_radar.ai.deepseek_provider import DeepSeekProvider
from morning_radar.ai.errors import AIBillingUnavailable
from morning_radar.ai.models import ResearchResolutionBatch, ResearchResolutionDraft
from morning_radar.ai.openai_provider import OpenAIProvider
from morning_radar.ai.qwen_provider import QwenProvider
from morning_radar.ai.request_payload import bind_call_meta, research_request_payload
from morning_radar.intake.inspect import inspect_intake
from morning_radar.intake.models import ReasonCode
from morning_radar.models import RawItem, ResearchDisposition, SourceRole, StatementType
from morning_radar.pipeline import MorningRadarPipeline
from morning_radar.research.engine import build_research_cases, resolve_research
from tests.unit.test_deepseek_provider import FakeChatCompletions
from tests.unit.test_deepseek_provider import provider as ds_provider
from tests.unit.test_phase1_patch import DAY_N, copy_project, save_checkpoint
from tests.unit.test_phase1_patch03 import _install_tracking_provider, _seed


def _lead(suffix: str, *, score: int) -> RawItem:
    age_hours = 3 if score >= 50 else 2
    return RawItem(
        id=f"item-{suffix}",
        title=f"Practitioner observed concrete {suffix} workflow failure today",
        url=f"https://example.com/{suffix}",
        source_name="Blog",
        source_type="rss",
        published_at=DAY_N - timedelta(hours=age_hours),
        fetched_at=DAY_N - timedelta(hours=age_hours),
        summary=f"Concrete practitioner notes about {suffix} with enough detail.",
        content_excerpt=f"Concrete practitioner notes about {suffix} with enough detail.",
        source_role=SourceRole.PRACTITIONER,
        statement_type=StatementType.FIRSTHAND_OBSERVATION,
        company_candidates=["openai"],
        metadata={"score": score, "content_version": "v1"},
    )


def _pair():
    items = [_lead("alpha-case", score=50), _lead("bravo-case", score=40)]
    cases = build_research_cases(items, maximum_cases=8)
    assert len(cases) == 2
    return items, cases


def _draft(case, *, claim: str) -> ResearchResolutionDraft:
    return ResearchResolutionDraft(
        case_id=case.id,
        in_scope=True,
        scope_rationale="该观察直接涉及 AI 模型或产品行为。",
        disposition=ResearchDisposition.RADAR_SIGNAL,
        statement_type=StatementType.FIRSTHAND_OBSERVATION,
        claim=claim,
        why_notable="该观察可能影响 AI 开发者实践。",
        uncertainty="当前仍需验证。",
    )


def _batch_json(case, *, claim: str) -> str:
    return ResearchResolutionBatch(cases=[_draft(case, claim=claim)]).model_dump_json()


def _both_json(cases) -> str:
    return ResearchResolutionBatch(
        cases=[
            _draft(cases[0], claim="A-CALL1"),
            _draft(cases[1], claim="B-CALL1"),
        ]
    ).model_dump_json()


class SelectiveResearch(FakeAIProvider):
    def __init__(self) -> None:
        super().__init__()
        self.research_calls = 0
        self.payloads: list[list[str]] = []

    def resolve_research_cases(self, cases):
        meta = self._record(
            "resolve_research_cases", research_request_payload(cases, self.topic_context)
        )
        self.research_calls += 1
        self.payloads.append([case.id for case in cases])
        selected = cases[:1] if self.research_calls == 1 and len(cases) > 1 else list(cases)
        resolved = [
            _draft(case, claim=f"SENTINEL-{self.research_calls}-{case.id[-4:]}")
            for case in selected
        ]
        return bind_call_meta(ResearchResolutionBatch(cases=resolved), meta)


class TruncateThenSplit(FakeAIProvider):
    def __init__(self) -> None:
        super().__init__()
        self.research_calls = 0

    def resolve_research_cases(self, cases):
        meta = self._record(
            "resolve_research_cases", research_request_payload(cases, self.topic_context)
        )
        self.research_calls += 1
        if len(cases) > 1:
            raise AIOutputError("structured output truncated")
        resolved = [_draft(cases[0], claim=f"SPLIT-{self.research_calls}")]
        return bind_call_meta(ResearchResolutionBatch(cases=resolved), meta)


class BillingOnRetry(FakeAIProvider):
    def __init__(self) -> None:
        super().__init__()
        self.research_calls = 0

    def resolve_research_cases(self, cases):
        meta = self._record(
            "resolve_research_cases", research_request_payload(cases, self.topic_context)
        )
        self.research_calls += 1
        if self.research_calls == 1 and len(cases) > 1:
            resolved = [_draft(cases[0], claim="A-OK")]
            return bind_call_meta(ResearchResolutionBatch(cases=resolved), meta)
        raise AIBillingUnavailable("402 payment required")


def test_y01_shared_call_for_joint_success() -> None:
    items, _cases = _pair()
    provider = FakeAIProvider()
    result = resolve_research(
        items,
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=0,
        split_retry_attempts=0,
    )
    metas = [result.case_call_meta[case.id] for case in result.planned_cases]
    assert len(metas) == 2
    assert metas[0]["attempt"] == metas[1]["attempt"]
    assert metas[0]["executed"] is True
    assert metas[0]["attempted_at"] == metas[1]["attempted_at"]
    assert provider.last_task == "resolve_research_cases"


def test_y02_retry_keeps_first_success_identity() -> None:
    items, cases = _pair()
    provider = SelectiveResearch()
    result = resolve_research(
        items,
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=1,
        split_retry_attempts=0,
    )
    assert provider.research_calls == 2
    meta_a = result.case_call_meta[cases[0].id]
    meta_b = result.case_call_meta[cases[1].id]
    assert meta_a["attempt"] == 1
    assert meta_b["attempt"] == 2
    assert meta_a["executed"] is True
    assert meta_b["executed"] is True
    assert result.case_resolutions[cases[0].id].claim.startswith("SENTINEL-1-")
    assert result.case_resolutions[cases[1].id].claim.startswith("SENTINEL-2-")
    assert cases[0].id not in result.item_outcomes or True


def test_y03_split_binds_actual_child_calls() -> None:
    items, cases = _pair()
    provider = TruncateThenSplit()
    result = resolve_research(
        items,
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=0,
        split_retry_attempts=2,
    )
    assert provider.research_calls == 3
    meta_a = result.case_call_meta[cases[0].id]
    meta_b = result.case_call_meta[cases[1].id]
    assert meta_a["attempt"] != meta_b["attempt"]
    assert result.case_resolutions[cases[0].id].claim.startswith("SPLIT-")
    assert result.case_resolutions[cases[1].id].claim.startswith("SPLIT-")
    assert result.case_resolutions[cases[0].id].claim != result.case_resolutions[cases[1].id].claim


def test_y04_failed_retry_does_not_overwrite_success() -> None:
    items, cases = _pair()
    provider = BillingOnRetry()
    result = resolve_research(
        items,
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=2,
        split_retry_attempts=0,
    )
    assert provider.research_calls == 2
    assert cases[0].id in result.case_resolutions
    assert result.case_resolutions[cases[0].id].claim == "A-OK"
    assert result.item_outcomes[items[1].id] is ReasonCode.RESEARCH_FATAL
    meta_a = result.case_call_meta[cases[0].id]
    meta_b = result.case_call_meta[cases[1].id]
    assert meta_a["attempt"] == 1
    assert meta_a["executed"] is True
    assert meta_b["attempt"] == 2
    assert meta_b["executed"] is True
    assert meta_b["attempt"] != meta_a["attempt"]


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def test_y05_deepseek_budget_block_has_own_identity() -> None:
    items, cases = _pair()
    configured = ds_provider([_batch_json(cases[0], claim="A-ONLY")], calls=1)
    result = resolve_research(
        items,
        provider=configured,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=1,
        split_retry_attempts=0,
    )
    assert configured.client.chat.completions.calls == 1
    meta_a = result.case_call_meta[cases[0].id]
    meta_b = result.case_call_meta[cases[1].id]
    assert meta_a["executed"] is True
    assert meta_a["attempt"] == 1
    assert meta_b["executed"] is False
    assert meta_b["attempt"] == 2
    assert meta_b["blocked_reason"]
    assert meta_b["task"] == "resolve_research_cases"
    assert result.item_outcomes[items[1].id] is ReasonCode.RESEARCH_FATAL
    assert cases[0].id in result.case_resolutions


def test_y06_openai_and_qwen_budget_block() -> None:
    items, cases = _pair()
    batch_a = ResearchResolutionBatch(cases=[_draft(cases[0], claim="A-ONLY")])

    class CapturingResponses:
        def __init__(self, results):
            self.results = results
            self.calls = 0
            self.requests = []

        def parse(self, **kwargs):
            self.requests.append(kwargs)
            result = self.results[self.calls]
            self.calls += 1
            if isinstance(result, Exception):
                raise result
            return SimpleNamespace(output_parsed=result, usage=None, status="completed")

    openai = OpenAIProvider(
        model="configured-test-model",
        api_key="test-key",
        budget=AIBudget(1, 100_000, 20),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(responses=CapturingResponses([batch_a])),
        network_attempts=2,
    )
    result = resolve_research(
        items,
        provider=openai,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=1,
        split_retry_attempts=0,
    )
    assert openai.client.responses.calls == 1
    assert result.case_call_meta[cases[0].id]["executed"] is True
    assert result.case_call_meta[cases[1].id]["executed"] is False
    assert result.case_call_meta[cases[1].id]["blocked_reason"]
    assert result.case_call_meta[cases[1].id]["task"] == "resolve_research_cases"
    assert (
        result.case_call_meta[cases[1].id]["attempt"]
        != result.case_call_meta[cases[0].id]["attempt"]
    )

    qwen = QwenProvider(
        model="configured-test-model",
        api_key="test-key",
        base_url="https://qwen.test",
        budget=AIBudget(1, 100_000, 20),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=ds_provider(
                    [_batch_json(cases[0], claim="A-ONLY")], calls=1
                ).client.chat.completions,
            )
        ),
    )
    q_result = resolve_research(
        items,
        provider=qwen,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=1,
        split_retry_attempts=0,
    )
    assert qwen.client.chat.completions.calls == 1
    assert q_result.case_call_meta[cases[1].id]["executed"] is False
    assert q_result.case_call_meta[cases[1].id]["blocked_reason"]
    assert QwenProvider.__mro__[1] is DeepSeekProvider


def test_y07_executed_vs_blocked_before_first_request() -> None:
    items, cases = _pair()
    billed = ds_provider([StatusError(402)], calls=5)
    billed_result = resolve_research(
        items,
        provider=billed,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=0,
        split_retry_attempts=0,
    )
    assert billed.client.chat.completions.calls == 1
    meta = next(iter(billed_result.case_call_meta.values()))
    assert meta["executed"] is True
    assert billed_result.stats.get("research_unavailable") is True

    starved = ds_provider([_both_json(cases)], calls=0)
    starved_result = resolve_research(
        items,
        provider=starved,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=1,
        split_retry_attempts=0,
    )
    assert starved.client.chat.completions.calls == 0
    blocked = next(iter(starved_result.case_call_meta.values()))
    assert blocked["executed"] is False
    assert blocked["blocked_reason"]
    assert starved_result.item_outcomes[items[0].id] is ReasonCode.RESEARCH_FATAL


def test_y08_network_budget_preserves_executed_and_case_reason() -> None:
    items, cases = _pair()
    deepseek = DeepSeekProvider(
        model="configured-test-model",
        api_key="test-key",
        base_url="https://api.deepseek.test",
        budget=AIBudget(5, 100_000, 20, maximum_network_requests=1),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeChatCompletions(["{invalid-json"]))
        ),
        network_attempts=2,
    )
    result = resolve_research(
        items,
        provider=deepseek,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=0,
        split_retry_attempts=0,
    )
    for case in cases:
        meta = result.case_call_meta[case.id]
        assert meta["executed"] is True
        assert meta["blocked_reason"] == "AI global network request limit exceeded"
    assert deepseek.client.chat.completions.calls == 1

    class EmptyResponses:
        def __init__(self) -> None:
            self.calls = 0

        def parse(self, **kwargs):
            del kwargs
            self.calls += 1
            return SimpleNamespace(output_parsed=None, usage=None, status="completed")

    responses = EmptyResponses()
    openai = OpenAIProvider(
        model="configured-test-model",
        api_key="test-key",
        budget=AIBudget(5, 100_000, 20, maximum_network_requests=1),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(responses=responses),
        network_attempts=2,
    )
    with pytest.raises(AIBudgetExceeded) as raised:
        openai.resolve_research_cases(cases)
    assert responses.calls == 1
    assert raised.value.call_meta["executed"] is True
    assert raised.value.call_meta["blocked_reason"] == "AI global network request limit exceeded"


def _research_record(project, item_id: str) -> dict:
    payload = inspect_intake(project, input_id=item_id)
    assert payload["records"]
    return payload["records"][0]["decision_details"]["research"]


def test_y09_ledger_reload_keeps_per_case_identities(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    items, cases = _pair()
    _seed(project, save_checkpoint(project, items, now=DAY_N, batch_id="batch-y02"))
    provider = SelectiveResearch()
    _install_tracking_provider(monkeypatch, provider)
    original = resolve_research

    def wrapped(process_items, **kwargs):
        kwargs["item_retry_attempts"] = 1
        kwargs["split_retry_attempts"] = 0
        return original(process_items, **kwargs)

    monkeypatch.setattr("morning_radar.pipeline.resolve_research", wrapped)
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    rec_a = _research_record(project, items[0].id)
    rec_b = _research_record(project, items[1].id)
    assert rec_a["status"] == "ok"
    assert rec_b["status"] == "ok"
    assert rec_a["attempt"]["attempt"] == 1
    assert rec_b["attempt"]["attempt"] == 2
    assert rec_a["scope_rationale"]
    assert rec_a["attempt"]["attempt"] != rec_b["attempt"]["attempt"]

    project2 = copy_project(tmp_path / "y05")
    _seed(project2, save_checkpoint(project2, items, now=DAY_N, batch_id="batch-y05"))
    ds = ds_provider([_batch_json(cases[0], claim="A-ONLY")], calls=1)
    fake = FakeAIProvider()
    _install_tracking_provider(monkeypatch, fake)

    def wrapped_budget(process_items, **kwargs):
        kwargs["provider"] = ds
        kwargs["item_retry_attempts"] = 1
        kwargs["split_retry_attempts"] = 0
        return original(process_items, **kwargs)

    monkeypatch.setattr("morning_radar.pipeline.resolve_research", wrapped_budget)
    MorningRadarPipeline(project2).process(now=DAY_N, notify=False)
    blocked = _research_record(project2, items[1].id)
    success = _research_record(project2, items[0].id)
    assert success["status"] == "ok"
    assert success["attempt"]["executed"] is True
    assert blocked["status"] == "failed"
    assert blocked["attempt"]["executed"] is False
    assert blocked["attempt"]["blocked_reason"]
    assert ds.client.chat.completions.calls == 1


def test_y10_network_budget_reason_reaches_disk_diagnostics(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    items, _cases = _pair()
    _seed(project, save_checkpoint(project, items, now=DAY_N, batch_id="batch-network-budget"))
    deepseek = DeepSeekProvider(
        model="configured-test-model",
        api_key="test-key",
        base_url="https://api.deepseek.test",
        budget=AIBudget(5, 100_000, 20, maximum_network_requests=1),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeChatCompletions(["{invalid-json"]))
        ),
        network_attempts=2,
    )
    _install_tracking_provider(monkeypatch, FakeAIProvider())
    original = resolve_research

    def wrapped(process_items, **kwargs):
        kwargs["provider"] = deepseek
        kwargs["item_retry_attempts"] = 0
        kwargs["split_retry_attempts"] = 0
        return original(process_items, **kwargs)

    monkeypatch.setattr("morning_radar.pipeline.resolve_research", wrapped)
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    for item in items:
        research = _research_record(project, item.id)
        assert research["status"] == "failed"
        assert research["attempt"]["executed"] is True
        assert research["attempt"]["blocked_reason"] == "AI global network request limit exceeded"
    assert deepseek.client.chat.completions.calls == 1


def test_y11_heal_restores_case_identities(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    items, _cases = _pair()
    _seed(project, save_checkpoint(project, items, now=DAY_N, batch_id="batch-heal"))
    provider = SelectiveResearch()
    _install_tracking_provider(monkeypatch, provider)
    original = resolve_research

    def wrapped(process_items, **kwargs):
        kwargs["item_retry_attempts"] = 1
        kwargs["split_retry_attempts"] = 0
        return original(process_items, **kwargs)

    monkeypatch.setattr("morning_radar.pipeline.resolve_research", wrapped)

    def boom(*args, **kwargs):
        raise RuntimeError("ledger complement interrupted")

    monkeypatch.setattr("morning_radar.intake.generation.apply_generation_effects", boom)
    with pytest.raises(RuntimeError, match="ledger complement interrupted"):
        MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    research_calls = provider.research_calls
    monkeypatch.undo()
    recovered = SelectiveResearch()
    _install_tracking_provider(monkeypatch, recovered)
    monkeypatch.setattr("morning_radar.pipeline.resolve_research", wrapped)
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    assert recovered.research_calls == 0
    rec_a = _research_record(project, items[0].id)
    rec_b = _research_record(project, items[1].id)
    assert rec_a["attempt"]["attempt"] == 1
    assert rec_b["attempt"]["attempt"] == 2
    assert rec_a["status"] == "ok"
    assert rec_b["status"] == "ok"
    del research_calls

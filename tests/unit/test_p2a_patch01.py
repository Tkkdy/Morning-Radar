from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from morning_radar.ai import AIBudget, AIBudgetExceeded, AIOutputError, FakeAIProvider
from morning_radar.ai.deepseek_provider import DeepSeekProvider
from morning_radar.ai.errors import AIAuthenticationError, AIBillingUnavailable
from morning_radar.ai.models import (
    ClassificationBatch,
    ClassifiedItem,
    ResearchResolutionBatch,
    ResearchResolutionDraft,
    StoryScore,
)
from morning_radar.ai.openai_provider import OpenAIProvider
from morning_radar.ai.qwen_provider import QwenProvider
from morning_radar.ai.request_payload import (
    build_topic_context,
    compact_evidence,
    dumps,
    evidence_snapshot,
    fit_research_request,
    get_call_meta,
    research_request_payload,
    score_story_payload,
)
from morning_radar.cli import main as cli_main
from morning_radar.intake.identity import content_version, intake_key
from morning_radar.intake.inspect import format_inspect_summary, inspect_intake
from morning_radar.intake.models import ProcessingStatus, PublishStatus, ReasonCode
from morning_radar.models import (
    PublishedAtRole,
    RawItem,
    ResearchDisposition,
    SourceRole,
    StatementType,
    Story,
)
from morning_radar.pipeline import MorningRadarPipeline, _artifact_digest
from morning_radar.research.engine import build_research_cases, resolve_research
from tests.unit.test_deepseek_provider import classification_json
from tests.unit.test_deepseek_provider import provider as ds_provider
from tests.unit.test_phase1_patch import DAY_N, copy_project, official_item, save_checkpoint
from tests.unit.test_phase1_patch03 import _install_tracking_provider, _ledger, _seed, _version_item

SGT = ZoneInfo("Asia/Singapore")
EVENTS_PATH = Path("tests/fixtures/coverage/p2a_events.json")
BRIEF_SECTIONS = (
    "top_stories",
    "ai_and_open_source",
    "developer_discussions",
    "other_reading",
    "market_and_companies",
)


def _install(monkeypatch, provider=None):
    return _install_tracking_provider(monkeypatch, provider or FakeAIProvider())


def _record(project, item_id: str) -> dict:
    payload = inspect_intake(project, input_id=item_id)
    assert payload["records"]
    return payload["records"][0]


def _records(project, item_id: str) -> list[dict]:
    return inspect_intake(project, input_id=item_id)["records"]


def _practitioner(
    suffix: str, *, published_at, title: str, url: str, excerpt: str, score: int = 40
) -> RawItem:
    return RawItem(
        id=f"item-{suffix}",
        title=title,
        url=url,
        source_name="Blog",
        source_type="rss",
        published_at=published_at,
        fetched_at=published_at,
        summary=excerpt,
        content_excerpt=excerpt,
        source_role=SourceRole.PRACTITIONER,
        statement_type=StatementType.FIRSTHAND_OBSERVATION,
        company_candidates=["openai"],
        metadata={"score": score, "content_version": "v1"},
    )


class TrackingFake(FakeAIProvider):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.research_calls = 0

    def resolve_research_cases(self, cases):
        self.research_calls += 1
        return super().resolve_research_cases(cases)


class ScoreBoom(TrackingFake):
    def score_story(self, story: Story) -> StoryScore:
        self._record("score_story", score_story_payload(story, self.topic_context))
        raise AIOutputError("invalid score json")


class TruncatingResearch(TrackingFake):
    def resolve_research_cases(self, cases):
        self.research_calls += 1
        self._record(
            "resolve_research_cases", research_request_payload(cases, self.topic_context)
        )
        raise AIOutputError("structured output truncated")


class FatalResearch(TrackingFake):
    def __init__(self, exc: Exception, **kwargs) -> None:
        super().__init__(**kwargs)
        self.exc = exc

    def resolve_research_cases(self, cases):
        self.research_calls += 1
        self._record(
            "resolve_research_cases", research_request_payload(cases, self.topic_context)
        )
        raise self.exc


class PartialResearch(TrackingFake):
    def resolve_research_cases(self, cases):
        self.research_calls += 1
        batch = super().resolve_research_cases(cases)
        keep = [item for item in batch.cases if cases and item.case_id == cases[0].id]
        return ResearchResolutionBatch(cases=keep)


def _budget_pair():
    items = [
        _practitioner(
            "lead-a",
            published_at=DAY_N - timedelta(hours=3),
            title="Practitioner observed structured output truncation in production",
            url="https://example.com/a",
            excerpt=("alpha excerpt " * 40),
            score=50,
        ),
        _practitioner(
            "lead-b",
            published_at=DAY_N - timedelta(hours=2),
            title="Practitioner reproduced a second independent workflow failure",
            url="https://example.com/b",
            excerpt=("bravo excerpt " * 40),
            score=40,
        ),
    ]
    cases = build_research_cases(items, maximum_cases=8)
    ctx = build_topic_context(None)
    first = dumps(research_request_payload(cases[:1], ctx))
    both = dumps(research_request_payload(cases, ctx))
    return items, cases, ctx, first, both


def test_x01_x02_stage_metadata_survives_write_brief(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    left = official_item(
        "alpha", published_at=DAY_N - timedelta(hours=3), title="Alpha launch notes"
    )
    right = official_item(
        "beta", published_at=DAY_N - timedelta(hours=2), title="Beta launch notes"
    )
    _seed(project, save_checkpoint(project, [left, right], now=DAY_N, batch_id="batch-meta"))
    provider = _install(monkeypatch, TrackingFake())
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    write_hash = provider.last_prompt_hash
    assert provider.last_task == "write_brief"
    left_rec = _record(project, left.id)
    right_rec = _record(project, right.id)
    left_cls = left_rec["decision_details"]["classification"]["attempt"]
    left_score = left_rec["decision_details"]["score"]
    right_score = right_rec["decision_details"]["score"]
    assert left_cls["task"] == "classify"
    assert left_cls["prompt_hash"] != write_hash
    assert left_score["attempt"]["task"] == "score_story"
    assert right_score["attempt"]["task"] == "score_story"
    assert left_score["attempt"]["prompt_hash"] != write_hash
    assert left_score["attempt"]["attempt"] != right_score["attempt"]["attempt"]
    assert left_score["story_id"] != right_score["story_id"]


def test_x03_failed_then_successful_attempt_is_current(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    item = official_item(
        "retry", published_at=DAY_N - timedelta(hours=2), title="Official retry launch"
    )
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-retry"))
    _install(monkeypatch, ScoreBoom())
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    failed = _record(project, item.id)
    assert failed["reason_code"] == ReasonCode.SCORE_FAILED.value
    assert failed["decision_details"]["score"]["status"] == "failed"
    assert failed["decision_details"]["score"]["model_explanation"] is None
    assert failed["decision_details"]["score"]["relevance_score"] is None
    assert failed["decision_details"]["score"]["attempt"]["task"] == "score_story"
    _install(monkeypatch, TrackingFake())
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    success = _record(project, item.id)
    assert success["decision_details"]["score"]["status"] == "ok"
    assert success["decision_details"]["score"]["model_explanation"]
    assert success["processing"] == ProcessingStatus.COMPLETED.value


def test_x04_research_success_survives_story_generation(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    published = DAY_N - timedelta(hours=2)
    lead = _practitioner(
        "verified",
        published_at=published,
        title="Practitioner confirmed an official model launch with logs",
        url="https://openai.com/index/verified-launch",
        excerpt="The official page describes a shipping model change and API access.",
    )
    support = official_item(
        "verified-official", published_at=published, title="Official verified launch notes"
    ).model_copy(update={"url": lead.url, "company_candidates": ["openai"]})
    _seed(project, save_checkpoint(project, [lead, support], now=DAY_N, batch_id="batch-research"))
    _install(monkeypatch, TrackingFake())
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    rec = _record(project, lead.id)
    research = rec["decision_details"]["research"]
    assert rec["processing"] == ProcessingStatus.COMPLETED.value
    assert research["status"] == "ok"
    assert research["scope_rationale"]
    assert research["disposition"]
    assert research["attempt"]["task"] == "resolve_research_cases"
    reloaded = _record(project, lead.id)
    assert (
        reloaded["decision_details"]["research"]["scope_rationale"]
        == research["scope_rationale"]
    )


def test_x05_score_failed_vs_not_run(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    scored = official_item(
        "need-score", published_at=DAY_N - timedelta(hours=2), title="Needs scoring"
    )
    skipped = official_item(
        "skip-score", published_at=DAY_N - timedelta(hours=1), title="Skip scoring"
    )
    _seed(project, save_checkpoint(project, [scored, skipped], now=DAY_N, batch_id="batch-score"))
    provider = ScoreBoom(
        classify_overrides={
            skipped.id: ClassifiedItem(
                item_id=skipped.id,
                relevant=False,
                relevance_reason="V2-skip-sentinel",
                important=False,
                importance_reason="Not an AI product event.",
                category="other_reading",
            )
        }
    )
    _install(monkeypatch, provider)
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    failed = _record(project, scored.id)
    ignored = _record(project, skipped.id)
    assert failed["decision_details"]["score"]["status"] == "failed"
    assert failed["decision_details"]["score"]["model_explanation"] is None
    assert ignored["reason_code"] == ReasonCode.CLASSIFIED_IRRELEVANT.value
    assert ignored["decision_details"]["score"]["status"] == "not_run"
    assert ignored["decision_details"]["score"]["relevance_score"] is None


def test_x06_merged_story_and_version_reasons(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    left = official_item(
        "merge-l", published_at=DAY_N - timedelta(hours=3), title="Same Launch Title"
    )
    right = official_item(
        "merge-r", published_at=DAY_N - timedelta(hours=2), title="Same Launch Title"
    ).model_copy(update={"source_name": "The Verge AI"})
    _seed(project, save_checkpoint(project, [left, right], now=DAY_N, batch_id="batch-merge"))
    provider = TrackingFake(
        classify_overrides={
            left.id: ClassifiedItem(
                item_id=left.id,
                relevant=True,
                relevance_reason="LEFT-SENTINEL",
                important=True,
                importance_reason="left importance",
                category="ai_and_open_source",
            ),
            right.id: ClassifiedItem(
                item_id=right.id,
                relevant=True,
                relevance_reason="RIGHT-SENTINEL",
                important=True,
                importance_reason="right importance",
                category="ai_and_open_source",
            ),
        }
    )
    _install(monkeypatch, provider)
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    left_rec = _record(project, left.id)
    right_rec = _record(project, right.id)
    assert left_rec["decision_details"]["classification"]["relevance_reason"] == "LEFT-SENTINEL"
    assert right_rec["decision_details"]["classification"]["relevance_reason"] == "RIGHT-SENTINEL"
    assert left_rec["story_id"] == right_rec["story_id"]
    assert left_rec["decision_details"]["score"]["story_level"] is True
    keys = set(left_rec["decision_details"]["score"]["participating_input_keys"])
    assert intake_key(left.id, left_rec["content_version"]) in keys
    assert intake_key(right.id, right_rec["content_version"]) in keys
    assert (
        left_rec["decision_details"]["score"]["attempt"]
        == right_rec["decision_details"]["score"]["attempt"]
    )

    project2 = copy_project(tmp_path / "v")
    v1 = _version_item(
        "vers",
        title="Versioned original",
        excerpt="old-sentinel",
        published_at=DAY_N - timedelta(hours=4),
        fetched_at=DAY_N - timedelta(hours=4),
    )
    _seed(project2, save_checkpoint(project2, [v1], now=DAY_N, batch_id="batch-v1"))
    _install(
        monkeypatch,
        TrackingFake(
            classify_overrides={
                v1.id: ClassifiedItem(
                    item_id=v1.id,
                    relevant=True,
                    relevance_reason="V1-SENTINEL",
                    important=True,
                    importance_reason="v1",
                    category="ai_and_open_source",
                )
            }
        ),
    )
    MorningRadarPipeline(project2).process(now=DAY_N, notify=False)
    v2 = _version_item(
        "vers",
        title="Versioned revised",
        excerpt="new-sentinel",
        published_at=DAY_N - timedelta(hours=4),
        fetched_at=DAY_N + timedelta(minutes=5),
    )
    _seed(
        project2,
        save_checkpoint(project2, [v1, v2], now=DAY_N + timedelta(minutes=5), batch_id="batch-v2"),
        now=DAY_N + timedelta(minutes=5),
    )
    _install(
        monkeypatch,
        TrackingFake(
            classify_overrides={
                v2.id: ClassifiedItem(
                    item_id=v2.id,
                    relevant=True,
                    relevance_reason="V2-SENTINEL",
                    important=True,
                    importance_reason="v2",
                    category="ai_and_open_source",
                )
            }
        ),
    )
    MorningRadarPipeline(project2).process(now=DAY_N + timedelta(minutes=5), notify=False)
    versions = _records(project2, v1.id)
    assert len(versions) >= 2
    reasons = {
        item["decision_details"]["classification"]["relevance_reason"]
        for item in versions
        if item["decision_details"]["classification"]["relevance_reason"]
    }
    assert "V1-SENTINEL" in reasons
    assert "V2-SENTINEL" in reasons


def test_x07_x08_budget_keeps_identity_and_omission_reasons() -> None:
    item = RawItem(
        id="item-hn-budget",
        title="HN found an official model launch with extra words",
        url="https://openai.com/index/demo-launch",
        source_name="Hacker News",
        source_type="hacker_news",
        published_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
        fetched_at=datetime(2026, 9, 1, 13, tzinfo=UTC),
        summary="Community submission pointing at an official page.",
        content_excerpt="The official page was not fetched in this run. Extra body." * 3,
        source_role=SourceRole.PRACTITIONER,
        statement_type=StatementType.FIRSTHAND_OBSERVATION,
        metadata={
            "discussion_url": "https://news.ycombinator.com/item?id=1",
            "score": 80,
            "content_version": "v1",
        },
    )
    ref = evidence_snapshot(item, association_basis="lead", content_version="v1")
    compact = compact_evidence(ref)
    assert ref.content_missing is False
    assert compact.content_version == "v1"
    assert compact.source_type == "hacker_news"
    assert compact.published_at == item.published_at
    assert compact.published_at_role is PublishedAtRole.HN_SUBMISSION_TIME
    assert compact.fetched_at == item.fetched_at
    assert compact.content_missing is False
    assert compact.text_omission_reason == "budget_omitted"
    missing = evidence_snapshot(
        item.model_copy(update={"summary": "", "content_excerpt": ""}),
        association_basis="lead",
        content_version="v1",
    )
    assert missing.content_missing is True
    assert compact_evidence(missing).content_missing is True
    items, cases, ctx, first, both = _budget_pair()
    provider = TrackingFake()
    wide = fit_research_request(cases, topic_context=ctx, maximum_characters=len(both) + 50)
    assert not wide.omitted
    tiny = fit_research_request(cases, topic_context=ctx, maximum_characters=20)
    assert tiny.unexecuted is True
    assert tiny.included == []
    before = provider.research_calls
    result = resolve_research(
        items,
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        maximum_input_characters=20,
    )
    assert provider.research_calls == before
    assert result.stats["research_logical_ai_calls"] == 0
    assert items[0].metadata["content_version"] == "v1"
    mid = fit_research_request(cases, topic_context=ctx, maximum_characters=len(first) + 30)
    assert len(mid.payload_text) <= len(first) + 30


def test_x09_x10_omitted_cases_survive_failed_and_fatal_research() -> None:
    items, cases, ctx, first, both = _budget_pair()
    limit = (len(first) + len(both)) // 2
    fitted = fit_research_request(cases, topic_context=ctx, maximum_characters=limit)
    if cases[1].id not in fitted.omitted:
        limit = len(first) + 10
        fitted = fit_research_request(cases, topic_context=ctx, maximum_characters=limit)
    assert cases[1].id in fitted.omitted
    trunc = TruncatingResearch()
    truncated = resolve_research(
        items,
        provider=trunc,
        maximum_cases=8,
        maximum_radar_signals=3,
        maximum_input_characters=limit,
        item_retry_attempts=0,
        split_retry_attempts=0,
    )
    assert truncated.omitted_cases[cases[1].id] == "research_input_budget"
    assert truncated.item_outcomes[items[1].id] is ReasonCode.RESEARCH_DEFERRED
    assert truncated.item_outcomes[items[0].id] in {
        ReasonCode.RESEARCH_OUTPUT_TRUNCATED,
        ReasonCode.RESEARCH_OUTPUT_INVALID,
        ReasonCode.RESEARCH_CASE_MISSING,
    }
    assert trunc.research_calls == 1
    for exc in (
        AIBillingUnavailable("402 payment required"),
        AIAuthenticationError("invalid api key"),
        AIBudgetExceeded("global character budget"),
    ):
        provider = FatalResearch(exc)
        result = resolve_research(
            items,
            provider=provider,
            maximum_cases=8,
            maximum_radar_signals=3,
            maximum_input_characters=limit,
            item_retry_attempts=2,
            split_retry_attempts=2,
        )
        assert provider.research_calls == 1
        assert result.omitted_cases[cases[1].id] == "research_input_budget"
        assert result.item_outcomes[items[1].id] is ReasonCode.RESEARCH_DEFERRED
        assert result.item_outcomes[items[0].id] is ReasonCode.RESEARCH_FATAL
    mixed = PartialResearch()
    mixed_result = resolve_research(
        items,
        provider=mixed,
        maximum_cases=8,
        maximum_radar_signals=3,
        maximum_input_characters=len(both) + 100,
        item_retry_attempts=0,
        split_retry_attempts=0,
    )
    assert mixed_result.case_resolutions
    assert items[1].id in mixed_result.item_outcomes


def test_x09_process_disk_keeps_omitted_and_failed_research(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    items, cases, ctx, first, both = _budget_pair()
    limit = (len(first) + len(both)) // 2
    fitted = fit_research_request(cases, topic_context=ctx, maximum_characters=limit)
    if cases[1].id not in fitted.omitted:
        limit = len(first) + 10
    _seed(project, save_checkpoint(project, items, now=DAY_N, batch_id="batch-omit"))
    _install(monkeypatch, TruncatingResearch())
    original = resolve_research

    def wrapped(process_items, **kwargs):
        kwargs["maximum_input_characters"] = limit
        kwargs["item_retry_attempts"] = 0
        kwargs["split_retry_attempts"] = 0
        return original(process_items, **kwargs)

    monkeypatch.setattr("morning_radar.pipeline.resolve_research", wrapped)
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    rec_a = _record(project, items[0].id)
    rec_b = _record(project, items[1].id)
    assert rec_a["decision_details"]["research"]["status"] == "failed"
    assert rec_b["decision_details"]["research"]["status"] == "omitted_budget"
    assert rec_b["decision_details"]["research"]["budget_reason"] == "research_input_budget"
    assert rec_b["decision_details"]["research"]["attempt"]["executed"] is False


def test_x11_heal_restores_stage_metadata_without_extra_calls(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    item = official_item(
        "heal-meta", published_at=DAY_N - timedelta(hours=2), title="Heal metadata launch"
    )
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-heal-meta"))
    provider = _install(monkeypatch, TrackingFake())

    def boom(*args, **kwargs):
        raise RuntimeError("ledger complement interrupted")

    monkeypatch.setattr("morning_radar.intake.generation.apply_generation_effects", boom)
    with pytest.raises(RuntimeError, match="ledger complement interrupted"):
        MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    assert provider.classified_titles
    monkeypatch.undo()
    recovered = _install(monkeypatch, TrackingFake())
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    rec = _record(project, item.id)
    assert recovered.classified_titles == []
    assert rec["decision_details"]["classification"]["attempt"]["task"] == "classify"
    assert rec["decision_details"]["score"]["attempt"]["task"] == "score_story"
    assert rec["decision_details"]["classification"]["attempt"]["prompt_hash"]
    assert rec["decision_details"]["score"]["attempt"]["policy_hash"]


def test_x12_deploy_legacy_and_unknown_url(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    item = official_item(
        "deploy-meta", published_at=DAY_N - timedelta(hours=2), title="Deployed launch"
    )
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-deploy-meta"))
    _install(monkeypatch, TrackingFake())
    brief = MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    digest = _artifact_digest(project / "data/briefs" / f"{brief.date}.json")
    monkeypatch.chdir(project)
    assert cli_main(["record-deploy", "--date", str(brief.date), "--brief-hash", digest]) == 0
    entry = _ledger(project).get(item.id, content_version(item))
    assert entry.publish is PublishStatus.DEPLOY_CONFIRMED
    details = entry.decision_details.model_dump(mode="json")
    deployed_at = entry.deployed_at
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    again = _ledger(project).get(item.id, content_version(item))
    assert again.publish is PublishStatus.DEPLOY_CONFIRMED
    assert again.deployed_at == deployed_at
    assert again.decision_details.model_dump(mode="json") == details
    unknown = inspect_intake(copy_project(tmp_path / "u"), url="https://example.com/never-collected")
    assert unknown["found"] is False
    assert unknown["coverage_gap"] is False
    legacy_project = copy_project(tmp_path / "legacy")
    ledger_path = legacy_project / "data/intake/ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": {
                    "item-legacy::v1": {
                        "input_id": "item-legacy",
                        "content_version": "v1",
                        "processing": "completed",
                        "evidence": "not_evaluated",
                        "publish": "not_generated",
                        "stage": "story",
                        "outcome": "processed",
                        "reason_code": "processed",
                        "run_id": "run-legacy",
                        "updated_at": DAY_N.isoformat(),
                        "attempt_count": 1,
                        "last_input_version": "v1",
                        "first_seen_at": DAY_N.isoformat(),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    payload = inspect_intake(legacy_project, input_id="item-legacy")
    assert payload["found"] is True
    assert "legacy_unavailable" in format_inspect_summary(payload)


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _brief_item(brief: dict, item_id: str):
    for section in BRIEF_SECTIONS:
        for item in brief.get(section) or []:
            if item.get("id") == item_id:
                return section, item
    return None


def test_x13_event_fixture_dates_ids_and_facets() -> None:
    data = _load_json(EVENTS_PATH)
    events = {item["event_id"]: item for item in data["events"]}
    assert len(events) == 15
    assert {item["run_id"] for item in data["runs"]} == {"O01", "O02"}
    for event in data["events"]:
        assert event.get("expected_brief_date") or event.get("expected_brief_date_range")
        assert event.get("expected_brief_date_basis")
        assert event.get("published_at_role")
        assert event.get("original_time_precision")
        assert len(event.get("required_facets") or []) >= 2
        assert event.get("facet_explanation")
        if event["historical_observation"] == "完整覆盖":
            displayed = event.get("displayed") or {}
            assert event.get("covered_facets")
            assert displayed.get("brief_item_id")
            assert displayed.get("section") in BRIEF_SECTIONS
        collected = event.get("collected") or {}
        if collected.get("raw"):
            raw_path = Path(collected["raw"])
            assert raw_path.exists()
            raw_items = _load_json(raw_path)
            assert collected["raw_item_id"] in {item["id"] for item in raw_items}
            raw_item = next(item for item in raw_items if item["id"] == collected["raw_item_id"])
            if event["event_id"] in {"E08", "E09"}:
                assert event["original_published_at"] == raw_item["published_at"]
        displayed = event.get("displayed") or {}
        if displayed.get("brief") and displayed.get("brief_item_id"):
            found = _brief_item(_load_json(Path(displayed["brief"])), displayed["brief_item_id"])
            assert found is not None
            section, item = found
            assert section == displayed["section"]
            if displayed.get("story_id"):
                assert displayed["story_id"] in (item.get("story_ids") or [])
        story = event.get("story") or {}
        if story.get("path"):
            match = next(
                item for item in _load_json(Path(story["path"])) if item["id"] == story["story_id"]
            )
            if story.get("raw_item_id"):
                assert story["raw_item_id"] in match["source_item_ids"]
        if event.get("original_published_at") and event["event_id"] in {"E08", "E09"}:
            original = datetime.fromisoformat(event["original_published_at"].replace("Z", "+00:00"))
            assert event["original_date"] == original.astimezone(SGT).date().isoformat()
            assert event["expected_brief_date"] != event["original_date"]
    assert events["E08"]["original_date"] == "2026-09-06"
    assert events["E08"]["expected_brief_date"] == "2026-09-07"
    assert events["E08"]["collected"]["raw_item_id"] == "item-3784d7aba9718af9aa19"
    assert events["E09"]["original_date"] == "2026-09-07"
    assert events["E09"]["expected_brief_date"] == "2026-09-08"
    assert events["E09"]["collected"]["raw_item_id"] == "item-dafe5c0295f8273a8c90"
    assert events["E11"]["displayed"]["brief_item_id"] == "brief-eba3309beb7b9c1f7f28"
    assert events["E11"]["displayed"]["story_id"] == "story-b719df82270b277ca939"
    assert events["E11"]["displayed"]["section"] == "ai_and_open_source"
    assert events["E11"]["official_sources"] == [
        "https://blog.google/innovation-and-ai/technology/google-ai-updates-august-2026/"
    ]
    assert events["E04"]["collected"]["raw"] is None
    assert not Path("data/briefs/2026-09-04.json").exists()


def _score_json() -> str:
    return StoryScore(
        relevance_score=0.9,
        importance_score=0.8,
        novelty_score=0.7,
        credibility_score=0.6,
        explanation="测试评分理由。",
    ).model_dump_json()


def _research_json(case_id: str) -> str:
    return ResearchResolutionBatch(
        cases=[
            ResearchResolutionDraft(
                case_id=case_id,
                in_scope=True,
                scope_rationale="该观察直接涉及 AI 模型或产品行为。",
                disposition=ResearchDisposition.RADAR_SIGNAL,
                statement_type=StatementType.FIRSTHAND_OBSERVATION,
                claim="开发者观察到结构化输出发生截断。",
                why_notable="该观察可能影响 AI 开发者实践。",
                uncertainty="当前仍需验证。",
            )
        ]
    ).model_dump_json()


def test_x14_deepseek_openai_qwen_bind_real_call_meta() -> None:
    item = official_item(
        "meta", published_at=DAY_N - timedelta(hours=1), title="Official meta launch"
    )
    story = Story(
        id="story-meta",
        canonical_title="官方发布",
        category="ai_and_open_source",
        updated_at=DAY_N,
        source_item_ids=[item.id],
        source_urls=[item.url],
        primary_source_url=item.url,
        facts=["官方发布了产品。"],
        relevance_score=0.0,
        importance_score=0.0,
        novelty_score=0.0,
        credibility_score=0.0,
    )
    cases = build_research_cases(
        [
            item.model_copy(
                update={
                    "source_role": SourceRole.PRACTITIONER,
                    "title": "Practitioner observed official meta launch details today",
                    "statement_type": StatementType.FIRSTHAND_OBSERVATION,
                    "summary": "Concrete practitioner notes about the launch.",
                    "content_excerpt": "Concrete practitioner notes about the launch.",
                    "metadata": {"score": 20},
                }
            )
        ],
        maximum_cases=8,
    )
    configured = ds_provider([classification_json(), _score_json(), _research_json(cases[0].id)])
    classified = configured.classify_items([item])
    classify_meta = get_call_meta(classified)
    scored = configured.score_story(story)
    score_meta = get_call_meta(scored)
    researched = configured.resolve_research_cases(cases)
    research_meta = get_call_meta(researched)
    assert classify_meta["task"] == "classify"
    assert score_meta["task"] == "score_story"
    assert research_meta["task"] == "resolve_research_cases"
    assert classify_meta["prompt_hash"] != score_meta["prompt_hash"]
    assert score_meta["prompt_hash"] != research_meta["prompt_hash"]
    body = json.loads(configured.client.chat.completions.requests[0]["messages"][1]["content"])
    assert "topic_context" in body
    expected_cls = ClassificationBatch(
        items=[
            ClassifiedItem(
                item_id=item.id,
                relevant=True,
                relevance_reason="相关",
                important=True,
                importance_reason="重要",
                category="ai_and_open_source",
            )
        ]
    )
    expected_score = StoryScore(
        relevance_score=0.9,
        importance_score=0.8,
        novelty_score=0.7,
        credibility_score=0.6,
        explanation="测试评分理由。",
    )
    expected_research = ResearchResolutionBatch(
        cases=[
            ResearchResolutionDraft(
                case_id=cases[0].id,
                in_scope=True,
                scope_rationale="该观察直接涉及 AI 模型或产品行为。",
                disposition=ResearchDisposition.RADAR_SIGNAL,
                statement_type=StatementType.FIRSTHAND_OBSERVATION,
                claim="开发者观察到结构化输出发生截断。",
                why_notable="该观察可能影响 AI 开发者实践。",
                uncertainty="当前仍需验证。",
            )
        ]
    )

    class CapturingResponses:
        def __init__(self, results):
            self.results = results
            self.requests = []

        def parse(self, **kwargs):
            self.requests.append(kwargs)
            return SimpleNamespace(
                output_parsed=self.results[len(self.requests) - 1],
                usage=None,
                status="completed",
            )

    openai = OpenAIProvider(
        model="configured-test-model",
        api_key="test-key",
        budget=AIBudget(10, 100_000, 20),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(
            responses=CapturingResponses([expected_cls, expected_score, expected_research])
        ),
        network_attempts=2,
    )
    assert get_call_meta(openai.classify_items([item]))["task"] == "classify"
    assert get_call_meta(openai.score_story(story))["task"] == "score_story"
    assert get_call_meta(openai.resolve_research_cases(cases))["task"] == "resolve_research_cases"
    assert openai.client.responses.requests
    qwen = QwenProvider(
        model="configured-test-model",
        api_key="test-key",
        base_url="https://qwen.test",
        budget=AIBudget(10, 100_000, 20),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=ds_provider(
                    [classification_json(), _score_json(), _research_json(cases[0].id)]
                ).client.chat.completions
            )
        ),
    )
    assert get_call_meta(qwen.classify_items([item]))["task"] == "classify"
    assert issubclass(QwenProvider, DeepSeekProvider)
    assert QwenProvider.__mro__[1] is DeepSeekProvider

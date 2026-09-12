from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from morning_radar.ai import FakeAIProvider
from morning_radar.ai.deepseek_provider import DeepSeekProvider
from morning_radar.ai.models import ClassificationBatch, ClassifiedItem, StoryScore
from morning_radar.ai.qwen_provider import QwenProvider
from morning_radar.ai.request_payload import (
    EDITORIAL_GUIDANCE,
    build_topic_context,
    dumps,
    evidence_snapshot,
    fit_research_request,
    policy_hash,
    prompt_hash_for,
    score_story_payload,
)
from morning_radar.intake.inspect import inspect_intake
from morning_radar.intake.models import ReasonCode
from morning_radar.models import RawItem, SourceRole, StatementType, Story
from morning_radar.pipeline import MorningRadarPipeline
from morning_radar.processing.story_builder import build_stories
from morning_radar.research.engine import build_research_cases, resolve_research
from morning_radar.settings import TopicConfig
from tests.unit.test_deepseek_provider import classification_json
from tests.unit.test_deepseek_provider import provider as ds_provider
from tests.unit.test_phase1_patch import DAY_N, copy_project, official_item, save_checkpoint
from tests.unit.test_phase1_patch03 import _install_tracking_provider, _seed
from tests.unit.test_phase1_patch05 import _version_item


def _hn_item() -> RawItem:
    return RawItem(
        id="item-hn-official",
        title="HN found an official model launch",
        url="https://openai.com/index/demo-launch",
        source_name="Hacker News",
        source_type="hacker_news",
        published_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
        fetched_at=datetime(2026, 9, 1, 13, tzinfo=UTC),
        summary="Community submission pointing at an official page.",
        content_excerpt="The official page was not fetched in this run.",
        source_role=SourceRole.COMMUNITY_DISCOVERY,
        statement_type=StatementType.UNVERIFIED_LEAD,
        metadata={
            "discussion_url": "https://news.ycombinator.com/item?id=1",
            "selection_reason": "high_signal_discovery",
            "score": 100,
            "content_version": "v1",
        },
    )


def test_p2a_01_event_fixture_schema() -> None:
    data = json.loads(Path("tests/fixtures/coverage/p2a_events.json").read_text(encoding="utf-8"))
    events = {item["event_id"]: item for item in data["events"]}
    assert len(events) == 15
    assert {item["run_id"] for item in data["runs"]} == {"O01", "O02"}
    for event in data["events"]:
        assert event["historical_observation"] in {"完整覆盖", "部分覆盖", "未覆盖", "未知"}
        assert event["expected_treatment"] in {"建议报道", "可选", "合理不报", "待判断"}
        assert event["original_time_precision"]
        assert event.get("expected_brief_date") or event.get("expected_brief_date_range")
        assert event.get("published_at_role")
        assert len(event.get("required_facets") or []) >= 2
    assert events["E01"]["historical_observation"] == "部分覆盖"
    assert events["E04"]["collected"]["raw"] is None
    assert Path(events["E06"]["displayed"]["brief"]).exists()
    assert not Path("data/briefs/2026-09-04.json").exists()
    assert events["E08"]["original_date"] == "2026-09-06"
    assert events["E08"]["expected_brief_date"] == "2026-09-07"
    assert events["E09"]["original_date"] == "2026-09-07"
    assert events["E09"]["expected_brief_date"] == "2026-09-08"
    assert events["E11"]["displayed"]["brief_item_id"] == "brief-eba3309beb7b9c1f7f28"
    assert events["E11"]["displayed"]["section"] == "ai_and_open_source"


def test_p2a_04_research_request_contains_excerpt() -> None:
    from morning_radar.ai.models import ResearchResolutionBatch, ResearchResolutionDraft
    from morning_radar.models import ResearchDisposition

    lead = _hn_item().model_copy(
        update={"source_role": SourceRole.PRACTITIONER, "company_candidates": ["openai"]}
    )
    cases = build_research_cases([lead], maximum_cases=8)
    payload = ResearchResolutionBatch(
        cases=[
            ResearchResolutionDraft(
                case_id=cases[0].id,
                in_scope=True,
                scope_rationale="in scope",
                disposition=ResearchDisposition.RADAR_SIGNAL,
                statement_type=StatementType.FIRSTHAND_OBSERVATION,
                claim=cases[0].claim,
                why_notable="notable",
                uncertainty="uncertain",
            )
        ]
    ).model_dump_json()
    configured = ds_provider([payload])
    configured.resolve_research_cases(cases)
    body = json.loads(configured.client.chat.completions.last_request["messages"][1]["content"])
    assert "Community submission" in json.dumps(body)
    assert body["payload"][0]["lead"]["raw_item_id"] == lead.id


def test_p2a_05_hn_discovery_not_official_fetch() -> None:
    ref = evidence_snapshot(_hn_item(), association_basis="lead")
    assert ref.source_role is SourceRole.COMMUNITY_DISCOVERY
    assert ref.official_page_fetched is False


def test_p2a_07_research_budget_omits_with_budget_reason() -> None:
    items = [
        RawItem(
            id=f"item-p{idx}",
            title=f"Practitioner observation number {idx} with concrete workflow detail",
            url=f"https://example.com/p{idx}",
            source_name="Blog",
            source_type="rss",
            fetched_at=datetime(2026, 9, 1, tzinfo=UTC),
            summary="x" * 200,
            content_excerpt="y" * 400,
            source_role=SourceRole.PRACTITIONER,
            statement_type=StatementType.FIRSTHAND_OBSERVATION,
            metadata={"score": 10 - idx},
        )
        for idx in range(6)
    ]
    cases = build_research_cases(items, maximum_cases=8)
    fitted = fit_research_request(
        cases, topic_context=build_topic_context(None), maximum_characters=1800
    )
    assert fitted.omitted
    assert set(fitted.omitted.values()) == {"research_input_budget"}
    result = resolve_research(
        items,
        provider=FakeAIProvider(),
        maximum_cases=8,
        maximum_radar_signals=3,
        maximum_input_characters=1800,
    )
    assert result.stats["research_omitted_cases"] >= 1


def test_p2a_09_injected_topics_change_requests() -> None:
    items = [_hn_item()]
    a = FakeAIProvider(
        topic_context=build_topic_context(
            [
                TopicConfig(
                    id="a", name="A", priority="high", keywords=["alpha"], exclude_keywords=[]
                )
            ]
        )
    )
    b = FakeAIProvider(
        topic_context=build_topic_context(
            [
                TopicConfig(
                    id="b", name="B", priority="low", keywords=["beta"], exclude_keywords=["nope"]
                )
            ]
        )
    )
    a.classify_items(items)
    b.classify_items(items)
    assert a.last_payload["topic_context"]["topics"][0]["id"] == "a"
    assert b.last_payload["topic_context"]["topics"][0]["id"] == "b"


def test_p2a_10_score_payload_drops_placeholder_fields() -> None:
    story = Story(
        id="story-1",
        canonical_title="Scored story",
        category="ai_and_open_source",
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
        source_item_ids=["item-1"],
        source_urls=["https://example.com/a"],
        primary_source_url="https://example.com/a",
        facts=["a fact"],
        relevance_score=0.0,
        importance_score=0.0,
        novelty_score=0.0,
        credibility_score=0.0,
    )
    blob = dumps(score_story_payload(story, build_topic_context(None)))
    for field in ("relevance_score", "importance_score", "novelty_score", "credibility_score"):
        assert field not in blob
    assert EDITORIAL_GUIDANCE in blob
    assert story.relevance_score == 0.0


def test_p2a_11_hashes_are_stable_and_sensitive() -> None:
    assert prompt_hash_for("hello") == prompt_hash_for("hello")
    assert prompt_hash_for("hello") != prompt_hash_for("hello world")
    ctx_a = build_topic_context(
        [TopicConfig(id="a", name="A", priority="high", keywords=["x"], exclude_keywords=[])]
    )
    ctx_b = build_topic_context(
        [TopicConfig(id="a", name="A", priority="high", keywords=["y"], exclude_keywords=[])]
    )
    assert policy_hash(ctx_a) != policy_hash(ctx_b)


def test_p2a_12_classified_irrelevant_reason_persists(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    item = official_item(
        "dropme", published_at=DAY_N - timedelta(hours=2), title="Irrelevant gossip"
    )
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-drop"))
    provider = FakeAIProvider(
        classify_overrides={
            item.id: ClassifiedItem(
                item_id=item.id,
                relevant=False,
                relevance_reason="Not an AI product event.",
                important=False,
                importance_reason="No developer impact.",
                category="other_reading",
            )
        }
    )
    monkeypatch.setattr(
        "morning_radar.pipeline.DeepSeekProvider.from_environment", lambda **k: provider
    )
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    record = inspect_intake(project, input_id=item.id)["records"][0]
    assert record["reason_code"] == ReasonCode.CLASSIFIED_IRRELEVANT.value
    assert (
        record["decision_details"]["classification"]["relevance_reason"]
        == "Not an AI product event."
    )


def test_p2a_13_low_score_keeps_model_and_rule_reasons(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    item = official_item(
        "low", published_at=DAY_N - timedelta(hours=2), title="Minor maintenance note"
    )
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-low"))
    provider = FakeAIProvider()
    provider.score_story = lambda story: StoryScore(  # type: ignore[method-assign]
        relevance_score=0.2,
        importance_score=0.1,
        novelty_score=0.1,
        credibility_score=0.4,
        explanation="Maintenance-only changelog.",
    )
    monkeypatch.setattr(
        "morning_radar.pipeline.DeepSeekProvider.from_environment", lambda **k: provider
    )
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    record = inspect_intake(project, input_id=item.id)["records"][0]
    assert record["reason_code"] == ReasonCode.BELOW_RELEVANCE_THRESHOLD.value
    assert record["decision_details"]["score"]["model_explanation"] == "Maintenance-only changelog."


def test_p2a_14_missing_classification_is_not_model_exclusion() -> None:
    class GapProvider(FakeAIProvider):
        def classify_items(self, items):
            super().classify_items(items)
            return ClassificationBatch(items=[])

    outcomes: dict[str, str] = {}
    item = official_item(
        "gap", published_at=DAY_N - timedelta(hours=1), title="Official model release notes"
    )
    build_stories([item], provider=GapProvider(), now=DAY_N, item_outcomes=outcomes)
    assert outcomes[item.id] == ReasonCode.CLASSIFICATION_RESPONSE_MISSING.value


def test_p2a_15_versions_do_not_share_one_row(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    v1 = _version_item(
        "alpha",
        title="Alpha original",
        excerpt="old",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N - timedelta(hours=3),
    )
    v2 = _version_item(
        "alpha",
        title="Alpha revised",
        excerpt="new",
        published_at=DAY_N - timedelta(hours=3),
        fetched_at=DAY_N + timedelta(minutes=5),
    )
    other = official_item("other", published_at=DAY_N - timedelta(hours=2), title="Unrelated kept")
    _seed(project, save_checkpoint(project, [v1], now=DAY_N, batch_id="batch-v1"))
    _seed(
        project,
        save_checkpoint(
            project, [v1, v2, other], now=DAY_N + timedelta(minutes=5), batch_id="batch-v2"
        ),
        now=DAY_N + timedelta(minutes=5),
    )
    _install_tracking_provider(monkeypatch)
    MorningRadarPipeline(project).process(now=DAY_N + timedelta(minutes=5), notify=False)
    records = inspect_intake(project, input_id=v1.id)["records"]
    assert len({item["content_version"] for item in records}) >= 2


def test_p2a_16_heal_keeps_decision_details(tmp_path, monkeypatch) -> None:
    project = copy_project(tmp_path)
    item = official_item(
        "heal", published_at=DAY_N - timedelta(hours=2), title="Official launch that should persist"
    )
    _seed(project, save_checkpoint(project, [item], now=DAY_N, batch_id="batch-heal"))
    provider = _install_tracking_provider(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("status confirmation interrupted")

    monkeypatch.setattr("morning_radar.pipeline._mark_brief_generated", boom)
    with pytest.raises(RuntimeError):
        MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    assert provider.classified_titles
    monkeypatch.undo()
    recovered = _install_tracking_provider(monkeypatch)
    MorningRadarPipeline(project).process(now=DAY_N, notify=False)
    record = inspect_intake(project, input_id=item.id)["records"][0]
    assert record.get("decision_details")
    assert recovered.classified_titles == []


def test_p2a_17_unknown_url_is_not_coverage_gap(tmp_path) -> None:
    payload = inspect_intake(copy_project(tmp_path), url="https://example.com/never-collected")
    assert payload["found"] is False
    assert payload["coverage_gap"] is False


def test_p2a_19_qwen_shares_deepseek_path() -> None:
    assert issubclass(QwenProvider, DeepSeekProvider)
    configured = ds_provider([classification_json()])
    configured.classify_items([_hn_item()])
    body = json.loads(configured.client.chat.completions.last_request["messages"][1]["content"])
    assert "topic_context" in body


def test_p2a_06_same_company_materials_stay_distinct() -> None:
    lead = _hn_item().model_copy(
        update={"source_role": SourceRole.PRACTITIONER, "company_candidates": ["openai"]}
    )
    other = official_item(
        "other-event",
        published_at=DAY_N,
        title="Same company, different event",
    ).model_copy(
        update={
            "summary": "A pricing change, not the launch.",
            "content_excerpt": "This excerpt is about a pricing change, not the launch.",
            "company_candidates": ["openai"],
        }
    )
    cases = build_research_cases([lead, other], maximum_cases=8)
    excerpts = " ".join(item.content_excerpt for item in cases[0].supporting_evidence)
    assert any(
        item.association_basis == "official_primary_entity_overlap"
        for item in cases[0].supporting_evidence
    )
    assert "pricing change" in excerpts

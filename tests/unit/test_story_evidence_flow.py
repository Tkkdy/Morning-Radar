"""Synthetic contract regressions; these do not assert real model semantics."""
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from morning_radar.ai import AIBudget, AIOutputError, DeepSeekProvider, FakeAIProvider
from morning_radar.ai.models import BriefDraft, StoryScore
from morning_radar.ai.openai_provider import OpenAIProvider
from morning_radar.ai.request_payload import score_story_payload, story_materials
from morning_radar.briefing.generator import _story_context
from morning_radar.models import BriefItem, DailyBrief, RawItem, Story
from morning_radar.models.core import GroundedAnalysis, GroundedPoint, TechnicalReading
from morning_radar.processing.story_builder import build_story
from morning_radar.publishing.site_builder import SiteBuilder

NOW = datetime(2026, 10, 11, tzinfo=UTC)
TEXT = "The company said it started a review in July. The publication questions its controls."


def raw(**changes):
    return RawItem(id="input-a", title="来源报道的审查", url="https://example.org/report",
                   source_name="测试媒体", source_type="rss", fetched_at=NOW,
                   summary=TEXT).model_copy(update=changes)


def draft(item):
    point = GroundedPoint(text="公司称审查在七月开始；这是来源转述。",
                          source_url=item.url,
                          quote="The company said it started a review in July.")
    return FakeAIProvider().merge_story([item]).model_copy(update={
        "facts": ["公司称审查在七月开始。"],
        "opinions": ["测试媒体质疑控制能力。"],
        "grounded_analysis": [GroundedAnalysis(
            premises=[point], inference="审查开始时间不足以判断问题持续多久。")],
        "uncertainties": ["实际事件持续时间未知。"],
    })


class Completions:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def parse(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(output_parsed=kwargs["text_format"].model_validate_json(
            self.responses.pop(0)))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=self.responses.pop(0)), finish_reason="stop")],
            usage=None)


@pytest.mark.parametrize("provider_class", [DeepSeekProvider, OpenAIProvider])
def test_attribution_and_material_reach_actual_score_and_write_messages(provider_class):
    item = raw()
    merge = draft(item)
    score = StoryScore(relevance_score=.8, importance_score=.8, novelty_score=.6,
                       credibility_score=.6, explanation="基于测试输入的评分。")
    completions = Completions([merge.model_dump_json(), score.model_dump_json(),
                               BriefDraft(items=[]).model_dump_json()])
    options = {"base_url": "https://example.test"} if provider_class is DeepSeekProvider else {}
    provider = provider_class(model="test-model", api_key="test-key",
        **options, budget=AIBudget(3, 100_000, 20),
        prompt_dir=Path("prompts"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions),
                               responses=completions))
    story = build_story([item], provider=provider, now=NOW)
    provider.write_brief([story], [])
    def payload(request):
        return json.loads(request["messages"][1]["content"]
                          if "messages" in request else request["input"])
    score_payload = payload(completions.requests[1])["payload"]
    write_payload = payload(completions.requests[2])["stories"][0]
    for payload in (score_payload, write_payload):
        assert payload["opinions"] == merge.opinions
        assert payload["grounded_analysis"][0]["premises"][0]["quote"] in TEXT
        assert payload["source_materials"][0]["summary"] == TEXT
        assert payload["source_materials"][0]["raw_item_id"] == item.id
        assert "analysis" not in payload
    assert story.opinions[0] not in story.facts


def test_source_materials_are_bounded_deduplicated_and_omissions_visible():
    a = raw(summary="", content_excerpt="A source paragraph. " * 200)
    b = raw(id="input-b", summary=a.content_excerpt[:200], content_excerpt="")
    later = [raw(id=f"input-{i}", url=f"https://example.org/{i}",
                 summary="Independent source paragraph. " * 60) for i in range(9)]
    material, omitted = story_materials([a, b, *later])
    assert len(material) == 8
    assert sum(len(m.summary) + len(m.content_excerpt) for m in material) <= 6000
    assert material[0].excerpt_truncated
    assert material[1].summary == "" and material[1].text_omission_reason == "duplicate_same_url"
    assert omitted == [i.id for i in later[6:]]
    for ref in material:
        original = next(i for i in [a, b, *later] if i.id == ref.raw_item_id)
        assert ref.summary in original.summary and ref.content_excerpt in original.content_excerpt


@pytest.mark.parametrize("change", [
    {"source_url": "https://example.org/not-in-this-batch"},
    {"quote": "The company admitted losing control throughout the entire year."},
])
def test_bad_analysis_premise_rejected_before_scoring(change):
    item = raw()
    value = draft(item)
    value.grounded_analysis[0].premises[0] = value.grounded_analysis[0].premises[0].model_copy(
        update=change)
    provider = FakeAIProvider()
    provider.merge_story = lambda _: value
    provider.score_story = lambda _: pytest.fail("invalid premises must not reach scoring")
    with pytest.raises(AIOutputError, match="absent"):
        build_story([item], provider=provider, now=NOW)


def test_legacy_response_is_explicitly_degraded_and_old_stories_remain_readable():
    item = raw()
    value = draft(item).model_copy(update={
        "grounded_analysis": [], "analysis": ["无引文的旧分析。"]})
    before = value.model_dump()
    provider = FakeAIProvider()
    provider.merge_story = lambda _: value
    story = build_story([item], provider=provider, now=NOW)
    assert story.analysis == [] and any("未附" in s for s in story.uncertainties)
    assert value.model_dump() == before
    old = story.model_dump(mode="json")
    for field in ("grounded_analysis", "opinions", "source_materials", "source_materials_omitted",
                  "analysis_quote_normalizations"):
        old.pop(field)
    old["analysis"] = ["历史记录不改写。"]
    loaded = Story.model_validate(old)
    assert loaded.analysis == old["analysis"] and loaded.opinions == []
    payload = score_story_payload(loaded, None)["payload"]
    assert "analysis" not in payload and payload["legacy_analysis_omitted"] is True
    invalid = story.model_dump()
    invalid["source_materials"][0]["raw_item_id"] = "another-batch"
    with pytest.raises(ValidationError, match="belong"):
        Story.model_validate(invalid)


def test_page_separates_opinion_inference_and_missing_results(tmp_path):
    item = raw()
    value = draft(item)
    point = value.grounded_analysis[0].premises[0]
    value.technical_reading = TechnicalReading(kind="technical_report", problem=point,
                                               method=point, author_evidence=point)
    provider = FakeAIProvider()
    provider.merge_story = lambda _: value
    story = build_story([item], provider=provider, now=NOW)
    context = _story_context(story)
    card = BriefItem(id="card", section="top_stories", title=story.canonical_title,
                     what_happened="来源报告审查事件。", source_urls=story.source_urls,
                     story_ids=[story.id], story_contexts=[context])
    brief = DailyBrief(date=NOW.date(), timezone="UTC", generated_at=NOW, top_stories=[card])
    SiteBuilder(template_dir=Path("templates"), output_dir=tmp_path).build(
        [brief], stylesheet=Path("site/assets/style.css"))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    for phrase in ("材料中的事实陈述", "来源评价", "系统推断", "来源前提", "具体评测结果"):
        assert phrase in html
    assert "<h4>已验证事实</h4>" not in html
    assert context.opinions == story.opinions
    assert context.grounded_analysis == story.grounded_analysis
    legacy = card.model_dump()
    legacy["story_contexts"][0].pop("opinions")
    legacy["story_contexts"][0].pop("grounded_analysis")
    assert BriefItem.model_validate(legacy).story_contexts[0].opinions == []

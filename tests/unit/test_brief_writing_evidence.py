"""Local structural evidence contracts, not model semantic acceptance."""
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from morning_radar.ai import AIBudget, AIOutputError, DeepSeekProvider, FakeAIProvider
from morning_radar.ai.models import BriefDraft, BriefItemRecoveryDraft, GeneratedBriefItem
from morning_radar.ai.openai_provider import OpenAIProvider
from morning_radar.ai.output_validation import sanitize_brief_explanation
from morning_radar.briefing.generator import BriefLimits, generate_daily_brief
from morning_radar.models import BriefItem, Story
from morning_radar.models.core import (
    BriefExplanation,
    GroundedAnalysis,
    GroundedPoint,
    ResearchEvidenceRef,
    SourceRole,
)

NOW = datetime(2026, 10, 11, tzinfo=UTC)
QUOTE = "The company said the review began in July."
URL = "https://example.org/report"


def source_story():
    return Story(id="story-a", canonical_title="公司说明内部审查", category="ai_and_open_source",
        updated_at=NOW, source_item_ids=["raw-a"], source_urls=[URL], primary_source_url=URL,
        facts=["公司称内部审查在七月开始。"], opinions=["媒体认为这项内部审查值得关注。"],
        analysis=["没有前提的旧分析"], grounded_analysis=[GroundedAnalysis(
            premises=[GroundedPoint(text="公司说明审查开始时间。", source_url=URL, quote=QUOTE)],
            inference="仅据开始时间仍不能判断问题持续多久。")],
        source_materials=[ResearchEvidenceRef(raw_item_id="raw-a", url=URL,
            source_role=SourceRole.EDITORIAL, summary=QUOTE)],
        relevance_score=.8, importance_score=.8, novelty_score=.6, credibility_score=.6)


def generated(evidence=None, **changes):
    return GeneratedBriefItem(story_ids=["story-a"], section="top_stories", title="内部审查",
        what_happened="公司称内部审查在七月开始。", source_urls=[URL],
        why_it_matters_evidence=evidence, **changes)


def explanation(kind="fact", field="facts", **changes):
    return BriefExplanation.model_validate(dict(kind=kind, text="公司提供了内部审查的起点。",
        references=[dict(story_id="story-a", field=field, index=0)], **changes))


def render_with(provider, stories=None):
    return generate_daily_brief(brief_date=NOW.date(), generated_at=NOW, timezone="UTC",
        stories=stories or [source_story()], signals=[], provider=provider,
        limits=BriefLimits(maximum_items=8), enabled_sections={}, run_stats={})


@pytest.mark.parametrize(("kind", "field"), [
    ("fact", "facts"), ("source_opinion", "opinions"), ("inference", "grounded_analysis"),
])
def test_valid_statement_types_survive_and_have_matching_basis(kind, field, tmp_path):
    item = generated(explanation(kind, field))
    clean = sanitize_brief_explanation(item, [source_story()])
    assert clean.why_it_matters == item.why_it_matters_evidence.text
    assert clean.why_it_matters_evidence.references[0].field == field
    assert clean._why_omission_reason is None
    assert item.why_it_matters is None  # Original response object not rewritten.
    class Provider(FakeAIProvider):
        def write_brief(self, stories, signals):
            return BriefDraft(items=[item])
    brief = render_with(Provider())
    from morning_radar.publishing.site_builder import SiteBuilder
    SiteBuilder(template_dir=Path("templates"), output_dir=tmp_path).build(
        [brief], stylesheet=Path("site/assets/style.css"))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert clean.why_it_matters in html and "这段说明的依据" in html
    basis = getattr(source_story(), field)[0]
    assert (basis.inference if field == "grounded_analysis" else basis) in html


def test_new_inference_uses_only_received_material_and_restores_quote():
    story = source_story().model_copy(update={"grounded_analysis": []})
    evidence = BriefExplanation(kind="inference", text="仅据起点还不能判断持续时间。",
        premises=[dict(story_id=story.id, text="公司称审查在七月开始。", source_url=URL,
                       quote=QUOTE.replace(" ", "  "))])
    clean = sanitize_brief_explanation(generated(evidence), [story])
    assert clean.why_it_matters == evidence.text
    assert clean.why_it_matters_evidence.premises[0].quote == QUOTE
    assert len(clean._why_quote_normalizations) == 1
    repeated = sanitize_brief_explanation(clean, [story])
    assert repeated._why_quote_normalizations == clean._why_quote_normalizations
    # Validated premises were also actually sent, even if source packing omitted the excerpt.
    clean = sanitize_brief_explanation(generated(evidence), [source_story().model_copy(
        update={"source_materials": []})])
    assert clean.why_it_matters == evidence.text
    missing = sanitize_brief_explanation(generated(evidence), [story.model_copy(
        update={"source_materials": []})])
    assert missing.why_it_matters is None
    assert missing._why_omission_reason


@pytest.mark.parametrize("defect", ["wrong_story", "missing_index", "wrong_kind", "missing_basis",
                                    "wrong_source", "absent_quote", "mismatched_text"])
def test_invalid_optional_basis_is_omitted_without_accepting_free_text(defect):
    data = explanation().model_dump()
    extra = {}
    if defect == "wrong_story":
        data["references"][0]["story_id"] = "story-b"
    elif defect == "missing_index":
        data["references"][0]["index"] = 7
    elif defect == "wrong_kind":
        data["references"][0]["field"] = "grounded_analysis"
    elif defect == "missing_basis":
        data["references"] = []
    elif defect in {"wrong_source", "absent_quote"}:
        data["references"] = []
        data["premises"] = [dict(story_id="story-a", text="公司称审查开始。",
            source_url="https://elsewhere.org/report" if defect == "wrong_source" else URL,
            quote=("A quote not present in the writing input."
                   if defect == "absent_quote" else QUOTE))]
    else:
        extra["why_it_matters"] = "与带依据说明不同的自由分析"
    item = generated(BriefExplanation.model_validate(data), **extra)
    # Even another available Story is not evidence for a card that did not cite it.
    other = source_story().model_copy(update={"id": "story-b"})
    clean = sanitize_brief_explanation(item, [source_story(), other])
    assert clean.why_it_matters is None and clean.why_it_matters_evidence is None
    assert clean._why_omission_reason
    assert clean.what_happened == item.what_happened


@pytest.mark.parametrize("recovery", [False, True])
def test_generator_applies_contract_to_batch_and_recovery_without_extra_calls(recovery):
    class Provider(FakeAIProvider):
        calls = 0
        def write_brief(self, stories, signals):
            self.calls += 1
            if recovery:
                raise AIOutputError("forced batch failure")
            return BriefDraft(items=[generated(why_it_matters="没有依据的自由分析")])
        def recover_brief_item(self, story, signals, editorial_decision=None):
            self.calls += 1
            return BriefItemRecoveryDraft(item=generated(why_it_matters="没有依据的自由分析"))
    provider = Provider()
    item = render_with(provider).top_stories[0]
    assert item.why_it_matters is None
    assert item.why_it_matters_omission_reason == "unstructured_explanation"
    assert provider.calls == (2 if recovery else 1)


def test_batch_cannot_borrow_evidence_from_another_batch():
    stories = [source_story().model_copy(update={"id": f"story-{i}"}) for i in range(5)]
    class Provider(FakeAIProvider):
        def write_brief(self, batch, signals):
            reference = dict(story_id="story-4", field="facts", index=0)
            item = generated(BriefExplanation(kind="fact", text="跨批次说明",
                                              references=[reference]))
            item.story_ids = [batch[0].id]
            return BriefDraft(items=[item])
    result = render_with(Provider(), stories)
    assert result.top_stories[0].why_it_matters is None
    assert result.top_stories[0].why_it_matters_omission_reason == "explanation_story_outside_input"


def test_empty_and_historical_brief_remain_readable_without_legacy_generation_bypass():
    empty = sanitize_brief_explanation(generated(), [source_story()])
    assert empty.why_it_matters is None and empty._why_omission_reason is None
    old = BriefItem(id="old", section="top_stories", title="历史条目", what_happened="历史正文",
                    why_it_matters="历史分析", story_ids=["story-a"], source_urls=[URL])
    assert BriefItem.model_validate_json(old.model_dump_json()).why_it_matters == "历史分析"
    class Failing(FakeAIProvider):
        def write_brief(self, *args):
            raise AIOutputError("failure")
        def recover_brief_item(self, *args):
            raise AIOutputError("failure")
    assert render_with(Failing()).top_stories[0].why_it_matters is None


class Wire:
    def __init__(self, output):
        self.output = output
        self.requests = []
    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(content=self.output.model_dump_json()))], usage=None)
    def parse(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(output_parsed=self.output)


@pytest.mark.parametrize("provider_class", [DeepSeekProvider, OpenAIProvider])
@pytest.mark.parametrize("recovery", [False, True])
def test_actual_provider_wire_drops_bad_optional_url_without_retry(provider_class, recovery):
    evidence = BriefExplanation(kind="inference", text="这是待验证的有限推断。", premises=[dict(
        story_id="story-a", text="材料说明审查起点。", source_url="https://invented.org/",
        quote=QUOTE)])
    item = generated(evidence)
    response = BriefItemRecoveryDraft(item=item) if recovery else BriefDraft(items=[item])
    wire = Wire(response)
    options = {"base_url": "https://example.test"} if provider_class is DeepSeekProvider else {}
    provider = provider_class(model="test", api_key="offline", budget=AIBudget(2, 100000, 10),
        prompt_dir=Path("prompts"), client=SimpleNamespace(chat=SimpleNamespace(completions=wire),
                                                          responses=wire), **options)
    result = (provider.recover_brief_item(source_story(), []) if recovery
              else provider.write_brief([source_story()], []))
    actual = result.item if recovery else result.items[0]
    assert actual.why_it_matters is None
    assert actual._why_omission_reason == "explanation_source_outside_story"
    assert len(wire.requests) == 1
    request = wire.requests[0]
    prompt = request.get("instructions") or request["messages"][0]["content"]
    if "text_format" in request:
        schema = request["text_format"].model_json_schema()
        assert "why_it_matters_evidence" in schema["$defs"]["GeneratedBriefItem"]["properties"]
    else:
        assert request["response_format"] == {"type": "json_object"}
        assert '"why_it_matters_evidence"' in prompt
        assert '"BriefExplanation"' in prompt
    assert "重要性说明的依据契约" in prompt
    assert "主体范围、时间范围、比较基线和可用性" in prompt


def test_score_boundary_rule_is_in_actual_request():
    from morning_radar.ai.models import StoryScore
    wire = Wire(StoryScore(relevance_score=.5, importance_score=.5, novelty_score=.5,
                          credibility_score=.5, explanation="仅作为编辑判断。"))
    provider = DeepSeekProvider(model="test", api_key="offline", base_url="https://example.test",
        budget=AIBudget(1, 100000, 10), prompt_dir=Path("prompts"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=wire)))
    provider.score_story(source_story())
    prompt = wire.requests[0]["messages"][0]["content"]
    assert "不把上位描述自行收窄成某个具体训练方法" in prompt
    assert "比较理由区分编辑判断和具体基线事实" in prompt


def test_malformed_optional_quote_is_dropped_at_schema_boundary():
    data = generated().model_dump()
    data["why_it_matters_evidence"] = dict(kind="inference", text="有限推断", premises=[dict(
        story_id="story-a", source_url=URL, text="来源前提", quote="too short")])
    parsed = BriefDraft.model_validate({"items": [data]})
    assert parsed.items[0].why_it_matters_evidence is None
    assert parsed.items[0]._why_omission_reason == "malformed_explanation_evidence"
    clean = sanitize_brief_explanation(parsed.items[0], [source_story()])
    assert clean._why_omission_reason == "malformed_explanation_evidence"

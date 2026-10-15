"""Fixed decisions test wiring, not model judgement or factual truth."""
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader
from test_editorial_evaluator import (
    Placement,
    ReversedBriefProvider,
    evaluate,
    generate,
    story,
)

from morning_radar.ai import AIBudgetExceeded, AIOutputError
from morning_radar.briefing import BriefLimits
from morning_radar.briefing.generator import (
    SECTION_NAMES,
    core_brief_request_payloads,
    reuse_archived_cards,
    select_brief_stories,
)
from morning_radar.editorial.models import EditorialDecision
from morning_radar.models import BriefItem


def dec(identity, placement, target=None):
    return EditorialDecision(
        story_id=identity, placement=placement, reader_value=2, evidence_value=2,
        fact_status="claim", retain_for_trends=False, reason="offline fixed decision",
        support_for_story_id=target,
    )


def cards(brief):
    return [item for section in (*SECTION_NAMES, "other_reading")
            for item in getattr(brief, section)]


def render(card):
    env = Environment(loader=FileSystemLoader("templates"), autoescape=True)
    env.filters["hostname"] = lambda value: value
    return env.get_template("brief_card.html.j2").render(
        item=card, compact=True, historical_annotations={},
    )


def test_active_short_capacity_and_title_only_hold():
    items = [story(str(i)) for i in range(13)]
    items[0] = items[0].model_copy(update={"facts": []})
    provider = ReversedBriefProvider([dec(item.id, "ONE-LINER") for item in items])
    brief = generate(items, provider, evaluate(items, provider))
    assert len(brief.other_reading) == 12
    assert all(card.story_ids != ["0"] for card in cards(brief))
    assert brief.run_stats["editorial_material_held"] == 1


def test_cross_batch_support_context_and_payload_count():
    items = [story(str(i)) for i in range(6)]
    decisions = [dec(str(i), "NEWS") for i in range(5)] + [dec("5", "SUPPORT", "0")]
    class Recording(ReversedBriefProvider):
        batches = None

        def write_brief(self, stories, signals, editorial_decisions=None):
            self.batches.append([item.id for item in stories])
            assert [d.story_id for d in editorial_decisions] == self.batches[-1]
            return super().write_brief(stories, signals, editorial_decisions)

    provider = Recording(decisions)
    provider.batches = []
    result = evaluate(items, provider)
    _, writing = select_brief_stories(
        items, limits=BriefLimits(12), editorial_result=result,
    )
    payloads, recovery = core_brief_request_payloads(writing, [], decisions)
    brief = generate(items, provider, result)
    assert len(payloads) == len(provider.batches) == 2
    assert len(recovery) == 6
    assert provider.batches == [["0", "1", "2", "3"], ["4", "5"]]
    assert len(cards(brief)) == 5
    owner = brief.other_reading[0]
    assert owner.story_ids == ["0", "5"]
    assert [c.story_id for c in owner.story_contexts] == ["0", "5"]
    assert items[5].source_urls[0] in owner.source_urls


def test_placement_roundtrip_legacy_and_visible_qualifier():
    items = [story("a").model_copy(update={"uncertainties": ["仅公开预览，尚未正式可用。"]})]
    provider = ReversedBriefProvider([dec("a", "NEWS")])
    brief = generate(items, provider, evaluate(items, provider))
    card = brief.other_reading[0]
    restored = BriefItem.model_validate_json(card.model_dump_json())
    assert restored.editorial_placement == Placement.NEWS
    old = card.model_dump()
    old.pop("editorial_placement")
    assert BriefItem.model_validate(old).editorial_placement is None
    markup = render(card)
    assert "简讯" in markup
    assert '<p class="uncertainty"><strong>不确定性：</strong>仅公开预览' in markup
    # No length truncation: the qualifier is complete.
    assert "尚未正式可用。" in markup
    one = card.model_copy(update={"editorial_placement": Placement.ONE_LINER,
                                 "why_it_matters": "保留在详情的说明"})
    assert '<details class="short-context">' in render(one)
    assert '<details class="short-context">' not in render(
        one.model_copy(update={"editorial_placement": Placement.NEWS})
    )


def test_archive_cannot_restore_old_long_card_or_changed_placement(tmp_path):
    items = [story("a")]
    provider = ReversedBriefProvider([dec("a", "STORY")])
    brief = generate(items, provider, evaluate(items, provider))
    path = tmp_path / "archive.json"
    path.write_text(brief.model_dump_json(), encoding="utf-8")
    reused = reuse_archived_cards(items, [path], editorial_decisions={"a": dec("a", "STORY")})
    assert len(reused) == 1
    assert not reuse_archived_cards(items, [path], editorial_decisions={"a": dec("a", "ONE-LINER")})
    brief.run_stats.pop("editorial_contract_version")
    path.write_text(brief.model_dump_json(), encoding="utf-8")
    assert not reuse_archived_cards(items, [path], editorial_decisions={"a": dec("a", "STORY")})


def test_failed_writing_keeps_drop_hidden_and_qualifiers():
    class Failed(ReversedBriefProvider):
        def write_brief(self, *args, **kwargs):
            raise AIBudgetExceeded("offline budget fixture")

        def recover_brief_item(self, *args, **kwargs):
            raise AIBudgetExceeded("offline budget fixture")

    items = [story("a").model_copy(update={"uncertainties": ["计划明年部署。", "厂商声称。"]}),
             story("drop").model_copy(update={"importance_score": 1})]
    provider = Failed([dec("a", "ONE-LINER"), dec("drop", "DROP")])
    brief = generate(items, provider, evaluate(items, provider))
    assert [c.story_ids for c in cards(brief)] == [["a"]]
    assert brief.other_reading[0].editorial_placement == Placement.ONE_LINER
    assert "计划明年部署。" in render(brief.other_reading[0])
    assert "厂商声称。" in render(brief.other_reading[0])
    assert brief.run_stats["ai_brief_recovery_budget_exhausted"]


def test_whole_editorial_failure_is_auditable():
    items = [story("a").model_copy(update={"relevance_score": 1})]
    provider = ReversedBriefProvider([])
    result = evaluate(items, provider, maximum=0)
    brief = generate(items, provider, result)
    assert brief.run_stats["editorial_degraded"]
    assert brief.run_stats["editorial_degradation_reason"] == "story_limit_exceeded"
    template = Path("templates/index.html.j2").read_text(encoding="utf-8")
    assert 'brief.run_stats.get("editorial_degraded")' in template


@pytest.mark.parametrize("error", [AIOutputError, AIBudgetExceeded])
def test_provider_and_budget_editorial_failure_are_visible(error):
    class Failed(ReversedBriefProvider):
        def evaluate_editorial(self, stories):
            raise error("offline failure fixture")

    items = [story("a").model_copy(update={"relevance_score": 1})]
    provider = Failed([])
    brief = generate(items, provider, evaluate(items, provider))
    assert brief.run_stats["editorial_degraded"]
    assert brief.run_stats["editorial_degradation_reason"] == "provider_or_validation_failure"
    assert cards(brief)[0].editorial_placement is None


def test_fallback_that_cannot_fit_schema_is_held_not_truncated():
    class Failed(ReversedBriefProvider):
        def write_brief(self, *args, **kwargs):
            raise AIBudgetExceeded("offline fixture")

        def recover_brief_item(self, *args, **kwargs):
            raise AIBudgetExceeded("offline fixture")

    original = "很长但不能安全截断的来源陈述" * 60
    items = [story("long").model_copy(update={"facts": [original]})]
    provider = Failed([dec("long", "ONE-LINER")])
    brief = generate(items, provider, evaluate(items, provider))
    assert not cards(brief)
    assert brief.run_stats["editorial_unsafe_fallback_held"] == 1
    assert items[0].facts == [original]

"""Reproduce the twelve-unit manual comparison through real E2 assembly.

Fixed Editorial decisions AND writing are fixture data, never model output.
Run from the checkout with src on sys.path; output must be a new .tmp directory.
"""
import argparse
import hashlib
import json
from pathlib import Path

from morning_radar.ai import FakeAIProvider
from morning_radar.ai.models import BriefDraft, GeneratedBriefItem
from morning_radar.briefing import BriefLimits, generate_daily_brief
from morning_radar.briefing.generator import SECTION_NAMES
from morning_radar.editorial.evaluator import evaluate_editorial
from morning_radar.editorial.models import EditorialDecision, EditorialDecisionBatch
from morning_radar.models import DailyBrief, Story
from morning_radar.publishing.site_builder import SiteBuilder

FIXTURE = Path("tests/fixtures/editorial_alignment/frozen-reading.json")


def build_frozen_preview(output: Path):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    baseline = DailyBrief.model_validate(data["baseline"])
    old = {card.id: card for section in (*SECTION_NAMES, "other_reading")
           for card in getattr(baseline, section)}
    stories, decisions, writing = [], [], {}
    for design in data["design"]:
        original = old[design["id"]]
        context = original.story_contexts[0]
        held = design["level"] == "hold"
        source_ids = [ref.raw_item_id for ref in context.source_refs]
        source_urls = list(dict.fromkeys([
            *original.source_urls,
            *(url for ref in context.source_refs for url in (ref.url, ref.discussion_url) if url),
        ]))
        values = {key: value for key, value in context.model_dump().items()
                  if key in Story.model_fields}
        story = Story(
            **{**values, "id": context.story_id,
               "canonical_title": design["title"] or original.title,
               "updated_at": baseline.generated_at,
               "source_item_ids": source_ids, "source_urls": source_urls,
               # Manual attributed statements supplied to test the wiring only.
               "facts": [] if held else [design["body"]],
               "opinions": [design["detail"]], "uncertainties": [],
               "importance_score": 0.1, "relevance_score": 0.1,
               "novelty_score": 0.1, "credibility_score": 0.1},
        )
        stories.append(story)
        decisions.append(EditorialDecision(
            story_id=story.id, placement=design["placement"], reader_value=2,
            evidence_value=2, fact_status="claim", retain_for_trends=False,
            reason="E0/E1 manual fixture decision; not an automatic judgement",
        ))
        if not held:
            writing[story.id] = GeneratedBriefItem(
                story_ids=[story.id], section=story.category, title=design["title"],
                what_happened=design["body"], source_urls=story.source_urls,
            )

    class FixedProvider(FakeAIProvider):
        def evaluate_editorial(self, stories):
            return EditorialDecisionBatch(decisions=decisions)

        def write_brief(self, stories, signals, editorial_decisions=None):
            assert [d.story_id for d in editorial_decisions] == [s.id for s in stories]
            return BriefDraft(items=[writing[s.id] for s in stories])

    provider = FixedProvider()
    result = evaluate_editorial(
        stories, provider=provider, current_date=baseline.date,
        generated_at=baseline.generated_at, enabled=True, shadow_mode=False,
        profile_version="e2-offline-fixed", maximum_stories=20,
    )
    brief = generate_daily_brief(
        brief_date=baseline.date, generated_at=baseline.generated_at,
        timezone=baseline.timezone, stories=stories, signals=[], provider=provider,
        limits=BriefLimits(12), enabled_sections={}, relevance_threshold=0.55,
        importance_threshold=0.6, editorial_result=result,
        run_stats={"fixture_mode": True, "manual_fixed_decisions_and_writing": True},
    )
    output = output.resolve()
    allowed = (Path.cwd() / ".tmp").resolve()
    if allowed not in output.parents:
        raise ValueError("Preview output must be inside this checkout's .tmp")
    output.mkdir(parents=True, exist_ok=False)
    (output / "candidate.json").write_text(brief.model_dump_json(indent=2), encoding="utf-8")
    (output / "editorial.json").write_text(result.daily.model_dump_json(indent=2), encoding="utf-8")
    SiteBuilder(template_dir=Path("templates"), output_dir=output / "site").build(
        [brief], stylesheet=Path("site/assets/style.css"),
        preview_notice="离线接线预览：冻结材料＋人工固定编辑决策＋Mock 写作。"
                       "3 条核心、5 条简讯、2 条待核线索；另 2 条标题级材料留待核查。"
                       "不是新模型自动生成；未完成真实模型或负责人阅读验收。",
    )
    visible = [card for section in (*SECTION_NAMES, "other_reading")
               for card in getattr(brief, section)]
    manifest = {
        **data["provenance"], "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "core": 3, "brief": 5, "lead": 2, "held": 2, "displayed": len(visible),
        "new_title_body_characters": sum(len(c.title) + len(c.what_happened) for c in visible),
        "old_e01_comparable_main_characters": 3509,
        "measurement": "Manual title/body comparison; excludes labels, links, expanded details",
        "real_model_calls": 0, "collection_requests": 0, "paid_cost_cny": 0,
        "human_reading_acceptance": "pending", "automatic_semantics": "NOT_TESTED",
        "held_ids": [d["id"] for d in data["design"] if d["level"] == "hold"],
        "candidate_sha256": hashlib.sha256((output / "candidate.json").read_bytes()).hexdigest(),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return brief, manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _, manifest = build_frozen_preview(args.output)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()

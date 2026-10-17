"""Persisted mixed Stories at the upstream boundary; writing outputs are synthetic."""
import json
import shutil
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from morning_radar import pipeline
from morning_radar.ai import AIBudget, DeepSeekProvider, FakeAIProvider
from morning_radar.ai.models import BriefDraft, BriefItemRecoveryDraft, GeneratedBriefItem
from morning_radar.briefing.generator import core_brief_request_payloads
from morning_radar.editorial.evaluator import EditorialRunResult, ReaderSelection
from morning_radar.editorial.models import DailyEditorialDecisions, EditorialDecision
from morning_radar.models import Story

FIXTURE = Path("tests/fixtures/real_acceptance/writing-mixed-stories.json")
NOW = datetime.fromisoformat("2026-10-10T16:13:24.454289+00:00")
BODY_IDS = [
    "story-ea9e7b9b18ba309e9ece", "story-5847c0a6f61fc15bfc7c",
    "story-70c02287f82dcd518b4b", "story-63fef70f17b9424d122c",
]
LEAD_IDS = ["story-0fcec57e98055de9950d", "story-d05bfc53b41bb8d5e555"]


def mixed_stories():
    return [Story.model_validate(row) for row in json.loads(FIXTURE.read_text(encoding="utf-8"))]


def run_mixed(project, *, active=False, fail_first=False, packing=None):
    """Run formal pipeline selection/reservation/generation on unfiltered saved Stories.

    Collection/building are upstream stubs, not a replay of model quality. The
    optional hook is the existing isolated request packing used by acceptance.
    """
    for folder in ("config", "fixtures", "prompts", "templates"):
        shutil.copytree(folder, project / folder)
    (project / "site/assets").mkdir(parents=True)
    shutil.copyfile("site/assets/style.css", project / "site/assets/style.css")
    stories = mixed_stories()
    before = [story.model_dump(mode="json") for story in stories]
    requests, reserved = [], {}

    def create(**request):
        payload = json.loads(request["messages"][-1]["content"])
        recovery = "story" in payload
        rows = [payload["story"]] if recovery else payload["stories"]
        requests.append(dict(task="recovery" if recovery else "batch", request=request))
        items = [GeneratedBriefItem(
            story_ids=[row["id"]], section="ai_and_open_source",
            title="离线接线合成条目", what_happened=row["facts"][0],
            source_urls=row["source_urls"],
        ) for row in rows]
        if active and not recovery:
            # Synthetic SUPPORT attaches to its primary when they share a batch.
            decisions = {d["story_id"]: d for d in payload["editorial_decisions"]}
            for item in list(items):
                decision = decisions[item.story_ids[0]]
                if decision["placement"] == "SUPPORT":
                    primary = next((i for i in items if i.story_ids[0] ==
                                    decision["support_for_story_id"]), None)
                    if primary:
                        primary.story_ids += item.story_ids
                        primary.source_urls += item.source_urls
                        items.remove(item)
        draft = BriefItemRecoveryDraft(item=items[0]) if recovery else BriefDraft(items=items)
        content = "{" if fail_first and len(requests) == 1 else draft.model_dump_json()
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                               message=SimpleNamespace(content=content))], usage=None)

    writer = DeepSeekProvider(model="deepseek-v4-flash", api_key="offline",
        base_url="https://example.test", budget=AIBudget(32, 832000, 40, 32),
        prompt_dir=Path("prompts"), client=SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create))))
    parse = writer._parse

    def once(**kwargs):
        kwargs["maximum_structured_attempts"] = 1
        return parse(**kwargs)

    writer._parse = once

    class Provider(FakeAIProvider):
        budget = writer.budget
        model = writer.model
        prompt_dir = writer.prompt_dir

        def write_brief(self, stories, signals, editorial_decisions=None):
            writer.topic_context = self.topic_context
            return writer.write_brief(stories, signals, editorial_decisions)

        def recover_brief_item(self, story, signals, editorial_decision=None):
            writer.topic_context = self.topic_context
            return writer.recover_brief_item(story, signals, editorial_decision)

    provider = Provider()
    provider.model = writer.model
    app = pipeline.MorningRadarPipeline(project, provider=provider)
    app.app = app.app.model_copy(update={"maximum_brief_items": 5})
    if active:
        primary = [BODY_IDS[0], LEAD_IDS[0], BODY_IDS[1], LEAD_IDS[1], BODY_IDS[2]]
        editorial = EditorialRunResult(
            daily=DailyEditorialDecisions(
                date=NOW.date(), generated_at=NOW, profile_version="offline",
                enabled=True, shadow_mode=False, decisions=[EditorialDecision(
                    story_id=sid, placement="SUPPORT" if sid == BODY_IDS[3] else "STORY",
                    support_for_story_id=BODY_IDS[0] if sid == BODY_IDS[3] else None,
                    reader_value=3, evidence_value=2, fact_status="claim",
                    retain_for_trends=False, reason="离线接线编辑选择",
                ) for sid in [*primary, BODY_IDS[3]]]),
            selection=ReaderSelection(main_story_ids=primary,
                support_by_story_id={BODY_IDS[0]: [BODY_IDS[3]]}),
        )
    else:
        editorial = EditorialRunResult(daily=DailyEditorialDecisions(
            date=NOW.date(), generated_at=NOW, profile_version="offline",
            enabled=False, shadow_mode=True))

    context = packing(provider) if packing else nullcontext()
    with context:
        reserve_impl = pipeline._reserve_brief_core_budget

        def reserve(current, selected, signals, decisions=None):
            reserved["story_ids"] = [s.id for s in selected]
            reserve_impl(current, selected, signals, decisions)
            reserved["payloads"], reserved["recoveries"] = core_brief_request_payloads(
                selected, signals, decisions)
            reserved["calls"] = current.budget.reserved_core_calls
            reserved["characters"] = current.budget.reserved_core_input_characters

        with patch("socket.socket.connect", side_effect=AssertionError("NO NETWORK")), \
             patch("socket.create_connection", side_effect=AssertionError("NO NETWORK")), \
             patch.object(pipeline, "build_stories", return_value=stories), \
             patch.object(pipeline, "evaluate_editorial", return_value=editorial), \
             patch.object(pipeline, "_reserve_brief_core_budget", reserve):
            brief = app.run(fixtures=True, dry_run=True, now=NOW, notify=False)
    assert before == [story.model_dump(mode="json") for story in stories]
    return brief, reserved, requests


@pytest.mark.parametrize("active", [False, True])
def test_pipeline_reserves_the_same_mixed_story_inputs_it_writes(tmp_path, active):
    brief, reserved, requests = run_mixed(tmp_path / "project", active=active)
    assert reserved["story_ids"] == BODY_IDS
    batches = [r for r in requests if r["task"] == "batch"]
    assert [json.loads(r["request"]["messages"][-1]["content"]) for r in batches] == (
        reserved["payloads"])
    assert all(r["task"] == "batch" for r in requests)
    assert brief.run_stats["ai_brief_recovery_attempts"] == 0
    expected_leads = set(LEAD_IDS if active else LEAD_IDS[:1])
    assert {sid for item in brief.other_reading for sid in item.story_ids} == expected_leads
    main = [*brief.top_stories, *brief.ai_and_open_source]
    assert {sid for item in main for sid in item.story_ids} == set(BODY_IDS)
    assert all(sid not in LEAD_IDS for item in main for sid in item.story_ids)


def test_mixed_input_failure_recovers_only_failed_batch(tmp_path):
    with patch("morning_radar.briefing.generator.BRIEF_BATCH_ITEM_LIMIT", 2):
        brief, reserved, requests = run_mixed(tmp_path / "project", fail_first=True)
    assert [r["task"] for r in requests] == ["batch", "batch", "recovery", "recovery"]
    recovery_ids = [json.loads(r["request"]["messages"][-1]["content"])["story"]["id"]
                    for r in requests if r["task"] == "recovery"]
    assert recovery_ids == BODY_IDS[:2]
    assert reserved["story_ids"] == BODY_IDS
    assert {sid for item in [*brief.top_stories, *brief.ai_and_open_source]
            for sid in item.story_ids} == set(BODY_IDS)

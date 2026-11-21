"""Offline B1 preview: historical inputs, saved outputs and labeled demo content."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from morning_radar.ai import FakeAIProvider
from morning_radar.briefing.generator import _story_context
from morning_radar.collectors.http import HttpClient
from morning_radar.models import BriefItem, DailyBrief, RawItem
from morning_radar.models.core import BriefContinuityContext, GroundedPoint, TechnicalReading
from morning_radar.processing.story_builder import build_stories
from morning_radar.publishing.site_builder import SiteBuilder
from morning_radar.research.materials import enrich_materials, save_event_evidence
from morning_radar.settings import MaterialEnrichmentConfig


def build_b1_preview(root: Path, output: Path) -> Path:
    root, output = root.resolve(), output.resolve()
    # Preview can create one NEW directory only, never reuse a production output tree.
    for protected in [root / "data", root / "site"]:
        if output == protected or protected in output.parents or output in protected.parents:
            raise ValueError("Preview output must be isolated from production data/site")
    output.mkdir(parents=True, exist_ok=False)
    now = datetime(2026, 10, 10, 8, tzinfo=UTC)
    demo_html = (root / "tests/fixtures/b1/technical-demo.html").read_text(encoding="utf-8")
    paper = RawItem(
        id="demo-paper",
        title="演示技术报告：分块缓存",
        url="https://example.org/paper",
        source_name="演示作者",
        source_type="rss",
        fetched_at=now,
        published_at=now,
        summary="演示材料，缺少方法和限制。",
    )
    release = paper.model_copy(
        update={
            "id": "demo-release",
            "title": "演示发布：分块缓存工具",
            "url": "https://example.org/release",
        }
    )
    supplemented = enrich_materials(
        [paper, release],
        root=output,
        now=now,
        policy=MaterialEnrichmentConfig(enabled=True, allowed_hosts=["example.org"]),
        important_ids={paper.id, release.id},
        network_allowed=True,
        http=HttpClient(
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        200, text=demo_html, headers={"Content-Type": "text/html"}
                    )
                )
            )
        ),
    )

    def point(text, quote):
        return GroundedPoint(text=text, quote=quote, source_url=paper.url)

    reading = TechnicalReading(
        kind="technical_report",
        problem=point(
            "减少长文档处理中重复读取带来的延迟。",
            "本演示研究讨论长文档处理中重复读取导致的延迟问题",
        ),
        method=point(
            "将重复片段分块缓存，只处理新片段。", "作者将重复片段分块缓存，并只对新片段执行处理"
        ),
        engineering_value=point(
            "适合先测量输入复用率，再决定是否引入缓存。",
            "输入中存在可重复的片段，缓存容量能够容纳热数据",
        ),
        author_evidence=point(
            "作者在演示数据和单机条件下观察到命中时读取次数减少；不是独立验证。",
            "在演示数据集和单机环境中，缓存命中时读取次数减少",
        ),
        conditions=point(
            "需要输入重复且热数据可驻留缓存。", "输入中存在可重复的片段，缓存容量能够容纳热数据"
        ),
        limitations=point(
            "缓存失效和一致性开销仍待测量。", "缓存失效和更新一致性的开销仍需要进一步测量"
        ),
    )

    class DemonstrationProvider(FakeAIProvider):
        def merge_story(self, items):
            draft = super().merge_story(items)
            return draft.model_copy(
                update={
                    "technical_reading": reading,
                    "canonical_title": "分块缓存：用一份技术报告解释工程取舍（演示）",
                }
            )

    stories = build_stories(supplemented.items, provider=DemonstrationProvider(), now=now)
    assert len(stories) == 1, "Accompanying report and release must share one demo card"
    save_event_evidence(output, stories, now=now)
    story = stories[0]
    technical = BriefItem(
        id="preview-technical",
        section="ai_and_open_source",
        title=story.canonical_title,
        what_happened="配套发布与技术报告合为一张卡，下面分层展示方法、证据和限制。",
        source_urls=story.source_urls,
        story_ids=[story.id],
        story_contexts=[_story_context(story)],
    )
    history_path = root / "tests/fixtures/batch_a/history.json"
    case = json.loads(history_path.read_text(encoding="utf-8"))[0]
    historical = RawItem.model_validate(case["raw"])
    historical.metadata["backfill_recorded_at"] = "2026-10-01T08:00:00Z"
    history_story = build_stories([historical], provider=FakeAIProvider(), now=now)[0]
    history_card = BriefItem(
        id="preview-history",
        section="top_stories",
        title="Dots：真实历史材料的补录展示样例",
        what_happened=historical.summary,
        uncertainty="该卡只展示历史输入及补录标识，不代表新模型分析，也不代表当日恢复成功。",
        source_urls=history_story.source_urls,
        story_ids=[history_story.id],
        story_contexts=[_story_context(history_story)],
    )
    saved_path = root / "data/briefs/2026-10-07.json"
    saved = DailyBrief.model_validate_json(saved_path.read_text(encoding="utf-8"))
    saved_item = next(
        item
        for section in [saved.top_stories, saved.ai_and_open_source, saved.other_reading]
        for item in section
        if item.generation_status == "generated"
    ).model_copy(deep=True)
    saved_item.id = "preview-saved-" + saved_item.id
    followup = BriefItem(
        id="preview-followup",
        section="ai_and_open_source",
        title="缓存工具修正更新一致性问题（演示）",
        what_happened="新演示版本增加主动失效检查，能否适应高更新频率仍需测量。",
        why_it_matters="接入频繁更新的数据前，需要把一致性检查的额外成本纳入评测。",
        uncertainty="演示内容；没有真实发布或性能验证。",
        source_urls=[release.url],
        story_ids=["demo-followup"],
        continuity_contexts=[
            BriefContinuityContext(
                current_story_id="demo-followup",
                relation_type="follow_up",
                what_changed="此前只描述命中缓存的读取收益；本次新增主动失效检查。",
            )
        ],
    )
    brief = DailyBrief(
        date=now.date(),
        generated_at=now,
        timezone="Asia/Singapore",
        top_stories=[history_card, saved_item],
        ai_and_open_source=[technical, followup],
    )
    labels = {
        history_card.id: "真实历史 raw + Fake Provider 展示；补录日期为演示标记",
        saved_item.id: "已保存的 2026-10-07 生成产物；不是本次模型调用，也不是原始 API 响应",
        technical.id: "完全合成的技术材料 + Fake Provider；仅验结构与交互",
        followup.id: "完全合成的后续报道；仅验增量表达与展示",
    }
    SiteBuilder(template_dir=root / "templates", output_dir=output / "site").build(
        [brief],
        stylesheet=root / "site/assets/style.css",
        preview_labels=labels,
        preview_notice=(
            "B1 隔离预览 · 混合日期材料 · 无付费模型调用 · "
            "每卡标识来源；不代表真实模型选题或分析质量"
        ),
    )
    (output / "preview-brief.json").write_text(brief.model_dump_json(indent=2), encoding="utf-8")
    manifest = {
        "mode": "offline",
        "model": "FakeAIProvider / saved output / authored demonstration",
        "paid_calls": 0,
        "network": "httpx.MockTransport only",
        "labels": labels,
        "saved_output": {
            "path": str(saved_path),
            "sha256": hashlib.sha256(saved_path.read_bytes()).hexdigest(),
        },
        "historical_input": {"path": str(history_path), "case_id": case["case_id"]},
    }
    (output / "preview-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output / "site/index.html"

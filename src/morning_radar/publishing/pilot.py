"""Render a saved candidate privately; this command cannot generate or approve news."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from morning_radar.models import DailyBrief
from morning_radar.publishing.site_builder import SiteBuilder

SourceKind = Literal["saved_model_output", "codex_edited_frozen"]
SECTIONS = (
    "top_stories", "market_and_companies", "ai_and_open_source",
    "trend_radar", "developer_discussions", "other_reading",
)


def build_pilot_preview(
    root: Path, brief_path: Path, output: Path, *, source_kind: SourceKind,
) -> Path:
    """Create one new local preview and a hash-bound, initially pending review form."""
    labels = {
        "saved_model_output": "已有模型候选，待负责人审阅",
        "codex_edited_frozen": "Codex 基于冻结材料编辑，待负责人审阅",
    }
    if source_kind not in labels:
        raise ValueError("Unknown candidate provenance")
    root, output = root.resolve(), output.resolve()
    for protected in (root / "data", root / "site"):
        if output == protected or protected in output.parents or output in protected.parents:
            raise ValueError("Pilot preview must be isolated from production data/site")
    original = brief_path.read_bytes()
    brief = DailyBrief.model_validate_json(original)
    cards = [card for section in SECTIONS for card in getattr(brief, section)]
    if not cards or len({card.id for card in cards}) != len(cards):
        raise ValueError("Pilot candidate needs nonempty, uniquely identified cards")
    output.mkdir(parents=True, exist_ok=False)
    (output / "candidate.json").write_bytes(original)
    review = {
        "schema_version": 1,
        "candidate_sha256": hashlib.sha256(original).hexdigest(),
        "source_kind": source_kind,
        "status": "pending_owner_review",
        "owner_confirmed": False,
        "actual_reading_minutes": None,
        "most_valuable_three": [],
        "most_important_omission": None,
        "factual_or_understanding_problems": None,
        "would_read_tomorrow": None,
        "accepted": None,
        "allowed_card_actions": ["keep", "correct", "hide", "return"],
        "cards": [
            {"id": card.id, "title": card.title, "action": None, "note": None}
            for card in cards
        ],
        "notice": "填写后由负责人提交审阅意见；本记录不会触发发布或通知。"
                  "修改候选须生成新副本并重新审阅，不能沿用旧哈希的确认。",
        "tendency": "frozen_and_not_rendered",
        "generation_status_is_not_acceptance": True,
    }
    (output / "owner-review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    SiteBuilder(template_dir=root / "templates", output_dir=output / "site").build(
        [brief], stylesheet=root / "site/assets/style.css",
        preview_notice=(
            "R1 私人候选 · " + labels[source_kind]
            + "。请按实际阅读体验填写旁边的 owner-review.json，不设固定阅读时长；"
            "可逐条保留、修正、隐藏或退回。页面生成成功不代表事实验收通过。"
        ),
        preview_labels={card.id: labels[source_kind] for card in cards},
        tendency_workspace=None,
    )
    return output / "site/index.html"

from datetime import UTC, date, datetime
from pathlib import Path

from morning_radar.models import (
    BriefContinuityContext,
    BriefItem,
    BriefJudgementCue,
    DailyBrief,
    DailyContinuity,
    JudgementRecord,
    JudgementUpdateKind,
    StoryEvidenceRef,
    StoryOccurrenceRef,
)
from morning_radar.publishing import SiteBuilder


def _brief(day: date, *, continuity: bool) -> DailyBrief:
    contexts = []
    if continuity:
        contexts = [
            BriefContinuityContext(
                current_story_id="story-current",
                relation_type="follow_up",
                what_changed="Example SDK 从 RC 推进到稳定版本。",
                previous_story_date=date(2026, 8, 1),
                previous_story_id="story-previous",
                previous_story_title="Example SDK RC",
                watch_matches=["观察 Example SDK 是否发布稳定版本。"],
                judgement_cues=[
                    BriefJudgementCue(
                        judgement_id="judgement-update",
                        update_kind="revised",
                        claim="部署瓶颈现在主要来自执行环境控制。",
                    ),
                    BriefJudgementCue(
                        judgement_id="judgement-dependent",
                        update_kind="needs_review",
                        claim="依赖旧解释的部署判断需要重新检查。",
                    ),
                ],
            )
        ]
    return DailyBrief(
        date=day,
        timezone="Asia/Singapore",
        generated_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
        top_stories=[
            BriefItem(
                id=f"brief-{day}",
                section="top_stories",
                title="Example SDK Stable",
                what_happened="Example SDK 发布稳定版本。",
                why_it_matters="开发者现在可以迁移。",
                source_urls=["https://example.com/release"],
                story_ids=["story-current" if continuity else "story-previous"],
                continuity_contexts=contexts,
            )
        ],
    )


def _judgement_records() -> list[DailyContinuity]:
    evidence = StoryEvidenceRef(
        story=StoryOccurrenceRef(date=date(2026, 8, 1), story_id="story-previous")
    )
    root = JudgementRecord(
        judgement_id="judgement-root",
        root_judgement_id="judgement-root",
        recorded_at=datetime(2026, 8, 1, tzinfo=UTC),
        claim="最初对执行环境的判断。",
        rationale="由当日 Story 支持。",
        evidence_refs=[evidence],
    )
    update = JudgementRecord(
        judgement_id="judgement-update",
        root_judgement_id="judgement-root",
        recorded_at=datetime(2026, 8, 2, tzinfo=UTC),
        claim="部署瓶颈现在主要来自执行环境控制。",
        rationale="新 Story 改变了核心解释。",
        evidence_refs=[
            StoryEvidenceRef(
                story=StoryOccurrenceRef(
                    date=date(2026, 8, 2), story_id="story-current"
                )
            )
        ],
        updates_judgement_id="judgement-root",
        update_kind=JudgementUpdateKind.REVISED,
    )
    return [
        DailyContinuity(
            date=date(2026, 8, 1),
            generated_at=root.recorded_at,
            judgements=[root],
        ),
        DailyContinuity(
            date=date(2026, 8, 2),
            generated_at=update.recorded_at,
            judgements=[update],
        ),
    ]


def test_site_renders_current_continuity_and_historical_annotation(
    tmp_path: Path,
) -> None:
    builder = SiteBuilder(template_dir=Path("templates"), output_dir=tmp_path / "site")
    builder.build(
        [_brief(date(2026, 8, 1), continuity=False), _brief(date(2026, 8, 2), continuity=True)],
        stylesheet=Path("site/assets/style.css"),
        continuities=_judgement_records(),
    )

    current = (tmp_path / "site/briefs/2026-08-02.html").read_text(encoding="utf-8")
    historical = (tmp_path / "site/briefs/2026-08-01.html").read_text(encoding="utf-8")

    assert "相比此前" in current
    assert "此前观察有进展" in current
    assert "此前判断需要修正" in current
    assert "相关判断需要复查" in current
    assert 'href="../briefs/2026-08-01.html#story-story-previous"' in current
    assert "后续更新" in historical
    assert "已于 2026-08-02被修正" in historical

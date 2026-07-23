from datetime import UTC, date, datetime
from pathlib import Path

from morning_radar.models import BriefItem, DailyBrief
from morning_radar.publishing import SiteBuilder


def test_site_builder_creates_index_archive_and_daily_page(tmp_path) -> None:
    item = BriefItem(
        id="brief-1",
        section="top_stories",
        title="很长的中文标题用于验证移动端能够自然换行而不会撑破页面布局",
        what_happened="发生了可追溯的 Fixture 事件。",
        why_it_matters="它验证了静态网站生成流程。",
        source_urls=["https://example.com/source"],
        story_ids=["story-1"],
    )
    brief = DailyBrief(
        date=date(2026, 7, 23),
        timezone="Asia/Singapore",
        generated_at=datetime(2026, 7, 23, tzinfo=UTC),
        top_stories=[item],
    )
    output = tmp_path / "site"

    SiteBuilder(template_dir=Path("templates"), output_dir=output).build(
        [brief],
        stylesheet=Path("site/assets/style.css"),
    )

    index = (output / "index.html").read_text(encoding="utf-8")
    assert (output / "archive.html").exists()
    assert (output / "briefs/2026-07-23.html").exists()
    assert "今日重点" in index
    assert "市场与公司" not in index
    assert 'href="https://example.com/source"' in index
    assert "viewport" in index

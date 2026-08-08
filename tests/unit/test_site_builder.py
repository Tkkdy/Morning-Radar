from datetime import UTC, date, datetime
from pathlib import Path

from morning_radar.models import (
    BriefItem,
    BriefStoryContext,
    DailyBrief,
    PublishedAtRole,
    StorySourceRef,
    StoryStatus,
)
from morning_radar.publishing import SiteBuilder


def test_site_builder_creates_index_archive_and_daily_page(tmp_path) -> None:
    item = BriefItem(
        id="brief-1",
        section="top_stories",
        title="很长的中文标题用于验证移动端能够自然换行而不会撑破页面布局",
        what_happened="发生了可追溯的 Fixture 事件。",
        why_it_matters="它验证了静态网站生成流程。",
        market_or_community_reaction="Fixture community reaction.",
        uncertainty="Fixture uncertainty.",
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
    assert item.title in index
    assert item.what_happened in index
    assert item.why_it_matters in index
    assert item.market_or_community_reaction in index
    assert item.uncertainty in index
    assert 'href="https://example.com/source"' in index
    assert "来源 1" in index
    assert "<details" not in index
    assert "viewport" in index


def test_site_builder_renders_v2_story_context_with_safe_source_semantics(tmp_path) -> None:
    now = datetime(2026, 8, 8, tzinfo=UTC)
    hn_url = "https://news.ycombinator.com/item?id=123"
    external_url = "https://example.com/original"
    hn_ref = StorySourceRef(
        raw_item_id="hn-1",
        title="HN source title",
        source_name="Hacker News",
        source_type="hacker_news",
        url=hn_url,
        published_at=now,
        published_at_role=PublishedAtRole.HN_SUBMISSION_TIME,
        fetched_at=now,
        discussion_url=hn_url,
    )
    github_ref = StorySourceRef(
        raw_item_id="github-1",
        title="Release source title",
        source_name="GitHub · example/project",
        source_type="github",
        url="https://github.com/example/project/releases/tag/v1",
        published_at=now,
        published_at_role=PublishedAtRole.GITHUB_RELEASE_PUBLISHED_TIME,
        fetched_at=now,
    )
    rss_ref = StorySourceRef(
        raw_item_id="rss-1",
        title="RSS source title",
        source_name="OpenAI Blog",
        source_type="rss",
        url="https://openai.example/post",
        published_at=None,
        published_at_role=PublishedAtRole.FEED_ENTRY_TIME,
        fetched_at=now,
    )
    external_hn_ref = StorySourceRef(
        raw_item_id="hn-2",
        title="External HN source title",
        source_name="Hacker News",
        source_type="hacker_news",
        url=external_url,
        published_at=now,
        published_at_role=PublishedAtRole.HN_SUBMISSION_TIME,
        fetched_at=now,
        discussion_url="https://news.ycombinator.com/item?id=456",
    )

    def item(index: int, source_ref: StorySourceRef) -> BriefItem:
        context = BriefStoryContext(
            story_id=f"story-{index}",
            canonical_title=f"Canonical {index}",
            category="ai_and_open_source",
            entity_names=["Example Corp"],
            product_names=["Example Product"],
            topic_names=["AI"],
            facts=[f"Verified fact {index}"],
            analysis=[f"Analysis {index}"],
            uncertainties=[f"Story uncertainty {index}"],
            status=StoryStatus.ANNOUNCED,
            primary_source_url=source_ref.url,
            source_refs=[source_ref],
        )
        return BriefItem(
            id=f"brief-{index}",
            section="top_stories",
            title=f"Card {index}",
            what_happened=f"What happened {index}",
            why_it_matters=f"Why it matters {index}",
            source_urls=[
                source_ref.url,
                *(
                    [source_ref.discussion_url]
                    if source_ref.discussion_url
                    and source_ref.discussion_url != source_ref.url
                    else []
                ),
            ],
            story_ids=[context.story_id],
            story_contexts=[context],
        )

    brief = DailyBrief(
        date=date(2026, 8, 8),
        timezone="Asia/Singapore",
        generated_at=now,
        top_stories=[
            item(1, hn_ref),
            item(2, github_ref),
            item(3, rss_ref),
            item(4, external_hn_ref),
        ],
    )
    output = tmp_path / "site"

    SiteBuilder(template_dir=Path("templates"), output_dir=output).build(
        [brief],
        stylesheet=Path("site/assets/style.css"),
    )

    html = (output / "index.html").read_text(encoding="utf-8")
    assert "Hacker News · 讨论" in html
    assert "2026-08-08 在 Hacker News 提交" in html
    assert "文章发布于" not in html
    assert html.count(f'href="{hn_url}"') == 1
    assert 'href="https://github.com/example/project/releases/tag/v1"' in html
    assert "GitHub · example/project · Release" in html
    assert "Release 发布于 2026-08-08" in html
    assert "OpenAI Blog · 原文" in html
    assert "来源条目时间：" not in html
    assert f'href="{external_url}"' in html
    assert 'href="https://news.ycombinator.com/item?id=456"' in html
    assert "已宣布" in html
    assert "主来源" in html
    assert "<details" in html
    assert "已验证事实" in html
    assert "HN source title" in html


def test_site_builder_falls_back_to_brief_url_when_primary_ref_is_missing(tmp_path) -> None:
    now = datetime(2026, 8, 8, tzinfo=UTC)
    fallback_url = "https://example.com/fallback"
    context = BriefStoryContext(
        story_id="story-1",
        canonical_title="Canonical",
        category="ai_and_open_source",
        primary_source_url=fallback_url,
        source_refs=[
            StorySourceRef(
                raw_item_id="item-1",
                title="Unmatched source",
                source_name="Example",
                source_type="rss",
                url="https://example.com/unmatched",
                fetched_at=now,
            )
        ],
    )
    item = BriefItem(
        id="brief-1",
        section="top_stories",
        title="Card",
        what_happened="What happened",
        why_it_matters="Why it matters",
        source_urls=[fallback_url],
        story_ids=[context.story_id],
        story_contexts=[context],
    )
    brief = DailyBrief(
        date=date(2026, 8, 8),
        timezone="Asia/Singapore",
        generated_at=now,
        top_stories=[item],
    )
    output = tmp_path / "site"

    SiteBuilder(template_dir=Path("templates"), output_dir=output).build(
        [brief],
        stylesheet=Path("site/assets/style.css"),
    )

    html = (output / "index.html").read_text(encoding="utf-8")
    assert f'href="{fallback_url}"' in html
    assert "来源 1" in html


def test_site_builder_labels_dated_rss_time_as_feed_entry_time(tmp_path) -> None:
    published_at = datetime(2026, 8, 8, tzinfo=UTC)
    source_ref = StorySourceRef(
        raw_item_id="rss-1",
        title="RSS source title",
        source_name="OpenAI Blog",
        source_type="rss",
        url="https://openai.example/post",
        published_at=published_at,
        published_at_role=PublishedAtRole.FEED_ENTRY_TIME,
        fetched_at=published_at,
    )
    context = BriefStoryContext(
        story_id="story-1",
        canonical_title="Canonical",
        category="ai_and_open_source",
        primary_source_url=source_ref.url,
        source_refs=[source_ref],
    )
    item = BriefItem(
        id="brief-1",
        section="top_stories",
        title="Card",
        what_happened="What happened",
        why_it_matters="Why it matters",
        source_urls=[source_ref.url],
        story_ids=[context.story_id],
        story_contexts=[context],
    )
    brief = DailyBrief(
        date=date(2026, 8, 8),
        timezone="Asia/Singapore",
        generated_at=published_at,
        top_stories=[item],
    )
    output = tmp_path / "site"

    SiteBuilder(template_dir=Path("templates"), output_dir=output).build(
        [brief],
        stylesheet=Path("site/assets/style.css"),
    )

    html = (output / "index.html").read_text(encoding="utf-8")
    assert "来源条目时间：2026-08-08" in html
    assert "文章发布于" not in html

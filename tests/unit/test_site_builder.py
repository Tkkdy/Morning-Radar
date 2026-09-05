from datetime import UTC, date, datetime
from pathlib import Path

from morning_radar.models import (
    BriefItem,
    BriefStoryContext,
    BriefTendency,
    DailyBrief,
    PublishedAtRole,
    RadarEvidenceRef,
    RadarSignal,
    SourceRole,
    StatementType,
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
    assert '<p class="section-label">今日重点</p>' in index
    assert '<p class="section-label">top_stories</p>' not in index
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
    historical = (output / "briefs/2026-07-23.html").read_text(encoding="utf-8")
    assert 'href="assets/style.css"' in index
    assert 'href="archive.html"' in index
    assert 'href="../assets/style.css"' in historical
    assert 'href="../archive.html"' in historical
    assert 'href="../index.html"' in historical
    assert item.title in historical


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
    assert html.count(f'href="{external_url}"') == 1
    assert html.count('href="https://news.ycombinator.com/item?id=456"') == 1
    assert "example.com · 原文" in html
    assert "经 Hacker News 发现" in html
    assert "已宣布" in html
    assert "主来源" in html
    assert "<details" in html
    assert "已验证事实" in html
    assert "HN source title" in html


def test_site_builder_renders_other_reading_compactly_without_main_duplication(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 8, tzinfo=UTC)
    source_ref = StorySourceRef(
        raw_item_id="rss-1",
        title="Source",
        source_name="Official Feed",
        source_type="rss",
        url="https://example.com/other",
        published_at=now,
        published_at_role=PublishedAtRole.FEED_ENTRY_TIME,
        fetched_at=now,
    )
    context = BriefStoryContext(
        story_id="story-other",
        canonical_title="其他内容",
        category="ai_and_open_source",
        facts=["已验证事实"],
        analysis=["分析内容"],
        uncertainties=["仍有不确定性"],
        entity_names=["Example"],
        product_names=["Example SDK"],
        topic_names=["AI"],
        status=StoryStatus.UPDATED,
        primary_source_url=source_ref.url,
        source_refs=[source_ref],
    )
    other = BriefItem(
        id="brief-other",
        section="other_reading",
        title="值得继续阅读的条目",
        what_happened="这是简短摘要。",
        why_it_matters="这段信息只在展开详情后通过 Story context 呈现。",
        source_urls=[source_ref.url],
        story_ids=[context.story_id],
        story_contexts=[context],
    )
    brief = DailyBrief(
        date=date(2026, 8, 8),
        timezone="Asia/Singapore",
        generated_at=now,
        other_reading=[other],
    )
    output = tmp_path / "site"

    SiteBuilder(template_dir=Path("templates"), output_dir=output).build(
        [brief],
        stylesheet=Path("site/assets/style.css"),
    )

    html = (output / "index.html").read_text(encoding="utf-8")
    assert "其他值得阅读" in html
    assert html.count(other.title) == 1
    assert 'class="brief-card compact"' in html
    assert other.what_happened in html
    assert other.why_it_matters not in html
    assert "Official Feed · 原文" in html
    assert "已更新" in html
    assert "查看详情" in html
    assert "分析内容" in html
    assert "仍有不确定性" in html


def test_site_builder_hides_empty_other_reading_for_legacy_brief(tmp_path) -> None:
    brief = DailyBrief.model_validate(
        {
            "date": "2026-08-08",
            "timezone": "Asia/Singapore",
            "generated_at": "2026-08-08T00:00:00Z",
        }
    )
    output = tmp_path / "site"

    SiteBuilder(template_dir=Path("templates"), output_dir=output).build(
        [brief],
        stylesheet=Path("site/assets/style.css"),
    )

    assert "其他值得阅读" not in (output / "index.html").read_text(encoding="utf-8")


def test_archive_summarizes_content_without_new_persistent_data(tmp_path) -> None:
    main = BriefItem(
        id="brief-main",
        section="top_stories",
        title="当天最值得关注的事件",
        what_happened="发生了重要事件。",
        why_it_matters="它值得关注。",
        source_urls=["https://example.com/main"],
        story_ids=["story-main"],
    )
    other = BriefItem(
        id="brief-other",
        section="other_reading",
        title="延伸阅读",
        what_happened="补充信息。",
        why_it_matters="提供额外背景。",
        source_urls=["https://example.com/other"],
        story_ids=["story-other"],
    )
    briefs = [
        DailyBrief(
            date=date(2026, 8, 9),
            timezone="Asia/Singapore",
            generated_at=datetime(2026, 8, 9, tzinfo=UTC),
            top_stories=[main],
            other_reading=[other],
        ),
        DailyBrief(
            date=date(2026, 8, 8),
            timezone="Asia/Singapore",
            generated_at=datetime(2026, 8, 8, tzinfo=UTC),
        ),
    ]
    output = tmp_path / "site"

    SiteBuilder(template_dir=Path("templates"), output_dir=output).build(
        briefs,
        stylesheet=Path("site/assets/style.css"),
    )

    archive = (output / "archive.html").read_text(encoding="utf-8")
    assert "当天最值得关注的事件" in archive
    assert "1 条主内容 · 1 条延伸阅读" in archive
    assert "当日无主内容" in archive
    assert "0 条主内容" in archive


def test_expanded_details_do_not_repeat_visible_card_narratives(tmp_path) -> None:
    context = BriefStoryContext(
        story_id="story-1",
        canonical_title="Canonical",
        category="ai_and_open_source",
        facts=["卡片事实", "额外事实"],
        analysis=["卡片分析", "额外分析"],
        uncertainties=["卡片不确定性", "额外不确定性"],
        primary_source_url="https://example.com/story",
    )
    item = BriefItem(
        id="brief-1",
        section="top_stories",
        title="Card",
        what_happened="卡片事实",
        why_it_matters="卡片分析",
        uncertainty="卡片不确定性",
        source_urls=["https://example.com/story"],
        story_ids=[context.story_id],
        story_contexts=[context],
    )
    brief = DailyBrief(
        date=date(2026, 8, 9),
        timezone="Asia/Singapore",
        generated_at=datetime(2026, 8, 9, tzinfo=UTC),
        top_stories=[item],
    )
    output = tmp_path / "site"

    SiteBuilder(template_dir=Path("templates"), output_dir=output).build(
        [brief],
        stylesheet=Path("site/assets/style.css"),
    )

    html = (output / "index.html").read_text(encoding="utf-8")
    assert html.count("卡片事实") == 1
    assert html.count("卡片分析") == 1
    assert html.count("卡片不确定性") == 1
    assert "额外事实" in html
    assert "额外分析" in html
    assert "额外不确定性" in html


def test_details_control_is_hidden_when_context_has_no_additional_information(
    tmp_path,
) -> None:
    context = BriefStoryContext(
        story_id="story-1",
        canonical_title="Canonical",
        category="ai_and_open_source",
        facts=["卡片事实"],
        analysis=["卡片分析"],
        uncertainties=["卡片不确定性"],
        primary_source_url="https://example.com/story",
    )
    item = BriefItem(
        id="brief-1",
        section="top_stories",
        title="Card",
        what_happened="卡片事实",
        why_it_matters="卡片分析",
        uncertainty="卡片不确定性",
        source_urls=["https://example.com/story"],
        story_ids=[context.story_id],
        story_contexts=[context],
    )
    output = tmp_path / "site"

    SiteBuilder(template_dir=Path("templates"), output_dir=output).build(
        [
            DailyBrief(
                date=date(2026, 8, 9),
                timezone="Asia/Singapore",
                generated_at=datetime(2026, 8, 9, tzinfo=UTC),
                top_stories=[item],
            )
        ],
        stylesheet=Path("site/assets/style.css"),
    )

    html = (output / "index.html").read_text(encoding="utf-8")
    assert "<details" not in html
    assert html.count("卡片事实") == 1
    assert html.count("卡片分析") == 1
    assert html.count("卡片不确定性") == 1


def test_cognitive_question_and_watch_next_have_distinct_reader_facing_roles(
    tmp_path,
) -> None:
    brief = DailyBrief(
        date=date(2026, 8, 9),
        timezone="Asia/Singapore",
        generated_at=datetime(2026, 8, 9, tzinfo=UTC),
        cognitive_extension="OpenAI 的发布会将如何改变开发者选择？",
        watch_next=["观察 OpenAI 是否公布开发者开放时间表。"],
    )
    output = tmp_path / "site"

    SiteBuilder(template_dir=Path("templates"), output_dir=output).build(
        [brief],
        stylesheet=Path("site/assets/style.css"),
    )

    html = (output / "index.html").read_text(encoding="utf-8")
    assert "<h2>值得继续思考</h2>" in html
    assert "<h2>继续观察</h2>" in html
    assert "<h2>认知延伸</h2>" not in html


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


def test_site_builder_labels_radar_signal_as_unverified_and_renders_tendency(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    brief = DailyBrief(
        date=date(2026, 8, 16),
        timezone="Asia/Singapore",
        generated_at=now,
        radar_signals=[
            RadarSignal(
                id="radar-1",
                observed_at=now,
                claim="A practitioner observed a concrete workflow regression.",
                why_notable="The observation affects a widely used workflow.",
                support_refs=[
                    RadarEvidenceRef(
                        raw_item_id="item-1",
                        url="https://example.com/observation",
                        source_role=SourceRole.PRACTITIONER,
                    )
                ],
                source_roles=[SourceRole.PRACTITIONER],
                missing_evidence=["官方变更说明"],
                uncertainty="尚无独立复现。",
                statement_type=StatementType.FIRSTHAND_OBSERVATION,
            )
        ],
        tendencies=[
            BriefTendency(
                tendency_id="tendency-1",
                standing="emerging",
                claim="Agents are moving into governed workflows.",
                shared_mechanism="Access to organizational context drives the shift.",
                decision_rationale="Independent events persisted across dates.",
            )
        ],
    )
    output = tmp_path / "site"

    SiteBuilder(template_dir=Path("templates"), output_dir=output).build(
        [brief], stylesheet=Path("site/assets/style.css")
    )

    html = (output / "index.html").read_text(encoding="utf-8")
    assert "雷达信号 · 尚待验证" in html
    assert "不确定性" in html
    assert "结构趋势" in html
    assert "emerging" in html


def test_site_builder_handles_empty_radar_and_tendency_day(tmp_path) -> None:
    brief = DailyBrief(
        date=date(2026, 8, 16),
        timezone="Asia/Singapore",
        generated_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    output = tmp_path / "site"

    SiteBuilder(template_dir=Path("templates"), output_dir=output).build(
        [brief], stylesheet=Path("site/assets/style.css")
    )

    html = (output / "index.html").read_text(encoding="utf-8")
    assert "雷达信号 · 尚待验证" not in html
    assert "结构趋势" not in html

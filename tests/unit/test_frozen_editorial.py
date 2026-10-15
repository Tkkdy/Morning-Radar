import json
from html.parser import HTMLParser
from pathlib import Path

from test_research import SemanticScopeProvider

from morning_radar.ai.request_payload import build_topic_context, evidence_snapshot
from morning_radar.models import RawItem
from morning_radar.research.engine import resolve_research
from morning_radar.settings import TopicConfig, load_model_list
from tests.editorial_preview import build_frozen_preview


class DetailParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.details = 0
        self.technical_depths = []
        self.expanded = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details":
            self.details += 1
            self.expanded.append("open" in attrs)
        if attrs.get("class") == "technical-reading":
            self.technical_depths.append(self.details)

    def handle_endtag(self, tag):
        if tag == "details":
            self.details -= 1


def test_frozen_twelve_units_assemble_without_changing_fact_qualifiers(tmp_path):
    # pytest's basetemp is confined to this checkout's .tmp.
    brief, manifest = build_frozen_preview(tmp_path / "reading")
    assert len(brief.top_stories) == 2
    assert len(brief.ai_and_open_source) == 1
    assert len(brief.other_reading) == 7
    assert not brief.radar_signals  # ordinary Story leads are not fake Research decisions
    assert manifest["new_title_body_characters"] == 1199
    html = (tmp_path / "reading/site/index.html").read_text(encoding="utf-8")
    for qualifier in ("Wonder Full", "计划 2027", "厂商预期", "候选版", "有限公开预览",
                      "尚未合并", "本期未复现", "尚无本期独立数学核验"):
        assert qualifier in html
    for rejected in ("官方页面抓取标记", "official_page_fetched", "Full 型号不存在",
                     "OpenAI 尚未对上述审查结论作出公开回应", "2027 年前尚不会实际发生"):
        assert rejected not in html
    assert "Intel D270" not in html and "claude-migrate" not in html
    parser = DetailParser()
    parser.feed(html)
    assert parser.technical_depths == [1]
    assert not any(parser.expanded)
    assert "GPQA-Diamond" in html
    boundaries = json.loads(Path(
        "tests/fixtures/editorial_alignment/fact-boundaries.json"
    ).read_text(encoding="utf-8"))
    assert len(boundaries["cases"]) == 5
    assert not boundaries["owner_confirmed"]


def test_frozen_python_tail_with_actual_topic_configuration():
    fixture = json.loads(Path(
        "tests/fixtures/editorial_alignment/python-scope.json"
    ).read_text(encoding="utf-8"))
    item = RawItem.model_validate(fixture["item"])
    before = evidence_snapshot(item, association_basis="lead")
    assert "LLM CLI" not in before.content_excerpt
    provider = SemanticScopeProvider(in_scope=False)
    provider.topic_context = build_topic_context(
        load_model_list(Path("config/topics.yaml"), "topics", TopicConfig),
    )
    result = resolve_research([item], provider=provider, maximum_cases=8, maximum_radar_signals=3)
    after = result.cases[0].lead
    assert "LLM CLI" in after.content_excerpt
    assert "startup cost" in after.content_excerpt
    assert after.content_excerpt == item.content_excerpt[after.excerpt_start:after.excerpt_end]
    assert len(after.content_excerpt) <= 480
    assert result.item_outcomes[item.id].value == "waiting_evidence"
    assert result.stats["research_input_characters"] <= 12000

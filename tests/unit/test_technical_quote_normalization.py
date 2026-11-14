"""Strict, source-local quote restoration; no model quality claims."""
from datetime import UTC, datetime

import pytest

from morning_radar.ai import AIOutputError, FakeAIProvider
from morning_radar.models import RawItem, Story
from morning_radar.models.core import GroundedPoint, TechnicalReading
from morning_radar.processing.story_builder import _validate_draft_urls, build_story
from morning_radar.research.materials import validate_technical_reading

NOW = datetime(2026, 10, 11, tzinfo=UTC)
SOURCE = "The model’s baseline score is 12 and it is not independently verified."
QUOTE = "The model's baseline score is 12 and it is not independently verified."


def item(text=SOURCE, **changes):
    return RawItem(id="quote-source", title="技术报告", url="https://example.org/report",
                   source_name="作者", source_type="rss", fetched_at=NOW,
                   summary=text).model_copy(update=changes)


def reading(quote=QUOTE, url="https://example.org/report"):
    point = GroundedPoint(text="作者报告了基线结果，尚未独立验证。", source_url=url, quote=quote)
    return TechnicalReading(kind="technical_report", problem=point, method=point)


def test_source_original_quotes_and_audit_survive_story_serialization():
    raw = item()
    provider = FakeAIProvider()
    draft = provider.merge_story([raw]).model_copy(update={"technical_reading": reading()})
    original = draft.model_dump()
    provider.merge_story = lambda _: draft
    # Production grouping may validate once before build_story validates again.
    _validate_draft_urls(draft, [raw])
    story = build_story([raw], provider=provider, now=NOW)
    restored = Story.model_validate_json(story.model_dump_json())
    assert restored.technical_reading.problem.quote == SOURCE
    for record in restored.technical_quote_normalizations:
        assert record.original_quote == QUOTE
        assert record.rule == "quotation-marks-v1"
        assert record.raw_item_id == raw.id and record.source_field == "summary"
        assert raw.summary[record.source_start:record.source_end] == SOURCE
    assert len(restored.technical_quote_normalizations) == 2
    assert draft.model_dump() == original and raw.summary == SOURCE


def test_exact_match_in_later_fragment_has_priority():
    result, audit = validate_technical_reading(reading(), [item(content_excerpt=QUOTE)])
    assert result.problem.quote == QUOTE and audit == []


@pytest.mark.parametrize("source,quote,rule", [
    ("The “given policy” is used.", 'The "given policy" is used.', "quotation-marks-v1"),
    ("The model’s  baseline\nscore is 12.", "The model’s baseline score is 12.", "whitespace-v1"),
])
def test_finite_typography_and_existing_whitespace_restore_contiguous_source(source, quote, rule):
    result, audit = validate_technical_reading(reading(quote), [item(source)])
    assert result.problem.quote == source and audit[0].rule == rule


@pytest.mark.parametrize("quote", [
    QUOTE.replace("12", "13"),
    QUOTE.replace("not independently", "independently"),
    QUOTE.replace("baseline", "improved"),
    QUOTE.replace("The", "THE"),
    QUOTE.replace("baseline", "base-line"),
    QUOTE.replace("12", "１２"),
    QUOTE.replace(" score is 12", " …"),
])
def test_substantive_or_unlisted_character_changes_are_rejected(quote):
    with pytest.raises(AIOutputError, match="absent"):
        validate_technical_reading(reading(quote), [item()])


def test_cannot_join_fields_or_items_or_borrow_another_url():
    quote = "A contiguous source fragment must retain its original identity."
    candidates = [
        [item("A contiguous source fragment",
              content_excerpt="must retain its original identity.")],
        [item("A contiguous source fragment"),
         item("must retain its original identity.", id="other")],
        [item(quote, url="https://example.org/other")],
    ]
    for inputs in candidates:
        with pytest.raises(AIOutputError, match="absent"):
            validate_technical_reading(reading(quote), inputs)


def test_failure_does_not_partially_restore_input():
    value = reading()
    value.method = value.method.model_copy(update={
        "quote": "An invented method absent from the excerpt.",
    })
    before = value.model_dump()
    with pytest.raises(AIOutputError, match="absent"):
        validate_technical_reading(value, [item()])
    assert value.model_dump() == before


def test_real_missing_method_preserves_story_without_technical_card():
    import json
    from pathlib import Path

    from morning_radar.ai.models import MergedStoryDraft

    fixture = Path("tests/fixtures/real_acceptance/technical-missing-method.json")
    evidence = json.loads(fixture.read_text(encoding="utf-8"))
    inputs = [RawItem.model_validate(x) for x in evidence["inputs"]]
    draft = MergedStoryDraft.model_validate_json(evidence["content"])
    before = draft.model_dump()
    provider = FakeAIProvider()
    provider.merge_story = lambda _: draft
    story = build_story(inputs, provider=provider, now=NOW)
    assert story.technical_reading is None
    assert story.facts == draft.facts and story.source_urls == draft.source_urls
    assert any("技术阅读卡未展示" in x and "方法概述" in x for x in story.uncertainties)
    assert draft.model_dump() == before


@pytest.mark.parametrize("missing", ["problem", "method"])
def test_incomplete_card_still_rejects_invalid_supplied_evidence(missing):
    value = reading("Invented evidence that is absent from the original source.")
    setattr(value, missing, None)
    with pytest.raises(AIOutputError, match="absent"):
        validate_technical_reading(value, [item()])

from datetime import UTC, datetime

from morning_radar.ai.models import ClassificationBatch, ClassifiedItem
from morning_radar.models import RawItem
from morning_radar.processing.story_builder import build_stories


def test_title_only_failure_keeps_original_lead_without_model_calls():
    class NoCalls:
        def __getattr__(self, name):
            raise AssertionError("Title-only lead must not invoke " + name)
    now = datetime(2026, 10, 13, tzinfo=UTC)
    item = RawItem(id="synthetic-empty", title="Recover desktop sessions after switching accounts",
                   url="https://example.org/original", source_name="Synthetic discovery",
                   source_type="hacker_news", fetched_at=now)
    stories = build_stories(
        [item], provider=NoCalls(), now=now, classifications=ClassificationBatch(
        items=[ClassifiedItem(item_id=item.id, relevant=True, relevance_reason="synthetic",
                              category="ai_and_open_source", important=True,
                              importance_reason="synthetic")]))
    assert len(stories) == 1 and not stories[0].facts
    assert stories[0].source_refs[0].title == item.title
    assert stories[0].source_urls == [item.url]
    assert "缺少摘要或原文摘录" in stories[0].uncertainties[0]

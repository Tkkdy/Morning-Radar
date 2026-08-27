from datetime import UTC, date, datetime

from morning_radar.candidates import admit_candidates
from morning_radar.diagnostics import DecisionStage, DecisionTraceBuilder
from morning_radar.models import RawItem

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def test_every_accepted_raw_has_final_disposition() -> None:
    raw = RawItem(
        id="raw-one",
        title="Unresolved AI lead",
        url="https://example.com/lead",
        source_name="Example",
        source_type="fixture",
        fetched_at=NOW,
    )
    builder = DecisionTraceBuilder([raw])
    builder.add_candidates(admit_candidates([raw], now=NOW))

    trace = builder.finish(
        trace_date=date(2026, 8, 22),
        generated_at=NOW,
        stories=[],
        brief_story_ids=set(),
    )

    stages = [item.stage for item in trace.records[0].transitions]
    assert stages[0] is DecisionStage.RAW_ACCEPTANCE
    assert stages[-1] is DecisionStage.FINAL_DISPOSITION
    assert trace.records[0].transitions[-1].decision == "NO_STORY"

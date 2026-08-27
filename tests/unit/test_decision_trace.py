from datetime import UTC, date, datetime

from morning_radar.candidates import admit_candidates
from morning_radar.diagnostics import DecisionStage, DecisionTraceBuilder
from morning_radar.models import RawItem, SemanticDisposition

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


def test_trace_preserves_initial_triage_evidence_addition_and_retriage_order() -> None:
    raw = RawItem(
        id="raw-one",
        title="Unresolved AI lead",
        url="https://example.com/lead",
        source_name="Example",
        source_type="fixture",
        fetched_at=NOW,
    )
    [admitted] = admit_candidates([raw], now=NOW)
    investigated = admitted.model_copy(
        update={"semantic_disposition": SemanticDisposition.INVESTIGATE}
    )
    built = investigated.model_copy(
        update={"semantic_disposition": SemanticDisposition.BUILD}
    )
    builder = DecisionTraceBuilder([raw])

    builder.add_candidate_admissions([admitted])
    builder.add_candidate_triage([investigated])
    builder.add_investigation_event(
        [raw.id],
        candidate_id=admitted.id,
        decision="EVIDENCE_ADDED",
        evidence_id="evidence-new",
    )
    builder.add_candidate_triage([built], stage=DecisionStage.SEMANTIC_RETRIAGE)

    transitions = builder.records[raw.id].transitions
    decisions = [(transition.stage, transition.decision) for transition in transitions]
    assert (DecisionStage.SEMANTIC_TRIAGE, "INVESTIGATE") in decisions
    assert (DecisionStage.INVESTIGATION, "EVIDENCE_ADDED") in decisions
    assert (DecisionStage.SEMANTIC_RETRIAGE, "BUILD") in decisions
    assert [transition.evidence_id for transition in transitions if transition.evidence_id] == [
        "evidence-new"
    ]

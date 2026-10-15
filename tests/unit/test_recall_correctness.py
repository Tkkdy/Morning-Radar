"""Offline recall contracts; no claim of improved end-to-end news relevance."""
import shutil
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_intake_reliability import NOW, raw_item
from test_research import SemanticScopeProvider

from morning_radar.ai.request_payload import evidence_snapshot, fit_research_request
from morning_radar.collectors.hacker_news import HackerNewsCollector
from morning_radar.collectors.hn_common import hn_item, merge_hn_observations
from morning_radar.collectors.hn_search import HNSearchCollector
from morning_radar.collectors.orchestrator import CollectionResult
from morning_radar.intake.candidates import _lab_id, select_process_candidates
from morning_radar.intake.checkpoint import write_intake_checkpoint
from morning_radar.intake.models import ProcessingStatus, ReasonCode
from morning_radar.intake.service import _intake_run_from_checkpoint, prepare_process
from morning_radar.processing.attention import attention_key, observed_count
from morning_radar.processing.matching import alias_matches
from morning_radar.processing.story_builder import _candidate_lane
from morning_radar.research.engine import build_research_cases, resolve_research
from morning_radar.settings import AppConfig, load_model


@pytest.mark.parametrize("text,expected", [
    ("Ollama release", False), ("Llama 3.1", True), ("LLAMA-3.2", True),
    ("Llama3.3", True), ("(llama)", True), ("llamafile", False),
    ("myllama", False), ("ＬＬＡＭＡ 4", True),
])
def test_alias_boundaries(text, expected):
    assert alias_matches("Llama", text) is expected


def test_source_id_precedes_text_alias():
    item = raw_item("id", published_at=NOW).model_copy(
        update={"title": "FirstLab release", "metadata": {"source_id": "second"}},
    )
    labs = [SimpleNamespace(id="first", aliases=["FirstLab"], official_source_id="first"),
            SimpleNamespace(id="second", aliases=["SecondLab"], official_source_id="second")]
    assert _lab_id(item, labs) == "second"


def hn(*, text="", score=None, comments=None, reason="high_signal_discovery", at=NOW):
    return hn_item(
        story_id=123, title="A concrete model implementation update", text=text,
        original_url="https://example.com/material", author="submitter",
        submitted_at=int(NOW.timestamp()), fetched_at=at,
        metadata={"score": score, "comments": comments, "selection_reason": reason,
                  "discovery_paths": [reason]},
    )


def test_complementary_hn_observations_keep_measured_values_and_origins():
    high = hn(score=210, comments=0).model_copy(
        update={"practice_signal_kind": "implementation_insight"},
    )
    watched = hn(text="Longer source excerpt", reason="watchlist_discovery",
                 at=NOW + timedelta(minutes=3))
    [merged] = merge_hn_observations([high, watched])
    assert merged.metadata["selection_reason"] == "watchlist_discovery"
    assert set(merged.metadata["discovery_reasons"]) == {
        "high_signal_discovery", "watchlist_discovery",
    }
    assert merged.metadata["score"] == 210
    assert merged.metadata["comments"] == 0
    assert merged.metadata["score_observed_at"] == NOW.isoformat()
    assert len(merged.metadata["hn_observations"]) == 2
    assert merged.practice_signal_kind == "implementation_insight"
    assert _candidate_lane(merged) == "practice_discovery"
    [case] = build_research_cases([merged], maximum_cases=8)
    assert case.supporting_evidence == []
    # Counters are observations, not monotonic maxima or sums.
    [latest] = merge_hn_observations([
        merged, hn(score=190, at=NOW + timedelta(minutes=5)),
    ])
    assert latest.metadata["score"] == 190
    assert len(latest.metadata["hn_observations"]) == 3
    assert len(merge_hn_observations([hn(text="version one"), hn(text="version two")])) == 2


def test_conflicting_simultaneous_counters_and_same_page_discoveries():
    [conflict] = merge_hn_observations([hn(score=10), hn(score=11)])
    assert conflict.metadata["score"] is None
    cases = build_research_cases([hn(), hn().model_copy(update={"id": "other-discussion"})],
                                maximum_cases=8)
    assert all(not case.supporting_evidence for case in cases)


def test_collectors_keep_zero_and_unknown_without_requests():
    collector = HackerNewsCollector(http=None, keywords=["model"], now=NOW)
    data = {"id": 123, "title": "model update", "time": int(NOW.timestamp())}
    assert collector._convert(data).metadata["score"] is None
    assert collector._convert({**data, "score": 0}).metadata["score"] == 0
    watch = SimpleNamespace(maximum_excerpt_characters=1600)
    search = HNSearchCollector(http=None, watchlist=watch, now=NOW)
    hit = {"objectID": "123", "title": "Llama3.1 update",
           "created_at_i": int(NOW.timestamp()), "points": 12, "num_comments": 0}
    result = search._convert(hit, "lab", ["Llama"], "Llama", 0, int(NOW.timestamp()))
    assert result.metadata["score"] == 12
    assert result.metadata["comments"] == 0
    assert search._convert({**hit, "title": "Ollama update"}, "lab", ["Llama"],
                           "Llama", 0, int(NOW.timestamp())) is None
    assert attention_key(None) != attention_key(0)
    assert attention_key(0) < attention_key(None)
    assert observed_count(False) is None


def test_market_suppression_before_capacity_preserves_raw_and_audit(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(Path("config"), project / "config")
    root = project / ".tmp/dry-run"
    low = raw_item("low", published_at=NOW).model_copy(
        update={"source_type": "market",
                "metadata": {"change_percent": 0.01, "priority": "high"}},
    )
    high = raw_item("alert", published_at=NOW).model_copy(
        update={"source_type": "market", "metadata": {"change_percent": -0.03}},
    )
    items = [low, high, *[raw_item(str(i), published_at=NOW) for i in range(17)]]
    checkpoint = write_intake_checkpoint(
        root, items=items, now=NOW, cutoff_at=NOW - timedelta(hours=30),
        collection=CollectionResult(items=items), source_state={},
    )
    raw_file = root / "data/intake/checkpoints" / f"{checkpoint.manifest.batch_id}.json"
    before = raw_file.read_bytes()
    app = load_model(project / "config/app.yaml", AppConfig)
    old = select_process_candidates(fresh=checkpoint.items, recovery=[], maximum_items=17,
                                    reserved_recovery_slots=4)
    assert low.id in {item.id for item in old.items}
    prepared = prepare_process(
        project, app, fixtures=True, intake=_intake_run_from_checkpoint(root, checkpoint), now=NOW,
    )
    assert len(prepared.selection.items) == 17
    assert low.id not in {item.id for item in prepared.selection.items}
    assert low.id not in {r.item.id for r in prepared.selection.deferred}
    record = next(r for r in checkpoint.items if r.item.id == low.id)
    entry = prepared.ledger.get(record.input_id, record.content_version)
    assert entry.processing == ProcessingStatus.EXCLUDED
    assert entry.reason_code == ReasonCode.ROUTINE_MARKET_SUPPRESSED
    alert = next(r for r in checkpoint.items if r.item.id == high.id)
    assert prepared.ledger.get(alert.input_id, alert.content_version).reason_code != (
        ReasonCode.ROUTINE_MARKET_SUPPRESSED
    )
    assert raw_file.read_bytes() == before
    # Both allocations retain the same cap; only eligible opportunity changes.
    assert len(old.items) == len(prepared.selection.items)


def test_late_scope_excerpt_is_contiguous_locatable_and_bounded():
    text = "General runtime details. " * 35 + "LLM CLI imports heavy modules at startup. " * 4
    item = hn(text=text)
    original = evidence_snapshot(item, association_basis="lead")
    selected = evidence_snapshot(item, association_basis="lead", scope_keywords=("LLM",))
    assert "LLM" not in original.content_excerpt
    assert "LLM" in selected.content_excerpt
    assert selected.excerpt_truncated
    assert selected.content_excerpt == item.content_excerpt[
        selected.excerpt_start:selected.excerpt_end
    ]
    assert len(selected.content_excerpt) <= 480
    cases = build_research_cases([item], maximum_cases=8, scope_keywords=("LLM",))
    fitted = fit_research_request(cases, topic_context=None, maximum_characters=12000)
    assert len(fitted.payload_text) <= 12000
    assert '"excerpt_start":' in fitted.payload_text
    provider = SemanticScopeProvider(in_scope=False)
    provider.topic_context = {"topics": [{"keywords": ["LLM"]}]}
    result = resolve_research([item], provider=provider, maximum_cases=8, maximum_radar_signals=3)
    assert result.item_outcomes[item.id] == ReasonCode.WAITING_EVIDENCE
    from morning_radar.pipeline import _research_decision
    audit = _research_decision(SimpleNamespace(item=item), result, now=NOW)
    assert audit.status == "ok"  # a resolution was executed, not omitted for budget
    assert audit.applied_disposition == "internal_only"
    assert "input_text_incomplete_scope_not_excluded" in audit.missing_evidence
    assert not result.radar_signals and not result.verified_item_ids
    assert provider.calls == 1


def test_complete_negative_scope_can_still_be_excluded():
    item = hn(text="A complete short report on unrelated bicycle parts.")
    result = resolve_research([item], provider=SemanticScopeProvider(in_scope=False),
                              maximum_cases=8, maximum_radar_signals=3)
    assert result.item_outcomes[item.id] == ReasonCode.RESEARCH_OUT_OF_SCOPE

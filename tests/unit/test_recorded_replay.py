from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from morning_radar.candidates import admit_candidates
from morning_radar.evaluation.recorded_replay import (
    RecordedReplayError,
    run_recorded_replay,
)
from morning_radar.models import (
    CandidateReasonCode,
    EvidenceState,
    ExecutionState,
    RawItem,
    SemanticDisposition,
    SourceRole,
    StatementType,
)
from morning_radar.storage import save_models, write_json

NOW = datetime(2026, 8, 22, tzinfo=UTC)
EVALUATION_DATE = date(2026, 8, 22)


def test_recorded_replay_downgrades_saved_model_build_without_provider(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    run = tmp_path / "run"
    (root / "config").mkdir(parents=True)
    (root / "config/sources.yaml").write_text("sources: []\n", encoding="utf-8")
    raw = RawItem(
        id="lead",
        title="HN lead pointing at a famous destination",
        url="https://www.nytimes.com/example",
        source_name="Hacker News",
        source_type="hacker_news",
        fetched_at=NOW,
        source_role=SourceRole.COMMUNITY_DISCOVERY,
        statement_type=StatementType.UNVERIFIED_LEAD,
    )
    write_json(root / "data/raw/2026-08-22.json", [raw.model_dump(mode="json")])
    [admitted] = admit_candidates([raw], now=NOW)
    recorded = admitted.model_copy(
        update={
            "semantic_disposition": SemanticDisposition.BUILD,
            "evidence_state": EvidenceState.SUFFICIENT,
            "execution_state": ExecutionState.NOT_NEEDED,
            "reason_codes": [CandidateReasonCode.DEVELOPER_IMPACT],
        }
    )
    save_models(run / "data/candidates/2026-08-22.json", [recorded])
    write_json(
        run / "summary.json",
        {
            "status": "COMPLETED",
            "environment": {"provider": "deepseek", "git_sha": "recorded-sha"},
        },
    )
    (run / "candidates.jsonl").write_text(
        json.dumps(
            {
                "candidate_id": recorded.id,
                "semantic_disposition": "build",
                "model_semantic_disposition": "build",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run / "stories.jsonl").write_text("", encoding="utf-8")

    report = run_recorded_replay(
        root=root,
        run_directory=run,
        evaluation_date=EVALUATION_DATE,
    )

    assert report["provider_calls"] == 0
    assert report["baseline_distribution"] == {"build": 1}
    assert report["replayed_distribution"] == {"investigate": 1}
    assert report["build_to_investigate"][0]["candidate_id"] == recorded.id


def test_recorded_replay_fails_closed_when_artifacts_are_missing(
    tmp_path: Path,
) -> None:
    with pytest.raises(RecordedReplayError, match="NOT_REPLAYABLE"):
        run_recorded_replay(
            root=tmp_path,
            run_directory=tmp_path / "missing",
            evaluation_date=EVALUATION_DATE,
        )

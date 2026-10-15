"""Offline safety and review-state contract for saved personal candidates."""
import hashlib
import json
from pathlib import Path

import pytest

from morning_radar.cli import main
from morning_radar.publishing.pilot import build_pilot_preview


def test_private_preview_does_not_construct_pipeline_or_render_legacy_tendency(
    tmp_path, monkeypatch,
):
    import morning_radar.cli as cli

    def forbidden(*args, **kwargs):
        raise AssertionError("Private rendering must not start the generation pipeline")

    monkeypatch.setattr(cli, "MorningRadarPipeline", forbidden)
    data = json.loads(Path("data/briefs/2026-10-13.json").read_text(encoding="utf-8"))
    data["tendencies"] = [{
        "tendency_id": "legacy", "standing": "approved", "claim": "UNAPPROVED_PRIVATE_CLAIM",
        "shared_mechanism": "UNAPPROVED_PRIVATE_CLAIM", "decision_rationale": "model approved",
    }]
    source = tmp_path / "source.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    before = source.read_bytes()
    output = tmp_path / "private"
    assert main(["preview-pilot", "--brief", str(source), "--output", str(output),
                 "--source-kind", "saved_model_output"]) == 0
    page = (output / "site/index.html").read_text(encoding="utf-8")
    review = json.loads((output / "owner-review.json").read_text(encoding="utf-8"))
    assert "UNAPPROVED_PRIVATE_CLAIM" not in page
    assert not (output / "site/tendency").exists()
    assert "待负责人审阅" in page
    assert review["candidate_sha256"] == hashlib.sha256(before).hexdigest()
    assert review["status"] == "pending_owner_review"
    assert review["accepted"] is None and review["actual_reading_minutes"] is None
    assert not review["owner_confirmed"]
    assert source.read_bytes() == (output / "candidate.json").read_bytes() == before
    assert not (output / "data").exists()


def test_private_preview_refuses_production_and_existing_output(tmp_path):
    root = Path(".")
    source = root / "data/briefs/2026-10-13.json"
    for output in [root, root / "data/pilot", root / "site/pilot"]:
        with pytest.raises(ValueError, match="isolated"):
            build_pilot_preview(root, source, output, source_kind="saved_model_output")
    marker = tmp_path / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        build_pilot_preview(root, source, tmp_path, source_kind="codex_edited_frozen")
    assert marker.read_text(encoding="utf-8") == "keep"


def test_private_preview_rejects_invalid_candidate_before_creating_output(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text("{}", encoding="utf-8")
    output = tmp_path / "new"
    with pytest.raises(ValueError):
        build_pilot_preview(Path("."), source, output, source_kind="saved_model_output")
    assert not output.exists()

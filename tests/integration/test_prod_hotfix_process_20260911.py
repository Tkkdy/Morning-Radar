"""HF09: save the incident degradation through the fixture process boundary."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from morning_radar.ai import AIBudgetExceeded, AIOutputError, FakeAIProvider
from morning_radar.intake.generation import generation_is_complete
from morning_radar.models import DailyBrief, Signal, SignalType
from morning_radar.pipeline import MorningRadarPipeline
from morning_radar.storage import load_model, read_json


class IncidentProvider(FakeAIProvider):
    def __init__(self) -> None:
        super().__init__()
        self.recovery_calls = 0
        self.direction_calls = 0

    def write_brief(self, stories, signals):
        del stories, signals
        raise AIOutputError("simulated batch truncation")

    def recover_brief_item(self, story, signals, editorial_decision=None):
        self.recovery_calls += 1
        if self.recovery_calls > 1:
            raise AIBudgetExceeded("AI daily input character limit exceeded")
        return super().recover_brief_item(story, signals, editorial_decision)

    def write_direction_observation(self, signals):
        del signals
        self.direction_calls += 1
        raise AIBudgetExceeded("AI daily input character limit exceeded")


def _copy_fixture_project(source: Path, destination: Path) -> None:
    for directory in ("config", "fixtures", "templates"):
        shutil.copytree(source / directory, destination / directory)
    (destination / "site/assets").mkdir(parents=True)
    shutil.copy2(source / "site/assets/style.css", destination / "site/assets/style.css")


def test_hf09_fixture_process_saves_budget_degraded_brief(tmp_path, monkeypatch) -> None:
    source = Path(".").resolve()
    project = tmp_path / "project"
    _copy_fixture_project(source, project)
    provider = IncidentProvider()
    signal = Signal(
        id="incident-signal", signal_type=SignalType.TOPIC_HEATING, topic="ai_coding",
        window_days=3, supporting_story_ids=["story-openai", "story-deepseek"],
        supporting_source_count=2, supporting_company_count=0, strength=0.8,
        explanation="fixture evidence", created_at=datetime(2026, 7, 23, tzinfo=UTC),
        updated_at=datetime(2026, 7, 23, tzinfo=UTC),
    )
    monkeypatch.setattr("morning_radar.pipeline.FakeAIProvider", lambda: provider)
    monkeypatch.setattr(
        "morning_radar.pipeline.TrendDetector.detect", lambda *_args, **_kwargs: [signal]
    )

    brief = MorningRadarPipeline(project).run(fixtures=True, dry_run=True, notify=False)

    output = project / ".tmp/dry-run"
    saved = load_model(output / "data/briefs/2026-07-23.json", DailyBrief)
    assert brief.direction_observation is None
    assert saved.direction_observation is None
    assert saved.run_stats["ai_direction_fallback_reason"] == (
        "AI daily input character limit exceeded"
    )
    assert generation_is_complete(output, "2026-07-23")
    assert read_json(output / "data/intake/ledger.json")["entries"]
    raw_urls = {entry["url"] for entry in read_json(output / "data/raw/2026-07-23.json")}
    assert all(url in raw_urls for item in saved.top_stories for url in item.source_urls)
    assert provider.direction_calls == 1

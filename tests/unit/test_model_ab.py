from datetime import UTC, date, datetime, timedelta

from morning_radar.ai import FakeAIProvider
from morning_radar.evaluation.model_ab import run_model_ab_experiment
from morning_radar.models import Story
from morning_radar.storage import read_json, write_json


def _story(day: date) -> Story:
    observed = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return Story(
        id="story-frozen",
        canonical_title="Frozen input story",
        category="ai_and_open_source",
        published_at=observed,
        updated_at=observed,
        source_item_ids=["raw-frozen"],
        source_urls=["https://example.com/frozen"],
        primary_source_url="https://example.com/frozen",
        facts=["同一冻结输入用于两个模型。"],
        analysis=["该变化影响开发流程。"],
        relevance_score=0.9,
        importance_score=0.8,
        novelty_score=0.7,
        credibility_score=0.8,
    )


def test_seven_paired_days_stop_and_write_multidimensional_report(tmp_path) -> None:
    start = date(2026, 8, 1)
    provider = FakeAIProvider()
    hashes: list[str] = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        write_json(
            tmp_path / "data/stories" / f"{day}.json",
            [_story(day).model_dump(mode="json")],
        )
        artifact = run_model_ab_experiment(
            tmp_path,
            production=provider,
            challenger=provider,
            current_date=day,
        )
        hashes.append(artifact["input_bundle_hash"])
        assert artifact["versions"]["A"]["brief"] == artifact["versions"]["B"]["brief"]

    report = read_json(tmp_path / "data/evaluations/model_ab/report.json")
    assert report["stop_reason"] == "seven_successful_paired_days"
    assert report["successful_paired_days"] == 7
    assert report["recommendation"].startswith("manual review")
    assert len(hashes) == 7
    assert not (tmp_path / "data/briefs").exists()
    assert not (tmp_path / "data/editorial").exists()

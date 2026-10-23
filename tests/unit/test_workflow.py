from pathlib import Path

import yaml


def workflow(name):
    return yaml.load(
        Path(".github/workflows", name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )


def test_single_release_owner_and_recovery_graph():
    daily = workflow("daily-brief.yml")
    assert daily["on"]["schedule"] == [{"cron": "37 3 * * *", "timezone": "Asia/Singapore"}]
    assert daily["concurrency"] == {
        "group": "morning-radar-generated-data",
        "cancel-in-progress": "false",
        "queue": "max",
    }
    job = daily["jobs"]["release"]
    assert job["permissions"] == {
        "contents": "write",
        "issues": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert "github.ref == 'refs/heads/master'" in job["if"]
    steps = job["steps"]
    commands = [s["run"] for s in steps if "run" in s]
    joined = "\n".join(commands)
    assert joined.index("release generate") < joined.index("release export")
    assert joined.index("release export") < joined.index('release "$action"')
    assert joined.index('release "$action"') < joined.index("release notify")
    assert "record-deploy" not in joined and "notify-latest" not in joined
    assert "run-tendency" not in joined
    for step in steps:
        if "run" in step:
            assert "github.event.issue" not in step["run"]
    preview = daily["jobs"]["preview"]
    assert "inputs.fixtures || inputs.dry_run" in preview["if"]
    assert "secrets." not in str(preview)
    assert "workflow_run" not in daily["on"]


def test_ingest_and_post_daily_have_no_model_or_delivery_authority():
    ingest = workflow("tendency-ingest.yml")
    daily = workflow("daily-brief.yml")
    assert ingest["concurrency"] == daily["concurrency"]
    assert ingest["permissions"] == {"contents": "write", "issues": "read"}
    assert set(ingest["on"]["issues"]["types"]) == {"opened", "edited", "reopened", "closed"}
    assert "secrets." not in str(ingest)
    post = workflow("post-daily.yml")
    assert set(post["on"]) == {"workflow_dispatch"}
    assert post["permissions"] == {"contents": "read"}
    assert "release status" in str(post)
    assert "deploy" not in str(post) and "secrets." not in str(post)


def test_manual_preview_includes_v035_intelligence_artifacts() -> None:
    workflow = Path(".github/workflows/manual-preview.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "timeout-minutes: 45" in workflow
    assert "--dry-run" in workflow
    assert "--skip-notify" in workflow
    assert ".tmp/dry-run/data/radar_signals/" in workflow
    assert ".tmp/dry-run/data/tendencies/" in workflow
    assert "retention-days: 3" in workflow


def test_editorial_eval_is_manual_isolated_and_read_only() -> None:
    workflow = Path(".github/workflows/editorial-eval.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "\n  push:" not in workflow
    assert "contents: read" in workflow
    assert "python -m morning_radar.editorial.evaluation" in workflow
    assert "python -m morning_radar run" not in workflow
    assert "notify-latest" not in workflow
    assert "actions/deploy-pages" not in workflow
    assert "git push" not in workflow
    assert "shadow_mode" not in workflow


def test_editorial_eval_uses_safe_configuration_and_uploads_evidence() -> None:
    workflow = Path(".github/workflows/editorial-eval.yml").read_text(encoding="utf-8")

    assert "secrets.DEEPSEEK_API_KEY" in workflow
    assert "vars.DEEPSEEK_MODEL || secrets.DEEPSEEK_MODEL" in workflow
    assert "vars.DEEPSEEK_BASE_URL || secrets.DEEPSEEK_BASE_URL" in workflow
    assert "raw_model_output.json" in workflow
    assert "validated_results.json" in workflow
    assert "metrics.json" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "if: ${{ always() }}" in workflow
    assert "enforce quality Gate" in workflow


def test_pr_ci_is_read_only_and_offline() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "contents: read" in workflow
    assert 'python -m pip install -e ".[dev]"' in workflow
    assert "python -m pytest" in workflow
    assert "python -m ruff check src tests" in workflow
    assert "secrets." not in workflow
    assert "deploy-pages" not in workflow
    assert "git push" not in workflow


def test_daily_run_name_distinguishes_preview_from_production() -> None:
    workflow = Path(".github/workflows/daily-brief.yml").read_text(encoding="utf-8")
    assert "run-name:" in workflow
    assert "dry-run" in workflow
    assert "fixtures" in workflow
    assert "production" in workflow

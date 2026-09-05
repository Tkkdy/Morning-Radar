from pathlib import Path


def test_workflow_has_manual_schedule_pages_and_safety_controls() -> None:
    workflow = Path(".github/workflows/daily-brief.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert 'cron: "37 23 * * *"' in workflow
    assert "python-version: \"3.12\"" in workflow
    assert "timeout-minutes: 45" in workflow
    assert "concurrency:" in workflow
    assert "actions/deploy-pages@v4" in workflow
    assert "secrets.DEEPSEEK_API_KEY" in workflow
    assert "secrets.DEEPSEEK_MODEL" in workflow
    assert "secrets.DEEPSEEK_BASE_URL" in workflow
    assert "secrets.OPENAI_API_KEY" not in workflow
    assert "github.token" in workflow
    assert "git diff --cached --quiet" in workflow


def test_notification_runs_only_after_pages_deploy_without_push_loop() -> None:
    workflow = Path(".github/workflows/daily-brief.yml").read_text(encoding="utf-8")

    deploy_index = workflow.index("- name: Deploy GitHub Pages")
    notify_index = workflow.index("- name: Notify after successful Pages deployment")
    state_index = workflow.index("- name: Commit notification state")

    assert "--skip-notify" in workflow
    assert deploy_index < notify_index < state_index
    assert "python -m morning_radar notify-latest" in workflow
    assert "!(inputs.dry_run || false) && !(inputs.fixtures || false)" in workflow
    assert "\n  push:" not in workflow


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


def test_post_daily_only_follows_successful_daily_on_master() -> None:
    workflow = Path(".github/workflows/post-daily.yml").read_text(encoding="utf-8")

    assert "workflow_run:" in workflow
    assert 'workflows: ["Daily Morning Radar"]' in workflow
    assert "types: [completed]" in workflow
    assert "branches: [master]" in workflow
    assert "workflow_run.conclusion == 'success'" in workflow
    assert "schedule:" not in workflow


def test_post_daily_orders_isolated_intelligence_and_redeploys_without_notification() -> None:
    workflow = Path(".github/workflows/post-daily.yml").read_text(encoding="utf-8")

    tendency = workflow.index("python -m morning_radar run-tendency")
    deep = workflow.index("python -m morning_radar run-deep-continuity")
    model_ab = workflow.index("python -m morning_radar run-model-ab")
    build = workflow.index("python -m morning_radar build-site")
    commit = workflow.index("git add data/tendencies data/continuity")
    deploy = workflow.index("actions/deploy-pages@v4")
    assert tendency < deep < model_ab < build < commit < deploy
    assert workflow.count("continue-on-error: true") == 3
    assert workflow.count("git push") == 1
    assert "notify-latest" not in workflow
    assert "WXPUSHER" not in workflow


def test_post_daily_is_the_only_optional_generated_data_writer() -> None:
    workflows = Path(".github/workflows")

    assert not (workflows / "tendency.yml").exists()
    assert not (workflows / "deep-continuity.yml").exists()
    assert not (workflows / "model-ab.yml").exists()
    assert "morning-radar-generated-data" in (
        workflows / "post-daily.yml"
    ).read_text(encoding="utf-8")


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

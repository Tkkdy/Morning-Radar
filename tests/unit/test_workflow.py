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

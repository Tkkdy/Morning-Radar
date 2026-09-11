---
protocol_version: 1
task_id: MR-PROD-HOTFIX-20260911
title: Brief budget degradation and continuity nullable narrative hotfix
status: READY_FOR_REVIEW
current_actor: EXECUTOR
next_actor: PLANNER_REVIEWER
repository: Tkkdy/Morning-Radar
base_branch: master
work_branch: codex/prod-hotfix-20260911
pr: unavailable
updated_at: 2026-09-11T06:00:00Z
---

# AI Development Handoff

## Current Goal
Apply the bounded production hotfix locally: preserve a valid Brief when optional direction observation exhausts AI budget, and skip language validation for schema-legal empty continuity narratives.

## Repository Reality / Deviations
- Independent worktree: `C:/Users/PsyDuck/Documents/Morning Radar prod-hotfix-20260911`.
- Remote master was read-only verified and fetched at `b27c68aebab9a12d92276d261663ac299efcc042`; this worktree and branch start exactly there.
- The original P2 worktree remains on `codex/coverage-p2-b` at `94bc895` with its dirty changes untouched.
- Python imports were checked with temporary `PYTHONPATH` and resolve to this hotfix worktree's `src`.

## Last Action
Completed the bounded H01/H02 repair and offline incident-chain regression coverage. No commit, push, PR, merge, deployment, notification, real network collection, or production model call.

## Validation Summary
- Focused hotfix tests: 8 passed (HF01-HF08); fixture process persistence: 1 passed (HF09).
- Related generator, continuity, provider, output-validation, and hotfix tests: 156 passed.
- Full independently collected suite: 525 tests, executed as disjoint groups of 375 and 150; both completed successfully.
- `python -m ruff check --no-cache src tests` and `git diff --check`: exit 0.

## Execution Report
- H01: `generate_daily_brief_with_memory` now catches only `AIBudgetExceeded` at the optional direction-observation boundary. It retains already validated/recovered body items, omits the direction field, sets the compatible `ai_direction_fallback`, and records the actual exception message in `ai_direction_fallback_reason`. Existing `AIOutputError` degradation remains unchanged.
- H02: `_user_visible_narratives` now passes optional relation and watch-match rationale through `_present`, so Pydantic-legal `None` fields never enter regex language validation. Required fields and non-empty narrative validation are unchanged.
- HF coverage: formal unit tests cover input-character/call/network budget reasons, no-signal/disabled omission, recovery-then-budget fallback, output-error compatibility, nullable Chinese/English/schema behavior, and a single-call DeepSeek mock schema/language parse. The integration test invokes the fixture process boundary, reloads Brief/generation/ledger artifacts, and verifies source URLs remain input-derived.
- Scope: only `src/morning_radar/briefing/generator.py`, `src/morning_radar/ai/output_validation.py`, new formal unit/integration tests, and this dedicated handoff changed. No config, budget ceiling, model, workflow, P2 code, or production data changes.

## Unresolved / Recovery Readiness
- Local mandatory work is complete. NOT_RUN: real GitHub Actions, Pages, notification, production model, and production recovery.
- After review and separately authorized integration, first reconfirm the actual master SHA; then inspect existing batch `batch-20260911T012654Z-6ca39acc65` before any recovery. A recovery may use `process --batch-id <batch>` only with explicit production/AI/write authorization and a confirmed checkout SHA. Current CLI has no dedicated `process --date`; do not substitute `--now` as a backfill-date interface.

## Next Action
Reviewer inspect the isolated hotfix diff and HF01-HF09 evidence. READY_FOR_REVIEW is not a merge, production recovery, or deployment result.

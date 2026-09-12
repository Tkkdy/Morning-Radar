---
protocol_version: 1
task_id: MR-COVERAGE-P2-B
title: Morning Radar P2-B lab discovery coverage and protected candidates
status: READY_FOR_REVIEW
current_actor: EXECUTOR
next_actor: PLANNER_REVIEWER
repository: Tkkdy/Morning-Radar
base_branch: master
work_branch: codex/coverage-p2-b
pr: unavailable
updated_at: 2026-09-11T04:00:00Z
---

# AI Development Handoff

## Current Goal
Implement P2-B locally: a bounded DeepSeek official-update collector, targeted HN lab discovery, deterministic fresh-candidate protection, and durable diagnostics without changing AI budgets or production delivery flow.

## Repository Reality / Deviations
- Created local branch `codex/coverage-p2-b` from `94bc895`, carrying the uncommitted, reviewer-passed P2-A/PATCH-01/PATCH-02 and network-budget follow-up. P2-B changes will remain distinguishable by path and report; no prior work was committed, reset, stashed, or overwritten.
- Remote checked read-only: `origin/master` is `5316837`; cached local `origin/master` was older. No fetch, rebase, merge, push, PR, deploy, or notification.
- Existing integration points: `settings.py`, `collectors/*`, `intake/service.py`, `intake/candidates.py`, checkpoint/ledger/inspect, and CI. `maximum_ai_network_requests=60`, HN top/new/best max candidates=30, and missing publication times currently fall back to fetched time in the news-window filter.
- Baseline collection: `python -m pytest --collect-only -q -p no:cacheprovider` exit 0, 554 tests listed. Full baseline execution remains pending.
- Unrelated `加课申请邮件.md` and prior `.tmp*` directories are preserved and excluded.
- Minor deviation: date-only official updates require a narrow explicit metadata-aware window path because existing missing-time handling treats `fetched_at` as publication time. This preserves legacy source behavior.

## Last Action
Implemented the initial P2-B configuration, fixed-endpoint collectors, date-only eligibility, checkpoint discovery audit, collection inspect command, and candidate diagnostics. No commit, push, PR, deploy, paid model, or notification.

## Validation Summary
- `git ls-remote --heads origin master` — exit 0; remote master `5316837`.
- Two disjoint pytest groups covering all 554 collected tests — both exit 0 (tail group: 215 passed; head group: remaining 339 passed).
- `.venv\\Scripts\\python.exe -B -m ruff check --no-cache src tests` — exit 0.
- `git diff --check` — exit 0.
- Anonymous read-only smoke: DeepSeek updates HTTP 200 / 41947 bytes; HN Algolia fixed endpoint HTTP 200 / 24009 bytes. No response persisted.
- Not run: paid models, production Actions, Pages, WxPusher, push/PR/deploy.

## Next Action
Reviewer inspect P2-B incremental files, especially the fixed endpoint boundaries, date-only eligibility, discovery audit, and candidate diagnostics. Confirm remaining taskbook acceptance gaps before any integration decision.

## Review Focus / Open Decisions
- P2-B must preserve inherited P2-A changes and not treat old P2-A handoff status as a reopening of that reviewed work.
- New discovery HTTP budget is separate from AI budget; date-only official entries must not manufacture a timestamp.

## PATCH-02 Execution Report
- Status: READY_FOR_REVIEW. R01-R06 are implemented locally without changing AI budgets, workflow, production data, or delivery behavior.
- Identity/provenance: `official_changelog` keeps only its observed anchor as the narrow dedup comparison key; ordinary URL normalization is unchanged. Same-ID equivalent HN observations merge before content versioning and preserve all discovery paths/reasons; substantive non-empty text changes remain distinct versions.
- Request boundary: discovery request deadline starts on its first physical request; the discovery client disables redirect following, keeps retries inside the physical cap, and records budget/deadline/page-limit/invalid-response reasons in `discovery_audit`.
- Candidate and dates: validated YAML update rules with stable IDs drive protected fresh candidates; diagnostics store lab/rule/hash/cap/selection. Official source-date precision and `official_page_fetched` survive research evidence and Story source references while `published_at` remains null.
- Validation: reviewer-owned offline acceptance copied into fresh local temp directories: 23 passed, exit 0. Current project suite split into disjoint groups: 327 passed and 215 passed, both exit 0. Ruff and `git diff --check` exit 0.
- Not run: corrected-parser live fixed-endpoint smoke, paid models, production Actions, Pages, WxPusher, push, PR, merge, deploy, notification. Local acceptance is not production verification.

## PATCH-03 In Progress
- Review decision: PATCH_REQUIRED. The prior PATCH-02 review found that same-discussion HN observations could lose title or target-link revisions, and that official same-title sections could be discarded by title fallback despite distinct anchors.
- Scope: local R01/R02 repair and offline regression tests only. YAML configuration, production requests, GitHub Actions, Pages, notification, commits, push, PR, merge, and deployment remain out of scope.

## PATCH-03 Execution Report
- Status: READY_FOR_REVIEW. R01/R02 were repaired locally without changing YAML configuration, AI budgets, production delivery flow, or recovery architecture.
- HN: observations sharing a discussion ID are compared against every retained version. Cleaned-title changes, two different observed external targets, and two different non-empty bodies create separate versions; missing fields are complementary. Equivalent V2 observations merge into V2 and union `discovery_paths` / `discovery_reasons`.
- Official sections: only `official_changelog` items with both a source ID and real URL anchor preserve that anchor as identity and bypass same-source title fallback. Repeated same-anchor sections still deduplicate; ordinary fragment-only URLs retain normal normalization; the official/practitioner evidence pair remains preserved.
- Formal mapping: `tests/unit/test_p2b_patch03.py` maps R01 title revision, target revision/checkpoint reload, equivalent/missing field merge, V1/V2/V2 grouping, official anchors, normal URL fallback, and official/practitioner pairing to formal node IDs. It also now contains MockTransport-only coverage for physical request cap, first-request deadline, redirect refusal, paging order with retained prior hits and failure diagnostics, response byte cap, protected-slot noise exclusion, and persisted date/discovery diagnostics.
- Validation: PATCH-03 focused 10 passed; relevant 45 passed. The current CI command paths were checked in `.github/workflows/ci.yml`; repository collection is 568 tests. Complete suite was executed in disjoint groups, 353 passed plus 215 passed (568 total), all exit 0. Ruff and `git diff --check` exit 0.
- Not run: corrected live smoke, paid models, current real network, GitHub Actions, Pages, WxPusher, push, PR, merge, deployment, or notification. Local verification is not production verification.

## PATCH-03 R03 Addendum
- Review decision addressed: PATCH_REQUIRED, limited to missing formal assertions; no production-code, YAML, budget, workflow, or delivery-flow changes were needed.
- Paging: the formal HN test now verifies two queries are scheduled primary-page-first, preserves both valid first-page hits when the second query has invalid `nbPages`, and records both invalid-pagination and later failed-page diagnostics.
- Byte limit: the formal test uses a raw JSON response padded beyond the byte cap while its parsed/reserialized JSON would be smaller, proving the collector applies the cap to received bytes before parsing.
- Date and diagnostics: a MockTransport DeepSeek collector now produces the input and its audit; the test proves source date/precision reach the research request payload, Story source reference, filtered checkpoint payload, and checkpoint `discovery_audit` after disk reload.
- Validation: focused PATCH-03 10 passed; expanded related regression 82 passed; full suite completed as disjoint groups, 353 passed plus 215 passed (568 total), all exit 0. Ruff and `git diff --check` exit 0.

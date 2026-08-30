# B0.5 Generic Holdout Harness and Corrected Selection Report

Status: `HARNESS_WORKING — PRODUCTION_HOLDOUT_BLOCKED`

## Environment

- Branch: `codex/b0.5-candidate-evidence-pipeline`
- Starting HEAD: `d0b4a130c5e84937694a7fc2722d3e13ba9952c1`
- Evaluation modes: `regression`, `holdout`
- Offline preflight provider: `FakeAIProvider`
- External Provider calls during this task: `0`
- Real Provider result: `NOT_EVALUATED`
- Live collectors, Evidence HTTP, notifications, and production writes: `DISABLED`
- Whole-run attempts per selected date: `1`

## Harness split

`regression` mode remains specific to 2026-08-22. It requires Raw item
`item-4d7b9f9d11a89fb3b930` and retains the DeepSeek Vision admission and Semantic
Triage regression assertion.

`holdout` mode accepts an explicit frozen historical date, does not require or assess the
08-22 golden item, and still calls `MorningRadarPipeline.run` with frozen Raw input,
historical `now`, an isolated output root, a fail-closed Evidence fetcher, and notifications
disabled. Only the evaluator's external dependencies are substituted; the semantic pipeline
is not reimplemented.

## Deterministic workload contract

Selection order is:

1. `admitted_count`
2. `eligible_count`
3. `recent_count`
4. date ascending

`raw_count` is diagnostic only. Story count, Brief quality, semantic disposition, and Fake or
real Provider output do not participate. Normal uses the lower median for an even pool and the
nearest lower-index unused date if the median collides with Heavy or Sparse. Sparse requires
`admitted_count > 0`.

## Candidate workload table

| Date | Raw | Recent | Eligible | Admitted | Workload tuple | Excluded | Reason |
|---|---:|---:|---:|---:|---|---|---|
| 2026-07-24 | 200 | 0 | 0 | 0 | `(0,0,0)` | no | — |
| 2026-07-25 | 200 | 0 | 0 | 0 | `(0,0,0)` | no | — |
| 2026-07-26 | 200 | 0 | 0 | 0 | `(0,0,0)` | no | — |
| 2026-07-27 | 2 | 2 | 2 | 2 | `(2,2,2)` | no | — |
| 2026-08-09 | 11 | 11 | 2 | 2 | `(2,2,11)` | no | — |
| 2026-07-23 | 4 | 4 | 4 | 3 | `(3,4,4)` | yes | B0.5 fixture integration development date |
| 2026-08-03 | 10 | 9 | 5 | 5 | `(5,5,9)` | no | — |
| 2026-08-05 | 16 | 13 | 5 | 5 | `(5,5,13)` | no | — |
| 2026-08-08 | 15 | 14 | 5 | 5 | `(5,5,14)` | no | — |
| 2026-07-28 | 15 | 6 | 6 | 6 | `(6,6,6)` | no | — |
| 2026-08-06 | 14 | 14 | 8 | 8 | `(8,8,14)` | no | — |
| 2026-07-29 | 17 | 16 | 8 | 8 | `(8,8,16)` | no | — |
| 2026-08-07 | 18 | 17 | 8 | 8 | `(8,8,17)` | no | — |
| 2026-08-02 | 15 | 14 | 10 | 10 | `(10,10,14)` | no | — |
| 2026-07-30 | 19 | 16 | 10 | 10 | `(10,10,16)` | no | — |
| 2026-08-01 | 17 | 16 | 12 | 12 | `(12,12,16)` | no | — |
| 2026-08-10 | 25 | 21 | 12 | 12 | `(12,12,21)` | no | — |
| 2026-07-31 | 17 | 16 | 13 | 13 | `(13,13,16)` | no | — |
| 2026-08-04 | 18 | 17 | 13 | 13 | `(13,13,17)` | no | — |
| 2026-08-16 | 28 | 23 | 15 | 15 | `(15,15,23)` | no | — |
| 2026-08-23 | 30 | 24 | 16 | 16 | `(16,16,24)` | no | — |
| 2026-08-17 | 35 | 27 | 19 | 18 | `(18,19,27)` | no | — |
| 2026-08-24 | 33 | 28 | 20 | 19 | `(19,20,28)` | no | — |
| 2026-08-18 | 41 | 35 | 28 | 28 | `(28,28,35)` | no | — |
| 2026-08-15 | 58 | 44 | 36 | 35 | `(35,36,44)` | no | — |
| 2026-08-25 | 51 | 43 | 36 | 36 | `(36,36,43)` | no | — |
| 2026-08-11 | 52 | 45 | 36 | 36 | `(36,36,45)` | no | — |
| 2026-08-22 | 57 | 46 | 38 | 38 | `(38,38,46)` | yes | B0.5 DeepSeek Vision development/regression date |
| 2026-08-13 | 61 | 46 | 39 | 39 | `(39,39,46)` | no | — |
| 2026-08-21 | 67 | 50 | 41 | 40 | `(40,41,50)` | no | — |
| 2026-08-14 | 55 | 49 | 41 | 41 | `(41,41,49)` | no | — |
| 2026-08-19 | 57 | 49 | 43 | 43 | `(43,43,49)` | no | — |
| 2026-08-27 | 66 | 59 | 50 | 49 | `(49,50,59)` | no | — |
| 2026-08-12 | 61 | 59 | 51 | 50 | `(50,51,59)` | no | — |
| 2026-08-20 | 75 | 62 | 55 | 55 | `(55,55,62)` | no | — |

## Corrected frozen holdout set

| Role | Date | Raw | Recent | Eligible | Admitted | Workload |
|---|---|---:|---:|---:|---:|---|
| Heavy | `2026-08-20` | 75 | 62 | 55 | 55 | `(55,55,62)` |
| Normal | `2026-07-31` | 17 | 16 | 13 | 13 | `(13,13,16)` |
| Sparse | `2026-07-27` | 2 | 2 | 2 | 2 | `(2,2,2)` |

These dates become the selected diagnostic holdout set when this report is recorded. If the
2026-08-20 Heavy finding is used to modify B0.5 production semantics, the complete current
Heavy/Normal/Sparse trio becomes development-contaminated for final generalization proof. A
new fresh trio must then be selected; this checkpoint does not reselect or run one.

## Offline preflight

| Role | Date | Pipeline completed | Fake triaged | External requests | Readiness |
|---|---|---|---:|---:|---|
| Heavy | 2026-08-20 | yes | 0/55 | 0 | `NOT_READY_FOR_REAL_HOLDOUT — PRODUCTION_BATCH_BUDGET_FAILURE` |
| Normal | 2026-07-31 | yes | 13/13 | 0 | `READY_FOR_HOLDOUT_REAL_EVAL` |
| Sparse | 2026-07-27 | yes | 2/2 | 0 | `READY_FOR_HOLDOUT_REAL_EVAL` |

The Heavy harness is working and `MorningRadarPipeline.run` completed. The frozen production
path admitted 55 Candidates, including 20 `MUST_TRIAGE`, then formed a first batch of 39 items.
Its 79,590-character Provider payload exceeded the 76,000 characters available to triage after
protected downstream reservations. Shared production `AIBudget` rejected the batch before any
Provider invocation, and production Candidate orchestration deferred the attempted batch plus
all remaining Candidates: 0/55 triaged and 55/55 `DEFERRED_BY_BUDGET`, including all 20
`MUST_TRIAGE`. This is classified as `PRODUCTION_BATCH_BUDGET_FAILURE`, not an evaluation
harness failure. It is not used to replace the selected date, and this evaluation-infrastructure
checkpoint does not alter Budget values, batch sizing, or production behavior. Fake dispositions
and Story results are not semantic quality evidence.

## Evidence classification

### DETERMINISTIC PROOF

- Historical Raw/Brief parsing and frozen historical `now`
- freshness, routine-market filtering, Candidate Admission, and workload counts
- deterministic Heavy/Normal/Sparse selection
- mode and safety-boundary tests
- each offline preflight reaching `MorningRadarPipeline.run` exactly once
- Heavy capacity-cliff diagnosis: 79,590 requested triage characters exceeded the protected
  76,000-character envelope before Provider invocation

### RECORDED REPLAY

- Historical Brief `generated_at` values are used only to freeze `now`.
- No recorded semantic output determines the selection.

### REAL PROVIDER RESULT

`NONE / NOT_EVALUATED`

### FAKE / OFFLINE RESULT

- Proves harness structure, frozen execution, deterministic routing into production orchestration,
  and pre-Provider failure behavior.
- Does not prove real semantic quality.

### NOT_EVALUATED / NOT_REPLAYABLE

- Real semantic quality, recall, precision, BUILD/INVESTIGATE/DROP quality, and Story quality
- Real Provider behavior for Heavy, Normal, and Sparse

## Anti-overfitting rule

Only `MAJOR_RECALL_FAILURE`, `EVIDENCE_INTEGRITY_FAILURE`, or
`SYSTEMIC_PIPELINE_FAILURE` may reopen B0.5 after a real holdout. All ordinary wording,
ordering, alias, pronoun, Candidate priority, or non-critical Story-boundary differences go to
the B0.6 backlog.

## Validation

- Focused: `python -m pytest tests/unit/test_semantic_evaluation.py tests/unit/test_holdout_evaluation.py` — `14 passed in 4.37s`
- Full: `python -m pytest` — `544 passed in 12.77s`
- Ruff: `python -m ruff check .` — `All checks passed!`
- Diff check: `git diff --check` — passed (line-ending warnings only)

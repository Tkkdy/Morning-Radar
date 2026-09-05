# AI Pipeline Reliability & Intelligence V2

## Architecture

The daily workflow keeps the modular monolith. After Stories are frozen, Brief generation and
Fast Continuity run in a local two-branch join. The Brief branch is core. Fast Continuity has a
configured 60-second join limit and falls back to its deterministic relation backbone.

Tendency and Deep Continuity are standalone CLI/GitHub Actions workflows. All workflows that
write generated data share the `morning-radar-generated-data` Actions concurrency group.

## Failure semantics

Provider SDK failures are translated into project errors. HTTP 402/insufficient balance opens a
provider-instance circuit, so later calls in that workflow do not reach the network. Production
auth/configuration errors remain explicit deployment failures. Editorial, Continuity, Tendency,
and challenger failures degrade only their own optional lane. Brief generation retains the
verified-fact fallback.

RSS HTTP 304 is returned as an unchanged, zero-item success before `raise_for_status()`.

## Budget guardrails

The global main input-character cap remains 120,000. `AIBudget` separately tracks logical tasks,
network requests, and actual provider usage. The main network cap is 60; every task is capped at
three attempts or fewer. Experimental/optional tasks stop after the first network attempt.
Research and Editorial use medium reasoning and a 4K output cap. Editorial never expands 16K to
24K. Tendency has its own 24K input, three-call, four-request envelope.

## Continuity V2

Relation, Watch, and Direct Judgement Revision are independent, non-empty-only lanes. Deterministic
relations run even when no AI budget exists. Negative Relation/Watch outputs are sparse and never
become production history; missing output remains unresolved, not rejected.

New Judgements must be falsifiable, alter future interpretation, outlive the day, describe the
loss if forgotten for 30 days, and merit proactive correction if false. V2 never creates a new
`SUPPORTED` record, while the historical enum and reducer remain unchanged.

Deep Review scans dependency changes, explicit counterevidence, and first crossing of a calibrated
multi-date/multi-source evidence threshold. No trigger means zero AI calls.

## Tendency workflow

`python -m morning_radar run-tendency` reads persisted Stories, Continuity, and Tendency history,
evaluates with an isolated provider budget, and persists only on a non-failed run. The main Brief
does not call Tendency AI and does not own production Tendency state. Site building joins the latest
persisted Tendency view without rewriting historical Brief JSON.

## A/B experiment

`python -m morning_radar run-model-ab` loads one persisted Story/Signal bundle, hashes it, and sends
that identical frozen input to the production and challenger lanes. It writes only
`data/evaluations/model_ab/` and `/evaluation/model-ab/`; it cannot mutate Continuity, Tendency,
production Briefs, or notifications. Labels vary deterministically by date and are hidden until
the local user chooses Reveal. Votes stay in localStorage.

The experiment stops after seven successful paired days, or after ten calendar days with fewer
than five valid pairs. It records multidimensional reliability, validation, latency, token and
reference metrics; it does not compute a synthetic overall score or switch a model.

Required challenger deployment configuration: `QWEN_API_KEY`, `QWEN_BASE_URL`, `QWEN_MODEL`.
Production remains explicitly configured by `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and
`DEEPSEEK_MODEL`; the code does not modify secrets.

## Rollout locks

- Editorial remains Shadow.
- No A/B winner is selected automatically.
- The 120K character cap is unchanged.
- Historical Continuity/Tendency data is not migrated or rewritten.
- No Decision Corpus or external research repository is created.

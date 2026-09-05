# AI Pipeline Reliability & Intelligence V2

## Architecture

The daily workflow keeps the modular monolith. After Stories are frozen, Brief generation and
Fast Continuity run in a local two-branch join. The Brief branch is core. Fast Continuity has a
configured 60-second deadline measured from task start. Every AI lane checks the remaining time,
and provider requests receive no timeout longer than that remainder. Publication falls back to the
deterministic relation backbone at the deadline; no later lane may begin.

`Daily Morning Radar` is the only scheduled entry. On successful completion on `master`,
`Post-Daily Intelligence` runs Tendency, Deep Continuity, and the configured model A/B in order
from one checkout, rebuilds the site, commits generated intelligence once, and redeploys Pages.
Optional intelligence failures do not prevent rebuilding/deploying the last valid site. Post-Daily
never sends a WxPusher notification.

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
three attempts or fewer. Experimental/optional tasks stop after the first network attempt. Flash
production tuning uses Low reasoning with a 6K output cap for Research and an 8K retry only after
structured truncation. Editorial uses no thinking with a 6K cap. Brief keeps High reasoning and
8K on its first attempt, then uses Medium reasoning and 8K only after structured truncation.
Transport retries retain the current structured attempt's reasoning and token policy. Tendency has
its own 24K input, three-call, four-request envelope.

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
that identical frozen input to a separately configured DeepSeek Flash lane and a real Qwen lane. It writes only
`data/evaluations/model_ab/` and `/evaluation/model-ab/`; it cannot mutate Continuity, Tendency,
production Briefs, or notifications. Labels vary deterministically by date and are hidden until
the local user chooses Reveal. Votes stay in localStorage.

The experiment starts only once both configured lanes are actually attempted. It stops after seven
successful paired days, or after ten experiment calendar days with fewer than five valid pairs.
Missing Qwen configuration returns `NOT_CONFIGURED`, exits successfully, and writes no dated
artifact, so those days do not count. It records multidimensional reliability, validation, latency, token and
reference metrics; it does not compute a synthetic overall score or switch a model.

Required challenger deployment configuration: `QWEN_API_KEY`, `QWEN_BASE_URL`, `QWEN_MODEL`.
The DeepSeek experiment lane uses `MODEL_AB_DEEPSEEK_MODEL` (default `deepseek-v4-flash`) with the
existing DeepSeek key and base URL. Daily production remains explicitly configured only by
`DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL`; the code does not modify secrets.

## Rollout locks

- Production remains DeepSeek V4 Flash. Dry-run previews exclude persisted current-day Continuity
  while retaining older production history. Confirmed Relation evidence is persisted against both
  whole Story occurrences; Judgement evidence keeps valid fact-level references. Empty or blank
  merged Story titles are rejected and retried at the AI schema boundary.
- Editorial remains Shadow.
- No A/B winner is selected automatically.
- The 120K character cap is unchanged.
- Historical Continuity/Tendency data is not migrated or rewritten.
- No Decision Corpus or external research repository is created.

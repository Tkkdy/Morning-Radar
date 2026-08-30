# B0.5 / B0.6 Backlog

Status: `B0.5_CORE_NOT_INDEPENDENTLY_RELEASABLE`

This is not a speculative roadmap. It records only work that already has
repository evidence, after Semantic Router research was closed.

## Closed for B0.5

`ROUTER_RESEARCH_CLOSED_FOR_B0_5`

Rejected as production migration:

- Direct LLM routing (`DROP | BUILD | INVESTIGATE` as the model-owned route)
- Atomic Router V1
- Atomic Router V2
- Minimal Atomic Flash
- Minimal Atomic Pro / stronger-model adequacy

Evidence:

- Direct routing is the current production path, not a later experiment.
  `MorningRadarPipeline.run` admits Candidates, then calls
  `triage_candidates()` with `prompts/candidate_triage.md` and
  `CandidateTriageBatch.semantic_disposition`. Story Construction only
  accepts `SemanticDisposition.BUILD`.
- Atomic V1 was structurally useful, but capability ownership was misplaced.
- Atomic V2 failed on dual-reference conflation
  (`INVALID_DIRECT_SUPPORT_BINDING` / destination vs relation Evidence).
- Minimal Atomic was stable and Evidence-safe, but failed INVESTIGATE utility
  and/or frozen release gates.
- Stronger model (`deepseek-v4-pro`) failed the frozen release gate:
  `REJECT_STRONGER_MODEL_ADEQUACY`.

Do not resume by default. A future Router needs a new architecture hypothesis
and a new evaluation plan.

## B0.6 — Candidate Semantic Router redesign

The B0.5 structural core cannot ship independently of a Semantic Router.

Hard dependency:

`origin/master` Story formation is `preselect_ai_candidates` +
`classify_items` + `merge_story`. B0.5 deleted
`src/morning_radar/research/engine.py` and replaced production Story
formation with Candidate triage. There is no remaining production baseline
that can run Admission, Evidence eligibility, Story Boundary, Capacity Cliff,
and Decision Trace without some LLM-owned or redesigned route into BUILD.

Required later:

- model owns meaning, not executable route
- system owns Evidence eligibility, destination selection, bounded-fetch
  capability, Budget, and final route
- INVESTIGATE must be useful without collapsing into UNRESOLVED
- BUILD must remain Evidence-bound

## Deferred items already evidenced

- Evidence Resolution Level 3 Targeted Official Lookup is not implemented.
  Current production stops at Level 1/2 existing-destination fetch and
  deterministic Official Surface verification.
- Atomic / Minimal Atomic evaluation code remains evaluation-only. It is not
  a production default, and it is not a B0.5 release candidate.

## Explicitly not resumed

Do not reopen for B0.5:

- another prompt, schema, temperature, or batch-size Router test
- another model adequacy experiment
- Story Boundary / availability / anaphora / scheduler optimization
- packing-efficiency tuning beyond the accepted Capacity Cliff fix

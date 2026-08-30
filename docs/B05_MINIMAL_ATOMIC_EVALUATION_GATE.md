# B0.5 Minimal Atomic Evaluation Gate

Status: `READY_FOR_REAL_SEMANTIC_EVAL`

This document freezes an evaluation-only prototype and the next proposed-only real
semantic experiment. It does not authorize a Provider call or production routing
migration.

## V2 Trial 3 root-cause audit

The recorded V2 Trial 3 artifact contains 18
`INVALID_DIRECT_SUPPORT_BINDING` occurrences. All 18 raw assessments selected
`DIRECT_SUPPORT`, all 18 left `relation_evidence_ids` empty, and all 18 populated
`verification_source_evidence_id`.

- 17/18 selected the unique eligible, excerpt-bearing claim Evidence in the
  verification-destination field instead of the relation-binding field.
- 1/18 (`candidate-0e7ded1847d561ca6738`) selected an official destination whose
  excerpt was empty. That destination was not eligible claim-bearing Evidence;
  source identity was mistaken for support.
- No other material category appeared in these 18 occurrences.

The audited Candidates were:

| Candidate | Hypothesis | Classification |
| --- | --- | --- |
| `candidate-006b36b9d1483b09aa8c` | Shared agentic work with GitHub Copilot in Microsoft Teams | eligible binding displaced into destination field |
| `candidate-0e7ded1847d561ca6738` | Measuring benchmark optimization in speech recognition | empty-excerpt source identity used as support |
| `candidate-22d91715672f19308caa` | Maximizing AI Factory Performance per Watt with NVIDIA DSX MaxLPS | eligible binding displaced into destination field |
| `candidate-5b7e60188786bff1aa89` | GPU-Accelerated Clustering for Financial Instruments at Scale | eligible binding displaced into destination field |
| `candidate-628202a98a8bc40296ef` | ollama/ollama: v0.33.0 | eligible binding displaced into destination field |
| `candidate-7f0f2d88470beb19984f` | NVIDIA AVO Reaches 100% on ARC-AGI-3 | eligible binding displaced into destination field |
| `candidate-942d749b003a60ce15fe` | Better tools for managing blocked users | eligible binding displaced into destination field |
| `candidate-97d340b5363216cc7c34` | pydantic/pydantic-ai: v2.32.2 | eligible binding displaced into destination field |
| `candidate-a3e6f24c629d26c03c7e` | The new GitHub Copilot experience in Slack | eligible binding displaced into destination field |
| `candidate-c63c4d2eb9ab9c5f7db2` | Say it once: introducing Bot Preference SynC | eligible binding displaced into destination field |
| `candidate-d16cffc461481617342c` | pydantic/pydantic-ai: v2.33.0 | eligible binding displaced into destination field |
| `candidate-eb2dcf949b9e1d34ebc8` | Where Security Fits in an AI Agent Stack | eligible binding displaced into destination field |
| `candidate-2761f29efb7ec3e68e72` | Stop Making TUIs | eligible binding displaced into destination field |
| `candidate-9f75676f15b6550e7ed1` | ChatGPT search now uses the site:operator at scale | eligible binding displaced into destination field |
| `candidate-c21612f76c50daf48abc` | llm-openrouter 0.7 | eligible binding displaced into destination field |
| `candidate-c3700454bb5584fc5395` | llm 0.32.1 | eligible binding displaced into destination field |
| `candidate-eb86cb474943d81d4dd1` | Tesla +5.14% | eligible binding displaced into destination field |
| `candidate-f514242617cff776b094` | Quoting Matt Webb | eligible binding displaced into destination field |

Verdict: `DUAL_EVIDENCE_REFERENCE_CONFLATION_MATERIALLY_SUPPORTED`.
Removing navigation/capability choices from the semantic model is also consistent
with the frozen architecture boundary: the model interprets meaning; the system
decides which bounded action it can execute.

## Evaluation-only Minimal Atomic contract

The LLM schema is `candidate-semantic-assessment-minimal-v1`. It owns only:

- scope relevance and basis;
- impact level and basis;
- core-claim/Evidence relation, relation Evidence IDs, and basis;
- affected audiences, impact mechanism, and alternative explanation;
- missing Evidence, verification target, and diagnostic verification path.

It does not contain verification action, verification source Evidence ID,
verification feasibility, Budget, priority, semantic disposition, or final route.

The deterministic system owns Evidence authority and eligibility, hard Evidence
binding validation, existing destination selection, current bounded-fetch
capability, investigation Budget availability, semantic warrant, routeability,
priority, and final route. The route space is `DROP`, `BUILD`, `INVESTIGATE`, and
`UNRESOLVED`. Only a deterministically confirmed duplicate can become `DROP` in
this prototype. Unknown, unsupported, incomplete, low-impact, out-of-scope, and
invalid local assessments remain explicit `UNRESOLVED` states.

`DIRECT_SUPPORT` can authorize `BUILD` only when it binds eligible excerpt-bearing
Evidence and passes the existing freshness guard. An official surface with an
empty excerpt remains insufficient. A meaningful in-scope `CRITICAL_GAP` can
become `INVESTIGATE` only when missing Evidence, target, and path are all present,
an existing non-discussion destination exists, bounded direct fetch is currently
supported, and investigation Budget is available.

One invalid Candidate becomes local `UNRESOLVED`; it does not invalidate the
other Candidate assessments. Batch identity corruption (missing, duplicate, or
foreign Candidate IDs) remains structurally fatal.

Offline structural tests prove schema parsing, removal of both competing Evidence
reference roles, hard Evidence boundaries, system-derived routeability, explicit
unsupported/incomplete behavior, one-local-invalid isolation in a 38-Candidate
batch, and bounded adapter parsing. They do not prove real semantic stability,
DROP quality, or token savings.

## Frozen real evaluation identity

| Property | Frozen value |
| --- | --- |
| Arm | Minimal Atomic PROPOSED only; CURRENT/V1/V2 are not rerun |
| Historical date | `2026-08-22` |
| Historical now | `2026-08-22T07:56:14.225732+08:00` |
| Candidate count / batch | `38`, one batch |
| Candidate payload SHA256 | `1212995347ba2710b22537b3b0e2973e00f4e7872c22c445dee833bcf3b4c858` |
| Prompt | `prompts/evaluation_minimal_atomic/candidate_triage.md` |
| Prompt SHA256 | `4c193118a7e992f5a0165b17fc183df6a3a12ede120a83b77c73a1f44bcfbf6a` |
| Schema | `candidate-semantic-assessment-minimal-v1` |
| Schema SHA256 | `7c8e80a9783be3b42e3d800bce9936c28826c6261cf63dc1d87149d7665e90df` |
| Router | `deterministic-resource-router-minimal-v1` |
| Model / host | `deepseek-v4-flash` / `api.deepseek.com` |
| Inference | thinking disabled; temperature `1.0` explicit; top_p and seed omitted |
| Trials | 3 independent logical trials; no failed-trial supplementation and no automatic whole-trial rerun |
| Disabled | CURRENT/V1/V2 reruns, Story, downstream, Evidence HTTP, production writes, notifications, GitHub Actions |

Bounded structured-output repair attempts inside one logical trial remain the
Provider adapter's existing behavior; they do not authorize a whole-trial rerun.

## Frozen metrics and historical comparators

The report must record trial completion, candidate-local valid rate, pairwise and
mean route agreement, 3/3 stable count, stable BUILD anchors, stable INVESTIGATE
anchors, reasonable no-resource behavior, UNRESOLVED rate, invalid rate,
Evidence-boundary violations, and prompt/completion/reasoning usage.

Historical comparators only:

| Arm | Mean route agreement | 3/3 stable |
| --- | ---: | ---: |
| CURRENT | 0.614 | 16/38 |
| Atomic V1 | 0.807 | 27/38 |
| Atomic V2 | 0.667 | 19/38 |

## Precommitted decision gate

Minimal Atomic is rejected for production migration if any of these blockers is
observed:

- any Evidence-integrity violation, including unsupported Evidence authorizing BUILD;
- one Candidate causing batch-fatal loss of otherwise usable assessments;
- utility collapse into predominantly UNRESOLVED without explicit semantic or
  resource justification;
- loss of known eligible Evidence-backed BUILD anchors;
- material stability regression below the useful Atomic V1 direction, especially
  performance below Atomic V2 on both mean agreement and 3/3 stability.

Acceptance does not require perfect agreement. It requires three completed trials,
zero Evidence-boundary violations, candidate-local failure isolation, preserved
anchor utility, reasonable no-resource behavior, and stability/utility sufficient
to justify a separately authorized production-migration phase.

`REAL PROVIDER NOT RUN`

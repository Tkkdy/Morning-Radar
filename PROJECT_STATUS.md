# Morning Radar Project Status

## B0.5 Retrieval-to-Story Intelligence Pipeline

Status: implemented locally; production activation intentionally not performed.

B0.5 replaces the pre-Story `ResearchCase` side path and the Story-cost-derived 17-item semantic
cap with one `RawItem → Candidate → Story` lifecycle:

- every eligible RawItem forms or joins a Candidate before resource selection;
- Semantic Triage returns DROP, BUILD, or INVESTIGATE;
- Evidence, semantic, and execution states are stored separately;
- High-Recall Guardrail means MUST_TRIAGE only;
- bounded Evidence Resolution supports safe destination fetch and deterministic Official Surface
  trust with a JSON cache;
- Story facts require Claim × Evidence support and preserve discovery/evidence provenance;
- Protected Minimum + Shared Pool prevents early AI stages from starving downstream work;
- Candidate and compact Decision Trace artifacts are persisted daily;
- DeepSeek Vision 2026-08-22 is a frozen Golden Failure replay;
- offline same-budget comparison and Budget Sweep are available through
  `python -m morning_radar.evaluation.b05`.

Production defaults remain bounded at 50 logical AI calls and 120000 input characters. Editorial
remains enabled in Shadow mode and `maximum_brief_items` remains 12. No production workflow,
notification, push, merge, or Editorial activation is part of B0.5 implementation validation.

The sections below describe the pre-B0.5 historical milestones and are retained as project history.

## v0.4.1 Editorial Evidence Retention Hardening

Baseline: v0.4 merge commit `f83d56ac70ff1670810ba223d563b66a4df6ce97` (PR #5).

Current work is a Shadow-only hardening patch for evidence retention semantics and the independent
held-out Eval quality Gate. It does not change Placement mapping, the legacy fallback, TrendDetector,
Tendency, publishing, notifications, or the production pipeline. Active mode remains disabled until a
single frozen DeepSeek Eval passes all reader-selection, reason, retention, and P0 thresholds.

## Current Stage

Version: v0.35
Status: Feature complete and merged into master.

Current focus is no longer implementing v0.35 features. The project has entered:

- production observation of v0.35 behavior;
- collection of real-world failure cases;
- planning for future Editorial Intelligence improvements.

Current master includes the v0.35 merge commit:

- Merge commit: `1b3276cd2dc0602a63f5f4df0aa4fdf2eb9ecfec`

The daily workflow has successfully executed after the merge and continues generating briefs, site output, continuity records, radar signals and tendency records.

---

# 1. Current Product Goal

Morning Radar is a personal AI technology intelligence system.

The goal is not to maximize news quantity, but to reduce the user's cognitive workload:

- identify important AI and technology changes;
- preserve historical context;
- distinguish facts, observations and hypotheses;
- discover practical developer signals;
- gradually identify structural tendencies.

The product currently focuses on AI practitioners and advanced technology users.

---

# 2. Completed Features

## v0.3 Continuity Intelligence

Completed:

- Story Continuity
- Watch Memory
- Judgement Memory
- Judgement updates and corrections
- immutable history storage
- derived current views
- dependency review handling

Core principle:

Historical judgement is not evidence. New judgement must be based on new evidence.

---

## v0.35 Source and Signal Rebalance

Completed:

- Trusted Practitioner seed set
- practitioner-aware source roles
- statement type tracking
- lane-aware candidate selection
- bounded research trigger
- Radar Signal model
- AIHOT discovery adapter

Current practitioner configuration contains 10 seed identities. Machine-readable active feeds are intentionally limited; unavailable channels remain disabled rather than using fragile scraping.

Active feeds currently include:

- 宝玉
- Simon Willison
- Armin Ronacher
- Peter Steinberger

Reference configuration:

- `config/people.yaml`

---

## v0.35 Tendency Intelligence

Completed:

- Evidence clustering
- Shared Mechanism evaluation
- Candidate / Emerging / Persistent / Overturned lifecycle
- Supported / Strengthened / Weakened / Revised / Overturned updates
- Counterevidence support
- Falsifier support
- immutable daily snapshots
- reducer-based current view
- policy version tracking

Important design rules:

- News volume does not equal independent evidence.
- Candidate is internal research state, not public output.
- Emerging requires strong evidence discipline.
- Persistent requires new evidence after formation.
- No new evidence is not automatically weakening.

---

# 3. Key Technical Decisions

## Storage

Use append-only daily JSON snapshots.

Do not rewrite historical judgement or tendency decisions.

Current views are derived from history.

---

## AI Budget

Global limits remain bounded:

- maximum logical AI calls: 50
- maximum AI input characters: 120000

Additional bounded tasks:

- Research: one batch
- Tendency evaluation: one batch

No per-item autonomous research loop.

---

## Precision First

The system prefers false negatives over noisy output.

Valid outputs include:

- zero Radar Signals;
- zero public Tendencies;
- no Emerging decision.

---

# 4. Core Files Modified

Main areas:

## Configuration

- `config/app.yaml`
- `config/people.yaml`

## Research

- `src/morning_radar/research/engine.py`

## Tendency

- `src/morning_radar/tendencies/engine.py`
- `src/morning_radar/tendencies/clusters.py`
- `src/morning_radar/tendencies/reducer.py`

## Story selection

- `src/morning_radar/processing/story_builder.py`

## Models

- continuity models
- radar signal models
- tendency models

## AI prompts

- research resolution prompt
- tendency evaluation prompt

---

# 5. Validation Results

Verified:

- pytest passed
- ruff passed
- git diff checks passed
- fixture dry-run passed
- production history remained unchanged during dry-run
- Manual Preview #11 passed
- v0.35 merged into master

Latest production workflow after merge:

- completed successfully
- generated daily brief
- deployed GitHub Pages
- sent notification

---

# 6. Known Issues

## Editorial Quality

Not solved in v0.35.

Observed examples:

- sensational AI-related news may pass collection but have low user value;
- pure stock price movement is usually not meaningful without deeper business context.

These belong to future Editorial Intelligence work.

---

## Continuity and Tendency Reality Validation

The architecture is complete, but long-term accuracy requires real-world operation.

Future corrections should come from actual mistakes:

- false Emerging tendency;
- missed meaningful tendency;
- incorrect evidence independence;
- incorrect revision handling.

---

# 7. Tried but Deferred Approaches

Deferred:

- full X ingestion
- WeChat/Xiaohongshu scraping
- universal web research agent
- community heat aggregation
- creator trust scoring
- vector database
- knowledge graph
- complex trend scoring
- automatic credibility ranking

Reason:

Complexity was not justified before real production feedback exists.

---

# 8. Next Development Order

## Step 1: Observe v0.35

Allow production to accumulate:

- Radar Signal examples
- Tendency decisions
- false positives
- false negatives

Do not immediately expand Tendency complexity.

---

## Step 2: Design Editorial Intelligence

Future discussion area:

- what deserves attention;
- what is noise;
- business structure interpretation;
- market signal semantics;
- practitioner context integration;
- Story priority rules.

Examples already identified:

Positive:

- strategic investments and financing structures that reveal industry changes.

Negative:

- isolated sensational AI incidents;
- simple daily stock price movement.

---

## Step 3: Future Codex Workflow

Before major implementation:

1. Product design discussion.
2. Freeze principles and examples.
3. Codex read-only architecture audit.
4. Implementation task.
5. Tests and preview.
6. PR review and merge.

---

# Current Summary

Morning Radar has moved from a daily news summarizer into a system with:

- memory;
- evidence tracking;
- practitioner signals;
- radar signals;
- cautious tendency reasoning.

The next major challenge is not collecting more data. It is improving editorial judgement: deciding what deserves the user's limited attention.

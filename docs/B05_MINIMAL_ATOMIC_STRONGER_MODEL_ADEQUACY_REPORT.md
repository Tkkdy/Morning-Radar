# B0.5 Minimal Atomic Stronger Model Adequacy Report

Primary decision: `REJECT_STRONGER_MODEL_ADEQUACY`

B0.5 Router stop status: `ROUTER_RESEARCH_CLOSED_FOR_B0_5`

The frozen stronger-model experiment completed all three logical trials with
`deepseek-v4-pro`. The prompt, schema, deterministic router, Evidence rules,
inference configuration, anchors, and release gate were unchanged. The model
passed Evidence, validity, no-resource, and stability gates, but failed BUILD,
INVESTIGATE, stable-INVESTIGATE, and UNRESOLVED gates.

## A. Experiment integrity and stronger model

| Property | Frozen and observed value |
| --- | --- |
| Repository HEAD at start | `d872a648466a91345d4ddc05a4ec630b382455e3` |
| Historical date / now | `2026-08-22` / `2026-08-22T07:56:14.225732+08:00` |
| Candidate count / batch | 38 / one batch |
| Payload SHA256 | `1212995347ba2710b22537b3b0e2973e00f4e7872c22c445dee833bcf3b4c858` |
| Prompt SHA256 | `4c193118a7e992f5a0165b17fc183df6a3a12ede120a83b77c73a1f44bcfbf6a` |
| Schema SHA256 | `7c8e80a9783be3b42e3d800bce9936c28826c6261cf63dc1d87149d7665e90df` |
| Router | `deterministic-resource-router-minimal-v2-bounded-uncertainty` |
| Provider / host | DeepSeek / `api.deepseek.com` |
| Actual model | `deepseek-v4-pro` |
| Inference | thinking disabled; temperature 1.0 explicit; top_p/seed omitted |
| Protocol | 3 sequential logical trials; no whole-trial reruns |

The only intended variable versus the prior frozen experiment was
`deepseek-v4-flash` → `deepseek-v4-pro`. All non-model identities matched before
Trial 1. No fallback model was used.

## B. Trial execution, validity, routes, and usage

| Trial | Status | Logical attempts | Network requests | Structured retries | Valid / invalid | DROP | BUILD | INVESTIGATE | UNRESOLVED | Prompt / completion tokens |
| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |
| 1 | COMPLETED | 1 | 1 | 0 | 38 / 0 | 0 | 10 | 2 | 26 | 23,002 / 7,970 |
| 2 | COMPLETED | 1 | 2 | 0 | 38 / 0 | 0 | 8 | 3 | 27 | 23,002 / 8,235 |
| 3 | COMPLETED | 1 | 2 | 1 | 38 / 0 | 0 | 12 | 0 | 26 | 46,004 / 18,062 |
| Total | 3/3 | 3 | 5 | 1 | 114 / 0 | 0 | 30 | 5 | 79 | 92,008 / 34,267 |

Trial 2 used one bounded network retry before its successful structured attempt.
Trial 3's first structured output was rejected and the already-frozen second
structured attempt succeeded. Neither was a whole-trial rerun. There were no final
Provider or logical-trial failures.

Total reported tokens: 126,275. Reasoning tokens: 0. All successful responses
reported system fingerprint `a307abda487cd1b463329ccb945ce396`.

## C. Validity and Evidence integrity

- Candidate-local valid assessments: 114/114
- Invalid assessments: 0
- Unsupported DIRECT_SUPPORT bindings: 0
- Empty-excerpt/source identity asserted as support: 0
- Invalid Evidence IDs: 0
- Authority/eligibility violations: 0
- Invalid assessment authorizing BUILD: 0
- Other deterministic Evidence-boundary violations: 0
- Batch-fatal validation failures: 0

Did unsupported Evidence authorize factual support? **NO**.

Evidence/validity gate: `PASS`.

## D. Stability

- Trial 1 vs 2 route agreement: 81.58%
- Trial 1 vs 3 route agreement: 84.21%
- Trial 2 vs 3 route agreement: 81.58%
- Mean pairwise route agreement: **82.46%**
- 3/3 stable Candidates: **28/38**

Both frozen stability thresholds passed.

## E. BUILD gate

| Anchor | Trial 1 / 2 / 3 |
| --- | --- |
| GitHub Copilot Teams | BUILD / BUILD / BUILD |
| Ollama | BUILD / UNRESOLVED / BUILD |
| GitHub Copilot Slack | BUILD / BUILD / BUILD |
| Cloudflare | UNRESOLVED / BUILD / BUILD |
| pydantic-ai v2.33 | BUILD / BUILD / BUILD |
| Micro1 | UNRESOLVED / UNRESOLVED / UNRESOLVED |

Actual: 13/18 BUILD occurrences. Required: at least 16/18. `FAIL`.

## F. INVESTIGATE gate

| Investigation anchor | Trial 1 / 2 / 3 |
| --- | --- |
| DeepSeek Vision | INVESTIGATE / INVESTIGATE / UNRESOLVED |
| Meta AI glasses | INVESTIGATE / INVESTIGATE / UNRESOLVED |
| Starcloud | UNRESOLVED / UNRESOLVED / UNRESOLVED |
| Border phone-data | UNRESOLVED / UNRESOLVED / UNRESOLVED |
| Physical books | UNRESOLVED / UNRESOLVED / UNRESOLVED |
| Flock camera | UNRESOLVED / UNRESOLVED / UNRESOLVED |

- Total INVESTIGATE: 5/114; required at least 6/114 — `FAIL`.
- Stable 3/3 INVESTIGATE anchors: 0; required at least 1 — `FAIL`.
- DeepSeek Vision: 2/3 INVESTIGATE; required at least 2/3 — `PASS`.

## G. No-resource and UNRESOLVED gates

Stop Making TUIs, What We Tell AI, Tesla +5.14%, and Quoting Matt Webb were
UNRESOLVED in all 12 occurrences.

- No-resource BUILD/INVESTIGATE escalations: 0/12; required 0/12 — `PASS`.
- UNRESOLVED: 79/114 (69.3%); required below 70/114 — `FAIL`.

## H. Historical comparison

All comparator rows are recorded historical results; none was rerun.

| Arm | Mean agreement | 3/3 stable | BUILD | INVESTIGATE | UNRESOLVED | Evidence violations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CURRENT | 61.4% | 16/38 | n/a | n/a | n/a | n/a |
| Atomic V1 | 80.7% | 27/38 | historical | historical | historical | historical |
| Minimal Atomic Flash #1 | 87.72% | 31/38 | 41/114 | 3/114 | 70/114 | 0 |
| Minimal Atomic Flash #2 | 85.96% | 30/38 | 47/114 | 4/114 | 63/114 | 1 |
| Minimal Atomic Pro | **82.46%** | **28/38** | **30/114** | **5/114** | **79/114** | **0** |

Pro improved DeepSeek Vision behavior and restored the Evidence/no-resource gates,
but did not satisfy the already-frozen release gate as a whole.

## I. Frozen release gate table

| Metric | Required | Actual | Result |
| --- | --- | --- | --- |
| Logical trials complete | 3/3 | 3/3 | PASS |
| Supplemental whole-trial reruns | 0 | 0 | PASS |
| Candidate-local valid rate | ≥90% | 114/114 = 100% | PASS |
| Evidence violations | 0 | 0 | PASS |
| Batch-fatal validation failure | none | none | PASS |
| BUILD anchors | ≥16/18 | 13/18 | **FAIL** |
| No-resource escalations | 0/12 | 0/12 | PASS |
| Total INVESTIGATE | ≥6/114 | 5/114 | **FAIL** |
| Stable INVESTIGATE anchors | ≥1 | 0 | **FAIL** |
| DeepSeek Vision INVESTIGATE | ≥2/3 | 2/3 | PASS |
| UNRESOLVED | <70/114 | 79/114 | **FAIL** |
| Mean route agreement | ≥80.7% | 82.46% | PASS |
| 3/3 stable Candidates | ≥27/38 | 28/38 | PASS |

## J. Decision and B0.5 stop status

`REJECT_STRONGER_MODEL_ADEQUACY`

`ROUTER_RESEARCH_CLOSED_FOR_B0_5`

The frozen stronger-model hypothesis did not pass the release gate. The directly
evidenced failures are BUILD-anchor retention, total INVESTIGATE utility, absence
of a stable INVESTIGATE anchor, and excessive UNRESOLVED routing. No Router V3,
new schema, prompt adjustment, semantic-warrant patch, temperature/batch test, or
additional model experiment is proposed or started.

Any future Semantic Router redesign belongs to the B0.6 backlog.

## K. Artifact integrity

Immutable local run directory:
`.tmp/b05-minimal-atomic-stronger-model-20260830`.

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `manifest.json` | 3,851 | `da2eaffb309680436f7334450fee2800b982a3b85c46799af88040532223665c` |
| `report.json` | 7,083 | `ee60813ccceedc7b642b1522817b82b8867406c12016c90a74e360f6a4c0337f` |
| `minimal-atomic-trial-1.json` | 82,400 | `58af5b73ece8e21ed1ee80ea67a92e33892cc89a49310c695fa1757f888ac70c` |
| `minimal-atomic-trial-2.json` | 81,804 | `96d81006301e923a0f7450b71d82ebd14d129a0c0bf442c0d9adcccdf2383e17` |
| `minimal-atomic-trial-3.json` | 88,009 | `dc94454a1e9bf5ceb20d9cbecd892ada5cdfdc445d1acfa903ef0bc321e79e1b` |

No API key, secret, or hidden reasoning is persisted.

## L. Safety and next step

- Flash reruns: 0
- Other models/providers: 0
- Live collectors: 0
- Evidence HTTP: 0
- Notifications: 0
- Production writes/runs: 0
- Production rollout/migration: 0
- GitHub Actions: 0
- Merge: 0
- Supplemental trials: 0

Next: do not continue B0.5 Router optimization. Record remaining Semantic Router
redesign as B0.6 backlog and review what subset of B0.5 remains releasable without
it.

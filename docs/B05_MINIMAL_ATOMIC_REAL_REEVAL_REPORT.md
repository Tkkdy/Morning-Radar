# B0.5 Minimal Atomic Second Real Reevaluation Report

Primary decision: `REJECT_MINIMAL_ATOMIC_V2`

The frozen three-trial corrected Minimal Atomic experiment completed without
contract drift, supplemental trials, whole-trial reruns, or post-result tuning.
The corrected route restored some INVESTIGATE occurrences and preserved aggregate
stability and BUILD utility, but it failed the precommitted INVESTIGATE,
no-resource, and Evidence-validation gates.

## A. Experiment integrity

| Property | Frozen and observed value |
| --- | --- |
| Repository HEAD at start | `2b78d7f03dd01bf8b1170bdf93793ba37ac769dc` |
| Historical date / now | `2026-08-22` / `2026-08-22T07:56:14.225732+08:00` |
| Candidate count / batch | 38 / one batch |
| Payload SHA256 | `1212995347ba2710b22537b3b0e2973e00f4e7872c22c445dee833bcf3b4c858` |
| Prompt SHA256 | `4c193118a7e992f5a0165b17fc183df6a3a12ede120a83b77c73a1f44bcfbf6a` |
| Schema SHA256 | `7c8e80a9783be3b42e3d800bce9936c28826c6261cf63dc1d87149d7665e90df` |
| Router | `deterministic-resource-router-minimal-v2-bounded-uncertainty` |
| Provider | DeepSeek `deepseek-v4-flash` at `api.deepseek.com` |
| Inference | thinking disabled; temperature 1.0 explicit; top_p/seed omitted |
| Protocol | 3 sequential logical trials; no whole-trial rerun |

All frozen identities matched before Trial 1. CURRENT, Atomic V1/V2, and the first
Minimal Atomic experiment were not rerun.

## B. Trial execution, validity, routes, and usage

| Trial | Status | Logical attempts | Network | Structured retries | Valid / invalid | DROP | BUILD | INVESTIGATE | UNRESOLVED | Prompt / completion tokens |
| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |
| 1 | COMPLETED | 1 | 1 | 0 | 37 / 1 | 0 | 16 | 0 | 22 | 23,002 / 3,173 |
| 2 | COMPLETED | 1 | 1 | 0 | 38 / 0 | 0 | 14 | 4 | 20 | 23,002 / 6,142 |
| 3 | COMPLETED | 1 | 1 | 0 | 38 / 0 | 0 | 17 | 0 | 21 | 23,002 / 8,437 |
| Total | 3/3 | 3 | 3 | 0 | 113 / 1 | 0 | 47 | 4 | 63 | 69,006 / 17,752 |

Total reported tokens were 86,758; reasoning tokens were 0. Serialized Candidate
payload accounting was 228,027 characters across the three trials. All three
responses reported system fingerprint `a26a7955944dc5c60445bff77fac9c8e`.

The only invalid assessment was Trial 1
`candidate-0e7ded1847d561ca6738` (speech benchmark optimization): it asserted
DIRECT_SUPPORT using `evidence-dd728acf481350192296`, whose excerpt was empty and
which was not eligible claim-bearing Evidence. Validation produced
`INVALID_DIRECT_SUPPORT_BINDING` and safely routed the Candidate to local
UNRESOLVED. The invalid binding did not authorize BUILD or destroy the batch.

## C. Stability

- Trial 1 vs 2 agreement: 78.95%
- Trial 1 vs 3 agreement: 97.37%
- Trial 2 vs 3 agreement: 81.58%
- Mean pairwise agreement: **85.96%**
- 3/3 stable Candidates: **30/38**

Both frozen stability thresholds passed.

## D. BUILD anchor gate

| Anchor | Trial 1 / 2 / 3 |
| --- | --- |
| GitHub Copilot Teams | BUILD / BUILD / BUILD |
| Ollama | BUILD / BUILD / BUILD |
| GitHub Copilot Slack | BUILD / BUILD / BUILD |
| Cloudflare | BUILD / BUILD / BUILD |
| pydantic-ai v2.33 | BUILD / BUILD / BUILD |
| Micro1 | BUILD / BUILD / BUILD |

Actual: 18/18 BUILD occurrences. Required: at least 16/18. `PASS`.

## E. INVESTIGATE anchor gate

| Anchor | Trial 1 / 2 / 3 |
| --- | --- |
| DeepSeek Vision | UNRESOLVED / INVESTIGATE / UNRESOLVED |
| Meta AI glasses | UNRESOLVED / INVESTIGATE / UNRESOLVED |
| Starcloud | UNRESOLVED / UNRESOLVED / UNRESOLVED |
| Border phone-data | UNRESOLVED / UNRESOLVED / UNRESOLVED |
| Physical books | UNRESOLVED / UNRESOLVED / UNRESOLVED |
| Flock camera | UNRESOLVED / UNRESOLVED / UNRESOLVED |

- Total INVESTIGATE: 4/114; required at least 6/114 — `FAIL`.
- Stable 3/3 INVESTIGATE anchors: 0; required at least 1 — `FAIL`.
- DeepSeek Vision: 1/3 INVESTIGATE; required at least 2/3 — `FAIL`.

In Trials 1 and 3 the DeepSeek assessment did not express the frozen corrected
pattern: its scope/relation were UNKNOWN rather than IN_SCOPE/CRITICAL_GAP. Trial 2
did express the complete executable pattern and deterministically reached
INVESTIGATE. This observation is recorded only to explain the gate result; no
prompt, schema, or router change was made.

## F. No-resource gate

| Anchor | Trial 1 / 2 / 3 |
| --- | --- |
| Stop Making TUIs | BUILD / UNRESOLVED / BUILD |
| What We Tell AI | UNRESOLVED / UNRESOLVED / UNRESOLVED |
| Tesla +5.14% | UNRESOLVED / UNRESOLVED / UNRESOLVED |
| Quoting Matt Webb | UNRESOLVED / UNRESOLVED / UNRESOLVED |

Actual BUILD/INVESTIGATE escalations: 2/12. Required: 0/12. `FAIL`.
Both escalations were valid DIRECT_SUPPORT/medium-impact BUILD assessments for Stop
Making TUIs; they were not unsupported-Evidence authorizations.

## G. Evidence integrity

- Unsupported DIRECT_SUPPORT binding occurrences: 1
- Invalid Evidence IDs: 0
- Source identity/empty excerpt asserted as claim support: 1
- Authority violations authorizing BUILD: 0
- Eligibility violations authorizing BUILD: 0
- Invalid assessment authorizing BUILD: 0
- Batch-fatal validation failures: 0

Required validation/Evidence violations: 0. Actual: 1 safely rejected local
binding. `FAIL`.

Did unsupported Evidence authorize factual support? **NO**. The hard validator
rejected it and returned local UNRESOLVED.

## H. UNRESOLVED gate

Actual: 63/114 (55.3%). Required: below 70/114. `PASS`.

## I. Historical comparison

Historical arms were read only.

| Arm | Mean agreement | 3/3 stable | BUILD | INVESTIGATE | UNRESOLVED | Evidence/validation violations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CURRENT | 61.4% | 16/38 | not directly compared | not directly compared | not directly compared | n/a |
| Atomic V1 | 80.7% | 27/38 | historical | historical | historical | historical |
| Minimal Atomic first real eval | 87.72% | 31/38 | 41/114 | 3/114 | 70/114 | 0 |
| Minimal Atomic second real eval | **85.96%** | **30/38** | **47/114** | **4/114** | **63/114** | **1** |

## J. Final frozen gate table

| Metric | Required | Actual | Result |
| --- | --- | --- | --- |
| Logical trial completion | 3/3 | 3/3 | PASS |
| Supplemental whole-trial reruns | 0 | 0 | PASS |
| Candidate-local valid rate | ≥90% | 113/114 = 99.1% | PASS |
| Evidence/validation violations | 0 | 1 | **FAIL** |
| Batch-fatal validation regression | none | none | PASS |
| BUILD anchors | ≥16/18 | 18/18 | PASS |
| No-resource BUILD/INVESTIGATE escalations | 0/12 | 2/12 | **FAIL** |
| Total INVESTIGATE | ≥6/114 | 4/114 | **FAIL** |
| Stable INVESTIGATE anchors | ≥1 | 0 | **FAIL** |
| DeepSeek Vision INVESTIGATE | ≥2/3 | 1/3 | **FAIL** |
| UNRESOLVED | <70/114 | 63/114 | PASS |
| Mean route agreement | ≥80.7% | 85.96% | PASS |
| 3/3 stable Candidates | ≥27/38 | 30/38 | PASS |

## K. Gate decision

`REJECT_MINIMAL_ATOMIC_V2`

The frozen rejection is based only on the failed gates above. Aggregate stability,
BUILD utility, candidate-local failure isolation, and total UNRESOLVED improved or
remained acceptable, but the required real INVESTIGATE recovery did not occur and
the no-resource/Evidence-validation gates regressed.

No V3, prompt adjustment, semantic-warrant change, or other fix is proposed or
implemented.

## L. Artifact integrity

Immutable local run directory:
`.tmp/b05-minimal-atomic-real-reeval-20260830`.

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `manifest.json` | 3,699 | `f60010b442fa578002b911bc68585b46bcdecd9127dfa2b3bf31ad93c9f2005d` |
| `report.json` | 7,006 | `e8a883913e2e7e3ab99ef0360daf2a5404a1edbd06fad3a026346a785507db05` |
| `minimal-atomic-trial-1.json` | 71,136 | `9bfde3764178b7698953dcc70f8c74b3033c6f7aa05dacc92c9c53c063865955` |
| `minimal-atomic-trial-2.json` | 79,470 | `5f9200799af533c35c5a2ae29271c1805bb61fc1536ff47eb23e84114c6fe467` |
| `minimal-atomic-trial-3.json` | 84,035 | `a40d36520742cbd6bc535f54f001aafade6840ef0dcdfcabc2da38fcdfd668ac` |

No secrets or hidden reasoning are stored in this report.

## M. Safety and next step

- Live collectors: 0
- Evidence HTTP: 0
- Notifications: 0
- Production writes: 0
- Production rollout: 0
- GitHub Actions: 0
- Merge: 0
- Other Provider experiments: 0
- Supplemental trials: 0
- Production router migration: 0

Next: review only the evidenced failed release gate. Do not begin another
optimization cycle automatically.

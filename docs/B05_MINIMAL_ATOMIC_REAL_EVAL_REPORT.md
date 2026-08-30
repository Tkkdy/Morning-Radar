# B0.5 Minimal Atomic Real Semantic Evaluation Report

Primary decision: `REJECT_MINIMAL_ATOMIC`

The frozen three-trial experiment completed without contract drift, whole-trial
reruns, post-result tuning, or production migration. Minimal Atomic preserved the
Evidence boundary and substantially improved aggregate route stability, but it
failed the precommitted utility gate: `UNRESOLVED` remained the majority route and
none of the frozen investigation anchors was stably routed to `INVESTIGATE`.

## A. Experiment integrity

| Property | Frozen and observed value |
| --- | --- |
| Repository HEAD at start | `00c2313927e09d3f4f3f3b8685d5810c18daec66` |
| Historical date | `2026-08-22` |
| Historical now | `2026-08-22T07:56:14.225732+08:00` |
| Candidate count / batch | 38 / one batch |
| Candidate payload SHA256 | `1212995347ba2710b22537b3b0e2973e00f4e7872c22c445dee833bcf3b4c858` |
| Serialized Candidate payload | 76,009 characters per trial |
| Prompt SHA256 | `4c193118a7e992f5a0165b17fc183df6a3a12ede120a83b77c73a1f44bcfbf6a` |
| Schema SHA256 | `7c8e80a9783be3b42e3d800bce9936c28826c6261cf63dc1d87149d7665e90df` |
| Schema / router | `candidate-semantic-assessment-minimal-v1` / `deterministic-resource-router-minimal-v1` |
| Provider | DeepSeek, `api.deepseek.com`, `deepseek-v4-flash` |
| Inference | thinking disabled; temperature 1.0 explicit; top_p and seed omitted |
| Trial protocol | 3 sequential independent logical trials; no whole-trial rerun |

All frozen identities matched before the first Provider request. CURRENT, Atomic
V1, and Atomic V2 were not rerun.

## B. Trial execution and usage

| Trial | Status | Logical attempts | Network requests | Structured attempts | Structured retries | Prompt tokens | Completion tokens | Failure |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | COMPLETED | 1 | 1 | 1 | 0 | 23,002 | 6,421 | none |
| 2 | COMPLETED | 1 | 1 | 1 | 0 | 23,002 | 6,886 | none |
| 3 | COMPLETED | 1 | 1 | 1 | 0 | 23,002 | 8,122 | none |
| Total | 3/3 | 3 | 3 | 3 | 0 | 69,006 | 21,429 | none |

Total reported tokens were 90,435. Reasoning tokens were 0. Serialized Candidate
payload accounting was 228,027 characters across the three logical trials. The
Provider reported the same system fingerprint in all three responses:
`a26a7955944dc5c60445bff77fac9c8e`.

## C. Candidate-local validity

| Trial | Valid | Invalid | Invalid reasons |
| --- | ---: | ---: | --- |
| 1 | 38/38 | 0/38 | none |
| 2 | 38/38 | 0/38 | none |
| 3 | 38/38 | 0/38 | none |

All 114 assessments passed candidate-local validation. There was no batch-fatal
identity or structural failure.

## D. Final routes

| Trial | DROP | BUILD | INVESTIGATE | UNRESOLVED | UNRESOLVED rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 0 | 14 | 2 | 22 | 57.9% |
| 2 | 0 | 16 | 1 | 21 | 55.3% |
| 3 | 0 | 11 | 0 | 27 | 71.1% |
| All occurrences | 0 | 41 | 3 | 70 | 61.4% |

Deterministic UNRESOLVED reasons:

| Reason | Trial 1 | Trial 2 | Trial 3 | Total |
| --- | ---: | ---: | ---: | ---: |
| `SEMANTIC_LOW_UNRESOLVED` | 12 | 10 | 13 | 35 |
| `SEMANTIC_OUT_OF_SCOPE_UNRESOLVED` | 6 | 7 | 9 | 22 |
| `UNKNOWN_ASSESSMENT` | 3 | 3 | 5 | 11 |
| `DETERMINISTIC_FRESHNESS_CONSTRAINT` | 1 | 1 | 0 | 2 |

The majority UNRESOLVED outcome was not caused by validation failure or Budget.
It was produced by the frozen semantic-warrant/router interaction.

## E. Stability

Pairwise route agreement:

- Trial 1 vs 2: 92.11% (35/38)
- Trial 1 vs 3: 86.84% (33/38)
- Trial 2 vs 3: 84.21% (32/38)
- Mean: **87.72%**

Exactly 31/38 Candidates were 3/3 route-stable. Stable Candidate IDs:

`candidate-1d548e3bcbd3593d5128`, `candidate-bc873e6c8c2f20674dab`,
`candidate-006b36b9d1483b09aa8c`, `candidate-0e7ded1847d561ca6738`,
`candidate-22d91715672f19308caa`, `candidate-5b7e60188786bff1aa89`,
`candidate-628202a98a8bc40296ef`, `candidate-7f0f2d88470beb19984f`,
`candidate-942d749b003a60ce15fe`, `candidate-97d340b5363216cc7c34`,
`candidate-a3e6f24c629d26c03c7e`, `candidate-d16cffc461481617342c`,
`candidate-bf8d7b923eea97b08071`, `candidate-bbe255b7a8c2c25ffb5b`,
`candidate-b1facd7245c2927ec3e9`, `candidate-800b638243f2d10a8212`,
`candidate-e593db5012fb60dd67e7`, `candidate-d8667e69f310a0ad1add`,
`candidate-3ba5569a0ab98de82cc0`, `candidate-0fd13f455ec5383f34a7`,
`candidate-2761f29efb7ec3e68e72`, `candidate-4a273817cc955a411b6f`,
`candidate-81825f6b4ef3c93c6a5b`, `candidate-8914b45c3a650752071c`,
`candidate-97efeb77b03c55418eea`, `candidate-9f75676f15b6550e7ed1`,
`candidate-c21612f76c50daf48abc`, `candidate-c3700454bb5584fc5395`,
`candidate-d90cf66f744f8e26c61c`, `candidate-eb86cb474943d81d4dd1`,
`candidate-f514242617cff776b094`.

The seven unstable Candidates were:

| Candidate | Trial 1 / 2 / 3 |
| --- | --- |
| Cloudflare Bot Preference SynC | BUILD / BUILD / UNRESOLVED |
| Security in an AI Agent Stack | BUILD / BUILD / UNRESOLVED |
| Kagi paywall-filter setting | INVESTIGATE / UNRESOLVED / UNRESOLVED |
| Meta AI glasses | INVESTIGATE / INVESTIGATE / UNRESOLVED |
| Micro1 gross run rate | BUILD / BUILD / UNRESOLVED |
| YouTube AI sponsorship backlash | UNRESOLVED / BUILD / UNRESOLVED |
| DOJ investigation of a16z | UNRESOLVED / BUILD / UNRESOLVED |

## F. Frozen anchor audit

### Evidence-backed BUILD utility

| Anchor | Trial 1 / 2 / 3 | Result |
| --- | --- | --- |
| GitHub Copilot Teams | BUILD / BUILD / BUILD | retained |
| Ollama | BUILD / BUILD / BUILD | retained |
| GitHub Copilot Slack | BUILD / BUILD / BUILD | retained |
| pydantic-ai v2.33 | BUILD / BUILD / BUILD | retained |
| Cloudflare | BUILD / BUILD / UNRESOLVED | Trial 3 impact drifted medium to low |
| Micro1 | BUILD / BUILD / UNRESOLVED | Trial 3 impact drifted medium to low |

BUILD anchors achieved 16/18 BUILD occurrences; four of six were 3/3 stable. This
is not a BUILD-anchor collapse.

### Investigation and recall utility

| Anchor | Trial 1 / 2 / 3 | Deterministic observation |
| --- | --- | --- |
| DeepSeek Vision golden | UNRESOLVED / UNRESOLVED / UNRESOLVED | always in-scope critical gap with executable destination, but impact always unknown |
| Meta AI glasses | INVESTIGATE / INVESTIGATE / UNRESOLVED | Trial 3 drifted to out-of-scope/unknown impact |
| Starcloud orbital data centers | UNRESOLVED / UNRESOLVED / UNRESOLVED | direct support; freshness constraint twice, low impact once |
| Border phone-data | UNRESOLVED / UNRESOLVED / UNRESOLVED | consistently out-of-scope |
| Physical books / AI scanning | UNRESOLVED / UNRESOLVED / UNRESOLVED | consistently out-of-scope |
| Flock camera | UNRESOLVED / UNRESOLVED / UNRESOLVED | consistently out-of-scope |

Only 2/18 investigation-anchor occurrences routed to INVESTIGATE, both for Meta AI
glasses. No investigation anchor was 3/3 stable as INVESTIGATE. The recall-sensitive
DeepSeek Vision golden Candidate remained visible as UNRESOLVED rather than DROP,
but never received the supported bounded INVESTIGATE action despite an executable
destination in all three trials.

### Reasonable no-resource behavior

`Stop Making TUIs`, `What We Tell AI`, `Tesla +5.14%`, and `Quoting Matt Webb`
were UNRESOLVED in all 12 occurrences. None was incorrectly escalated to BUILD or
INVESTIGATE, and none was silently converted to DROP.

## G. Evidence integrity

- Unsupported DIRECT_SUPPORT bindings: 0
- Invalid Evidence IDs: 0
- Source identity used as factual support: 0
- Evidence eligibility/authority violations: 0
- BUILD from an invalid assessment: 0
- Other deterministic Evidence-boundary violations: 0

Did unsupported Evidence authorize factual support? **NO**.

## H. Historical comparison

CURRENT, V1, and V2 are historical artifacts only and were not rerun.

| Arm | Mean route agreement | 3/3 stable | Relevant limitation |
| --- | ---: | ---: | --- |
| CURRENT | 61.4% | 16/38 | direct route instability |
| Atomic V1 | 80.7% | 27/38 | LLM-owned verification feasibility |
| Atomic V2 | 66.7% | 19/38 | dual-reference invalidity and Trial 3 collapse |
| Minimal Atomic | **87.72%** | **31/38** | majority UNRESOLVED; zero stable INVESTIGATE anchors |

Minimal Atomic materially improved compatible aggregate stability and removed the
V2 Evidence-binding failure. The aggregate number does not override the frozen
anchor/utility gate.

## I. Gate decision

`REJECT_MINIMAL_ATOMIC`

The precommitted Evidence-integrity and batch-locality gates passed. BUILD utility
was substantially retained. The precommitted utility rejection condition was,
however, triggered:

- 70/114 routes (61.4%) were UNRESOLVED, rising to 27/38 in Trial 3;
- only 3/114 routes were INVESTIGATE;
- no frozen investigation anchor was stably INVESTIGATE;
- the recall-sensitive DeepSeek Vision golden anchor was 3/3 UNRESOLVED despite a
  complete executable destination contract.

The directly evidenced blocker is an **INVESTIGATE utility collapse into
UNRESOLVED**, arising from model-semantic impact/scope assessments interacting with
the frozen semantic-warrant rule. It is not an Evidence-validation regression,
Provider failure, Budget failure, or batch-fatal failure.

No replacement design is proposed in this report.

## J. Persisted artifact integrity

The immutable local run directory is
`.tmp/b05-minimal-atomic-real-20260830`. Its artifacts were not modified after the
run. SHA256 values:

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `manifest.json` | 3,438 | `b93506efd44442ed245cddcaf7b1f03fb76fcd28200652b627bdc3375ec1b41b` |
| `report.json` | 6,748 | `01c535888ff0b11308651a0d21a49c2af0e57b5c997c373109024a890526a916` |
| `minimal-atomic-trial-1.json` | 79,109 | `18827f8078f5c9b21bd81ae32f27cf4fc98bc71a72eb6a4aa02181cf219d6a3c` |
| `minimal-atomic-trial-2.json` | 81,900 | `227a7b5dd35be9b96ef1d799210997fd40fea2a6f0edbc2c17de83ad287c43e1` |
| `minimal-atomic-trial-3.json` | 78,116 | `fecb7cb0199b884f0dacf479f7520a93268f3c38a4c03083e2683e771986f337` |

No secret, API key, or hidden chain-of-thought is present in this report.

## K. Safety and repository scope

- Live collectors: 0
- Evidence HTTP: 0
- Notifications: 0
- Production writes: 0
- Production rollout: 0
- GitHub Actions: 0
- Merge: 0
- Other unauthorized Provider experiments: 0
- Real Provider logical trials: 3
- Real Provider network requests: 3
- Supplemental trials: 0
- Production router changes: 0

Next step: review only the evidenced failed utility gate before authorizing further
development.

# B0.5 Investigation Utility Recovery Report

Status: `READY_FOR_MINIMAL_ATOMIC_REAL_REEVAL`

This is an offline correction and deterministic replay over the three previously
recorded Minimal Atomic real outputs. No Provider was called and the replay is not
a new semantic result.

## A. Root-cause matrix

Legend: `C` means the missing-Evidence/target/path contract is complete; `D` means
an existing destination is available; capability and Budget were supported in all
18 cells. Eligible Evidence IDs and relation Evidence IDs are shown when present.

| Candidate | Trial | Scope | Impact | Relation / relation IDs | Missing / target / path | Eligible IDs | C | D | Old warrant | Route / reason | First blocking condition |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DeepSeek Vision | 1 | in | unknown | gap / none | yes / yes / yes | none | Y | Y | N | UNRESOLVED / UNKNOWN | unknown-impact certainty gate |
| DeepSeek Vision | 2 | in | unknown | gap / none | yes / yes / yes | none | Y | Y | N | UNRESOLVED / UNKNOWN | unknown-impact certainty gate |
| DeepSeek Vision | 3 | in | unknown | gap / two discovery IDs | yes / yes / yes | none | Y | Y | N | UNRESOLVED / UNKNOWN | unknown-impact certainty gate |
| Meta AI glasses | 1 | in | medium | gap / none | yes / yes / yes | none | Y | Y | Y | INVESTIGATE / EXECUTABLE | none |
| Meta AI glasses | 2 | in | medium | gap / none | yes / yes / yes | none | Y | Y | Y | INVESTIGATE / EXECUTABLE | none |
| Meta AI glasses | 3 | out | unknown | gap / two discovery IDs | no / no / no | none | N | Y | N | UNRESOLVED / OUT_OF_SCOPE | scope assessment |
| Starcloud | 1 | in | medium | direct / `evidence-baabe231334911df6d13` | no / no / no | same ID | N/A | Y | N | UNRESOLVED / FRESHNESS | relation is not a critical gap |
| Starcloud | 2 | in | medium | direct / `evidence-baabe231334911df6d13` | no / yes / yes | same ID | N/A | Y | N | UNRESOLVED / FRESHNESS | relation is not a critical gap |
| Starcloud | 3 | in | low | direct / `evidence-baabe231334911df6d13` | no / no / no | same ID | N/A | Y | N | UNRESOLVED / LOW | low impact |
| Border phone-data | 1 | out | low | gap / none | yes / yes / yes | none | Y | Y | N | UNRESOLVED / OUT_OF_SCOPE | scope assessment |
| Border phone-data | 2 | out | unknown | gap / none | no / no / no | none | N | Y | N | UNRESOLVED / OUT_OF_SCOPE | scope assessment |
| Border phone-data | 3 | out | unknown | gap / two discovery IDs | no / no / no | none | N | Y | N | UNRESOLVED / OUT_OF_SCOPE | scope assessment |
| Physical books | 1 | out | low | gap / none | yes / yes / yes | none | Y | Y | N | UNRESOLVED / OUT_OF_SCOPE | scope assessment |
| Physical books | 2 | out | unknown | gap / none | no / no / no | none | N | Y | N | UNRESOLVED / OUT_OF_SCOPE | scope assessment |
| Physical books | 3 | out | low | gap / two discovery IDs | no / no / no | none | N | Y | N | UNRESOLVED / OUT_OF_SCOPE | scope assessment |
| Flock camera | 1 | out | low | gap / none | yes / yes / yes | none | Y | Y | N | UNRESOLVED / OUT_OF_SCOPE | scope assessment |
| Flock camera | 2 | out | unknown | gap / none | no / no / no | none | N | Y | N | UNRESOLVED / OUT_OF_SCOPE | scope assessment |
| Flock camera | 3 | out | unknown | gap / two discovery IDs | no / no / no | none | N | Y | N | UNRESOLVED / OUT_OF_SCOPE | scope assessment |

The discovery-only IDs in CRITICAL_GAP rows did not authorize factual support.
All assessments were valid, and every destination was an existing Candidate-local
destination. No resolver capability, Budget, destination, Evidence authority, or
Evidence eligibility failure was the first blocker.

## B. Dominant mechanism

Of 18 anchor/trial cells, two already reached INVESTIGATE. First blockers for the
remaining 16 were:

| Category | Count |
| --- | ---: |
| Model scope assessment not in-scope | 10 |
| Deterministic warrant rejects unknown impact | 3 |
| Model relation is DIRECT_SUPPORT, not CRITICAL_GAP | 2 |
| Model impact is LOW | 1 |
| Contract/destination/capability/Budget first blocker | 0 |

Classification: `MIXED`.

The model-semantic cases cannot be honestly repaired by replaying old outputs and
were not overridden. A single deterministic contract mistake was nevertheless
fully evidenced: in every recorded cell with `IN_SCOPE + CRITICAL_GAP + unknown
impact + complete contract + executable destination + MUST_TRIAGE`, the old router
returned UNKNOWN/UNRESOLVED before evaluating the bounded action. This occurred in
all three DeepSeek Vision trials and, outside the anchor set, in all three Kobo and
all three speech-benchmark trials.

## C. Single general correction

Router version:
`deterministic-resource-router-minimal-v2-bounded-uncertainty`.

The deterministic semantic-warrant rule now treats an in-scope MUST_TRIAGE
Candidate with unknown impact as potentially important enough for bounded
investigation when and only when:

- the relation is CRITICAL_GAP;
- missing Evidence, verification target, and verification path are complete;
- an existing destination is available;
- bounded direct fetch is supported;
- investigation Budget is available;
- candidate-local validation is valid.

The action is assigned a lower deterministic priority (0.5) than established
medium/high-impact investigation. MUST_TRIAGE directs bounded attention; it does
not become Evidence and cannot authorize BUILD.

The correction is general because it uses only existing semantic and deterministic
state. It contains no Candidate, date, entity, source, or company exception. Scope
OUT, impact LOW, incomplete contracts, unsupported capability, unavailable Budget,
and invalid Evidence remain UNRESOLVED.

## D. Deterministic replay of 114 frozen assessments

`DETERMINISTIC_REPLAYABLE`; no prompt or schema changed.

Exactly nine occurrences changed, covering three general-pattern Candidates in
each of the three trials:

- DeepSeek Vision: UNRESOLVED → INVESTIGATE ×3
- Kobo can run apps: UNRESOLVED → INVESTIGATE ×3
- Speech benchmark optimization: UNRESOLVED → INVESTIGATE ×3

| Trial | Old DROP / BUILD / INVESTIGATE / UNRESOLVED | New DROP / BUILD / INVESTIGATE / UNRESOLVED |
| --- | --- | --- |
| 1 | 0 / 14 / 2 / 22 | 0 / 14 / 5 / 19 |
| 2 | 0 / 16 / 1 / 21 | 0 / 16 / 4 / 18 |
| 3 | 0 / 11 / 0 / 27 | 0 / 11 / 3 / 24 |
| Total | 0 / 41 / 3 / 70 | 0 / 41 / 12 / 61 |

Replay UNRESOLVED rate falls from 61.4% to 53.5%. Mean route agreement remains
87.72%, and 3/3 stability remains 31/38 because all nine corrections are stable
within their Candidate. Candidate-local validity remains 114/114 and deterministic
Evidence-boundary violations remain 0.

These are counterfactual deterministic routes over saved semantics, not new real
Provider measurements.

## E. Anchor and safety effects

- DeepSeek Vision becomes INVESTIGATE ×3 instead of UNRESOLVED ×3.
- Meta AI glasses remains INVESTIGATE / INVESTIGATE / UNRESOLVED.
- Starcloud, Border, Physical books, and Flock camera are unchanged.
- Stable INVESTIGATE anchors increase from 0 to 1.
- All six BUILD anchors are unchanged: 16/18 BUILD occurrences.
- Stop Making TUIs, What We Tell AI, Tesla +5.14%, and Quoting Matt Webb remain
  UNRESOLVED in all 12 no-resource occurrences.
- Unsupported capability and incomplete-contract tests remain UNRESOLVED.
- An empty official destination, invalid Evidence ID, or unsupported Evidence
  still cannot authorize DIRECT_SUPPORT or BUILD.
- One invalid Candidate remains local UNRESOLVED rather than batch-fatal.

## F. Frozen Minimal Atomic real reevaluation gate

The next experiment is not authorized by this report. If separately authorized,
it must use:

| Property | Frozen value |
| --- | --- |
| Arm | corrected Minimal Atomic PROPOSED only; no CURRENT/V1/V2/v1 reruns |
| Historical date / now | `2026-08-22` / `2026-08-22T07:56:14.225732+08:00` |
| Candidate count / batch | 38 / one batch |
| Payload SHA256 | `1212995347ba2710b22537b3b0e2973e00f4e7872c22c445dee833bcf3b4c858` |
| Prompt SHA256 | `4c193118a7e992f5a0165b17fc183df6a3a12ede120a83b77c73a1f44bcfbf6a` |
| Schema SHA256 | `7c8e80a9783be3b42e3d800bce9936c28826c6261cf63dc1d87149d7665e90df` |
| Router | `deterministic-resource-router-minimal-v2-bounded-uncertainty` |
| Provider | DeepSeek `deepseek-v4-flash` at `api.deepseek.com` |
| Inference | thinking disabled; temperature 1.0 explicit; top_p/seed omitted |
| Trials | 3 sequential logical trials; no failed-trial supplementation or automatic whole-trial rerun |
| Disabled | Story, downstream, Evidence HTTP, production writes, notifications, GitHub Actions |

Frozen metrics remain trial completion, candidate-local validity, route
distribution, pairwise/mean agreement, 3/3 stability, BUILD/INVESTIGATE/no-resource
anchors, UNRESOLVED and invalid rates, Evidence violations, structured retries,
network requests, and token/character usage.

The corrected router may be accepted for a later production-migration decision
only if all of these precommitted conditions hold:

- 3/3 logical trials complete without supplemental whole-trial reruns;
- zero Evidence-boundary violations and no batch-fatal local-validation regression;
- at least 90% of the 114 candidate assessments are candidate-local valid;
- BUILD anchors retain at least 16/18 BUILD occurrences;
- reasonable no-resource anchors produce 0/12 BUILD or INVESTIGATE escalations;
- INVESTIGATE increases to at least 6/114 occurrences;
- at least one frozen investigation anchor is 3/3 INVESTIGATE;
- DeepSeek Vision is INVESTIGATE in at least 2/3 trials;
- UNRESOLVED is below the prior 70/114 occurrences;
- mean route agreement is at least 80.7% and at least 27/38 Candidates are 3/3
  stable, preserving the useful Atomic V1 direction.

Any contract drift makes the run inconclusive. Evidence regression, batch-fatal
failure, BUILD/no-resource regression, failure to restore the frozen INVESTIGATE
criteria, or material stability regression rejects the correction.

Historical Minimal Atomic v1 comparator: agreement 87.72%, stable 31/38,
INVESTIGATE 3/114, UNRESOLVED 70/114.

`REAL PROVIDER NOT RUN`

## G. Safety

- Real Provider calls: 0
- Live collectors: 0
- Evidence HTTP: 0
- Notifications: 0
- Production writes: 0
- Production rollout: 0
- GitHub Actions: 0
- Merge: 0
- Production Minimal Atomic migration: 0

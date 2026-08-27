# B0.5 P0 Hardening Report

Date: 2026-08-28
Review status: REVIEW ONLY; Draft PR; no production rollout approval

## Scope and operating constraints

This hardening pass changes only the audited B0.5 safety and evidence boundaries. It did not call a
real AI Provider, WxPusher, the production Daily Morning Radar workflow, or Level 3 search. Editorial
remains in Shadow mode. The offline replay uses the production pipeline/functions and replaces only
external AI and HTTP boundaries with deterministic fakes.

## P0 results

### 1. Budget protection

Protected Minimum + Shared Pool now protects both logical calls and serialized input characters.
Both dimensions are configured in `config/app.yaml`, accounted per stage, and released when a stage
completes. A Triage request that would consume a later stage's call or character reserve is classified
as `DEFERRED_BY_BUDGET`, not `FAILED_AI` or semantic `DROP`.

### 2. Frozen 2026-08-22 workload under the 120k hard cap

The full offline replay directly invoked `MorningRadarPipeline.run` with the frozen historical Raw
workload. Measured result:

- Triage serialized payload: 75,690 characters (Candidate stat, excluding JSON list punctuation:
  75,651);
- total serialized AI input: 118,008 / 120,000 characters;
- logical calls: 11 / 50;
- stage calls observed: Triage, Story, Editorial, Continuity, Brief, and Tendency;
- persisted Stories: 2;
- deterministic persisted-Story integrity violations: 0.

The global cap remains hard. The Story stage deferred remaining candidates when shared capacity was
exhausted; it did not consume the reserves for downstream stages. Therefore this result proves full
pipeline traversal and downstream-stage preservation, not unlimited completion of every admitted
Story candidate. Fake output does not prove real-provider semantic quality or reader precision.

### 3. Claim Scope no greater than Evidence Scope

Final Story admission now uses deterministic structured compatibility across:

- claim subject versus Evidence `authoritative_for` / `subject_entities`;
- availability scope (`ONE_ACCOUNT`, `SOME_USERS`, `BROAD`, `GA`);
- temporal scope (`OBSERVED_NOW`, `CURRENTLY_EXISTS`, `NEWLY_RELEASED`, `FIRST_EVER`);
- assertion scope (`OBSERVED`, `OFFICIALLY_ANNOUNCED`, `INDEPENDENTLY_VERIFIED`);
- practitioner Observation Quality for firsthand availability/behavior claims.

High-risk scope is also inferred deterministically from English and Chinese claim text, so a model
cannot under-declare a visible GA, first, new-release, or performance claim in structured output.

### 4. Claim compatibility rules

The Story Boundary has non-model-bypassable rules:

- discovery-only and unverified external Evidence cannot support a Story fact;
- practitioner Evidence must meet firsthandness, specificity, and artifact-support quality gates for
  firsthand availability/behavior claims and cannot expand to broad/GA scope;
- official Evidence is authoritative only for its structured subject scope;
- official performance claims must remain explicitly attributed;
- independent current-existence reporting does not prove a new release;
- novelty/first claims require independent Evidence and matching temporal scope.

### 5. `scope_supported`

`scope_supported` is retained only as model proposal/diagnostic data. Story validation never reads it
for admission. `true` cannot bypass an incompatible authority/scope decision, and `false` does not
veto an otherwise deterministically compatible claim.

### 6. Evidence Resolution Level 3

Level 3 Targeted Official Lookup is **not implemented**. It is explicitly deferred. Current resolution
is limited to known Official Surface verification and bounded fetch of an existing destination URL;
there is no open search or claim-directed discovery of a new official page.

### 7. New URL provenance

Evidence URLs enter through only two deterministic routes:

1. a Collector's Raw URL or collector-verified discussion URL;
2. the final redirect URL returned by a bounded fetch that started from an existing Candidate
   destination.

An HTML canonical URL is metadata only and does not automatically become Evidence. An unknown final
host is marked `UNVERIFIED_EXTERNAL` and cannot support a Story fact. Provider output is validated
against an allowed URL set constructed before the call; the model has no path to add a URL to that
set. `github.com` is rejected as a global Official Surface. GitHub self-authority requires an exact
collector repository identity and URL owner/repository match, and is limited to that repository.

### 8. DNS rebinding, redirect, and proxy SSRF boundary

The default Evidence client now disables environment proxy inheritance with `trust_env=False`. It
rejects credentials, IP literals, local/single-label names, non-HTTP(S) schemes, arbitrary ports, and
DNS results containing non-public addresses. Every redirect target is revalidated; redirects,
retries, response size, and accepted content types are bounded. Cookies and Authorization are not
sent.

Residual boundary: DNS is validated before connection, but the validated IP is not pinned to the
socket. A DNS rebinding / validation-to-connect TOCTOU window therefore remains. Deployment egress
controls are still required. Tests may inject an HTTP client; the production path uses the hardened
default client.

## Evaluation interpretation

The same-budget recall comparison and routing sweep remain deterministic Fake-AI evaluations. The
full offline replay now uses the real production orchestration rather than a test-only simplified
pipeline. Evidence integrity is calculated by rerunning the production deterministic boundary over
persisted Stories. Reader precision and invalid-candidate workload remain `NOT_EVALUATED` where no
frozen labels support those metrics.

This report does not approve production rollout, Editorial activation, merge, or production workflow
dispatch.

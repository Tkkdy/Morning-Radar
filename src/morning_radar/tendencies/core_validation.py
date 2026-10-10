"""Reference, scope and revision validation; semantics still require model evaluation."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import uuid4

from morning_radar.ai.output_validation import is_suspicious_english_prose
from morning_radar.tendencies.core_models import CoreDecision, CoreEvidence, Revision
from morning_radar.tendencies.core_store import StaleRevision
from morning_radar.tendencies.inputs import digest


def fingerprint(claim: str) -> str:
    return re.sub(r"[\W_]+", "", claim.casefold())


def materialize(
    decision: CoreDecision,
    *,
    evidence: dict[str, CoreEvidence],
    current: dict[str, Revision],
    seeds: dict[str, dict],
    now: datetime,
    run_id: str,
    analysis_version: str,
) -> Revision | None:
    narratives = [
        decision.reason,
        decision.hypothesis.claim,
        decision.hypothesis.mechanism,
        decision.hypothesis.from_state,
        decision.hypothesis.to_state,
    ]
    if any(is_suspicious_english_prose(text) for text in narratives):
        raise ValueError("User-visible inference must use Chinese")
    previous = current.get(decision.hypothesis_id)
    parent = current.get(decision.parent_hypothesis_id)
    if decision.hypothesis_id and not previous:
        raise ValueError("Unknown hypothesis")
    if decision.previous_revision_id != (previous.revision_id if previous else None):
        raise StaleRevision("Stale proposed update")
    if decision.kind == "no_change":
        if not previous or decision.hypothesis != previous.decision.hypothesis:
            raise ValueError("No-change cannot replace a proposition")
        return None
    if previous is None and decision.kind not in {"early", "split"}:
        raise ValueError("New hypotheses require Early or an explicit branch")
    if previous and decision.kind == "early":
        raise ValueError("Existing hypothesis cannot be recreated as Early")
    if decision.kind == "split":
        if previous or not parent or parent.revision_id != decision.parent_revision_id:
            raise StaleRevision("Invalid branch parent")
        if not decision.relinquished:
            raise ValueError("Branch must explain separated scope")
    elif decision.parent_hypothesis_id or decision.parent_revision_id:
        raise ValueError("Unexpected branch relation")
    if decision.kind in {"narrowed", "split"}:
        old = previous or parent
        if not set(decision.hypothesis.scope) < set(old.decision.hypothesis.scope):
            raise ValueError("Narrowing/branch must reduce declared scope")
        if not decision.relinquished:
            raise ValueError("Narrowing must state abandoned claim")
    seed = seeds.get(decision.seed_id)
    if seed and seed.get("correction_of") and not previous:
        raise ValueError("User correction cannot be converted into a new hypothesis")
    if decision.seed_id and seed is None:
        raise ValueError("Unknown user seed")
    if decision.basis == "user_correction":
        if not previous or not seed or decision.kind != "user_correction":
            raise ValueError("Correction requires an actual user submission")
        if (
            seed.get("correction_of") != previous.hypothesis_id
            or seed.get("expected_revision") != previous.revision_id
        ):
            raise StaleRevision("User correction targets an obsolete version")
    elif decision.kind == "user_correction":
        raise ValueError("Wrong correction basis")
    if decision.basis == "analysis_reassessment":
        if not previous or decision.kind not in {"analysis_reassessment", "archived"}:
            raise ValueError("Analysis changes must not masquerade as external support")
    elif decision.kind in {"analysis_reassessment", "archived"}:
        raise ValueError("Analysis/attention status needs an explicit analysis basis")
    keys = [c.key for c in decision.hypothesis.conditions]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate condition identity")
    if any(t.condition_key not in keys for t in decision.hypothesis.tests):
        raise ValueError("Observation test must target a real condition")
    if decision.hypothesis.from_state.strip() == decision.hypothesis.to_state.strip():
        raise ValueError("No structural transition identified")
    refs = {}
    for citation in decision.citations:
        item = evidence.get(citation.evidence_id)
        if not item or citation.quote not in item.excerpt:
            raise ValueError("Unknown evidence or fabricated source quote")
        if max(item.observed_at, item.ingested_at) > now:
            raise ValueError("Future knowledge in historical evaluation")
        if citation.condition_key not in [*keys, "baseline"]:
            raise ValueError("Citation must locate a condition or baseline")
        if citation.role != "context" and (
            item.source_kind in {"user_hypothesis", "system_inference"}
            or item.status not in {"captured", "fetched"}
        ):
            raise ValueError("Unverified/unavailable input cannot verify or refute a condition")
        refs[item.evidence_id] = item
    for condition in decision.hypothesis.conditions:
        if not set(condition.evidence_ids) <= refs.keys() or not any(
            c.condition_key == condition.key and c.evidence_id in condition.evidence_ids
            for c in decision.citations
        ):
            raise ValueError("Condition observation lacks attributed evidence")
    for baseline in decision.hypothesis.baseline:
        if not baseline.assumption and (
            not baseline.evidence_ids or not set(baseline.evidence_ids) <= refs.keys()
        ):
            raise ValueError("Unverified historical baseline must remain an assumption")
    supporting = [refs[c.evidence_id] for c in decision.citations if c.role == "support"]
    scope = {value for item in supporting for value in item.scope}
    if not set(decision.hypothesis.scope) <= scope:
        raise ValueError("Claim scope exceeds supporting observations")
    if previous is None and not decision.hypothesis.unresolved:
        raise ValueError("Early inference must expose remaining conditions")
    if previous is None and any(
        fingerprint(r.decision.hypothesis.claim) == fingerprint(decision.hypothesis.claim)
        for r in current.values()
    ):
        raise ValueError("Existing proposition must use its stable identity")
    if (
        previous
        and decision.kind in {"supported", "strengthened"}
        and (
            decision.hypothesis.claim != previous.decision.hypothesis.claim
            or decision.hypothesis.scope != previous.decision.hypothesis.scope
        )
    ):
        raise ValueError("Support cannot silently change a claim or widen its scope")
    if decision.kind == "overturned" and not any(c.role == "counter" for c in decision.citations):
        raise ValueError("Overturning requires targeted counterevidence")
    if previous and decision.kind not in {"supported", "archived"}:
        valid_parts = {
            "baseline",
            "mechanism",
            "scope",
            *keys,
            *(c.key for c in previous.decision.hypothesis.conditions),
        }
        if not decision.changed_parts or not set(decision.changed_parts) <= valid_parts:
            raise ValueError("Substantive revision must identify the affected part")
    if previous and decision.basis == "external_evidence":
        old_observations = set(previous.observed_signatures) | {
            digest(" ".join(r.excerpt.split()) + ":" + r.source_kind) for r in previous.evidence
        }
        relevant = [
            refs[c.evidence_id] for c in decision.citations if c.role in {"support", "counter"}
        ]
        if not any(
            digest(" ".join(item.excerpt.split()) + ":" + item.source_kind) not in old_observations
            for item in relevant
        ):
            return None  # Same evidence is a check, never a new real-world update.
    if (
        decision.basis != "external_evidence"
        and previous
        and (decision.hypothesis == previous.decision.hypothesis and decision.kind != "archived")
    ):
        return None
    standing = "early" if previous is None else previous.standing
    if decision.kind == "strengthened":
        standing = "active"
    if decision.kind in {"overturned", "archived"}:
        standing = decision.kind
    return Revision(
        revision_id="rev-" + uuid4().hex,
        hypothesis_id=previous.hypothesis_id if previous else "hyp-" + uuid4().hex,
        previous_revision_id=previous.revision_id if previous else None,
        parent_hypothesis_id=decision.parent_hypothesis_id,
        parent_revision_id=decision.parent_revision_id,
        first_recorded_at=previous.first_recorded_at if previous else now,
        recorded_at=now,
        origin=previous.origin if previous else "user" if seed else "system",
        seed_id=previous.seed_id if previous else decision.seed_id,
        standing=standing,
        decision=decision,
        evidence=list(refs.values()),
        run_id=run_id,
        analysis_version=analysis_version,
        observed_signatures=sorted(
            set(previous.observed_signatures if previous else [])
            | {
                digest(" ".join(item.excerpt.split()) + ":" + item.source_kind)
                for item in refs.values()
                if item.source_kind not in {"user_hypothesis", "system_inference"}
            }
        ),
        synthetic=any(item.synthetic for item in refs.values()),
    )

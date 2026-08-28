"""Build traceable Story objects from classified, deduplicated RawItems."""

from __future__ import annotations

import hashlib
import logging
import math
import re
from datetime import datetime
from typing import Protocol

from morning_radar.ai import AIBudgetExceeded, AIOutputError
from morning_radar.ai.models import MergedStoryDraft
from morning_radar.ai.provider import AIProvider
from morning_radar.models import (
    AssertionScope,
    AvailabilityScope,
    Candidate,
    CandidateEvidence,
    ClaimScopeDimensions,
    ClaimType,
    EvidenceAuthority,
    EvidenceState,
    PublishedAtRole,
    RawItem,
    SemanticDisposition,
    Story,
    StorySourceRef,
    TemporalScope,
)
from morning_radar.provenance import verified_source_urls, verified_source_urls_for_items

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
PUBLISHED_AT_ROLE_BY_SOURCE_TYPE = {
    "rss": PublishedAtRole.FEED_ENTRY_TIME,
    "atom": PublishedAtRole.FEED_ENTRY_TIME,
    "hacker_news": PublishedAtRole.HN_SUBMISSION_TIME,
    "github": PublishedAtRole.GITHUB_RELEASE_PUBLISHED_TIME,
    "market": PublishedAtRole.MARKET_TRADING_DAY,
}
LOGGER = logging.getLogger(__name__)
BOUNDED_ANAPHORA_PATTERN = re.compile(
    r"^\s*(?:该|此)(?:版本|功能|模型|产品|工具|插件|服务|项目)"
)


class StoryValidationError(ValueError):
    pass


class LegacyStoryProvider(Protocol):
    """Narrow compatibility contract for evaluation-only legacy Story tests."""

    def merge_story(self, items: list[RawItem]) -> MergedStoryDraft: ...

    def score_story(self, story: Story): ...


def _effective_claim_type(claim: str, declared: ClaimType) -> ClaimType:
    lowered = claim.casefold()
    if re.search(r"(?:\bfirst\b|全球首次|世界首次|行业首次|首个)", lowered):
        return ClaimType.NOVELTY_FIRST
    if re.search(r"(?:\d+(?:\.\d+)?\s*[×x]|倍|benchmark|性能|更快)", lowered):
        return ClaimType.PERFORMANCE
    if re.search(r"(?:\bga\b|general availability|正式发布|全球发布)", lowered):
        return ClaimType.RELEASE_GA
    return declared


def _normalized_entity(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.casefold())


def _entity_matches(subject: str, candidates: list[str]) -> bool:
    expected = _normalized_entity(subject)
    return bool(expected) and any(
        _normalized_entity(candidate) == expected for candidate in candidates
    )


def _unique_evidence_subject_family(
    evidence: list[CandidateEvidence],
) -> str | None:
    """Return one stable Evidence subject without merging unrelated families."""
    families: list[tuple[set[str], set[str]]] = []
    for item in evidence:
        if item.authority is EvidenceAuthority.SELF_AUTHORITATIVE:
            aliases = item.authoritative_for
        elif item.authority is EvidenceAuthority.INDEPENDENT_REPORTING:
            normalized_subjects = {
                _normalized_entity(entity): entity
                for entity in item.subject_entities
                if _normalized_entity(entity)
            }
            if len(normalized_subjects) != 1:
                return None
            aliases = list(normalized_subjects.values())
        else:
            continue
        valid_aliases = [alias for alias in aliases if _normalized_entity(alias)]
        if not valid_aliases:
            continue
        normalized_aliases = {_normalized_entity(alias) for alias in valid_aliases}
        representatives = {valid_aliases[0]}
        overlapping = [
            index
            for index, (family_aliases, _) in enumerate(families)
            if family_aliases & normalized_aliases
        ]
        for index in reversed(overlapping):
            family_aliases, family_representatives = families.pop(index)
            normalized_aliases.update(family_aliases)
            representatives.update(family_representatives)
        families.append((normalized_aliases, representatives))
    if len(families) != 1:
        return None
    return min(
        families[0][1],
        key=lambda value: (_normalized_entity(value), value.casefold(), value),
    )


def _deterministic_claim_subject(
    claim: str,
    *,
    candidate_entities: list[str],
    evidence: list[CandidateEvidence],
) -> str | None:
    """Derive one grounded subject without trusting model-proposed metadata."""
    evidence_entities = [
        entity
        for item in evidence
        for entity in (
            item.authoritative_for
            if item.authority is EvidenceAuthority.SELF_AUTHORITATIVE
            else item.subject_entities
        )
    ]
    known_entities = list(dict.fromkeys([*candidate_entities, *evidence_entities]))
    normalized_claim = _normalized_entity(claim)
    mentioned = [
        entity
        for entity in known_entities
        if (normalized := _normalized_entity(entity)) and normalized in normalized_claim
    ]
    most_specific = [
        entity
        for entity in mentioned
        if not any(
            _normalized_entity(entity) != _normalized_entity(other)
            and _normalized_entity(entity) in _normalized_entity(other)
            for other in mentioned
        )
    ]
    normalized_matches = {
        _normalized_entity(entity): entity for entity in most_specific
    }
    if len(normalized_matches) == 1:
        return next(iter(normalized_matches.values()))

    claim_is_verbatim_evidence = any(
        normalized_claim
        and normalized_claim
        in {
            _normalized_entity(item.scope),
            _normalized_entity(item.excerpt),
        }
        for item in evidence
    )
    selected_evidence_entities = {
        _normalized_entity(entity): entity for entity in evidence_entities
    }
    if claim_is_verbatim_evidence and len(selected_evidence_entities) == 1:
        return next(iter(selected_evidence_entities.values()))
    if BOUNDED_ANAPHORA_PATTERN.search(claim):
        return _unique_evidence_subject_family(evidence)
    return None


def _inferred_claim_scope(claim: str, claim_type: ClaimType) -> ClaimScopeDimensions:
    lowered = claim.casefold()
    availability = AvailabilityScope.UNKNOWN
    temporal = TemporalScope.UNKNOWN
    assertion = AssertionScope.UNKNOWN
    if re.search(r"(?:\bga\b|general availability|全球可用|全面开放|正式可用)", lowered):
        availability = AvailabilityScope.GA
    elif re.search(r"(?:所有用户|全部用户|广泛可用|broad availability)", lowered):
        availability = AvailabilityScope.BROAD
    elif re.search(r"(?:部分用户|部分账户|some users|limited rollout)", lowered):
        availability = AvailabilityScope.SOME_USERS
    elif re.search(r"(?:我的账户|单个账户|one account)", lowered):
        availability = AvailabilityScope.ONE_ACCOUNT

    if re.search(r"(?:全球首次|世界首次|行业首次|首个|\bfirst ever\b)", lowered):
        temporal = TemporalScope.FIRST_EVER
    elif re.search(r"(?:今天.*(?:发布|推出|上线)|新发布|刚刚发布|newly released)", lowered):
        temporal = TemporalScope.NEWLY_RELEASED
    elif re.search(r"(?:当前存在|现已支持|现在支持|currently exists|now supports)", lowered):
        temporal = TemporalScope.CURRENTLY_EXISTS
    elif re.search(r"(?:观察到|已看到|observed)", lowered):
        temporal = TemporalScope.OBSERVED_NOW

    if any(marker in claim for marker in ("官方宣称", "官方称", "官方表示", "声称")):
        assertion = AssertionScope.OFFICIALLY_ANNOUNCED
    elif claim_type in {ClaimType.PERFORMANCE, ClaimType.NOVELTY_FIRST}:
        assertion = AssertionScope.INDEPENDENTLY_VERIFIED
    elif claim_type is ClaimType.RELEASE_GA:
        assertion = AssertionScope.OFFICIALLY_ANNOUNCED
    elif re.search(r"(?:观察到|已看到|我的账户|部分用户)", claim):
        assertion = AssertionScope.OBSERVED
    return ClaimScopeDimensions(
        availability=availability,
        temporal=temporal,
        assertion=assertion,
    )


def _requested_scope(
    claim: str,
    declared: ClaimType,
    proposed: ClaimScopeDimensions,
) -> ClaimScopeDimensions:
    inferred = _inferred_claim_scope(claim, declared)
    return ClaimScopeDimensions(
        # Availability breadth must be stated by the final fact. The model's
        # proposed scope remains diagnostic and cannot expand an unstated claim.
        availability=inferred.availability,
        temporal=(
            inferred.temporal
            if inferred.temporal is not TemporalScope.UNKNOWN
            else proposed.temporal
        ),
        assertion=(
            inferred.assertion
            if inferred.assertion is not AssertionScope.UNKNOWN
            else proposed.assertion
        ),
    )


def _availability_supports(evidence: AvailabilityScope, claim: AvailabilityScope) -> bool:
    if claim is AvailabilityScope.UNKNOWN:
        return True
    allowed = {
        AvailabilityScope.UNKNOWN: set(),
        AvailabilityScope.ONE_ACCOUNT: {
            AvailabilityScope.ONE_ACCOUNT,
            AvailabilityScope.SOME_USERS,
        },
        AvailabilityScope.SOME_USERS: {
            AvailabilityScope.ONE_ACCOUNT,
            AvailabilityScope.SOME_USERS,
        },
        AvailabilityScope.BROAD: {
            AvailabilityScope.ONE_ACCOUNT,
            AvailabilityScope.SOME_USERS,
            AvailabilityScope.BROAD,
        },
        AvailabilityScope.GA: set(AvailabilityScope) - {AvailabilityScope.UNKNOWN},
    }
    return claim in allowed[evidence]


def _temporal_supports(evidence: TemporalScope, claim: TemporalScope) -> bool:
    if claim is TemporalScope.UNKNOWN:
        return True
    allowed = {
        TemporalScope.UNKNOWN: set(),
        TemporalScope.OBSERVED_NOW: {TemporalScope.OBSERVED_NOW},
        TemporalScope.CURRENTLY_EXISTS: {
            TemporalScope.OBSERVED_NOW,
            TemporalScope.CURRENTLY_EXISTS,
        },
        TemporalScope.NEWLY_RELEASED: {
            TemporalScope.OBSERVED_NOW,
            TemporalScope.CURRENTLY_EXISTS,
            TemporalScope.NEWLY_RELEASED,
        },
        TemporalScope.FIRST_EVER: set(TemporalScope) - {TemporalScope.UNKNOWN},
    }
    return claim in allowed[evidence]


def _assertion_supports(evidence: AssertionScope, claim: AssertionScope) -> bool:
    if claim is AssertionScope.UNKNOWN:
        return True
    return claim in {
        AssertionScope.UNKNOWN: set(),
        AssertionScope.OBSERVED: {AssertionScope.OBSERVED},
        AssertionScope.OFFICIALLY_ANNOUNCED: {
            AssertionScope.OBSERVED,
            AssertionScope.OFFICIALLY_ANNOUNCED,
        },
        AssertionScope.INDEPENDENTLY_VERIFIED: {
            AssertionScope.OBSERVED,
            AssertionScope.INDEPENDENTLY_VERIFIED,
        },
    }[evidence]


def _firsthand_quality_is_sufficient(evidence: CandidateEvidence) -> bool:
    quality = evidence.observation_quality
    return bool(
        quality
        and quality.firsthandness
        and quality.specificity
        and quality.artifact_support
    )


def _evidence_supports_claim(
    evidence: CandidateEvidence,
    *,
    claim_subject: str | None,
    claim_type: ClaimType,
    requested: ClaimScopeDimensions,
) -> bool:
    if evidence.authority in {
        EvidenceAuthority.DISCOVERY_ONLY,
        EvidenceAuthority.UNVERIFIED_EXTERNAL,
    }:
        return False
    if evidence.authority is EvidenceAuthority.SELF_AUTHORITATIVE:
        if not claim_subject or not _entity_matches(
            claim_subject, evidence.authoritative_for
        ):
            return False
    elif evidence.authority is EvidenceAuthority.INDEPENDENT_REPORTING:
        if not claim_subject or not _entity_matches(
            claim_subject, evidence.subject_entities
        ):
            return False
    elif claim_subject and evidence.subject_entities and not _entity_matches(
        claim_subject, evidence.subject_entities
    ):
        return False
    if evidence.authority is EvidenceAuthority.FIRSTHAND_OBSERVATION and (
        claim_type in {ClaimType.AVAILABILITY, ClaimType.FIRSTHAND_BEHAVIOR}
        or requested.availability is not AvailabilityScope.UNKNOWN
        or requested.assertion is AssertionScope.OBSERVED
    ) and not _firsthand_quality_is_sufficient(evidence):
        return False
    support = evidence.support_scope
    return (
        _availability_supports(support.availability, requested.availability)
        and _temporal_supports(support.temporal, requested.temporal)
        and _assertion_supports(support.assertion, requested.assertion)
    )


def _validate_candidate_story_draft(
    candidate: Candidate, draft: MergedStoryDraft
) -> dict[str, str]:
    if not draft.facts:
        raise StoryValidationError("Story Construction returned no supported facts")
    evidence_by_id = {item.evidence_id: item for item in candidate.evidence}
    support_by_claim = {support.claim: support for support in draft.fact_supports}
    if (
        len(draft.fact_supports) != len(draft.facts)
        or len(set(draft.facts)) != len(draft.facts)
        or set(support_by_claim) != set(draft.facts)
    ):
        raise StoryValidationError("every Story fact requires one exact claim support")
    if candidate.evidence_state is EvidenceState.CONTRADICTED and not all(
        any(marker in fact for marker in ("但", "冲突", "不一致", "部分", "尚未明确"))
        and len(support_by_claim[fact].evidence_ids) >= 2
        for fact in draft.facts
    ):
        raise StoryValidationError(
            "contradicted Evidence may only support an explicitly bounded conflict Story"
        )
    claim_subjects: dict[str, str] = {}
    for fact in draft.facts:
        support = support_by_claim[fact]
        if not set(support.evidence_ids).issubset(evidence_by_id):
            raise StoryValidationError("claim support references unknown Evidence")
        evidence = [evidence_by_id[item_id] for item_id in support.evidence_ids]
        claim_type = _effective_claim_type(fact, support.claim_type)
        requested = _requested_scope(fact, claim_type, support.requested_scope)
        claim_subject = _deterministic_claim_subject(
            fact,
            candidate_entities=candidate.entity_names,
            evidence=evidence,
        )
        if claim_subject is None:
            raise StoryValidationError(
                "Claim subject could not be derived from Candidate/Evidence"
            )
        if not any(
            _evidence_supports_claim(
                item,
                claim_subject=claim_subject,
                claim_type=claim_type,
                requested=requested,
            )
            for item in evidence
        ):
            raise StoryValidationError(
                "Claim Scope or authority is incompatible with Evidence support"
            )
        claim_subjects[fact] = claim_subject
        if claim_type is ClaimType.RELEASE_GA and all(
            item.authority is EvidenceAuthority.FIRSTHAND_OBSERVATION for item in evidence
        ):
            raise StoryValidationError("firsthand observation cannot prove official release/GA")
        if claim_type is ClaimType.NOVELTY_FIRST and not any(
            item.authority is EvidenceAuthority.INDEPENDENT_REPORTING for item in evidence
        ):
            raise StoryValidationError("novelty/first claim requires independent Evidence")
        if claim_type is ClaimType.PERFORMANCE and all(
            item.authority is EvidenceAuthority.SELF_AUTHORITATIVE for item in evidence
        ) and not any(marker in fact for marker in ("宣称", "官方称", "表示", "声称")):
            raise StoryValidationError(
                "self-reported performance must be attributed as an official claim"
            )
    allowed_urls = {item.url for item in candidate.evidence}
    if not draft.source_urls or not set(draft.source_urls).issubset(allowed_urls):
        raise StoryValidationError("Story URLs must come from Candidate Evidence")
    if draft.primary_source_url not in draft.source_urls:
        raise StoryValidationError("Story primary URL must be one of its Evidence URLs")
    return claim_subjects


def story_evidence_integrity_violations(story: Story) -> list[str]:
    """Recheck persisted Story claims with the production deterministic boundary."""
    violations: list[str] = []
    evidence_by_id = {item.evidence_id: item for item in story.evidence_refs}
    for support in story.claim_supports:
        evidence_ids = set(support.evidence_ids)
        if not evidence_ids or not evidence_ids.issubset(evidence_by_id):
            violations.append(f"{support.claim}: unknown Evidence reference")
            continue
        claim_type = _effective_claim_type(support.claim, support.claim_type)
        requested = _requested_scope(
            support.claim,
            claim_type,
            support.requested_scope,
        )
        selected_evidence = [evidence_by_id[evidence_id] for evidence_id in evidence_ids]
        claim_subject = _deterministic_claim_subject(
            support.claim,
            candidate_entities=[],
            evidence=selected_evidence,
        )
        if claim_subject != support.claim_subject:
            violations.append(f"{support.claim}: non-deterministic Claim subject")
            continue
        if not any(
            _evidence_supports_claim(
                evidence_by_id[evidence_id],
                claim_subject=claim_subject,
                claim_type=claim_type,
                requested=requested,
            )
            for evidence_id in evidence_ids
        ):
            violations.append(f"{support.claim}: incompatible Claim/Evidence scope")
    evidence_urls = {item.url for item in story.evidence_refs}
    if not set(story.source_urls).issubset(evidence_urls):
        violations.append("Story contains URL outside persisted Evidence set")
    return violations


def filter_story_candidate_inputs(
    items: list[RawItem],
    *,
    market_movement_threshold: float,
) -> tuple[list[RawItem], int]:
    """Suppress routine market rows from Story AI without dropping raw data."""
    selected: list[RawItem] = []
    suppressed = 0
    for item in items:
        if item.source_type != "market":
            selected.append(item)
            continue
        change = item.metadata.get("change_percent")
        valid_change = (
            isinstance(change, (int, float))
            and not isinstance(change, bool)
            and math.isfinite(change)
        )
        if valid_change and abs(change) < market_movement_threshold:
            suppressed += 1
            continue
        selected.append(item)
    return selected, suppressed


def _story_id(items: list[RawItem]) -> str:
    identity = "|".join(sorted(item.id for item in items))
    return f"story-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


def choose_primary_source(items: list[RawItem]) -> RawItem:
    """Choose a source deterministically; AI does not control canonical links."""

    def source_key(item: RawItem) -> tuple[int, int, int, str]:
        official = bool(item.metadata.get("official"))
        priority = str(item.metadata.get("priority", "low"))
        has_time = item.published_at is not None
        return (
            0 if official else 1,
            PRIORITY_ORDER.get(priority, 3),
            0 if has_time else 1,
            item.url,
        )

    if not items:
        raise ValueError("cannot choose a primary source from no items")
    return min(items, key=source_key)


def _validate_draft_urls(draft: MergedStoryDraft, items: list[RawItem]) -> None:
    allowed = set(verified_source_urls_for_items(items))
    returned = set(draft.source_urls)
    if draft.primary_source_url:
        returned.add(draft.primary_source_url)
    invented = returned - allowed
    if invented:
        raise StoryValidationError(
            "AI story draft contains URL outside verified source set: "
            f"{sorted(invented)[0]}"
        )


def _source_ref(item: RawItem) -> StorySourceRef:
    """Snapshot collector context without broadening verified URL provenance."""
    discussion_url = None
    if item.source_type == "hacker_news":
        candidate = item.metadata.get("discussion_url")
        if isinstance(candidate, str) and candidate in verified_source_urls(item):
            discussion_url = candidate
    return StorySourceRef(
        raw_item_id=item.id,
        title=item.title,
        source_name=item.source_name,
        source_type=item.source_type,
        url=item.url,
        author=item.author,
        # This is the collected source's time (HN submission time for HN),
        # not a claimed original-article or underlying-event time.
        published_at=item.published_at,
        published_at_role=PUBLISHED_AT_ROLE_BY_SOURCE_TYPE.get(
            item.source_type,
            PublishedAtRole.UNKNOWN,
        ),
        fetched_at=item.fetched_at,
        discussion_url=discussion_url,
        source_role=item.source_role,
        statement_type=item.statement_type,
        practice_signal_kind=item.practice_signal_kind,
    )


def build_story(
    items: list[RawItem],
    *,
    provider: LegacyStoryProvider,
    now: datetime,
) -> Story:
    draft = provider.merge_story(items)
    _validate_draft_urls(draft, items)
    primary = choose_primary_source(items)
    source_urls = list(verified_source_urls_for_items(items))
    published_values = [item.published_at for item in items if item.published_at is not None]
    provisional = Story(
        id=_story_id(items),
        canonical_title=draft.canonical_title,
        category=draft.category,
        entity_names=draft.entity_names,
        product_names=draft.product_names,
        topic_names=draft.topic_names,
        published_at=min(published_values) if published_values else None,
        updated_at=now,
        source_item_ids=[item.id for item in items],
        source_urls=source_urls,
        primary_source_url=primary.url,
        source_refs=[_source_ref(item) for item in items],
        facts=draft.facts,
        analysis=draft.analysis,
        uncertainties=draft.uncertainties,
        relevance_score=0,
        importance_score=0,
        novelty_score=0,
        credibility_score=0,
        status=draft.status,
    )
    score = provider.score_story(provisional)
    return provisional.model_copy(
        update={
            "relevance_score": score.relevance_score,
            "importance_score": score.importance_score,
            "novelty_score": score.novelty_score,
            "credibility_score": score.credibility_score,
        }
    )


def build_candidate_story(
    candidate: Candidate,
    *,
    raw_items: list[RawItem],
    provider: AIProvider,
    now: datetime,
) -> Story:
    """Construct a Story only after validating Claim × Evidence support."""
    if candidate.semantic_disposition is not SemanticDisposition.BUILD:
        raise StoryValidationError("only BUILD Candidates may attempt Story Construction")
    items_by_id = {item.id: item for item in raw_items}
    items = [items_by_id[item_id] for item_id in candidate.raw_item_ids if item_id in items_by_id]
    if not items:
        raise StoryValidationError("Candidate has no available RawItem provenance")
    draft = provider.construct_story(candidate)
    claim_subjects = _validate_candidate_story_draft(candidate, draft)
    published_values = [item.published_at for item in items if item.published_at is not None]
    provisional = Story(
        id=f"story-{hashlib.sha256(candidate.id.encode()).hexdigest()[:20]}",
        canonical_title=draft.canonical_title,
        category=draft.category,
        entity_names=draft.entity_names,
        product_names=draft.product_names,
        topic_names=draft.topic_names,
        published_at=min(published_values) if published_values else None,
        updated_at=now,
        source_item_ids=[item.id for item in items],
        source_urls=list(
            dict.fromkeys([*draft.source_urls, *verified_source_urls_for_items(items)])
        ),
        primary_source_url=draft.primary_source_url or draft.source_urls[0],
        source_refs=[_source_ref(item) for item in items],
        candidate_ids=[candidate.id],
        evidence_refs=candidate.evidence,
        claim_supports=[
            {
                "claim": item.claim,
                "claim_subject": claim_subjects[item.claim],
                "claim_type": item.claim_type,
                "evidence_ids": item.evidence_ids,
                "requested_scope": item.requested_scope,
                "evidence_scope": item.evidence_scope,
                "claim_scope": item.claim_scope,
                "scope_supported": item.scope_supported,
            }
            for item in draft.fact_supports
        ],
        facts=draft.facts,
        analysis=draft.analysis,
        uncertainties=draft.uncertainties,
        relevance_score=0,
        importance_score=0,
        novelty_score=0,
        credibility_score=0,
        status=draft.status,
    )
    score = provider.score_story(provisional)
    return provisional.model_copy(
        update={
            "relevance_score": score.relevance_score,
            "importance_score": score.importance_score,
            "novelty_score": score.novelty_score,
            "credibility_score": score.credibility_score,
        }
    )


def build_candidate_stories(
    candidates: list[Candidate],
    *,
    raw_items: list[RawItem],
    provider: AIProvider,
    now: datetime,
    maximum_candidates: int,
) -> tuple[list[Story], dict[str, str]]:
    """Spend Story resources independently from Triage capacity."""
    build_candidates = [
        candidate
        for candidate in candidates
        if candidate.semantic_disposition is SemanticDisposition.BUILD
    ]
    build_candidates.sort(
        key=lambda candidate: (
            candidate.evidence_state.value != "sufficient",
            not candidate.must_triage,
            -candidate.investigation_priority,
            candidate.id,
        )
    )
    stories: list[Story] = []
    dispositions: dict[str, str] = {}
    bounded = build_candidates[:maximum_candidates]
    for index, candidate in enumerate(bounded):
        try:
            stories.append(
                build_candidate_story(
                    candidate,
                    raw_items=raw_items,
                    provider=provider,
                    now=now,
                )
            )
        except AIBudgetExceeded:
            LOGGER.warning("Story budget deferred Candidate %s", candidate.id)
            for deferred in bounded[index:]:
                dispositions[deferred.id] = "STORY_DEFERRED_BY_BUDGET"
            break
        except (AIOutputError, OSError):
            LOGGER.exception("Story Construction AI failed for Candidate %s", candidate.id)
            dispositions[candidate.id] = "STORY_FAILED_AI"
        except (StoryValidationError, ValueError):
            LOGGER.exception("Story Construction rejected Candidate %s", candidate.id)
            dispositions[candidate.id] = "STORY_REJECTED"
        else:
            dispositions[candidate.id] = "STORY_BUILT"
    for candidate in build_candidates[maximum_candidates:]:
        dispositions[candidate.id] = "STORY_DEFERRED_BY_BUDGET"
    return rank_stories(stories), dispositions


def ranking_score(story: Story) -> float:
    return (
        story.importance_score * 0.4
        + story.relevance_score * 0.3
        + story.credibility_score * 0.2
        + story.novelty_score * 0.1
    )


def rank_stories(stories: list[Story]) -> list[Story]:
    return sorted(
        stories,
        key=lambda story: (ranking_score(story), story.updated_at, story.id),
        reverse=True,
    )

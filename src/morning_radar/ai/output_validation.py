"""Conservative validation for user-visible structured AI narratives."""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable

from pydantic import BaseModel

from morning_radar.ai.models import (
    BriefDraft,
    ContinuityResolution,
    DirectionObservation,
    GeneratedJudgementDraft,
    GeneratedWatchDraft,
    MergedStoryDraft,
    ResearchResolutionBatch,
    TendencyEvaluationBatch,
)
from morning_radar.editorial.models import EditorialDecisionBatch
from morning_radar.models import Signal, Story

LOGGER = logging.getLogger(__name__)

_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_CODE_SPAN = re.compile(r"`[^`]*`", re.DOTALL)
_ENGLISH_WORD = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_CODE_STRUCTURE = re.compile(r"(?:\w+\[[^\]]+\]|[A-Za-z_]\w*\([^)]*\)\s*(?:->|:))")
_CODE_DECLARATION = re.compile(
    r"(?:\b(?:def|function)\s+[A-Za-z_$]\w*\s*\([^)]*\)\s*(?:\{|:)"
    r"|\bclass\s+[A-Za-z_$]\w*(?:\([^)]*\))?\s*(?:\{|:)"
    r"|\b(?:const|let|var)\s+[A-Za-z_$]\w*\s*="
    r"|=>)"
)
_GENERIC_ANCHORS = {
    "ai",
    "artificial intelligence",
    "education",
    "startup",
    "tech",
    "technology",
    "人工智能",
}
_GENERIC_INSIGHT_PATTERNS = (
    re.compile(r"^(?:人工智能|AI)行业(?:正在|仍在|继续)?快速发展[。.]?$", re.IGNORECASE),
    re.compile(r"^(?:人工智能|AI)智能体(?:正变得|越来越)?重要[。.]?$", re.IGNORECASE),
    re.compile(r"^模型竞争(?:正在|将会)?(?:持续)?加剧[。.]?$", re.IGNORECASE),
)


def is_suspicious_english_prose(value: str) -> bool:
    """Detect long English prose while allowing proper nouns, URLs, and code."""
    cleaned = _URL.sub(" ", _CODE_SPAN.sub(" ", value)).strip()
    if len(cleaned) < 28:
        return False
    if _CODE_STRUCTURE.search(cleaned) or _CODE_DECLARATION.search(cleaned):
        return False
    english_words = _ENGLISH_WORD.findall(cleaned)
    if len(english_words) < 6:
        return False
    cjk_count = len(_CJK.findall(cleaned))
    if cjk_count > 2:
        return False
    latin_count = len(_LATIN.findall(cleaned))
    narrative_characters = latin_count + cjk_count
    return narrative_characters > 0 and latin_count / narrative_characters >= 0.8


def validate_simplified_chinese_output(output: BaseModel) -> None:
    for value in _user_visible_narratives(output):
        if is_suspicious_english_prose(value):
            raise ValueError("User-visible AI narrative contains obvious English prose")


def validate_core_simplified_chinese_output(output: BaseModel) -> None:
    narratives = (
        _brief_item_narratives(output)
        if isinstance(output, BriefDraft)
        else _user_visible_narratives(output)
    )
    for value in narratives:
        if is_suspicious_english_prose(value):
            raise ValueError("User-visible AI narrative contains obvious English prose")


def validate_direction_evidence(
    output: DirectionObservation,
    signals: list[Signal],
) -> None:
    evidence_ids = set(output.evidence_story_ids)
    if output.observation is None:
        if evidence_ids:
            raise ValueError("Empty direction observation must not claim evidence")
        return
    if len(evidence_ids) < 2:
        raise ValueError("Direction observation requires at least two evidence stories")
    if not any(evidence_ids.issubset(set(signal.supporting_story_ids)) for signal in signals):
        raise ValueError("Direction evidence must belong to one input Signal")


def validate_brief_references(output: BriefDraft, stories: list[Story]) -> None:
    """Require each Brief item to reference only its actual input Stories."""
    stories_by_id = {story.id: story for story in stories}
    for item_index, item in enumerate(output.items):
        if not item.story_ids:
            _reject_brief_references(f"item {item_index} has empty story_ids")

        unknown_ids = [story_id for story_id in item.story_ids if story_id not in stories_by_id]
        if unknown_ids:
            _reject_brief_references(
                f"item {item_index} has unknown Story IDs: {', '.join(unknown_ids)}"
            )

        if not item.source_urls:
            _reject_brief_references(f"item {item_index} has empty source_urls")

        referenced_urls = {
            url for story_id in item.story_ids for url in stories_by_id[story_id].source_urls
        }
        mismatched_urls = [url for url in item.source_urls if url not in referenced_urls]
        if mismatched_urls:
            _reject_brief_references(
                "item "
                f"{item_index} source URLs do not match its Story IDs: "
                f"{', '.join(mismatched_urls)}"
            )


def sanitize_memory_drafts(output: BriefDraft, stories: list[Story]) -> BriefDraft:
    """Drop invalid optional memory drafts without weakening core Brief items."""
    stories_by_id = {story.id: story for story in stories}
    valid_watches: list[GeneratedWatchDraft] = []
    valid_judgements: list[GeneratedJudgementDraft] = []
    dropped: Counter[str] = Counter()
    for watch in output.watch_items:
        referenced = [stories_by_id.get(story_id) for story_id in watch.source_story_ids]
        if not referenced or any(story is None for story in referenced):
            dropped["watch_story_reference"] += 1
            continue
        allowed_entities = {value for story in referenced for value in story.entity_names}
        allowed_products = {value for story in referenced for value in story.product_names}
        allowed_topics = {value for story in referenced for value in story.topic_names}
        if not any((watch.entity_anchors, watch.product_anchors, watch.topic_anchors)):
            dropped["watch_anchor"] += 1
            continue
        if (
            not set(watch.entity_anchors).issubset(allowed_entities)
            or not set(watch.product_anchors).issubset(allowed_products)
            or not set(watch.topic_anchors).issubset(allowed_topics)
        ):
            dropped["watch_anchor"] += 1
            continue
        normalized_anchors = {
            _normalize_anchor(value)
            for value in (
                *watch.entity_anchors,
                *watch.product_anchors,
                *watch.topic_anchors,
            )
        }
        normalized_expectation = _normalize_anchor(watch.expectation)
        if (
            is_suspicious_english_prose(watch.expectation)
            or _is_generic_insight(watch.expectation)
            or not any(
                _anchor_matches(anchor, normalized_expectation) for anchor in normalized_anchors
            )
        ):
            dropped["watch_platitude"] += 1
            continue
        valid_watches.append(watch)

    for judgement in output.judgements:
        referenced = [stories_by_id.get(story_id) for story_id in judgement.evidence_story_ids]
        anchors = {
            _normalize_anchor(value)
            for story in referenced
            if story is not None
            for value in (*story.entity_names, *story.product_names, *story.topic_names)
            if len(_normalize_anchor(value)) >= 3
        }
        normalized_claim = _normalize_anchor(judgement.claim)
        explicit_gate = (
            judgement.falsifiable is True
            and judgement.changes_future_interpretation is True
            and judgement.correction_required_if_false is True
            and judgement.expected_lifetime_days >= 2
            and bool(judgement.loss_if_unmentioned_30d.strip())
        )
        if (
            not explicit_gate
            or any(story_id not in stories_by_id for story_id in judgement.evidence_story_ids)
            or len(judgement.claim.strip()) < 20
            or is_suspicious_english_prose(judgement.claim)
            or is_suspicious_english_prose(judgement.rationale)
            or _is_generic_insight(judgement.claim)
            or not any(_anchor_matches(anchor, normalized_claim) for anchor in anchors)
        ):
            dropped["judgement_contract"] += 1
            continue
        valid_judgements.append(judgement)
    if dropped:
        LOGGER.warning(
            "Dropped invalid optional memory drafts: %s",
            ",".join(f"{key}:{value}" for key, value in sorted(dropped.items())),
        )
    return output.model_copy(update={"watch_items": valid_watches, "judgements": valid_judgements})


def validate_and_sanitize_brief(
    output: BriefDraft,
    stories: list[Story],
    signals: list[Signal],
) -> BriefDraft:
    """Validate core references before independently degrading optional fields."""
    validate_brief_references(output, stories)
    sanitized = sanitize_editorial_extensions(output, stories, signals)
    return sanitize_memory_drafts(sanitized, stories)


def _reject_brief_references(message: str) -> None:
    LOGGER.warning("Rejected invalid Brief references: %s", message)
    raise ValueError(message)


def validate_editorial_grounding(
    output: BriefDraft,
    stories: list[Story],
    signals: list[Signal],
) -> None:
    anchors = _grounding_anchors(stories, signals)
    narratives = [*output.watch_next]
    if output.cognitive_extension:
        narratives.append(output.cognitive_extension)
    for narrative in narratives:
        normalized = _normalize_anchor(narrative)
        if not any(_anchor_matches(anchor, normalized) for anchor in anchors):
            raise ValueError(
                "Editorial extension must name a concrete input entity, product, or topic"
            )
    if output.cognitive_extension and not output.cognitive_extension.rstrip().endswith(("?", "？")):
        raise ValueError("Cognitive extension must be framed as a question")


def sanitize_editorial_extensions(
    output: BriefDraft,
    stories: list[Story],
    signals: list[Signal],
) -> BriefDraft:
    """Drop invalid optional extensions without weakening core Brief validation."""
    anchors = _grounding_anchors(stories, signals)
    valid_watch: list[str] = []
    watch_reasons: Counter[str] = Counter()
    for narrative in output.watch_next:
        reason = _extension_failure_reason(narrative, anchors=anchors)
        if reason is None:
            valid_watch.append(narrative)
        else:
            watch_reasons[reason] += 1

    cognitive = output.cognitive_extension
    cognitive_reason = (
        _extension_failure_reason(cognitive, anchors=anchors, require_question=True)
        if cognitive
        else None
    )
    if cognitive_reason is not None:
        cognitive = None

    diagnostics: list[str] = []
    if watch_reasons:
        reasons = ",".join(f"{reason}:{count}" for reason, count in sorted(watch_reasons.items()))
        diagnostics.append(f"watch_next={reasons}")
    if cognitive_reason is not None:
        diagnostics.append(f"cognitive_extension={cognitive_reason}")
    if diagnostics:
        LOGGER.warning(
            "Dropped invalid optional Brief extensions: %s",
            " ".join(diagnostics),
        )

    return output.model_copy(
        update={
            "watch_next": valid_watch,
            "cognitive_extension": cognitive,
        }
    )


def _extension_failure_reason(
    narrative: str,
    *,
    anchors: set[str],
    require_question: bool = False,
) -> str | None:
    if is_suspicious_english_prose(narrative):
        return "language"
    normalized = _normalize_anchor(narrative)
    if not any(_anchor_matches(anchor, normalized) for anchor in anchors):
        return "grounding"
    if require_question and not narrative.rstrip().endswith(("?", "？")):
        return "question_contract"
    return None


def _grounding_anchors(stories: list[Story], signals: list[Signal]) -> set[str]:
    values = {
        value
        for story in stories
        for value in (*story.entity_names, *story.product_names, *story.topic_names)
    }
    values.update(signal.topic for signal in signals)
    anchors = {_normalize_anchor(value) for value in values}
    return {anchor for anchor in anchors if len(anchor) >= 3 and anchor not in _GENERIC_ANCHORS}


def _normalize_anchor(value: str) -> str:
    return " ".join(re.split(r"[_\s-]+", value.casefold())).strip()


def _anchor_matches(anchor: str, narrative: str) -> bool:
    if _CJK.search(anchor):
        return anchor in narrative
    return re.search(rf"(?<![a-z0-9]){re.escape(anchor)}(?![a-z0-9])", narrative) is not None


def _is_generic_insight(value: str) -> bool:
    normalized = " ".join(value.split()).strip()
    return any(pattern.fullmatch(normalized) for pattern in _GENERIC_INSIGHT_PATTERNS)


def _present(values: Iterable[str | None]) -> Iterable[str]:
    return (value for value in values if value)


def _user_visible_narratives(output: BaseModel) -> Iterable[str]:
    if isinstance(output, MergedStoryDraft):
        yield output.canonical_title
        yield from output.facts
        yield from output.analysis
        yield from output.opinions
        yield from output.uncertainties
    elif isinstance(output, BriefDraft):
        yield from _brief_item_narratives(output)
        yield from (watch.expectation for watch in output.watch_items)
        for judgement in output.judgements:
            yield judgement.claim
            yield judgement.rationale
            yield from _present((judgement.uncertainty,))
        yield from output.watch_next
        yield from _present((output.cognitive_extension,))
    elif isinstance(output, DirectionObservation):
        yield from _present((output.observation,))
        yield from output.uncertainties
    elif isinstance(output, ContinuityResolution):
        for relation in output.relations:
            yield relation.rationale
            yield from _present((relation.what_changed,))
        for match in output.watch_matches:
            yield match.rationale
        for update in output.judgement_updates:
            yield update.claim
            yield update.rationale
            yield from _present((update.uncertainty,))
    elif isinstance(output, ResearchResolutionBatch):
        for case in output.cases:
            yield case.claim
            yield from _present((case.why_notable, case.uncertainty))
            yield from case.missing_evidence
    elif isinstance(output, TendencyEvaluationBatch):
        for decision in output.decisions:
            yield decision.claim
            yield decision.assessment.shared_mechanism
            yield decision.assessment.baseline
            yield decision.assessment.falsifier
            yield from decision.assessment.observable_impacts
            yield decision.assessment.decision_rationale
            yield from _present((decision.assessment.formation_exception_rationale,))
    elif isinstance(output, EditorialDecisionBatch):
        for decision in output.decisions:
            yield decision.reason


def _brief_item_narratives(output: BriefDraft) -> Iterable[str]:
    for item in output.items:
        yield item.title
        yield item.what_happened
        yield item.why_it_matters
        yield from _present((item.market_or_community_reaction, item.uncertainty))

"""Conservative validation for user-visible structured AI narratives."""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel

from morning_radar.ai.models import BriefDraft, DirectionObservation, MergedStoryDraft
from morning_radar.models import Signal, Story

_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_CODE_SPAN = re.compile(r"`[^`]*`", re.DOTALL)
_ENGLISH_WORD = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_CODE_STRUCTURE = re.compile(
    r"(?:\w+\[[^\]]+\]|[A-Za-z_]\w*\([^)]*\)\s*(?:->|:))"
)
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
    if not any(
        evidence_ids.issubset(set(signal.supporting_story_ids))
        for signal in signals
    ):
        raise ValueError("Direction evidence must belong to one input Signal")


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
    if output.cognitive_extension and not output.cognitive_extension.rstrip().endswith(
        ("?", "？")
    ):
        raise ValueError("Cognitive extension must be framed as a question")


def _grounding_anchors(stories: list[Story], signals: list[Signal]) -> set[str]:
    values = {
        value
        for story in stories
        for value in (*story.entity_names, *story.product_names, *story.topic_names)
    }
    values.update(signal.topic for signal in signals)
    anchors = {_normalize_anchor(value) for value in values}
    return {
        anchor
        for anchor in anchors
        if len(anchor) >= 3 and anchor not in _GENERIC_ANCHORS
    }


def _normalize_anchor(value: str) -> str:
    return " ".join(re.split(r"[_\s-]+", value.casefold())).strip()


def _anchor_matches(anchor: str, narrative: str) -> bool:
    if _CJK.search(anchor):
        return anchor in narrative
    return re.search(rf"(?<![a-z0-9]){re.escape(anchor)}(?![a-z0-9])", narrative) is not None


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
        for item in output.items:
            yield item.title
            yield item.what_happened
            yield item.why_it_matters
            yield from _present(
                (item.market_or_community_reaction, item.uncertainty)
            )
        yield from output.watch_next
        yield from _present((output.cognitive_extension,))
    elif isinstance(output, DirectionObservation):
        yield from _present((output.observation,))
        yield from output.uncertainties

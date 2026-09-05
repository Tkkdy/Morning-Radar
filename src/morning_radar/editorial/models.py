"""Validated schemas for portable editorial decisions and stored batches."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import Field, PrivateAttr, field_validator, model_validator

from morning_radar.models.core import RadarModel, _validate_aware_datetime


class Placement(StrEnum):
    TOP = "TOP"
    STORY = "STORY"
    NEWS = "NEWS"
    ONE_LINER = "ONE-LINER"
    SUPPORT = "SUPPORT"
    DROP = "DROP"


class Treatment(StrEnum):
    DEEP_STORY = "deep_story"
    SHORT_NEWS = "short_news"
    ONE_LINER = "one_liner"
    SUPPORT_ONLY = "support_only"
    HIDDEN = "hidden"


class FactStatus(StrEnum):
    CLAIM = "claim"
    VERIFIED_FACT = "verified_fact"
    INFERENCE = "inference"
    MIXED = "mixed"


class DecisionReason(StrEnum):
    DEVELOPER_PRODUCTION_IMPACT = "developer_production_impact"
    LOWER_TECHNICAL_BARRIER = "lower_technical_barrier"
    UNIVERSAL_PRIMITIVE = "universal_primitive"
    PRACTITIONER_EVIDENCE = "practitioner_evidence"
    LARGE_USER_IMPACT = "large_user_impact"
    STRUCTURAL_BUSINESS_CHANGE = "structural_business_change"
    AI_ECOSYSTEM_LANDMARK = "ai_ecosystem_landmark"
    TREND_CONFIRMATION = "trend_confirmation"
    INDEPENDENT_VERIFICATION = "independent_verification"
    CREDIBLE_MULTI_VENDOR_COMMITMENT = "credible_multi_vendor_commitment"
    NICHE_PENETRATION = "niche_penetration"
    RELATIVE_MAGNITUDE = "relative_magnitude"
    MACRO_MARKET_REGIME = "macro_market_regime"
    MINOR_NEWS_DELTA = "minor_news_delta"
    UNVERIFIED_CLAIM = "unverified_claim"
    POLICY_BACKGROUND_ONLY = "policy_background_only"
    DEMONSTRATED_POLICY_IMPACT = "demonstrated_policy_impact"
    EXTREME_MARKET_MOVE = "extreme_market_move"
    TRANSIENT_AND_RESOLVED = "transient_and_resolved"
    ALREADY_WIDELY_KNOWN = "already_widely_known"
    SUPPORTING_EVIDENCE_ONLY = "supporting_evidence_only"


_ALLOWED_TREATMENTS = {
    Placement.TOP: {Treatment.DEEP_STORY, Treatment.SHORT_NEWS},
    Placement.STORY: {Treatment.DEEP_STORY, Treatment.SHORT_NEWS},
    Placement.NEWS: {Treatment.SHORT_NEWS, Treatment.ONE_LINER},
    Placement.ONE_LINER: {Treatment.ONE_LINER},
    Placement.SUPPORT: {Treatment.SUPPORT_ONLY},
    Placement.DROP: {Treatment.HIDDEN},
}


class EditorialDecision(RadarModel):
    _legacy_treatment: Treatment | None = PrivateAttr(default=None)

    def __init__(self, **data: object) -> None:
        treatment = data.get("treatment")
        super().__init__(**data)
        self._legacy_treatment = Treatment(treatment) if treatment is not None else None

    @property
    def treatment(self) -> Treatment:
        if self._legacy_treatment is not None:
            return self._legacy_treatment
        return {
            Placement.TOP: Treatment.DEEP_STORY,
            Placement.STORY: Treatment.DEEP_STORY,
            Placement.NEWS: Treatment.SHORT_NEWS,
            Placement.ONE_LINER: Treatment.ONE_LINER,
            Placement.SUPPORT: Treatment.SUPPORT_ONLY,
            Placement.DROP: Treatment.HIDDEN,
        }[self.placement]

    story_id: str = Field(min_length=1)
    placement: Placement
    reader_value: int = Field(ge=0, le=4)
    evidence_value: int = Field(ge=0, le=4)
    fact_status: FactStatus
    retain_for_trends: bool
    trend_links: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=240)
    support_for_story_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def absorb_legacy_runtime_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        treatment = data.pop("treatment", None)
        placement = data.get("placement")
        if treatment is not None and placement is not None:
            try:
                if Treatment(treatment) not in _ALLOWED_TREATMENTS[Placement(placement)]:
                    raise ValueError(f"treatment {treatment} is invalid for placement {placement}")
            except KeyError:
                pass
        reasons = data.get("decision_reasons", [])
        if "trend_confirmation" in [str(reason) for reason in reasons] and not data.get(
            "retain_for_trends"
        ):
            raise ValueError("trend_confirmation must be retained for trends")
        if not data.get("reason"):
            reasons = data.pop("decision_reasons", [])
            data["reason"] = (
                data.get("why_now")
                or data.get("news_delta")
                or (str(reasons[0]) if reasons else "兼容历史编辑判断。")
            )
        for name in (
            "causal_confidence",
            "context_snapshot",
            "news_delta",
            "why_now",
            "uncertainty",
            "editorial_confidence",
            "decision_reasons",
        ):
            data.pop(name, None)
        return data

    @model_validator(mode="after")
    def validate_placement_contract(self) -> EditorialDecision:
        if self.placement is Placement.SUPPORT:
            if self.support_for_story_id is None:
                raise ValueError("SUPPORT requires support_for_story_id")
        elif self.support_for_story_id is not None:
            raise ValueError("support_for_story_id is only valid for SUPPORT")
        if self.retain_for_trends and not self.trend_links:
            raise ValueError("retained editorial evidence requires a trend link")
        if not self.retain_for_trends and self.trend_links:
            raise ValueError("non-retained editorial evidence cannot have trend links")
        if self.evidence_value >= 3 and not self.retain_for_trends:
            raise ValueError("evidence_value >= 3 must be retained for trends")
        if self.evidence_value <= 1 and self.retain_for_trends:
            raise ValueError("evidence_value <= 1 cannot be retained for trends")
        return self


class EditorialDecisionBatch(RadarModel):
    decisions: list[EditorialDecision] = Field(default_factory=list)


class DailyEditorialDecisions(RadarModel):
    date: date
    generated_at: datetime
    profile_version: str = Field(min_length=1)
    enabled: bool
    shadow_mode: bool
    degraded: bool = False
    degradation_reason: str | None = None
    decisions: list[EditorialDecision] = Field(default_factory=list)

    _generated_is_aware = field_validator("generated_at")(_validate_aware_datetime)

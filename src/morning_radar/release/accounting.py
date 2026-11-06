"""Persist physical-call intent before transport; stop on uncertain daily cost."""

from __future__ import annotations

import json
from decimal import Decimal
from threading import RLock

import yaml
from pydantic import Field

from morning_radar.ai.budget import AIBudgetExceeded
from morning_radar.ai.usage import PriceQuote, usage_summary
from morning_radar.models.core import RadarModel


class ReleasePolicy(RadarModel):
    daily_limit_cny: Decimal = Field(default=Decimal("0.4"), gt=0)
    per_call_reserve_cny: Decimal = Field(default=Decimal("0.04"), gt=0)
    maximum_physical_calls: int = Field(default=60, ge=1, le=100)
    tendency_reserve_cny: Decimal = Field(default=Decimal("0.08"), ge=0)
    continuity_reserve_cny: Decimal = Field(default=Decimal("0.04"), ge=0)
    price: PriceQuote | None = None

    @classmethod
    def load(cls, root):
        return cls.model_validate(
            yaml.safe_load((root / "config/release.yaml").read_text(encoding="utf-8"))
        )


class Accounting:
    def __init__(self, root, day, archive, policy):
        self.root, self.day, self.archive, self.policy = root, day, archive, policy
        self.lock = RLock()
        self.active = set()
        self.phase = "unrestricted"

    def before(self, record=None):
        summary = usage_summary(self.root, self.day)
        rows = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in (self.root / "data/usage" / self.day).glob("*.json")
        ]
        if any(r["cost_status"] != "estimated" and r["call_id"] not in self.active for r in rows):
            raise AIBudgetExceeded(
                "Prior physical call cost unavailable; reconcile before more work"
            )
        if summary["physical_calls"] >= self.policy.maximum_physical_calls:
            raise AIBudgetExceeded("Persistent daily physical-call cap reached")
        reservation = self.policy.per_call_reserve_cny
        if record is not None:
            price = self.policy.price
            if price is None or price.model != record["model"] or price.currency != "CNY":
                raise AIBudgetExceeded("Verified matching price unavailable")
            output_cap = record.get("maximum_output_tokens")
            if not output_cap:
                raise AIBudgetExceeded("Output token bound unavailable")
            # Conservative UTF-8 byte bound plus protocol overhead; not a tariff guarantee.
            reservation = max(
                reservation,
                (
                    Decimal(record["request_characters"] * 4 + 1024)
                    * max(price.input_per_million, price.cached_input_per_million or Decimal(0))
                    + Decimal(output_cap) * price.output_per_million
                )
                / Decimal(1_000_000),
            )
        outstanding = sum(
            (
                Decimal(r["reservation_cny"])
                for r in rows
                if r["call_id"] in self.active and r["cost_status"] != "estimated"
            ),
            Decimal(0),
        )
        protected = Decimal(0)
        if self.phase == "daily":
            protected = self.policy.tendency_reserve_cny + self.policy.continuity_reserve_cny
        elif self.phase == "tendency":
            protected = self.policy.continuity_reserve_cny
        if Decimal(summary["known_cost_cny"]) + outstanding + reservation + protected > (
            self.policy.daily_limit_cny
        ):
            raise AIBudgetExceeded("Persistent daily cost reservation unavailable")
        return reservation

    def checkpoint(self):
        self.archive("physical-call observation")

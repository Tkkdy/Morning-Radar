"""Per-physical-attempt accounting; immutable identities survive re-imports."""

from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import local
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class PriceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    currency: str = "CNY"
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    cached_input_per_million: Decimal | None = Field(default=None, ge=0)
    source_url: str
    verified_on: str


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class UsageRecorder:
    def __init__(self, root: Path, *, business_date: str, module: str, accounting=None) -> None:
        self.accounting = accounting
        self._thread_state = local()
        self.directory = root / "data/usage" / business_date
        self.business_date, self.module = business_date, module

    @property
    def last_call_id(self) -> str | None:
        # Daily writing and fast continuity share a provider across worker threads.
        return getattr(self._thread_state, "call_id", None)

    @last_call_id.setter
    def last_call_id(self, value: str) -> None:
        self._thread_state.call_id = value

    def start(
        self, *, provider: str, model: str, task: str, request_characters: int,
        maximum_output_tokens: int | None = None,
    ) -> dict:
        record = {
            "schema_version": 1, "call_id": uuid4().hex, "business_date": self.business_date,
            "module": self.module, "provider": provider, "model": model, "task": task,
            "started_at": datetime.now(UTC).isoformat(), "state": "started",
            "request_characters": request_characters, "usage": None, "price": None,
            "cost_status": "unavailable", "cost_cny": None,
            "maximum_output_tokens": maximum_output_tokens,
        }
        with self.accounting.lock if self.accounting else nullcontext():
            if self.accounting:
                record["reservation_cny"] = str(self.accounting.before(record))
            atomic_json(self.directory / (record["call_id"] + ".json"), record)
            if self.accounting:
                self.accounting.checkpoint()
                self.accounting.active.add(record["call_id"])
        self.last_call_id = record["call_id"]
        return record

    def finish(
        self, record: dict, *, usage: dict | None, state: str, price: PriceQuote | None = None
    ) -> dict:
        price = price or (self.accounting.policy.price if self.accounting else None)
        result = {**record, "state": state, "usage": usage}
        cost = priced_cost(record, usage, price)
        if price is not None:
            result["price"] = price.model_dump(mode="json")
        if cost is not None:
            result["cost_cny"] = str(cost)
            result["cost_status"] = "estimated"
        with self.accounting.lock if self.accounting else nullcontext():
            atomic_json(self.directory / (record["call_id"] + ".json"), result)
            if self.accounting:
                self.accounting.checkpoint()
                self.accounting.active.discard(record["call_id"])
        return result

    def import_record(self, record: dict) -> None:
        identity = record["call_id"]
        if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
            raise ValueError("Invalid physical call identity")
        if record["business_date"] != self.business_date:
            raise ValueError("Business date mismatch")
        path = self.directory / (identity + ".json")
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8")) != record:
                raise ValueError("Conflicting duplicate call")
            return
        atomic_json(path, record)



def priced_cost(record: dict, usage: dict | None, price: PriceQuote | None) -> Decimal | None:
    if usage is None or price is None or price.model != record["model"] or price.currency != "CNY":
        return None
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if prompt is None or completion is None:
        return None
    cached = 0
    if record["provider"] == "deepseek":
        cached, miss = usage.get("cache_hit_tokens"), usage.get("cache_miss_tokens")
        if cached is None or miss is None or cached + miss != prompt:
            return None
    elif record["provider"] == "openai":
        cached = usage.get("cached_tokens")
        if cached is None:
            return None
    if min(prompt, completion, cached) < 0 or cached > prompt:
        return None
    if cached and price.cached_input_per_million is None:
        return None
    return (
        Decimal(prompt - cached) * price.input_per_million
        + Decimal(cached) * (price.cached_input_per_million or Decimal(0))
        + Decimal(completion) * price.output_per_million
    ) / Decimal(1_000_000)


def usage_summary(root: Path, business_date: str) -> dict:
    calls = {}
    for path in sorted((root / "data/usage" / business_date).glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        key = record["call_id"]
        if key in calls and calls[key] != record:
            raise ValueError("Conflicting physical call records")
        calls[key] = record
    return {
        "schema_version": 1, "business_date": business_date,
        **_summarize_calls(list(calls.values())),
        "modules": sorted({r["module"] for r in calls.values()}),
        "by_module": {
            module: _summarize_calls([r for r in calls.values() if r["module"] == module])
            for module in sorted({r["module"] for r in calls.values()})
        },
    }


def _summarize_calls(calls: list[dict]) -> dict:
    unknown = sum(r["cost_status"] != "estimated" for r in calls)
    known = sum((Decimal(r["cost_cny"]) for r in calls
                 if r["cost_status"] == "estimated"), Decimal(0))
    token_totals = {}
    for key in ["prompt_tokens", "completion_tokens", "reasoning_tokens"]:
        observed = [(r.get("usage") or {}).get(key) for r in calls]
        token_totals[key] = {
            "observed": sum(value for value in observed if value is not None),
            "missing_calls": sum(value is None for value in observed),
        }
    return {
        "physical_calls": len(calls),
        "cost_status": "unavailable" if unknown or not calls else "estimated",
        "incomplete_calls": unknown, "known_cost_cny": str(known),
        "total_cost_cny": None if unknown or not calls else str(known),
        "tokens": token_totals,
    }



def attach_usage(provider, root: Path, *, business_date: str, module: str) -> None:
    # Fake providers have no physical billable transport.
    if hasattr(provider, "client"):
        provider.usage_recorder = UsageRecorder(
            root, business_date=business_date, module=module,
            accounting=getattr(provider, "release_accounting", None),
        )

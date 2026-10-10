"""Per-physical-attempt accounting; immutable identities survive re-imports."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class PriceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    currency: str = "CNY"
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    source_url: str
    verified_on: str


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class UsageRecorder:
    def __init__(self, root: Path, *, business_date: str, module: str) -> None:
        self.directory = root / "data/usage" / business_date
        self.business_date, self.module = business_date, module

    def start(self, *, provider: str, model: str, task: str, request_characters: int) -> dict:
        record = {
            "schema_version": 1, "call_id": uuid4().hex, "business_date": self.business_date,
            "module": self.module, "provider": provider, "model": model, "task": task,
            "started_at": datetime.now(UTC).isoformat(), "state": "started",
            "request_characters": request_characters, "usage": None, "price": None,
            "cost_status": "unavailable", "cost_cny": None,
        }
        atomic_json(self.directory / (record["call_id"] + ".json"), record)
        self.last_call_id = record["call_id"]
        return record

    def finish(
        self, record: dict, *, usage: dict | None, state: str, price: PriceQuote | None = None
    ) -> dict:
        result = {**record, "state": state, "usage": usage}
        if (
            usage is not None and price is not None and price.model == record["model"]
            and price.currency == "CNY"
            and usage.get("prompt_tokens") is not None
            and usage.get("completion_tokens") is not None
        ):
            result["price"] = price.model_dump(mode="json")
            result["cost_cny"] = str((
                Decimal(usage["prompt_tokens"]) * price.input_per_million
                + Decimal(usage["completion_tokens"]) * price.output_per_million
            ) / Decimal(1_000_000))
            result["cost_status"] = "estimated"
        atomic_json(self.directory / (record["call_id"] + ".json"), result)
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
        provider.usage_recorder = UsageRecorder(root, business_date=business_date, module=module)

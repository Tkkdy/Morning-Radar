"""Side-effect-free, frozen-input DeepSeek/Qwen Brief comparison."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any

from morning_radar.ai import (
    AIAuthenticationError,
    AIBillingUnavailable,
    AIBudget,
    AIConfigurationError,
    AIOutputError,
    DeepSeekProvider,
)
from morning_radar.ai.provider import AIProvider
from morning_radar.editorial.evaluator import validate_editorial_batch
from morning_radar.evaluation.reporting import build_model_ab_report
from morning_radar.models import Signal, Story
from morning_radar.storage import load_models, read_json, write_json
from morning_radar.time_utils import display_date, utc_now


def stable_label_mapping(day: date) -> dict[str, str]:
    """Deterministically vary labels by date without changing during a day."""
    even = int(hashlib.sha256(day.isoformat().encode()).hexdigest(), 16) % 2 == 0
    return (
        {"A": "production", "B": "challenger"} if even else {"A": "challenger", "B": "production"}
    )


def _provider_metrics(provider: AIProvider, started: float) -> dict[str, Any]:
    budget = getattr(provider, "budget", None)
    usage = budget.usage_run_stats() if budget is not None else {}
    logical = int(getattr(budget, "calls_used", 0))
    network = int(getattr(budget, "network_requests_used", 0))
    return {
        "latency_seconds": round(time.monotonic() - started, 3),
        "logical_tasks": logical,
        "network_requests": network,
        "retry_count": max(0, network - logical),
        "prompt_tokens": usage.get("ai_prompt_tokens", 0),
        "completion_tokens": usage.get("ai_completion_tokens", 0),
        "reasoning_tokens": usage.get("ai_reasoning_tokens", 0),
        "finish_reasons": {key: value for key, value in usage.items() if "_finish_" in key},
    }


def _run_lane(
    provider: AIProvider,
    stories: list[Story],
    signals: list[Signal],
) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {
        "provider": getattr(provider, "provider_name", type(provider).__name__),
        "model": getattr(provider, "model", "fixture"),
        "schema_valid": False,
        "provider_error": None,
        "deterministic_fallback": False,
    }
    editorial_payload: dict[str, Any] | None = None
    editorial_error: str | None = None
    try:
        editorial = validate_editorial_batch(provider.evaluate_editorial(stories), stories)
        editorial_payload = editorial.model_dump(mode="json")
    except (
        AIOutputError,
        AIConfigurationError,
        AIAuthenticationError,
        AIBillingUnavailable,
        ValueError,
    ) as exc:
        editorial_error = type(exc).__name__
    try:
        brief = provider.write_brief(stories, signals)
        known_ids = {story.id for story in stories}
        returned_ids = {story_id for item in brief.items for story_id in item.story_ids}
        unsupported = sorted(returned_ids - known_ids)
        result.update(
            {
                "schema_valid": editorial_payload is not None,
                "editorial_shadow": editorial_payload,
                "editorial_error": editorial_error,
                "brief": brief.model_dump(mode="json"),
                "automatic_validation": {
                    "factual_reference_valid": not unsupported,
                    "story_coverage": (
                        round(len(returned_ids & known_ids) / len(known_ids), 3)
                        if known_ids
                        else 1.0
                    ),
                    "unsupported_or_invented_refs": unsupported,
                    "truncation": any(
                        key.endswith("_finish_length") and value
                        for key, value in _provider_metrics(provider, started)[
                            "finish_reasons"
                        ].items()
                    ),
                },
            }
        )
    except (
        AIOutputError,
        AIConfigurationError,
        AIAuthenticationError,
        AIBillingUnavailable,
        ValueError,
    ) as exc:
        result["provider_error"] = type(exc).__name__
    result.update(_provider_metrics(provider, started))
    return result


def _experiment_stop(artifacts: list[dict[str, Any]], day: date) -> str | None:
    paired = [item for item in artifacts if item.get("successful_pair")]
    if len(paired) >= 7:
        return "seven_successful_paired_days"
    if artifacts:
        first = min(date.fromisoformat(item["date"]) for item in artifacts)
        if (day - first).days >= 9 and len(paired) < 5:
            return "provider_reliability_insufficient"
    return None


def run_model_ab_experiment(
    root: Path,
    *,
    production: AIProvider | None = None,
    challenger: AIProvider | None = None,
    current_date: date | None = None,
) -> dict[str, Any]:
    """Run both lanes from one persisted input bundle; never mutate production state."""
    root = root.resolve()
    day = current_date or display_date(utc_now())
    artifact_dir = root / "data/evaluations/model_ab"
    prior = [read_json(path) for path in sorted(artifact_dir.glob("????-??-??.json"))]
    stop_reason = _experiment_stop(prior, day)
    if stop_reason:
        report = build_model_ab_report(prior, stop_reason=stop_reason)
        write_json(artifact_dir / "report.json", report)
        return {"date": day.isoformat(), **report}
    stories = load_models(root / "data/stories" / f"{day}.json", Story)
    signal_path = root / "data/signals" / f"{day}.json"
    signals = load_models(signal_path, Signal) if signal_path.exists() else []
    bundle_payload = {
        "stories": [item.model_dump(mode="json") for item in stories],
        "signals": [item.model_dump(mode="json") for item in signals],
    }
    bundle_json = json.dumps(bundle_payload, ensure_ascii=False, sort_keys=True)
    bundle_hash = hashlib.sha256(bundle_json.encode()).hexdigest()
    production_provider = production or DeepSeekProvider.from_environment(
        budget=AIBudget(2, 50000, 40, 4), prompt_dir=root / "prompts"
    )
    production_result = _run_lane(production_provider, stories, signals)
    if challenger is None:
        try:
            challenger = DeepSeekProvider(
                model=os.getenv("QWEN_MODEL", ""),
                api_key=os.getenv("QWEN_API_KEY", ""),
                base_url=os.getenv("QWEN_BASE_URL", ""),
                budget=AIBudget(2, 50000, 40, 2),
                prompt_dir=root / "prompts",
                network_attempts=1,
            )
            challenger.provider_name = "qwen"
        except AIConfigurationError as exc:
            challenger_result = {
                "provider": "qwen",
                "model": os.getenv("QWEN_MODEL", "unconfigured"),
                "schema_valid": False,
                "provider_error": type(exc).__name__,
            }
        else:
            challenger_result = _run_lane(challenger, stories, signals)
    else:
        challenger_result = _run_lane(challenger, stories, signals)
    lanes = {"production": production_result, "challenger": challenger_result}
    mapping = stable_label_mapping(day)
    artifact = {
        "date": day.isoformat(),
        "input_bundle_id": bundle_hash[:20],
        "input_bundle_hash": bundle_hash,
        "label_mapping": mapping,
        "versions": {label: lanes[lane] for label, lane in mapping.items()},
        "successful_pair": all(item.get("schema_valid") for item in lanes.values()),
    }
    write_json(artifact_dir / f"{day}.json", artifact)
    all_artifacts = [*prior, artifact]
    build_blind_evaluation_page(root, all_artifacts)
    completed_reason = _experiment_stop(all_artifacts, day)
    if completed_reason:
        write_json(
            artifact_dir / "report.json",
            build_model_ab_report(all_artifacts, stop_reason=completed_reason),
        )
    return artifact


def build_blind_evaluation_page(root: Path, artifacts: list[dict[str, Any]]) -> None:
    payload = json.dumps(artifacts, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width\"><title>Model A/B Evaluation</title>
<link rel=\"stylesheet\" href=\"../../assets/style.css\"><body><main>
<h1>Model A/B Blind Evaluation</h1><p>模型名称默认隐藏；选择仅保存在本机浏览器。</p>
<div id=\"days\"></div><button id=\"reveal\">Reveal models</button>
<h2>Local preference summary</h2><p id=\"summary\"></p></main>
<script>const data={payload};const root=document.getElementById('days');
const votes=JSON.parse(localStorage.getItem('morning-radar-model-ab')||'{{}}');
function render(){{root.innerHTML='';for(const day of data){{const section=document.createElement('section');
section.innerHTML=`<h2>${{day.date}}</h2>`;for(const label of ['A','B']){{const v=day.versions[label];
const pre=document.createElement('pre');pre.textContent=JSON.stringify(v.brief||v.provider_error,null,2);
section.append(`${{label}} `,pre);}}for(const choice of ['A','B','Tie']){{const b=document.createElement('button');
b.textContent=choice+(votes[day.date]===choice?' ✓':'');b.onclick=()=>{{votes[day.date]=choice;
localStorage.setItem('morning-radar-model-ab',JSON.stringify(votes));render();}};section.append(b);}}root.append(section);}}
document.getElementById('summary').textContent=JSON.stringify(Object.values(votes).reduce((a,v)=>(a[v]=(a[v]||0)+1,a),{{}}));}}
document.getElementById('reveal').onclick=()=>alert(data.map(d=>d.date+': '+JSON.stringify(d.label_mapping)).join('\\n'));
render();</script></body></html>"""
    destination = root / "site/evaluation/model-ab/index.html"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")

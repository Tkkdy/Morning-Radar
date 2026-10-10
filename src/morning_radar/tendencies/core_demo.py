"""Entirely synthetic authored trajectory, never evidence of model discovery skill."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from morning_radar.ai import FakeAIProvider
from morning_radar.ai.usage import atomic_json
from morning_radar.tendencies.core import run_core
from morning_radar.tendencies.core_export import export_core, render_markdown
from morning_radar.tendencies.core_models import CoreOutput
from morning_radar.tendencies.core_store import CoreStore

DEMO_TIME = datetime(2026, 10, 10, 8, tzinfo=UTC)
DEMO_TEXTS = [
    "【synthetic】港区甲乙试验站报告低功耗传感器可在休眠后快速唤醒，尚未测试长期可靠性。",
    "【synthetic】港区甲乙试验站报告间歇网络可在本地缓存后恢复补传，但未验证高峰拥塞。",
    "【synthetic】同一试验的后续记录显示甲乙两站连续运行一周，间歇采样并未丢失关键告警。",
    "【synthetic】后续反例显示乙站高峰拥塞下出现告警延迟，甲站低负载期间尚未发现此问题。",
]


def write_demo_evidence(root: Path, index: int, now: datetime) -> None:
    atomic_json(
        root / "data/evidence/v1" / str(now.date()) / f"synthetic-{index}.json",
        {
            "schema_version": 1,
            "evidence_id": f"synthetic-{index}",
            "input_id": f"demo-input-{index}",
            "content_version": str(index),
            "source_url": f"https://example.org/synthetic/port/{index}",
            "title": "synthetic 港区试验材料",
            "excerpt": DEMO_TEXTS[index],
            "captured_at": now.isoformat(),
            "observed_at": now.isoformat(),
            "published_at": None,
            "source_role": "official_primary",
            "status": "captured",
            "scope": ["港区甲", "港区乙"],
            "synthetic": True,
        },
    )
    atomic_json(
        root / "data/evidence/events" / f"synthetic-{index}.json",
        {
            "schema_version": 1,
            "events": [
                {
                    "input_ids": [f"demo-input-{index}"],
                    "event_id": "synthetic-port-trial",
                    "recorded_at": now.isoformat(),
                }
            ],
        },
    )


def demo_decision(context: dict, kind: str = "early") -> dict:
    """Authored output for exercising real validation/persistence, not AI reasoning."""
    evidence = context["evidence"]
    target = context.get("targets", [None])[0] if context.get("targets") else None
    narrowed = kind == "narrowed"
    claim = (
        "低负载港区甲的监测可能从持续联网转向间歇协同"
        if narrowed
        else ("港区甲乙的监测可能从持续联网转向间歇协同")
    )
    hypothesis = {
        "claim": claim,
        "scope": ["港区甲"] if narrowed else ["港区甲", "港区乙"],
        "from_state": "依赖持续联网的告警采集",
        "to_state": "有条件的间歇采样和补传",
        "baseline": [
            {
                "text": "持续联网可能曾是可靠告警的默认前提，需核实。",
                "assumption": True,
                "evidence_ids": [],
            }
        ],
        "conditions": [
            {
                "key": "coordination",
                "observation": "来源报告了唤醒与缓存能力变化。",
                "interpretation": "两项能力可能一起减少持续联网依赖。",
                "evidence_ids": [e["evidence_id"] for e in evidence],
            }
        ],
        "mechanism": "唤醒降低持续工作要求，缓存解除即时网络条件；拥塞可能成为新瓶颈。",
        "unresolved": ["长期可靠性和高峰拥塞条件仍未充分验证。"],
        "alternatives": ["观察到的效果可能仅来自低负载条件。"],
        "tests": [
            {
                "condition_key": "coordination",
                "observe": "实际执行高负载告警延迟测试",
                "if_seen": "若可靠可扩大适用负载条件",
                "if_absent": "若完成测试而未达标则收窄范围",
            }
        ],
    }
    return {
        "hypothesis_id": target["hypothesis_id"] if target else None,
        "previous_revision_id": target["revision_id"] if target else None,
        "kind": kind,
        "basis": "external_evidence",
        "hypothesis": hypothesis,
        "citations": [
            {
                "evidence_id": e["evidence_id"],
                "quote": e["excerpt"],
                "role": "counter" if "反例" in e["excerpt"] else "support",
                "condition_key": "coordination",
            }
            for e in evidence
        ],
        "changed_parts": [] if not target else ["coordination", "scope"],
        "relinquished": ["放弃对港区乙高峰条件的适用判断"] if narrowed else [],
        "reason": "synthetic 演示："
        + {
            "early": "互补条件支持早期推断",
            "strengthened": "同一试验的新运行记录验证持续运行环节",
            "narrowed": "反例击中高负载条件，明确收窄范围",
        }.get(kind, "检查"),
    }


class SyntheticTrajectoryProvider(FakeAIProvider):
    def __init__(self, kind: str):
        self.kind = kind

    def discover_hypotheses(self, context):
        if context["registry"]:
            return CoreOutput(outcome="no_changes")
        return CoreOutput(decisions=[demo_decision(context)], outcome="proposed")

    def calibrate_hypotheses(self, context):
        return CoreOutput(decisions=[demo_decision(context, self.kind)], outcome="proposed")


def build_core_demo(config_root: Path, output: Path) -> Path:
    config_root, output = config_root.resolve(), output.resolve()
    for protected in [config_root / "data", config_root / "site"]:
        if output == protected or protected in output.parents or output in protected.parents:
            raise ValueError("Demo output must be isolated from production data/site")
    if output.exists():
        raise ValueError("Demo requires a new isolated directory")
    output.mkdir(parents=True)
    runs = []
    for step, kind in enumerate(["early", "strengthened", "narrowed"]):
        now = DEMO_TIME + timedelta(hours=step)
        if step == 0:
            write_demo_evidence(output, 0, now)
            write_demo_evidence(output, 1, now)
        else:
            write_demo_evidence(output, step + 1, now)
        result = run_core(
            output, config_root=config_root, provider=SyntheticTrajectoryProvider(kind), now=now
        )
        runs.append(result)
    payload = export_core(CoreStore(output))
    assert [r["decision"]["kind"] for r in payload["history"]] == [
        "early",
        "strengthened",
        "narrowed",
    ]
    atomic_json(
        output / "trajectory.json",
        {
            "synthetic": True,
            "provider": "Authored SyntheticTrajectoryProvider, not real model inference",
            "paid_calls": 0,
            "runs": runs,
            **payload,
        },
    )
    destination = output / "trajectory.md"
    destination.write_text(
        "# 全程 synthetic：只验机制，不证明真实发现能力\n\n" + render_markdown(payload),
        encoding="utf-8",
    )
    (output / "operations.json").write_text(
        json.dumps(
            {
                "read_original_revision": payload["history"][0]["revision_id"],
                "hypothesis_id": payload["current"][0]["hypothesis_id"],
                "current_scope": payload["current"][0]["decision"]["hypothesis"]["scope"],
                "first_scope": payload["history"][0]["decision"]["hypothesis"]["scope"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return destination

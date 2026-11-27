"""Formal provider paths share existing transport, structured parsing and metering."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from morning_radar.tendencies.core_models import CoreOutput, SplitOutput


def core_instructions(task: str, instructions: str) -> str:
    if task in {"tendency_discover", "tendency_calibrate"}:
        from morning_radar.tendencies.core_models import CoreDecision, CoreOutput

        instructions = instructions.split("单条 decision 的完整字段契约")[0]
        empty = CoreOutput(outcome="insufficient_evidence", gaps=["材料尚不足以提出有依据的方向"])
        instructions += (
            "\n返回的是 CoreOutput 根对象，不是单条 decision。根对象契约见消息末尾。"
            "outcome 必填；decisions 的每一项遵守下面的 CoreDecision 契约。"
            "没有命题时可返回以下合法空结果（这是格式示例，不是指定答案）：\n"
            + empty.model_dump_json()
        )
        instructions += "\n单条 decision 的完整字段契约：\n" + json.dumps(
            CoreDecision.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
    if task == "tendency_review":
        from morning_radar.tendencies.grounded_review import Assessment
        instructions += "\n审查字段契约：\n" + json.dumps(
            Assessment.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
    return instructions


def normalize_core_output(output):
    """Only two observed, semantically empty wire extensions; never relax storage."""
    normalized = copy.deepcopy(output)
    changes = []
    for index, decision in enumerate(normalized.decisions):
        hypothesis = decision.get("hypothesis")
        if not isinstance(hypothesis, dict):
            continue
        for key in ("conditions_note", "unresolved_note"):
            if key in hypothesis and hypothesis[key] is None:
                hypothesis.pop(key)
                changes.append(
                    {
                        "path": f"decisions/{index}/hypothesis/{key}",
                        "before": None,
                        "operation": "remove",
                    }
                )
    return normalized, {"version": "core-null-notes-v1", "changes": changes}


class TendencyCoreProviderMixin:
    def discover_hypotheses(self, context: dict) -> CoreOutput:
        from morning_radar.tendencies.core_models import CoreOutput

        return self._core_parse("tendency_discover", CoreOutput, context)

    def calibrate_hypotheses(self, context: dict) -> CoreOutput:
        from morning_radar.tendencies.core_models import CoreOutput

        return self._core_parse("tendency_calibrate", CoreOutput, context)

    def review_hypotheses(self, context: dict):
        from morning_radar.tendencies.grounded_review import ReviewOutput
        return self._core_parse("tendency_review", ReviewOutput, context)

    def split_hypotheses(self, context: dict) -> SplitOutput:
        from morning_radar.tendencies.core_models import SplitOutput

        return self._core_parse("tendency_split", SplitOutput, context)

    def _core_parse(self, task, schema, context):
        self.last_review_response = None
        self.last_core_response = None
        self.last_core_normalization = None
        self.last_core_raw_output = None
        recorder = getattr(self, "usage_recorder", None)
        if recorder:
            recorder.last_call_id = None
        try:
            output = self._parse(
                task=task,
                schema=schema,
                payload_data=context,
                item_count=len(context.get("evidence", [])),
                allowed_urls={
                    e["source_url"] for e in context.get("evidence", []) if e.get("source_url")
                },
                maximum_structured_attempts=1,
            )
            self.last_core_raw_output = output.model_dump(mode="json")
            if task in {"tendency_discover", "tendency_calibrate"}:
                self.last_core_raw_output = output.model_dump(mode="json")
                output, self.last_core_normalization = normalize_core_output(output)
            return output
        finally:
            self.last_core_call_id = getattr(recorder, "last_call_id", None)

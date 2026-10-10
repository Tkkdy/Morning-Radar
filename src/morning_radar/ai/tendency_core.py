"""Formal provider paths share existing transport, structured parsing and metering."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from morning_radar.tendencies.core_models import CoreOutput, SplitOutput


class TendencyCoreProviderMixin:
    def discover_hypotheses(self, context: dict) -> CoreOutput:
        from morning_radar.tendencies.core_models import CoreOutput

        return self._core_parse("tendency_discover", CoreOutput, context)

    def calibrate_hypotheses(self, context: dict) -> CoreOutput:
        from morning_radar.tendencies.core_models import CoreOutput

        return self._core_parse("tendency_calibrate", CoreOutput, context)

    def split_hypotheses(self, context: dict) -> SplitOutput:
        from morning_radar.tendencies.core_models import SplitOutput

        return self._core_parse("tendency_split", SplitOutput, context)

    def _core_parse(self, task, schema, context):
        self.last_core_response = None
        recorder = getattr(self, "usage_recorder", None)
        if recorder:
            recorder.last_call_id = None
        try:
            return self._parse(
                task=task,
                schema=schema,
                payload_data=context,
                item_count=len(context.get("evidence", [])),
                allowed_urls={
                    e["source_url"] for e in context.get("evidence", []) if e.get("source_url")
                },
                maximum_structured_attempts=1,
            )
        finally:
            self.last_core_call_id = getattr(recorder, "last_call_id", None)

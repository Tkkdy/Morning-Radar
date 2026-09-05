"""Small AI safety guard for logical work, network attempts, and real usage."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from threading import Lock


class AITaskPriority(StrEnum):
    CORE = "CORE"
    IMPORTANT = "IMPORTANT"
    OPTIONAL = "OPTIONAL"
    EXPERIMENTAL = "EXPERIMENTAL"


class AIBudgetExceeded(RuntimeError):
    pass


@dataclass(slots=True)
class AIBudget:
    maximum_calls: int
    maximum_input_characters: int
    maximum_items: int
    maximum_network_requests: int = 60
    calls_used: int = 0
    input_characters_used: int = 0
    network_requests_used: int = 0
    task_network_requests: dict[str, int] = field(default_factory=dict)
    task_usage: dict[str, dict[str, int]] = field(default_factory=dict)
    task_finish_reasons: dict[str, dict[str, int]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def consume(self, payload: str, *, item_count: int) -> None:
        """Reserve one logical task. Structured retries must not call this again."""
        with self._lock:
            self._consume_locked(payload, item_count=item_count)

    def _consume_locked(self, payload: str, *, item_count: int) -> None:
        if item_count > self.maximum_items:
            raise AIBudgetExceeded(f"AI item limit exceeded: {item_count} > {self.maximum_items}")
        if self.calls_used + 1 > self.maximum_calls:
            raise AIBudgetExceeded("AI daily call limit exceeded")
        if self.input_characters_used + len(payload) > self.maximum_input_characters:
            raise AIBudgetExceeded("AI daily input character limit exceeded")
        self.calls_used += 1
        self.input_characters_used += len(payload)

    def reset_task_attempts(self, task: str) -> None:
        with self._lock:
            self.task_network_requests[task] = 0

    def record_network_request(
        self,
        task: str = "unknown",
        *,
        maximum_task_attempts: int = 3,
    ) -> None:
        maximum_task_attempts = min(3, max(1, maximum_task_attempts))
        with self._lock:
            self._record_network_request_locked(task, maximum_task_attempts=maximum_task_attempts)

    def _record_network_request_locked(self, task: str, *, maximum_task_attempts: int) -> None:
        used = self.task_network_requests.get(task, 0)
        if used >= maximum_task_attempts:
            raise AIBudgetExceeded(f"AI task network attempt limit exceeded: {task}")
        if self.network_requests_used >= self.maximum_network_requests:
            raise AIBudgetExceeded("AI global network request limit exceeded")
        self.network_requests_used += 1
        self.task_network_requests[task] = used + 1

    def record_response_usage(
        self,
        task: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        reasoning_tokens: int = 0,
        finish_reason: str = "unknown",
    ) -> None:
        with self._lock:
            usage = self.task_usage.setdefault(
                task,
                {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0},
            )
            usage["prompt_tokens"] += prompt_tokens
            usage["completion_tokens"] += completion_tokens
            usage["reasoning_tokens"] += reasoning_tokens
            reasons = self.task_finish_reasons.setdefault(task, {})
            reasons[finish_reason] = reasons.get(finish_reason, 0) + 1

    def usage_run_stats(self) -> dict[str, int]:
        with self._lock:
            stats: dict[str, int] = {
                "logical_ai_tasks": self.calls_used,
                "network_ai_requests": self.network_requests_used,
                "ai_prompt_tokens": 0,
                "ai_completion_tokens": 0,
                "ai_reasoning_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "reasoning_tokens": 0,
            }
            for task, usage in sorted(self.task_usage.items()):
                for name, value in usage.items():
                    stats[f"ai_{task}_{name}"] = value
                    stats[f"ai_{name}"] += value
                    stats[name] += value
            for task, reasons in sorted(self.task_finish_reasons.items()):
                for reason, count in sorted(reasons.items()):
                    stats[f"ai_{task}_finish_{reason}"] = count
            return stats

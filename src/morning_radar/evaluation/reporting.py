"""Aggregate multidimensional A/B telemetry without inventing a quality score."""

from __future__ import annotations

from typing import Any


def build_model_ab_report(artifacts: list[dict[str, Any]], *, stop_reason: str) -> dict[str, Any]:
    lanes: dict[str, list[dict[str, Any]]] = {"production": [], "challenger": []}
    for artifact in artifacts:
        mapping = artifact.get("label_mapping", {})
        versions = artifact.get("versions", {})
        for label, lane in mapping.items():
            if lane in lanes and label in versions:
                lanes[lane].append(versions[label])

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        valid = [row for row in rows if row.get("schema_valid")]
        references = [row.get("automatic_validation", {}) for row in valid]
        return {
            "runs": len(rows),
            "schema_successes": len(valid),
            "provider_failures": sum(bool(row.get("provider_error")) for row in rows),
            "structured_retries": sum(int(row.get("retry_count", 0)) for row in rows),
            "average_latency_seconds": (
                round(sum(float(row.get("latency_seconds", 0)) for row in rows) / len(rows), 3)
                if rows
                else None
            ),
            "prompt_tokens": sum(int(row.get("prompt_tokens", 0)) for row in rows),
            "completion_tokens": sum(int(row.get("completion_tokens", 0)) for row in rows),
            "reasoning_tokens": sum(int(row.get("reasoning_tokens", 0)) for row in rows),
            "valid_factual_references": sum(
                bool(item.get("factual_reference_valid")) for item in references
            ),
            "average_story_coverage": (
                round(
                    sum(float(item.get("story_coverage", 0)) for item in references)
                    / len(references),
                    3,
                )
                if references
                else None
            ),
            "estimated_cost": None,
            "estimated_cost_note": "Pricing is deployment-specific; no runtime CNY cost engine.",
        }

    return {
        "status": "stopped",
        "stop_reason": stop_reason,
        "successful_paired_days": sum(
            bool(artifact.get("successful_pair")) for artifact in artifacts
        ),
        "calendar_artifacts": len(artifacts),
        "production": summarize(lanes["production"]),
        "challenger": summarize(lanes["challenger"]),
        "user_preference": "available only in evaluation-page localStorage",
        "recommendation": "manual review required; no automatic winner selection",
        "editorial_recommendation": "manual ACTIVATE or KEEP SHADOW decision required",
    }

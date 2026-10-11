"""Synthetic responses only: exercises UI inputs through the real C runner."""

from __future__ import annotations

import copy
from pathlib import Path

from morning_radar.ai import FakeAIProvider
from morning_radar.tendencies.core import run_core
from morning_radar.tendencies.core_demo import demo_decision, synthetic_review
from morning_radar.tendencies.core_models import CoreOutput, SplitOutput
from morning_radar.tendencies.core_store import CoreStore
from morning_radar.tendencies.web_input import line_entries


class SyntheticEditorProvider(FakeAIProvider):
    """Authored demonstration, not model discovery or opinion verification."""

    review_hypotheses = staticmethod(synthetic_review)

    def split_hypotheses(self, context):
        return SplitOutput(items=line_entries(context["raw_text"]))

    def discover_hypotheses(self, context):
        known = {r["claim"] for r in context["registry"]}
        proposals = []
        for seed in context["seeds"]:
            if seed["text"] in known or "synthetic" not in seed["text"]:
                continue
            draft = demo_decision(context)
            draft["seed_id"] = seed["seed_id"]
            draft["hypothesis"]["claim"] = seed["text"]
            draft["reason"] = "synthetic：人工预制输出，仅验证多投递与修订接线。"
            proposals.append(draft)
        return CoreOutput(decisions=proposals, outcome="proposed" if proposals else "no_changes")

    def calibrate_hypotheses(self, context):
        proposals = []
        for target in context["targets"]:
            draft = copy.deepcopy(target["decision"])
            draft.update(
                hypothesis_id=target["hypothesis_id"],
                previous_revision_id=target["revision_id"],
                parent_hypothesis_id=None,
                parent_revision_id=None,
                seed_id=None,
                kind="no_change",
                basis="external_evidence",
            )
            seed = next(
                (
                    s
                    for s in context["seeds"]
                    if s.get("correction_of") == target["hypothesis_id"]
                    and s.get("expected_revision") == target["revision_id"]
                ),
                None,
            )
            if seed:
                draft.update(
                    kind="user_correction",
                    basis="user_correction",
                    seed_id=seed["seed_id"],
                    changed_parts=["mechanism"],
                    reason="synthetic：按已保存用户纠偏修改机制措辞，非外部事实增强。",
                )
                draft["hypothesis"]["mechanism"] += " 演示纠偏：须明确实际负载边界。"
            proposals.append(draft)
        return CoreOutput(decisions=proposals, outcome="proposed")


def process_demo(root: Path, repository: Path):
    store = CoreStore(root)
    if any(not r.synthetic for r in store.revisions()) or any(
        not s.get("synthetic") for s in store.submissions().values()
    ):
        raise ValueError("Synthetic runner refuses real records")
    return run_core(root, config_root=repository, provider=SyntheticEditorProvider())

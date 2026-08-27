import json
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("prompt_name", "required_fields"),
    [
        ("merge_story.md", ("canonical_title", "facts", "analysis", "uncertainties")),
        (
            "write_brief.md",
            (
                "title",
                "what_happened",
                "why_it_matters",
                "market_or_community_reaction",
                "uncertainty",
                "watch_next",
                "cognitive_extension",
            ),
        ),
        ("direction_observation.md", ("observation", "uncertainties")),
        (
            "candidate_triage.md",
            ("hypothesis", "potential impact", "missing_evidence", "verification_path"),
        ),
        (
            "construct_story.md",
            ("facts", "analysis", "uncertainties", "fact_supports"),
        ),
        (
            "evaluate_tendencies.md",
            ("shared mechanism", "baseline", "falsifier", "counterevidence"),
        ),
    ],
)
def test_user_visible_ai_prompt_requires_simplified_chinese(
    prompt_name: str,
    required_fields: tuple[str, ...],
) -> None:
    prompt = (Path("prompts") / prompt_name).read_text(encoding="utf-8")

    assert "所有面向最终晨报读者的自然语言" in prompt
    assert "必须使用简体中文" in prompt
    assert "专有名词" in prompt
    for field in required_fields:
        assert field in prompt


def test_brief_prompt_forbids_invented_story_references() -> None:
    prompt = Path("prompts/write_brief.md").read_text(encoding="utf-8")

    assert "story_ids 中的每个值都必须从输入 Story 的 id 字段逐字复制" in prompt
    assert "不得创建、猜测、缩写、修改、重新格式化或合并 Story ID" in prompt
    assert "每个输出 item 只能引用实际用于" in prompt
    assert "该 item 所引用 Stories" in prompt


def test_brief_prompt_defines_editorial_hierarchy_and_extension_role() -> None:
    prompt = Path("prompts/write_brief.md").read_text(encoding="utf-8")

    assert "items 按编辑优先级从高到低排列" in prompt
    assert "top_stories 只用于“今天必须知道”" in prompt
    assert "cognitive_extension 不是预测或结论" in prompt
    assert "只能返回一个值得继续思考的问题" in prompt


def test_story_prompts_keep_practitioner_and_discovery_evidence_boundaries() -> None:
    classify = Path("prompts/classify.md").read_text(encoding="utf-8")
    merge = Path("prompts/merge_story.md").read_text(encoding="utf-8")
    score = Path("prompts/score_story.md").read_text(encoding="utf-8")

    assert "Trusted Practitioner" in classify
    assert "AIHOT/upstream discovery lead" in classify
    assert "marketing、opinion 与 unverified" in merge
    assert "Community Attention" in score
    assert "不能提高事实可信度" in score


def test_editorial_prompt_forbids_invented_verification_and_weighted_master_score() -> None:
    prompt = Path("prompts/evaluate_editorial.md").read_text(encoding="utf-8")
    assert "不得使用模型" in prompt
    assert "补造验证状态" in prompt
    assert "不得生成一个\n加权总分" in prompt
    assert "SUPPORT 只补充目标" in prompt
    assert "修改价格、修改许可证" in prompt
    assert "market source 可以验证股价、成交量" in prompt
    assert "厂商不能单独验证自己声称的模型性能" in prompt


def test_editorial_prompt_defines_independent_evidence_retention_semantics() -> None:
    prompt = Path("prompts/evaluate_editorial.md").read_text(encoding="utf-8")
    profile = Path("prompts/editorial/profile.md").read_text(encoding="utf-8")

    for content in (prompt, profile):
        assert "Reader placement" in content or "reader placement" in content
        assert "所有 Story" in content
        assert "Trend" in content
        assert "weak signal" in content
        assert "装饰性更新" in content
        assert "短暂故障" in content
        assert "行业趋势" in content
    assert "evidence_value 为 3 或 4 时必须保留" in prompt
    assert "false 时 trend_links 必须为空" in prompt
    assert "trend_confirmation 时必须保留" in prompt
    assert "TOP 不必然保留，DROP 也不必然丢弃后台证据" in prompt


def test_editorial_golden_cases_cover_frontstage_and_evidence_boundaries() -> None:
    golden_path = Path("prompts/editorial/golden_cases.jsonl")
    cases = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines()]

    required = {
        "placement",
        "treatment",
        "evidence_value",
        "retain_for_trends",
        "trend_links",
        "reason",
    }
    assert all(required <= case.keys() for case in cases)
    assert any(
        case["placement"] in {"ONE-LINER", "DROP"} and case["evidence_value"] >= 3
        for case in cases
    )
    assert any(
        case["placement"] in {"TOP", "STORY"} and case["retain_for_trends"]
        for case in cases
    )
    assert any(
        case["placement"] in {"ONE-LINER", "DROP"} and not case["retain_for_trends"]
        for case in cases
    )
    assert any(
        case["placement"] == "TOP" and not case["retain_for_trends"] for case in cases
    )
    for case in cases:
        assert bool(case["trend_links"]) is case["retain_for_trends"]


def test_golden_cases_do_not_copy_held_out_scenarios() -> None:
    golden = Path("prompts/editorial/golden_cases.jsonl").read_text(encoding="utf-8").casefold()
    held_out = [
        json.loads(line)["scenario"]
        for line in Path("tests/fixtures/editorial_eval_cases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert all(scenario.casefold() not in golden for scenario in held_out)

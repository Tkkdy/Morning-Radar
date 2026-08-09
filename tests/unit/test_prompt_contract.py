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

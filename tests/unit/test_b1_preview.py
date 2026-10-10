from pathlib import Path

import pytest

from morning_radar.publishing.preview import build_b1_preview


def test_offline_preview_has_provenance_and_grounded_technical_card(tmp_path):
    output = tmp_path / "preview"
    index = build_b1_preview(Path("."), output)
    html = index.read_text(encoding="utf-8")
    daily = (output / "site/briefs/2026-10-10.html").read_text(encoding="utf-8")
    for page in [html, daily]:
        assert "B1 隔离预览" in page
        assert "真实历史 raw + Fake Provider" in page
        assert "不是原始 API 响应" in page
        assert "完全合成的技术材料" in page
        assert page.count('aria-label="技术材料解读"') == 1
        assert "作者报告的证据" in page
        assert "主要方法的原始依据" in page
        assert "缓存失效和更新一致性的开销仍需要进一步测量" in page
        assert 'href="https://example.org/paper"' in page
        assert page.index("此前只描述命中缓存") < page.index("新演示版本增加主动失效检查")
        assert "补录于 2026-10-01" in page
    assert (output / "preview-manifest.json").exists()
    assert list((output / "data/evidence/events").glob("*.json"))
    with pytest.raises(FileExistsError):
        build_b1_preview(Path("."), output)


@pytest.mark.parametrize("relative", ["data", "site", "data/preview", "site/preview", "."])
def test_preview_rejects_production_and_ancestor_paths(relative):
    root = Path(".").resolve()
    with pytest.raises(ValueError, match="isolated"):
        build_b1_preview(root, root / relative)


def test_daily_failure_artifact_preserves_material_evidence():
    workflow = Path(".github/workflows/daily-brief.yml").read_text(encoding="utf-8")
    diagnostics = workflow[workflow.index("- name: Preserve module health"):]
    assert "always()" in diagnostics
    assert "data/evidence/" in diagnostics

"""Synthetic external responses through the formal release coordinator and C core."""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

from morning_radar.collectors import CollectionResult, FixtureCollector
from morning_radar.release.archive import GitArchive, git
from morning_radar.release.coordinator import ReleaseCoordinator
from morning_radar.tendencies.web_demo import SyntheticEditorProvider
from morning_radar.time_utils import utc_now


def prepare(repository: Path, root: Path) -> None:
    if root.exists() and any(root.iterdir()):
        raise ValueError("Demo requires a new empty isolated directory")
    if (
        root.resolve() == repository.resolve()
        or root.resolve().is_relative_to(repository.resolve() / "data")
        or root.resolve().is_relative_to(repository.resolve() / "site")
    ):
        raise ValueError("Production path refused")
    root.mkdir(parents=True, exist_ok=True)
    for name in ["config", "prompts", "templates", "fixtures"]:
        shutil.copytree(repository / name, root / name)
    (root / "site/assets").mkdir(parents=True)
    shutil.copyfile(repository / "site/assets/style.css", root / "site/assets/style.css")
    (root / ".gitattributes").write_text("data/releases/v1/packages/** -text\n")
    (root / ".gitignore").write_text("/site/\n.tmp/\n*.lock\n", encoding="utf-8")
    git(root, "init", "-b", "master")
    git(root, "add", "config", "prompts", "templates", "fixtures", ".gitignore", ".gitattributes")
    git(
        root,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "test: isolated synthetic input configuration",
    )


def demo(repository: Path, root: Path) -> dict:
    prepare(repository, root)
    clock = utc_now() + timedelta(minutes=1)
    from morning_radar.tendencies.core_demo import write_demo_evidence

    write_demo_evidence(root, 0, clock - timedelta(minutes=2))
    write_demo_evidence(root, 1, clock - timedelta(minutes=1))
    items = FixtureCollector(root / "fixtures/sample_items.json").collect()
    items = [item.model_copy(update={"fetched_at": clock, "published_at": clock}) for item in items]
    collection = CollectionResult(
        items=items, raw_collected=len(items), after_buffer=len(items), after_dedup=len(items)
    )
    issue = {
        "id": "SYNTHETIC_1",
        "number": 1,
        "title": "[Tendency] synthetic",
        "body": "### 原始输入\n\nsynthetic：港区甲乙可能采用间歇采样。\n"
        "synthetic：港区甲乙可能采用本地缓存补传。"
        "\n\n### 拆分方式\n\n每行一个判断",
        "url": "https://github.com/Tkkdy/Morning-Radar/issues/1",
        "createdAt": clock.isoformat(),
        "lastEditedAt": None,
        "author": {"login": "Tkkdy"},
        "editor": None,
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "nameWithOwner": "Tkkdy/Morning-Radar",
                        "issues": {
                            "nodes": [issue],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }
                }
            },
        )
    )
    coordinator = ReleaseCoordinator(
        root,
        archive=GitArchive(root, allow_synthetic=True),
        synthetic=True,
        provider=ReleaseDemoProvider(),
    )
    with patch(
        "morning_radar.intake.service._production_collect", return_value=(collection, [], [])
    ):
        result = coordinator.generate(
            token="synthetic", transport=transport, now=clock, identity="synthetic-release-1"
        )
    output = root / ".tmp/preview"
    coordinator.export(result["release_id"], output)
    return {
        "synthetic": True,
        "release_id": result["release_id"],
        "preview": str(output / "index.html"),
        "manifest": str(
            coordinator.directory / "packages" / result["release_id"] / "manifest.json"
        ),
    }


class ReleaseDemoProvider(SyntheticEditorProvider):
    """Authored C response grounded only in the labeled port fixture."""

    def discover_hypotheses(self, context):
        context = {
            **context,
            "evidence": [e for e in context["evidence"] if e["input_id"].startswith("demo-input-")],
        }
        return super().discover_hypotheses(context)

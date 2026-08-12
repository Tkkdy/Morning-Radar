"""Render a static, GitHub Pages-ready site from DailyBrief JSON data."""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

from morning_radar.models import DailyBrief, DailyContinuity, JudgementUpdateKind


class SiteBuilder:
    def __init__(self, *, template_dir: Path, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.environment = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(("html", "xml")),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.environment.filters["hostname"] = _hostname

    def build(
        self,
        briefs: list[DailyBrief],
        *,
        stylesheet: Path,
        continuities: list[DailyContinuity] | None = None,
    ) -> None:
        if not briefs:
            raise ValueError("At least one DailyBrief is required to build the site")
        ordered = sorted(briefs, key=lambda item: item.date, reverse=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "briefs").mkdir(exist_ok=True)
        (self.output_dir / "assets").mkdir(exist_ok=True)
        stylesheet_destination = self.output_dir / "assets" / "style.css"
        if stylesheet.resolve() != stylesheet_destination.resolve():
            shutil.copyfile(stylesheet, stylesheet_destination)
        latest = ordered[0]
        annotations = _historical_judgement_annotations(continuities or [])
        self._render(
            "index.html.j2",
            self.output_dir / "index.html",
            brief=latest,
            root_prefix="",
            historical_annotations=annotations.get(latest.date, {}),
        )
        self._render("archive.html.j2", self.output_dir / "archive.html", briefs=ordered)
        for brief in ordered:
            self._render(
                "brief.html.j2",
                self.output_dir / "briefs" / f"{brief.date}.html",
                brief=brief,
                root_prefix="../",
                historical_annotations=annotations.get(brief.date, {}),
            )

    def _render(self, template: str, destination: Path, **context: object) -> None:
        destination.write_text(
            self.environment.get_template(template).render(**context),
            encoding="utf-8",
        )


def _hostname(url: str) -> str:
    """Return a safe display hostname without changing the source URL."""
    hostname = urlsplit(url).hostname or ""
    return hostname.removeprefix("www.")


def _historical_judgement_annotations(
    continuities: list[DailyContinuity],
) -> dict[object, dict[str, list[dict[str, str]]]]:
    records = {
        judgement.judgement_id: judgement
        for daily in continuities
        for judgement in daily.judgements
    }
    roots = {
        judgement.root_judgement_id: judgement
        for judgement in records.values()
        if judgement.updates_judgement_id is None
    }
    result: dict[object, dict[str, list[dict[str, str]]]] = {}
    visible_updates = {
        JudgementUpdateKind.WEAKENED,
        JudgementUpdateKind.REVISED,
        JudgementUpdateKind.OVERTURNED,
    }
    for daily in continuities:
        for judgement in daily.judgements:
            if judgement.update_kind not in visible_updates:
                continue
            root = roots.get(judgement.root_judgement_id)
            if root is None:
                continue
            annotation = {
                "date": str(daily.date),
                "kind": judgement.update_kind.value,
                "claim": judgement.claim,
            }
            for evidence in root.evidence_refs:
                by_story = result.setdefault(evidence.story.date, {})
                by_story.setdefault(evidence.story.story_id, []).append(annotation)
    return result

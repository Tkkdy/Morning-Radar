"""Render a static, GitHub Pages-ready site from DailyBrief JSON data."""

from __future__ import annotations

import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from morning_radar.models import DailyBrief


class SiteBuilder:
    def __init__(self, *, template_dir: Path, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.environment = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(("html", "xml")),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def build(self, briefs: list[DailyBrief], *, stylesheet: Path) -> None:
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
        self._render("index.html.j2", self.output_dir / "index.html", brief=latest)
        self._render("archive.html.j2", self.output_dir / "archive.html", briefs=ordered)
        for brief in ordered:
            self._render(
                "brief.html.j2",
                self.output_dir / "briefs" / f"{brief.date}.html",
                brief=brief,
            )

    def _render(self, template: str, destination: Path, **context: object) -> None:
        destination.write_text(
            self.environment.get_template(template).render(**context),
            encoding="utf-8",
        )

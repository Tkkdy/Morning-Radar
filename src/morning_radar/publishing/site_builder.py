"""Render a static, GitHub Pages-ready site from DailyBrief JSON data."""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

from morning_radar.models import DailyBrief, DailyContinuity, JudgementUpdateKind
from morning_radar.publishing.status import write_radar_status


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
        preview_notice: str | None = None,
        preview_labels: dict[str, str] | None = None,
        tendency_workspace: Path | None = None,
        local_workbench_url: str | None = None,
    ) -> None:
        if local_workbench_url is not None:
            target = urlsplit(local_workbench_url)
            if target.scheme != "http" or target.hostname != "127.0.0.1":
                raise ValueError("Local workbench link must use loopback HTTP")
        self.environment.globals["local_workbench_url"] = local_workbench_url
        if not briefs:
            raise ValueError("At least one DailyBrief is required to build the site")
        # Legacy model approvals have no human confirmation binding. Keep the saved
        # brief intact; public Tendency content comes only from CoreStore's view.
        ordered = sorted((brief.model_copy(update={"tendencies": []}) for brief in briefs),
                         key=lambda item: item.date, reverse=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "briefs").mkdir(exist_ok=True)
        (self.output_dir / "assets").mkdir(exist_ok=True)
        stylesheet_destination = self.output_dir / "assets" / "style.css"
        if stylesheet.resolve() != stylesheet_destination.resolve():
            shutil.copyfile(stylesheet, stylesheet_destination)
        if tendency_workspace is not None:
            from morning_radar.tendencies.core_store import CoreStore
            from morning_radar.tendencies.web_pages import build_pages
            build_pages(CoreStore(tendency_workspace), self.output_dir / "tendency",
                        repository=Path(self.environment.loader.searchpath[0]).parent)
        self.environment.globals["rendered_story_anchors"] = {
            brief.date: {
                card.story_contexts[0].story_id if card.story_contexts else card.story_ids[0]
                for section in ("top_stories", "market_and_companies", "ai_and_open_source",
                                "trend_radar", "developer_discussions", "other_reading")
                for card in getattr(brief, section)
                if card.story_contexts or card.story_ids
            }
            for brief in ordered
        }
        latest = ordered[0]
        self._write_status(latest)
        from morning_radar.health import publish_health
        publish_health(self.output_dir.parent, str(latest.date))
        annotations = _historical_judgement_annotations(continuities or [])
        self._render(
            "index.html.j2",
            self.output_dir / "index.html",
            tendency_entry=tendency_workspace is not None,
            brief=latest,
            root_prefix="",
            historical_annotations=annotations.get(latest.date, {}),
            preview_notice=preview_notice, preview_labels=preview_labels or {},
        )
        self._render("archive.html.j2", self.output_dir / "archive.html", briefs=ordered,
                     tendency_entry=tendency_workspace is not None)
        for brief in ordered:
            self._render(
                "brief.html.j2",
                self.output_dir / "briefs" / f"{brief.date}.html",
                tendency_entry=tendency_workspace is not None,
                brief=brief,
                root_prefix="../",
                historical_annotations=annotations.get(brief.date, {}),
                preview_notice=preview_notice, preview_labels=preview_labels or {},
            )

    def _render(self, template: str, destination: Path, **context: object) -> None:
        destination.write_text(
            self.environment.get_template(template).render(**context),
            encoding="utf-8",
        )

    def _write_status(self, brief: DailyBrief) -> None:
        """Write the public readiness contract for the latest generated brief."""
        write_radar_status(
            self.output_dir / "status.json",
            run_date=brief.date,
            status="SUCCESS",
            detail="Morning brief generated",
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

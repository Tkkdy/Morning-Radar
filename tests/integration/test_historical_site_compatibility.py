from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from morning_radar.models import DailyBrief, Story
from morning_radar.publishing import SiteBuilder
from morning_radar.storage import load_model, load_models


class _InternalLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag not in {"a", "link"}:
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


def test_tracked_history_parses_renders_and_has_no_broken_internal_links(
    tmp_path: Path,
) -> None:
    root = Path(".").resolve()
    brief_paths = sorted((root / "data/briefs").glob("*.json"))
    story_paths = sorted((root / "data/stories").glob("*.json"))
    briefs = [load_model(path, DailyBrief) for path in brief_paths]
    for path in story_paths:
        load_models(path, Story)
    assert briefs
    assert len(story_paths) == len(brief_paths)

    output = tmp_path / "site"
    SiteBuilder(template_dir=root / "templates", output_dir=output).build(
        briefs,
        stylesheet=root / "site/assets/style.css",
    )

    html_paths = sorted(output.rglob("*.html"))
    assert len(html_paths) == len(briefs) + 2
    broken: list[tuple[str, str]] = []
    for html_path in html_paths:
        parser = _InternalLinkParser()
        parser.feed(html_path.read_text(encoding="utf-8"))
        for href in parser.hrefs:
            parsed = urlsplit(href)
            if parsed.scheme or parsed.netloc or href.startswith(("#", "mailto:")):
                continue
            target = (html_path.parent / parsed.path).resolve()
            if not target.exists():
                broken.append((str(html_path.relative_to(output)), href))

    assert broken == []
    archive = (output / "archive.html").read_text(encoding="utf-8")
    for brief in briefs:
        page = output / "briefs" / f"{brief.date}.html"
        rendered = page.read_text(encoding="utf-8")
        assert 'href="../assets/style.css"' in rendered
        assert 'href="../archive.html"' in rendered
        assert 'href="../index.html"' in rendered
        assert f'href="briefs/{brief.date}.html"' in archive

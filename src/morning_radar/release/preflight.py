"""Package checks; relative navigation must work below a Pages repository prefix."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in {"href", "src"} and value:
                self.links.append(value)


def check_links(root: Path) -> None:
    for page in root.rglob("*.html"):
        parser = References()
        parser.feed(page.read_text(encoding="utf-8"))
        for value in parser.links:
            url = urlsplit(value)
            if url.scheme or url.netloc or not url.path:
                continue
            if url.path.startswith("/"):
                raise ValueError("Root-relative link breaks project Pages prefix")
            target = (page.parent / unquote(url.path)).resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                raise ValueError("Missing local package reference: " + value)

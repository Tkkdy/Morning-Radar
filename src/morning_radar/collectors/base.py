"""Small collector contract shared by production and fixture adapters."""

from __future__ import annotations

from typing import Protocol

from morning_radar.models import RawItem


class Collector(Protocol):
    name: str

    def collect(self) -> list[RawItem]:
        """Return normalized domain objects or raise a source-scoped error."""
        ...


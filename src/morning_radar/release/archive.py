"""Explicit Git archive; remote conflicts never merge generated JSON implicitly."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from threading import RLock

# Exact data families, no site/, caches, credentials, or arbitrary diagnostics.
FAMILIES = (
    "intake",
    "raw",
    "snapshots",
    "state",
    "briefs",
    "stories",
    "continuity",
    "radar_signals",
    "evidence",
    "usage",
    "health",
    "releases/v1",
)


class ArchiveConflict(RuntimeError):
    pass


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        encoding="utf-8",
        stderr=subprocess.PIPE,
    ).strip()


def archive_files(root: Path) -> list[str]:
    paths = set()
    for family in FAMILIES:
        for path in (root / "data" / family).rglob("*"):
            if path.is_file() and path.suffix in {".json", ".html", ".css", ".js"}:
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError("Archive symlink refused")
                paths.add(path.relative_to(root).as_posix())
    projection = root / "data/tendency-public/v1/confirmed.json"
    if projection.is_file():
        if projection.is_symlink():
            raise ValueError("Archive symlink refused")
        paths.add(projection.relative_to(root).as_posix())
    return sorted(paths)


class GitArchive:
    """The Actions queue is the distributed lock; Git push is the final CAS.

    A rejected push leaves the local commit intact. Recovery uses a fresh authoritative
    checkout plus a separately restored private store for Tendency; it must not
    rebase mutable derived state. The public projection cannot restore private drafts.
    """

    def __init__(
        self,
        root: Path,
        *,
        remote: str | None = None,
        branch: str = "master",
        allow_synthetic: bool = False,
    ):
        self.root, self.remote, self.branch = root, remote, branch
        self.allow_synthetic = allow_synthetic
        self.lock = RLock()

    def __call__(self, reason: str) -> str:
        with self.lock:
            return self._commit(reason)

    def _commit(self, reason: str) -> str:
        if git(self.root, "diff", "--cached", "--name-only"):
            raise ArchiveConflict("Unrelated staged changes; archive refused")
        # A path already tracked remains in every pushed tree even without git add.
        private_tracked = git(self.root, "ls-files", "--", "data/tendency-core")
        if private_tracked:
            raise ArchiveConflict(
                "Private Tendency state is already tracked; public migration requires owner action"
            )
        from morning_radar.tendencies.core_export import save_public_projection
        from morning_radar.tendencies.core_store import CoreStore
        save_public_projection(CoreStore(self.root))
        files = archive_files(self.root)
        if not self.allow_synthetic:
            for name in files:
                if name.endswith(".json"):
                    value = json.loads((self.root / name).read_text(encoding="utf-8"))
                    if contains_synthetic(value):
                        raise ValueError("Synthetic data cannot enter production archive")
        for offset in range(0, len(files), 100):
            git(self.root, "add", "--", *files[offset : offset + 100])
        if git(self.root, "diff", "--cached", "--name-only"):
            git(
                self.root,
                "-c",
                "user.name=github-actions[bot]",
                "-c",
                "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "commit",
                "-m",
                "chore: archive release " + reason,
            )
        head = git(self.root, "rev-parse", "HEAD")
        if self.remote:
            try:
                git(self.root, "push", self.remote, "HEAD:refs/heads/" + self.branch)
            except subprocess.CalledProcessError as exc:
                raise ArchiveConflict("Archive push rejected; preserve commit " + head) from exc
        return head


def contains_synthetic(value) -> bool:
    if isinstance(value, dict):
        return value.get("synthetic") is True or any(contains_synthetic(v) for v in value.values())
    return isinstance(value, list) and any(contains_synthetic(v) for v in value)

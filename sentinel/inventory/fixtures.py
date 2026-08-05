"""Fixture/dev surface provider: network-free by construction.

Reads the frozen fixture corpus read-only — never writes, never
creates, never caches inside ``fixtures_root``. ``tests/test_fixture_corpus.py``
depends on that tree staying byte-for-byte identical to what's
committed, so this provider (and anything built on it) must never
write under it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from checks.base import normalize_text
from sentinel.config import (
    ADR_0004_REQUIRED_README_SECTIONS,
    FIXTURE_LINK_SCANNED_PATHS,
    FIXTURE_REQUIRED_FILES,
)
from sentinel.inventory.base import (
    Content,
    ConfirmedAbsent,
    Fetch,
    RepoPolicy,
    RepoSurface,
    Unknown,
)


def _fixture_policy() -> RepoPolicy:
    return RepoPolicy(
        required_paths=FIXTURE_REQUIRED_FILES,
        link_scanned_paths=FIXTURE_LINK_SCANNED_PATHS,
        readme_structure_applicable=True,
        required_readme_sections=ADR_0004_REQUIRED_README_SECTIONS,
        enforce_readme_order=True,
        policy_source_path=None,
    )


def _make_fetch(owner_dir: Path) -> Fetch:
    def fetch(path: str):
        target = owner_dir / path
        try:
            if not target.is_file():
                return ConfirmedAbsent()
            return Content(text=normalize_text(target.read_bytes()))
        except OSError as exc:
            return Unknown(reason=f"{type(exc).__name__}: {exc}")

    return fetch


@dataclass(frozen=True)
class FixtureSurfaceProvider:
    fixtures_root: Path

    def repos(self) -> Sequence[RepoSurface]:
        policy = _fixture_policy()
        owners = sorted(d.name for d in self.fixtures_root.iterdir() if d.is_dir())
        return [
            RepoSurface(owner=name, fetch=_make_fetch(self.fixtures_root / name), policy=policy)
            for name in owners
        ]

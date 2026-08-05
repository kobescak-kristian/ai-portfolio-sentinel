"""Shared fixtures for the Phase 2 test suite.

Everything here operates on ``tmp_path`` copies or in-memory fakes —
never the real ``fixtures/repos`` tree (``tests/test_fixture_corpus.py``
depends on that tree staying byte-for-byte identical to what's
committed).
"""

from __future__ import annotations

import shutil
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import pytest

from checks.judgment.stubs import NullJudgmentStub
from sentinel.config import ADR_0004_REQUIRED_README_SECTIONS, RunConfig
from sentinel.ids import FrozenClock, NoOpSleeper, SeededIdFactory
from sentinel.inventory.base import Content, ConfirmedAbsent, RepoPolicy, RepoSurface
from sentinel.net.client import FakeHttpClient
from sentinel.net.links import StaticLinkResolver
from sentinel.pipeline import Deps, RunHooks


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Structural enforcement: no test may reach the live network.
    Fixture/dev tests use FixtureSurfaceProvider or in-memory fakes;
    live-shaped tests use FakeHttpClient. A real socket.connect here
    is always a test bug."""

    def _guard(*_args, **_kwargs):
        raise AssertionError("live network access attempted in a test")

    monkeypatch.setattr(socket.socket, "connect", _guard)
    yield


class SimulatedCrash(BaseException):
    """Raised by an injected RunHooks callback to simulate a real
    process crash at an exact pipeline point. Deliberately a direct
    ``BaseException`` subclass, not ``Exception`` — the pipeline's
    failure-containment handler catches ``Exception`` only, exactly
    as it would fail to catch a real SIGKILL/power-loss; a
    RuntimeError-based double would be silently contained instead of
    propagating, which is not what a crash-consistency test needs."""


def crash_at(hook_name: str) -> RunHooks:
    def _raise(*_args, **_kwargs):
        raise SimulatedCrash(f"simulated crash at {hook_name}")

    return RunHooks(**{hook_name: _raise})


@pytest.fixture
def fixed_clock():
    def _make(*ticks: datetime) -> FrozenClock:
        return FrozenClock(ticks=list(ticks))

    return _make


T0 = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)
T3 = T0 + timedelta(hours=3)


@pytest.fixture
def seeded_ids():
    def _make(run_ids: Sequence[str] = ("run-001",)) -> SeededIdFactory:
        return SeededIdFactory(run_ids=list(run_ids))

    return _make


def make_in_memory_fetch(files: dict[str, str]):
    def fetch(path: str):
        if path not in files:
            return ConfirmedAbsent()
        return Content(text=files[path])

    return fetch


def make_repo_surface(
    owner: str,
    files: dict[str, str],
    *,
    required_paths: tuple[str, ...] = (),
    link_scanned_paths: tuple[str, ...] = ("README.md", "EVAL_RESULTS.md"),
    readme_structure_applicable: bool = True,
    required_readme_sections: tuple[str, ...] = ADR_0004_REQUIRED_README_SECTIONS,
    enforce_readme_order: bool = True,
    policy_source_path: str | None = None,
) -> RepoSurface:
    policy = RepoPolicy(
        required_paths=required_paths,
        link_scanned_paths=link_scanned_paths,
        readme_structure_applicable=readme_structure_applicable,
        required_readme_sections=required_readme_sections,
        enforce_readme_order=enforce_readme_order,
        policy_source_path=policy_source_path,
    )
    return RepoSurface(owner=owner, fetch=make_in_memory_fetch(files), policy=policy)


class ListSurfaceProvider:
    """A fake SurfaceProvider — a fixed list of RepoSurfaces."""

    def __init__(self, repos: Sequence[RepoSurface]):
        self._repos = list(repos)

    def repos(self) -> Sequence[RepoSurface]:
        return self._repos


@pytest.fixture
def make_deps():
    def _make(
        *,
        clock=None,
        ids=None,
        http=None,
        surface_provider=None,
        link_resolver=None,
        judgment=None,
        hooks: RunHooks = RunHooks(),
    ) -> Deps:
        return Deps(
            clock=clock or FrozenClock(ticks=[T0, T1, T2, T3]),
            ids=ids or SeededIdFactory(run_ids=["run-001"]),
            sleeper=NoOpSleeper(),
            http=http,
            surface_provider=surface_provider,
            link_resolver=link_resolver or StaticLinkResolver(mapping={}),
            judgment=judgment or NullJudgmentStub(),
            hooks=hooks,
        )

    return _make


@pytest.fixture
def make_config():
    def _make(tmp_path: Path, **overrides) -> RunConfig:
        defaults = dict(
            run_kind="dev",
            source="fixtures",
            db_path=tmp_path / "sentinel.sqlite3",
            findings_path=tmp_path / "FINDINGS.md",
            log_path=tmp_path / "run.jsonl",
            cost_ledger_path=tmp_path / "cost_ledger.jsonl",
            fixtures_root=tmp_path / "fixtures",
        )
        defaults.update(overrides)
        return RunConfig(**defaults)

    return _make


@pytest.fixture
def corpus_copy(tmp_path: Path):
    """Copy one or more named fixture snapshots into tmp_path — the
    only way any test may touch fixture content, never the live path."""

    def _copy(*names: str) -> Path:
        root = Path("fixtures/repos")
        dest_root = tmp_path / "fixtures_copy"
        dest_root.mkdir(exist_ok=True)
        for name in names:
            shutil.copytree(root / name, dest_root / name)
        return dest_root

    return _copy


@pytest.fixture
def fake_http():
    def _make(responses: dict) -> FakeHttpClient:
        return FakeHttpClient(responses=responses)

    return _make


__all__ = [
    "SimulatedCrash",
    "crash_at",
    "make_in_memory_fetch",
    "make_repo_surface",
    "ListSurfaceProvider",
    "T0",
    "T1",
    "T2",
    "T3",
]

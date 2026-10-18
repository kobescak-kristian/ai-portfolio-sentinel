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


# ---------------------------------------------------------------------------
# Phase-5 pre-establishment view (2026-10-18). The replacement receipts and
# their CostRows were appended to the committed registry and cost ledger; the
# P5-D readiness/gate tooling tests describe the world BEFORE that append.
# Both appends were pure, so the first lines of each committed file are
# byte-for-byte the earlier file (same precedent as the latch GENESIS view).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
PHASE5_PRE_ESTABLISHMENT_LINES = {
    "artifacts/phase5_receipt_registry.jsonl": 4,
    "telemetry/cost_ledger.jsonl": 42,
}


class Phase5PreEstablishmentView:
    def __init__(self, root: Path):
        self.paths = {}
        for rel, count in PHASE5_PRE_ESTABLISHMENT_LINES.items():
            lines = (_REPO_ROOT / rel).read_bytes().replace(b"\r\n", b"\n").split(b"\n")
            assert len(lines) - 1 >= count, rel
            out = root / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"".join(line + b"\n" for line in lines[:count]))
            self.paths[rel] = out

    def path(self, rel: str) -> Path:
        return self.paths[rel]

    def redirect(self, path) -> Path:
        for rel, view in self.paths.items():
            if Path(path).resolve() == (_REPO_ROOT / rel).resolve():
                return view
        return Path(path)

    def patch_readiness(self, monkeypatch, module, *source_classes) -> None:
        """Route a readiness script's registry/ledger reads of the committed
        files to the view: ``registry_facts``/``ledger_facts`` and each
        Sources class's ``read_bytes``."""
        for name in ("registry_facts", "ledger_facts"):
            original = getattr(module, name)
            monkeypatch.setattr(
                module, name, lambda path, *a, _original=original, **k: _original(self.redirect(path), *a, **k)
            )
        for cls in (module.Sources, *source_classes):
            original_read = cls.read_bytes
            monkeypatch.setattr(
                cls, "read_bytes",
                lambda src, rel, _original=original_read: (
                    self.paths[rel].read_bytes()
                    if rel in self.paths and Path(src.root).resolve() == _REPO_ROOT.resolve()
                    else _original(src, rel)
                ),
            )

    def patch_durable_history(self, monkeypatch, common) -> None:
        """``common.load_durable_history()`` (the default committed path)
        reads the view; ``common`` is the loaded ``scripts/_phase5_common``
        module, which a script loads before any of its functions run."""
        original = common.load_durable_history
        view = self.paths["artifacts/phase5_receipt_registry.jsonl"]
        monkeypatch.setattr(
            common, "load_durable_history",
            lambda registry_path=None: original(registry_path if registry_path is not None else view),
        )


@pytest.fixture
def phase5_pre_establishment(tmp_path_factory) -> Phase5PreEstablishmentView:
    return Phase5PreEstablishmentView(tmp_path_factory.mktemp("phase5-pre-establishment"))


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

"""Inventory protocols: the 3-state fetch result (C1), per-repo policy
(C8-round-2), and deterministic work-unit derivation.

Work units are derived from an *expectation* list, never from what
currently exists — this is what makes deletions resolvable (C2:
carry-forward spans all six classes). ``RepoPolicy.required_paths``
and ``.link_scanned_paths`` are expected to already include any
carry-forward union computed by the caller (sentinel.pipeline) from
currently-OPEN ledger findings, before ``build_work_units`` runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence, Union


@dataclass(frozen=True)
class Content:
    """The fetch succeeded and the path exists — normalized (BOM
    stripped, LF newlines) UTF-8 text."""

    text: str


@dataclass(frozen=True)
class ConfirmedAbsent:
    """A definitive negative result: HTTP 404/410, or (for a whole
    repo) the repo is confirmed gone after a complete, successful
    inventory pagination. Never returned for a transient failure."""


@dataclass(frozen=True)
class Unknown:
    """The fetch could not reach a confirmed result: timeout,
    connection/DNS/TLS error, rate limit, 5xx, or content that could
    not be parsed deterministically where parsing was required. Per
    C1, a task fed an Unknown result must end FAILED->DEAD_LETTER,
    never DONE — the caller must never guess."""

    reason: str


FetchResult = Union[Content, ConfirmedAbsent, Unknown]

Fetch = Callable[[str], FetchResult]


@dataclass(frozen=True)
class RepoPolicy:
    """Per-repo, per-run applicable policy. Fixture/eval mode builds a
    single frozen policy (sentinel.config.FIXTURE_REQUIRED_FILES /
    FIXTURE_LINK_SCANNED_PATHS / ADR_0004_REQUIRED_README_SECTIONS,
    order enforced). Live mode derives every field from that specific
    repository's own public policy surfaces, per run (C8-round-2) —
    never from Sentinel's own convention, another repo's convention,
    or any private governance record.
    """

    required_paths: tuple[str, ...]
    link_scanned_paths: tuple[str, ...]
    readme_structure_applicable: bool
    required_readme_sections: tuple[str, ...]
    enforce_readme_order: bool
    # The one required_path (if any) whose own content is also this
    # repo's live policy source (its actively-invoked gate file). The
    # missing-required-file checker parses it a second time for this
    # path specifically and dead-letters rather than silently passing
    # if it exists but can't be parsed deterministically.
    policy_source_path: str | None = None


@dataclass(frozen=True)
class RepoSurface:
    """One monitored owner: a live repo or a fixture snapshot
    directory, plus how to fetch a repo-relative path's content and
    the policy that governs which checks apply to it."""

    owner: str
    fetch: Fetch
    policy: RepoPolicy


@dataclass(frozen=True)
class WorkUnitSpec:
    """One (surface, check_class) task to create, plus the specific
    repo-relative path that check inspects."""

    surface: str
    check_class: str
    repo: RepoSurface
    detail_path: str


def _dedupe_repos_by_owner(repos: Sequence[RepoSurface]) -> list[RepoSurface]:
    """A repo appearing twice in one run's inventory (e.g. a paginated
    listing returning the same name on two pages) collapses to a
    single entry — first occurrence wins — *before* task creation, so
    a doubled inventory entry never doubles the task/finding count."""
    seen: dict[str, RepoSurface] = {}
    for repo in repos:
        seen.setdefault(repo.owner, repo)
    return list(seen.values())


def build_work_units(repos: Sequence[RepoSurface]) -> list[WorkUnitSpec]:
    units: list[WorkUnitSpec] = []
    for repo in _dedupe_repos_by_owner(repos):
        policy = repo.policy
        for path in policy.link_scanned_paths:
            units.append(
                WorkUnitSpec(
                    surface=f"{repo.owner}/{path}",
                    check_class="broken-link",
                    repo=repo,
                    detail_path=path,
                )
            )
            units.append(
                WorkUnitSpec(
                    surface=f"{repo.owner}/{path}",
                    check_class="missing-synthetic-label",
                    repo=repo,
                    detail_path=path,
                )
            )
        units.append(
            WorkUnitSpec(
                surface=f"{repo.owner}/README.md",
                check_class="number-mismatch",
                repo=repo,
                detail_path="README.md",
            )
        )
        if policy.readme_structure_applicable:
            units.append(
                WorkUnitSpec(
                    surface=f"{repo.owner}/README.md",
                    check_class="readme-structure",
                    repo=repo,
                    detail_path="README.md",
                )
            )
        units.append(
            WorkUnitSpec(
                surface=f"{repo.owner}/STATE.md",
                check_class="stale-STATE-marker",
                repo=repo,
                detail_path="STATE.md",
            )
        )
        for required_path in policy.required_paths:
            units.append(
                WorkUnitSpec(
                    surface=f"{repo.owner}/{required_path}",
                    check_class="missing-required-file",
                    repo=repo,
                    detail_path=required_path,
                )
            )
    units.sort(key=lambda u: (u.surface, u.check_class))
    return units


class SurfaceProvider:
    """Protocol (structural): produces the list of monitored repos
    for one run. FixtureSurfaceProvider and the live GitHub provider
    both satisfy this shape without inheriting from it."""

    def repos(self) -> Sequence[RepoSurface]:  # pragma: no cover - protocol
        raise NotImplementedError

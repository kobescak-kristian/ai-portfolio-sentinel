"""Shared checker plumbing: the observed-finding shape, the
confirmed/inconclusive outcome type (C1), the checker registry, and
text normalization.

``normalize_text``/``split_lines`` are mandatory preprocessing for
every checker that reads file content — Phase 2 is authored on
Windows and gated on ubuntu-latest; without LF normalization, line
numbers (and therefore fingerprints) would differ by platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, NamedTuple, Protocol, Union

from sentinel.inventory.base import Fetch

if TYPE_CHECKING:
    from checks.judgment.stubs import JudgmentStub
    from sentinel.net.links import LinkResolver


def normalize_text(raw: bytes) -> str:
    """Decode UTF-8 (stripping a BOM if present) and normalize
    newlines to LF only."""
    text = raw.decode("utf-8-sig")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_lines(text: str) -> list[str]:
    return text.split("\n")


@dataclass(frozen=True)
class ObservedFinding:
    """What a checker emits — not yet a ledger Finding. sentinel.lifecycle
    turns this into a Finding via compute_content_hash/compute_fingerprint,
    which is why normalized_content must be stable across reruns and
    must never include anything that changes without the underlying
    defect changing (e.g. an HTTP status code, a byte count)."""

    surface: str
    check_class: str
    location: str
    detail: str
    normalized_content: str


class Confirmed(NamedTuple):
    """The check reached a definitive result — the task ends DONE.
    An empty findings list is a legitimate, common result (nothing
    wrong, or nothing to check because the content is confirmed
    absent for a content-inspection class)."""

    findings: list[ObservedFinding]


class Inconclusive(NamedTuple):
    """The check could not reach a definitive result — the task ends
    FAILED->DEAD_LETTER and its scope is excluded from auto-resolve
    this run (C1). Never emit findings alongside this."""

    reason: str


CheckOutcome = Union[Confirmed, Inconclusive]


@dataclass(frozen=True)
class CheckContext:
    """Everything one checker invocation needs. ``fetch`` resolves any
    repo-relative path for this task's owner. ``required_readme_sections``
    and ``enforce_readme_order`` are only meaningful to readme-structure;
    ``link_resolver`` only to broken-link; ``judgment`` only to the two
    stub adapters — each checker reads only the fields it needs."""

    owner: str
    detail_path: str
    fetch: Fetch
    link_resolver: "LinkResolver"
    judgment: "JudgmentStub"
    required_readme_sections: tuple[str, ...] = ()
    enforce_readme_order: bool = False
    # True only for the missing-required-file task whose detail_path is
    # this repo's own live policy source (RepoPolicy.policy_source_path).
    # That task must also confirm the content parses deterministically —
    # present-but-unparseable dead-letters rather than silently passing.
    policy_parse_required: bool = False


class Checker(Protocol):
    def __call__(self, ctx: CheckContext) -> CheckOutcome: ...


CHECKERS: dict[str, Checker] = {}


def register_checker(check_class: str) -> Callable[[Checker], Checker]:
    def _register(fn: Checker) -> Checker:
        CHECKERS[check_class] = fn
        return fn

    return _register

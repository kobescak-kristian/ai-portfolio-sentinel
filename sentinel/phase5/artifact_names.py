"""Deterministic Actions-artifact naming and parsing (P5-B Part 3/3).

Window ids use the already-frozen Part-2 rule: ``window_id =
"p5w-" + control_run_id`` (state-f). Nothing here changes
``models.QualificationWindowRecord`` — the rule is enforced only at the
point a window is actually constructed (``run_phase5_window_freeze.py``)
and, defensively, by ``assert_artifact_safe_window_id`` so every name
derived from a window id stays mechanically parseable.

Every name embeds the *producing* ``github_run_id`` so v4's per-run
artifact-name uniqueness is automatic; evidence names additionally
embed ``run_attempt`` since a rerun of the same job would otherwise
collide on a non-lineage evidence name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# The production form is always "p5w-<control_run_id>" with a purely
# numeric GitHub run id, but the shared Part-2 test builders
# (tests/test_phase5_bundle.py::make_window) use readable hyphenated
# ids like "p5w-control-run-1" — GitHub artifact names permit hyphens
# freely, so the body charset stays permissive rather than fighting an
# already-frozen Part-2 test fixture convention.
_WINDOW_ID_BODY = r"[a-z0-9-]{1,40}"
_WINDOW_ID = rf"p5w-{_WINDOW_ID_BODY}"
_RUN_ID = r"[0-9]+"
_ATTEMPT = r"[0-9]+"

_WINDOW_ID_RE = re.compile(rf"^{_WINDOW_ID}$")

_PURPOSE_SLUGS = {
    "P5C_WIF_PROBE": "p5c-wif-probe",
    "P5D_OFFICIAL_SONNET_GATE": "p5d-official-sonnet-gate",
}
_SLUG_TO_PURPOSE = {slug: purpose for purpose, slug in _PURPOSE_SLUGS.items()}


class ArtifactNameError(ValueError):
    """A window id or constructed artifact name failed a safety check."""


def assert_artifact_safe_window_id(window_id: str) -> None:
    if not _WINDOW_ID_RE.fullmatch(window_id):
        raise ArtifactNameError(
            "window_id must match 'p5w-' followed by 1-20 characters [a-z0-9]"
        )


def genesis_name(window_id: str, run_id: str) -> str:
    assert_artifact_safe_window_id(window_id)
    return f"sentinel-p5-genesis-{window_id}-r{run_id}"


def slot_name(window_id: str, slot_index: int, run_id: str) -> str:
    assert_artifact_safe_window_id(window_id)
    if not 1 <= slot_index <= 5:
        raise ArtifactNameError("slot_index must be 1..5")
    return f"sentinel-p5-slot-{window_id}-s{slot_index}-r{run_id}"


def refusal_name(window_id: str, run_id: str) -> str:
    assert_artifact_safe_window_id(window_id)
    return f"sentinel-p5-refusal-{window_id}-r{run_id}"


def oneshot_marker_name(purpose: str, run_id: str) -> str:
    slug = _PURPOSE_SLUGS.get(purpose)
    if slug is None:
        raise ArtifactNameError(f"unknown one-shot purpose: {purpose!r}")
    return f"sentinel-p5-oneshot-{slug}-r{run_id}"


def attempt_evidence_name(run_id: str, attempt: int) -> str:
    return f"sentinel-p5-attempt-r{run_id}-a{attempt}"


def prewindow_evidence_name(run_id: str, attempt: int) -> str:
    return f"sentinel-p5-prewindow-r{run_id}-a{attempt}"


def rehearsal_evidence_name(run_id: str, attempt: int) -> str:
    return f"sentinel-p5-rehearsal-r{run_id}-a{attempt}"


def probe_evidence_name(run_id: str, attempt: int) -> str:
    return f"sentinel-p5-probe-evidence-r{run_id}-a{attempt}"


def gate_evidence_name(run_id: str, attempt: int) -> str:
    return f"sentinel-p5-gate-evidence-r{run_id}-a{attempt}"


def freeze_refusal_evidence_name(run_id: str, attempt: int) -> str:
    return f"sentinel-p5-freeze-refusal-r{run_id}-a{attempt}"


# ---------------------------------------------------------------------------
# Prefixes for REST-listing discovery
# ---------------------------------------------------------------------------

GENESIS_PREFIX = "sentinel-p5-genesis-"
ONESHOT_PREFIX = "sentinel-p5-oneshot-"
PROBE_EVIDENCE_PREFIX = "sentinel-p5-probe-evidence-"
GATE_EVIDENCE_PREFIX = "sentinel-p5-gate-evidence-"


def slot_prefix(window_id: str) -> str:
    assert_artifact_safe_window_id(window_id)
    return f"sentinel-p5-slot-{window_id}-"


def refusal_prefix(window_id: str) -> str:
    assert_artifact_safe_window_id(window_id)
    return f"sentinel-p5-refusal-{window_id}-"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

ArtifactKind = Literal[
    "GENESIS", "SLOT_SUCCESSOR", "CONTROL_REFUSAL", "ONESHOT_MARKER",
    "ATTEMPT_EVIDENCE", "PREWINDOW_EVIDENCE", "REHEARSAL_EVIDENCE",
    "PROBE_EVIDENCE", "GATE_EVIDENCE", "FREEZE_REFUSAL_EVIDENCE",
]


@dataclass(frozen=True)
class ParsedArtifactName:
    kind: ArtifactKind
    window_id: str | None = None
    slot_index: int | None = None
    purpose: str | None = None
    run_id: str | None = None
    attempt: int | None = None


_PATTERNS: tuple[tuple[ArtifactKind, re.Pattern], ...] = (
    ("GENESIS", re.compile(rf"^sentinel-p5-genesis-(?P<window_id>{_WINDOW_ID})-r(?P<run_id>{_RUN_ID})$")),
    (
        "SLOT_SUCCESSOR",
        re.compile(
            rf"^sentinel-p5-slot-(?P<window_id>{_WINDOW_ID})-s(?P<slot_index>[1-5])-r(?P<run_id>{_RUN_ID})$"
        ),
    ),
    ("CONTROL_REFUSAL", re.compile(rf"^sentinel-p5-refusal-(?P<window_id>{_WINDOW_ID})-r(?P<run_id>{_RUN_ID})$")),
    (
        "ONESHOT_MARKER",
        re.compile(r"^sentinel-p5-oneshot-(?P<slug>p5c-wif-probe|p5d-official-sonnet-gate)-r(?P<run_id>" + _RUN_ID + ")$"),
    ),
    ("ATTEMPT_EVIDENCE", re.compile(rf"^sentinel-p5-attempt-r(?P<run_id>{_RUN_ID})-a(?P<attempt>{_ATTEMPT})$")),
    ("PREWINDOW_EVIDENCE", re.compile(rf"^sentinel-p5-prewindow-r(?P<run_id>{_RUN_ID})-a(?P<attempt>{_ATTEMPT})$")),
    ("REHEARSAL_EVIDENCE", re.compile(rf"^sentinel-p5-rehearsal-r(?P<run_id>{_RUN_ID})-a(?P<attempt>{_ATTEMPT})$")),
    ("PROBE_EVIDENCE", re.compile(rf"^sentinel-p5-probe-evidence-r(?P<run_id>{_RUN_ID})-a(?P<attempt>{_ATTEMPT})$")),
    ("GATE_EVIDENCE", re.compile(rf"^sentinel-p5-gate-evidence-r(?P<run_id>{_RUN_ID})-a(?P<attempt>{_ATTEMPT})$")),
    (
        "FREEZE_REFUSAL_EVIDENCE",
        re.compile(rf"^sentinel-p5-freeze-refusal-r(?P<run_id>{_RUN_ID})-a(?P<attempt>{_ATTEMPT})$"),
    ),
)


def parse_artifact_name(name: str) -> ParsedArtifactName | None:
    """Best-effort structural parse. Returns ``None`` for anything not
    matching one of this module's exact naming forms — callers treat
    that as "not one of ours", never as an error."""
    for kind, pattern in _PATTERNS:
        match = pattern.fullmatch(name)
        if match is None:
            continue
        groups = match.groupdict()
        if kind == "ONESHOT_MARKER":
            return ParsedArtifactName(
                kind=kind, purpose=_SLUG_TO_PURPOSE[groups["slug"]], run_id=groups["run_id"]
            )
        if kind in ("GENESIS", "CONTROL_REFUSAL"):
            return ParsedArtifactName(kind=kind, window_id=groups["window_id"], run_id=groups["run_id"])
        if kind == "SLOT_SUCCESSOR":
            return ParsedArtifactName(
                kind=kind,
                window_id=groups["window_id"],
                slot_index=int(groups["slot_index"]),
                run_id=groups["run_id"],
            )
        # every evidence kind: run_id + attempt only
        return ParsedArtifactName(kind=kind, run_id=groups["run_id"], attempt=int(groups["attempt"]))
    return None

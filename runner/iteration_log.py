"""ITERATION_LOG rendering and append support (ADR-0010 section 5 part 4).

**ITERATION_LOG is DERIVED PUBLIC EVIDENCE. It is NOT authoritative loop
state.** The durable SQLite ledger — ``loop_runs``, ``loop_iterations``,
``runs``, ``tasks``, ``findings`` — together with the durable ``CostRow``s
is authoritative. Everything rendered here is produced FROM that state and
is checked back against it by the gate's self-check. If the two ever
disagree, this file is wrong and the ledger is right.

Import boundary, stated precisely because "stdlib-only" is ambiguous:

* ZERO third-party imports;
* the standard library plus ONE narrow first-party import,
  ``runner.breakers``, for the closed ADR-0010 section 6 stop-reason
  vocabulary — restating that vocabulary here would create a second copy
  that could drift from the supervisor's;
* no ``sentinel.*``, no ``agents.*``, no ``checks.*``, no ``telemetry.*``,
  no provider or network surface of any kind.

It is domain-light derived-evidence code and contains no Sentinel
integration logic: it does not know how a run executes, what a check class
is, how cost is resolved, or where a gate root lives. It receives
already-structured, public-safe evidence objects from its caller and
renders or appends them.

**Public hygiene is a property of the input schema, not of a cleanup
pass.** This module accepts NO caller-supplied free prose. Every value it
accepts is a structured field validated against a closed schema:
identifiers against a strict charset, enums against closed sets, the
source SHA against 40 lowercase hex, timestamps against the ledger's own
timestamp shape, counters against non-negative ints. A machine-local
absolute path, a UNC path or a secret-shaped token cannot satisfy the
identifier charset, so such a value is REJECTED here rather than cleaned
up later. Sanitizing free text belongs at the gate/integration layer,
which reuses the repository's existing ``sentinel.logs.redact`` — this
module deliberately owns no second path/secret taxonomy.

The one human paragraph per section is GENERATED from already-validated
metadata rather than accepted from a caller, which is what makes ADR-0010
section 7's "no numerical fact appears only in prose" true by
construction.

**Marker convention.** Mirrors the proven append semantics of
``sentinel/report.py`` (which is NOT modified), with one deliberate
difference: the closing marker carries no leading slash. A ``/sentinel:``
token would read as a POSIX absolute path to the repository's existing
hygiene mechanism, making our own markers a permanent false positive in
any scan that reuses it. ADR-0010 section 6 asks for markers "equivalent
to" the slash form; these are equivalent and neither marker is a substring
of the other.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Optional, Sequence

from runner.breakers import STOP_REASONS

# --- closed vocabularies ----------------------------------------------------

#: ADR-0010 section 5: the exact machine-recognizable label that makes a
#: section the alert evidence of part 4. No email, Slack, webhook, push
#: notification or dashboard is introduced — this label IS the channel.
PHASE4_FAILURE_ALERT = "PHASE4_FAILURE_ALERT"

SYNTHETIC = "SYNTHETIC"
SEEDED_FAULT = "SEEDED_FAULT"
CLASSIFICATIONS: frozenset[str] = frozenset({SYNTHETIC, SEEDED_FAULT})

INTENT = "INTENT"
FINALIZED = "FINALIZED"
ITERATION_STATES: frozenset[str] = frozenset({INTENT, FINALIZED})

RUN_STATUSES: frozenset[str] = frozenset({"COMPLETED", "FAILED"})

ALERT_LABELS: frozenset[str] = frozenset({PHASE4_FAILURE_ALERT})

# --- markers ---------------------------------------------------------------

_SECTION_OPEN = "<!-- sentinel:phase4-loop {section_id} -->"
_SECTION_CLOSE = "<!-- sentinel:phase4-loop-end {section_id} -->"
_META_OPEN = "<!-- sentinel:phase4-meta {section_id} -->"
_META_CLOSE = "<!-- sentinel:phase4-meta-end {section_id} -->"
_ROWS_OPEN = "<!-- sentinel:phase4-iterations {section_id} -->"
_ROWS_CLOSE = "<!-- sentinel:phase4-iterations-end {section_id} -->"

_SECTION_ID_SCAN = re.compile(r"<!-- sentinel:phase4-loop ([A-Za-z0-9._:-]+) -->")

_HEADER = (
    "<!-- ITERATION_LOG — DERIVED PUBLIC EVIDENCE, machine-written, "
    "append-only. NOT authoritative loop state. -->\n"
    "# ITERATION_LOG — ai-portfolio-sentinel bounded loop\n"
    "\n"
    "**This file is derived public evidence. It is NOT authoritative loop\n"
    "state.** The durable SQLite ledger (`loop_runs`, `loop_iterations`,\n"
    "`runs`, `tasks`, `findings`) and the durable `CostRow`s are\n"
    "authoritative. Every figure below is rendered from that state and is\n"
    "checked back against it; where the two disagree, the ledger is right\n"
    "and this file is wrong.\n"
    "\n"
)

# --- field validators (the closed input schema) -----------------------------

#: Deliberately narrow. A Windows absolute path (backslash, ``C:``), a UNC
#: path, a POSIX absolute path (leading ``/``) and a temp-root path all fail
#: this by construction, which is why unsafe values are rejected at the
#: boundary rather than sanitized afterwards.
#:
#: The underscore is excluded on purpose. That is a charset RESTRICTION, not
#: a second secret taxonomy: it is what makes the two prefix-detectable
#: secret shapes the repository's existing guard knows about — ``ghp_`` and
#: ``github_pat_`` — unrepresentable as an identifier, without this module
#: owning any notion of what a secret looks like. ``Bearer <token>`` dies on
#: the space, ``token=<value>`` and ``<name>key=<value>`` die on the ``=``.
#: Nothing this module legitimately names needs an underscore: loop ids,
#: gate leg/case names and run ids (``r-<hex>``) are hyphenated.
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,63}")
_SOURCE_SHA = re.compile(r"[0-9a-f]{40}")
#: The ledger DDL's own timestamp shape, restated as a pattern because this
#: module never imports the domain that owns the DDL.
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00")


class IterationLogError(ValueError):
    """A value offered to the ITERATION_LOG failed its field validator, or
    a rendered section is internally inconsistent. Public evidence is
    refused rather than repaired."""


def _identifier(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise IterationLogError(
            f"{name} must match {_IDENTIFIER.pattern!r} (closed identifier "
            f"charset); refusing to render it into public evidence"
        )
    return value


def _optional_identifier(name: str, value: Any) -> Optional[str]:
    return None if value is None else _identifier(name, value)


def _enum(name: str, value: Any, allowed: frozenset[str]) -> str:
    if value not in allowed:
        raise IterationLogError(f"{name} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _optional_enum(name: str, value: Any, allowed: frozenset[str]) -> Optional[str]:
    return None if value is None else _enum(name, value, allowed)


def _count(name: str, value: Any) -> int:
    # ``bool`` is an ``int`` subclass; a boolean in a counter field is a
    # caller bug, not a zero or a one.
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise IterationLogError(f"{name} must be a non-negative int, got {value!r}")
    return value


def _optional_count(name: str, value: Any) -> Optional[int]:
    return None if value is None else _count(name, value)


def _exit_code(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise IterationLogError(f"{name} must be a non-negative int, got {value!r}")
    return value


def _source_sha(value: Any) -> str:
    if not isinstance(value, str) or not _SOURCE_SHA.fullmatch(value):
        raise IterationLogError(
            f"source_sha must be exactly 40 lowercase hex characters, got {value!r}"
        )
    return value


def _timestamp(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise IterationLogError(
            f"{name} must be an ISO-8601 UTC timestamp of the ledger's shape, got {value!r}"
        )
    return value


def _optional_timestamp(name: str, value: Any) -> Optional[str]:
    return None if value is None else _timestamp(name, value)


# --- the frozen public section representation -------------------------------


@dataclass(frozen=True)
class IterationMachineRow:
    """One canonical machine row per loop iteration (ADR-0010 section 7 /
    dispatch section 8).

    Nullable values are permitted where a seeded precondition genuinely has
    no corresponding executed value — an iteration still in INTENT has no
    bound run, no run status and no run counts. Values are NEVER invented to
    make rows visually uniform."""

    iteration_index: int
    planned_run_id: str
    iteration_state: str
    bound_run_id: Optional[str] = None
    run_status: Optional[str] = None
    tasks_created: Optional[int] = None
    tasks_terminal: Optional[int] = None
    findings_new: Optional[int] = None
    findings_still_open: Optional[int] = None
    findings_resolved: Optional[int] = None
    iteration_cost_eur_micros: Optional[int] = None
    cumulative_cost_eur_micros: Optional[int] = None
    effective_allowance_eur_micros: Optional[int] = None
    consecutive_failures_after: Optional[int] = None
    breaker: Optional[str] = None
    started_at_utc: Optional[str] = None
    finished_at_utc: Optional[str] = None

    def as_machine_dict(self) -> dict[str, Any]:
        """Validate every field and return the canonical machine mapping.
        Rendering and reparsing both go through this, so a row that
        round-trips has been validated twice by the same rules."""
        return {
            "iteration_index": _count("iteration_index", self.iteration_index),
            "planned_run_id": _identifier("planned_run_id", self.planned_run_id),
            "iteration_state": _enum("iteration_state", self.iteration_state, ITERATION_STATES),
            "bound_run_id": _optional_identifier("bound_run_id", self.bound_run_id),
            "run_status": _optional_enum("run_status", self.run_status, RUN_STATUSES),
            "tasks_created": _optional_count("tasks_created", self.tasks_created),
            "tasks_terminal": _optional_count("tasks_terminal", self.tasks_terminal),
            "findings_new": _optional_count("findings_new", self.findings_new),
            "findings_still_open": _optional_count(
                "findings_still_open", self.findings_still_open
            ),
            "findings_resolved": _optional_count("findings_resolved", self.findings_resolved),
            "iteration_cost_eur_micros": _optional_count(
                "iteration_cost_eur_micros", self.iteration_cost_eur_micros
            ),
            "cumulative_cost_eur_micros": _optional_count(
                "cumulative_cost_eur_micros", self.cumulative_cost_eur_micros
            ),
            "effective_allowance_eur_micros": _optional_count(
                "effective_allowance_eur_micros", self.effective_allowance_eur_micros
            ),
            "consecutive_failures_after": _optional_count(
                "consecutive_failures_after", self.consecutive_failures_after
            ),
            "breaker": _optional_enum("breaker", self.breaker, STOP_REASONS),
            "started_at_utc": _optional_timestamp("started_at_utc", self.started_at_utc),
            "finished_at_utc": _optional_timestamp("finished_at_utc", self.finished_at_utc),
        }


@dataclass(frozen=True)
class SectionMeta:
    """Section metadata (dispatch section 7).

    ``section_id`` is derived from ``loop_id`` and ``gate_case`` rather than
    from any display text, so two sections cannot collide because their
    headings happen to read alike."""

    loop_id: str
    gate_leg: str
    gate_case: str
    classification: str
    source_sha: str
    max_iterations: int
    loop_budget_eur_micros: int
    failure_threshold: int
    stop_reason: str
    exit_code: int
    iterations_recorded: int
    alert_label: Optional[str] = None

    @property
    def section_id(self) -> str:
        return "{}::{}".format(
            _identifier("loop_id", self.loop_id), _identifier("gate_case", self.gate_case)
        )

    def as_metadata_dict(self) -> dict[str, Any]:
        return {
            "loop_id": _identifier("loop_id", self.loop_id),
            "gate_leg": _identifier("gate_leg", self.gate_leg),
            "gate_case": _identifier("gate_case", self.gate_case),
            "classification": _enum("classification", self.classification, CLASSIFICATIONS),
            "source_sha": _source_sha(self.source_sha),
            "max_iterations": _count("max_iterations", self.max_iterations),
            "loop_budget_eur_micros": _count(
                "loop_budget_eur_micros", self.loop_budget_eur_micros
            ),
            "failure_threshold": _count("failure_threshold", self.failure_threshold),
            "stop_reason": _enum("stop_reason", self.stop_reason, STOP_REASONS),
            "exit_code": _exit_code("exit_code", self.exit_code),
            "iterations_recorded": _count("iterations_recorded", self.iterations_recorded),
            "alert_label": _optional_enum("alert_label", self.alert_label, ALERT_LABELS),
        }


@dataclass(frozen=True)
class ParsedSection:
    """One section read back from written ITERATION_LOG bytes."""

    section_id: str
    metadata: dict[str, Any]
    rows: list[dict[str, Any]]


# --- rendering --------------------------------------------------------------


def _narrative(meta: dict[str, Any]) -> str:
    """The one small human paragraph. Generated from already-validated
    metadata, never accepted from a caller — which is what makes "no
    numerical fact appears only in prose" true by construction: every
    number below is a metadata field rendered a second time."""
    prefix = f"{meta['alert_label']} — " if meta["alert_label"] is not None else ""
    return (
        f"{prefix}{meta['gate_leg']} case {meta['gate_case']} "
        f"({meta['classification']}) ran loop `{meta['loop_id']}` under N = "
        f"{meta['max_iterations']}, a loop ceiling of "
        f"{meta['loop_budget_eur_micros']} micro-EUR and a failure threshold "
        f"of {meta['failure_threshold']}. It recorded "
        f"{meta['iterations_recorded']} iterations and stopped on "
        f"{meta['stop_reason']} with exit code {meta['exit_code']}. These "
        f"figures are derived from durable loop state, not authoritative "
        f"over it."
    )


def render_section(meta: SectionMeta, rows: Sequence[IterationMachineRow]) -> str:
    """Pure function of its input: identical input renders byte-identical
    output, with no I/O and no clock read.

    Human prose is kept to one paragraph; everything checkable lives in the
    two delimited machine blocks, which is what the durable-state self-check
    reads back."""
    metadata = meta.as_metadata_dict()
    if metadata["iterations_recorded"] != len(rows):
        raise IterationLogError(
            f"iterations_recorded ({metadata['iterations_recorded']}) does not match "
            f"the number of machine rows ({len(rows)})"
        )
    section_id = meta.section_id

    lines: list[str] = []
    lines.append(_SECTION_OPEN.format(section_id=section_id))
    lines.append(f"## {metadata['gate_leg']} / {metadata['gate_case']} — loop {metadata['loop_id']}")
    lines.append("")
    lines.append(_narrative(metadata))
    lines.append("")
    lines.append(_META_OPEN.format(section_id=section_id))
    lines.append("```json")
    lines.append(_canonical_json(metadata))
    lines.append("```")
    lines.append(_META_CLOSE.format(section_id=section_id))
    lines.append("")
    lines.append(_ROWS_OPEN.format(section_id=section_id))
    lines.append("```jsonl")
    for row in rows:
        lines.append(_canonical_json(row.as_machine_dict()))
    lines.append("```")
    lines.append(_ROWS_CLOSE.format(section_id=section_id))
    lines.append("")
    # The closing marker is the section's FINAL line — that is what makes a
    # section's completeness mechanically checkable after a crash.
    lines.append(_SECTION_CLOSE.format(section_id=section_id))
    return "\n".join(lines) + "\n"


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


# --- reparsing written bytes ------------------------------------------------


def _block_lines(text: str, open_marker: str, close_marker: str, *, what: str) -> list[str]:
    start = text.find(open_marker)
    end = text.find(close_marker)
    if start < 0 or end < 0 or end < start:
        raise IterationLogError(f"{what} block is missing or malformed")
    body = text[start + len(open_marker) : end].strip("\n")
    lines = [line for line in body.splitlines() if line.strip() and not line.startswith("```")]
    return lines


def parse_sections(text: str) -> dict[str, ParsedSection]:
    """Read sections back out of written ITERATION_LOG bytes.

    Every metadata field and every machine-row field is re-validated through
    the same field validators used to render it, by reconstructing the
    frozen dataclass and requiring an exact round trip. A file that parses
    here has therefore passed the closed schema twice: once on the way out,
    once on the way back in."""
    sections: dict[str, ParsedSection] = {}
    for section_id in _SECTION_ID_SCAN.findall(text):
        if section_id in sections:
            raise IterationLogError(f"section {section_id!r} appears more than once")
        if _SECTION_CLOSE.format(section_id=section_id) not in text:
            raise IterationLogError(f"section {section_id!r} has no closing marker")
        start = text.index(_SECTION_OPEN.format(section_id=section_id))
        end = text.index(_SECTION_CLOSE.format(section_id=section_id))
        body = text[start:end]

        meta_lines = _block_lines(
            body,
            _META_OPEN.format(section_id=section_id),
            _META_CLOSE.format(section_id=section_id),
            what=f"section {section_id!r} metadata",
        )
        if len(meta_lines) != 1:
            raise IterationLogError(
                f"section {section_id!r} metadata block must hold exactly one machine line"
            )
        metadata = _revalidated(json.loads(meta_lines[0]), SectionMeta, "as_metadata_dict")

        row_lines = _block_lines(
            body,
            _ROWS_OPEN.format(section_id=section_id),
            _ROWS_CLOSE.format(section_id=section_id),
            what=f"section {section_id!r} iterations",
        )
        rows = [
            _revalidated(json.loads(line), IterationMachineRow, "as_machine_dict")
            for line in row_lines
        ]
        if metadata["iterations_recorded"] != len(rows):
            raise IterationLogError(
                f"section {section_id!r} declares {metadata['iterations_recorded']} "
                f"iterations but carries {len(rows)} machine rows"
            )
        sections[section_id] = ParsedSection(
            section_id=section_id, metadata=metadata, rows=rows
        )
    return sections


def _revalidated(payload: Any, cls: type, method: str) -> dict[str, Any]:
    """Rebuild the frozen dataclass from a parsed mapping and require an
    exact round trip. Unknown keys, missing keys and any field that fails
    its validator are all refused."""
    if not isinstance(payload, dict):
        raise IterationLogError(f"{cls.__name__} machine line is not a JSON object")
    expected = {f.name for f in fields(cls)}
    if set(payload) != expected:
        raise IterationLogError(
            f"{cls.__name__} machine line has fields {sorted(payload)}, expected "
            f"{sorted(expected)}"
        )
    rebuilt = getattr(cls(**payload), method)()
    if rebuilt != payload:
        raise IterationLogError(f"{cls.__name__} machine line did not round-trip unchanged")
    return rebuilt


# --- append (mirrors sentinel/report.py's proven semantics) -----------------


def is_section_complete(path: Path, section_id: str) -> bool:
    if not Path(path).exists():
        return False
    text = Path(path).read_text(encoding="utf-8")
    return (
        _SECTION_OPEN.format(section_id=section_id) in text
        and _SECTION_CLOSE.format(section_id=section_id) in text
    )


def _atomic_write(path: Path, text: str) -> None:
    """The one bounded exception to append-only: used ONLY to repair a
    crash-truncated trailing fragment, never to rewrite anything before
    it."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def append_section(path: Path, section_id: str, section_text: str) -> bool:
    """Idempotent, crash-consistent append, keyed by ``section_id``.

    Returns True if a complete section was (re)written, False if a complete
    one already existed. An opening marker with no matching close is
    necessarily a trailing incomplete fragment — a section's markers are
    unique to its ``section_id`` and only ever appear as a matched pair — so
    only that fragment is truncated, never anything before it."""
    path = Path(path)
    open_marker = _SECTION_OPEN.format(section_id=section_id)
    close_marker = _SECTION_CLOSE.format(section_id=section_id)

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_HEADER)
            handle.write(section_text)
            handle.flush()
            os.fsync(handle.fileno())
        return True

    existing = path.read_text(encoding="utf-8")
    if open_marker in existing and close_marker in existing:
        return False

    if open_marker in existing:
        idx = existing.index(open_marker)
        _atomic_write(path, existing[:idx])

    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(section_text)
        handle.flush()
        os.fsync(handle.fileno())
    return True


def iteration_log_sha256(path: Path) -> str:
    """SHA-256 over the exact final bytes of the written ITERATION_LOG.

    Recorded in the gate artifact, never inside the ITERATION_LOG itself —
    a file cannot truthfully contain its own hash."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = [
    "ALERT_LABELS",
    "CLASSIFICATIONS",
    "FINALIZED",
    "INTENT",
    "ITERATION_STATES",
    "IterationLogError",
    "IterationMachineRow",
    "PHASE4_FAILURE_ALERT",
    "ParsedSection",
    "RUN_STATUSES",
    "SEEDED_FAULT",
    "SYNTHETIC",
    "SectionMeta",
    "append_section",
    "is_section_complete",
    "iteration_log_sha256",
    "parse_sections",
    "render_section",
]

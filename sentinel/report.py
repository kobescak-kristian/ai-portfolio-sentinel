"""FINDINGS.md rendering: a pure renderer plus an idempotent,
crash-consistent append (C4).

Every rendered section opens with ``<!-- sentinel:run {run_id} -->``
and its **final line** is ``<!-- /sentinel:run {run_id} -->`` — the
closing marker on the last line is what makes a section's
completeness mechanically checkable. ``append_run_section`` is safe to
call twice for the same run (idempotent no-op) and safe to call after
a crash left a trailing, incomplete section for that same run_id (it
truncates only that trailing fragment via a temp-file + atomic
replace, then appends the complete section) — never touching any
section before it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from contracts.schemas import Finding, RunRecord

_HEADER = (
    "<!-- Sentinel run report — append-only, machine-written. Proposals only: "
    "the sentinel never edits a monitored surface and never opens a queue "
    "item. -->\n"
    "# FINDINGS — ai-portfolio-sentinel\n\n"
)

_OPEN_MARKER = "<!-- sentinel:run {run_id} -->"
_CLOSE_MARKER = "<!-- /sentinel:run {run_id} -->"

_LIVE_LABEL = "REAL DATA — the operator's own public repositories"
_SYNTHETIC_LABEL = "SYNTHETIC FIXTURES — labeled synthetic, not real data"


@dataclass(frozen=True)
class ReportInput:
    run: RunRecord
    tasks_done: int
    tasks_failed: int
    tasks_dead_letter: int
    new_findings: Sequence[Finding]
    resolved_findings: Sequence[Finding]


def render_run_section(data: ReportInput) -> str:
    """Pure function of its input — no I/O, no clock read. Calling it
    twice on identical input produces byte-identical output."""
    run = data.run
    label = _LIVE_LABEL if run.run_kind == "live" else _SYNTHETIC_LABEL
    if run.status == "COMPLETED":
        status_label = "COMPLETED"
    else:
        status_label = f"FAILED (partial — {run.tasks_terminal}/{run.tasks_created} tasks terminal)"

    lines: list[str] = []
    lines.append(_OPEN_MARKER.format(run_id=run.run_id))
    lines.append(f"## Run {run.run_id} — {run.started_at_utc.isoformat()}")
    lines.append("")
    lines.append(f"- Run kind: **{run.run_kind}** — {label}")
    lines.append(f"- Ledger status: {status_label}")
    lines.append(
        f"- Tasks: {run.tasks_created} created / {run.tasks_terminal} terminal "
        f"(done {data.tasks_done} · failed {data.tasks_failed} · dead-letter {data.tasks_dead_letter})"
    )
    lines.append(
        f"- Findings: {run.findings_new} new · {run.findings_still_open} still open · "
        f"{run.findings_resolved} resolved"
    )
    lines.append("")

    if data.new_findings:
        lines.append("### New findings — proposals only")
        lines.append("")
        for finding in sorted(
            data.new_findings, key=lambda f: (f.check_class, f.surface, f.location)
        ):
            lines.append(
                f"- `[{finding.check_class}]` {finding.surface} — {finding.detail} "
                f"— `fp:{finding.fingerprint[:12]}`"
            )
        lines.append("")

    if data.resolved_findings:
        lines.append("### Resolved this run")
        lines.append("")
        for finding in sorted(
            data.resolved_findings, key=lambda f: (f.check_class, f.surface, f.location)
        ):
            lines.append(
                f"- `[{finding.check_class}]` {finding.surface} — first seen "
                f"{finding.first_seen_utc.isoformat()} — `fp:{finding.fingerprint[:12]}`"
            )
        lines.append("")

    lines.append(_CLOSE_MARKER.format(run_id=run.run_id))
    return "\n".join(lines) + "\n"


def is_section_complete(path: Path, run_id: str) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    return (
        _OPEN_MARKER.format(run_id=run_id) in text
        and _CLOSE_MARKER.format(run_id=run_id) in text
    )


def _atomic_write(path: Path, text: str) -> None:
    """The one bounded exception to append-only: used only to repair
    a crash-truncated trailing fragment, never to rewrite anything
    before it."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def append_run_section(path: Path, run_id: str, section_text: str) -> bool:
    """Returns True if a complete section was (re)written for
    ``run_id``, False if one already existed (idempotent no-op)."""
    open_marker = _OPEN_MARKER.format(run_id=run_id)
    close_marker = _CLOSE_MARKER.format(run_id=run_id)

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
        # Crash-mid-render: an opening marker with no matching close is
        # necessarily a trailing incomplete fragment (a run's markers
        # are unique to that run_id and only ever appear as a matched
        # pair). Truncate only that fragment, never anything before it.
        idx = existing.index(open_marker)
        _atomic_write(path, existing[:idx])

    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(section_text)
        handle.flush()
        os.fsync(handle.fileno())
    return True

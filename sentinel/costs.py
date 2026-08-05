"""Zero-cost and real-agent CostRow construction, idempotent append (C4).

Every run appends exactly one row via the existing frozen
``telemetry.cost_ledger``. A stub-mode run makes zero model calls, so
its row shows 0 input tokens, 0 output tokens, 0 micro-euros — a true
measurement, not a placeholder. ``model="none-deterministic"`` is
non-empty, path-free, and cannot be mistaken for a provider/model
identifier.

Phase 3 addition (dispatch q77-p3-a, section F): an agent-mode run
instead aggregates its ``agent_calls`` audit rows (sentinel/ledger.py)
into one real CostRow. This is checked generically from ledger state
(whether any ``agent_calls`` rows exist for the run), not from a
separate flag threaded through Deps — so the same reconciliation path
(``sentinel.pipeline.reconcile_terminal_run_outputs``) builds the
correct row after success, failure, *or* crash recovery, with no
special-casing for how the run was invoked.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from contracts.schemas import CostRow
from sentinel import ledger
from telemetry.cost_ledger import append_cost_row, read_cost_rows


def build_zero_cost_row(*, run_id: str, run_kind: str, recorded_at_utc: datetime) -> CostRow:
    return CostRow(
        schema_version=1,
        run_id=run_id,
        recorded_at_utc=recorded_at_utc,
        run_kind=run_kind,
        model="none-deterministic",
        input_tokens=0,
        output_tokens=0,
        cost_eur_micros=0,
    )


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def repair_trailing_fragment(cost_ledger_path: Path) -> bool:
    """A crash mid-write can leave a truncated, unparseable final
    line. If the last line doesn't parse as JSON, remove just that
    trailing fragment (temp-file + atomic replace); every prior line
    is untouched. Returns True if a repair was performed."""
    if not cost_ledger_path.exists():
        return False
    text = cost_ledger_path.read_text(encoding="utf-8")
    if not text:
        return False
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if not lines:
        return False
    try:
        json.loads(lines[-1])
        return False
    except ValueError:
        pass
    repaired = "".join(line + "\n" for line in lines[:-1])
    _atomic_write(cost_ledger_path, repaired)
    return True


def has_cost_row_for_run(cost_ledger_path: Path, run_id: str) -> bool:
    if not cost_ledger_path.exists():
        return False
    try:
        rows = read_cost_rows(cost_ledger_path)
    except ValueError:
        # A corrupt trailing line (crash mid-write) breaks whole-file
        # parsing. Report "not found" rather than raising — the caller
        # is expected to try repair_trailing_fragment() next.
        return False
    return any(row.run_id == run_id for row in rows)


def append_zero_cost_row(
    cost_ledger_path: Path, *, run_id: str, run_kind: str, recorded_at_utc: datetime
) -> CostRow:
    row = build_zero_cost_row(run_id=run_id, run_kind=run_kind, recorded_at_utc=recorded_at_utc)
    append_cost_row(cost_ledger_path, row)
    return row


# ---------------------------------------------------------------------
# Phase 3 addition (dispatch q77-p3-a, section F): real agent-mode
# CostRow aggregation from the agent_calls audit trail.
# ---------------------------------------------------------------------


def has_agent_calls_for_run(conn: sqlite3.Connection, run_id: str) -> bool:
    return bool(ledger.list_agent_calls_for_run(conn, run_id))


def build_agent_cost_row(
    conn: sqlite3.Connection, *, run_id: str, run_kind: str, recorded_at_utc: datetime
) -> CostRow:
    """Aggregate every ``agent_calls`` row for this run into one
    CostRow — "one aggregate CostRow for the Sentinel run" (dispatch
    E). A row still RESERVED (its final usage was never recovered —
    crash mid-call) is charged at its reserved amount, never zero,
    without the row itself being touched
    (``ledger.unresolved_agent_calls`` finds it; this function only
    reads, it never rewrites an audit row)."""
    calls = ledger.list_agent_calls_for_run(conn, run_id)
    if not calls:
        raise ValueError(f"no agent_calls rows for run {run_id!r} — nothing to aggregate")

    model = calls[0].model
    total_charged_micros = 0
    total_input_tokens = 0
    total_output_tokens = 0
    for call in calls:
        if call.state == "RESERVED":
            total_charged_micros += call.reserved_eur_micros
        else:
            total_charged_micros += call.charged_eur_micros or 0
        total_input_tokens += call.input_tokens or 0
        total_output_tokens += call.output_tokens or 0

    return CostRow(
        schema_version=1,
        run_id=run_id,
        recorded_at_utc=recorded_at_utc,
        run_kind=run_kind,
        model=model,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        cost_eur_micros=total_charged_micros,
    )


def append_agent_cost_row(
    cost_ledger_path: Path,
    conn: sqlite3.Connection,
    *,
    run_id: str,
    run_kind: str,
    recorded_at_utc: datetime,
) -> CostRow:
    row = build_agent_cost_row(
        conn, run_id=run_id, run_kind=run_kind, recorded_at_utc=recorded_at_utc
    )
    append_cost_row(cost_ledger_path, row)
    return row

"""Zero-cost CostRow construction and idempotent append (C4).

Every run appends one row via the existing frozen
``telemetry.cost_ledger``. Phase 2 makes zero model calls, so every
row shows 0 input tokens, 0 output tokens, 0 micro-euros — a true
measurement, not a placeholder. ``model="none-deterministic"`` is
non-empty, path-free, and cannot be mistaken for a provider/model
identifier.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from contracts.schemas import CostRow
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

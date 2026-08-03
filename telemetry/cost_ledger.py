"""Append-only JSONL cost ledger (BLUEPRINT §0, §6 P0, §7).

The ledger path is always received explicitly; this module reads no
environment variables and adds no path or environment information of
its own. DEFAULT_LEDGER_PATH documents the conventional location
relative to the repository root — nothing here creates that file.

Frozen serialization rule (JSONL): a validated CostRow serializes to
one compact JSON object per line — UTF-8, sorted keys, separators ","
and ":" with no optional whitespace, newline terminator, datetime in
one fixed UTC ISO-8601 form ending "+00:00", no NaN or Infinity.
"""

from __future__ import annotations

import json
from pathlib import Path

from contracts.schemas import CostRow

DEFAULT_LEDGER_PATH = Path("telemetry/cost_ledger.jsonl")


def serialize_cost_row(row: CostRow) -> str:
    data = row.model_dump()
    data["recorded_at_utc"] = row.recorded_at_utc.isoformat()
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def append_cost_row(ledger_path: Path, row: CostRow) -> None:
    line = serialize_cost_row(row)
    with open(ledger_path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()


def read_cost_rows(ledger_path: Path) -> list[CostRow]:
    rows: list[CostRow] = []
    with open(ledger_path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(CostRow.model_validate(json.loads(stripped)))
    return rows

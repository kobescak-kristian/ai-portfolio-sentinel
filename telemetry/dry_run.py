"""Telemetry dry run: prove the harness writes and reads one CostRow.

Invocation everywhere (local runs and evidence):

    python -m telemetry.dry_run

from the repository root. Writes one zero-cost "dev" row into a
temporary directory, reads it back, prints the serialized row, and
leaves no generated file behind in the repository.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from contracts.schemas import CostRow
from telemetry.cost_ledger import append_cost_row, read_cost_rows, serialize_cost_row


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = Path(tmp) / "cost_ledger.jsonl"
        row = CostRow(
            schema_version=1,
            run_id="telemetry-dry-run",
            recorded_at_utc=datetime.now(timezone.utc),
            run_kind="dev",
            model="none",
            input_tokens=0,
            output_tokens=0,
            cost_eur_micros=0,
        )
        append_cost_row(ledger_path, row)
        rows = read_cost_rows(ledger_path)
        if rows != [row]:
            raise SystemExit("dry run FAIL: read-back does not match written row")
        print(serialize_cost_row(rows[0]))


if __name__ == "__main__":
    main()

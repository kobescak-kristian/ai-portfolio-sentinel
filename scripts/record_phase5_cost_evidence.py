#!/usr/bin/env python
"""P5-C/P5-D CostRow committed-ledger handoff tool (P5-B Part 3/3).

Implemented now; EXECUTED only in the later P5-C/P5-D recording
sessions — Part 3 makes no such invocation. Reads one probe or gate
evidence artifact JSON file, validates its ``cost_rows`` against the
frozen ``CostRow`` schema (already enforced by the evidence record's
own pydantic parse), refuses any row whose ``run_id`` is already
present in the committed cost ledger, and appends the rest. This is
what makes ``run_phase5_window_freeze.py``'s seam-3 prerequisite check
mechanically true: P5-C/P5-D actual spend enters the committed ledger
exactly once, before any P5-E headroom computation reads it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._phase5_common import REPO_ROOT  # noqa: E402
from sentinel import costs  # noqa: E402
from sentinel.phase5.evidence_records import GateEvidenceRecord, ProbeEvidenceRecord  # noqa: E402
from telemetry.cost_ledger import append_cost_row  # noqa: E402

DEFAULT_LEDGER_PATH = REPO_ROOT / "telemetry" / "cost_ledger.jsonl"


def _load_cost_rows(evidence_path: Path) -> tuple:
    body = evidence_path.read_text(encoding="utf-8")
    for model_cls in (ProbeEvidenceRecord, GateEvidenceRecord):
        try:
            record = model_cls.model_validate_json(body)
        except Exception:  # noqa: BLE001 - try the other evidence shape
            continue
        return record.cost_rows
    raise SystemExit(f"error: {evidence_path} did not parse as a Phase-5 probe or gate evidence record")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-path", type=Path, required=True)
    parser.add_argument("--cost-ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    args = parser.parse_args(argv)

    rows = _load_cost_rows(args.evidence_path)
    if not rows:
        print("error: evidence record carries zero CostRows — nothing to record", file=sys.stderr)
        return 1

    duplicates = [r.run_id for r in rows if costs.has_cost_row_for_run(args.cost_ledger, r.run_id)]
    if duplicates:
        print(
            f"error: run_id(s) already present in {args.cost_ledger}: {', '.join(duplicates)} "
            "— refusing to append a duplicate CostRow",
            file=sys.stderr,
        )
        return 1

    for row in rows:
        append_cost_row(args.cost_ledger, row)
        print(f"recorded CostRow: run_id={row.run_id} cost_eur_micros={row.cost_eur_micros}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

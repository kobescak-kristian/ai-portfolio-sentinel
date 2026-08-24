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
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._phase5_common import REPO_ROOT  # noqa: E402
from sentinel.phase5.evidence_records import GateEvidenceRecord, ProbeEvidenceRecord  # noqa: E402
from telemetry.cost_ledger import append_cost_row, read_cost_rows  # noqa: E402

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
        print("error: evidence record carries zero CostRows, nothing to record", file=sys.stderr)
        return 1

    # Strict-parse the entire existing ledger up front. A malformed or
    # truncated ledger refuses the whole operation before any append,
    # and the ledger itself is never touched (no repair_trailing_fragment
    # is ever invoked here). Existing run_ids are derived only from this
    # successful strict parse, never from a best-effort/partial read.
    if args.cost_ledger.exists():
        try:
            existing_rows = read_cost_rows(args.cost_ledger)
        except (ValueError, OSError) as exc:
            print(
                f"error: {args.cost_ledger} did not strict-parse cleanly ({exc}); "
                "refusing to append, ledger left unchanged",
                file=sys.stderr,
            )
            return 1
    else:
        existing_rows = []

    existing_run_ids = {r.run_id for r in existing_rows}
    incoming_run_ids = [r.run_id for r in rows]
    seen: set[str] = set()
    intra_batch_dupes = {rid for rid in incoming_run_ids if rid in seen or seen.add(rid)}
    against_existing = {rid for rid in incoming_run_ids if rid in existing_run_ids}
    problems = intra_batch_dupes | against_existing

    if problems:
        print(
            f"error: duplicate run_id(s) {', '.join(sorted(problems))}; "
            "refusing to append, ledger left unchanged",
            file=sys.stderr,
        )
        return 1

    for row in rows:
        append_cost_row(args.cost_ledger, row)
        print(f"recorded CostRow: run_id={row.run_id} cost_eur_micros={row.cost_eur_micros}")

    # Post-append verification: strict-reparse and confirm every newly
    # appended run_id occurs exactly once. Explicit runtime validation,
    # not a Python `assert` (never disappears under `python -O`).
    reparsed = read_cost_rows(args.cost_ledger)
    counts = Counter(r.run_id for r in reparsed)
    bad = [rid for rid in incoming_run_ids if counts[rid] != 1]
    if bad:
        print(
            "error: post-append verification failed, "
            + ", ".join(f"run_id={rid} occurs {counts[rid]} times" for rid in bad)
            + f" in {args.cost_ledger}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

#!/usr/bin/env python
"""Phase-3 development gate (dispatch q77-p3-a, section I).

Runs the real Sentinel pipeline — four deterministic checkers plus the
real caged Haiku checker for the two judgment classes, agent mode
explicit — against the frozen Phase-1 fixture corpus, scores the
result against `evals/answer_key.jsonl` / `evals/clean_surfaces.jsonl`
per the frozen `evals/SCORING.md` contract, and checks every locked
threshold and invariant from `evals/eval_config.yaml`. Exits non-zero
on any threshold, invariant, cage, or cap failure.

Reads the frozen `fixtures/` and `evals/` trees read-only — never
writes there, never invents a scoring rule beyond what SCORING.md
states. This is the required standalone scorer named by the dispatch;
`evals/run_eval.py` is deliberately not created.

Cost design: one shared ``RunBudgetCoordinator`` (one EUR-0.50
run-scoped cap, per agents/checker/config.py) is reused across BOTH
passes below — the primary scoring pass and the doubled-fixture
idempotency pass — rather than granting each its own EUR 0.50. This is
the more conservative reading of "one shared EUR 0.50-equivalent cap
applies to the entire ... Sentinel run" and keeps this gate's total
real spend bounded by one cap, not two.

Passes:
  1. Primary pass — scores against the answer key / clean units.
  2. Doubled-fixture pass — same fixtures, same ledger DB, a fresh
     run_id. Proves idempotent_rerun and dedup_correct_on_doubled_fixture_run:
     zero new findings, every finding from pass 1 still OPEN.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from pathlib import Path
from typing import Optional

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.checker import auth  # noqa: E402
from agents.checker.budget import RunBudgetCoordinator  # noqa: E402
from agents.checker.config import AUTH_MODE_LABEL, MODEL  # noqa: E402
from agents.checker.fx import resolve_ecb_usd_per_eur  # noqa: E402
from agents.checker.harness import CagedCheckerStub  # noqa: E402
from sentinel import costs, ledger  # noqa: E402
from sentinel.config import RunConfig  # noqa: E402
from sentinel.ids import RandomIdFactory  # noqa: E402
from sentinel.pipeline import Deps, execute_run  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = REPO_ROOT / "fixtures" / "repos"
EVALS_ROOT = REPO_ROOT / "evals"
ANSWER_KEY_PATH = EVALS_ROOT / "answer_key.jsonl"
CLEAN_SURFACES_PATH = EVALS_ROOT / "clean_surfaces.jsonl"
EVAL_CONFIG_PATH = EVALS_ROOT / "eval_config.yaml"

JUDGMENT_CLASSES = {"stale-STATE-marker", "missing-synthetic-label"}
DETERMINISTIC_CLASSES = {"broken-link", "number-mismatch", "missing-required-file", "readme-structure"}


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def _load_eval_config() -> dict:
    with open(EVAL_CONFIG_PATH, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@dataclass
class ScoreResult:
    emitted: int
    true_positives: int
    false_positives: int
    misses: int
    per_class_recall: dict[str, tuple[int, int]]  # class -> (hits, total_positives)
    clean_flagged: int
    clean_total: int
    matched_positive_ids: set
    unmatched_findings: list[tuple]


def score_findings(findings: list[dict], answer_key: list[dict], clean_units: list[dict]) -> ScoreResult:
    """Implements evals/SCORING.md §1-2 exactly. Positive matching
    (§1) is one-to-one on EXACT (check_class, surface, location) for
    every class, readme-structure included — "location is exact for
    all line-level classes", and the frozen readme-structure location
    semantics still name one exact line. Only clean-unit matching (§2)
    collapses a valid README to one file-level unit regardless of the
    emitted line; positive matching never does. Duplicates (a second
    finding matching an already-claimed positive) are false positives."""
    positives_by_key: dict[tuple, list[dict]] = {}
    for row in answer_key:
        key = (row["check_class"], row["surface"], row["location"])
        positives_by_key.setdefault(key, []).append(row)

    clean_by_key: dict[tuple, dict] = {}
    for row in clean_units:
        if row["check_class"] == "readme-structure":
            # A structurally valid README is one file-level clean unit
            # regardless of the emitted line (SCORING.md §2) -- clean
            # matching only, never positive matching.
            path = row["location"].split(":")[0]
            key = ("readme-structure", row["surface"], path)
        else:
            key = (row["check_class"], row["surface"], row["location"])
        clean_by_key[key] = row

    matched_positive_keys: set = set()
    true_positives = 0
    false_positives = 0
    flagged_clean_ids: set = set()
    unmatched: list[tuple] = []

    for finding in findings:
        exact_key = (finding["check_class"], finding["surface"], finding["location"])

        if exact_key in positives_by_key and exact_key not in matched_positive_keys:
            matched_positive_keys.add(exact_key)
            true_positives += 1
            continue

        # Either a duplicate of an already-matched positive, or not a
        # positive at all: false positive either way (SCORING.md §1).
        false_positives += 1
        if finding["check_class"] == "readme-structure":
            clean_key = ("readme-structure", finding["surface"], finding["location"].split(":")[0])
        else:
            clean_key = exact_key
        clean_row = clean_by_key.get(clean_key)
        if clean_row is not None:
            flagged_clean_ids.add(clean_row["clean_id"])
        else:
            unmatched.append(exact_key)

    misses = len(positives_by_key) - len(matched_positive_keys)

    per_class_recall: dict[str, tuple[int, int]] = {}
    per_class_total: dict[str, int] = {}
    per_class_hit: dict[str, int] = {}
    for key in positives_by_key:
        cls = key[0]
        per_class_total[cls] = per_class_total.get(cls, 0) + 1
        if key in matched_positive_keys:
            per_class_hit[cls] = per_class_hit.get(cls, 0) + 1
    for cls, total in per_class_total.items():
        per_class_recall[cls] = (per_class_hit.get(cls, 0), total)

    clean_total_units = len({
        (row["check_class"], row["surface"], row["location"].split(":")[0])
        if row["check_class"] == "readme-structure"
        else (row["check_class"], row["surface"], row["location"])
        for row in clean_units
    })

    return ScoreResult(
        emitted=len(findings),
        true_positives=true_positives,
        false_positives=false_positives,
        misses=misses,
        per_class_recall=per_class_recall,
        clean_flagged=len(flagged_clean_ids),
        clean_total=clean_total_units,
        matched_positive_ids=matched_positive_keys,
        unmatched_findings=unmatched,
    )


def _finding_rows_for_run(conn, run_id: str) -> list[dict]:
    rows = ledger.list_findings_for_run(conn, run_id, role="first_seen")
    return [
        {"check_class": r.finding.check_class, "surface": r.finding.surface, "location": r.finding.location}
        for r in rows
    ]


def _check_ratio_threshold(name: str, numerator: int, denominator: int, ratio_min: str) -> tuple[bool, str]:
    if denominator == 0:
        return True, f"{name}: vacuous (denominator 0)"
    actual = Decimal(numerator) / Decimal(denominator)
    passed = actual >= Decimal(ratio_min)
    return passed, f"{name}: {numerator}/{denominator} = {actual:.4f} (>= {ratio_min} required) -> {'PASS' if passed else 'FAIL'}"


def run_gate(*, judgment_mode: str, gate_root: Path) -> dict:
    gate_root.mkdir(parents=True, exist_ok=True)
    eval_config = _load_eval_config()
    answer_key = _read_jsonl(ANSWER_KEY_PATH)
    clean_units = _read_jsonl(CLEAN_SURFACES_PATH)

    db_path = gate_root / "gate.sqlite3"
    findings_path = gate_root / "FINDINGS.md"
    log_path = gate_root / "gate.jsonl"
    cost_ledger_path = gate_root / "cost_ledger.jsonl"
    for p in (db_path, findings_path, log_path, cost_ledger_path):
        if p.exists():
            p.unlink()

    ids = RandomIdFactory()
    run1_id = ids.new_run_id()
    run2_id = ids.new_run_id()

    coordinator: Optional[RunBudgetCoordinator] = None
    if judgment_mode == "agent":
        auth.assert_no_auth_override_risk()
        now = datetime.now(timezone.utc)
        fx_rate = resolve_ecb_usd_per_eur(now=now)
        coordinator = RunBudgetCoordinator(fx_rate=fx_rate)

    def _deps(run_id: str) -> Deps:
        if judgment_mode != "agent":
            return Deps()
        conn = ledger.open_ledger(db_path)
        stub = CagedCheckerStub(run_id=run_id, conn=conn, coordinator=coordinator)
        return Deps(judgment=stub)

    config1 = RunConfig(
        run_kind="dev", source="fixtures", fixtures_root=FIXTURES_ROOT,
        db_path=db_path, findings_path=findings_path, log_path=log_path,
        cost_ledger_path=cost_ledger_path, run_id=run1_id, judgment_mode=judgment_mode,
    )
    outcome1 = execute_run(config1, _deps(run1_id))

    config2 = RunConfig(
        run_kind="dev", source="fixtures", fixtures_root=FIXTURES_ROOT,
        db_path=db_path, findings_path=findings_path, log_path=log_path,
        cost_ledger_path=cost_ledger_path, run_id=run2_id, judgment_mode=judgment_mode,
    )
    outcome2 = execute_run(config2, _deps(run2_id))

    conn = ledger.open_ledger(db_path, create=False)
    try:
        findings1 = _finding_rows_for_run(conn, run1_id)
        score = score_findings(findings1, answer_key, clean_units)

        invariants = {
            "every_task_terminal": (outcome1.tasks_created == outcome1.tasks_terminal
                                     and outcome2.tasks_created == outcome2.tasks_terminal),
            "zero_lost_tasks": (outcome1.tasks_created > 0 and outcome2.tasks_created > 0),
            "idempotent_rerun": (outcome2.findings_new == 0),
            "dedup_correct_on_doubled_fixture_run": (
                outcome2.findings_still_open == (score.true_positives + score.false_positives)
                and outcome2.findings_resolved == 0
            ),
        }

        thresholds = eval_config["thresholds"]
        checks: list[tuple[bool, str]] = []
        checks.append(_check_ratio_threshold(
            "pooled_precision", score.true_positives, score.emitted, thresholds["pooled_precision"]["ratio_min"]
        ))
        checks.append(_check_ratio_threshold(
            "pooled_recall", score.true_positives, len(answer_key), thresholds["pooled_recall"]["ratio_min"]
        ))
        for cls, (hits, total) in sorted(score.per_class_recall.items()):
            checks.append(_check_ratio_threshold(
                f"per_class_recall[{cls}]", hits, total, thresholds["per_class_recall"]["ratio_min"]
            ))
        clean_flag_ok = score.clean_flagged <= eval_config["thresholds"]["clean_false_flag"]["max_flagged_clean_units"]
        checks.append((clean_flag_ok, (
            f"clean_false_flag: {score.clean_flagged}/{score.clean_total} flagged "
            f"(<= {eval_config['thresholds']['clean_false_flag']['max_flagged_clean_units']} allowed) "
            f"-> {'PASS' if clean_flag_ok else 'FAIL'}"
        )))
        for name, ok in invariants.items():
            checks.append((ok, f"invariant[{name}]: {'PASS' if ok else 'FAIL'}"))

        overall_pass = all(ok for ok, _ in checks)

        cost_row1 = None
        cost_row2 = None
        if judgment_mode == "agent":
            if costs.has_agent_calls_for_run(conn, run1_id):
                cost_row1 = costs.build_agent_cost_row(conn, run_id=run1_id, run_kind="dev", recorded_at_utc=datetime.now(timezone.utc))
            if costs.has_agent_calls_for_run(conn, run2_id):
                cost_row2 = costs.build_agent_cost_row(conn, run_id=run2_id, run_kind="dev", recorded_at_utc=datetime.now(timezone.utc))

        total_charged_micros = (cost_row1.cost_eur_micros if cost_row1 else 0) + (cost_row2.cost_eur_micros if cost_row2 else 0)
        cap_ok = total_charged_micros <= 500_000  # RUN_BUDGET_EUR_MICROS, restated as an int literal here deliberately: this is the gate's OWN cross-check, independent of the coordinator's own internal enforcement
        checks.append((cap_ok, f"aggregate_cost_within_cap: {total_charged_micros} micro-EUR (<= 500000) -> {'PASS' if cap_ok else 'FAIL'}"))
        overall_pass = overall_pass and cap_ok

        return {
            "source_commit": None,  # filled in by main()
            "model": MODEL if judgment_mode == "agent" else "none-deterministic",
            "auth_mode": AUTH_MODE_LABEL if judgment_mode == "agent" else "n/a",
            "judgment_mode": judgment_mode,
            "run1_id": run1_id,
            "run2_id": run2_id,
            "positives_total": len(answer_key),
            "emitted": score.emitted,
            "true_positives": score.true_positives,
            "false_positives": score.false_positives,
            "misses": score.misses,
            "pooled_precision": str(Decimal(score.true_positives) / Decimal(score.emitted)) if score.emitted else None,
            "pooled_recall": str(Decimal(score.true_positives) / Decimal(len(answer_key))),
            "per_class_recall": {cls: f"{hits}/{total}" for cls, (hits, total) in sorted(score.per_class_recall.items())},
            "clean_flagged": score.clean_flagged,
            "clean_total": score.clean_total,
            "invariants": invariants,
            "checks": [msg for _, msg in checks],
            "cost_row1_micros": cost_row1.cost_eur_micros if cost_row1 else 0,
            "cost_row2_micros": cost_row2.cost_eur_micros if cost_row2 else 0,
            "total_charged_eur_micros": total_charged_micros,
            "overall_pass": overall_pass,
        }
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--judgment-mode", choices=["stub", "agent"], default="agent")
    parser.add_argument("--gate-root", type=Path, default=REPO_ROOT / "var" / "phase3_gate")
    parser.add_argument("--artifacts-dir", type=Path, default=REPO_ROOT / "artifacts")
    args = parser.parse_args(argv)

    import subprocess

    try:
        source_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        source_commit = "unknown"

    result = run_gate(judgment_mode=args.judgment_mode, gate_root=args.gate_root)
    result["source_commit"] = source_commit

    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = args.artifacts_dir / "phase3_dev_gate.json"
    with open(artifact_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")

    print(json.dumps(result, indent=2, sort_keys=True))
    print()
    print("OVERALL:", "PASS" if result["overall_pass"] else "FAIL")
    return 0 if result["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

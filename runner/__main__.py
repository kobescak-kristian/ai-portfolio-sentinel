"""``python -m runner`` — the bounded-loop entry point. STUB MODE ONLY.

Separate from ``python -m sentinel``, which is unchanged, as is the
standing scheduled task's resolved command. This entry point starts no
provider-capable loop: ``--judgment-mode agent`` is refused fail-closed
BEFORE any provider construction or invocation, with a deterministic
nonzero exit. That refusal is the feature, not a placeholder for a
later flag.

Exit codes follow ADR-0010 section 6: 0 for COMPLETED_ITERATION_CAP, 1
for the three abnormal stop reasons, 2 for a usage/config error or the
provider-mode refusal (no loop row is created in that case).

There is deliberately no flag, environment variable or configuration
path that raises the loop ceiling. ADR-0010 section 2 forbids one, and
any future operation above 750,000 micro-EUR needs a separate dated
owner-governed decision.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

from runner.breakers import (
    CONSECUTIVE_FAILURE_THRESHOLD,
    LOOP_BUDGET_EUR_MICROS,
    InvalidIterationLimit,
)
from runner.loop import LoopConfig, run_loop
from runner.sentinel_adapter import (
    PER_RUN_CAP_EUR_MICROS,
    RandomIdFactory,
    RunConfig,
    RunLogger,
    SentinelIterationExecutor,
    SystemClock,
)
from runner.state import SqliteLoopStateStore, open_loop_state

USAGE_ERROR = 2


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="runner", description="ADR-0010 bounded-loop runner (stub mode only)"
    )
    parser.add_argument("--loop-id", required=True)
    parser.add_argument(
        "--iterations", type=int, required=True, help="N, bounded to 1 <= N <= 10"
    )
    parser.add_argument("--run-kind", choices=["dev", "eval", "live"], required=True)
    parser.add_argument("--source", choices=["fixtures", "live"], required=True)
    parser.add_argument("--fixtures-root", type=Path, default=Path("fixtures/repos"))
    parser.add_argument("--github-user")
    parser.add_argument("--site-repo")
    parser.add_argument("--db", type=Path, default=Path("var/sentinel.sqlite3"))
    parser.add_argument("--findings", type=Path, default=Path("FINDINGS.md"))
    parser.add_argument("--log", type=Path, default=Path("var/logs/loop.jsonl"))
    parser.add_argument(
        "--cost-ledger", type=Path, default=Path("telemetry/cost_ledger.jsonl")
    )
    # Accepted so the refusal is explicit and testable rather than an
    # argparse "invalid choice" — the loop must be seen to refuse.
    parser.add_argument("--judgment-mode", choices=["stub", "agent"], default="stub")
    return parser


def _validate(args) -> Optional[str]:
    if args.judgment_mode != "stub":
        return (
            "provider/agent mode is not authorised for the bounded loop in this "
            "implementation; refused fail-closed before any provider construction"
        )
    if args.source == "live":
        if not args.github_user:
            return "--source live requires --github-user"
        if args.run_kind == "eval":
            return "--run-kind eval must not be combined with --source live"
    return None


def main(argv: Sequence[str]) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else USAGE_ERROR

    error = _validate(args)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return USAGE_ERROR

    base_config = RunConfig(
        run_kind=args.run_kind,
        source=args.source,
        db_path=args.db,
        findings_path=args.findings,
        log_path=args.log,
        cost_ledger_path=args.cost_ledger,
        fixtures_root=args.fixtures_root,
        github_user=args.github_user,
        site_repo=args.site_repo,
        # Stub mode by construction: this entry point offers no path to
        # anything else.
        judgment_mode="stub",
    )

    conn = open_loop_state(args.db)
    try:
        with RunLogger(args.log) as logger:
            try:
                outcome = run_loop(
                    LoopConfig(
                        loop_id=args.loop_id,
                        max_iterations=args.iterations,
                        per_run_cap_eur_micros=PER_RUN_CAP_EUR_MICROS,
                        loop_budget_eur_micros=LOOP_BUDGET_EUR_MICROS,
                        failure_threshold=CONSECUTIVE_FAILURE_THRESHOLD,
                    ),
                    store=SqliteLoopStateStore(conn),
                    executor=SentinelIterationExecutor(base_config=base_config),
                    clock=SystemClock(),
                    ids=RandomIdFactory(),
                    logger=logger,
                )
            except InvalidIterationLimit as exc:
                # Refused before any loop row, iteration intent or run.
                print(f"error: {exc}", file=sys.stderr)
                return USAGE_ERROR
        print(f"{outcome.stop_reason} loop_id={outcome.loop_id} "
              f"iterations_completed={outcome.iterations_completed} "
              f"accounted_cost_eur_micros={outcome.accounted_cost_eur_micros}")
        return outcome.exit_code
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

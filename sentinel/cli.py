"""Argparse CLI surface: ``python -m sentinel run|recover ...``.

Exit codes: 0 COMPLETED, 1 FAILED (a coherent FAILED RunRecord was
written), 2 usage/config error (no run row created), 3 recovery-only.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from sentinel import ledger
from sentinel.config import RunConfig
from sentinel.logs import RunLogger
from sentinel.pipeline import Deps, execute_run, recover_interrupted_runs


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(prog="sentinel")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="execute one deterministic control-plane run")
    run_p.add_argument("--run-kind", choices=["dev", "eval", "live"], required=True)
    run_p.add_argument("--source", choices=["fixtures", "live"], required=True)
    run_p.add_argument("--fixtures-root", type=Path, default=Path("fixtures/repos"))
    run_p.add_argument("--github-user")
    run_p.add_argument("--site-repo")
    run_p.add_argument("--db", type=Path, default=Path("var/sentinel.sqlite3"))
    run_p.add_argument("--findings", type=Path, default=Path("FINDINGS.md"))
    run_p.add_argument("--log", type=Path, default=Path("var/logs/sentinel.jsonl"))
    run_p.add_argument("--cost-ledger", type=Path, default=Path("telemetry/cost_ledger.jsonl"))
    run_p.add_argument("--http-timeout-seconds", type=float, default=10.0)
    run_p.add_argument("--max-http-requests", type=int, default=500)
    run_p.add_argument("--no-recover", action="store_true")
    run_p.add_argument("--allow-task-failure", action="store_true")
    run_p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    run_p.add_argument("--run-id")

    recover_p = sub.add_parser("recover", help="sweep any interrupted run to a terminal FAILED state")
    recover_p.add_argument("--db", type=Path, required=True)
    recover_p.add_argument("--log", type=Path, required=True)

    return parser


def _validate_run_args(args) -> Optional[str]:
    if args.source == "live":
        if not args.github_user:
            return "--source live requires --github-user"
        if args.run_kind == "eval":
            return "--run-kind eval must not be combined with --source live"
    return None


def _config_from_args(args) -> RunConfig:
    return RunConfig(
        run_kind=args.run_kind,
        source=args.source,
        db_path=args.db,
        findings_path=args.findings,
        log_path=args.log,
        cost_ledger_path=args.cost_ledger,
        fixtures_root=args.fixtures_root,
        github_user=args.github_user,
        site_repo=args.site_repo,
        http_timeout_seconds=args.http_timeout_seconds,
        max_http_requests=args.max_http_requests,
        recover=not args.no_recover,
        fail_run_on_task_failure=not args.allow_task_failure,
        log_level=args.log_level,
        run_id=args.run_id,
    )


def main(argv: Sequence[str], *, deps: Optional[Deps] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 2

    if args.command == "run":
        error = _validate_run_args(args)
        if error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        config = _config_from_args(args)
        outcome = execute_run(config, deps)
        return outcome.exit_code

    if args.command == "recover":
        conn = ledger.open_ledger(args.db)
        try:
            with RunLogger(args.log) as logger:
                recover_interrupted_runs(conn, now=datetime.now(timezone.utc), logger=logger)
        finally:
            conn.close()
        return 3

    return 2

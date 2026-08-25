#!/usr/bin/env python
"""Phase-5 WIF capability-probe entrypoint (P5-B Part 3/3, workflow C:
``.github/workflows/sentinel-wif-probe.yml``). Implements P5-C's
plumbing; Part 3 never executes it.

Two subcommands, matching the frozen order: every retryable non-
provider preflight (expected-source, one-shot discovery, WIF config,
fresh ECB FX + coordinator construction) runs in ``preflight`` and
must all pass BEFORE the immutable one-shot marker is uploaded by the
workflow step in between; ``execute`` runs only after that marker is
confirmed visible via REST, and is the only subcommand that can reach
OIDC or the provider.

NONQUALIFYING by construction: the ``workflow_dispatch`` trigger and
this script's non-lineage evidence artifact names can never enter
``bundle.select_active_window`` or any chain walker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._phase5_common import (  # noqa: E402
    REPO_ROOT,
    Phase5ScriptError,
    assert_expected_source_live,
    assert_expected_source_on_disk,
    build_evidence_client,
    assert_marker_visible_for_this_run,
    discover_oneshot_markers,
    prepare_fresh_work_root,
    write_json_artifact,
    write_marker_json,
)
from sentinel.phase5 import artifact_names  # noqa: E402
from sentinel.phase5.github_context import derive_github_context  # noqa: E402
from sentinel.phase5.models import OneShotMarker  # noqa: E402
from sentinel.phase5.oneshot import assert_purpose_not_yet_consumed, is_eligible_marker_creation  # noqa: E402

PURPOSE = "P5C_WIF_PROBE"
PROBE_TOTAL_EUR_MICROS = 150_000
PROBE_RESERVE_EUR_MICROS = 150_000


def cmd_preflight(args: argparse.Namespace) -> int:
    from agents.checker import auth, oidc
    from agents.checker.fx import resolve_ecb_usd_per_eur
    from agents.checker.budget import RunBudgetCoordinator

    env = os.environ
    try:
        assert_expected_source_on_disk(args.expected_source_sha)
        client = build_evidence_client(env)  # pops GITHUB_TOKEN
        assert_expected_source_live(client, args.expected_source_sha)
        ctx = derive_github_context(env)

        prepare_fresh_work_root(args.work_root)
        markers = discover_oneshot_markers(client, args.work_root)
        assert_purpose_not_yet_consumed(PURPOSE, markers)

        candidate = OneShotMarker(
            schema_version=1,
            purpose=PURPOSE,
            created_at_utc=datetime.now(timezone.utc),
            workflow_identity=ctx.workflow_path,
            github_run_id=ctx.run_id,
            run_attempt=ctx.run_attempt,
            event=ctx.event,
            source_sha=ctx.sha,
        )
        if not is_eligible_marker_creation(candidate):
            raise Phase5ScriptError("run_attempt > 1 is never eligible to create a probe marker")

        oidc.write_placeholder_token_file(env)
        auth.assert_wif_config_ready(env)

        now = datetime.now(timezone.utc)
        fx_rate = resolve_ecb_usd_per_eur(now=now)
        RunBudgetCoordinator(
            fx_rate=fx_rate, total_eur_micros=PROBE_TOTAL_EUR_MICROS,
            max_per_call_reserve_eur_micros=PROBE_RESERVE_EUR_MICROS,
        )  # constructed only to prove it can be, before the marker exists

        write_json_artifact(
            {
                "source": fx_rate.source, "rate_date": fx_rate.rate_date,
                "retrieved_at_utc": fx_rate.retrieved_at_utc.isoformat(),
                "usd_per_eur": str(fx_rate.usd_per_eur),
            },
            args.fx_state_path,
        )
        write_marker_json(candidate, args.marker_out)
        print(f"PREFLIGHT PASS: marker prepared at {args.marker_out}")
        return 0
    except Phase5ScriptError as exc:
        print(f"PREFLIGHT FAIL: {exc}", file=sys.stderr)
        return 2


def _derive_auth_mode(calls) -> "str | None":
    """Derive auth_mode from this run's persisted agent_calls rows,
    never by assuming the configured auth profile's label. Zero rows
    means no call was ever attempted. A single agreed label is
    reported as-is. Disagreement across rows (never expected in
    normal operation) reports a deterministic non-WIF placeholder that
    can never satisfy CAPABILITY_PASS. Takes the already-fetched row
    list rather than (conn, run_id) so it has no ledger import of its
    own -- ledger access stays local to cmd_execute, matching this
    script's existing per-function local-import convention."""
    if not calls:
        return None
    modes = {c.auth_mode for c in calls}
    if len(modes) == 1:
        return next(iter(modes))
    return "conflicting-auth-mode"


def cmd_execute(args: argparse.Namespace) -> int:
    from agents.checker import auth, oidc
    from agents.checker.budget import RunBudgetCoordinator
    from agents.checker.config import HAIKU_ORDINARY
    from agents.checker.fx import FxRate
    from agents.checker.harness import CagedCheckerStub
    from agents.checker.oidc import health_gated
    from checks.judgment.stubs import JudgmentRequest
    from contracts.schemas import RunRecord
    from sentinel import costs, ledger

    env = os.environ
    session = None
    conn = None
    judged_cleanly = False
    cost_rows: tuple = ()
    accounted_total = 0
    auth_mode: "str | None" = None
    try:
        client = build_evidence_client(env)  # pops GITHUB_TOKEN
        ctx = derive_github_context(env)
        expected_marker_name = artifact_names.oneshot_marker_name(PURPOSE, ctx.run_id)
        assert_marker_visible_for_this_run(client, ctx.run_id, expected_marker_name)
        assert_expected_source_live(client, args.expected_source_sha)

        fx_data = json.loads(args.fx_state_path.read_text(encoding="utf-8"))
        fx_rate = FxRate(
            source=fx_data["source"], rate_date=fx_data["rate_date"],
            retrieved_at_utc=datetime.fromisoformat(fx_data["retrieved_at_utc"]),
            usd_per_eur=Decimal(fx_data["usd_per_eur"]),
        )

        session = oidc.acquire_oidc(env)
        session.install_and_start(env)

        run_id = f"r-p5c-{ctx.run_id}"
        db_path = args.work_root / "probe.sqlite3"
        conn = ledger.open_ledger(db_path)

        with ledger.unit_of_work(conn):
            ledger.insert_run(
                conn,
                RunRecord(
                    schema_version=1, run_id=run_id, run_kind="live", status="RUNNING",
                    started_at_utc=datetime.now(timezone.utc), finished_at_utc=None,
                    tasks_created=0, tasks_terminal=0,
                    findings_new=0, findings_still_open=0, findings_resolved=0,
                ),
            )

        coordinator = RunBudgetCoordinator(
            fx_rate=fx_rate, total_eur_micros=PROBE_TOTAL_EUR_MICROS,
            max_per_call_reserve_eur_micros=PROBE_RESERVE_EUR_MICROS,
        )
        stub = CagedCheckerStub(
            run_id=run_id, conn=conn, coordinator=coordinator,
            model=HAIKU_ORDINARY.model, auth_profile=auth.WIF,
        )
        stub.query_fn = health_gated(stub.query_fn, session)
        try:
            stub.judge(
                JudgmentRequest(
                    surface="phase5-wif-probe/PROBE.md",
                    check_class="missing-synthetic-label",
                    path="PROBE.md",
                    text="line one\nline two has 42\nline three",
                )
            )
            judged_cleanly = True
        except Exception as exc:  # noqa: BLE001 - judgment failure is accounted, not swallowed
            print(f"PROBE JUDGMENT FAILED: {type(exc).__name__}", file=sys.stderr)

        if costs.has_agent_calls_for_run(conn, run_id):
            row = costs.build_agent_cost_row(
                conn, run_id=run_id, run_kind="live", recorded_at_utc=datetime.now(timezone.utc)
            )
            cost_rows = (row,)
            accounted_total = row.cost_eur_micros
            auth_mode = _derive_auth_mode(ledger.list_agent_calls_for_run(conn, run_id))

        # Unguarded locally: a close_run failure is an explicit
        # infrastructure failure and must reach the outer except below,
        # never be caught here and normalized into a successful return.
        with ledger.unit_of_work(conn):
            ledger.close_run(
                conn, run_id,
                status="COMPLETED" if judged_cleanly else "FAILED",
                finished_at_utc=datetime.now(timezone.utc),
                counts=ledger.RunCounts(
                    tasks_terminal=0, findings_new=0,
                    findings_still_open=0, findings_resolved=0,
                ),
            )
    except Exception as exc:  # noqa: BLE001 - any failure, including RunRecord terminalization, is recorded truthfully as FAIL
        judged_cleanly = False
        print(f"PROBE EXECUTE FAILED: {type(exc).__name__}", file=sys.stderr)
    finally:
        if session is not None:
            session.shutdown(env)
        else:
            oidc.scrub_identity_token_file(env)
        if conn is not None:
            conn.close()

    disposition = (
        "CAPABILITY_PASS"
        if (
            judged_cleanly
            and cost_rows
            and auth_mode == auth.WIF.label
            and accounted_total <= PROBE_TOTAL_EUR_MICROS
        )
        else "CAPABILITY_FAIL"
    )

    from sentinel.phase5.evidence_records import ProbeEvidenceRecord

    ctx = derive_github_context(env)
    evidence = ProbeEvidenceRecord(
        schema_version=1, workflow_identity=ctx.workflow_path, github_run_id=ctx.run_id,
        run_attempt=ctx.run_attempt, event=ctx.event, ref=ctx.ref, source_sha=ctx.sha,
        created_at_utc=datetime.now(timezone.utc), steps=(),
        expected_source_sha=args.expected_source_sha, disposition=disposition,
        cost_rows=cost_rows, accounted_total_eur_micros=accounted_total,
        auth_mode=auth_mode,
    )
    evidence_path = args.evidence_out
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(evidence.model_dump_json(), encoding="utf-8")
    print(f"DISPOSITION: {disposition}")
    return 0 if disposition == "CAPABILITY_PASS" else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("preflight")
    pre.add_argument("--expected-source-sha", required=True)
    pre.add_argument("--work-root", type=Path, default=REPO_ROOT / "var" / "phase5-probe")
    pre.add_argument("--marker-out", type=Path, required=True)
    pre.add_argument("--fx-state-path", type=Path, required=True)

    ex = sub.add_parser("execute")
    ex.add_argument("--expected-source-sha", required=True)
    ex.add_argument("--work-root", type=Path, default=REPO_ROOT / "var" / "phase5-probe")
    ex.add_argument("--fx-state-path", type=Path, required=True)
    ex.add_argument("--evidence-out", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "preflight":
        return cmd_preflight(args)
    return cmd_execute(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

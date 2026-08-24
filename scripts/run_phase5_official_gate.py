#!/usr/bin/env python
"""Phase-5 official Sonnet gate entrypoint (P5-B Part 3/3, workflow D:
``.github/workflows/sentinel-official-gate.yml``). ADR-0011 §7 pins
this exact path. Implements P5-D's plumbing; Part 3 never executes it.

Reuses the frozen scoring, eval-config-load and execution-validity
machinery from ``scripts/run_phase3_dev_gate.py`` by import, over the
SAME frozen ``fixtures/`` + ``evals/`` contract — the differences are
the marker-sandwich one-shot guard, WIF auth instead of local-OAuth,
model ``claude-sonnet-5``, and ONE shared 5,000,000 / 1,000,000
micro-EUR coordinator for the whole gate session instead of the dev
gate's two independent 750,000/150,000 breakers.

Two subcommands, same frozen ordering discipline as the WIF probe:
every retryable preflight (expected-source, frozen-fixture presence,
fresh evidence dirs, one-shot discovery, WIF config, FX + coordinator
construction) runs in ``preflight``, before the marker is uploaded;
``execute`` runs only after the marker is confirmed visible, and is
the only subcommand that can reach OIDC or the provider.
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
    assert_marker_visible_for_this_run,
    build_evidence_client,
    discover_oneshot_markers,
    write_json_artifact,
    write_marker_json,
)
from scripts.run_phase3_dev_gate import (  # noqa: E402
    ANSWER_KEY_PATH,
    CLEAN_SURFACES_PATH,
    _assert_fresh_evidence_dir,
    _check_ratio_threshold,
    _finding_rows_for_run,
    _load_eval_config,
    _read_jsonl,
    evaluate_execution_validity,
    score_findings,
)
from sentinel.phase5 import artifact_names  # noqa: E402
from sentinel.phase5.github_context import derive_github_context  # noqa: E402
from sentinel.phase5.models import OneShotMarker  # noqa: E402
from sentinel.phase5.oneshot import assert_purpose_not_yet_consumed, is_eligible_marker_creation  # noqa: E402

PURPOSE = "P5D_OFFICIAL_SONNET_GATE"

# Independently restated (anti-tautology precedent, matching
# run_phase3_dev_gate.py's own PER_RUN_COST_CAP_EUR_MICROS comment):
# NOT imported from agents/checker/config.py, so this cross-check does
# not agree with the enforcement mechanism by construction. Pinned by
# tests/test_phase5_gate_runner.py against SONNET_OFFICIAL_GATE.
GATE_TOTAL_EUR_MICROS = 5_000_000
GATE_RESERVE_EUR_MICROS = 1_000_000


def cmd_preflight(args: argparse.Namespace) -> int:
    from agents.checker import auth, oidc
    from agents.checker.budget import RunBudgetCoordinator
    from agents.checker.fx import resolve_ecb_usd_per_eur

    env = os.environ
    try:
        assert_expected_source_on_disk(args.expected_source_sha)
        client = build_evidence_client(env)  # pops GITHUB_TOKEN
        assert_expected_source_live(client, args.expected_source_sha)
        ctx = derive_github_context(env)

        # frozen fixture/eval contract must load cleanly
        _load_eval_config()
        _read_jsonl(ANSWER_KEY_PATH)
        _read_jsonl(CLEAN_SURFACES_PATH)

        _assert_fresh_evidence_dir(args.gate_root, "gate-root")
        _assert_fresh_evidence_dir(args.artifacts_dir, "artifacts-dir")

        markers = discover_oneshot_markers(client, args.work_root)
        assert_purpose_not_yet_consumed(PURPOSE, markers)

        candidate = OneShotMarker(
            schema_version=1, purpose=PURPOSE, created_at_utc=datetime.now(timezone.utc),
            workflow_identity=ctx.workflow_path, github_run_id=ctx.run_id,
            run_attempt=ctx.run_attempt, event=ctx.event, source_sha=ctx.sha,
        )
        if not is_eligible_marker_creation(candidate):
            raise Phase5ScriptError("run_attempt > 1 is never eligible to create a gate marker")

        oidc.write_placeholder_token_file(env)
        auth.assert_wif_config_ready(env)

        fx_rate = resolve_ecb_usd_per_eur(now=datetime.now(timezone.utc))
        RunBudgetCoordinator(
            fx_rate=fx_rate, total_eur_micros=GATE_TOTAL_EUR_MICROS,
            max_per_call_reserve_eur_micros=GATE_RESERVE_EUR_MICROS,
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


def _run_gate_session(*, gate_root: Path, coordinator, session, expected_source_sha: str) -> dict:
    from agents.checker import auth
    from agents.checker.config import SONNET_OFFICIAL_GATE
    from agents.checker.harness import CagedCheckerStub
    from agents.checker.oidc import health_gated
    from sentinel import costs, ledger
    from sentinel.config import RunConfig
    from sentinel.ids import RandomIdFactory
    from sentinel.pipeline import Deps, execute_run

    _assert_fresh_evidence_dir(gate_root, "gate-root")
    gate_root.mkdir(parents=True, exist_ok=False)
    eval_config = _load_eval_config()
    answer_key = _read_jsonl(ANSWER_KEY_PATH)
    clean_units = _read_jsonl(CLEAN_SURFACES_PATH)

    db_path = gate_root / "gate.sqlite3"
    findings_path = gate_root / "FINDINGS.md"
    log_path = gate_root / "gate.jsonl"
    cost_ledger_path = gate_root / "cost_ledger.jsonl"

    ids = RandomIdFactory()
    run1_id, run2_id = ids.new_run_id(), ids.new_run_id()

    def deps_for(run_id: str) -> Deps:
        conn = ledger.open_ledger(db_path)
        stub = CagedCheckerStub(
            run_id=run_id, conn=conn, coordinator=coordinator,
            model=SONNET_OFFICIAL_GATE.model, auth_profile=auth.WIF,
        )
        stub.query_fn = health_gated(stub.query_fn, session)
        return Deps(judgment=stub)

    from scripts.run_phase3_dev_gate import FIXTURES_ROOT

    config1 = RunConfig(
        run_kind="dev", source="fixtures", fixtures_root=FIXTURES_ROOT, db_path=db_path,
        findings_path=findings_path, log_path=log_path, cost_ledger_path=cost_ledger_path,
        run_id=run1_id, judgment_mode="agent",
    )
    outcome1 = execute_run(config1, deps_for(run1_id))
    config2 = RunConfig(
        run_kind="dev", source="fixtures", fixtures_root=FIXTURES_ROOT, db_path=db_path,
        findings_path=findings_path, log_path=log_path, cost_ledger_path=cost_ledger_path,
        run_id=run2_id, judgment_mode="agent",
    )
    outcome2 = execute_run(config2, deps_for(run2_id))

    conn = ledger.open_ledger(db_path, create=False)
    try:
        findings1 = _finding_rows_for_run(conn, run1_id)
        score = score_findings(findings1, answer_key, clean_units)

        invariants = {
            "every_task_terminal": (
                outcome1.tasks_created == outcome1.tasks_terminal
                and outcome2.tasks_created == outcome2.tasks_terminal
            ),
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
        max_flagged = thresholds["clean_false_flag"]["max_flagged_clean_units"]
        clean_flag_ok = score.clean_flagged <= max_flagged
        checks.append((clean_flag_ok, (
            f"clean_false_flag: {score.clean_flagged}/{score.clean_total} flagged "
            f"(<= {max_flagged} allowed) -> {'PASS' if clean_flag_ok else 'FAIL'}"
        )))
        for name, ok in invariants.items():
            checks.append((ok, f"invariant[{name}]: {'PASS' if ok else 'FAIL'}"))

        scoring_pass = all(ok for ok, _ in checks)

        cost_rows = []
        for run_id in (run1_id, run2_id):
            if costs.has_agent_calls_for_run(conn, run_id):
                cost_rows.append(
                    costs.build_agent_cost_row(
                        conn, run_id=run_id, run_kind="dev", recorded_at_utc=datetime.now(timezone.utc)
                    )
                )
        accounted_total = sum(r.cost_eur_micros for r in cost_rows)
        cost_ok = accounted_total <= GATE_TOTAL_EUR_MICROS
        checks.append((cost_ok, (
            f"gate_session_cost_within_cap: {accounted_total} micro-EUR "
            f"(<= {GATE_TOTAL_EUR_MICROS}) -> {'PASS' if cost_ok else 'FAIL'}"
        )))

        validity = evaluate_execution_validity(
            conn, run1_id=run1_id, run2_id=run2_id, outcome1=outcome1, outcome2=outcome2,
            required_source_sha=expected_source_sha, attested_source_sha=expected_source_sha,
        )
        checks.extend(zip(validity["predicates"].values(), validity["check_lines"]))

        overall_pass = scoring_pass and cost_ok and validity["valid"]
        miss_patterns = tuple(f"{c}|{s}|{loc}" for c, s, loc in score.unmatched_findings)

        return {
            "run_ids": (run1_id, run2_id),
            "scoring": {
                "emitted": score.emitted, "true_positives": score.true_positives,
                "false_positives": score.false_positives, "misses": score.misses,
                "clean_flagged": score.clean_flagged, "clean_total": score.clean_total,
                "per_class_recall": {c: f"{h}/{t}" for c, (h, t) in sorted(score.per_class_recall.items())},
            },
            "thresholds": dict(thresholds),
            "invariant_results": invariants,
            "execution_validity": validity,
            "miss_patterns": miss_patterns,
            "cost_rows": tuple(cost_rows),
            "accounted_total_eur_micros": accounted_total,
            "green": overall_pass,
            "check_lines": [msg for _, msg in checks],
        }
    finally:
        conn.close()


def cmd_execute(args: argparse.Namespace) -> int:
    from agents.checker import oidc
    from agents.checker.budget import RunBudgetCoordinator
    from agents.checker.config import SONNET_OFFICIAL_GATE
    from agents.checker.fx import FxRate

    env = os.environ
    session = None
    disposition = "INFRASTRUCTURE_FAILURE"
    result: dict | None = None
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

        coordinator = RunBudgetCoordinator(
            fx_rate=fx_rate, total_eur_micros=GATE_TOTAL_EUR_MICROS,
            max_per_call_reserve_eur_micros=GATE_RESERVE_EUR_MICROS,
        )
        result = _run_gate_session(
            gate_root=args.gate_root, coordinator=coordinator, session=session,
            expected_source_sha=args.expected_source_sha,
        )
        disposition = "GREEN" if result["green"] else "HONEST_FAIL"
    except Exception as exc:  # noqa: BLE001 - any pre-scoring failure is INFRASTRUCTURE_FAILURE
        print(f"GATE EXECUTE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        if session is not None:
            session.shutdown(env)
        else:
            oidc.scrub_identity_token_file(env)

    from sentinel.phase5.evidence_records import GateEvidenceRecord

    ctx = derive_github_context(env)
    evidence = GateEvidenceRecord(
        schema_version=1, workflow_identity=ctx.workflow_path, github_run_id=ctx.run_id,
        run_attempt=ctx.run_attempt, event=ctx.event, ref=ctx.ref, source_sha=ctx.sha,
        created_at_utc=datetime.now(timezone.utc), steps=(),
        expected_source_sha=args.expected_source_sha,
        model=SONNET_OFFICIAL_GATE.model, profile_name=SONNET_OFFICIAL_GATE.name,
        run_ids=tuple(result["run_ids"]) if result else (),
        scoring=result["scoring"] if result else {},
        thresholds=result["thresholds"] if result else {},
        invariant_results=result["invariant_results"] if result else {},
        execution_validity=result["execution_validity"] if result else {},
        miss_patterns=result["miss_patterns"] if result else (),
        cost_rows=result["cost_rows"] if result else (),
        accounted_total_eur_micros=result["accounted_total_eur_micros"] if result else 0,
        disposition=disposition,
    )
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = args.artifacts_dir / "phase5_official_gate.json"
    evidence_path.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
    if result:
        (args.artifacts_dir / "phase5_official_gate_checks.json").write_text(
            json.dumps(result["check_lines"], indent=2), encoding="utf-8"
        )
    print(f"DISPOSITION: {disposition}")
    return 0 if disposition == "GREEN" else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("preflight")
    pre.add_argument("--expected-source-sha", required=True)
    pre.add_argument("--gate-root", type=Path, required=True)
    pre.add_argument("--artifacts-dir", type=Path, required=True)
    pre.add_argument("--work-root", type=Path, default=REPO_ROOT / "var" / "phase5-gate")
    pre.add_argument("--marker-out", type=Path, required=True)
    pre.add_argument("--fx-state-path", type=Path, required=True)

    ex = sub.add_parser("execute")
    ex.add_argument("--expected-source-sha", required=True)
    ex.add_argument("--gate-root", type=Path, required=True)
    ex.add_argument("--artifacts-dir", type=Path, required=True)
    ex.add_argument("--fx-state-path", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "preflight":
        return cmd_preflight(args)
    return cmd_execute(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

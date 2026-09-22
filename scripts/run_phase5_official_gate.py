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
import contextlib
import json
import os
import re
import sys
import time
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
    assert_oneshot_not_consumed_durably,
    assert_purpose_armable,
    assert_replacement_history_permits,
    attribute_invalid_record,
    build_evidence_client,
    discover_oneshot_markers,
    establish_preflight_journal,
    load_durable_history,
    prepare_fresh_work_root,
    replacement_provenance_fields,
    terminal_identity,
    terminal_layout,
    terminal_writer_for,
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
from sentinel.phase5.oneshot import is_eligible_marker_creation  # noqa: E402
from sentinel.phase5.evidence_records import GateEvidenceRecord  # noqa: E402
from sentinel.phase5.execution_control import (  # noqa: E402
    ExecutionSafetyDomain,
    InvalidRefused,
    InvocationRegistry,
    QualityRefused,
    SessionLatch,
    TerminalArbiter,
)
from sentinel.phase5.execution_envelope import (  # noqa: E402
    SessionClock,
    load_committed_envelope,
    resolve_job_start_anchor,
)
from sentinel.phase5.journal import (  # noqa: E402
    OperationalJournal,
    install_observing_signal_handlers,
    read_journal,
    summarize_journal,
)
from sentinel.phase5.terminal import (  # noqa: E402
    CHECKS_FILENAME,
    JOURNAL_FILENAME,
    PRIOR_ABSENT,
    EnvelopeIdentity,
    TerminalStateError,
    assert_transition,
    build_invalid_record,
    write_ancillary_atomically,
    write_terminal_atomically,
)

PURPOSE = "P5D_OFFICIAL_SONNET_GATE"

# Stage-2C execution-envelope identity. None until Stage 2C produces it;
# while None, ``assert_purpose_armable`` refuses the replacement purpose
# in both subcommands (dispatch q77-p5d-repair-stage2b2-implement-a).
ENVELOPE: "EnvelopeIdentity | None" = None

# Independently restated (anti-tautology precedent, matching
# run_phase3_dev_gate.py's own PER_RUN_COST_CAP_EUR_MICROS comment):
# NOT imported from agents/checker/config.py, so this cross-check does
# not agree with the enforcement mechanism by construction. Pinned by
# tests/test_phase5_gate_runner.py against SONNET_OFFICIAL_GATE.
GATE_TOTAL_EUR_MICROS = 5_000_000
GATE_RESERVE_EUR_MICROS = 1_000_000

# Stage 2C-3 (ADR-0012 repair; dispatch q77-p5d-repair-stage2c3-implement-a).
# The committed execution envelope this runner would load, once Stage 2C-B
# commits one. No such artifact is ever written in this stage.
ENVELOPE_PATH = Path("artifacts/phase5_execution_envelope.json")
RUNNER_EXIT_LOCK_WAIT_S = 5.0
EXPECTED_API_JOB_NAME = "gate"


class SessionAborted(RuntimeError):
    """A Stage-2C cause latched. Raised (a) by the before_task_execute
    hook mid-run, where sentinel.pipeline.execute_run's own blanket
    exception handling absorbs it and returns a failed RunOutcome, and
    (b) explicitly by _run_gate_session immediately after each
    execute_run call returns, where it propagates to _execute_body's
    existing outer exception handling."""


def _make_runner_terminator(domain: ExecutionSafetyDomain, *, exiter=os._exit):
    """Domain-owning runner-termination closure for SessionMonitor's
    escalate callback. Attempts the shared lock with a bounded wait,
    then exits unconditionally whether or not it was acquired -- a
    hung holder of the lock must never block the runner from exiting.
    Emits no quality information."""

    def _terminate_runner(cause, arbiter_state) -> None:  # noqa: ARG001 - shape required by SessionMonitor
        acquired = domain.lock.acquire(timeout=RUNNER_EXIT_LOCK_WAIT_S)
        try:
            exiter(1)
        finally:
            if acquired:
                domain.lock.release()

    return _terminate_runner


def gate_profile_identity() -> "tuple[str, str]":
    """(model, profile name) of the official gate profile. The gate
    finalizer obtains the identity through this function so the profile
    constant stays referenced only by this ADR-pinned runner."""
    from agents.checker.config import SONNET_OFFICIAL_GATE

    return SONNET_OFFICIAL_GATE.model, SONNET_OFFICIAL_GATE.name


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
        prepare_fresh_work_root(args.work_root)

        # Durable-history-first one-shot discovery and replacement
        # eligibility (ADR-0012 Amendment A1; dispatch
        # q77-p5d-repair-stage2-implement-a). The original P5-D purpose
        # is durably consumed in the committed receipt registry, so
        # this refuses today exactly as it must -- the replacement
        # purpose is NOT armed by this dispatch; PURPOSE stays
        # P5D_OFFICIAL_SONNET_GATE. This wiring exists so a later,
        # separately governed arming dispatch needs only to switch
        # PURPOSE, not add new eligibility logic.
        receipts = load_durable_history()
        markers = discover_oneshot_markers(client, args.work_root)
        assert_oneshot_not_consumed_durably(PURPOSE, receipts, markers)
        assert_replacement_history_permits(receipts, markers, PURPOSE)
        assert_purpose_armable(PURPOSE, ENVELOPE)

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
        # Pre-marker terminal-publication infrastructure (dispatch
        # q77-p5d-repair-stage2b2-implement-a): the terminal layout and a
        # verified PREFLIGHTED journal must exist, or preflight refuses
        # here and the one-shot marker is never written.
        establish_preflight_journal(args.artifacts_dir)
        write_marker_json(candidate, args.marker_out)
        print(f"PREFLIGHT PASS: marker prepared at {args.marker_out}")
        return 0
    except Phase5ScriptError as exc:
        print(f"PREFLIGHT FAIL: {exc}", file=sys.stderr)
        return 2


def _derive_auth_mode(calls) -> "str | None":
    """Derive auth_mode from this gate session's persisted agent_calls
    rows across both designated runs, never by assuming the configured
    auth profile's label. Same discipline as the P5-C probe's
    ``run_phase5_wif_probe._derive_auth_mode`` (dispatch
    q77-p5c-execute-a, C0-C): zero rows means no call was ever
    attempted; a single agreed label is reported as-is; disagreement
    across rows (never expected in normal operation) reports a
    deterministic non-WIF placeholder that can never satisfy
    GREEN/HONEST_FAIL."""
    if not calls:
        return None
    modes = {c.auth_mode for c in calls}
    if len(modes) == 1:
        return next(iter(modes))
    return "conflicting-auth-mode"


def _recover_partial_auth_mode(gate_root: Path) -> "str | None":
    """Best-effort auth-provenance recovery for a post-marker
    INFRASTRUCTURE_FAILURE (dispatch q77-p5d-s1-evidence-repair-a).
    ``gate_root`` is asserted fresh at the start of every gate session
    (``_assert_fresh_evidence_dir``), so any ``gate.sqlite3`` found
    here belongs to no run but this one — a plain distinct-value scan
    over the whole table is exact, with no run_id needed and no risk
    of cross-session contamination. Returns None if the ledger was
    never created (the failure occurred before any provider work
    began) or is unreadable; never raises, so a recovery failure can
    never mask the real INFRASTRUCTURE_FAILURE cause."""
    db_path = gate_root / "gate.sqlite3"
    if not db_path.exists():
        return None
    try:
        from sentinel import ledger

        conn = ledger.open_ledger(db_path, create=False)
        try:
            modes = {row[0] for row in conn.execute("SELECT DISTINCT auth_mode FROM agent_calls")}
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - recovery is best-effort, never fatal
        return None
    if not modes:
        return None
    if len(modes) == 1:
        return next(iter(modes))
    return "conflicting-auth-mode"


def _failed_check_messages(checks: "list[tuple[bool, str]]") -> "tuple[str, ...]":
    """Machine-derived record of every failed check line — scoring,
    invariants, cost, execution-validity alike (dispatch
    q77-p5d-s1-evidence-repair-a). Never empty when any entry in
    ``checks`` failed, regardless of which category failed, so a gate
    that fails solely on cost or an invariant or an execution-validity
    predicate — with otherwise perfect scoring, and therefore empty
    ``miss_patterns`` — still yields a non-empty, schema-satisfying
    analysis. Extracted as a pure function (rather than inlined) so it
    is directly unit-testable against a synthetic ``checks`` list,
    with no gate-session/DB/fixture harness required."""
    return tuple(msg for ok, msg in checks if not ok)


def _run_gate_session(
    *, gate_root: Path, coordinator, session, expected_source_sha: str,
    clock: SessionClock, latch: SessionLatch, registry: InvocationRegistry, journal: OperationalJournal,
    stall_budget_ms: int, config, terminate, on_control_failure,
) -> dict:
    from agents.checker import auth
    from agents.checker.config import SONNET_OFFICIAL_GATE
    from agents.checker.envelope_guard import deadline_guarded
    from agents.checker.harness import CagedCheckerStub
    from agents.checker.oidc import assertion_refreshed, health_gated
    from sentinel import costs, ledger
    from sentinel.config import RunConfig
    from sentinel.ids import RandomIdFactory
    from sentinel.pipeline import Deps, RunHooks, execute_run

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

    def _abort_if_latched(task) -> None:  # noqa: ARG001 - RunHooks.before_task_execute shape
        if latch.is_set:
            raise SessionAborted(latch.cause)

    def deps_for(run_id: str, run_ordinal: int) -> Deps:
        conn = ledger.open_ledger(db_path)
        stub = CagedCheckerStub(
            run_id=run_id, conn=conn, coordinator=coordinator,
            model=SONNET_OFFICIAL_GATE.model, auth_profile=auth.WIF,
        )
        # assertion_refreshed OUTSIDE health_gated: every gate invocation spawns a
        # fresh Agent-SDK CLI process that performs its own provider exchange, and
        # the provider rejects re-exchanging one assertion (B5-P0 Part 1). This
        # gate has no wall-clock measurement to protect, so the composed wrapper
        # is correct here; the timing driver must instead prepare outside its
        # measured region.
        stub.query_fn = deadline_guarded(
            assertion_refreshed(health_gated(stub.query_fn, session), session, os.environ),
            run_ordinal=run_ordinal, clock=clock, latch=latch, registry=registry,
            journal=journal, stall_budget_ms=stall_budget_ms, config=config,
            terminate=terminate, on_control_failure=on_control_failure,
        )
        return Deps(judgment=stub, hooks=RunHooks(before_task_execute=_abort_if_latched))

    from scripts.run_phase3_dev_gate import FIXTURES_ROOT

    config1 = RunConfig(
        run_kind="dev", source="fixtures", fixtures_root=FIXTURES_ROOT, db_path=db_path,
        findings_path=findings_path, log_path=log_path, cost_ledger_path=cost_ledger_path,
        run_id=run1_id, judgment_mode="agent",
    )
    outcome1 = execute_run(config1, deps_for(run1_id, 1))
    if latch.is_set:
        raise SessionAborted(latch.cause)

    config2 = RunConfig(
        run_kind="dev", source="fixtures", fixtures_root=FIXTURES_ROOT, db_path=db_path,
        findings_path=findings_path, log_path=log_path, cost_ledger_path=cost_ledger_path,
        run_id=run2_id, judgment_mode="agent",
    )
    outcome2 = execute_run(config2, deps_for(run2_id, 2))
    if latch.is_set:
        raise SessionAborted(latch.cause)

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
        all_calls = []
        for run_id in (run1_id, run2_id):
            all_calls.extend(ledger.list_agent_calls_for_run(conn, run_id))
            if costs.has_agent_calls_for_run(conn, run_id):
                cost_rows.append(
                    costs.build_agent_cost_row(
                        conn, run_id=run_id, run_kind="dev", recorded_at_utc=datetime.now(timezone.utc)
                    )
                )
        accounted_total = sum(r.cost_eur_micros for r in cost_rows)
        auth_mode = _derive_auth_mode(all_calls)
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
        failed_checks = _failed_check_messages(checks)

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
            "failed_checks": failed_checks,
            "cost_rows": tuple(cost_rows),
            "accounted_total_eur_micros": accounted_total,
            "auth_mode": auth_mode,
            "green": overall_pass,
            "check_lines": [msg for _, msg in checks],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stage 2B-2 execute step (ADR-0012 sections 9, 21; Amendment A2/A6;
# dispatch q77-p5d-repair-stage2b2-implement-a)
# ---------------------------------------------------------------------------

EXECUTE_QUALITY_LINE = (
    "EXECUTE COMPLETE: runner terminal evidence written; "
    "disposition withheld until publication is confirmed"
)
_EXCEPTION_TYPE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}")


def _bounded_exception_type(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if _EXCEPTION_TYPE_NAME.fullmatch(name) else "Exception"


def _flush_std_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 - flushing into /dev/null is best-effort
            pass


@contextlib.contextmanager
def _suppressed_operator_output(*, os_name: "str | None" = None):
    """Gate-only operator-output suppression (ADR-0012 Amendment A2).

    Before publication, nothing the execute step's process tree writes
    may reach the workflow log: the pinned SDK lets the bundled CLI
    inherit fd 2 when no stderr callback is set, SDK loggers fall through
    to ``logging.lastResort`` on ``sys.stderr``, and tracebacks go to
    stderr. fd 1 and fd 2 are pointed at ``os.devnull``, so child
    processes inherit it too. POSIX only: on Windows ``dup2`` does not
    change the Win32 standard handles a child inherits.

    Normal return only: Python streams are flushed AGAIN while they still
    target ``/dev/null`` and only then are the saved descriptors
    restored, so no buffered text can surface in the real log. On any
    exception thrown through the body (callers convert every ordinary
    Exception inside it) nothing is restored: a KeyboardInterrupt,
    SystemExit or signal-driven termination propagates unchanged and its
    traceback goes to /dev/null."""
    if (os_name if os_name is not None else os.name) != "posix":
        raise Phase5ScriptError("operator-output suppression requires a POSIX runner")
    _flush_std_streams()
    saved_out = os.dup(1)
    saved_err = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    yield
    _flush_std_streams()
    os.dup2(saved_out, 1)
    os.dup2(saved_err, 2)
    for fd in (saved_out, saved_err, devnull):
        os.close(fd)


class _JournalStateTracker:
    """Journals permitted RUNNER state transitions. Never raises and
    never changes execution: a transition the state model does not
    permit (for example after a missing journal) is simply not
    journaled."""

    def __init__(self, journal: OperationalJournal, state: "str | None") -> None:
        self._journal = journal
        self.state = state

    def transition(self, nxt: str) -> bool:
        try:
            assert_transition(self.state, nxt, writer="RUNNER")
        except TerminalStateError:
            return False
        self._journal.append("STATE_TRANSITION", state_from=self.state, state_to=nxt)
        self.state = nxt
        return True


def _quality_record(identity, result: dict) -> GateEvidenceRecord:
    model, profile_name = gate_profile_identity()
    return GateEvidenceRecord(
        schema_version=1, workflow_identity=identity.workflow_identity, github_run_id=identity.run_id,
        run_attempt=identity.run_attempt, event=identity.event, ref=identity.ref,
        source_sha=identity.source_sha, created_at_utc=datetime.now(timezone.utc), steps=(),
        expected_source_sha=identity.expected_source_sha, model=model, profile_name=profile_name,
        run_ids=tuple(result["run_ids"]), scoring=result["scoring"], thresholds=result["thresholds"],
        invariant_results=result["invariant_results"], execution_validity=result["execution_validity"],
        miss_patterns=result["miss_patterns"], failed_checks=result["failed_checks"],
        cost_rows=result["cost_rows"], accounted_total_eur_micros=result["accounted_total_eur_micros"],
        disposition="GREEN" if result["green"] else "HONEST_FAIL",
        auth_mode=result["auth_mode"],
        terminal_writer=terminal_writer_for(PURPOSE, "RUNNER"),
        **replacement_provenance_fields(PURPOSE, ENVELOPE),
    )


def _write_runner_terminal(
    journal: OperationalJournal, tracker: _JournalStateTracker, record: GateEvidenceRecord, *,
    record_kind: str, identity, publication_root: Path, staging_root: Path, next_state: str,
) -> bool:
    journal.append("TERMINAL_WRITE_STARTED", record_kind=record_kind)
    try:
        digest = write_terminal_atomically(
            record, publication_root=publication_root, staging_root=staging_root,
            prior=PRIOR_ABSENT, identity=identity,
        )
    except Exception as exc:  # noqa: BLE001 - a terminal write fault is journaled, never raised
        journal.append("TERMINAL_WRITE_FAILED", record_kind=record_kind, exception_type=_bounded_exception_type(exc))
        return False
    journal.append("TERMINAL_WRITE_COMPLETED", record_kind=record_kind, sha256=digest)
    tracker.transition(next_state)
    return True


def _infrastructure_invalid_record(identity, cause: str, gate_root: Path) -> GateEvidenceRecord:
    model, profile_name = gate_profile_identity()
    return attribute_invalid_record(
        build_invalid_record(
            identity=identity, envelope=ENVELOPE, created_at_utc=datetime.now(timezone.utc),
            model=model, profile_name=profile_name, infrastructure_cause=cause, writer="RUNNER",
            auth_mode=_recover_partial_auth_mode(gate_root),
        ),
        purpose=PURPOSE,
    )


def _commit_invalid_via_arbiter(
    arbiter: TerminalArbiter, journal: OperationalJournal, tracker: _JournalStateTracker,
    cause: str, record: GateEvidenceRecord, *, publication_root: Path, staging_root: Path,
    identity, exception_type: str,
) -> "tuple[int, str]":
    digest_box: "list[str]" = []

    def _replace() -> None:
        digest_box.append(write_terminal_atomically(
            record, publication_root=publication_root, staging_root=staging_root,
            prior=PRIOR_ABSENT, identity=identity,
        ))

    journal.append("TERMINAL_WRITE_STARTED", record_kind="INFRASTRUCTURE_INVALID")
    try:
        arbiter.commit_invalid(cause, _replace)
    except InvalidRefused:
        journal.append("TERMINAL_WRITE_FAILED", record_kind="INFRASTRUCTURE_INVALID", exception_type="InvalidRefused")
        return 3, "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=TERMINAL_WRITE_FAILED"
    except Exception as exc:  # noqa: BLE001 - the replace callback itself raised (INVALID_FAILED);
        # never retried, never escapes this helper.
        journal.append(
            "TERMINAL_WRITE_FAILED", record_kind="INFRASTRUCTURE_INVALID",
            exception_type=_bounded_exception_type(exc),
        )
        return 3, "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=TERMINAL_WRITE_FAILED"
    journal.append("TERMINAL_WRITE_COMPLETED", record_kind="INFRASTRUCTURE_INVALID", sha256=digest_box[0])
    tracker.transition("INVALID_EVIDENCE_WRITTEN")
    return 1, f"EXECUTE INFRASTRUCTURE_FAILURE: cause={cause} exception_type={exception_type}"


def _handle_execute_failure(
    failure: Exception, *, journal: OperationalJournal, tracker: _JournalStateTracker,
    identity, gate_root: Path, provider_boundary_crossed: bool,
    latch: "SessionLatch | None", arbiter: "TerminalArbiter | None",
    publication_root: Path, staging_root: Path,
) -> "tuple[int, str]":
    """Stage 2C-3 failure handling. Called from _execute_body's own
    except clause -- still inside the try/finally whose finally stops
    the SessionMonitor -- so a post-control commit_invalid runs while
    the monitor is still alive to supervise it."""
    if journal.signals_observed:
        return 3, "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=SIGNAL_OBSERVED"

    exception_type = _bounded_exception_type(failure)
    latched_cause = latch.cause if latch is not None and latch.is_set else None
    if latched_cause is not None:
        # A Stage-2C cause: already journaled as OBJECTIVE_CAUSE_LATCHED by
        # whichever component won the latch (deadline_guarded or
        # SessionMonitor). journal.py freezes RUNNER_EXCEPTION to Stage-2B
        # causes only, so it is never journaled again here with this cause.
        cause = latched_cause
    else:
        cause = "RUNNER_EXCEPTION" if provider_boundary_crossed else "PRE_PROVIDER_FAILURE"
        journal.append("RUNNER_EXCEPTION", cause=cause, exception_type=exception_type)

    if identity is None:
        return 3, "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=RUNNER_INTERNAL_ERROR"
    record = _infrastructure_invalid_record(identity, cause, gate_root)
    if journal.signals_observed:
        return 3, "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=SIGNAL_OBSERVED"

    if arbiter is not None:
        # POST-CONTROL (contract B): route through the arbiter.
        return _commit_invalid_via_arbiter(
            arbiter, journal, tracker, cause, record,
            publication_root=publication_root, staging_root=staging_root, identity=identity,
            exception_type=exception_type,
        )
    # PRE-CONTROL (contract B): existing direct path, unchanged.
    written = _write_runner_terminal(
        journal, tracker, record, record_kind="INFRASTRUCTURE_INVALID", identity=identity,
        publication_root=publication_root, staging_root=staging_root,
        next_state="INVALID_EVIDENCE_WRITTEN",
    )
    if not written:
        return 3, "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=TERMINAL_WRITE_FAILED"
    return 1, f"EXECUTE INFRASTRUCTURE_FAILURE: cause={cause} exception_type={exception_type}"


def _execute_body(args, journal: OperationalJournal, tracker: _JournalStateTracker) -> "tuple[int, str]":
    from agents.checker import oidc
    from agents.checker.budget import RunBudgetCoordinator
    from agents.checker.fx import FxRate
    from agents.checker.process_control import CONTROL_CONFIG, SessionMonitor, terminate_descendants

    env = os.environ
    publication_root, staging_root, _quarantine_root = terminal_layout(args.artifacts_dir)
    session = None
    identity = None
    provider_boundary_crossed = False
    monitor = None
    latch = None
    arbiter = None
    outcome: "tuple[int, str] | None" = None
    try:
        ctx = derive_github_context(env)
        identity = terminal_identity(ctx, args.expected_source_sha, PURPOSE)
        client = build_evidence_client(env)  # pops GITHUB_TOKEN
        expected_marker_name = artifact_names.oneshot_marker_name(PURPOSE, ctx.run_id)
        assert_marker_visible_for_this_run(client, ctx.run_id, expected_marker_name)
        tracker.transition("REPLACEMENT_MARKED")
        assert_expected_source_live(client, args.expected_source_sha)

        # --- Stage 2C-3: anchor / envelope / control construction. Both
        # must succeed before any of the six control objects are built;
        # a failure here is still PRE_PROVIDER_FAILURE (arbiter is None,
        # falls to the existing outer failure handling). No committed
        # envelope is ever written in this stage, so this always refuses
        # today -- see STATE.md for why that does not change today's live
        # workflow outcome. ---
        resolved_at_utc = datetime.now(timezone.utc)
        resolved_at_mono = time.monotonic()
        jobs = client.list_run_attempt_jobs(ctx.run_id, ctx.run_attempt)
        anchor = resolve_job_start_anchor(
            jobs, run_id=ctx.run_id, run_attempt=ctx.run_attempt,
            expected_workflow_job_id=env.get("GITHUB_JOB", ""),
            expected_api_job_name=EXPECTED_API_JOB_NAME,
            expected_runner_name=env.get("RUNNER_NAME", ""),
            resolved_at_utc=resolved_at_utc, monotonic_at_resolve=resolved_at_mono,
        )
        stage2c_envelope = load_committed_envelope(ENVELOPE_PATH)

        domain = ExecutionSafetyDomain()
        latch = SessionLatch(domain)
        registry = InvocationRegistry(domain)
        clock = SessionClock.from_anchor(anchor, stage2c_envelope)
        arbiter = TerminalArbiter(domain, latch, clock)
        monitor = SessionMonitor(
            clock, latch, registry, arbiter, CONTROL_CONFIG, journal,
            terminate=terminate_descendants, escalate=_make_runner_terminator(domain),
        )
        monitor.start()
        # --- end Stage 2C-3 control construction ---

        fx_data = json.loads(args.fx_state_path.read_text(encoding="utf-8"))
        fx_rate = FxRate(
            source=fx_data["source"], rate_date=fx_data["rate_date"],
            retrieved_at_utc=datetime.fromisoformat(fx_data["retrieved_at_utc"]),
            usd_per_eur=Decimal(fx_data["usd_per_eur"]),
        )
        assert_purpose_armable(PURPOSE, ENVELOPE)

        # Everything from OIDC acquisition onward may reach external
        # identity or provider work, so it is never PRE_PROVIDER_FAILURE.
        provider_boundary_crossed = True
        tracker.transition("EXECUTING")
        session = oidc.acquire_oidc(env)
        session.install_and_start(env)

        coordinator = RunBudgetCoordinator(
            fx_rate=fx_rate, total_eur_micros=GATE_TOTAL_EUR_MICROS,
            max_per_call_reserve_eur_micros=GATE_RESERVE_EUR_MICROS,
        )
        result = _run_gate_session(
            gate_root=args.gate_root, coordinator=coordinator, session=session,
            expected_source_sha=args.expected_source_sha,
            clock=clock, latch=latch, registry=registry, journal=journal,
            stall_budget_ms=stage2c_envelope.stall_budget_ms, config=CONTROL_CONFIG,
            terminate=terminate_descendants, on_control_failure=monitor.request_escalation,
        )
        tracker.transition("SCORED_PROVISIONAL")
        record = _quality_record(identity, result)

        digest_box: "list[str]" = []

        def _replace_quality() -> None:
            digest_box.append(write_terminal_atomically(
                record, publication_root=publication_root, staging_root=staging_root,
                prior=PRIOR_ABSENT, identity=identity,
            ))

        journal.append("TERMINAL_WRITE_STARTED", record_kind="QUALITY")
        try:
            arbiter.commit_quality(_replace_quality)
        except QualityRefused as refused:
            # Case A: refused BEFORE _replace_quality ran -- quality was
            # never written, so a NEW distinct invalid record may be
            # attempted once.
            if arbiter.deadline_established_by_commit_point:
                journal.append("OBJECTIVE_CAUSE_LATCHED", cause="SESSION_DEADLINE")
            if refused.cause is None:
                # Defensive: the docstring calls this impossible in normal
                # flow. Fail closed; never fabricate a cause.
                journal.append("TERMINAL_WRITE_FAILED", record_kind="QUALITY", exception_type="QualityRefused")
                outcome = (3, "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=TERMINAL_WRITE_FAILED")
            else:
                fallback_record = _infrastructure_invalid_record(identity, refused.cause, args.gate_root)
                outcome = _commit_invalid_via_arbiter(
                    arbiter, journal, tracker, refused.cause, fallback_record,
                    publication_root=publication_root, staging_root=staging_root, identity=identity,
                    exception_type=_bounded_exception_type(refused),
                )
        except Exception as exc:  # noqa: BLE001 - _replace_quality (write_terminal_atomically) itself
            # raised: arbiter is now QUALITY_FAILED. Case B: the publication
            # itself failed/is ambiguous -- no invalid fallback is
            # attempted; the separate finalizer is the backstop.
            journal.append("TERMINAL_WRITE_FAILED", record_kind="QUALITY", exception_type=_bounded_exception_type(exc))
            outcome = (3, "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=TERMINAL_WRITE_FAILED")
        else:
            journal.append("TERMINAL_WRITE_COMPLETED", record_kind="QUALITY", sha256=digest_box[0])
            tracker.transition("TERMINAL_EVIDENCE_WRITTEN")
            try:
                write_ancillary_atomically(
                    CHECKS_FILENAME, json.dumps(result["check_lines"], indent=2).encode("utf-8"),
                    publication_root=publication_root, staging_root=staging_root,
                )
            except Exception:  # noqa: BLE001 - ancillary incompleteness never downgrades quality
                pass
            outcome = (0, EXECUTE_QUALITY_LINE)
    except Exception as exc:  # noqa: BLE001 - ordinary exceptions only; cancellation propagates
        # Failure handling (including any post-control arbiter.commit_invalid)
        # happens HERE, still inside this try/finally, so the monitor is
        # still running while it happens -- finally below stops it only
        # afterward.
        outcome = _handle_execute_failure(
            exc, journal=journal, tracker=tracker, identity=identity, gate_root=args.gate_root,
            provider_boundary_crossed=provider_boundary_crossed, latch=latch, arbiter=arbiter,
            publication_root=publication_root, staging_root=staging_root,
        )
    finally:
        if monitor is not None:
            monitor.stop()
        if session is not None:
            session.shutdown(env)
        else:
            oidc.scrub_identity_token_file(env)

    return outcome


def _execute_quietly(args) -> "tuple[int, str]":
    publication_root, _staging_root, _quarantine_root = terminal_layout(args.artifacts_dir)
    journal_path = publication_root / JOURNAL_FILENAME
    try:
        initial_state = summarize_journal(read_journal(journal_path)).last_state
    except Exception:  # noqa: BLE001 - an unreadable journal only weakens journaling
        initial_state = None
    journal = OperationalJournal(journal_path, writer="RUNNER").open()
    restore_signal_handlers = install_observing_signal_handlers(journal)
    try:
        return _execute_body(args, journal, _JournalStateTracker(journal, initial_state))
    finally:
        restore_signal_handlers()
        journal.close()


def cmd_execute(args: argparse.Namespace) -> int:
    """Quality-neutral execute step. Before publication is confirmed,
    GREEN and HONEST_FAIL are indistinguishable on every surface this
    process controls: one constant stdout line, exit 0, empty stderr.
    Infrastructure failure stays separately observable (exit 1 or 3)."""
    try:
        quiet = _suppressed_operator_output()
        quiet.__enter__()
    except Exception:  # noqa: BLE001 - refusal happens before any provider work
        print("EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=RUNNER_INTERNAL_ERROR")
        return 3
    try:
        code, line = _execute_quietly(args)
    except Exception:  # noqa: BLE001 - ordinary exceptions only; cancellation propagates unrestored
        code, line = 3, "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=RUNNER_INTERNAL_ERROR"
    quiet.__exit__(None, None, None)
    print(line)
    return code


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

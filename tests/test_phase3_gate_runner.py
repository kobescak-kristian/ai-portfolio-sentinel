"""Phase-3 gate-runner lifecycle tests (adr/0005-phase3-gate-remediation.md,
adr/0007-prospective-validation-protocol.md §5,
adr/0009-post-adr0008-phase3-validation-protocol.md §2/§3/§4/§5).

Covers what each dispatch changed in
``scripts/run_phase3_dev_gate.py``: one independent
``RunBudgetCoordinator`` per designated run ID (replacing the single
shared coordinator that made run 2 vacuous), the runner's own
independent cost cross-check restated as EUR 0.75 per run / EUR 1.50
per two-run gate session, the ADR-0007 §5 prospective preflight, and —
Stage 2 of ADR-0009 — the logical-judgment-history execution-validity
evaluator that replaces ADR-0007 §2's failed-call and raw-call-count
semantics prospectively.

The ADR-0009 proofs (V1-V29) seed REAL rows through the real ledger
writers under the real SQLite schema and then run the real evaluator
over that database, so what is proven is reconstruction from the
DURABLE representation, not agreement between two in-memory objects.

Nothing here runs the gate. No test in this module makes a model call,
touches the network, or requires OAuth: ``conftest.py``'s autouse
``block_network`` fixture fails any test that reaches a real socket,
``claude_agent_sdk.query`` is patched to raise wherever a checker stub
is constructed, and the tests that exercise ``run_gate`` replace
``execute_run`` with a local fake. The frozen fixture corpus and the
scoring contract are not touched.
"""

from __future__ import annotations

import ast
import importlib
import itertools
import json
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agents.checker import failures as checker_failures
from agents.checker import tools as checker_tools
from agents.checker.budget import BudgetExhausted, RunBudgetCoordinator
from agents.checker.fx import FxRate
from agents.checker.harness import CagedCheckerStub
from agents.checker.tools import ToolAttemptRecord
from contracts.schemas import (
    CheckTask,
    Finding,
    RunRecord,
    compute_content_hash,
    compute_fingerprint,
)
from sentinel import costs, ledger

# ``scripts/`` is a directory of entry points, not one of the first-party
# package roots tests/test_dependency_surface.py models, so it is loaded
# by name here rather than with a static ``import scripts...`` statement.
gate = importlib.import_module("scripts.run_phase3_dev_gate")

T0 = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)

_FAKE_RATE = FxRate(
    source="ecb-eurofxref-daily",
    rate_date="2026-08-19",
    retrieved_at_utc=T0,
    usd_per_eur=Decimal("1.1554"),
)

ADOPTED_PER_RUN_CAP = 750_000
ADOPTED_SESSION_CAP = 1_500_000


# ---------------------------------------------------------------------
# One independent run-budget breaker per designated run ID.
# ---------------------------------------------------------------------


def test_gate_builds_two_distinct_coordinators_one_per_designated_run():
    coord1, coord2 = gate.build_gate_coordinators(_FAKE_RATE)
    assert isinstance(coord1, RunBudgetCoordinator)
    assert isinstance(coord2, RunBudgetCoordinator)
    assert coord1 is not coord2  # never one shared EUR 1.50 pool


def test_each_gate_coordinator_carries_the_adopted_per_run_cap():
    coord1, coord2 = gate.build_gate_coordinators(_FAKE_RATE)
    assert coord1.total_eur_micros == ADOPTED_PER_RUN_CAP
    assert coord2.total_eur_micros == ADOPTED_PER_RUN_CAP
    # The single resolved FX rate is shared; budget state never is.
    assert coord1.fx_rate is coord2.fx_rate is _FAKE_RATE


def test_exhausting_run_one_does_not_pre_exhaust_run_two():
    """The defect this replaces: run 1 saturated the one shared cap, so
    run 2 made zero real model calls and its idempotent-rerun/dedup
    invariants passed on exhaustion containment rather than on
    real-agent re-execution."""
    coord1, coord2 = gate.build_gate_coordinators(_FAKE_RATE)

    with pytest.raises(BudgetExhausted):
        while True:
            coord1.commit_unresolved(coord1.reserve())

    assert coord1.remaining_eur_micros() <= 0
    assert coord1.total_charged_eur_micros() == ADOPTED_PER_RUN_CAP
    # Run 2's breaker is untouched and can still fund real calls.
    assert coord2.remaining_eur_micros() == ADOPTED_PER_RUN_CAP
    assert coord2.total_charged_eur_micros() == 0
    assert coord2.reserve().reserved_eur_micros > 0


# ---------------------------------------------------------------------
# The gate's own, deliberately independent cost cross-check.
# ---------------------------------------------------------------------


def test_cost_cross_check_literals_are_the_adopted_values():
    assert gate.PER_RUN_COST_CAP_EUR_MICROS == ADOPTED_PER_RUN_CAP
    assert gate.GATE_SESSION_COST_CAP_EUR_MICROS == ADOPTED_SESSION_CAP


def test_cost_cross_check_is_not_imported_from_config_and_so_is_not_tautological():
    """The cross-check exists to disagree with the coordinator if the
    coordinator is wrong. Importing config's own limits would make it
    agree by construction, so the runner must not carry them at all."""
    assert not hasattr(gate, "RUN_BUDGET_EUR_MICROS")
    assert not hasattr(gate, "MAX_PER_CALL_RESERVE_EUR_MICROS")


def test_each_run_is_checked_against_its_own_cap_independently():
    at_cap = gate.evaluate_cost_caps(ADOPTED_PER_RUN_CAP, ADOPTED_PER_RUN_CAP)
    assert all(ok for ok, _ in at_cap)

    run1_over = gate.evaluate_cost_caps(ADOPTED_PER_RUN_CAP + 1, 0)
    assert run1_over[0][0] is False
    assert "run1" in run1_over[0][1]
    assert run1_over[1][0] is True  # run 2 is judged on its own spend

    run2_over = gate.evaluate_cost_caps(0, ADOPTED_PER_RUN_CAP + 1)
    assert run2_over[0][0] is True
    assert run2_over[1][0] is False
    assert "run2" in run2_over[1][1]


def test_the_aggregate_gate_session_bound_is_checked_explicitly():
    checks = gate.evaluate_cost_caps(ADOPTED_PER_RUN_CAP, ADOPTED_PER_RUN_CAP)
    session = [msg for _, msg in checks if "gate_session_cost_within_cap" in msg]
    assert len(session) == 1
    assert str(ADOPTED_SESSION_CAP) in session[0]
    assert session[0].endswith("PASS")

    over = gate.evaluate_cost_caps(ADOPTED_PER_RUN_CAP + 1, ADOPTED_PER_RUN_CAP + 1)
    assert over[-1][0] is False
    assert "gate_session_cost_within_cap" in over[-1][1]


# ---------------------------------------------------------------------
# Per-run wiring: which run got which breaker is observable.
# ---------------------------------------------------------------------


def test_build_run_deps_wires_the_passed_coordinator_onto_that_runs_stub(tmp_path):
    coord1, coord2 = gate.build_gate_coordinators(_FAKE_RATE)
    with patch("agents.checker.harness.query", side_effect=AssertionError("no model call")):
        deps1 = gate.build_run_deps(
            judgment_mode="agent", db_path=tmp_path / "gate.sqlite3",
            run_id="r-1", coordinator=coord1,
        )
        deps2 = gate.build_run_deps(
            judgment_mode="agent", db_path=tmp_path / "gate.sqlite3",
            run_id="r-2", coordinator=coord2,
        )

    assert isinstance(deps1.judgment, CagedCheckerStub)
    assert isinstance(deps2.judgment, CagedCheckerStub)
    assert deps1.judgment.coordinator is coord1
    assert deps2.judgment.coordinator is coord2
    assert deps1.judgment.coordinator is not deps2.judgment.coordinator


def test_build_run_deps_in_stub_mode_constructs_no_checker_agent(tmp_path):
    deps = gate.build_run_deps(
        judgment_mode="stub", db_path=tmp_path / "gate.sqlite3",
        run_id="r-1", coordinator=None,
    )
    assert not isinstance(deps.judgment, CagedCheckerStub)


def _fake_execute_run(config, deps):
    """Stands in for the real pipeline: creates the ledger schema and a
    terminal run row so run_gate's own post-run reads (including the
    ADR-0007 validity reconstruction) succeed, and makes no model call
    of any kind."""
    conn = ledger.open_ledger(config.db_path)
    try:
        with ledger.unit_of_work(conn):
            ledger.insert_run(
                conn,
                RunRecord(
                    schema_version=1, run_id=config.run_id, run_kind="dev",
                    status="COMPLETED", started_at_utc=T0, finished_at_utc=T0,
                    tasks_created=1, tasks_terminal=1, findings_new=0,
                    findings_still_open=0, findings_resolved=0,
                ),
            )
    finally:
        conn.close()
    return SimpleNamespace(
        status="COMPLETED", exit_code=0,
        tasks_created=1, tasks_terminal=1, findings_new=0,
        findings_still_open=0, findings_resolved=0,
    )


def test_run_gate_gives_each_designated_run_its_own_coordinator(tmp_path, monkeypatch):
    """End of the changed seam: run_gate must hand run 1 and run 2 two
    different breakers, each at the adopted per-run cap."""
    seen: list[tuple[str, RunBudgetCoordinator]] = []

    def _recording_build_run_deps(*, judgment_mode, db_path, run_id, coordinator):
        seen.append((run_id, coordinator))
        return gate.Deps()

    monkeypatch.setattr(gate, "build_run_deps", _recording_build_run_deps)
    monkeypatch.setattr(gate, "execute_run", _fake_execute_run)
    monkeypatch.setattr(gate, "resolve_ecb_usd_per_eur", lambda now: _FAKE_RATE)
    monkeypatch.setattr(gate.auth, "assert_no_auth_override_risk", lambda *a, **k: None)

    with patch("agents.checker.harness.query", side_effect=AssertionError("no model call")):
        result = gate.run_gate(judgment_mode="agent", gate_root=tmp_path / "gate")

    assert len(seen) == 2
    (run1_id, coord1), (run2_id, coord2) = seen
    assert run1_id == result["run1_id"]
    assert run2_id == result["run2_id"]
    assert run1_id != run2_id
    assert coord1 is not coord2
    assert coord1.total_eur_micros == ADOPTED_PER_RUN_CAP
    assert coord2.total_eur_micros == ADOPTED_PER_RUN_CAP

    # Both cost cross-checks are emitted on every gate run, not only on
    # the failing path.
    check_text = "\n".join(result["checks"])
    assert "run1_cost_within_run_cap" in check_text
    assert "run2_cost_within_run_cap" in check_text
    assert "gate_session_cost_within_cap" in check_text


# ---------------------------------------------------------------------
# ADR-0007 Stage 2: execution-validity predicates reconstructed from
# persisted ledger state (adr/0007 §2). Seeding uses only the existing
# ledger writers; nothing here touches a model or the network.
# ---------------------------------------------------------------------

RUN1 = "r-11111111111111111111111111111111"
RUN2 = "r-22222222222222222222222222222222"
SHA_A = "a" * 40
SHA_B = "b" * 40

_TASK_SEQ = itertools.count()

_CALL_FX = dict(
    fx_source="ecb-eurofxref-daily",
    fx_rate_date="2026-08-19",
    fx_retrieved_at_utc=T0,
    fx_rate_decimal="1.1554",
)


def _seed_run(conn, run_id, *, status="COMPLETED", tasks_created=2, tasks_terminal=None):
    if tasks_terminal is None:
        tasks_terminal = tasks_created if status == "COMPLETED" else 0
    with ledger.unit_of_work(conn):
        ledger.insert_run(
            conn,
            RunRecord(
                schema_version=1, run_id=run_id, run_kind="dev", status=status,
                started_at_utc=T0,
                finished_at_utc=None if status == "RUNNING" else T0,
                tasks_created=tasks_created, tasks_terminal=tasks_terminal,
                findings_new=0, findings_still_open=0, findings_resolved=0,
            ),
        )


def _seed_task(conn, run_id, status):
    with ledger.unit_of_work(conn):
        ledger.insert_tasks(
            conn,
            [CheckTask(
                schema_version=1, task_id=f"t-{next(_TASK_SEQ):04d}", run_id=run_id,
                surface="synthetic-01/STATE.md", check_class="stale-STATE-marker",
                created_at_utc=T0, status=status,
            )],
        )


_SURFACE_SEQ = itertools.count()


def _new_task_key():
    """A fresh logical judgment identity. ADR-0009 groups invocation
    rows by (run_id, task_key), so every seeded call gets its own
    logical task unless a test deliberately shares one."""
    return f"synthetic-{next(_SURFACE_SEQ):03d}/STATE.md::stale-STATE-marker"


def _attempt(ordinal, outcome=checker_tools.ACCEPTED):
    """One persisted tool-attempt audit row, in the real record shape
    the harness flushes (agents/checker/tools.py::ToolAttemptRecord)."""
    return ToolAttemptRecord(
        ordinal=ordinal,
        proposed_reason_code="MARKER_STALE",
        proposed_evidence_count=1,
        primary_line=1,
        secondary_line=None,
        outcome=outcome,
    )


def _seed_agent_call(
    conn, run_id, state, *,
    reserved=None, task_key=None, charged=None,
    sdk_is_error=None, sdk_subtype=None, rejection_reason=None,
    attempts=(), tool_attempts=None,
):
    """Mirrors the harness: RESERVED/COMPLETED/FAILED carry a positive
    reservation persisted before the SDK call; REJECTED/EXHAUSTED are
    never-called rows with reserved = charged = 0. Tool-attempt audit
    rows are written in the SAME transaction as finalization, exactly as
    ``harness._terminalize`` does, so ADR-0009's audit-completeness rule
    is exercised against the real durable representation.

    ``tool_attempts`` defaults to ``len(attempts)`` — the invariant the
    real runtime always satisfies. A test may set it higher to seed the
    inconsistent audit ADR-0009 must fail closed on."""
    if reserved is None:
        reserved = 0 if state in ("REJECTED", "EXHAUSTED") else 150_000
    if task_key is None:
        task_key = _new_task_key()
    if charged is None:
        charged = min(reserved, 1_000) if state == "COMPLETED" else reserved
    if tool_attempts is None:
        tool_attempts = len(attempts)
    with ledger.unit_of_work(conn):
        call_id = ledger.insert_agent_call_reserved(
            conn, run_id=run_id,
            task_key=task_key,
            surface=task_key.split("::")[0], check_class="stale-STATE-marker",
            model="claude-haiku-4-5-20251001",
            auth_mode="operator-subscription-oauth-assumed",
            started_at_utc=T0, reserved_eur_micros=reserved, **_CALL_FX,
        )
        if state != "RESERVED":
            ledger.insert_tool_attempts(conn, call_id, attempts)
            ledger.finalize_agent_call(
                conn, call_id, state=state, finished_at_utc=T0,
                charged_eur_micros=charged,
                sdk_is_error=sdk_is_error, sdk_subtype=sdk_subtype,
                tool_attempts=tool_attempts, rejection_reason=rejection_reason,
            )
    return call_id


def _seed_budget_ceiling_failure(conn, run_id, *, task_key=None, **overrides):
    """One FAILED row that reconstructs as SDK_BUDGET_CEILING unless a
    test overrides one of its components: complete audit, no
    BREAKER_REFUSED, typed budget subtype, sdk_is_error true, positive
    reservation."""
    kwargs = dict(
        reserved=150_000,
        sdk_is_error=True,
        sdk_subtype=gate.RECOVERY_SDK_SUBTYPE,
        attempts=(_attempt(1),),
    )
    kwargs.update(overrides)
    return _seed_agent_call(
        conn, run_id, "FAILED", task_key=task_key or _new_task_key(), **kwargs
    )


def _seed_recovered_history(conn, run_id, *, task_key=None, second_state="COMPLETED", **overrides):
    """The ADR-0009 BOUNDED RECOVERY shape: a budget-ceiling FAILED row
    followed by a COMPLETED row for the SAME logical task."""
    task_key = task_key or _new_task_key()
    _seed_budget_ceiling_failure(conn, run_id, task_key=task_key, **overrides)
    _seed_agent_call(conn, run_id, second_state, task_key=task_key)
    return task_key


def _seed_normal_history(conn, run_id, *, task_key=None):
    return _seed_agent_call(conn, run_id, "COMPLETED", task_key=task_key or _new_task_key())


def _seeded_conn(
    tmp_path, *,
    run1_status="COMPLETED", run2_status="COMPLETED",
    run1_calls=("COMPLETED", "COMPLETED"), run2_calls=("COMPLETED", "COMPLETED"),
    run1_tasks=(), run2_tasks=(),
):
    """Each entry in run*_calls becomes its OWN logical task (its own
    task_key), so ("COMPLETED", "COMPLETED") is two valid NORMAL
    histories rather than one invalid double-completion."""
    conn = ledger.open_ledger(tmp_path / "gate.sqlite3")
    for run_id, status, calls, tasks in (
        (RUN1, run1_status, run1_calls, run1_tasks),
        (RUN2, run2_status, run2_calls, run2_tasks),
    ):
        _seed_run(conn, run_id, status=status)
        for task_status in tasks:
            _seed_task(conn, run_id, task_status)
        for call_state in calls:
            _seed_agent_call(conn, run_id, call_state)
    return conn


def _history_conn(tmp_path, run1_seeder, run2_seeder=None):
    """A fresh two-run ledger whose logical histories are seeded
    explicitly. run 2 defaults to one valid NORMAL history, so a single
    run-1 history is the only thing under test."""
    conn = ledger.open_ledger(tmp_path / "gate.sqlite3")
    _seed_run(conn, RUN1)
    _seed_run(conn, RUN2)
    run1_seeder(conn, RUN1)
    (run2_seeder or _seed_normal_history)(conn, RUN2)
    return conn


def _histories(validity, label="run1"):
    return {h["task_key"]: h for h in validity[label]["logical_histories"]}


def _only_history(validity, label="run1"):
    rows = validity[label]["logical_histories"]
    assert len(rows) == 1, rows
    return rows[0]


def _validity(conn, *, required=SHA_A, attested=SHA_A, exit1=0, exit2=0):
    return gate.evaluate_execution_validity(
        conn, run1_id=RUN1, run2_id=RUN2,
        outcome1=SimpleNamespace(exit_code=exit1),
        outcome2=SimpleNamespace(exit_code=exit2),
        required_source_sha=required, attested_source_sha=attested,
    )


def test_validity_passes_on_fully_valid_two_run_ledger(tmp_path):
    conn = _seeded_conn(tmp_path, run1_tasks=("DONE",), run2_tasks=("DONE",))
    validity = _validity(conn)
    assert validity["valid"] is True
    assert all(validity["predicates"].values())


@pytest.mark.parametrize("bad_run", ["run1", "run2"])
@pytest.mark.parametrize("status", ["RUNNING", "FAILED"])
def test_non_completed_run_status_blocks_validity(tmp_path, bad_run, status):
    conn = _seeded_conn(tmp_path, **{f"{bad_run}_status": status})
    validity = _validity(conn)
    assert validity["predicates"][f"{bad_run}_completed"] is False
    assert validity["valid"] is False


@pytest.mark.parametrize("bad_run", ["run1", "run2"])
@pytest.mark.parametrize("task_status,predicate", [
    ("FAILED", "zero_failed_tasks"),
    ("DEAD_LETTER", "zero_dead_letter_tasks"),
])
def test_failed_or_dead_letter_task_blocks_validity(tmp_path, bad_run, task_status, predicate):
    conn = _seeded_conn(tmp_path, **{f"{bad_run}_tasks": (task_status,)})
    validity = _validity(conn)
    assert validity["predicates"][predicate] is False
    assert validity["valid"] is False


@pytest.mark.parametrize("call_state", ["FAILED", "REJECTED", "EXHAUSTED", "RESERVED"])
def test_non_completed_agent_call_blocks_validity(tmp_path, call_state):
    """RESERVED here is a row inserted and never finalized — the
    crash-unresolved shape — and it blocks PASS like the others. Under
    ADR-0009 a lone non-COMPLETED row is an invalid logical history; the
    never-invoked and non-terminal states additionally keep their own
    standalone predicates, which ADR-0009 does not weaken."""
    conn = _seeded_conn(tmp_path, run2_calls=("COMPLETED", "COMPLETED", call_state))
    validity = _validity(conn)
    assert validity["predicates"]["all_logical_histories_valid"] is False
    if call_state != "FAILED":
        assert validity["predicates"][f"zero_agent_calls_{call_state.lower()}"] is False
    assert validity["valid"] is False


@pytest.mark.parametrize("required,attested", [
    (None, SHA_A),            # required missing
    (SHA_A, None),            # nothing was actually verified
    (SHA_A, SHA_B),           # supplied but mismatched
    ("A" * 40, "A" * 40),     # not lowercase hex
    ("abc", "abc"),           # not 40 chars
])
def test_source_attestation_is_mechanical_never_mere_presence(tmp_path, required, attested):
    """A supplied --require-source-sha alone must NEVER attest the
    source: only the preflight-verified HEAD exactly equal to a valid
    40-lowercase-hex required SHA can, even on an otherwise fully
    valid ledger."""
    conn = _seeded_conn(tmp_path)
    validity = _validity(conn, required=required, attested=attested)
    assert validity["predicates"]["source_sha_attested"] is False
    assert validity["valid"] is False


def test_validity_reports_counts_for_independent_reconstruction(tmp_path):
    conn = _seeded_conn(
        tmp_path,
        run1_calls=("COMPLETED", "FAILED"),
        run2_calls=("RESERVED",),
        run1_tasks=("DONE", "DEAD_LETTER"),
    )
    validity = _validity(conn)
    r1, r2 = validity["run1"], validity["run2"]
    assert r1["run_status"] == "COMPLETED"
    assert r1["agent_call_state_counts"]["COMPLETED"] == 1
    assert r1["agent_call_state_counts"]["FAILED"] == 1
    assert r1["failed_agent_calls"] == 1
    assert r1["agent_call_rows_total"] == 2
    assert r1["reserved_positive_calls"] == 2  # both rows reserved > 0
    assert r1["model_invocation_rows"] == 2
    assert r1["model_path_task_count"] == 2  # two separate logical tasks
    assert r1["normal_history_count"] == 1
    assert r1["recovered_history_count"] == 0
    assert r1["invalid_history_count"] == 1  # the lone FAILED row
    assert r1["tasks_dead_letter"] == 1
    assert r2["agent_call_state_counts"]["RESERVED"] == 1
    assert r2["reserved_positive_calls"] == 1
    assert validity["required_source_sha"] == SHA_A
    assert validity["attested_source_sha"] == SHA_A
    assert validity["relevant_call_definition"]
    assert validity["definitions"]["valid_histories"] == [
        "[COMPLETED]",
        "[FAILED reconstructed as SDK_BUDGET_CEILING, COMPLETED]",
    ]
    assert len(validity["check_lines"]) == len(validity["predicates"])
    # Every logical history carries enough to be re-derived by hand.
    for history in r1["logical_histories"]:
        assert set(history) == {
            "task_key", "call_ids", "states", "model_path",
            "valid", "classification", "invalid_reason",
        }


# =====================================================================
# ADR-0009 §2 — logical judgment histories. V1-V13, V17.
# =====================================================================


def test_v1_normal_history_is_valid(tmp_path):
    conn = _history_conn(tmp_path, _seed_normal_history)
    validity = _validity(conn)
    history = _only_history(validity)
    assert history["states"] == ["COMPLETED"]
    assert history["valid"] is True
    assert history["classification"] == gate.NORMAL_HISTORY
    assert history["invalid_reason"] is None
    assert validity["valid"] is True


def test_v2_bounded_recovery_history_is_valid(tmp_path):
    """[FAILED reconstructed as SDK_BUDGET_CEILING, COMPLETED]: complete
    tool audit, no BREAKER_REFUSED, typed budget subtype, sdk_is_error
    true, positive reservation."""
    conn = _history_conn(tmp_path, lambda c, r: _seed_recovered_history(c, r))
    validity = _validity(conn)
    history = _only_history(validity)
    assert history["states"] == ["FAILED", "COMPLETED"]
    assert history["valid"] is True
    assert history["classification"] == gate.BOUNDED_RECOVERY_HISTORY
    assert validity["run1"]["recovered_history_count"] == 1
    assert validity["run1"]["model_path_task_count"] == 1
    assert validity["valid"] is True


def test_v3_lone_failed_row_is_invalid(tmp_path):
    conn = _history_conn(tmp_path, lambda c, r: _seed_budget_ceiling_failure(c, r))
    validity = _validity(conn)
    history = _only_history(validity)
    assert history["valid"] is False
    assert history["invalid_reason"] == gate.INVALID_LONE_FAILED_ROW
    assert validity["predicates"]["all_logical_histories_valid"] is False
    assert validity["valid"] is False


def test_v4_failed_with_other_subtype_then_completed_is_invalid(tmp_path):
    conn = _history_conn(
        tmp_path,
        lambda c, r: _seed_recovered_history(c, r, sdk_subtype="error_during_execution"),
    )
    validity = _validity(conn)
    history = _only_history(validity)
    assert history["valid"] is False
    assert history["invalid_reason"] == gate.INVALID_RECOVERY_SUBTYPE
    assert validity["valid"] is False


def test_v4_failed_without_sdk_error_flag_then_completed_is_invalid(tmp_path):
    conn = _history_conn(
        tmp_path, lambda c, r: _seed_recovered_history(c, r, sdk_is_error=False)
    )
    validity = _validity(conn)
    assert _only_history(validity)["invalid_reason"] == gate.INVALID_RECOVERY_NOT_SDK_ERROR
    assert validity["valid"] is False


def test_v4_failed_with_zero_reservation_then_completed_is_invalid(tmp_path):
    conn = _history_conn(tmp_path, lambda c, r: _seed_recovered_history(c, r, reserved=0))
    validity = _validity(conn)
    assert _only_history(validity)["invalid_reason"] == gate.INVALID_RECOVERY_NO_RESERVATION
    assert validity["valid"] is False


def test_v5_budget_ceiling_then_failed_is_invalid(tmp_path):
    conn = _history_conn(
        tmp_path, lambda c, r: _seed_recovered_history(c, r, second_state="FAILED")
    )
    validity = _validity(conn)
    history = _only_history(validity)
    assert history["states"] == ["FAILED", "FAILED"]
    assert history["invalid_reason"] == gate.INVALID_SECOND_ROW_NOT_COMPLETED
    assert validity["valid"] is False


def test_v6_two_completed_rows_for_one_logical_task_are_invalid(tmp_path):
    def seed(conn, run_id):
        key = _new_task_key()
        _seed_agent_call(conn, run_id, "COMPLETED", task_key=key)
        _seed_agent_call(conn, run_id, "COMPLETED", task_key=key)

    conn = _history_conn(tmp_path, seed)
    validity = _validity(conn)
    history = _only_history(validity)
    assert history["states"] == ["COMPLETED", "COMPLETED"]
    assert history["invalid_reason"] == gate.INVALID_FIRST_ROW_NOT_FAILED
    assert validity["valid"] is False


def test_v7_three_invocation_rows_are_invalid(tmp_path):
    def seed(conn, run_id):
        key = _new_task_key()
        _seed_budget_ceiling_failure(conn, run_id, task_key=key)
        _seed_budget_ceiling_failure(conn, run_id, task_key=key)
        _seed_agent_call(conn, run_id, "COMPLETED", task_key=key)

    conn = _history_conn(tmp_path, seed)
    validity = _validity(conn)
    history = _only_history(validity)
    assert len(history["call_ids"]) == 3
    assert history["invalid_reason"] == gate.INVALID_TOO_MANY_ROWS
    assert validity["valid"] is False


@pytest.mark.parametrize("state,reason", [
    ("REJECTED", "INVALID_REJECTED_ROW"),    # V8
    ("EXHAUSTED", "INVALID_EXHAUSTED_ROW"),  # V9
    ("RESERVED", "INVALID_RESERVED_ROW"),    # V10
])
def test_v8_v9_v10_never_invoked_or_non_terminal_rows_are_invalid(tmp_path, state, reason):
    conn = _history_conn(tmp_path, lambda c, r: _seed_agent_call(c, r, state))
    validity = _validity(conn)
    history = _only_history(validity)
    assert history["invalid_reason"] == getattr(gate, reason)
    assert validity["valid"] is False


_MISLEADING_BUDGET_PROSE = (
    "SDK_BUDGET_CEILING: SDK per-call budget ceiling "
    "(subtype='error_max_budget_usd')"
)


def test_v11_tool_breaker_carrying_the_budget_subtype_is_never_a_recovery(tmp_path):
    """LOAD-BEARING NEGATIVE (adr/0009 §2A). Every corroborating field
    says budget ceiling — typed subtype, sdk_is_error, positive
    reservation — and rejection_reason is deliberately written to LOOK
    like SDK_BUDGET_CEILING. The structured audit says a tool-call
    circuit breaker refused a proposal, and ADR-0008's classifier gives
    containment precedence, so the mechanized class is TOOL_BREAKER and
    the recovery is INVALID."""
    conn = _history_conn(
        tmp_path,
        lambda c, r: _seed_recovered_history(
            c, r,
            attempts=(_attempt(1), _attempt(2, checker_tools.BREAKER_REFUSED)),
            rejection_reason=_MISLEADING_BUDGET_PROSE,
        ),
    )
    validity = _validity(conn)
    history = _only_history(validity)
    assert history["states"] == ["FAILED", "COMPLETED"]
    assert history["valid"] is False
    assert history["invalid_reason"] == gate.INVALID_RECOVERY_TOOL_BREAKER
    assert validity["predicates"]["all_logical_histories_valid"] is False
    assert validity["valid"] is False


def test_v11_breaker_outcome_is_the_one_the_runtime_actually_writes():
    """The negative above is only load-bearing if the runner is looking
    for the outcome string the harness really persists."""
    assert gate.BREAKER_REFUSED_OUTCOME == checker_tools.BREAKER_REFUSED
    assert gate.RECOVERY_SDK_SUBTYPE == checker_failures.SDK_BUDGET_CEILING_SUBTYPE


@pytest.mark.parametrize("prose", [
    None,
    "",
    _MISLEADING_BUDGET_PROSE,
    "SDK_BUDGET_CEILING",
    "tool-call circuit breaker tripped",
    "totally unrelated text",
])
def test_v12_rejection_reason_prose_never_changes_validity(tmp_path, prose):
    """Two otherwise identical histories differing ONLY in
    rejection_reason must receive the same ADR-0009 verdict — once where
    the structured evidence says valid recovery, once where it says
    containment."""
    valid_conn = _history_conn(
        tmp_path / "valid",
        lambda c, r: _seed_recovered_history(c, r, rejection_reason=prose),
    )
    assert _only_history(_validity(valid_conn))["valid"] is True

    breaker_conn = _history_conn(
        tmp_path / "breaker",
        lambda c, r: _seed_recovered_history(
            c, r,
            attempts=(_attempt(1), _attempt(2, checker_tools.BREAKER_REFUSED)),
            rejection_reason=prose,
        ),
    )
    breaker_history = _only_history(_validity(breaker_conn))
    assert breaker_history["valid"] is False
    assert breaker_history["invalid_reason"] == gate.INVALID_RECOVERY_TOOL_BREAKER


def test_v12_runner_never_reads_rejection_reason():
    """Structural, not behavioural: no executable statement in the whole
    runner may touch the prose field. Parsed as an AST so prose ABOUT
    the rule (docstrings, comments) is not mistaken for a read of it."""
    source = (gate.REPO_ROOT / "scripts" / "run_phase3_dev_gate.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    attribute_reads = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "rejection_reason"
    ]
    subscript_reads = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == "rejection_reason"
    ]
    assert not attribute_reads and not subscript_reads


def test_v13_audit_row_count_below_declared_attempts_fails_closed(tmp_path):
    """agent_call.tool_attempts says 3, only 2 audit rows persisted: the
    audit that would have carried a BREAKER_REFUSED row is incomplete,
    so its absence proves nothing and recovery is refused."""
    conn = _history_conn(
        tmp_path,
        lambda c, r: _seed_recovered_history(
            c, r, attempts=(_attempt(1), _attempt(2)), tool_attempts=3
        ),
    )
    validity = _validity(conn)
    history = _only_history(validity)
    assert history["valid"] is False
    assert history["invalid_reason"] == gate.INVALID_RECOVERY_AUDIT_INCOMPLETE
    assert validity["valid"] is False


def test_v13_ordinal_gap_with_matching_count_fails_closed(tmp_path):
    """Count alone is not enough: ordinals 1 and 3 with tool_attempts=2
    means one proposal's record is missing and another is misplaced."""
    conn = _history_conn(
        tmp_path,
        lambda c, r: _seed_recovered_history(
            c, r, attempts=(_attempt(1), _attempt(3)), tool_attempts=2
        ),
    )
    history = _only_history(_validity(conn))
    assert history["invalid_reason"] == gate.INVALID_RECOVERY_AUDIT_INCOMPLETE


def test_v13_zero_attempt_audit_is_complete_and_permits_recovery(tmp_path):
    """The boundary the fail-closed rule must NOT over-reach: a model
    that made no tool call at all leaves tool_attempts = 0 and zero
    rows, which is a complete audit."""
    conn = _history_conn(
        tmp_path, lambda c, r: _seed_recovered_history(c, r, attempts=())
    )
    history = _only_history(_validity(conn))
    assert history["valid"] is True
    assert history["classification"] == gate.BOUNDED_RECOVERY_HISTORY


def test_v17_a_valid_recovered_history_has_exactly_one_completed_outcome(tmp_path):
    conn = _history_conn(tmp_path, lambda c, r: _seed_recovered_history(c, r))
    validity = _validity(conn)
    history = _only_history(validity)
    assert history["valid"] is True
    assert history["states"].count("COMPLETED") == 1
    assert history["states"].count("FAILED") == 1
    assert validity["run1"]["completed_agent_calls"] == 1


# =====================================================================
# ADR-0009 §3 — logical cross-run coverage. V14-V16.
# =====================================================================


def test_v14_recovered_task_counts_once_against_a_normal_task(tmp_path):
    """run 1 recovers one logical task (two invocation rows); run 2
    covers the same logical work normally (one row). Raw call counts
    differ; logical coverage is equal, and the predicate PASSES."""
    def seed_run1(conn, run_id):
        _seed_recovered_history(conn, run_id)
        _seed_normal_history(conn, run_id)

    def seed_run2(conn, run_id):
        _seed_normal_history(conn, run_id)
        _seed_normal_history(conn, run_id)

    conn = _history_conn(tmp_path, seed_run1, seed_run2)
    validity = _validity(conn)
    r1, r2 = validity["run1"], validity["run2"]
    assert r1["model_invocation_rows"] == 3
    assert r2["model_invocation_rows"] == 2  # raw rows differ
    assert r1["model_path_task_count"] == r2["model_path_task_count"] == 2
    assert r1["recovered_history_count"] == 1
    assert validity["predicates"]["logical_task_coverage_equal"] is True
    assert validity["predicates"]["all_logical_histories_valid"] is True
    assert validity["valid"] is True


def test_v15_unequal_logical_coverage_blocks_validity(tmp_path):
    def seed_run1(conn, run_id):
        _seed_normal_history(conn, run_id)
        _seed_normal_history(conn, run_id)

    conn = _history_conn(tmp_path, seed_run1, _seed_normal_history)
    validity = _validity(conn)
    assert validity["predicates"]["run1_has_model_path_tasks"] is True
    assert validity["predicates"]["run2_has_model_path_tasks"] is True
    assert validity["predicates"]["logical_task_coverage_equal"] is False
    assert validity["valid"] is False


@pytest.mark.parametrize("empty_run", ["run1", "run2"])
def test_v16_zero_model_path_coverage_blocks_validity(tmp_path, empty_run):
    seeders = {"run1": _seed_normal_history, "run2": _seed_normal_history}
    seeders[empty_run] = lambda c, r: None
    conn = _history_conn(tmp_path, seeders["run1"], seeders["run2"])
    validity = _validity(conn)
    assert validity["predicates"][f"{empty_run}_has_model_path_tasks"] is False
    assert validity["valid"] is False


def test_v16_never_invoked_rows_do_not_create_model_path_coverage(tmp_path):
    """An EXHAUSTED row is reserved = 0: no SDK invocation happened, so
    it is not model-path coverage — and it is an invalid history too."""
    conn = _history_conn(tmp_path, lambda c, r: _seed_agent_call(c, r, "EXHAUSTED"))
    validity = _validity(conn)
    assert validity["run1"]["model_path_task_count"] == 0
    assert validity["predicates"]["run1_has_model_path_tasks"] is False
    assert validity["valid"] is False


# =====================================================================
# ADR-0009 §5 — the consumption component C. V24-V25.
# =====================================================================


def test_v24_consumption_count_c_includes_both_rows_of_a_recovery(tmp_path):
    conn = _history_conn(
        tmp_path, lambda c, r: _seed_recovered_history(c, r), _seed_normal_history
    )
    validity = _validity(conn)
    assert validity["consumption_count_c"] == 3  # 2 run-1 rows + 1 run-2 row


def test_v25_zero_consumption_is_distinguishable_from_positive(tmp_path):
    """C == 0 versus C > 0 is read off positive reservations alone —
    never from model output or exception prose."""
    empty = _history_conn(tmp_path / "empty", lambda c, r: None, lambda c, r: None)
    assert _validity(empty)["consumption_count_c"] == 0

    used = _history_conn(tmp_path / "used", _seed_normal_history, _seed_normal_history)
    assert _validity(used)["consumption_count_c"] == 2


def test_v25_never_invoked_rows_do_not_count_toward_c(tmp_path):
    conn = _history_conn(
        tmp_path,
        lambda c, r: _seed_agent_call(c, r, "REJECTED"),
        lambda c, r: _seed_agent_call(c, r, "EXHAUSTED"),
    )
    assert _validity(conn)["consumption_count_c"] == 0


# ---------------------------------------------------------------------
# ADR-0007 Stage 2: run_gate composition — the frozen checks stay
# frozen, and OVERALL PASS now also requires execution validity.
# ---------------------------------------------------------------------


def _install_agent_mode_fakes(monkeypatch):
    monkeypatch.setattr(gate, "resolve_ecb_usd_per_eur", lambda now: _FAKE_RATE)
    monkeypatch.setattr(gate.auth, "assert_no_auth_override_risk", lambda *a, **k: None)
    monkeypatch.setattr(gate, "build_run_deps", lambda **kw: gate.Deps())


def _insert_answer_key_findings(conn, run_id):
    """Seeds one OPEN finding per answer-key row so every frozen
    scoring threshold genuinely computes PASS (60/60 exact matches).
    The frozen answer key is read-only input here."""
    rows = gate._read_jsonl(gate.ANSWER_KEY_PATH)
    with ledger.unit_of_work(conn):
        for i, row in enumerate(rows):
            content_hash = compute_content_hash(row["location"], f"seed={i}")
            ledger.insert_finding(
                conn,
                Finding(
                    schema_version=1,
                    fingerprint=compute_fingerprint(row["surface"], row["check_class"], content_hash),
                    surface=row["surface"], check_class=row["check_class"],
                    content_hash=content_hash, location=row["location"],
                    detail=f"seeded regression finding {i}", status="OPEN",
                    first_seen_utc=T0, last_seen_utc=T0,
                    first_seen_run_id=run_id, last_seen_run_id=run_id,
                ),
            )
    return len(rows)


def _make_seeding_execute_run(seed_run_fn):
    """Returns an execute_run double that seeds the real gate ledger
    via seed_run_fn(conn, run_id, index) and returns the outcome that
    function provides. No model call of any kind."""
    seen: list[str] = []

    def _fake(config, deps):
        seen.append(config.run_id)
        conn = ledger.open_ledger(config.db_path)
        try:
            return seed_run_fn(conn, config.run_id, len(seen))
        finally:
            conn.close()

    return _fake


def test_historical_false_pass_shape_is_now_overall_fail(tmp_path, monkeypatch):
    """The exact defect ADR-0007 closes: every frozen scoring
    threshold, invariant and cost cap computes PASS (DEAD_LETTER
    counts as terminal, so every_task_terminal held historically) while
    the judgment work itself FAILED/DEAD_LETTERed — OVERALL PASS must
    now be impossible, and only execution-validity checks may FAIL."""

    def seed(conn, run_id, index):
        _seed_run(conn, run_id, status="FAILED", tasks_created=22, tasks_terminal=22)
        for _ in range(2):
            _seed_task(conn, run_id, "DEAD_LETTER")
            _seed_agent_call(conn, run_id, "FAILED")
        findings = _insert_answer_key_findings(conn, run_id) if index == 1 else 0
        return SimpleNamespace(
            status="FAILED", exit_code=1,
            tasks_created=22, tasks_terminal=22,
            findings_new=findings, findings_still_open=60, findings_resolved=0,
        )

    _install_agent_mode_fakes(monkeypatch)
    monkeypatch.setattr(gate, "execute_run", _make_seeding_execute_run(seed))
    with patch("agents.checker.harness.query", side_effect=AssertionError("no model call")):
        result = gate.run_gate(
            judgment_mode="agent", gate_root=tmp_path / "gate",
            required_source_sha=SHA_A, attested_source_sha=SHA_A,
        )

    failing = [line for line in result["checks"] if line.endswith("FAIL")]
    assert failing, "the invalid execution must produce failing checks"
    assert all(line.startswith("execution_validity[") for line in failing), (
        "every frozen scoring/invariant/cost check still computes PASS -- "
        f"unexpected frozen failures: {failing}"
    )
    assert result["overall_pass"] is False
    assert result["execution_validity"]["valid"] is False
    assert result["execution_validity"]["predicates"]["zero_dead_letter_tasks"] is False
    # V18: the lone-FAILED shape stays invalid under ADR-0009 too — a
    # FAILED row is permitted ONLY as the first row of the exact valid
    # bounded-recovery history.
    assert result["execution_validity"]["predicates"]["all_logical_histories_valid"] is False
    assert all(
        h["invalid_reason"] == gate.INVALID_LONE_FAILED_ROW
        for h in result["execution_validity"]["run1"]["logical_histories"]
    )


def test_fully_valid_execution_leaves_frozen_checks_capable_of_pass(tmp_path, monkeypatch):
    """Monotonicity: with a mechanically valid execution the new seam
    changes nothing — the frozen checks alone still decide, and a
    clean run reaches OVERALL PASS."""

    def seed(conn, run_id, index):
        _seed_run(conn, run_id, status="COMPLETED", tasks_created=22, tasks_terminal=22)
        for _ in range(2):
            _seed_agent_call(conn, run_id, "COMPLETED")
        findings = _insert_answer_key_findings(conn, run_id) if index == 1 else 0
        return SimpleNamespace(
            status="COMPLETED", exit_code=0,
            tasks_created=22, tasks_terminal=22,
            findings_new=findings, findings_still_open=60, findings_resolved=0,
        )

    _install_agent_mode_fakes(monkeypatch)
    monkeypatch.setattr(gate, "execute_run", _make_seeding_execute_run(seed))
    with patch("agents.checker.harness.query", side_effect=AssertionError("no model call")):
        result = gate.run_gate(
            judgment_mode="agent", gate_root=tmp_path / "gate",
            required_source_sha=SHA_A, attested_source_sha=SHA_A,
        )

    assert result["overall_pass"] is True
    assert result["execution_validity"]["valid"] is True
    assert result["required_source_sha"] == result["attested_source_sha"] == SHA_A


# =====================================================================
# ADR-0009 §4 — accounted-consumption ACCEPTANCE ceilings. V19-V23.
# =====================================================================


def test_v19_per_run_cost_exactly_at_the_ceiling_passes():
    checks = gate.evaluate_cost_caps(ADOPTED_PER_RUN_CAP, ADOPTED_PER_RUN_CAP)
    assert checks[0][0] is True
    assert checks[1][0] is True


def test_v20_per_run_cost_one_micro_euro_over_the_ceiling_fails():
    checks = gate.evaluate_cost_caps(ADOPTED_PER_RUN_CAP + 1, 0)
    assert checks[0][0] is False
    assert str(ADOPTED_PER_RUN_CAP + 1) in checks[0][1]


def test_v21_session_cost_exactly_at_the_ceiling_passes():
    checks = gate.evaluate_cost_caps(ADOPTED_PER_RUN_CAP, ADOPTED_PER_RUN_CAP)
    session = checks[-1]
    assert session[0] is True
    assert str(ADOPTED_SESSION_CAP) in session[1]


def test_v22_session_cost_over_the_ceiling_fails():
    checks = gate.evaluate_cost_caps(ADOPTED_PER_RUN_CAP, ADOPTED_PER_RUN_CAP + 1)
    assert checks[-1][0] is False


def test_v23_known_charged_overshoot_is_aggregated_in_full_never_clamped(tmp_path):
    """adr/0008 §7 / adr/0009 §4: the SDK enforces its per-call budget
    after API-call activity, so a terminal row can legitimately carry
    charged > reserved. The gate must account the FULL charged amount —
    clamping it back to the reservation would manufacture a PASS."""
    conn = ledger.open_ledger(tmp_path / "gate.sqlite3")
    _seed_run(conn, RUN1)
    _seed_agent_call(
        conn, RUN1, "COMPLETED", reserved=150_000, charged=ADOPTED_PER_RUN_CAP + 1
    )
    row = costs.build_agent_cost_row(
        conn, run_id=RUN1, run_kind="dev", recorded_at_utc=T0
    )
    assert row.cost_eur_micros == ADOPTED_PER_RUN_CAP + 1  # no min(), no clamp

    checks = gate.evaluate_cost_caps(row.cost_eur_micros, 0)
    assert checks[0][0] is False, "accounted overshoot past the ceiling must FAIL"
    # The session bound is judged on its own: one run's overshoot past
    # the per-run ceiling is still under EUR 1.50 in aggregate, and the
    # per-run FAIL alone is enough to fail the gate.
    assert checks[-1][0] is True
    assert gate.evaluate_cost_caps(row.cost_eur_micros, ADOPTED_PER_RUN_CAP)[-1][0] is False


def test_v23_overshoot_within_the_ceiling_is_still_accounted_in_full(tmp_path):
    conn = ledger.open_ledger(tmp_path / "gate.sqlite3")
    _seed_run(conn, RUN1)
    _seed_agent_call(conn, RUN1, "COMPLETED", reserved=150_000, charged=200_000)
    row = costs.build_agent_cost_row(
        conn, run_id=RUN1, run_kind="dev", recorded_at_utc=T0
    )
    assert row.cost_eur_micros == 200_000
    assert gate.evaluate_cost_caps(row.cost_eur_micros, 0)[0][0] is True


# =====================================================================
# ADR-0009 Stage 2 §10 — end-to-end run_gate composition, model-free.
# =====================================================================


def _adr0009_composition_seed(*, breaker_on_first_failure):
    """run 1 covers two logical judgment tasks, one of them through a
    bounded recovery; run 2 covers the same two logically, normally.
    Frozen scoring, invariants and cost ceilings are all seeded to PASS,
    so execution validity is the only thing that can decide."""

    def seed(conn, run_id, index):
        _seed_run(conn, run_id, status="COMPLETED", tasks_created=22, tasks_terminal=22)
        if index == 1:
            attempts = (
                (_attempt(1), _attempt(2, checker_tools.BREAKER_REFUSED))
                if breaker_on_first_failure
                else (_attempt(1),)
            )
            _seed_recovered_history(
                conn, run_id,
                attempts=attempts,
                rejection_reason=_MISLEADING_BUDGET_PROSE,
            )
            _seed_normal_history(conn, run_id)
        else:
            _seed_normal_history(conn, run_id)
            _seed_normal_history(conn, run_id)
        findings = _insert_answer_key_findings(conn, run_id) if index == 1 else 0
        return SimpleNamespace(
            status="COMPLETED", exit_code=0,
            tasks_created=22, tasks_terminal=22,
            findings_new=findings, findings_still_open=60, findings_resolved=0,
        )

    return seed


def _run_adr0009_composition(tmp_path, monkeypatch, *, breaker_on_first_failure):
    _install_agent_mode_fakes(monkeypatch)
    monkeypatch.setattr(
        gate, "execute_run",
        _make_seeding_execute_run(
            _adr0009_composition_seed(breaker_on_first_failure=breaker_on_first_failure)
        ),
    )
    with patch("agents.checker.harness.query", side_effect=AssertionError("no model call")):
        return gate.run_gate(
            judgment_mode="agent", gate_root=tmp_path / "gate",
            required_source_sha=SHA_A, attested_source_sha=SHA_A,
        )


def test_composition_valid_bounded_recovery_reaches_overall_pass(tmp_path, monkeypatch):
    result = _run_adr0009_composition(tmp_path, monkeypatch, breaker_on_first_failure=False)

    validity = result["execution_validity"]
    assert validity["run1"]["recovered_history_count"] == 1
    assert validity["run1"]["model_invocation_rows"] == 3
    assert validity["run2"]["model_invocation_rows"] == 2
    assert validity["run1"]["model_path_task_count"] == 2
    assert validity["run2"]["model_path_task_count"] == 2
    assert validity["consumption_count_c"] == 5
    assert validity["valid"] is True
    assert not [line for line in result["checks"] if line.endswith("FAIL")]
    assert result["overall_pass"] is True


def test_composition_breaker_refused_audit_alone_flips_overall_to_fail(tmp_path, monkeypatch):
    """The ONLY difference from the passing case is the first FAILED
    row's structured tool audit: same budget subtype, same sdk_is_error,
    same reservation, same misleading prose, same scoring, same costs.
    OVERALL must flip to FAIL, and the failure must come from ADR-0009
    execution validity — never from a relaxed scoring check."""
    result = _run_adr0009_composition(tmp_path, monkeypatch, breaker_on_first_failure=True)

    failing = [line for line in result["checks"] if line.endswith("FAIL")]
    assert failing
    assert all(line.startswith("execution_validity[") for line in failing), failing
    assert result["overall_pass"] is False

    validity = result["execution_validity"]
    assert validity["predicates"]["all_logical_histories_valid"] is False
    recovered = [
        h for h in validity["run1"]["logical_histories"] if len(h["call_ids"]) == 2
    ]
    assert len(recovered) == 1
    assert recovered[0]["invalid_reason"] == gate.INVALID_RECOVERY_TOOL_BREAKER
    # The corroborating fields still all say "budget ceiling" — only the
    # structured containment evidence differs.
    assert validity["run1"]["recovered_history_count"] == 0


# =====================================================================
# ADR-0009 Stage 2 — surfaces this change must NOT have moved.
# V26-V29 (the R1-R26, identity and frozen-scorer suites themselves live
# in tests/test_adr0008.py, tests/test_class_parity.py and the eval
# tests; what is pinned here is that Stage 2 imported none of their
# authority into the runner).
# =====================================================================


def test_stage2_changed_no_adopted_numeric_bound():
    """No cap rises, no retry expands (adr/0009 §1, dispatch §7)."""
    from agents.checker.config import MAX_MODEL_ATTEMPTS_PER_TASK, MAX_TOOL_CALLS_PER_CHECK

    assert MAX_MODEL_ATTEMPTS_PER_TASK == 2
    assert MAX_TOOL_CALLS_PER_CHECK == 5
    assert checker_failures.RETRYABLE_FAILURE_CLASSES == frozenset(
        {checker_failures.SDK_BUDGET_CEILING}
    )
    assert gate.PER_RUN_COST_CAP_EUR_MICROS == ADOPTED_PER_RUN_CAP
    assert gate.GATE_SESSION_COST_CAP_EUR_MICROS == ADOPTED_SESSION_CAP


def test_stage2_scores_live_findings_not_the_tool_attempt_audit(tmp_path):
    """dispatch §6: a failed invocation's tool proposals are audit
    evidence only. The scorer's input is the persisted LIVE findings
    table, and no ADR-0009 code path converts an attempt row into one."""
    conn = ledger.open_ledger(tmp_path / "gate.sqlite3")
    _seed_run(conn, RUN1)
    _seed_budget_ceiling_failure(
        conn, RUN1, attempts=(_attempt(1), _attempt(2, checker_tools.ACCEPTED))
    )
    assert ledger.list_tool_attempts_for_run(conn, RUN1)  # audit rows exist
    assert gate._finding_rows_for_run(conn, RUN1) == []   # zero live findings


def test_v29_no_test_in_this_module_can_reach_a_model_or_the_network():
    """Structural: conftest's autouse block_network guard is active, and
    every run_gate test additionally patches the SDK entry point to
    raise. A real invocation is impossible here, not merely unused."""
    import socket

    with pytest.raises(AssertionError, match="live network access"):
        socket.socket().connect(("127.0.0.1", 1))


# ---------------------------------------------------------------------
# ADR-0007 §5 prospective preflight: fails closed before the
# consumption boundary, on an injected fake git — never the network.
# ---------------------------------------------------------------------


def _fake_git(head=SHA_A, origin_main=None, status=""):
    resolved_origin = head if origin_main is None else origin_main

    def fake(args):
        if args[:1] == ["fetch"]:
            return ""
        if args == ["rev-parse", "HEAD"]:
            return head
        if args == ["rev-parse", "origin/main"]:
            return resolved_origin
        if args == ["status", "--porcelain"]:
            return status
        raise AssertionError(f"unexpected git call: {args}")

    return fake


@pytest.fixture
def preflight_env(monkeypatch, tmp_path):
    def _setup(*, head=SHA_A, origin_main=None, status="", freeze=0):
        monkeypatch.setattr(gate, "_git", _fake_git(head, origin_main, status))
        monkeypatch.setattr(gate, "_freeze_guard_main", lambda: freeze)
        return dict(
            gate_root=tmp_path / "fresh_gate",
            artifacts_dir=tmp_path / "fresh_artifacts",
        )

    return _setup


@pytest.mark.parametrize("bad_sha", ["", "a" * 39, "a" * 41, "A" * 40, "g" * 40, "not-a-sha"])
def test_preflight_rejects_malformed_sha_before_any_git_call(monkeypatch, tmp_path, bad_sha):
    monkeypatch.setattr(gate, "_git", lambda args: pytest.fail("git must not be called"))
    with pytest.raises(gate.PreflightError, match="40-lowercase-hex"):
        gate.run_prospective_preflight(
            require_source_sha=bad_sha,
            gate_root=tmp_path / "g", artifacts_dir=tmp_path / "a",
        )


def test_preflight_rejects_head_not_equal_to_required_sha(preflight_env):
    paths = preflight_env(head=SHA_B)
    with pytest.raises(gate.PreflightError, match="HEAD"):
        gate.run_prospective_preflight(require_source_sha=SHA_A, **paths)


def test_preflight_rejects_origin_main_not_equal_to_head(preflight_env):
    paths = preflight_env(origin_main=SHA_B)
    with pytest.raises(gate.PreflightError, match="origin/main"):
        gate.run_prospective_preflight(require_source_sha=SHA_A, **paths)


def test_preflight_rejects_dirty_tree(preflight_env):
    paths = preflight_env(status=" M STATE.md")
    with pytest.raises(gate.PreflightError, match="not clean"):
        gate.run_prospective_preflight(require_source_sha=SHA_A, **paths)


def test_preflight_rejects_freeze_guard_failure(preflight_env):
    paths = preflight_env(freeze=1)
    with pytest.raises(gate.PreflightError, match="freeze guard"):
        gate.run_prospective_preflight(require_source_sha=SHA_A, **paths)


@pytest.mark.parametrize("forbidden", ["var/phase3_gate", "var/phase3_regate", "artifacts"])
def test_preflight_rejects_historical_default_evidence_paths(preflight_env, tmp_path, forbidden):
    paths = preflight_env()
    paths["gate_root"] = gate.REPO_ROOT / forbidden
    with pytest.raises(gate.PreflightError, match="historical/default"):
        gate.run_prospective_preflight(require_source_sha=SHA_A, **paths)


def test_preflight_rejects_existing_evidence_dir(preflight_env, tmp_path):
    paths = preflight_env()
    paths["artifacts_dir"].mkdir(parents=True)
    with pytest.raises(gate.PreflightError, match="already exists"):
        gate.run_prospective_preflight(require_source_sha=SHA_A, **paths)


def test_preflight_accepts_fresh_explicit_paths_and_captures_verified_head(preflight_env):
    paths = preflight_env()
    result = gate.run_prospective_preflight(require_source_sha=SHA_A, **paths)
    assert result.verified_head_sha == SHA_A
    assert result.evidence_lines


def test_preflight_failure_precedes_the_consumption_boundary(monkeypatch, tmp_path):
    """A preflight failure must exit before run_gate — and therefore
    before any positive-reservation agent_call row can be persisted
    (those are only written inside execute_run, inside run_gate):
    C == 0 by construction."""

    def offline_git(args):
        raise subprocess.CalledProcessError(128, ["git", *args], stderr="fatal: offline")

    monkeypatch.setattr(gate, "_git", offline_git)
    monkeypatch.setattr(
        gate, "run_gate",
        lambda **kw: pytest.fail("consumption boundary crossed"),
    )
    code = gate.main([
        "--judgment-mode", "agent", "--require-source-sha", SHA_A,
        "--gate-root", str(tmp_path / "g"), "--artifacts-dir", str(tmp_path / "a"),
    ])
    assert code == 2


def test_main_records_preflight_verified_sha_as_authoritative_source(monkeypatch, tmp_path):
    """The artifact's source_commit is the SHA the preflight actually
    verified — source_commit == required_source_sha ==
    attested_source_sha — never a later re-read of HEAD."""
    head = "c" * 40
    monkeypatch.setattr(gate, "_git", _fake_git(head=head))
    monkeypatch.setattr(gate, "_freeze_guard_main", lambda: 0)

    def fake_run_gate(**kwargs):
        return {
            "overall_pass": False, "checks": [],
            "required_source_sha": kwargs["required_source_sha"],
            "attested_source_sha": kwargs["attested_source_sha"],
        }

    monkeypatch.setattr(gate, "run_gate", fake_run_gate)
    code = gate.main([
        "--judgment-mode", "agent", "--require-source-sha", head,
        "--gate-root", str(tmp_path / "g"), "--artifacts-dir", str(tmp_path / "a"),
    ])
    assert code == 1
    artifact = json.loads((tmp_path / "a" / "phase3_dev_gate.json").read_text(encoding="utf-8"))
    assert artifact["source_commit"] == head
    assert artifact["required_source_sha"] == head
    assert artifact["attested_source_sha"] == head
    assert artifact["preflight"]


# ---------------------------------------------------------------------
# CLI contract and the no-deletion guarantee.
# ---------------------------------------------------------------------


def test_agent_mode_requires_require_source_sha(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        gate.main([
            "--judgment-mode", "agent",
            "--gate-root", str(tmp_path / "g"), "--artifacts-dir", str(tmp_path / "a"),
        ])
    assert excinfo.value.code == 2


def test_stub_mode_rejects_require_source_sha(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        gate.main([
            "--judgment-mode", "stub", "--require-source-sha", SHA_A,
            "--gate-root", str(tmp_path / "g"), "--artifacts-dir", str(tmp_path / "a"),
        ])
    assert excinfo.value.code == 2


@pytest.mark.parametrize("missing", ["--gate-root", "--artifacts-dir"])
def test_evidence_paths_are_required_arguments(tmp_path, missing):
    argv = ["--judgment-mode", "stub"]
    if missing != "--gate-root":
        argv += ["--gate-root", str(tmp_path / "g")]
    if missing != "--artifacts-dir":
        argv += ["--artifacts-dir", str(tmp_path / "a")]
    with pytest.raises(SystemExit) as excinfo:
        gate.main(argv)
    assert excinfo.value.code == 2


def test_run_gate_never_deletes_existing_evidence(tmp_path):
    root = tmp_path / "existing_gate"
    root.mkdir()
    evidence = root / "gate.sqlite3"
    evidence.write_text("prior evidence", encoding="utf-8")
    with pytest.raises(gate.PreflightError, match="already exists"):
        gate.run_gate(judgment_mode="stub", gate_root=root)
    assert evidence.read_text(encoding="utf-8") == "prior evidence"


def test_run_gate_refuses_historical_default_gate_root():
    with pytest.raises(gate.PreflightError, match="historical/default"):
        gate.run_gate(judgment_mode="stub", gate_root=gate.REPO_ROOT / "var" / "phase3_gate")

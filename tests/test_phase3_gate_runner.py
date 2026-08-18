"""Phase-3 gate-runner lifecycle tests (adr/0005-phase3-gate-remediation.md).

Covers exactly what the remediation changed in
``scripts/run_phase3_dev_gate.py``: one independent
``RunBudgetCoordinator`` per designated run ID (replacing the single
shared coordinator that made run 2 vacuous), and the runner's own
independent cost cross-check restated as EUR 0.75 per run / EUR 1.50
per two-run gate session.

Nothing here runs the gate. No test in this module makes a model call,
touches the network, or requires OAuth: ``conftest.py``'s autouse
``block_network`` fixture fails any test that reaches a real socket,
``claude_agent_sdk.query`` is patched to raise wherever a checker stub
is constructed, and the one test that exercises ``run_gate`` replaces
``execute_run`` with a local fake. The frozen fixture corpus and the
scoring contract are not touched.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agents.checker.budget import BudgetExhausted, RunBudgetCoordinator
from agents.checker.fx import FxRate
from agents.checker.harness import CagedCheckerStub
from contracts.schemas import RunRecord
from sentinel import ledger

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
    run row so run_gate's own post-run reads succeed, and makes no
    model call of any kind."""
    conn = ledger.open_ledger(config.db_path)
    try:
        with ledger.unit_of_work(conn):
            ledger.insert_run(
                conn,
                RunRecord(
                    schema_version=1, run_id=config.run_id, run_kind="dev",
                    status="RUNNING", started_at_utc=T0, tasks_created=1,
                    tasks_terminal=1, findings_new=0, findings_still_open=0,
                    findings_resolved=0,
                ),
            )
    finally:
        conn.close()
    return SimpleNamespace(
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

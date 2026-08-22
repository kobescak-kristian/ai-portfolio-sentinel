"""ADR-0010 bounded-loop supervisor: model-free unit and recovery tests.

**Model-free by construction.** Nothing in this file constructs an SDK
client, resolves a live FX rate, or reaches a provider. The allowance
tests use a deterministic fake ``FxRate`` and assert only that the
reduced figure reaches ``RunBudgetCoordinator`` — the downward
budget-construction seam. They prove propagation; they do NOT prove that
a provider-capable loop works, and no such claim is made here or
anywhere else in this session.

Two structural choices are worth naming, because they are what makes
these tests evidence rather than decoration:

* The store under test is the REAL SQLite store on a real ledger file,
  not a dictionary. The section-4 invariant is a claim about committed
  database state, so a fake store would prove nothing about it. The
  durable-intent ordering test reads the intent back through a SEPARATE
  connection from inside the iteration callable — the only way to show
  the commit really happened before the work started.
* ``FakeDomain`` is a self-consistent stand-in for the run world: it
  writes real ``runs`` rows and real ``CostRow``s and answers probes and
  cost queries from that durable state. It is not a mock with scripted
  returns, so "the terminal run was adopted rather than repeated" is
  observable as a fact about persisted data plus a call log — and the
  ``bound_run_id`` foreign key is genuinely exercised rather than
  side-stepped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

import pytest

from runner.breakers import (
    COMPLETED_ITERATION_CAP,
    CONSECUTIVE_FAILURE_BREAKER_TRIPPED,
    COST_BREAKER_TRIPPED,
    LOOP_ABORTED_ERROR,
    LOOP_BUDGET_EUR_MICROS,
    InvalidIterationLimit,
)
from runner.loop import (
    FINALIZED,
    INTENT,
    IterationRow,
    LoopConfig,
    LoopHooks,
    LoopSummary,
    RunProbe,
    derive_consecutive_failures,
    run_loop,
)
from runner.sentinel_adapter import durable_accounted_cost
from runner.state import LoopStateError, SqliteLoopStateStore, open_loop_state
from contracts.schemas import CostRow, RunRecord
from sentinel import ledger
from sentinel.ids import FrozenClock
from sentinel.logs import RunLogger
from telemetry.cost_ledger import append_cost_row, read_cost_rows
from tests.conftest import SimulatedCrash, T0

PER_RUN_CAP = 750_000


# --- doubles ----------------------------------------------------------------


class SeededRunIds:
    """Deterministic planned_run_ids so assertions can name them."""

    def __init__(self, prefix: str = "r-loop"):
        self.prefix = prefix
        self.issued: list[str] = []

    def new_run_id(self) -> str:
        value = f"{self.prefix}-{len(self.issued):03d}"
        self.issued.append(value)
        return value


class RecordingLogger:
    """Writes through the REAL RunLogger — so every loop event must be
    in the closed vocabulary or the write raises — and records what was
    logged so severity can be asserted."""

    def __init__(self, path: Path):
        self._inner = RunLogger(path)
        self.records: list[tuple[str, str, dict]] = []

    def log(self, severity, event, *, now, **fields):
        self._inner.log(severity, event, now=now, **fields)
        self.records.append((severity, event, fields))

    def events(self) -> list[str]:
        return [event for _sev, event, _f in self.records]

    def severity_of(self, event: str) -> list[str]:
        return [sev for sev, name, _f in self.records if name == event]

    def close(self) -> None:
        self._inner.close()


def seed_run(conn, run_id: str, status: str) -> None:
    """Create a REAL ``runs`` row. The loop's ``bound_run_id`` carries a
    foreign key to ``runs(run_id)``, so a dictionary stand-in would not
    exercise the integrity the schema actually enforces — the fake
    domain therefore writes genuine run rows, and 'the iteration bound
    to a run that exists' is checked by SQLite rather than asserted."""
    with ledger.unit_of_work(conn):
        ledger.insert_run(
            conn,
            RunRecord(
                schema_version=1,
                run_id=run_id,
                run_kind="dev",
                status="RUNNING",
                started_at_utc=T0,
                tasks_created=0,
                tasks_terminal=0,
                findings_new=0,
                findings_still_open=0,
                findings_resolved=0,
            ),
        )
        if status != "RUNNING":
            ledger.close_run(
                conn,
                run_id,
                status=status,
                finished_at_utc=T0 + timedelta(hours=1),
                counts=ledger.RunCounts(0, 0, 0, 0),
            )


def write_cost_row(path: Path, run_id: str, micros: int) -> None:
    """A REAL durable CostRow — ADR-0010 section 2's accounting source."""
    append_cost_row(
        path,
        CostRow(
            schema_version=1,
            run_id=run_id,
            recorded_at_utc=T0,
            run_kind="dev",
            model="none-deterministic",
            input_tokens=0,
            output_tokens=0,
            cost_eur_micros=micros,
        ),
    )


@dataclass
class FakeDomain:
    """A self-consistent stand-in for the Sentinel run world.

    Not a mock with scripted returns: it writes real ``runs`` rows and
    real ``CostRow``s, and answers probes and cost queries from that
    durable state. "The terminal run was adopted rather than repeated"
    is therefore observable as a fact about persisted data plus a call
    log, not as a stubbed return value."""

    conn: object = None
    cost_ledger_path: Optional[Path] = None
    statuses: list[str] = field(default_factory=list)
    costs: list[int] = field(default_factory=list)
    outputs_done: set = field(default_factory=set)
    execute_calls: list[str] = field(default_factory=list)
    allowances: list[int] = field(default_factory=list)
    recover_calls: list[str] = field(default_factory=list)
    reconcile_calls: list[str] = field(default_factory=list)
    raise_on_execute: Optional[BaseException] = None

    def _next(self, seq, default):
        index = len(self.execute_calls)
        if index < len(seq):
            return seq[index]
        return seq[-1] if seq else default

    def seed(self, run_id: str, status: str, *, cost: int = 0, outputs: bool = True):
        """Pre-existing durable run state, as a resumed loop would find."""
        seed_run(self.conn, run_id, status)
        if status != "RUNNING":
            write_cost_row(self.cost_ledger_path, run_id, cost)
            if outputs:
                self.outputs_done.add(run_id)
        return self

    def probe(self, planned_run_id: str) -> RunProbe:
        run = ledger.get_run(self.conn, planned_run_id)
        if run is None:
            return RunProbe(status=None, outputs_complete=False)
        if run.status == "RUNNING":
            return RunProbe(status="RUNNING", outputs_complete=False)
        return RunProbe(
            status=run.status, outputs_complete=planned_run_id in self.outputs_done
        )

    def execute(self, planned_run_id: str, *, allowance_eur_micros: int) -> str:
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        status = self._next(self.statuses, "COMPLETED")
        cost = self._next(self.costs, 0)
        self.execute_calls.append(planned_run_id)
        self.allowances.append(allowance_eur_micros)
        seed_run(self.conn, planned_run_id, status)
        write_cost_row(self.cost_ledger_path, planned_run_id, cost)
        self.outputs_done.add(planned_run_id)
        return status

    def recover_interrupted(self, planned_run_id: str) -> str:
        self.recover_calls.append(planned_run_id)
        with ledger.unit_of_work(self.conn):
            ledger.close_run(
                self.conn,
                planned_run_id,
                status="FAILED",
                finished_at_utc=T0 + timedelta(hours=2),
                counts=ledger.RunCounts(0, 0, 0, 0),
            )
        write_cost_row(self.cost_ledger_path, planned_run_id, 0)
        self.outputs_done.add(planned_run_id)
        return "FAILED"

    def reconcile_outputs(self, planned_run_id: str) -> None:
        self.reconcile_calls.append(planned_run_id)
        self.outputs_done.add(planned_run_id)

    def accounted_cost(self, run_ids: Sequence[str]) -> int:
        return durable_accounted_cost(self.cost_ledger_path, run_ids)


@pytest.fixture
def loop_env(tmp_path):
    """A real ledger, a real store, a real cost ledger, and a logger
    that validates every event against the closed vocabulary."""
    conn = open_loop_state(tmp_path / "loop.sqlite3")
    logger = RecordingLogger(tmp_path / "loop.jsonl")
    ticks = [T0 + timedelta(seconds=i) for i in range(400)]
    yield {
        "conn": conn,
        "db_path": tmp_path / "loop.sqlite3",
        "cost_ledger_path": tmp_path / "cost_ledger.jsonl",
        "store": SqliteLoopStateStore(conn),
        "logger": logger,
        "clock": FrozenClock(ticks=ticks),
        "tmp_path": tmp_path,
    }
    logger.close()
    conn.close()


@pytest.fixture
def domain_factory(loop_env):
    def _make(**kwargs) -> FakeDomain:
        return FakeDomain(
            conn=loop_env["conn"],
            cost_ledger_path=loop_env["cost_ledger_path"],
            **kwargs,
        )

    return _make


def config(n: int = 3, *, loop_id: str = "loop-001", ceiling: int = LOOP_BUDGET_EUR_MICROS):
    return LoopConfig(
        loop_id=loop_id,
        max_iterations=n,
        per_run_cap_eur_micros=PER_RUN_CAP,
        loop_budget_eur_micros=ceiling,
    )


def drive(env, cfg, domain, *, ids=None, hooks=LoopHooks()):
    return run_loop(
        cfg,
        store=env["store"],
        executor=domain,
        clock=env["clock"],
        ids=ids or SeededRunIds(),
        logger=env["logger"],
        hooks=hooks,
    )


# --- normal completion ------------------------------------------------------


def test_normal_loop_completes_exactly_n_iterations(loop_env, domain_factory):
    domain = domain_factory(statuses=["COMPLETED"], costs=[1_000])
    outcome = drive(loop_env, config(n=3), domain)

    assert outcome.stop_reason == COMPLETED_ITERATION_CAP
    assert outcome.exit_code == 0
    assert outcome.iterations_completed == 3
    assert len(domain.execute_calls) == 3


def test_no_duplicate_and_no_skipped_iteration(loop_env, domain_factory):
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    drive(loop_env, config(n=5), domain)

    rows = loop_env["store"].list_iterations("loop-001")
    assert [r.iteration_index for r in rows] == [0, 1, 2, 3, 4]
    assert all(r.iteration_state == FINALIZED for r in rows)
    # One planned_run_id per iteration, each executed exactly once.
    assert len(set(domain.execute_calls)) == len(domain.execute_calls) == 5
    assert {r.planned_run_id for r in rows} == set(domain.execute_calls)


def test_loop_logs_started_and_completed_from_the_closed_vocabulary(
    loop_env, domain_factory
):
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    drive(loop_env, config(n=1), domain)

    events = loop_env["logger"].events()
    assert "loop.started" in events
    assert "loop.iteration_intent" in events
    assert "loop.iteration_finalized" in events
    assert "loop.completed" in events


# --- section 12: N validated before any durable work ------------------------


@pytest.mark.parametrize("n", [0, -1, 11, 50])
def test_invalid_n_refused_before_any_loop_or_iteration_intent(
    loop_env, domain_factory, n
):
    domain = domain_factory(statuses=["COMPLETED"])
    with pytest.raises(InvalidIterationLimit):
        drive(loop_env, config(n=n), domain)

    # Nothing durable, and no work at all.
    assert loop_env["store"].loop_record("loop-001") is None
    assert loop_env["store"].list_iterations("loop-001") == []
    assert domain.execute_calls == []


# --- section 4: durable intent before work ----------------------------------


def test_iteration_callable_is_unreachable_before_the_intent_is_committed(
    loop_env, domain_factory
):
    """The strongest available proof: the iteration callable opens its
    OWN connection to the ledger and reads the INTENT row back. If the
    supervisor executed before committing, this read would find nothing
    and the assertion inside the callable would fail the test."""
    db_path = loop_env["db_path"]
    observed: list[str] = []

    class IntentCheckingDomain(FakeDomain):
        def execute(self, planned_run_id, *, allowance_eur_micros):
            other_conn = open_loop_state(db_path)
            try:
                rows = SqliteLoopStateStore(other_conn).list_iterations("loop-001")
            finally:
                other_conn.close()
            match = [r for r in rows if r.planned_run_id == planned_run_id]
            assert match, (
                "iteration callable was reached before its durable intent commit"
            )
            observed.append(match[0].iteration_state)
            return super().execute(
                planned_run_id, allowance_eur_micros=allowance_eur_micros
            )

    domain = IntentCheckingDomain(
        conn=loop_env["conn"],
        cost_ledger_path=loop_env["cost_ledger_path"],
        statuses=["COMPLETED"],
        costs=[0],
    )
    drive(loop_env, config(n=2), domain)
    # Visible from another connection, and still INTENT at that moment —
    # finalization has deliberately not happened yet.
    assert observed == [INTENT, INTENT]


def test_planned_run_id_is_the_run_id_handed_to_the_iteration(loop_env, domain_factory):
    ids = SeededRunIds()
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    drive(loop_env, config(n=3), domain, ids=ids)

    rows = loop_env["store"].list_iterations("loop-001")
    assert [r.planned_run_id for r in rows] == ids.issued
    assert domain.execute_calls == ids.issued


def test_a_second_intent_for_the_same_iteration_is_refused_by_the_database(loop_env):
    import sqlite3

    store = loop_env["store"]
    store.begin_or_load_loop(
        loop_id="loop-001",
        max_iterations=3,
        loop_budget_eur_micros=LOOP_BUDGET_EUR_MICROS,
        failure_threshold=3,
        now=T0,
    )
    store.record_intent(
        loop_id="loop-001", iteration_index=0, planned_run_id="r-a", now=T0
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.record_intent(
            loop_id="loop-001", iteration_index=0, planned_run_id="r-b", now=T0
        )
    # And the same planned_run_id may not be reused for another index.
    with pytest.raises(sqlite3.IntegrityError):
        store.record_intent(
            loop_id="loop-001", iteration_index=1, planned_run_id="r-a", now=T0
        )


def test_an_iteration_finalizes_exactly_once(loop_env, domain_factory):
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    drive(loop_env, config(n=1), domain)

    store = loop_env["store"]
    row = store.list_iterations("loop-001")[0]
    with pytest.raises(LoopStateError):
        store.finalize_iteration(
            loop_id="loop-001",
            iteration_index=0,
            bound_run_id=row.planned_run_id,
            run_status="COMPLETED",
            accounted_cost_eur_micros=0,
            consecutive_failures_after=0,
            summary=LoopSummary(1, 1, 0, 0),
            now=T0,
        )


# --- section 1: the failure streak ------------------------------------------


def test_failed_iterations_increment_the_streak_and_trip_at_exactly_three(
    loop_env, domain_factory
):
    domain = domain_factory(statuses=["FAILED"], costs=[0])
    outcome = drive(loop_env, config(n=10), domain)

    assert outcome.stop_reason == CONSECUTIVE_FAILURE_BREAKER_TRIPPED
    assert outcome.exit_code != 0
    assert outcome.consecutive_failures == 3
    # The breaker refuses the NEXT iteration; it does not abort one in
    # progress, so exactly three runs happened.
    assert len(domain.execute_calls) == 3


def test_a_completed_iteration_resets_the_streak(loop_env, domain_factory):
    domain = domain_factory(
        statuses=["FAILED", "FAILED", "COMPLETED", "FAILED", "FAILED"], costs=[0]
    )
    outcome = drive(loop_env, config(n=5), domain)

    assert outcome.stop_reason == COMPLETED_ITERATION_CAP
    assert outcome.exit_code == 0
    assert outcome.consecutive_failures == 2
    assert len(domain.execute_calls) == 5


def test_streak_is_reconstructed_from_durable_rows_not_memory():
    rows = [
        IterationRow("l", 0, "r0", FINALIZED, "r0", "COMPLETED"),
        IterationRow("l", 1, "r1", FINALIZED, "r1", "FAILED"),
        IterationRow("l", 2, "r2", FINALIZED, "r2", "FAILED"),
    ]
    assert derive_consecutive_failures(rows) == 2
    assert derive_consecutive_failures(rows[:1]) == 0
    assert derive_consecutive_failures([]) == 0


def test_terminal_boundary_n_reached_with_streak_three_is_the_failure_breaker(
    loop_env, domain_factory
):
    """ADR-0010 section 7 leg 3's terminal-boundary case: reaching N
    must NOT be reported as normal completion when the streak reached
    the threshold on the same iteration."""
    domain = domain_factory(statuses=["FAILED"], costs=[0])
    outcome = drive(loop_env, config(n=3), domain)

    assert outcome.iterations_completed == 3
    assert outcome.stop_reason == CONSECUTIVE_FAILURE_BREAKER_TRIPPED
    assert outcome.exit_code != 0


# --- section 2/3: cost boundaries -------------------------------------------


def test_mid_loop_749999_does_not_trip_cost_alone(loop_env, domain_factory):
    domain = domain_factory(statuses=["COMPLETED"], costs=[749_999, 0, 0])
    outcome = drive(loop_env, config(n=3), domain)

    assert outcome.stop_reason == COMPLETED_ITERATION_CAP
    assert outcome.accounted_cost_eur_micros == 749_999
    assert len(domain.execute_calls) == 3


def test_mid_loop_exactly_at_the_ceiling_refuses_the_next_iteration(
    loop_env, domain_factory
):
    domain = domain_factory(statuses=["COMPLETED"], costs=[750_000, 0])
    outcome = drive(loop_env, config(n=5), domain)

    assert outcome.stop_reason == COST_BREAKER_TRIPPED
    assert outcome.exit_code != 0
    assert outcome.iterations_completed == 1
    # The refusal proves NO next underlying run starts (leg 2d).
    assert len(domain.execute_calls) == 1


def test_overshoot_is_accounted_in_full_and_never_clamped(loop_env, domain_factory):
    domain = domain_factory(statuses=["COMPLETED"], costs=[900_000])
    outcome = drive(loop_env, config(n=5), domain)

    assert outcome.stop_reason == COST_BREAKER_TRIPPED
    assert outcome.accounted_cost_eur_micros == 900_000  # not clamped to 750_000
    assert len(domain.execute_calls) == 1
    record = loop_env["store"].loop_record("loop-001")
    assert record["accounted_cost_eur_micros"] == 900_000


def test_n_reached_at_exactly_the_ceiling_completes_normally(loop_env, domain_factory):
    domain = domain_factory(statuses=["COMPLETED"], costs=[375_000, 375_000])
    outcome = drive(loop_env, config(n=2), domain)

    assert outcome.accounted_cost_eur_micros == 750_000
    assert outcome.stop_reason == COMPLETED_ITERATION_CAP
    assert outcome.exit_code == 0


def test_n_reached_above_the_ceiling_trips_the_cost_breaker(loop_env, domain_factory):
    domain = domain_factory(statuses=["COMPLETED"], costs=[375_000, 375_001])
    outcome = drive(loop_env, config(n=2), domain)

    assert outcome.accounted_cost_eur_micros == 750_001
    assert outcome.stop_reason == COST_BREAKER_TRIPPED
    assert outcome.exit_code != 0


def test_reduced_allowance_reaches_each_iteration_and_is_never_restored(
    loop_env, domain_factory
):
    domain = domain_factory(statuses=["COMPLETED"], costs=[500_000, 100_000, 0])
    drive(loop_env, config(n=3), domain)

    # min(per_run_cap, remaining) at each start: 750k, then 250k, then 150k.
    assert domain.allowances == [750_000, 250_000, 150_000]
    assert domain.allowances[1] < PER_RUN_CAP
    assert all(a <= PER_RUN_CAP for a in domain.allowances)


def test_cost_breaker_logs_an_error_severity_event(loop_env, domain_factory):
    domain = domain_factory(statuses=["COMPLETED"], costs=[900_000])
    drive(loop_env, config(n=5), domain)

    assert loop_env["logger"].severity_of("breaker.cost_tripped") == ["ERROR"]


def test_consecutive_failure_breaker_logs_an_error_severity_event(
    loop_env, domain_factory
):
    domain = domain_factory(statuses=["FAILED"], costs=[0])
    drive(loop_env, config(n=10), domain)

    assert loop_env["logger"].severity_of("breaker.consecutive_failure_tripped") == [
        "ERROR"
    ]


# --- section 6: stop reasons and exit codes ---------------------------------


def test_unexpected_supervisor_error_fails_closed_to_loop_aborted_error(
    loop_env, domain_factory
):
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    domain.raise_on_execute = RuntimeError("iteration blew up")
    outcome = drive(loop_env, config(n=2), domain)

    assert outcome.stop_reason == LOOP_ABORTED_ERROR
    assert outcome.exit_code != 0
    record = loop_env["store"].loop_record("loop-001")
    assert record["status"] == "FINISHED"
    assert record["stop_reason"] == LOOP_ABORTED_ERROR
    assert "loop.failed" in loop_env["logger"].events()


def test_exactly_one_terminal_stop_reason_per_loop(loop_env, domain_factory):
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    drive(loop_env, config(n=1), domain)

    record = loop_env["store"].loop_record("loop-001")
    assert record["stop_reason"] == COMPLETED_ITERATION_CAP
    # A finished loop is never reopened, so no second reason can land.
    with pytest.raises(LoopStateError):
        drive(loop_env, config(n=1), domain_factory(statuses=["COMPLETED"]))


def test_a_loop_cannot_be_resumed_under_different_bounds(loop_env, domain_factory):
    """No back-door raise of the loop ceiling through a resume."""
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    loop_env["store"].begin_or_load_loop(
        loop_id="loop-001",
        max_iterations=2,
        loop_budget_eur_micros=LOOP_BUDGET_EUR_MICROS,
        failure_threshold=3,
        now=T0,
    )
    with pytest.raises(LoopStateError):
        drive(loop_env, config(n=2, ceiling=5_000_000), domain)
    assert domain.execute_calls == []


# --- section 4: recovery A-D ------------------------------------------------


def _seed_intent(env, planned_run_id="r-pre", *, n=3, index=0):
    env["store"].begin_or_load_loop(
        loop_id="loop-001",
        max_iterations=n,
        loop_budget_eur_micros=LOOP_BUDGET_EUR_MICROS,
        failure_threshold=3,
        now=T0,
    )
    env["store"].record_intent(
        loop_id="loop-001", iteration_index=index, planned_run_id=planned_run_id, now=T0
    )


def test_recovery_a_no_run_row_starts_once_with_the_same_planned_run_id(
    loop_env, domain_factory
):
    _seed_intent(loop_env, "r-pre", n=2)
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    outcome = drive(loop_env, config(n=2), domain)

    assert domain.execute_calls[0] == "r-pre"
    assert domain.execute_calls.count("r-pre") == 1
    assert outcome.stop_reason == COMPLETED_ITERATION_CAP
    assert "loop.recovered" in loop_env["logger"].events()


def test_recovery_b_terminal_run_is_adopted_and_never_re_invoked(
    loop_env, domain_factory
):
    _seed_intent(loop_env, "r-pre", n=2)
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    domain.seed("r-pre", "COMPLETED", cost=12_345, outputs=True)

    outcome = drive(loop_env, config(n=2), domain)

    assert "r-pre" not in domain.execute_calls  # never re-invoked
    assert domain.reconcile_calls == []  # outputs were already complete
    rows = loop_env["store"].list_iterations("loop-001")
    assert rows[0].run_status == "COMPLETED"
    assert rows[0].bound_run_id == "r-pre"
    assert outcome.accounted_cost_eur_micros == 12_345


def test_recovery_c_running_run_uses_interrupted_run_recovery(loop_env, domain_factory):
    _seed_intent(loop_env, "r-pre", n=2)
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    domain.seed("r-pre", "RUNNING")

    drive(loop_env, config(n=2), domain)

    assert domain.recover_calls == ["r-pre"]
    assert "r-pre" not in domain.execute_calls  # no replacement run invented
    rows = loop_env["store"].list_iterations("loop-001")
    assert rows[0].run_status == "FAILED"  # the recovered terminal run IS the result
    assert rows[0].bound_run_id == "r-pre"


def test_recovery_d_incomplete_outputs_are_reconciled_without_a_rerun(
    loop_env, domain_factory
):
    _seed_intent(loop_env, "r-pre", n=2)
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    # Terminal run, but its derived outputs are deliberately NOT complete.
    domain.seed("r-pre", "COMPLETED", cost=7_000, outputs=False)

    drive(loop_env, config(n=2), domain)

    assert domain.reconcile_calls == ["r-pre"]
    assert "r-pre" not in domain.execute_calls  # no rerun
    rows = loop_env["store"].list_iterations("loop-001")
    assert rows[0].run_status == "COMPLETED"


def test_earlier_unfinished_iterations_are_reconciled_before_any_later_new_one(
    loop_env, domain_factory
):
    _seed_intent(loop_env, "r-pre", n=3)
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    domain.seed("r-pre", "COMPLETED", outputs=True)

    drive(loop_env, config(n=3), domain)

    rows = loop_env["store"].list_iterations("loop-001")
    assert [r.iteration_index for r in rows] == [0, 1, 2]
    assert rows[0].bound_run_id == "r-pre"
    # Index 0 was adopted first; only then were 1 and 2 executed.
    assert domain.execute_calls == [rows[1].planned_run_id, rows[2].planned_run_id]


def test_recovery_a_still_respects_the_hard_cost_ceiling(loop_env, domain_factory):
    """An intent already committed is an iteration in progress, so the
    failure breaker does not abort it — but the loop ceiling is a real
    pre-start gate and no run has started yet."""
    store = loop_env["store"]
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    domain.seed("r-0", "COMPLETED", cost=750_000, outputs=True)

    store.begin_or_load_loop(
        loop_id="loop-001",
        max_iterations=3,
        loop_budget_eur_micros=LOOP_BUDGET_EUR_MICROS,
        failure_threshold=3,
        now=T0,
    )
    store.record_intent(
        loop_id="loop-001", iteration_index=0, planned_run_id="r-0", now=T0
    )
    store.finalize_iteration(
        loop_id="loop-001",
        iteration_index=0,
        bound_run_id="r-0",
        run_status="COMPLETED",
        accounted_cost_eur_micros=750_000,
        consecutive_failures_after=0,
        summary=LoopSummary(1, 1, 0, 750_000),
        now=T0,
    )
    store.record_intent(
        loop_id="loop-001", iteration_index=1, planned_run_id="r-1", now=T0
    )

    outcome = drive(loop_env, config(n=3), domain)

    assert outcome.stop_reason == COST_BREAKER_TRIPPED
    assert domain.execute_calls == []  # the pending iteration never started


# --- section 7 leg 4: the crash seam ----------------------------------------


def test_crash_after_terminal_run_before_finalize_adopts_and_never_repeats(
    loop_env, domain_factory
):
    """The primary injected seam: the underlying run is terminal and
    durable, but loop finalization has not committed. After restart the
    loop must reuse the same loop_id, the same iteration index and the
    same planned_run_id, adopt the existing run, and complete
    deterministically — with no duplicate run and no skipped iteration."""
    domain = domain_factory(statuses=["COMPLETED"], costs=[1_000])
    ids = SeededRunIds()

    crashed_at: list[tuple[int, str]] = []

    def crash(_loop_id, index, planned_run_id):
        if index == 1 and not crashed_at:
            crashed_at.append((index, planned_run_id))
            raise SimulatedCrash("crash after run terminal, before loop finalize")

    with pytest.raises(SimulatedCrash):
        drive(
            loop_env,
            config(n=3),
            domain,
            ids=ids,
            hooks=LoopHooks(after_run_terminal_before_finalize=crash),
        )

    index, crashed_run_id = crashed_at[0]
    rows = loop_env["store"].list_iterations("loop-001")
    assert rows[index].iteration_state == INTENT  # finalization did not commit
    assert rows[index].planned_run_id == crashed_run_id
    # The underlying run IS terminal and durable at the seam.
    assert ledger.get_run(loop_env["conn"], crashed_run_id).status == "COMPLETED"
    executed_before = list(domain.execute_calls)

    # Restart. Same loop_id, same store, same durable domain state, and
    # a DIFFERENT id factory — so a re-minted planned_run_id would be
    # immediately visible rather than coincidentally identical.
    outcome = drive(loop_env, config(n=3), domain, ids=SeededRunIds(prefix="r-restart"))

    assert outcome.stop_reason == COMPLETED_ITERATION_CAP
    assert outcome.exit_code == 0
    rows = loop_env["store"].list_iterations("loop-001")
    assert [r.iteration_index for r in rows] == [0, 1, 2]
    assert all(r.iteration_state == FINALIZED for r in rows)
    # Same planned_run_id, adopted rather than repeated.
    assert rows[index].planned_run_id == crashed_run_id
    assert rows[index].bound_run_id == crashed_run_id
    assert domain.execute_calls.count(crashed_run_id) == executed_before.count(
        crashed_run_id
    )
    assert len(set(domain.execute_calls)) == len(domain.execute_calls)


def test_crash_after_intent_before_run_start_reuses_the_same_planned_run_id(
    loop_env, domain_factory
):
    """Dispatch section 6: a crash after the intent commit but before
    the run starts must NOT generate a second planned_run_id. On
    restart this is recovery case A — the run starts once, with the id
    that was already committed."""
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])

    def crash(_loop_id, _index, _planned_run_id):
        raise SimulatedCrash("crash after intent, before run start")

    with pytest.raises(SimulatedCrash):
        drive(
            loop_env,
            config(n=2),
            domain,
            ids=SeededRunIds(),
            hooks=LoopHooks(after_iteration_intent=crash),
        )

    rows = loop_env["store"].list_iterations("loop-001")
    assert len(rows) == 1
    committed_id = rows[0].planned_run_id
    assert rows[0].iteration_state == INTENT
    assert domain.execute_calls == []  # no run ever started

    # Restart with a DIFFERENT id factory: a freshly minted id would be
    # immediately visible instead of coincidentally matching.
    outcome = drive(loop_env, config(n=2), domain, ids=SeededRunIds(prefix="r-second"))

    assert outcome.stop_reason == COMPLETED_ITERATION_CAP
    rows = loop_env["store"].list_iterations("loop-001")
    assert rows[0].planned_run_id == committed_id
    assert domain.execute_calls[0] == committed_id
    assert domain.execute_calls.count(committed_id) == 1
    assert len(set(domain.execute_calls)) == len(domain.execute_calls)


def test_a_finished_loop_cannot_be_finished_twice(loop_env):
    store = loop_env["store"]
    store.begin_or_load_loop(
        loop_id="loop-001",
        max_iterations=1,
        loop_budget_eur_micros=LOOP_BUDGET_EUR_MICROS,
        failure_threshold=3,
        now=T0,
    )
    summary = LoopSummary(0, 0, 0, 0)
    store.finish_loop(
        loop_id="loop-001",
        stop_reason=COMPLETED_ITERATION_CAP,
        summary=summary,
        now=T0,
    )
    with pytest.raises(LoopStateError):
        store.finish_loop(
            loop_id="loop-001",
            stop_reason=COST_BREAKER_TRIPPED,
            summary=summary,
            now=T0,
        )


def test_a_failing_cost_read_never_hides_the_stop_reason(loop_env, domain_factory):
    """Terminal state must still be written with exactly one reason even
    if the final cost read blows up — otherwise a loop could end with no
    authoritative stop reason at all."""
    domain = domain_factory(statuses=["COMPLETED"], costs=[0])
    calls = {"n": 0}
    real_accounted = domain.accounted_cost

    def flaky(run_ids):
        calls["n"] += 1
        if calls["n"] > 3:
            raise RuntimeError("cost ledger unreadable")
        return real_accounted(run_ids)

    domain.accounted_cost = flaky
    outcome = drive(loop_env, config(n=1), domain)

    assert outcome.stop_reason in {COMPLETED_ITERATION_CAP, LOOP_ABORTED_ERROR}
    record = loop_env["store"].loop_record("loop-001")
    assert record["status"] == "FINISHED"
    assert record["stop_reason"] == outcome.stop_reason


# --- persistence reconstruction ---------------------------------------------


def test_loop_summary_is_persisted_and_matches_the_iteration_rows(
    loop_env, domain_factory
):
    domain = domain_factory(
        statuses=["COMPLETED", "FAILED", "COMPLETED"], costs=[10, 20, 30]
    )
    outcome = drive(loop_env, config(n=3), domain)

    record = loop_env["store"].loop_record("loop-001")
    assert record["status"] == "FINISHED"
    assert record["stop_reason"] == COMPLETED_ITERATION_CAP
    assert record["iterations_started"] == 3
    assert record["iterations_completed"] == 3
    assert record["consecutive_failures"] == 0
    assert record["accounted_cost_eur_micros"] == 60 == outcome.accounted_cost_eur_micros
    assert record["max_iterations"] == 3
    assert record["loop_budget_eur_micros"] == LOOP_BUDGET_EUR_MICROS
    assert record["failure_threshold"] == 3

    rows = loop_env["store"].list_iterations("loop-001")
    assert [r.run_status for r in rows] == ["COMPLETED", "FAILED", "COMPLETED"]


def test_iteration_rows_record_the_streak_at_each_step(loop_env, domain_factory):
    domain = domain_factory(statuses=["FAILED", "FAILED", "COMPLETED"], costs=[0])
    drive(loop_env, config(n=3), domain)

    streaks = [
        row[0]
        for row in loop_env["conn"]
        .execute(
            "SELECT consecutive_failures_after FROM loop_iterations "
            "WHERE loop_id = 'loop-001' ORDER BY iteration_index"
        )
        .fetchall()
    ]
    assert streaks == [1, 2, 0]


# --- allowance propagation (model-free) -------------------------------------


def fake_fx_rate():
    """A deterministic stand-in. No network, no ECB call, no SDK."""
    from agents.checker.fx import FxRate

    return FxRate(
        source="test-fixed-rate",
        rate_date="2026-08-22",
        retrieved_at_utc=T0,
        usd_per_eur=Decimal("1.1000"),
    )


def test_reduced_allowance_is_propagated_into_the_run_budget_coordinator():
    """ADR-0010 section 2 / dispatch section 10. The existing
    coordinator already accepts ``total_eur_micros``, so propagation
    needs no change to agents/checker/* — this proves the reduced figure
    actually lands there, and only that. It does NOT prove a
    provider-capable loop works; no model call is made."""
    from runner.sentinel_adapter import build_iteration_budget

    coordinator = build_iteration_budget(
        allowance_eur_micros=250_000, fx_rate=fake_fx_rate()
    )
    assert coordinator.total_eur_micros == 250_000
    assert coordinator.remaining_eur_micros() == 250_000
    # And the reduction really binds: a reservation is drawn from the
    # reduced pool, not from the full EUR 0.75 per-run cap.
    reservation = coordinator.reserve()
    assert reservation.reserved_eur_micros <= 250_000
    assert coordinator.remaining_eur_micros() < 250_000


def test_full_allowance_still_equals_the_unchanged_per_run_cap():
    from runner.sentinel_adapter import PER_RUN_CAP_EUR_MICROS, build_iteration_budget

    assert PER_RUN_CAP_EUR_MICROS == 750_000
    coordinator = build_iteration_budget(
        allowance_eur_micros=PER_RUN_CAP_EUR_MICROS, fx_rate=fake_fx_rate()
    )
    assert coordinator.total_eur_micros == 750_000


@pytest.mark.parametrize("allowance", [0, -1, -750_000])
def test_non_positive_allowance_refuses_fail_closed(allowance):
    from runner.sentinel_adapter import AllowanceNotEnforceable, build_iteration_budget

    with pytest.raises(AllowanceNotEnforceable):
        build_iteration_budget(allowance_eur_micros=allowance, fx_rate=fake_fx_rate())


def test_an_allowance_above_the_per_run_cap_refuses_fail_closed():
    """The normal EUR 0.75 allowance is never silently restored, and a
    larger one is never granted."""
    from runner.sentinel_adapter import AllowanceNotEnforceable, build_iteration_budget

    with pytest.raises(AllowanceNotEnforceable):
        build_iteration_budget(allowance_eur_micros=750_001, fx_rate=fake_fx_rate())


def test_tiny_remaining_allowance_rounds_the_sdk_figure_down_not_up():
    """ADR-0010's recorded residual: for a very small remaining budget
    the SDK's four-decimal USD allowance can round down to 0.0000. That
    is fail-closed and correct, and no positive floor is invented to
    defeat it."""
    from runner.sentinel_adapter import build_iteration_budget

    coordinator = build_iteration_budget(allowance_eur_micros=1, fx_rate=fake_fx_rate())
    assert coordinator.reserve().sdk_max_budget_usd == 0.0


# --- durable cost accounting ------------------------------------------------


def test_accounted_cost_is_read_from_durable_cost_rows(tmp_path):
    """Section 2's accounting source. The figure comes from committed
    CostRows for the loop's own iteration run ids — a volatile counter
    is explicitly NOT the source of truth."""
    path = tmp_path / "cost_ledger.jsonl"
    for run_id, micros in [("r-0", 100), ("r-1", 250), ("r-other", 9_999)]:
        write_cost_row(path, run_id, micros)

    assert durable_accounted_cost(path, ["r-0", "r-1"]) == 350
    assert durable_accounted_cost(path, []) == 0
    assert durable_accounted_cost(tmp_path / "absent.jsonl", ["r-0"]) == 0


def test_accounted_cost_never_clamps_a_row_above_the_per_run_cap(tmp_path):
    path = tmp_path / "cost_ledger.jsonl"
    write_cost_row(path, "r-over", 1_234_567)
    assert durable_accounted_cost(path, ["r-over"]) == 1_234_567


def test_a_corrupt_trailing_cost_line_reports_nothing_rather_than_raising(tmp_path):
    """A crash mid-write can leave an unparseable final line. The loop's
    cost read must not raise there — the durable loop tables retain what
    was accounted at each finalize, and a read failure must never be
    allowed to mask the real stop reason."""
    path = tmp_path / "cost_ledger.jsonl"
    write_cost_row(path, "r-0", 100)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write('{"schema_version": 1, "run_i')

    assert durable_accounted_cost(path, ["r-0"]) == 0


# --- the real Sentinel adapter (stub mode, zero model calls) ----------------
#
# The tests above prove the supervisor's logic against a fake domain.
# These prove the other half: that the adapter really binds the
# supervisor's planned_run_id to RunConfig.run_id, really delegates
# recovery to Sentinel's own functions, and really reads cost from the
# committed ledger. Stub mode throughout, so every run makes zero model
# calls by construction.


def build_sentinel_executor(tmp_path):
    from runner.sentinel_adapter import SentinelIterationExecutor
    from sentinel.config import RunConfig
    from sentinel.net.links import StaticLinkResolver
    from sentinel.pipeline import Deps
    from tests.conftest import ListSurfaceProvider, make_repo_surface

    out = tmp_path / "out"
    base = RunConfig(
        run_kind="dev",
        source="fixtures",
        db_path=out / "sentinel.sqlite3",
        findings_path=out / "FINDINGS.md",
        log_path=out / "run.jsonl",
        cost_ledger_path=out / "cost_ledger.jsonl",
        fixtures_root=tmp_path / "fixtures",
        judgment_mode="stub",
    )
    repo = make_repo_surface("acme", {"README.md": "## Solution\n"})

    def deps_factory(_run_id, _allowance):
        return Deps(
            surface_provider=ListSurfaceProvider([repo]),
            link_resolver=StaticLinkResolver(mapping={}),
        )

    return SentinelIterationExecutor(base_config=base, deps_factory=deps_factory)


def test_adapter_binds_the_planned_run_id_as_the_actual_run_id(tmp_path):
    """The section-4 invariant reaching the pipeline unchanged: the run
    the pipeline creates IS the run the loop already committed to."""
    executor = build_sentinel_executor(tmp_path)

    assert executor.probe("r-planned-xyz").status is None  # nothing yet
    status = executor.execute("r-planned-xyz", allowance_eur_micros=750_000)

    assert status == "COMPLETED"
    conn = ledger.open_ledger(executor.base_config.db_path, create=False)
    try:
        run = ledger.get_run(conn, "r-planned-xyz")
        assert run is not None and run.status == "COMPLETED"
    finally:
        conn.close()
    assert executor.allowances_seen == [750_000]


def test_adapter_probe_reports_complete_outputs_after_a_finished_run(tmp_path):
    executor = build_sentinel_executor(tmp_path)
    executor.execute("r-probe", allowance_eur_micros=750_000)

    probe = executor.probe("r-probe")
    assert probe.status == "COMPLETED"
    assert probe.outputs_complete is True


def test_adapter_reads_cost_from_the_committed_cost_ledger(tmp_path):
    """A stub-mode run appends a genuine zero-cost row, so the durable
    read finds a row and reports its true value — a measurement, not a
    placeholder."""
    executor = build_sentinel_executor(tmp_path)
    executor.execute("r-cost", allowance_eur_micros=750_000)

    assert executor.accounted_cost(["r-cost"]) == 0
    assert executor.accounted_cost(["r-absent"]) == 0
    rows = read_cost_rows(executor.base_config.cost_ledger_path)
    assert [r.run_id for r in rows] == ["r-cost"]
    assert rows[0].cost_eur_micros == 0


def test_adapter_recovery_c_drives_an_interrupted_run_to_terminal(tmp_path):
    """Recovery case C against the REAL interrupted-run recovery: no
    replacement run id is invented, and the recovered terminal run is
    the iteration's result."""
    executor = build_sentinel_executor(tmp_path)
    conn = ledger.open_ledger(executor.base_config.db_path)
    try:
        seed_run(conn, "r-interrupted", "RUNNING")
    finally:
        conn.close()

    assert executor.probe("r-interrupted").status == "RUNNING"
    status = executor.recover_interrupted("r-interrupted")

    assert status == "FAILED"
    assert executor.probe("r-interrupted").status == "FAILED"
    # The same run id throughout — nothing else was created.
    conn = ledger.open_ledger(executor.base_config.db_path, create=False)
    try:
        assert [r.run_id for r in ledger.list_runs(conn)] == ["r-interrupted"]
    finally:
        conn.close()


def test_adapter_recovery_d_reconciles_missing_outputs_without_a_rerun(tmp_path):
    """Recovery case D against the REAL terminal-output reconciliation.
    The run stays exactly as it was; only its derived outputs are
    backfilled, and no second cost source is invented."""
    executor = build_sentinel_executor(tmp_path)
    executor.execute("r-outputs", allowance_eur_micros=750_000)

    # Simulate a crash landing after the run closed but before its
    # outputs were written.
    executor.base_config.findings_path.write_text("", encoding="utf-8")
    executor.base_config.cost_ledger_path.write_text("", encoding="utf-8")
    probe = executor.probe("r-outputs")
    assert probe.status == "COMPLETED"
    assert probe.outputs_complete is False

    executor.reconcile_outputs("r-outputs")

    assert executor.probe("r-outputs").outputs_complete is True
    # Exactly one cost row for the run — reconciliation backfilled, it
    # did not double-count.
    rows = read_cost_rows(executor.base_config.cost_ledger_path)
    assert [r.run_id for r in rows] == ["r-outputs"]
    # And the run itself was never re-executed.
    assert executor.executed_run_ids == ["r-outputs"]


def test_adapter_recovery_c_refuses_if_the_run_never_became_terminal(tmp_path):
    executor = build_sentinel_executor(tmp_path)
    with pytest.raises(RuntimeError):
        executor.recover_interrupted("r-never-existed")


def test_a_full_loop_through_the_real_adapter_completes_and_stays_zero_cost(tmp_path):
    """End-to-end, model-free: the supervisor drives two real Sentinel
    runs in stub mode. Every CostRow is zero-token and zero-cost, which
    is what "no model call happened" looks like in the ledger."""
    executor = build_sentinel_executor(tmp_path)
    # Loop state shares the run ledger: ``bound_run_id`` is foreign-keyed
    # to ``runs(run_id)``, so a separate loop database could not bind an
    # iteration to its run at all. This is the same wiring
    # ``python -m runner`` uses, where one ``--db`` serves both.
    conn = open_loop_state(executor.base_config.db_path)
    logger = RecordingLogger(tmp_path / "loop.jsonl")
    try:
        outcome = run_loop(
            LoopConfig(
                loop_id="loop-real",
                max_iterations=2,
                per_run_cap_eur_micros=PER_RUN_CAP,
            ),
            store=SqliteLoopStateStore(conn),
            executor=executor,
            clock=FrozenClock(ticks=[T0 + timedelta(seconds=i) for i in range(200)]),
            ids=SeededRunIds(prefix="r-real"),
            logger=logger,
        )
    finally:
        logger.close()
        conn.close()

    assert outcome.stop_reason == COMPLETED_ITERATION_CAP
    assert outcome.exit_code == 0
    assert outcome.iterations_completed == 2
    assert outcome.accounted_cost_eur_micros == 0
    assert executor.executed_run_ids == ["r-real-000", "r-real-001"]

    rows = read_cost_rows(executor.base_config.cost_ledger_path)
    assert [r.run_id for r in rows] == ["r-real-000", "r-real-001"]
    for row in rows:
        assert row.input_tokens == 0
        assert row.output_tokens == 0
        assert row.cost_eur_micros == 0
        assert row.model == "none-deterministic"


# --- runner CLI: stub only --------------------------------------------------


def test_runner_cli_refuses_agent_mode_fail_closed_before_any_construction(tmp_path):
    """Dispatch section 18: a provider-capable loop is not authorised.
    The refusal is deterministic, nonzero, and lands before any config,
    ledger or provider object is built — nothing is written at all."""
    from runner.__main__ import main

    before = {p for p in tmp_path.rglob("*")}
    code = main(
        [
            "--loop-id", "loop-cli",
            "--iterations", "1",
            "--run-kind", "dev",
            "--source", "fixtures",
            "--db", str(tmp_path / "s.sqlite3"),
            "--judgment-mode", "agent",
        ]
    )
    assert code != 0
    assert {p for p in tmp_path.rglob("*")} == before


def test_runner_cli_rejects_an_out_of_range_iteration_count(tmp_path):
    from runner.__main__ import main

    code = main(
        [
            "--loop-id", "loop-cli",
            "--iterations", "11",
            "--run-kind", "dev",
            "--source", "fixtures",
            "--db", str(tmp_path / "s.sqlite3"),
            "--findings", str(tmp_path / "F.md"),
            "--log", str(tmp_path / "l.jsonl"),
            "--cost-ledger", str(tmp_path / "c.jsonl"),
        ]
    )
    assert code == 2


def test_runner_cli_rejects_unknown_flags_and_missing_arguments():
    from runner.__main__ import main

    assert main([]) == 2
    assert main(["--nonexistent-flag"]) == 2


def test_runner_cli_requires_github_user_for_live_source(tmp_path):
    from runner.__main__ import main

    code = main(
        [
            "--loop-id", "loop-cli",
            "--iterations", "1",
            "--run-kind", "live",
            "--source", "live",
            "--db", str(tmp_path / "s.sqlite3"),
        ]
    )
    assert code == 2


def test_runner_cli_rejects_eval_kind_against_a_live_source(tmp_path):
    from runner.__main__ import main

    code = main(
        [
            "--loop-id", "loop-cli",
            "--iterations", "1",
            "--run-kind", "eval",
            "--source", "live",
            "--github-user", "someone",
            "--db", str(tmp_path / "s.sqlite3"),
        ]
    )
    assert code == 2


def test_runner_cli_offers_no_flag_that_raises_the_loop_ceiling():
    """ADR-0010 section 2: no CLI flag, configuration value or
    environment variable may raise the loop ceiling. The surface is
    checked directly, because "we just did not add one" is only true
    until someone adds one."""
    from runner.__main__ import build_parser

    options = {
        opt for action in build_parser()._actions for opt in action.option_strings
    }
    for forbidden in ("--loop-budget", "--budget", "--ceiling", "--max-cost"):
        assert forbidden not in options


def test_runner_cli_runs_a_stub_mode_loop_end_to_end(tmp_path, monkeypatch):
    """The one authorised path: stub mode, zero model calls, exit 0."""
    from runner.__main__ import main

    fixtures = tmp_path / "fixtures" / "repos" / "synthetic-x"
    fixtures.mkdir(parents=True)
    (fixtures / "README.md").write_text("## Solution\n", encoding="utf-8")
    (tmp_path / "fixtures" / "link_truth.jsonl").write_text("", encoding="utf-8")

    code = main(
        [
            "--loop-id", "loop-cli",
            "--iterations", "2",
            "--run-kind", "dev",
            "--source", "fixtures",
            "--fixtures-root", str(tmp_path / "fixtures" / "repos"),
            "--db", str(tmp_path / "s.sqlite3"),
            "--findings", str(tmp_path / "F.md"),
            "--log", str(tmp_path / "l.jsonl"),
            "--cost-ledger", str(tmp_path / "c.jsonl"),
        ]
    )
    assert code == 0

    conn = open_loop_state(tmp_path / "s.sqlite3")
    try:
        record = SqliteLoopStateStore(conn).loop_record("loop-cli")
        assert record["stop_reason"] == COMPLETED_ITERATION_CAP
        assert record["iterations_completed"] == 2
        assert record["accounted_cost_eur_micros"] == 0
    finally:
        conn.close()

    for row in read_cost_rows(tmp_path / "c.jsonl"):
        assert row.cost_eur_micros == 0
        assert row.model == "none-deterministic"

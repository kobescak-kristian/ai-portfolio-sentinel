"""SQLite-backed durable loop state (ADR-0010 section 4).

Persistence only. This module reuses the existing ledger connection and
transaction primitives (``sentinel.ledger.open_ledger`` /
``unit_of_work``) rather than opening a second database or introducing a
second transaction discipline — the loop tables live in the same ledger
file as ``runs``/``tasks``/``findings``, added by the same idempotent
DDL script.

That co-location is required, not incidental: ``loop_iterations.
bound_run_id`` is foreign-keyed to ``runs(run_id)``, so loop state kept
in a separate database could not bind an iteration to its run at all.
``python -m runner`` therefore points one ``--db`` at both.

No ``DELETE`` statement appears anywhere in this file, matching
``sentinel/ledger.py``'s discipline; the DDL's delete-abort triggers are
the backstop, not the mechanism.

The single design point worth stating plainly: ``record_intent`` commits
before it returns. The supervisor's whole crash-safety story rests on
that, because it is what makes "the durable intent exists before any
work begins" a fact about the database rather than a fact about the
order of lines in a function.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from contracts.schemas import serialize_db_datetime
from runner.loop import FINALIZED, INTENT, IterationRow, LoopSummary

# Imported by exact name, not as the ``sentinel`` package: the static
# boundary test allows this module precisely two domain imports, and
# naming them individually is what keeps that allowance narrow.
from sentinel.ledger import open_ledger, unit_of_work


class LoopStateError(RuntimeError):
    """A guarded loop-state write affected zero rows, or a durable
    invariant was violated."""


def open_loop_state(db_path: str | Path) -> sqlite3.Connection:
    """Open the ledger (applying the full idempotent DDL, loop tables
    included) exactly as every other consumer does."""
    return open_ledger(db_path)


class SqliteLoopStateStore:
    """``runner.loop.LoopStateStore`` over the shared SQLite ledger."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # --- loop lifecycle ---------------------------------------------------

    def begin_or_load_loop(
        self,
        *,
        loop_id: str,
        max_iterations: int,
        loop_budget_eur_micros: int,
        failure_threshold: int,
        now: datetime,
    ) -> bool:
        existing = self._conn.execute(
            "SELECT max_iterations, loop_budget_eur_micros, failure_threshold, status "
            "FROM loop_runs WHERE loop_id = ?",
            (loop_id,),
        ).fetchone()
        if existing is not None:
            # A resumed loop keeps the bounds it was created with. A
            # caller that tries to resume the same loop_id under
            # different bounds is refused rather than silently
            # re-parameterised — that would let an operator raise the
            # ceiling through the back door, which ADR-0010 section 2
            # forbids outright.
            if (
                existing["max_iterations"] != max_iterations
                or existing["loop_budget_eur_micros"] != loop_budget_eur_micros
                or existing["failure_threshold"] != failure_threshold
            ):
                raise LoopStateError(
                    f"loop {loop_id!r} already exists with different bounds; "
                    "a loop's bounds are fixed at creation"
                )
            if existing["status"] != "RUNNING":
                raise LoopStateError(
                    f"loop {loop_id!r} is already FINISHED; a terminal loop is never reopened"
                )
            return False

        with unit_of_work(self._conn):
            self._conn.execute(
                """
                INSERT INTO loop_runs (
                    loop_id, started_at_utc, finished_at_utc, max_iterations,
                    loop_budget_eur_micros, failure_threshold, iterations_started,
                    iterations_completed, consecutive_failures,
                    accounted_cost_eur_micros, status, stop_reason
                ) VALUES (?, ?, NULL, ?, ?, ?, 0, 0, 0, 0, 'RUNNING', NULL)
                """,
                (
                    loop_id,
                    serialize_db_datetime(now),
                    max_iterations,
                    loop_budget_eur_micros,
                    failure_threshold,
                ),
            )
        return True

    def finish_loop(
        self, *, loop_id: str, stop_reason: str, summary: LoopSummary, now: datetime
    ) -> None:
        """Exactly one terminal stop reason per loop — the guarded
        ``WHERE status = 'RUNNING'`` is what makes that true even if a
        caller tries twice."""
        with unit_of_work(self._conn):
            cur = self._conn.execute(
                """
                UPDATE loop_runs
                   SET status = 'FINISHED', stop_reason = ?, finished_at_utc = ?,
                       iterations_started = ?, iterations_completed = ?,
                       consecutive_failures = ?, accounted_cost_eur_micros = ?
                 WHERE loop_id = ? AND status = 'RUNNING'
                """,
                (
                    stop_reason,
                    serialize_db_datetime(now),
                    summary.iterations_started,
                    summary.iterations_completed,
                    summary.consecutive_failures,
                    summary.accounted_cost_eur_micros,
                    loop_id,
                ),
            )
            if cur.rowcount != 1:
                raise LoopStateError(
                    f"loop {loop_id!r} was not RUNNING for finish_loop (already finished?)"
                )

    # --- iterations -------------------------------------------------------

    def list_iterations(self, loop_id: str) -> list[IterationRow]:
        rows = self._conn.execute(
            """
            SELECT loop_id, iteration_index, planned_run_id, bound_run_id,
                   iteration_state, run_status
              FROM loop_iterations
             WHERE loop_id = ?
             ORDER BY iteration_index
            """,
            (loop_id,),
        ).fetchall()
        return [
            IterationRow(
                loop_id=row["loop_id"],
                iteration_index=row["iteration_index"],
                planned_run_id=row["planned_run_id"],
                iteration_state=row["iteration_state"],
                bound_run_id=row["bound_run_id"],
                run_status=row["run_status"],
            )
            for row in rows
        ]

    def record_intent(
        self, *, loop_id: str, iteration_index: int, planned_run_id: str, now: datetime
    ) -> IterationRow:
        """Persist the durable intent and COMMIT. Nothing may execute
        for this iteration until this has returned.

        ``UNIQUE(planned_run_id)`` and ``PRIMARY KEY(loop_id,
        iteration_index)`` mean a second intent for the same iteration,
        or a reused planned_run_id, is an IntegrityError rather than a
        silent duplicate run."""
        with unit_of_work(self._conn):
            self._conn.execute(
                """
                INSERT INTO loop_iterations (
                    loop_id, iteration_index, planned_run_id, bound_run_id,
                    iteration_state, run_status, accounted_cost_eur_micros,
                    consecutive_failures_after, started_at_utc, finished_at_utc
                ) VALUES (?, ?, ?, NULL, 'INTENT', NULL, 0, 0, ?, NULL)
                """,
                (loop_id, iteration_index, planned_run_id, serialize_db_datetime(now)),
            )
            self._conn.execute(
                "UPDATE loop_runs SET iterations_started = iterations_started + 1 "
                "WHERE loop_id = ?",
                (loop_id,),
            )
        return IterationRow(
            loop_id=loop_id,
            iteration_index=iteration_index,
            planned_run_id=planned_run_id,
            iteration_state=INTENT,
        )

    def finalize_iteration(
        self,
        *,
        loop_id: str,
        iteration_index: int,
        bound_run_id: str,
        run_status: str,
        accounted_cost_eur_micros: int,
        consecutive_failures_after: int,
        summary: LoopSummary,
        now: datetime,
    ) -> None:
        """Bind the run, move INTENT -> FINALIZED and refresh the loop
        summary in ONE transaction, so loop bookkeeping can never be
        half-applied. The ``WHERE iteration_state = 'INTENT'`` guard
        mirrors ``ledger.transition_task``'s optimistic discipline: an
        iteration finalizes exactly once."""
        with unit_of_work(self._conn):
            cur = self._conn.execute(
                """
                UPDATE loop_iterations
                   SET bound_run_id = ?, iteration_state = 'FINALIZED', run_status = ?,
                       accounted_cost_eur_micros = ?, consecutive_failures_after = ?,
                       finished_at_utc = ?
                 WHERE loop_id = ? AND iteration_index = ? AND iteration_state = 'INTENT'
                """,
                (
                    bound_run_id,
                    run_status,
                    accounted_cost_eur_micros,
                    consecutive_failures_after,
                    serialize_db_datetime(now),
                    loop_id,
                    iteration_index,
                ),
            )
            if cur.rowcount != 1:
                raise LoopStateError(
                    f"iteration ({loop_id!r}, {iteration_index}) was not INTENT "
                    "for finalize_iteration (already finalized?)"
                )
            self._conn.execute(
                """
                UPDATE loop_runs
                   SET iterations_started = ?, iterations_completed = ?,
                       consecutive_failures = ?, accounted_cost_eur_micros = ?
                 WHERE loop_id = ?
                """,
                (
                    summary.iterations_started,
                    summary.iterations_completed,
                    summary.consecutive_failures,
                    summary.accounted_cost_eur_micros,
                    loop_id,
                ),
            )

    # --- read-back for evidence and tests ---------------------------------

    def loop_record(self, loop_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM loop_runs WHERE loop_id = ?", (loop_id,)
        ).fetchone()


__all__ = [
    "FINALIZED",
    "INTENT",
    "LoopStateError",
    "SqliteLoopStateStore",
    "open_loop_state",
]

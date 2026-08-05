"""sentinel.ledger — delta only vs tests/test_contracts.py.

test_contracts.py already exhausts DDL/trigger behavior via raw
sqlite3. This file covers only what the DAL adds on top: PRAGMA
enforcement on every connection, idempotent schema init, rollback on
a failed write, lock/busy-timeout behavior, and CAS transition
conflicts. Round-trip validity of stored rows is exercised
incidentally throughout — it doesn't need its own restatement here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from contracts.schemas import CheckTask, RunRecord
from sentinel import ledger

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


def _run(run_id="r1", **overrides):
    defaults = dict(
        schema_version=1,
        run_id=run_id,
        run_kind="dev",
        status="RUNNING",
        started_at_utc=NOW,
        tasks_created=0,
        tasks_terminal=0,
        findings_new=0,
        findings_still_open=0,
        findings_resolved=0,
    )
    defaults.update(overrides)
    return RunRecord(**defaults)


def test_foreign_keys_enforced_on_every_connection(tmp_path):
    conn = ledger.open_ledger(tmp_path / "db.sqlite3")
    (value,) = conn.execute("PRAGMA foreign_keys").fetchone()
    assert value == 1


def test_reopening_existing_db_does_not_reapply_ddl_or_lose_rows(tmp_path):
    db_path = tmp_path / "db.sqlite3"
    conn = ledger.open_ledger(db_path)
    with ledger.unit_of_work(conn):
        ledger.insert_run(conn, _run())
    conn.close()

    conn2 = ledger.open_ledger(db_path)
    assert ledger.get_run(conn2, "r1") is not None
    conn2.close()


def test_initialize_schema_returns_created_flag(tmp_path):
    db_path = tmp_path / "db.sqlite3"
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    assert ledger.initialize_schema(conn) is True
    assert ledger.initialize_schema(conn) is False
    conn.close()


def test_every_datetime_column_is_25_chars_and_round_trips(tmp_path):
    conn = ledger.open_ledger(tmp_path / "db.sqlite3")
    with ledger.unit_of_work(conn):
        ledger.insert_run(conn, _run())
    (started,) = conn.execute("SELECT started_at_utc FROM runs").fetchone()
    assert len(started) == 25
    assert datetime.fromisoformat(started) == NOW


def test_unit_of_work_rolls_back_on_exception(tmp_path):
    conn = ledger.open_ledger(tmp_path / "db.sqlite3")
    with pytest.raises(sqlite3.IntegrityError):
        with ledger.unit_of_work(conn):
            ledger.insert_run(conn, _run())
            ledger.insert_run(conn, _run())  # duplicate PK -> IntegrityError
    # Reopen via a fresh connection: nothing committed.
    conn2 = ledger.open_ledger(tmp_path / "db.sqlite3", create=False)
    assert conn2.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    conn2.close()


def test_transition_task_is_compare_and_swap(tmp_path):
    conn = ledger.open_ledger(tmp_path / "db.sqlite3")
    with ledger.unit_of_work(conn):
        ledger.insert_run(conn, _run())
        task = CheckTask(
            schema_version=1,
            task_id="t1",
            run_id="r1",
            surface="acme/README.md",
            check_class="broken-link",
            created_at_utc=NOW,
            status="PENDING",
        )
        ledger.insert_tasks(conn, [task])

    with ledger.unit_of_work(conn):
        ledger.transition_task(conn, "r1", "t1", expected="PENDING", new="IN_PROGRESS")

    with pytest.raises(ledger.TaskTransitionConflict):
        with ledger.unit_of_work(conn):
            # stale precondition: task is IN_PROGRESS, not PENDING
            ledger.transition_task(conn, "r1", "t1", expected="PENDING", new="IN_PROGRESS")


def test_advance_finding_requires_open_row(tmp_path):
    conn = ledger.open_ledger(tmp_path / "db.sqlite3")
    with ledger.unit_of_work(conn):
        ledger.insert_run(conn, _run())
    with pytest.raises(ledger.LedgerConflict):
        with ledger.unit_of_work(conn):
            ledger.advance_finding(conn, 999, last_seen_utc=NOW, last_seen_run_id="r1")


def test_busy_timeout_raises_operational_error_not_hang(tmp_path):
    """A short busy_timeout means a second writer fails fast with
    OperationalError rather than hanging indefinitely — unattended-run
    safety. Both connections stay in the main thread (sqlite3
    connections are single-thread by default); the file-level write
    lock still contends correctly across connections either way."""
    db_path = tmp_path / "db.sqlite3"
    conn1 = ledger.open_ledger(db_path, pragmas={**ledger.DEFAULT_PRAGMAS, "busy_timeout": "50"})
    conn2 = ledger.open_ledger(db_path, create=False, pragmas={**ledger.DEFAULT_PRAGMAS, "busy_timeout": "50"})

    conn1.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn2.execute("BEGIN IMMEDIATE")
    finally:
        conn1.execute("COMMIT")
        conn1.close()
        conn2.close()

"""Data-access layer over the frozen ``contracts/ledger_schema.sql``.

This is the only module that issues SQL. Every write function takes a
caller-supplied connection and does not commit — only ``open_ledger``
and ``initialize_schema`` commit. Callers wrap one or more writes in
``unit_of_work`` to define a transaction boundary; the caller decides
where boundaries fall (sentinel.pipeline), this module just makes
each individual write safe and atomic.

No ``DELETE`` statement appears anywhere in this file — the frozen
DDL's delete-abort triggers are the backstop, this is the discipline
that means they're never exercised in normal operation.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Collection, Iterator, Literal, NamedTuple, Sequence

import contracts
from contracts.schemas import (
    CheckTask,
    Finding,
    RunRecord,
    serialize_db_datetime,
)
from sentinel.states import TaskTransitionConflict, assert_legal

DDL_PATH: Path = Path(contracts.__file__).parent / "ledger_schema.sql"

DEFAULT_PRAGMAS: dict[str, str] = {
    "foreign_keys": "ON",
    "journal_mode": "WAL",
    "synchronous": "FULL",
    "busy_timeout": "5000",
}


class LedgerError(RuntimeError):
    """Base class for DAL-level errors."""


class LedgerConflict(LedgerError):
    """A guarded write affected zero rows — the row didn't exist, or
    didn't match the expected precondition (e.g. status)."""


class RunCounts(NamedTuple):
    tasks_terminal: int
    findings_new: int
    findings_still_open: int
    findings_resolved: int


class FindingRow(NamedTuple):
    id: int
    finding: Finding


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def open_ledger(
    db_path: str | Path,
    *,
    create: bool = True,
    pragmas: dict[str, str] | None = None,
) -> sqlite3.Connection:
    path = Path(db_path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.exists():
        raise LedgerError(f"ledger database does not exist: {path}")
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    for pragma, value in (pragmas or DEFAULT_PRAGMAS).items():
        conn.execute(f"PRAGMA {pragma} = {value}")
    initialize_schema(conn)
    return conn


def initialize_schema(conn: sqlite3.Connection) -> bool:
    """Apply the frozen DDL if the schema doesn't exist yet. Returns
    True if it was just created, False if it already existed."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
    ).fetchone()
    if row is not None:
        return False
    conn.executescript(DDL_PATH.read_text(encoding="utf-8"))
    return True


@contextmanager
def unit_of_work(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------


def insert_run(conn: sqlite3.Connection, run: RunRecord) -> None:
    conn.execute(
        """
        INSERT INTO runs (
            run_id, schema_version, run_kind, status, started_at_utc,
            finished_at_utc, tasks_created, tasks_terminal,
            findings_new, findings_still_open, findings_resolved
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.run_id,
            run.schema_version,
            run.run_kind,
            run.status,
            serialize_db_datetime(run.started_at_utc),
            serialize_db_datetime(run.finished_at_utc) if run.finished_at_utc else None,
            run.tasks_created,
            run.tasks_terminal,
            run.findings_new,
            run.findings_still_open,
            run.findings_resolved,
        ),
    )


def _run_from_row(row: sqlite3.Row) -> RunRecord:
    data = dict(row)
    data["started_at_utc"] = _dt(data["started_at_utc"])
    data["finished_at_utc"] = _dt(data["finished_at_utc"]) if data["finished_at_utc"] else None
    return RunRecord.model_validate(data)


def get_run(conn: sqlite3.Connection, run_id: str) -> RunRecord | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return _run_from_row(row) if row is not None else None


def list_runs(
    conn: sqlite3.Connection, *, status: str | None = None, limit: int | None = None
) -> list[RunRecord]:
    sql = "SELECT * FROM runs"
    params: list[object] = []
    if status is not None:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY started_at_utc"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_run_from_row(row) for row in rows]


def bump_run_counts(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    tasks_created: int | None = None,
    tasks_terminal_delta: int = 0,
) -> None:
    cur = conn.execute(
        """
        UPDATE runs
           SET tasks_created = COALESCE(:tasks_created, tasks_created),
               tasks_terminal = tasks_terminal + :delta
         WHERE run_id = :run_id
        """,
        {"tasks_created": tasks_created, "delta": tasks_terminal_delta, "run_id": run_id},
    )
    if cur.rowcount != 1:
        raise LedgerConflict(f"run {run_id!r} not found for bump_run_counts")


def close_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: Literal["COMPLETED", "FAILED"],
    finished_at_utc: datetime,
    counts: RunCounts,
) -> None:
    cur = conn.execute(
        """
        UPDATE runs
           SET status = ?, finished_at_utc = ?, tasks_terminal = ?,
               findings_new = ?, findings_still_open = ?, findings_resolved = ?
         WHERE run_id = ?
        """,
        (
            status,
            serialize_db_datetime(finished_at_utc),
            counts.tasks_terminal,
            counts.findings_new,
            counts.findings_still_open,
            counts.findings_resolved,
            run_id,
        ),
    )
    if cur.rowcount != 1:
        raise LedgerConflict(f"run {run_id!r} not found for close_run")


# --------------------------------------------------------------------------
# tasks
# --------------------------------------------------------------------------


def insert_tasks(conn: sqlite3.Connection, tasks: Sequence[CheckTask]) -> None:
    conn.executemany(
        """
        INSERT INTO tasks (
            schema_version, task_id, run_id, surface, check_class,
            created_at_utc, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                t.schema_version,
                t.task_id,
                t.run_id,
                t.surface,
                t.check_class,
                serialize_db_datetime(t.created_at_utc),
                t.status,
            )
            for t in tasks
        ],
    )


def _task_from_row(row: sqlite3.Row) -> CheckTask:
    data = dict(row)
    data["created_at_utc"] = _dt(data["created_at_utc"])
    return CheckTask.model_validate(data)


def get_task(conn: sqlite3.Connection, run_id: str, task_id: str) -> CheckTask | None:
    row = conn.execute(
        "SELECT * FROM tasks WHERE run_id = ? AND task_id = ?", (run_id, task_id)
    ).fetchone()
    return _task_from_row(row) if row is not None else None


def list_tasks(
    conn: sqlite3.Connection, run_id: str, *, statuses: Sequence[str] | None = None
) -> list[CheckTask]:
    sql = "SELECT * FROM tasks WHERE run_id = ?"
    params: list[object] = [run_id]
    if statuses is not None:
        placeholders = ",".join("?" for _ in statuses)
        sql += f" AND status IN ({placeholders})"
        params.extend(statuses)
    sql += " ORDER BY task_id"
    rows = conn.execute(sql, params).fetchall()
    return [_task_from_row(row) for row in rows]


def count_tasks(
    conn: sqlite3.Connection, run_id: str, *, statuses: Sequence[str] | None = None
) -> int:
    sql = "SELECT COUNT(*) FROM tasks WHERE run_id = ?"
    params: list[object] = [run_id]
    if statuses is not None:
        placeholders = ",".join("?" for _ in statuses)
        sql += f" AND status IN ({placeholders})"
        params.extend(statuses)
    (count,) = conn.execute(sql, params).fetchone()
    return count


def transition_task(
    conn: sqlite3.Connection, run_id: str, task_id: str, *, expected: str, new: str
) -> None:
    assert_legal(expected, new)
    cur = conn.execute(
        "UPDATE tasks SET status = ? WHERE run_id = ? AND task_id = ? AND status = ?",
        (new, run_id, task_id, expected),
    )
    if cur.rowcount != 1:
        raise TaskTransitionConflict(
            f"task {task_id!r} in run {run_id!r} was not in status {expected!r}"
        )


def fail_and_dead_letter(conn: sqlite3.Connection, run_id: str, task_id: str, *, expected: str) -> None:
    """Atomic (within the caller's transaction) FAILED -> DEAD_LETTER
    routing. ``expected`` is the task's current status before failure
    (PENDING or IN_PROGRESS)."""
    transition_task(conn, run_id, task_id, expected=expected, new="FAILED")
    transition_task(conn, run_id, task_id, expected="FAILED", new="DEAD_LETTER")


def sweep_non_terminal(conn: sqlite3.Connection, run_id: str) -> int:
    """Route every PENDING/IN_PROGRESS task of a run to DEAD_LETTER.
    Used by crash recovery and by failure containment. Returns the
    count of tasks swept."""
    swept = 0
    for task in list_tasks(conn, run_id, statuses=["PENDING", "IN_PROGRESS"]):
        fail_and_dead_letter(conn, run_id, task.task_id, expected=task.status)
        swept += 1
    return swept


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------


def _finding_from_row(row: sqlite3.Row) -> FindingRow:
    data = dict(row)
    finding_id = data.pop("id")
    data["first_seen_utc"] = _dt(data["first_seen_utc"])
    data["last_seen_utc"] = _dt(data["last_seen_utc"])
    data["resolved_at_utc"] = _dt(data["resolved_at_utc"]) if data["resolved_at_utc"] else None
    return FindingRow(id=finding_id, finding=Finding.model_validate(data))


def get_open_finding(conn: sqlite3.Connection, fingerprint: str) -> FindingRow | None:
    row = conn.execute(
        "SELECT * FROM findings WHERE fingerprint = ? AND status = 'OPEN'", (fingerprint,)
    ).fetchone()
    return _finding_from_row(row) if row is not None else None


def has_resolved_finding(conn: sqlite3.Connection, fingerprint: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM findings WHERE fingerprint = ? AND status = 'RESOLVED' LIMIT 1",
        (fingerprint,),
    ).fetchone()
    return row is not None


def list_open_findings(
    conn: sqlite3.Connection, *, scopes: Collection[tuple[str, str]] | None = None
) -> list[FindingRow]:
    rows = conn.execute("SELECT * FROM findings WHERE status = 'OPEN'").fetchall()
    result = [_finding_from_row(row) for row in rows]
    if scopes is not None:
        scope_set = set(scopes)
        result = [r for r in result if (r.finding.surface, r.finding.check_class) in scope_set]
    return result


def list_findings_for_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    role: Literal["first_seen", "last_seen", "resolved"],
) -> list[FindingRow]:
    column = f"{role}_run_id"
    rows = conn.execute(f"SELECT * FROM findings WHERE {column} = ?", (run_id,)).fetchall()
    return [_finding_from_row(row) for row in rows]


def insert_finding(conn: sqlite3.Connection, finding: Finding) -> int:
    cur = conn.execute(
        """
        INSERT INTO findings (
            schema_version, fingerprint, surface, check_class, content_hash,
            location, detail, status, first_seen_utc, last_seen_utc,
            resolved_at_utc, first_seen_run_id, last_seen_run_id, resolved_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            finding.schema_version,
            finding.fingerprint,
            finding.surface,
            finding.check_class,
            finding.content_hash,
            finding.location,
            finding.detail,
            finding.status,
            serialize_db_datetime(finding.first_seen_utc),
            serialize_db_datetime(finding.last_seen_utc),
            serialize_db_datetime(finding.resolved_at_utc) if finding.resolved_at_utc else None,
            finding.first_seen_run_id,
            finding.last_seen_run_id,
            finding.resolved_run_id,
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def advance_finding(
    conn: sqlite3.Connection, finding_id: int, *, last_seen_utc: datetime, last_seen_run_id: str
) -> None:
    """Advance an OPEN finding's last_seen_utc/last_seen_run_id only.
    Clamped to the existing last_seen_utc so the trigger's monotonic
    requirement can never be violated by clock skew or a same-second
    rerun."""
    row = conn.execute(
        "SELECT last_seen_utc FROM findings WHERE id = ? AND status = 'OPEN'", (finding_id,)
    ).fetchone()
    if row is None:
        raise LedgerConflict(f"finding {finding_id!r} is not an OPEN row")
    existing = _dt(row["last_seen_utc"])
    effective = max(last_seen_utc, existing)
    cur = conn.execute(
        "UPDATE findings SET last_seen_utc = ?, last_seen_run_id = ? WHERE id = ? AND status = 'OPEN'",
        (serialize_db_datetime(effective), last_seen_run_id, finding_id),
    )
    if cur.rowcount != 1:
        raise LedgerConflict(f"finding {finding_id!r} was not advanced (race?)")


def resolve_finding(
    conn: sqlite3.Connection, finding_id: int, *, resolved_at_utc: datetime, resolved_run_id: str
) -> None:
    """Resolve an OPEN finding. Never touches last_seen_utc/
    last_seen_run_id (the trigger forbids it). Clamped to the
    existing last_seen_utc so resolved_at_utc >= last_seen_utc always
    holds."""
    row = conn.execute(
        "SELECT last_seen_utc FROM findings WHERE id = ? AND status = 'OPEN'", (finding_id,)
    ).fetchone()
    if row is None:
        raise LedgerConflict(f"finding {finding_id!r} is not an OPEN row")
    existing = _dt(row["last_seen_utc"])
    effective = max(resolved_at_utc, existing)
    cur = conn.execute(
        """
        UPDATE findings
           SET status = 'RESOLVED', resolved_at_utc = ?, resolved_run_id = ?
         WHERE id = ? AND status = 'OPEN'
        """,
        (serialize_db_datetime(effective), resolved_run_id, finding_id),
    )
    if cur.rowcount != 1:
        raise LedgerConflict(f"finding {finding_id!r} was not resolved (race?)")

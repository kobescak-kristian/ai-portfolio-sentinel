"""sentinel.lifecycle — dedup/lifecycle at the DAL level (below the
full pipeline). tests/test_contracts.py proves the DDL/trigger permit
recurrence and the two lifecycle operations at the raw-SQL level;
this file proves sentinel.lifecycle *performs* them correctly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from checks.base import ObservedFinding
from contracts.schemas import RunRecord
from sentinel import ledger, lifecycle

T0 = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)
T3 = T0 + timedelta(hours=3)


def _make_run(conn, run_id, started):
    with ledger.unit_of_work(conn):
        ledger.insert_run(
            conn,
            RunRecord(
                schema_version=1,
                run_id=run_id,
                run_kind="dev",
                status="RUNNING",
                started_at_utc=started,
                tasks_created=0,
                tasks_terminal=0,
                findings_new=0,
                findings_still_open=0,
                findings_resolved=0,
            ),
        )


def _finding(surface="acme/README.md", check_class="broken-link", detail="d", content="c"):
    return ObservedFinding(
        surface=surface, check_class=check_class, location="README.md:1", detail=detail,
        normalized_content=content,
    )


def test_recurrence_after_resolution_is_a_new_row(tmp_path):
    conn = ledger.open_ledger(tmp_path / "db.sqlite3")
    _make_run(conn, "run-1", T0)
    _make_run(conn, "run-2", T1)
    _make_run(conn, "run-3", T2)

    obs = _finding()
    with ledger.unit_of_work(conn):
        result1 = lifecycle.apply_observed(conn, "run-1", T0, [obs])
    fingerprint = result1.new_fingerprints[0]

    with ledger.unit_of_work(conn):
        row = ledger.get_open_finding(conn, fingerprint)
        lifecycle.resolve_absent(
            conn, "run-2", T1, scanned_scopes={(obs.surface, obs.check_class)}, observed_fingerprints=set()
        )

    with ledger.unit_of_work(conn):
        result3 = lifecycle.apply_observed(conn, "run-3", T2, [obs])
    assert fingerprint in result3.new_fingerprints
    assert fingerprint in result3.recurred_fingerprints

    count = conn.execute(
        "SELECT COUNT(*) FROM findings WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()[0]
    assert count == 2
    open_count = conn.execute(
        "SELECT COUNT(*) FROM findings WHERE fingerprint = ? AND status='OPEN'", (fingerprint,)
    ).fetchone()[0]
    assert open_count == 1


def test_dead_lettered_scope_never_auto_resolves(tmp_path):
    """A scope whose task ended FAILED/DEAD_LETTER this run must be
    excluded from scanned_scopes entirely — its OPEN findings stay
    untouched, never advanced, never resolved."""
    conn = ledger.open_ledger(tmp_path / "db.sqlite3")
    _make_run(conn, "run-1", T0)
    _make_run(conn, "run-2", T1)

    obs = _finding()
    with ledger.unit_of_work(conn):
        lifecycle.apply_observed(conn, "run-1", T0, [obs])
    before = dict(conn.execute("SELECT * FROM findings").fetchone())

    # run-2: the task for this scope ended DEAD_LETTER (unknown), so
    # scanned_scopes for run-2 is empty — the caller (pipeline) never
    # includes this scope.
    with ledger.unit_of_work(conn):
        resolved = lifecycle.resolve_absent(
            conn, "run-2", T1, scanned_scopes=set(), observed_fingerprints=set()
        )
    assert resolved == []
    after = dict(conn.execute("SELECT * FROM findings").fetchone())
    assert before == after  # completely untouched


def test_compute_run_counts_reconciles_from_ledger_state(tmp_path):
    conn = ledger.open_ledger(tmp_path / "db.sqlite3")
    _make_run(conn, "run-1", T0)
    _make_run(conn, "run-2", T1)

    obs_a = _finding(surface="acme/a.md", content="a")
    obs_b = _finding(surface="acme/b.md", content="b")
    with ledger.unit_of_work(conn):
        lifecycle.apply_observed(conn, "run-1", T0, [obs_a, obs_b])
    counts1 = lifecycle.compute_run_counts(conn, "run-1")
    assert counts1.findings_new == 2
    assert counts1.findings_still_open == 0
    assert counts1.findings_resolved == 0

    with ledger.unit_of_work(conn):
        lifecycle.resolve_absent(
            conn,
            "run-2",
            T1,
            scanned_scopes={(obs_a.surface, obs_a.check_class), (obs_b.surface, obs_b.check_class)},
            observed_fingerprints={lifecycle.compute_content_and_fingerprint(obs_a)[1]},
        )
    counts2 = lifecycle.compute_run_counts(conn, "run-2")
    assert counts2.findings_new == 0
    assert counts2.findings_resolved == 1
    assert counts2.findings_still_open == 1


def test_within_run_duplicate_fingerprint_collapses(tmp_path):
    conn = ledger.open_ledger(tmp_path / "db.sqlite3")
    _make_run(conn, "run-1", T0)
    obs = _finding()
    with ledger.unit_of_work(conn):
        result = lifecycle.apply_observed(conn, "run-1", T0, [obs, obs])
    assert len(result.new_fingerprints) == 1
    assert len(result.duplicate_within_run) == 1

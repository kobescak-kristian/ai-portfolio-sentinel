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


# ---------------------------------------------------------------------
# adr/0006 cross-run judgment identity (T6, T7). Model-free: findings
# are built through the real agents.checker.evidence host validator, but
# no SDK call, no network and no fixture read occurs. T1-T4/T8 live in
# tests/test_bounds.py; T5 in tests/test_checks_deterministic.py.
# ---------------------------------------------------------------------

# Exact line and spans from the consumed re-gate (2026-08-19,
# synthetic-05/EVAL_RESULTS.md:14), as plain literals.
_REGATE_TEXT = "intro\n- Coverage: 85.5 percent\noutro"
_SPAN_RUN_1 = "Coverage: 85.5 percent"
_SPAN_RUN_2 = "- Coverage: 85.5 percent"
_LABEL_CODE = "FIGURE_WITHOUT_ADJACENT_SYNTHETIC_LABEL"
_SURFACE = "acme/EVAL_RESULTS.md"
_SCOPE = (_SURFACE, "missing-synthetic-label")


def _judgment_finding(excerpt: str):
    """The real host-validated construction path -- not a hand-built
    ObservedFinding, so this exercises evidence.py's actual output."""
    from agents.checker.evidence import EvidenceItem, build_observed_finding
    from checks.judgment.stubs import JudgmentRequest

    request = JudgmentRequest(
        surface=_SURFACE, check_class="missing-synthetic-label",
        path="EVAL_RESULTS.md", text=_REGATE_TEXT,
    )
    return build_observed_finding(
        request, reason_code=_LABEL_CODE, evidence=[EvidenceItem(line=2, excerpt=excerpt)]
    )


def _row_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]


def test_t6_rerun_with_a_different_valid_excerpt_advances_and_mints_nothing(tmp_path):
    """The direct regression proof for the two invariants that failed in
    the consumed re-gate (idempotent_rerun,
    dedup_correct_on_doubled_fixture_run).

    Run 1 observes the defect citing one valid span; run 2 observes the
    same semantic defect citing a different valid span of the same line.
    Run 2 must advance the existing finding: nothing new, nothing
    resolved, one still open."""
    conn = ledger.open_ledger(tmp_path / "db.sqlite3")
    _make_run(conn, "run-1", T0)
    _make_run(conn, "run-2", T1)

    run_1_obs = _judgment_finding(_SPAN_RUN_1)
    with ledger.unit_of_work(conn):
        result_1 = lifecycle.apply_observed(conn, "run-1", T0, [run_1_obs])
    assert len(result_1.new_fingerprints) == 1
    fingerprint = result_1.new_fingerprints[0]

    run_2_obs = _judgment_finding(_SPAN_RUN_2)
    assert run_2_obs.detail != run_1_obs.detail  # a genuinely different citation
    with ledger.unit_of_work(conn):
        result_2 = lifecycle.apply_observed(conn, "run-2", T1, [run_2_obs])
        resolved = lifecycle.resolve_absent(
            conn, "run-2", T1,
            scanned_scopes={_SCOPE},
            observed_fingerprints=set(result_2.advanced_fingerprints) | set(result_2.new_fingerprints),
        )

    assert result_2.new_fingerprints == []
    assert result_2.advanced_fingerprints == [fingerprint]
    assert resolved == []

    counts = lifecycle.compute_run_counts(conn, "run-2")
    assert counts.findings_new == 0
    assert counts.findings_resolved == 0
    assert counts.findings_still_open == 1
    assert _row_count(conn) == 1  # one defect, one row -- never two

    # detail is FIRST-SEEN audit evidence: the advance does not rewrite it.
    row = ledger.get_open_finding(conn, fingerprint)
    assert row.finding.detail == run_1_obs.detail
    assert row.finding.last_seen_run_id == "run-2"


def test_t7_old_excerpt_bearing_identity_resolves_once_and_never_deletes_a_row(tmp_path):
    """Compatibility for an OPEN judgment finding created under the old
    excerpt-bearing identity. No schema migration and no historical-DB
    rewrite: the first observation under the new identity inserts the
    new fingerprint and auto-resolves the old one exactly once, deletes
    nothing, raises nothing, and is stable from then on."""
    conn = ledger.open_ledger(tmp_path / "db.sqlite3")
    for run_id, started in (("run-1", T0), ("run-2", T1), ("run-3", T2)):
        _make_run(conn, run_id, started)

    # The old rule, written as a literal: evidence.py can no longer
    # produce this shape, so it is reconstructed here deliberately.
    old_rule_obs = ObservedFinding(
        surface=_SURFACE, check_class="missing-synthetic-label",
        location="EVAL_RESULTS.md:2",
        detail=f"{_LABEL_CODE} at line 2: {_SPAN_RUN_1!r}",
        normalized_content=f"{_LABEL_CODE}|{_SPAN_RUN_1}",
    )
    with ledger.unit_of_work(conn):
        old_result = lifecycle.apply_observed(conn, "run-1", T0, [old_rule_obs])
    old_fingerprint = old_result.new_fingerprints[0]
    assert _row_count(conn) == 1

    new_rule_obs = _judgment_finding(_SPAN_RUN_1)
    new_fingerprint = lifecycle.compute_content_and_fingerprint(new_rule_obs)[1]
    assert new_fingerprint != old_fingerprint  # the identity really did change

    with ledger.unit_of_work(conn):
        result_2 = lifecycle.apply_observed(conn, "run-2", T1, [new_rule_obs])
        resolved = lifecycle.resolve_absent(
            conn, "run-2", T1,
            scanned_scopes={_SCOPE},
            observed_fingerprints=set(result_2.new_fingerprints) | set(result_2.advanced_fingerprints),
        )

    assert result_2.new_fingerprints == [new_fingerprint]
    assert resolved == [old_fingerprint]
    assert _row_count(conn) == 2  # append-only: nothing was deleted
    assert ledger.get_open_finding(conn, old_fingerprint) is None
    assert ledger.get_open_finding(conn, new_fingerprint) is not None

    # The old row still exists, RESOLVED and stamped -- not removed.
    old_status = conn.execute(
        "SELECT status, resolved_run_id FROM findings WHERE fingerprint = ?", (old_fingerprint,)
    ).fetchone()
    assert old_status["status"] == "RESOLVED"
    assert old_status["resolved_run_id"] == "run-2"

    # Run 3: stable. Nothing new, nothing resolved, one still open.
    with ledger.unit_of_work(conn):
        result_3 = lifecycle.apply_observed(conn, "run-3", T2, [_judgment_finding(_SPAN_RUN_2)])
        resolved_3 = lifecycle.resolve_absent(
            conn, "run-3", T2,
            scanned_scopes={_SCOPE},
            observed_fingerprints=set(result_3.advanced_fingerprints) | set(result_3.new_fingerprints),
        )
    assert result_3.new_fingerprints == []
    assert result_3.advanced_fingerprints == [new_fingerprint]
    assert resolved_3 == []
    assert _row_count(conn) == 2

    counts = lifecycle.compute_run_counts(conn, "run-3")
    assert counts.findings_new == 0
    assert counts.findings_resolved == 0
    assert counts.findings_still_open == 1

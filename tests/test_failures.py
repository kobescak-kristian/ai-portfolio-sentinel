"""Failure-injection suite (BLUEPRINT §3, §5, §6; ADR 0002).

Phase 2 activates the 8 deterministic-control-plane stubs below with
real bodies, plus 2 crash-consistency legs (C4) and a self-guard that
the remaining later-phase stubs stay skipped. Phase 3 activated the
per-run cost-cap leg; Phase 4 (dispatch q77-p4-fi-a) activates the two
seeded BREAKER legs. Exactly one stub is still skipped, deliberately:
the full ADR-0010 section 5 four-part failure alert needs a labeled
``ITERATION_LOG.md`` evidence line, and that surface has not landed. Cage and no-write-access
tests are NOT stubbed here: the full cage suite lands in
tests/test_bounds.py at Phase 3. Phase 2's own narrower boundary
tests (zero-model-call invariant, no-write-access-by-construction)
live in tests/test_read_only_boundary.py — a deliberately different,
narrower file, not a re-run of the Phase-3 gate.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Sequence

import pytest

from checks.judgment.stubs import ScriptedJudgmentStub
from contracts.schemas import CostRow, RunRecord
from runner.breakers import (
    CONSECUTIVE_FAILURE_BREAKER_TRIPPED,
    COST_BREAKER_TRIPPED,
    LOOP_BUDGET_EUR_MICROS,
)
from runner.loop import FINALIZED, LoopConfig, LoopSummary, RunProbe, run_loop
from runner.sentinel_adapter import durable_accounted_cost
from runner.state import SqliteLoopStateStore, open_loop_state
from sentinel import ledger, lifecycle
from sentinel.ids import FrozenClock
from sentinel.logs import RunLogger
from sentinel.pipeline import Deps, execute_run
from telemetry.cost_ledger import append_cost_row

from tests.conftest import (
    ListSurfaceProvider,
    SimulatedCrash,
    T0,
    T1,
    T2,
    crash_at,
    make_repo_surface,
)

# --- Phase 2: deterministic control plane (state machine, dedup, ledger) ---


def test_every_task_reaches_terminal_state(tmp_path, make_config, make_deps, fixed_clock):
    """Invariant: every_task_terminal. A checker that always raises
    exhausts to DEAD_LETTER; the run still reaches a coherent terminal
    state with no PENDING/IN_PROGRESS rows left anywhere."""

    class PoisonedJudgment:
        def judge(self, request):
            raise RuntimeError("seeded permanent fault")

    repo = make_repo_surface(
        "acme",
        {
            "README.md": "## Problem\np\n## Solution\ns\n## System\nsy\n## Outcome\no\n## Version Log\nv\n",
            "EVAL_RESULTS.md": "no figures here\n",
        },
        required_paths=(".githooks/pre-push",),
    )
    provider = ListSurfaceProvider([repo])
    config = make_config(tmp_path, run_kind="dev", source="fixtures", fail_run_on_task_failure=False)
    deps = make_deps(clock=fixed_clock(T0, T1), surface_provider=provider, judgment=PoisonedJudgment())

    outcome = execute_run(config, deps)

    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        statuses = {
            row["status"]
            for row in conn.execute(
                "SELECT status FROM tasks WHERE run_id = ?", (outcome.run_id,)
            ).fetchall()
        }
        assert statuses <= {"DONE", "FAILED", "DEAD_LETTER"}
        assert "DEAD_LETTER" in statuses  # non-vacuity: the poisoned checker must have fired
        assert 0 == conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE run_id = ? AND status IN ('PENDING','IN_PROGRESS')",
            (outcome.run_id,),
        ).fetchone()[0]
        run_row = conn.execute(
            "SELECT tasks_created, tasks_terminal FROM runs WHERE run_id = ?", (outcome.run_id,)
        ).fetchone()
        assert run_row["tasks_created"] == run_row["tasks_terminal"]
        assert outcome.tasks_created == outcome.tasks_terminal
        assert outcome.tasks_created > 0
    finally:
        conn.close()


def test_no_task_lost_across_a_run(tmp_path, make_config, make_deps, fixed_clock):
    """Invariant: zero_lost_tasks. The set of (surface, check_class)
    tasks created equals what's recorded in the ledger — nothing
    created is ever silently dropped."""
    repo = make_repo_surface(
        "acme",
        {"README.md": "## Problem\n## Solution\n## System\n## Outcome\n## Version Log\n"},
    )
    provider = ListSurfaceProvider([repo])
    config = make_config(tmp_path)
    deps = make_deps(clock=fixed_clock(T0, T1), surface_provider=provider)

    outcome = execute_run(config, deps)

    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        tasks = ledger.list_tasks(conn, outcome.run_id)
        pairs = {(t.surface, t.check_class) for t in tasks}
        assert len(pairs) == len(tasks)  # no duplicate (surface, check_class) pair
        assert len(tasks) == outcome.tasks_created == outcome.tasks_terminal
        assert outcome.tasks_created > 0
    finally:
        conn.close()


def test_failed_task_routes_to_dead_letter_atomically(tmp_path, make_config, make_deps, fixed_clock):
    """A checker that raises mid-way must not leave a partially
    committed finding: DEAD_LETTER routing and the absence of any
    finding for that task are the same atomic transaction."""

    class PoisonedJudgment:
        def judge(self, request):
            raise RuntimeError("seeded permanent fault")

    repo = make_repo_surface(
        "acme",
        {"README.md": "## Problem\n## Solution\n## System\n## Outcome\n## Version Log\n"},
    )
    provider = ListSurfaceProvider([repo])
    config = make_config(tmp_path, fail_run_on_task_failure=False)
    deps = make_deps(
        clock=fixed_clock(T0, T1),
        surface_provider=provider,
        judgment=PoisonedJudgment(),
    )

    outcome = execute_run(config, deps)

    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        dead_letter_tasks = ledger.list_tasks(conn, outcome.run_id, statuses=["DEAD_LETTER"])
        assert len(dead_letter_tasks) >= 1
        for task in dead_letter_tasks:
            assert task.check_class in ("stale-STATE-marker", "missing-synthetic-label")
        assert 0 == conn.execute(
            "SELECT COUNT(*) FROM findings WHERE check_class IN ('stale-STATE-marker','missing-synthetic-label')"
        ).fetchone()[0]
        assert 0 == conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE run_id = ? AND status = 'IN_PROGRESS'",
            (outcome.run_id,),
        ).fetchone()[0]
    finally:
        conn.close()


def test_crash_mid_run_leaves_ledger_consistent_on_rerun(
    tmp_path, make_config, make_deps, fixed_clock, seeded_ids
):
    """A crash before any task executes must leave a RUNNING row that
    a later invocation recovers to a coherent FAILED state — never a
    silent COMPLETED, and never lost/duplicated findings on rerun."""
    repo = make_repo_surface(
        "acme",
        {"README.md": "## Problem\n## Solution\n## System\n## Outcome\n## Version Log\n"},
    )
    provider = ListSurfaceProvider([repo])
    config = make_config(tmp_path)

    crash_deps = make_deps(
        clock=fixed_clock(T0),
        ids=seeded_ids(["run-crash"]),
        surface_provider=provider,
        hooks=crash_at("after_tasks_created"),
    )
    with pytest.raises(SimulatedCrash):
        execute_run(config, crash_deps)

    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        crashed_runs = ledger.list_runs(conn, status="RUNNING")
        assert len(crashed_runs) == 1
        crashed_run_id = crashed_runs[0].run_id
    finally:
        conn.close()

    rerun_deps = make_deps(
        clock=fixed_clock(T1, T2), ids=seeded_ids(["run-rerun"]), surface_provider=provider
    )
    outcome2 = execute_run(config, rerun_deps)

    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        recovered = ledger.get_run(conn, crashed_run_id)
        assert recovered.status == "FAILED"
        assert recovered.finished_at_utc is not None
        assert 0 == conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status = 'RUNNING'"
        ).fetchone()[0]
        assert 0 == conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('PENDING','IN_PROGRESS')"
        ).fetchone()[0]
        assert outcome2.status == "COMPLETED"
        dupes = conn.execute(
            "SELECT fingerprint FROM findings WHERE status='OPEN' GROUP BY fingerprint HAVING COUNT(*) > 1"
        ).fetchall()
        assert dupes == []
    finally:
        conn.close()


def test_idempotent_rerun_produces_no_new_findings(
    tmp_path, make_config, make_deps, fixed_clock, seeded_ids
):
    """Invariant: idempotent_rerun. Same inputs, second run: zero new
    findings, zero resolved, and FINDINGS.md gains exactly one more
    section reporting zero new findings."""
    repo = make_repo_surface(
        "acme",
        {"README.md": "## Solution\n## Outcome\n"},  # missing Problem/System/Version Log
    )
    provider = ListSurfaceProvider([repo])
    config = make_config(tmp_path)

    outcome1 = execute_run(
        config, make_deps(clock=fixed_clock(T0), ids=seeded_ids(["run-1"]), surface_provider=provider)
    )
    assert outcome1.findings_new > 0

    outcome2 = execute_run(
        config, make_deps(clock=fixed_clock(T1), ids=seeded_ids(["run-2"]), surface_provider=provider)
    )
    assert outcome2.findings_new == 0
    assert outcome2.findings_resolved == 0
    assert outcome2.findings_still_open == outcome1.findings_new

    text = config.findings_path.read_text(encoding="utf-8")
    assert text.count("sentinel:run") == 4  # 2 runs x (open + close) markers
    assert f"Run {outcome2.run_id}" in text


def test_dedup_correct_on_doubled_fixture_run(tmp_path, make_config, make_deps, fixed_clock):
    """Invariant: dedup_correct_on_doubled_fixture_run — both halves.
    (a) The same surface appearing twice in one run's inventory never
    doubles the OPEN-finding count. (b) Identical content on two
    distinct surfaces produces distinct fingerprints (dedup must not
    over-merge across surfaces)."""
    files = {"README.md": "## Solution\n"}
    repo = make_repo_surface("acme", files)
    provider = ListSurfaceProvider([repo, repo])  # (a) doubled inventory entry
    config = make_config(tmp_path)

    execute_run(config, make_deps(clock=fixed_clock(T0), surface_provider=provider))

    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        dupes = conn.execute(
            "SELECT fingerprint, COUNT(*) c FROM findings WHERE status='OPEN' "
            "GROUP BY fingerprint HAVING c > 1"
        ).fetchall()
        assert dupes == []
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        distinct_units = conn.execute(
            "SELECT COUNT(DISTINCT surface || '|' || check_class) FROM tasks"
        ).fetchone()[0]
        assert task_count == distinct_units  # the duplicate collapsed before task creation
    finally:
        conn.close()

    # (b) doubled corpus content on two distinct surfaces
    repo_a = make_repo_surface("acme-a", files)
    repo_b = make_repo_surface("acme-b", files)
    provider2 = ListSurfaceProvider([repo_a, repo_b])
    config2 = make_config(tmp_path, db_path=tmp_path / "sentinel2.sqlite3", findings_path=tmp_path / "F2.md")
    execute_run(config2, make_deps(clock=fixed_clock(T0), surface_provider=provider2))
    conn2 = ledger.open_ledger(config2.db_path, create=False)
    try:
        fps = [row["fingerprint"] for row in conn2.execute("SELECT fingerprint FROM findings").fetchall()]
        assert len(fps) == len(set(fps))
        assert len(fps) >= 2  # readme-structure fires identically on both surfaces
    finally:
        conn2.close()


def test_open_finding_advances_last_seen_without_duplicate_row(
    tmp_path, make_config, make_deps, fixed_clock, seeded_ids
):
    """A defect present in two consecutive runs: exactly one OPEN
    row, whose only changed columns across the two runs are
    last_seen_utc and last_seen_run_id."""
    from sentinel.net.links import StaticLinkResolver

    dead_url = "https://dead-example.example.invalid/x"
    repo = make_repo_surface(
        "acme",
        {
            "README.md": (
                "## Problem\n## Solution\n## System\n## Outcome\n## Version Log\n\n"
                f"[bad link]({dead_url})\n"
            )
        },
        link_scanned_paths=("README.md",),
    )
    provider = ListSurfaceProvider([repo])
    config = make_config(tmp_path)
    link_resolver = StaticLinkResolver(mapping={dead_url: "dead"})

    outcome1 = execute_run(
        config,
        make_deps(
            clock=fixed_clock(T0),
            ids=seeded_ids(["run-1"]),
            surface_provider=provider,
            link_resolver=link_resolver,
        ),
    )
    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        before = dict(conn.execute("SELECT * FROM findings").fetchone())
    finally:
        conn.close()

    outcome2 = execute_run(
        config,
        make_deps(
            clock=fixed_clock(T1),
            ids=seeded_ids(["run-2"]),
            surface_provider=provider,
            link_resolver=link_resolver,
        ),
    )
    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        rows = conn.execute("SELECT * FROM findings").fetchall()
        assert len(rows) == 1
        after = dict(rows[0])
        assert after["id"] == before["id"]
        assert after["status"] == "OPEN"
        assert after["resolved_at_utc"] is None
        differing = {k for k in before if before[k] != after[k]}
        assert differing == {"last_seen_utc", "last_seen_run_id"}
        assert after["last_seen_run_id"] == outcome2.run_id
        assert before["first_seen_run_id"] == outcome1.run_id == after["first_seen_run_id"]
    finally:
        conn.close()


def test_absent_finding_auto_resolves_with_dated_row(
    tmp_path, make_config, make_deps, fixed_clock, seeded_ids
):
    """A defect present in run 1, then genuinely fixed for run 2: the
    same row resolves, stamped with run 2's timestamp and run_id,
    last_seen_utc unchanged (the defect was never re-observed)."""
    broken = make_repo_surface("acme", {"README.md": "## Solution\n"})
    fixed = make_repo_surface(
        "acme", {"README.md": "## Problem\n## Solution\n## System\n## Outcome\n## Version Log\n"}
    )

    config = make_config(tmp_path)
    outcome1 = execute_run(
        config,
        make_deps(
            clock=fixed_clock(T0), ids=seeded_ids(["run-1"]), surface_provider=ListSurfaceProvider([broken])
        ),
    )
    assert outcome1.findings_new > 0

    outcome2 = execute_run(
        config,
        make_deps(
            clock=fixed_clock(T1), ids=seeded_ids(["run-2"]), surface_provider=ListSurfaceProvider([fixed])
        ),
    )
    assert outcome2.findings_resolved == outcome1.findings_new
    assert outcome2.findings_new == 0

    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        rows = conn.execute("SELECT * FROM findings").fetchall()
        assert len(rows) == outcome1.findings_new  # never deleted, still present
        for row in rows:
            assert row["status"] == "RESOLVED"
            assert row["resolved_run_id"] == outcome2.run_id
            assert row["last_seen_utc"] == row["first_seen_utc"]  # never re-observed
            assert row["resolved_at_utc"] >= row["last_seen_utc"]
    finally:
        conn.close()

    text = config.findings_path.read_text(encoding="utf-8")
    assert "Resolved this run" in text


# --- Phase 2: crash-consistent finalization (C4) ---------------------------


def test_crash_before_terminal_close_recovers_without_premature_report(
    tmp_path, make_config, make_deps, fixed_clock, seeded_ids
):
    """A crash before the run's terminal DB transaction commits must
    leave no output at all *until* recovery gives it a coherent
    terminal state — no report is ever written for a still-RUNNING
    row. Once the next invocation's recovery pass closes it FAILED,
    reconciliation backfills its report honestly (FAILED, partial)."""
    repo = make_repo_surface("acme", {"README.md": "## Solution\n"})
    provider = ListSurfaceProvider([repo])
    config = make_config(tmp_path)

    with pytest.raises(SimulatedCrash):
        execute_run(
            config,
            make_deps(
                clock=fixed_clock(T0),
                ids=seeded_ids(["run-crash"]),
                surface_provider=provider,
                hooks=crash_at("before_run_close"),
            ),
        )

    assert not config.findings_path.exists()
    assert not config.cost_ledger_path.exists()

    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        # Immediately after the crash — before any recovery has run —
        # the row is still RUNNING: close_run() never committed.
        running = ledger.list_runs(conn, status="RUNNING")
        assert len(running) == 1
        crashed_run_id = running[0].run_id
    finally:
        conn.close()

    execute_run(
        config, make_deps(clock=fixed_clock(T1), ids=seeded_ids(["run-2"]), surface_provider=provider)
    )
    # The next invocation's own recovery pass closes the crashed run to
    # a coherent FAILED state *before* that new run starts; reconciliation
    # then (correctly, per plan §5) backfills a report for it too — every
    # terminal run gets a durable, visible record, and a recovered run's
    # section states its FAILED/partial status honestly rather than
    # being silently omitted.
    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        recovered = ledger.get_run(conn, crashed_run_id)
        assert recovered.status == "FAILED"
    finally:
        conn.close()
    text = config.findings_path.read_text(encoding="utf-8")
    assert f"sentinel:run {crashed_run_id}" in text
    assert "FAILED (partial" in text


def test_crash_after_close_repaired_by_next_invocation(
    tmp_path, make_config, make_deps, fixed_clock, seeded_ids
):
    """A crash after the run closes to a terminal DB status but
    before FINDINGS.md/CostRow are written is repaired by the very
    next invocation — reconciliation backfills exactly the missing
    output, never duplicating an existing marker or CostRow."""
    repo = make_repo_surface("acme", {"README.md": "## Solution\n"})
    provider = ListSurfaceProvider([repo])
    config = make_config(tmp_path)

    with pytest.raises(SimulatedCrash):
        execute_run(
            config,
            make_deps(
                clock=fixed_clock(T0),
                ids=seeded_ids(["run-crash"]),
                surface_provider=provider,
                hooks=crash_at("before_report_append"),
            ),
        )
    crashed_run_id_holder = []
    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        completed = ledger.list_runs(conn, status="COMPLETED")
        assert len(completed) == 1
        crashed_run_id_holder.append(completed[0].run_id)
    finally:
        conn.close()
    crashed_run_id = crashed_run_id_holder[0]
    assert not config.findings_path.exists()
    assert not config.cost_ledger_path.exists()

    execute_run(
        config, make_deps(clock=fixed_clock(T1), ids=seeded_ids(["run-2"]), surface_provider=provider)
    )

    text = config.findings_path.read_text(encoding="utf-8")
    assert text.count(f"sentinel:run {crashed_run_id}") == 2  # open + close, exactly once each
    from telemetry.cost_ledger import read_cost_rows

    rows = read_cost_rows(config.cost_ledger_path)
    run_ids = [r.run_id for r in rows]
    assert run_ids.count(crashed_run_id) == 1


# --- self-guard: only later-phase stubs remain skipped ----------------------


def test_only_phase_3_and_4_stubs_remain_skipped():
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    skipped = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            call = deco if isinstance(deco, ast.Call) else None
            if call is None:
                continue
            func = call.func
            is_skip = (isinstance(func, ast.Attribute) and func.attr == "skip") or (
                isinstance(func, ast.Name) and func.id == "skip"
            )
            if not is_skip:
                continue
            reason = None
            for kw in call.keywords:
                if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                    reason = kw.value.value
            skipped[node.name] = reason

    # Current truth after dispatch q77-p4-fi-a: both seeded BREAKER legs
    # are activated, and exactly ONE stub remains. It is not skipped for
    # convenience - ADR-0010 section 5 defines a proven failure alert as
    # ALL FOUR of a structured ERROR event, a durable stop_reason, a
    # nonzero exit and a labeled ITERATION_LOG.md evidence line. Three
    # are implemented and proven by the two tests below; the fourth
    # surface has not landed, and the definition is not weakened to
    # reach zero skips early.
    assert set(skipped) == {"test_seeded_breaker_trip_produces_failure_alert"}
    for name, reason in skipped.items():
        assert reason is not None and "Phase 4" in reason, (name, reason)
        assert "ITERATION_LOG" in reason, (name, reason)


# --- Phase 3: caged checker agent (dispatch q77-p3-a) -----------------------


def test_per_run_cost_cap_halts_checker(tmp_path, make_config, make_deps, fixed_clock):
    """FI (activated at Phase 3): per-run cost cap. A run-scoped budget
    sized for exactly one judgment call drains on the first call;
    every remaining judgment task must dead-letter without another
    model call, deterministic tasks still complete normally, no
    partial finding survives for a dead-lettered judgment scope, the
    run fails overall (fail_run_on_task_failure defaults True), and
    the aggregate charged cost never exceeds the budget."""
    from decimal import Decimal
    from types import SimpleNamespace
    from unittest.mock import patch

    from agents.checker.budget import RunBudgetCoordinator
    from agents.checker.fx import FxRate
    from agents.checker.harness import CagedCheckerStub
    from sentinel import costs

    calls_made: list[str] = []

    def tiny_query_fn(check_class, reservation, state, user_prompt):
        calls_made.append(check_class)
        # total_cost_usd=None -> "unresolved usage" path -> the full
        # reservation is charged, deliberately draining the entire
        # tiny budget on this one call so every later judgment task
        # hits BudgetExhausted before any further call.
        return SimpleNamespace(
            is_error=False, subtype="success", num_turns=1,
            total_cost_usd=None, usage={}, result="",
        )

    rate = FxRate(source="ecb-eurofxref-daily", rate_date="2026-08-05", retrieved_at_utc=T0, usd_per_eur=Decimal("1.1554"))
    # Sized to exactly one call's worth (agents.checker.config.MAX_PER_CALL_RESERVE_EUR_MICROS,
    # 150_000 micro-EUR since adr/0005) so the first judgment call drains
    # the run's entire budget.
    coordinator = RunBudgetCoordinator(fx_rate=rate, total_eur_micros=150_000)

    repo = make_repo_surface(
        "acme",
        {
            "README.md": "## Problem\np\n## Solution\ns\n## System\nsy\n## Outcome\no\n## Version Log\nv\n",
            "EVAL_RESULTS.md": "no figures here\n",
            "STATE.md": "current state text\n",
            ".githooks/pre-push": "#!/bin/sh\n",
        },
        required_paths=(".githooks/pre-push",),
    )
    provider = ListSurfaceProvider([repo])
    config = make_config(tmp_path, run_kind="dev", source="fixtures", run_id="r-cost-cap")

    stub_conn = ledger.open_ledger(config.db_path)
    # This FI test exercises the shared-budget/dead-letter behavior in
    # isolation, not authentication — the auth-override check is
    # exercised directly by its own tests in tests/test_bounds.py.
    with patch("agents.checker.harness.auth.assert_no_auth_override_risk", return_value=None):
        judgment = CagedCheckerStub(
            run_id="r-cost-cap", conn=stub_conn, coordinator=coordinator, clock=lambda: T0, query_fn=tiny_query_fn
        )
        deps = make_deps(clock=fixed_clock(T0, T1, T2), surface_provider=provider, judgment=judgment)
        outcome = execute_run(config, deps)

    assert outcome.status == "FAILED"
    assert len(calls_made) == 1  # every subsequent judgment task was refused before any call

    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        dead_lettered = ledger.list_tasks(conn, "r-cost-cap", statuses=["DEAD_LETTER"])
        judgment_classes = {"stale-STATE-marker", "missing-synthetic-label"}
        judgment_dead_lettered = [t for t in dead_lettered if t.check_class in judgment_classes]
        assert len(judgment_dead_lettered) >= 1

        done = ledger.list_tasks(conn, "r-cost-cap", statuses=["DONE"])
        deterministic_classes = {"broken-link", "number-mismatch", "readme-structure", "missing-required-file"}
        deterministic_done = [t for t in done if t.check_class in deterministic_classes]
        assert len(deterministic_done) > 0  # deterministic tasks were not affected by the exhausted budget

        open_findings = ledger.list_open_findings(conn)
        judgment_findings = [f for f in open_findings if f.finding.check_class in judgment_classes]
        assert judgment_findings == []  # no partial finding survives for any dead-lettered scope

        row = costs.build_agent_cost_row(conn, run_id="r-cost-cap", run_kind="dev", recorded_at_utc=T2)
        assert row.cost_eur_micros <= 150_000
    finally:
        conn.close()
        stub_conn.close()


# --- Phase 4: breakers and bounded loop -------------------------------------
#
# Seeded, MODEL-FREE fault injection against the landed ADR-0010
# supervisor (dispatch q77-p4-fi-a).
#
# These are deliberately NOT another set of pure predicate cases.
# tests/test_breakers.py already pins the boundary arithmetic and
# tests/test_loop_runner.py already pins the reset sequence, the
# terminal-precedence matrix and every section-3A boundary consequence;
# repeating those here would add volume, not evidence. What these two
# tests prove is the other thing: the REAL supervisory path under a
# seeded fault — runner.loop.run_loop driving the real
# SqliteLoopStateStore over a real ledger, with real ``runs`` rows, real
# durable ``CostRow``s and the real RunLogger — with the breaker's
# consequences read back from committed state and from the log file on
# disk rather than from an in-memory double.
#
# Nothing below contacts the network, imports claude_agent_sdk, calls a
# provider, or bypasses the supervisor or its durable loop state.

PHASE4_LOOP_ID = "loop-fi"

# The existing per-run cap, injected rather than imported: runner.loop is
# domain-free and its caller supplies this figure.
PHASE4_PER_RUN_CAP_MICROS = 750_000


def _seed_run(conn, run_id: str, status: str) -> None:
    """A REAL ``runs`` row, opened and closed through the ledger's own
    primitives. ``loop_iterations.bound_run_id`` is foreign-keyed to
    ``runs(run_id)`` and constrained equal to ``planned_run_id``, so a
    dictionary stand-in would side-step the integrity the schema
    actually enforces — every iteration these tests finalize binds to a
    run SQLite has verified exists."""
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
        ledger.close_run(
            conn,
            run_id,
            status=status,
            finished_at_utc=T0 + timedelta(hours=1),
            counts=ledger.RunCounts(0, 0, 0, 0),
        )


def _write_cost_row(path: Path, run_id: str, micros: int) -> None:
    """A REAL durable CostRow — ADR-0010 §2's accounting source. Seeding
    cost here rather than in a counter is the point: §2 says accounted
    consumption is reconstructed from committed cost records, so this is
    the one place where a seeded overspend is indistinguishable from a
    real one as far as the loop is concerned."""
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
class SeededIterationExecutor:
    """A test-only ``runner.loop.IterationExecutor`` with a seeded
    terminal outcome.

    Deliberately minimal: it exists to inject a fault, not to simulate
    Sentinel (``tests/test_loop_runner.py`` already drives the real
    adapter end to end). It still writes a real terminal ``runs`` row
    and a real ``CostRow`` per iteration and answers every probe and
    cost query from that durable state, so "no further run started" is
    observable as a fact about persisted data plus a call log.

    Model-free by construction: no network, no ``claude_agent_sdk``, no
    provider, no query surface, and no path around the supervisor or the
    durable loop state."""

    conn: object
    cost_ledger_path: Path
    seeded_status: str = "FAILED"
    seeded_cost_eur_micros: int = 0
    execute_calls: list[str] = field(default_factory=list)
    allowances_seen: list[int] = field(default_factory=list)

    def probe(self, planned_run_id: str) -> RunProbe:
        run = ledger.get_run(self.conn, planned_run_id)
        if run is None:
            return RunProbe(status=None, outputs_complete=False)
        if run.status == "RUNNING":
            return RunProbe(status="RUNNING", outputs_complete=False)
        return RunProbe(status=run.status, outputs_complete=True)

    def execute(self, planned_run_id: str, *, allowance_eur_micros: int) -> str:
        self.execute_calls.append(planned_run_id)
        self.allowances_seen.append(allowance_eur_micros)
        _seed_run(self.conn, planned_run_id, self.seeded_status)
        _write_cost_row(
            self.cost_ledger_path, planned_run_id, self.seeded_cost_eur_micros
        )
        return self.seeded_status

    def recover_interrupted(self, planned_run_id: str) -> str:
        raise AssertionError("no interrupted run is seeded by these FI cases")

    def reconcile_outputs(self, planned_run_id: str) -> None:
        raise AssertionError("no incomplete-output run is seeded by these FI cases")

    def accounted_cost(self, run_ids: Sequence[str]) -> int:
        return durable_accounted_cost(self.cost_ledger_path, run_ids)


class SeededPlannedRunIds:
    """Deterministic ``planned_run_id``s, so assertions can name exactly
    which runs were and were not created."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.issued: list[str] = []

    def new_run_id(self) -> str:
        value = f"{self.prefix}-{len(self.issued):03d}"
        self.issued.append(value)
        return value


def _severities_for(log_path: Path, event: str) -> list[str]:
    """Severity of every record for one event, read back from the REAL
    JSONL the loop wrote.

    Reading the file rather than an in-memory recorder is what makes
    this ADR-0010 §5 part 1 evidence: the event had to pass
    ``RunLogger``'s closed-vocabulary check and land on disk, so a
    breaker event that was merely constructed in memory would not appear
    here."""
    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [r["severity"] for r in records if r["event"] == event]


def _phase4_loop_config(n: int) -> LoopConfig:
    return LoopConfig(
        loop_id=PHASE4_LOOP_ID,
        max_iterations=n,
        per_run_cap_eur_micros=PHASE4_PER_RUN_CAP_MICROS,
        loop_budget_eur_micros=LOOP_BUDGET_EUR_MICROS,
    )


def test_cost_breaker_trips_on_seeded_overspend(tmp_path):
    """FI (activated at Phase 4, dispatch q77-p4-fi-a): the ADR-0010 §2
    cost breaker under a SEEDED durable overspend.

    One completed iteration is seeded whose durable CostRow puts
    accounted loop consumption at 750001 micro-EUR — exactly one above
    the frozen 750000 ceiling. The real supervisor is then resumed with
    N NOT yet exhausted, so normal iteration-cap completion cannot
    intervene and the breaker is the only thing that can stop the loop.

    It must trip on §3 leg A's strict ``>``, account the overshoot in
    full without clamping, emit the ERROR-severity breaker event,
    persist the stop reason, exit nonzero, and start nothing further.

    Model-free: the fault is a committed cost row, which is precisely
    where §2 says the loop reads its accounting from. No provider, no
    SDK, no network."""
    db_path = tmp_path / "loop.sqlite3"
    cost_ledger_path = tmp_path / "cost_ledger.jsonl"
    log_path = tmp_path / "loop.jsonl"
    seeded_run_id = "r-fi-cost-000"
    seeded_overshoot = 750_001  # exactly one micro-EUR above the frozen ceiling

    conn = open_loop_state(db_path)
    logger = RunLogger(log_path)
    executor = SeededIterationExecutor(conn=conn, cost_ledger_path=cost_ledger_path)
    try:
        store = SqliteLoopStateStore(conn)
        # Seed a legitimate finalized iteration through the REAL store:
        # durable intent, a real terminal run row, a real CostRow, then a
        # real finalize binding the two. No row is hand-written, and no
        # in-memory loop total is faked.
        store.begin_or_load_loop(
            loop_id=PHASE4_LOOP_ID,
            max_iterations=3,
            loop_budget_eur_micros=LOOP_BUDGET_EUR_MICROS,
            failure_threshold=3,
            now=T0,
        )
        store.record_intent(
            loop_id=PHASE4_LOOP_ID,
            iteration_index=0,
            planned_run_id=seeded_run_id,
            now=T0,
        )
        _seed_run(conn, seeded_run_id, "COMPLETED")
        _write_cost_row(cost_ledger_path, seeded_run_id, seeded_overshoot)
        store.finalize_iteration(
            loop_id=PHASE4_LOOP_ID,
            iteration_index=0,
            bound_run_id=seeded_run_id,
            run_status="COMPLETED",
            accounted_cost_eur_micros=seeded_overshoot,
            consecutive_failures_after=0,
            summary=LoopSummary(1, 1, 0, seeded_overshoot),
            now=T1,
        )

        outcome = run_loop(
            _phase4_loop_config(n=3),
            store=store,
            executor=executor,
            clock=FrozenClock(ticks=[T2 + timedelta(seconds=i) for i in range(50)]),
            ids=SeededPlannedRunIds("r-fi-cost-next"),
            logger=logger,
        )
    finally:
        logger.close()
        conn.close()

    assert outcome.stop_reason == COST_BREAKER_TRIPPED
    assert outcome.exit_code != 0
    # The known overshoot is accounted in full, never clamped to the
    # ceiling (ADR-0010 §2), and N was genuinely not exhausted.
    assert outcome.accounted_cost_eur_micros == seeded_overshoot == 750_001
    assert outcome.iterations_completed == 1 < 3

    # The seeded executor was never asked to run anything: the refusal
    # landed before any work, not after it.
    assert executor.execute_calls == []

    # ADR-0010 §5 part 1 — a structured ERROR-severity event from the
    # closed vocabulary, read back from the real loop log on disk.
    assert _severities_for(log_path, "breaker.cost_tripped") == ["ERROR"]

    conn = open_loop_state(db_path)
    try:
        store = SqliteLoopStateStore(conn)
        record = store.loop_record(PHASE4_LOOP_ID)
        # §5 part 2 — the stop reason and the un-clamped figure are DURABLE.
        assert record["status"] == "FINISHED"
        assert record["stop_reason"] == COST_BREAKER_TRIPPED
        assert record["accounted_cost_eur_micros"] == 750_001

        rows = store.list_iterations(PHASE4_LOOP_ID)
        # No next iteration began: no second INTENT row, and no second
        # underlying run anywhere in the ledger.
        assert len(rows) == 1
        assert [r.run_id for r in ledger.list_runs(conn)] == [seeded_run_id]

        # The already-finalized iteration's identity is untouched by the trip.
        assert rows[0].iteration_index == 0
        assert rows[0].iteration_state == FINALIZED
        assert rows[0].planned_run_id == seeded_run_id
        assert rows[0].bound_run_id == seeded_run_id
        assert rows[0].run_status == "COMPLETED"
    finally:
        conn.close()


def test_consecutive_failure_breaker_trips_on_seeded_failures(tmp_path):
    """FI (activated at Phase 4, dispatch q77-p4-fi-a): the ADR-0010 §1
    consecutive-failure breaker under SEEDED iteration failures.

    The real supervisor is driven with N = 4 — high enough that normal
    iteration-cap completion cannot pre-empt the streak — against an
    executor whose every iteration ends FAILED at deterministic ZERO
    cost, so the cost breaker cannot mask the streak breaker. Exactly
    three iterations must start and finalize; the fourth must never
    start, because §1 refuses the NEXT iteration rather than aborting
    one already underway.

    Model-free: the seeded executor makes no provider call and imports
    no SDK, yet each iteration still leaves a real terminal ``runs`` row
    and a real CostRow and finalizes through the real store."""
    db_path = tmp_path / "loop.sqlite3"
    cost_ledger_path = tmp_path / "cost_ledger.jsonl"
    log_path = tmp_path / "loop.jsonl"

    conn = open_loop_state(db_path)
    logger = RunLogger(log_path)
    ids = SeededPlannedRunIds("r-fi-fail")
    executor = SeededIterationExecutor(
        conn=conn,
        cost_ledger_path=cost_ledger_path,
        seeded_status="FAILED",
        seeded_cost_eur_micros=0,
    )
    try:
        outcome = run_loop(
            _phase4_loop_config(n=4),
            store=SqliteLoopStateStore(conn),
            executor=executor,
            clock=FrozenClock(ticks=[T0 + timedelta(seconds=i) for i in range(200)]),
            ids=ids,
            logger=logger,
        )
    finally:
        logger.close()
        conn.close()

    # Exactly three iterations started and exactly three finalized.
    assert outcome.iterations_started == 3
    assert outcome.iterations_completed == 3
    # The streak stopped it at exactly the threshold, with a nonzero exit.
    assert outcome.consecutive_failures == 3
    assert outcome.stop_reason == CONSECUTIVE_FAILURE_BREAKER_TRIPPED
    assert outcome.exit_code != 0
    # Cost never came near the ceiling, so nothing masked the streak.
    assert outcome.accounted_cost_eur_micros == 0

    # The fourth iteration never started: the executor was called exactly
    # three times, for exactly the three ids the loop committed to.
    assert executor.execute_calls == ids.issued
    assert len(executor.execute_calls) == 3

    # ADR-0010 §5 part 1, from the real loop log on disk.
    assert _severities_for(log_path, "breaker.consecutive_failure_tripped") == ["ERROR"]

    conn = open_loop_state(db_path)
    try:
        store = SqliteLoopStateStore(conn)
        record = store.loop_record(PHASE4_LOOP_ID)
        # §5 part 2 — the durable stop reason and the durable streak.
        assert record["status"] == "FINISHED"
        assert record["stop_reason"] == CONSECUTIVE_FAILURE_BREAKER_TRIPPED
        assert record["iterations_started"] == 3
        assert record["iterations_completed"] == 3
        assert record["consecutive_failures"] == 3

        rows = store.list_iterations(PHASE4_LOOP_ID)
        assert [r.iteration_index for r in rows] == [0, 1, 2]  # no fourth INTENT row
        assert all(r.iteration_state == FINALIZED for r in rows)
        assert [r.run_status for r in rows] == ["FAILED", "FAILED", "FAILED"]
        assert [r.bound_run_id for r in rows] == ids.issued
        # And no fourth underlying run was created either.
        assert sorted(r.run_id for r in ledger.list_runs(conn)) == sorted(ids.issued)
    finally:
        conn.close()


# The full four-part ADR-0010 §5 alert is NOT proven by this session.
# Parts 1-3 (structured ERROR event, durable stop_reason, nonzero exit)
# are proven above for both breakers. Part 4 is a labeled
# ITERATION_LOG.md evidence line, and that surface has not landed; its
# format belongs to the Phase-4 gate dispatch. No substitute format is
# invented here, and the definition of "failure alert" is not weakened
# in order to reach zero skips early.
@pytest.mark.skip(
    reason=(
        "FI stub — Phase 4 full alert requires ITERATION_LOG.md; "
        "activates in q77-p4-gate-a"
    )
)
def test_seeded_breaker_trip_produces_failure_alert():
    raise NotImplementedError

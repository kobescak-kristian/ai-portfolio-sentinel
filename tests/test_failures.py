"""Failure-injection suite skeletons (BLUEPRINT §3, §5, §6; ADR 0002).

Named stubs only at Phase 1 — each activates with the phase that
builds the capability it gates, and stays visibly skipped in CI until
then. Cage and no-write-access tests are NOT stubbed here: they belong
to tests/test_bounds.py, which lands at Phase 3 with the caged checker.
"""

from __future__ import annotations

import pytest

# --- Phase 2: deterministic control plane (state machine, dedup, ledger) ---


@pytest.mark.skip(reason="FI stub — activates at Phase 2: task state machine")
def test_every_task_reaches_terminal_state():
    raise NotImplementedError


@pytest.mark.skip(reason="FI stub — activates at Phase 2: task queue accounting")
def test_no_task_lost_across_a_run():
    raise NotImplementedError


@pytest.mark.skip(reason="FI stub — activates at Phase 2: dead-letter routing")
def test_failed_task_routes_to_dead_letter_atomically():
    raise NotImplementedError


@pytest.mark.skip(reason="FI stub — activates at Phase 2: crash-consistent ledger")
def test_crash_mid_run_leaves_ledger_consistent_on_rerun():
    raise NotImplementedError


@pytest.mark.skip(reason="FI stub — activates at Phase 2: idempotent re-run")
def test_idempotent_rerun_produces_no_new_findings():
    raise NotImplementedError


@pytest.mark.skip(reason="FI stub — activates at Phase 2: dedup on doubled fixtures")
def test_dedup_correct_on_doubled_fixture_run():
    raise NotImplementedError


@pytest.mark.skip(reason="FI stub — activates at Phase 2: finding lifecycle")
def test_open_finding_advances_last_seen_without_duplicate_row():
    raise NotImplementedError


@pytest.mark.skip(reason="FI stub — activates at Phase 2: finding auto-resolve")
def test_absent_finding_auto_resolves_with_dated_row():
    raise NotImplementedError


# --- Phase 3: caged checker agent ------------------------------------------


@pytest.mark.skip(reason="FI stub — activates at Phase 3: per-run cost cap")
def test_per_run_cost_cap_halts_checker():
    raise NotImplementedError


# --- Phase 4: breakers and bounded loop -------------------------------------


@pytest.mark.skip(reason="FI stub — activates at Phase 4: cost breaker (seeded)")
def test_cost_breaker_trips_on_seeded_overspend():
    raise NotImplementedError


@pytest.mark.skip(
    reason="FI stub — activates at Phase 4: consecutive-failure breaker (seeded)"
)
def test_consecutive_failure_breaker_trips_on_seeded_failures():
    raise NotImplementedError


@pytest.mark.skip(reason="FI stub — activates at Phase 4: failure alerting (seeded)")
def test_seeded_breaker_trip_produces_failure_alert():
    raise NotImplementedError

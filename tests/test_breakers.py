"""ADR-0010 breaker predicates and the frozen termination precedence.

Model-free by construction — ``runner.breakers`` imports only the
standard library, so nothing here can reach a provider even by accident.

Every boundary in ADR-0010 section 3A is pinned below as its own case.
The two that matter most, and that this file exists to defend, are the
deliberately asymmetric comparisons: post-iteration overshoot is strict
``>``, pre-start refusal is remaining ``<= 0``. Exactly 750,000
accounted is an acceptable terminal state but not a state from which
another iteration may start. Section 3A forbids normalising those into
one operator, so if a future change makes both tests pass with the same
comparison, the change is wrong, not the tests.
"""

from __future__ import annotations

import pytest

from runner.breakers import (
    COMPLETED_ITERATION_CAP,
    CONSECUTIVE_FAILURE_BREAKER_TRIPPED,
    CONSECUTIVE_FAILURE_THRESHOLD,
    COST_BREAKER_TRIPPED,
    EXIT_CODES,
    LOOP_ABORTED_ERROR,
    LOOP_BUDGET_EUR_MICROS,
    MAX_ITERATIONS,
    STOP_REASONS,
    InvalidIterationLimit,
    cost_overshot,
    effective_allowance,
    evaluate_termination,
    failure_threshold_reached,
    iteration_failed,
    next_consecutive_failures,
    pre_start_cost_refused,
    remaining_loop_budget,
    validate_max_iterations,
)

PER_RUN_CAP = 750_000


def decide(cost, *, streak=0, completed=0, n=10):
    return evaluate_termination(
        accounted_cost_eur_micros=cost,
        consecutive_failures=streak,
        iterations_completed=completed,
        max_iterations=n,
        per_run_cap_eur_micros=PER_RUN_CAP,
    )


# --- frozen constants -------------------------------------------------------


def test_frozen_adr0010_constants():
    assert LOOP_BUDGET_EUR_MICROS == 750_000
    assert CONSECUTIVE_FAILURE_THRESHOLD == 3
    assert MAX_ITERATIONS == 10


def test_stop_reason_vocabulary_is_closed_and_exit_codes_are_frozen():
    assert STOP_REASONS == {
        COMPLETED_ITERATION_CAP,
        COST_BREAKER_TRIPPED,
        CONSECUTIVE_FAILURE_BREAKER_TRIPPED,
        LOOP_ABORTED_ERROR,
    }
    assert EXIT_CODES[COMPLETED_ITERATION_CAP] == 0
    for reason in STOP_REASONS - {COMPLETED_ITERATION_CAP}:
        assert EXIT_CODES[reason] != 0


# --- section 12: iteration limit -------------------------------------------


@pytest.mark.parametrize("n", [1, 2, 9, 10])
def test_valid_iteration_limits_accepted(n):
    assert validate_max_iterations(n) == n


@pytest.mark.parametrize("n", [0, -1, 11, 100])
def test_iteration_limit_outside_1_to_10_refused(n):
    with pytest.raises(InvalidIterationLimit):
        validate_max_iterations(n)


@pytest.mark.parametrize("n", [None, "3", 3.0, True])
def test_iteration_limit_must_be_a_plain_int(n):
    with pytest.raises(InvalidIterationLimit):
        validate_max_iterations(n)


# --- section 1: the failure unit -------------------------------------------


@pytest.mark.parametrize("status", ["FAILED", "RUNNING", "", None, "completed"])
def test_any_status_other_than_completed_is_a_failed_iteration(status):
    assert iteration_failed(status) is True


def test_only_completed_is_not_a_failure():
    assert iteration_failed("COMPLETED") is False


def test_streak_increments_on_failure_and_resets_only_on_completed():
    streak = 0
    for status in ("FAILED", "FAILED"):
        streak = next_consecutive_failures(streak, status)
    assert streak == 2
    assert next_consecutive_failures(streak, "COMPLETED") == 0
    # A partially successful run is still not COMPLETED, so it does not
    # reset — section 1 is explicit that nothing but COMPLETED does.
    assert next_consecutive_failures(streak, "FAILED") == 3


@pytest.mark.parametrize(
    "streak,expected", [(0, False), (1, False), (2, False), (3, True), (4, True)]
)
def test_threshold_is_exactly_three(streak, expected):
    assert failure_threshold_reached(streak) is expected


def test_fail_fail_success_fail_fail_does_not_trip_from_stale_state():
    """ADR-0010 section 7 leg 3's reset case, at predicate level."""
    streak = 0
    for status in ("FAILED", "FAILED", "COMPLETED", "FAILED", "FAILED"):
        streak = next_consecutive_failures(streak, status)
    assert streak == 2
    assert failure_threshold_reached(streak) is False


# --- section 2: cost arithmetic --------------------------------------------


@pytest.mark.parametrize(
    "cost,expected", [(0, 750_000), (749_999, 1), (750_000, 0), (750_001, -1)]
)
def test_remaining_budget_is_never_clamped(cost, expected):
    assert remaining_loop_budget(cost) == expected


def test_overshoot_uses_strict_greater_than():
    assert cost_overshot(749_999) is False
    assert cost_overshot(750_000) is False  # exactly at the ceiling is NOT overshoot
    assert cost_overshot(750_001) is True


def test_pre_start_refusal_uses_remaining_less_than_or_equal_zero():
    assert pre_start_cost_refused(1) is False
    assert pre_start_cost_refused(0) is True  # exactly at the ceiling refuses a start
    assert pre_start_cost_refused(-1) is True


def test_the_two_comparisons_are_deliberately_asymmetric_at_the_ceiling():
    """The single case that proves section 3A's asymmetry: at exactly
    750,000 accounted, the loop may terminate normally but may NOT start
    another iteration."""
    at_ceiling = 750_000
    assert cost_overshot(at_ceiling) is False
    assert pre_start_cost_refused(remaining_loop_budget(at_ceiling)) is True


@pytest.mark.parametrize(
    "cap,remaining,expected",
    [(750_000, 750_000, 750_000), (750_000, 1, 1), (750_000, 250_000, 250_000),
     (100_000, 750_000, 100_000)],
)
def test_effective_allowance_is_the_minimum(cap, remaining, expected):
    assert effective_allowance(cap, remaining) == expected


# --- section 3 / 3A: the frozen precedence ---------------------------------


def test_n_reached_cost_749999_streak_below_three_completes():
    d = decide(749_999, streak=2, completed=10, n=10)
    assert d.stop_reason == COMPLETED_ITERATION_CAP
    assert d.exit_code == 0


def test_n_reached_cost_exactly_750000_completes():
    d = decide(750_000, streak=0, completed=10, n=10)
    assert d.stop_reason == COMPLETED_ITERATION_CAP
    assert d.exit_code == 0


def test_n_reached_cost_above_ceiling_trips_the_cost_breaker():
    d = decide(750_001, streak=0, completed=10, n=10)
    assert d.stop_reason == COST_BREAKER_TRIPPED
    assert d.exit_code != 0


def test_n_not_reached_cost_exactly_750000_refuses_the_next_iteration():
    d = decide(750_000, streak=0, completed=4, n=10)
    assert d.stop_reason == COST_BREAKER_TRIPPED
    assert d.exit_code != 0
    assert d.allowance_eur_micros is None


def test_n_not_reached_cost_above_ceiling_trips_the_cost_breaker():
    d = decide(750_001, streak=0, completed=4, n=10)
    assert d.stop_reason == COST_BREAKER_TRIPPED


def test_n_reached_within_ceiling_with_streak_three_prefers_the_failure_breaker():
    """Section 3B outranks normal iteration-cap completion."""
    d = decide(500_000, streak=3, completed=10, n=10)
    assert d.stop_reason == CONSECUTIVE_FAILURE_BREAKER_TRIPPED
    assert d.exit_code != 0


def test_overshoot_outranks_the_failure_breaker():
    """Section 3A: overshoot is leg A, the failure streak is leg B."""
    d = decide(800_000, streak=3, completed=10, n=10)
    assert d.stop_reason == COST_BREAKER_TRIPPED


def test_continue_carries_the_reduced_allowance():
    d = decide(500_000, streak=0, completed=2, n=10)
    assert d.stop_reason is None
    assert d.should_stop is False
    assert d.allowance_eur_micros == 250_000  # min(750_000, 750_000 - 500_000)


def test_continue_at_full_budget_is_capped_by_the_per_run_cap_not_raised_by_it():
    d = decide(0, streak=0, completed=0, n=10)
    assert d.allowance_eur_micros == PER_RUN_CAP
    assert d.allowance_eur_micros <= LOOP_BUDGET_EUR_MICROS


def test_a_stopping_decision_never_carries_an_allowance():
    for cost, streak, completed in [(750_001, 0, 1), (0, 3, 1), (0, 0, 10), (750_000, 0, 1)]:
        d = decide(cost, streak=streak, completed=completed, n=10)
        assert d.should_stop is True
        assert d.allowance_eur_micros is None

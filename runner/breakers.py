"""Pure ADR-0010 breaker predicates and the frozen termination order.

Domain-free by construction: this module imports nothing but the
standard library. It knows about integers, a failure streak and a
closed vocabulary of stop reasons — it does not know that a "run" is a
Sentinel run, and it must never learn.

Everything here is a restatement of
``adr/0010-phase4-loop-safety-controls.md`` sections 1, 2, 3 and 6.
Two details in section 3 are load-bearing and are called out where they
appear, because both are the kind of thing a later reader tidies up
without realising it is a decision:

* post-iteration overshoot uses strict ``>``;
* pre-start refusal uses remaining ``<= 0``.

Exactly 750,000 accounted is therefore an acceptable terminal state but
not a state from which another iteration may start. ADR-0010 section 3A
forbids normalising these into one comparison operator.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- frozen ADR-0010 constants ---------------------------------------------

# ADR-0010 section 2. A real pre-start loop ceiling, not an
# after-the-fact acceptance metric. No CLI flag, configuration value or
# environment variable may raise it; any operation above it requires a
# separate dated owner-governed decision, and ADR-0010 pre-authorises
# none. It deliberately does NOT replace or raise the unchanged EUR 0.75
# per-run cap.
LOOP_BUDGET_EUR_MICROS = 750_000

# ADR-0010 section 1. The breaker trips at exactly three consecutive
# failed iterations, scoped to one loop_id.
CONSECUTIVE_FAILURE_THRESHOLD = 3

# BLUEPRINT section 6 P4 / ADR-0010 section 7 leg 1: N <= 10.
MIN_ITERATIONS = 1
MAX_ITERATIONS = 10

# --- closed stop-reason vocabulary (ADR-0010 section 6) --------------------

COMPLETED_ITERATION_CAP = "COMPLETED_ITERATION_CAP"
COST_BREAKER_TRIPPED = "COST_BREAKER_TRIPPED"
CONSECUTIVE_FAILURE_BREAKER_TRIPPED = "CONSECUTIVE_FAILURE_BREAKER_TRIPPED"
LOOP_ABORTED_ERROR = "LOOP_ABORTED_ERROR"

STOP_REASONS: frozenset[str] = frozenset(
    {
        COMPLETED_ITERATION_CAP,
        COST_BREAKER_TRIPPED,
        CONSECUTIVE_FAILURE_BREAKER_TRIPPED,
        LOOP_ABORTED_ERROR,
    }
)

# COMPLETED_ITERATION_CAP is normal completion and exits 0. The other
# three are abnormal / fail-closed and exit nonzero.
EXIT_CODES: dict[str, int] = {
    COMPLETED_ITERATION_CAP: 0,
    COST_BREAKER_TRIPPED: 1,
    CONSECUTIVE_FAILURE_BREAKER_TRIPPED: 1,
    LOOP_ABORTED_ERROR: 1,
}

# The one run status that is not a loop-iteration failure (section 1).
RUN_STATUS_COMPLETED = "COMPLETED"


class InvalidIterationLimit(ValueError):
    """``N`` is outside ``1 <= N <= 10``. Raised before any loop intent,
    iteration intent or underlying run exists."""


def validate_max_iterations(max_iterations: int) -> int:
    """Enforce ``1 <= N <= 10`` BEFORE any durable work. Called first by
    the supervisor, so an illegal N can never reach a loop row, an
    iteration intent or a Sentinel run."""
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool):
        raise InvalidIterationLimit(f"max_iterations must be an int, got {max_iterations!r}")
    if not (MIN_ITERATIONS <= max_iterations <= MAX_ITERATIONS):
        raise InvalidIterationLimit(
            f"max_iterations must satisfy {MIN_ITERATIONS} <= N <= {MAX_ITERATIONS}, "
            f"got {max_iterations}"
        )
    return max_iterations


# --- section 1: the failure unit and the streak ----------------------------


def iteration_failed(run_status: str | None) -> bool:
    """ADR-0010 section 1: an iteration failed if and only if its
    underlying run's final status is not ``COMPLETED``. A dead-lettered
    task, a failed agent_call, an ADR-0008 first attempt, an HTTP retry
    and a tool breaker event are sub-run mechanisms and are NOT counted
    here — they are governed by their own ADRs."""
    return run_status != RUN_STATUS_COMPLETED


def next_consecutive_failures(current: int, run_status: str | None) -> int:
    """Only a ``COMPLETED`` iteration resets the streak to zero. Nothing
    else resets it — not time, not a new scheduler fire, not an operator
    restart, not a partially successful run."""
    return current + 1 if iteration_failed(run_status) else 0


def failure_threshold_reached(
    consecutive_failures: int, threshold: int = CONSECUTIVE_FAILURE_THRESHOLD
) -> bool:
    return consecutive_failures >= threshold


# --- section 2: loop cost arithmetic ---------------------------------------


def remaining_loop_budget(
    accounted_cost_eur_micros: int, ceiling_eur_micros: int = LOOP_BUDGET_EUR_MICROS
) -> int:
    """May be zero or negative. A negative figure is truthful accounted
    overshoot (never clamped), not permission for further spend."""
    return ceiling_eur_micros - accounted_cost_eur_micros


def cost_overshot(
    accounted_cost_eur_micros: int, ceiling_eur_micros: int = LOOP_BUDGET_EUR_MICROS
) -> bool:
    """Post-iteration test. Strict ``>`` — see the module docstring."""
    return accounted_cost_eur_micros > ceiling_eur_micros


def pre_start_cost_refused(remaining: int) -> bool:
    """Pre-start test. ``<= 0`` — see the module docstring."""
    return remaining <= 0


def effective_allowance(per_run_cap_eur_micros: int, remaining: int) -> int:
    """ADR-0010 section 2. The reduced allowance must be propagated
    downward into the existing run/model budget mechanism; the normal
    EUR 0.75 allowance is never silently restored."""
    return min(per_run_cap_eur_micros, remaining)


# --- section 3: the frozen termination precedence --------------------------


@dataclass(frozen=True)
class TerminationDecision:
    """Either stop with exactly one terminal reason, or continue with a
    concrete allowance for the next iteration. Never both."""

    stop_reason: str | None
    exit_code: int | None
    allowance_eur_micros: int | None

    @property
    def should_stop(self) -> bool:
        return self.stop_reason is not None


def _stop(reason: str) -> TerminationDecision:
    return TerminationDecision(
        stop_reason=reason, exit_code=EXIT_CODES[reason], allowance_eur_micros=None
    )


def evaluate_termination(
    *,
    accounted_cost_eur_micros: int,
    consecutive_failures: int,
    iterations_completed: int,
    max_iterations: int,
    per_run_cap_eur_micros: int,
    loop_ceiling_eur_micros: int = LOOP_BUDGET_EUR_MICROS,
    failure_threshold: int = CONSECUTIVE_FAILURE_THRESHOLD,
) -> TerminationDecision:
    """ADR-0010 section 3, in this exact order. Evaluated after every
    finalized or adopted iteration, and again before any iteration would
    start — which is the same evaluation, because A/B/C are the
    post-iteration legs and D/E are the pre-start legs of one frozen
    sequence.

    The section-3A boundary consequences follow directly:

    * N reached, cost 749999, streak < 3  -> COMPLETED_ITERATION_CAP
    * N reached, cost exactly 750000      -> COMPLETED_ITERATION_CAP
    * N reached, cost above 750000        -> COST_BREAKER_TRIPPED
    * N not reached, cost exactly 750000  -> COST_BREAKER_TRIPPED (D)
    * N not reached, cost above 750000    -> COST_BREAKER_TRIPPED (A)
    * N reached, cost <= 750000, streak 3 -> CONSECUTIVE_FAILURE_BREAKER_TRIPPED
    """
    # A. Accounted overshoot. Reaching N cannot hide it.
    if cost_overshot(accounted_cost_eur_micros, loop_ceiling_eur_micros):
        return _stop(COST_BREAKER_TRIPPED)

    # B. Consecutive-failure threshold. Outranks normal completion.
    if failure_threshold_reached(consecutive_failures, failure_threshold):
        return _stop(CONSECUTIVE_FAILURE_BREAKER_TRIPPED)

    # C. Normal iteration-cap completion. Valid only when cost is within
    #    the ceiling and the streak is below the threshold — both of
    #    which are already true here, because A and B did not fire.
    if iterations_completed >= max_iterations:
        return _stop(COMPLETED_ITERATION_CAP)

    # D. Pre-start cost refusal — reached only because another iteration
    #    would otherwise start.
    remaining = remaining_loop_budget(accounted_cost_eur_micros, loop_ceiling_eur_micros)
    if pre_start_cost_refused(remaining):
        return _stop(COST_BREAKER_TRIPPED)

    # E. Continue, with the reduced allowance.
    return TerminationDecision(
        stop_reason=None,
        exit_code=None,
        allowance_eur_micros=effective_allowance(per_run_cap_eur_micros, remaining),
    )

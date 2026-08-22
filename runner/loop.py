"""The ADR-0010 bounded-loop supervisor. Domain-free by construction.

This module imports nothing but the standard library and
``runner.breakers``. It MUST NOT import ``sentinel.*``, ``checks.*``,
``agents.*`` or ``telemetry.*`` — ``tests/test_read_only_boundary.py``
enforces that statically. It supervises "iterations"; it does not know
that an iteration is a Sentinel run. ``runner/sentinel_adapter.py`` is
the one place where that binding happens.

This is deliberately NOT a second Sentinel pipeline. It owns exactly
four things:

1. iteration control (how many, in what order, may another start);
2. breaker evaluation, in ADR-0010 section 3's frozen order;
3. the durable-intent-before-work ordering of section 4;
4. recovery orchestration for cases A-D, and one terminal stop reason.

Everything else — building a run configuration, executing it, reading
durable cost, constructing a reduced budget — is behind the
``IterationExecutor`` protocol and belongs to the adapter.

**Ordering contract.** For iteration *k* the ``planned_run_id`` is
generated once, persisted as an INTENT row and COMMITTED before the
iteration callable may execute. A crash after that commit but before the
run starts must not mint a second ``planned_run_id``; a crash after the
underlying run is terminal but before loop finalization must adopt the
existing run rather than repeat it. Both are section 4 invariants, and
both are the reason the state store is asked to commit at a point the
supervisor controls rather than at the end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, Protocol, Sequence

from runner.breakers import (
    CONSECUTIVE_FAILURE_THRESHOLD,
    COST_BREAKER_TRIPPED,
    EXIT_CODES,
    LOOP_ABORTED_ERROR,
    LOOP_BUDGET_EUR_MICROS,
    TerminationDecision,
    effective_allowance,
    evaluate_termination,
    iteration_failed,
    next_consecutive_failures,
    pre_start_cost_refused,
    remaining_loop_budget,
    validate_max_iterations,
)

INTENT = "INTENT"
FINALIZED = "FINALIZED"


# --- injected seams --------------------------------------------------------


@dataclass(frozen=True)
class RunProbe:
    """What the executor can durably say about a ``planned_run_id``
    without starting anything. ``status`` is ``None`` when no run row
    exists at all (recovery case A), ``"RUNNING"`` for an interrupted run
    (case C), and a terminal status otherwise (cases B and D)."""

    status: Optional[str]
    outputs_complete: bool = True


class IterationExecutor(Protocol):
    """The whole domain boundary, in five methods."""

    def probe(self, planned_run_id: str) -> RunProbe:
        """Durable, side-effect-free inspection of one planned run."""

    def execute(self, planned_run_id: str, *, allowance_eur_micros: int) -> str:
        """Run iteration work under the reduced allowance, using EXACTLY
        this ``planned_run_id``. Returns the terminal run status."""

    def recover_interrupted(self, planned_run_id: str) -> str:
        """Recovery case C: drive an existing RUNNING run to a terminal
        state through the domain's own interrupted-run recovery. Never
        creates a replacement run. Returns the terminal run status."""

    def reconcile_outputs(self, planned_run_id: str) -> None:
        """Recovery case D: a terminal run exists but its derived
        outputs are incomplete. Reconcile them. Never reruns the
        iteration and never invents a second cost source."""

    def accounted_cost(self, run_ids: Sequence[str]) -> int:
        """Durable accounted consumption, in EUR micros, for these run
        ids. ADR-0010 section 2: reconstructed from durable cost
        records, never from a volatile in-memory counter."""


@dataclass(frozen=True)
class IterationRow:
    loop_id: str
    iteration_index: int
    planned_run_id: str
    iteration_state: str
    bound_run_id: Optional[str] = None
    run_status: Optional[str] = None


@dataclass(frozen=True)
class LoopSummary:
    iterations_started: int
    iterations_completed: int
    consecutive_failures: int
    accounted_cost_eur_micros: int


class LoopStateStore(Protocol):
    """Durable loop state. Every method commits before returning — the
    supervisor relies on that for the section-4 invariant."""

    def begin_or_load_loop(
        self,
        *,
        loop_id: str,
        max_iterations: int,
        loop_budget_eur_micros: int,
        failure_threshold: int,
        now: datetime,
    ) -> bool:
        """Create the loop row if absent. Returns True if this call
        created it, False if an existing loop is being resumed."""

    def list_iterations(self, loop_id: str) -> list[IterationRow]:
        """Every iteration row for the loop, ordered by index."""

    def record_intent(
        self, *, loop_id: str, iteration_index: int, planned_run_id: str, now: datetime
    ) -> IterationRow:
        """Persist and COMMIT the durable intent. Nothing may execute
        for this iteration until this call has returned."""

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
        summary, in one transaction."""

    def finish_loop(
        self, *, loop_id: str, stop_reason: str, summary: LoopSummary, now: datetime
    ) -> None:
        """Write exactly one terminal stop reason for the loop."""


class LoopLogger(Protocol):
    def log(self, severity: str, event: str, *, now: datetime, **fields: object) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class RunIdFactory(Protocol):
    def new_run_id(self) -> str: ...


@dataclass(frozen=True)
class LoopHooks:
    """Loop-level fault-injection seams. Each defaults to a no-op.

    ``after_run_terminal_before_finalize`` is ADR-0010 section 7 leg 4's
    primary injected seam: the underlying run is terminal and durable,
    but loop iteration finalization is not yet committed. It lives here,
    at the supervisor, precisely so the seam can be exercised WITHOUT
    modifying the domain pipeline to create it.

    ``after_iteration_intent`` is the other half of the section-4 story:
    the intent is committed but no run has started yet. Crashing there
    must not mint a second ``planned_run_id``."""

    after_iteration_intent: Optional[Callable[[str, int, str], None]] = None
    after_run_terminal_before_finalize: Optional[Callable[[str, int, str], None]] = None


@dataclass(frozen=True)
class LoopConfig:
    loop_id: str
    max_iterations: int
    # The existing per-run cap. Injected rather than imported, because
    # this module is domain-free — the adapter supplies the real value.
    per_run_cap_eur_micros: int
    loop_budget_eur_micros: int = LOOP_BUDGET_EUR_MICROS
    failure_threshold: int = CONSECUTIVE_FAILURE_THRESHOLD


@dataclass(frozen=True)
class LoopOutcome:
    loop_id: str
    stop_reason: str
    exit_code: int
    iterations_started: int
    iterations_completed: int
    consecutive_failures: int
    accounted_cost_eur_micros: int


@dataclass
class _Deps:
    store: LoopStateStore
    executor: IterationExecutor
    clock: Clock
    ids: RunIdFactory
    logger: LoopLogger
    hooks: LoopHooks = field(default_factory=LoopHooks)


# --- state reconstruction --------------------------------------------------


def derive_consecutive_failures(iterations: Sequence[IterationRow]) -> int:
    """The trailing run of finalized iterations whose run status is not
    ``COMPLETED``. Reconstructed from durable rows rather than carried
    in memory, so it survives a crash and resume of the same loop
    exactly as ADR-0010 section 1 requires."""
    streak = 0
    for row in reversed([r for r in iterations if r.iteration_state == FINALIZED]):
        if not iteration_failed(row.run_status):
            break
        streak += 1
    return streak


def _finalized(iterations: Sequence[IterationRow]) -> list[IterationRow]:
    return [r for r in iterations if r.iteration_state == FINALIZED]


def _first_unfinalized(iterations: Sequence[IterationRow]) -> Optional[IterationRow]:
    """Earlier unfinished iteration indexes must be reconciled before
    any later new iteration starts, so this always returns the LOWEST
    unfinalized index."""
    for row in iterations:
        if row.iteration_state != FINALIZED:
            return row
    return None


def _summarize(
    iterations: Sequence[IterationRow], accounted_cost_eur_micros: int
) -> LoopSummary:
    finalized = _finalized(iterations)
    return LoopSummary(
        iterations_started=len(iterations),
        # A finalized iteration counts toward N whatever its run status:
        # otherwise a loop of failures could never reach N and would
        # spin forever instead of tripping the failure breaker.
        iterations_completed=len(finalized),
        consecutive_failures=derive_consecutive_failures(iterations),
        accounted_cost_eur_micros=accounted_cost_eur_micros,
    )


def _bound_run_ids(iterations: Sequence[IterationRow]) -> list[str]:
    return [r.bound_run_id for r in _finalized(iterations) if r.bound_run_id]


# --- the supervisor --------------------------------------------------------


class _AbortLoop(Exception):
    """Internal: stop now with this reason, persisting terminal state."""

    def __init__(self, stop_reason: str):
        super().__init__(stop_reason)
        self.stop_reason = stop_reason


def run_loop(
    config: LoopConfig,
    *,
    store: LoopStateStore,
    executor: IterationExecutor,
    clock: Clock,
    ids: RunIdFactory,
    logger: LoopLogger,
    hooks: LoopHooks = LoopHooks(),
) -> LoopOutcome:
    """Execute one bounded loop to exactly one terminal stop reason."""
    # BEFORE any loop intent, any iteration intent and any underlying
    # run. An illegal N never reaches durable state at all.
    validate_max_iterations(config.max_iterations)

    deps = _Deps(
        store=store, executor=executor, clock=clock, ids=ids, logger=logger, hooks=hooks
    )
    created = store.begin_or_load_loop(
        loop_id=config.loop_id,
        max_iterations=config.max_iterations,
        loop_budget_eur_micros=config.loop_budget_eur_micros,
        failure_threshold=config.failure_threshold,
        now=clock.now(),
    )
    logger.log(
        "INFO",
        "loop.started",
        now=clock.now(),
        loop_id=config.loop_id,
        max_iterations=config.max_iterations,
        resumed=not created,
    )

    try:
        stop_reason = _supervise(config, deps)
    except _AbortLoop as abort:
        stop_reason = abort.stop_reason
    except Exception as exc:  # noqa: BLE001 - fail closed, then re-report
        # Unexpected supervisor error. Fail closed to LOOP_ABORTED_ERROR
        # and still write durable terminal state, so the loop has
        # exactly one authoritative stop reason rather than none.
        logger.log(
            "ERROR",
            "loop.failed",
            now=clock.now(),
            loop_id=config.loop_id,
            stop_reason=LOOP_ABORTED_ERROR,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return _finish(config, deps, LOOP_ABORTED_ERROR, already_logged=True)

    return _finish(config, deps, stop_reason)


def _supervise(config: LoopConfig, deps: _Deps) -> str:
    while True:
        iterations = deps.store.list_iterations(config.loop_id)
        pending = _first_unfinalized(iterations)

        if pending is None:
            decision = _decide(config, deps, iterations)
            if decision.should_stop:
                assert decision.stop_reason is not None
                return decision.stop_reason
            assert decision.allowance_eur_micros is not None
            pending = _open_intent(config, deps, iterations)
            run_status = deps.executor.execute(
                pending.planned_run_id, allowance_eur_micros=decision.allowance_eur_micros
            )
        else:
            run_status = _reconcile(config, deps, iterations, pending)

        _finalize(config, deps, pending, run_status)


def _decide(
    config: LoopConfig, deps: _Deps, iterations: Sequence[IterationRow]
) -> TerminationDecision:
    """One evaluation of ADR-0010 section 3's frozen A-E order, using
    durably reconstructed cost and streak state."""
    accounted = deps.executor.accounted_cost(_bound_run_ids(iterations))
    summary = _summarize(iterations, accounted)
    decision = evaluate_termination(
        accounted_cost_eur_micros=summary.accounted_cost_eur_micros,
        consecutive_failures=summary.consecutive_failures,
        iterations_completed=summary.iterations_completed,
        max_iterations=config.max_iterations,
        per_run_cap_eur_micros=config.per_run_cap_eur_micros,
        loop_ceiling_eur_micros=config.loop_budget_eur_micros,
        failure_threshold=config.failure_threshold,
    )
    if decision.should_stop:
        _log_stop(config, deps, decision.stop_reason, summary)
    return decision


def _log_stop(
    config: LoopConfig, deps: _Deps, stop_reason: str | None, summary: LoopSummary
) -> None:
    """ADR-0010 section 5 part 1: a structured ERROR-severity event from
    the closed logging vocabulary. Parts 2 and 3 (a durable stop_reason
    and a nonzero exit) are produced by ``_finish``. Part 4, the labeled
    ITERATION_LOG evidence line, is NOT produced in this session — so
    the four-part contract is not yet proven."""
    if stop_reason == COST_BREAKER_TRIPPED:
        deps.logger.log(
            "ERROR",
            "breaker.cost_tripped",
            now=deps.clock.now(),
            loop_id=config.loop_id,
            accounted_cost_eur_micros=summary.accounted_cost_eur_micros,
            loop_budget_eur_micros=config.loop_budget_eur_micros,
        )
    elif stop_reason and stop_reason.startswith("CONSECUTIVE_FAILURE"):
        deps.logger.log(
            "ERROR",
            "breaker.consecutive_failure_tripped",
            now=deps.clock.now(),
            loop_id=config.loop_id,
            consecutive_failures=summary.consecutive_failures,
            failure_threshold=config.failure_threshold,
        )


def _open_intent(
    config: LoopConfig, deps: _Deps, iterations: Sequence[IterationRow]
) -> IterationRow:
    """Section 4, steps 1-3: generate the ``planned_run_id`` exactly
    once, persist it, and COMMIT — before anything may execute."""
    index = len(iterations)
    planned_run_id = deps.ids.new_run_id()
    row = deps.store.record_intent(
        loop_id=config.loop_id,
        iteration_index=index,
        planned_run_id=planned_run_id,
        now=deps.clock.now(),
    )
    deps.logger.log(
        "INFO",
        "loop.iteration_intent",
        now=deps.clock.now(),
        loop_id=config.loop_id,
        iteration_index=index,
        run_id=planned_run_id,
    )
    if deps.hooks.after_iteration_intent:
        deps.hooks.after_iteration_intent(config.loop_id, index, planned_run_id)
    return row


def _reconcile(
    config: LoopConfig,
    deps: _Deps,
    iterations: Sequence[IterationRow],
    pending: IterationRow,
) -> str:
    """ADR-0010 section 4 recovery, cases A-D, always using the stored
    ``planned_run_id``. The section-4 invariant is that a terminal
    underlying run is NEVER repeated merely because loop bookkeeping
    crashed after run finalization."""
    probe = deps.executor.probe(pending.planned_run_id)
    deps.logger.log(
        "WARNING",
        "loop.recovered",
        now=deps.clock.now(),
        loop_id=config.loop_id,
        iteration_index=pending.iteration_index,
        run_id=pending.planned_run_id,
        probed_status=probe.status or "NONE",
    )

    # C. A RUNNING run exists: use the domain's own interrupted-run
    #    recovery. Never invent a replacement run id; the recovered
    #    terminal run IS this iteration's result.
    if probe.status == "RUNNING":
        return deps.executor.recover_interrupted(pending.planned_run_id)

    # A. No run row exists: the run may start ONCE, with the SAME
    #    planned_run_id. The failure-streak breaker is deliberately not
    #    consulted here — this iteration's intent is already committed,
    #    so it is an iteration already in progress, and section 1 says a
    #    breaker refuses the NEXT iteration rather than aborting one
    #    underway. The hard cost ceiling still applies, because no run
    #    has started and section 2's ceiling is a real pre-start gate.
    if probe.status is None:
        accounted = deps.executor.accounted_cost(_bound_run_ids(iterations))
        remaining = remaining_loop_budget(accounted, config.loop_budget_eur_micros)
        if pre_start_cost_refused(remaining):
            _log_stop(
                config, deps, COST_BREAKER_TRIPPED, _summarize(iterations, accounted)
            )
            raise _AbortLoop(COST_BREAKER_TRIPPED)
        return deps.executor.execute(
            pending.planned_run_id,
            allowance_eur_micros=effective_allowance(
                config.per_run_cap_eur_micros, remaining
            ),
        )

    # D. A terminal run exists but its derived outputs are incomplete:
    #    reconcile them first. No rerun, no second cost source.
    if not probe.outputs_complete:
        deps.executor.reconcile_outputs(pending.planned_run_id)

    # B. Adopt the existing terminal run. execute() is NOT called.
    return probe.status


def _finalize(
    config: LoopConfig, deps: _Deps, pending: IterationRow, run_status: str
) -> None:
    # Section 7 leg 4's seam: the underlying run is terminal and
    # durable, loop finalization is not yet committed.
    if deps.hooks.after_run_terminal_before_finalize:
        deps.hooks.after_run_terminal_before_finalize(
            config.loop_id, pending.iteration_index, pending.planned_run_id
        )

    iterations = deps.store.list_iterations(config.loop_id)
    prior = [r for r in iterations if r.iteration_index < pending.iteration_index]
    streak_after = next_consecutive_failures(
        derive_consecutive_failures(prior), run_status
    )

    projected = [
        r
        if r.iteration_index != pending.iteration_index
        else IterationRow(
            loop_id=r.loop_id,
            iteration_index=r.iteration_index,
            planned_run_id=r.planned_run_id,
            iteration_state=FINALIZED,
            bound_run_id=pending.planned_run_id,
            run_status=run_status,
        )
        for r in iterations
    ]
    accounted = deps.executor.accounted_cost(_bound_run_ids(projected))
    iteration_cost = deps.executor.accounted_cost([pending.planned_run_id])

    deps.store.finalize_iteration(
        loop_id=config.loop_id,
        iteration_index=pending.iteration_index,
        bound_run_id=pending.planned_run_id,
        run_status=run_status,
        accounted_cost_eur_micros=iteration_cost,
        consecutive_failures_after=streak_after,
        summary=_summarize(projected, accounted),
        now=deps.clock.now(),
    )
    deps.logger.log(
        "INFO" if not iteration_failed(run_status) else "ERROR",
        "loop.iteration_finalized",
        now=deps.clock.now(),
        loop_id=config.loop_id,
        iteration_index=pending.iteration_index,
        run_id=pending.planned_run_id,
        run_status=run_status,
        consecutive_failures=streak_after,
        accounted_cost_eur_micros=accounted,
    )


def _finish(
    config: LoopConfig, deps: _Deps, stop_reason: str, *, already_logged: bool = False
) -> LoopOutcome:
    iterations = deps.store.list_iterations(config.loop_id)
    try:
        accounted = deps.executor.accounted_cost(_bound_run_ids(iterations))
    except Exception:  # noqa: BLE001 - a cost read must never hide the stop reason
        accounted = 0
    summary = _summarize(iterations, accounted)
    deps.store.finish_loop(
        loop_id=config.loop_id,
        stop_reason=stop_reason,
        summary=summary,
        now=deps.clock.now(),
    )
    exit_code = EXIT_CODES[stop_reason]
    if not already_logged:
        deps.logger.log(
            "INFO" if exit_code == 0 else "ERROR",
            "loop.completed" if exit_code == 0 else "loop.failed",
            now=deps.clock.now(),
            loop_id=config.loop_id,
            stop_reason=stop_reason,
            iterations_completed=summary.iterations_completed,
            accounted_cost_eur_micros=summary.accounted_cost_eur_micros,
        )
    return LoopOutcome(
        loop_id=config.loop_id,
        stop_reason=stop_reason,
        exit_code=exit_code,
        iterations_started=summary.iterations_started,
        iterations_completed=summary.iterations_completed,
        consecutive_failures=summary.consecutive_failures,
        accounted_cost_eur_micros=summary.accounted_cost_eur_micros,
    )

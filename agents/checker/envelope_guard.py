"""P5-D per-logical-invocation deadline guard at the ``query_fn`` seam
(ADR-0012 section 8, Amendment A4; dispatch
q77-p5d-repair-stage2c2-implement-a, Stage 2C-2).

Library only: nothing in ``scripts/`` wraps a real gate ``query_fn`` with
this in Stage 2C-2; that wiring is Stage 2C-3.

``deadline_guarded`` wraps the full ``query_fn(check_class, reservation,
state, user_prompt, model)`` call, including every internal SDK and tool
turn. It stays a coroutine function, so ``CagedCheckerStub._invoke``
keeps its existing ``anyio.run`` path and ``harness.py`` is unchanged.

Before EVERY provider start, under the shared safety-domain lock: a
latched cause refuses; an expired session trips ``SESSION_DEADLINE`` and
refuses; otherwise the budget is ``min(remaining_session_ms,
stall_budget_ms)`` and the invocation is registered.

Outcome classification (exactly one registry finish per invocation):

- this scope's own deadline fired -> ``TIMED_OUT``. The key is
  ``scope.cancel_called``: only this scope's deadline sets it (the guard
  never calls ``cancel()`` and never exposes the scope), and an outer
  scope's cancellation never sets it. It also covers a callee that
  swallowed our cancellation and returned, or replaced it with an
  ordinary ``Exception`` during cleanup;
- an ordinary ``Exception`` before any deadline (including an
  SDK-originated ``TimeoutError``) -> ``RAISED``, re-raised unchanged;
- an external ``BaseException`` or outer cancellation -> ``RAISED``,
  re-raised unchanged, and never converted into a Stage-2C cause.

``move_on_after`` is used rather than ``fail_after`` so a ``TimeoutError``
raised by the SDK itself is never mistaken for the envelope deadline.

Cause on timeout: ``SESSION_DEADLINE`` when ``remaining_session_ms <=
stall_budget_ms`` at start (at equality the session deadline is binding,
per A4) or when the session has expired by the time the deadline fires;
otherwise ``INVOCATION_STALL_DEADLINE``. The first cause wins, and
``OBJECTIVE_CAUSE_LATCHED`` is journaled only by the trip that won. The
provider descendant tree is then terminated; survivors or a termination
fault are reported through ``on_control_failure`` (fail closed). The
raised ``InvocationDeadlineExceeded`` is an ordinary ``RuntimeError`` the
harness classifies as a non-retryable transport failure, so no timeout
can ever become an ``SDK_BUDGET_CEILING`` retry.
"""

from __future__ import annotations

import functools
import inspect
import itertools
from typing import Callable

import anyio

from sentinel.phase5.execution_control import (
    STAGE2C_CAUSES,
    ExecutionControlConfig,
    InvocationRegistry,
    SessionLatch,
)
from sentinel.phase5.execution_envelope import SessionClock, invocation_budget_ms


class EnvelopeGuardError(ValueError):
    """The guard was constructed with invalid arguments."""


class ProviderStartRefused(RuntimeError):
    """No provider work was started: a Stage-2C cause is latched or the
    session deadline has passed. Carries only the closed-vocabulary cause."""

    def __init__(self, cause: str) -> None:
        self.cause = cause
        super().__init__(f"ProviderStartRefused: cause={cause}")


class InvocationDeadlineExceeded(RuntimeError):
    """The guarded invocation reached its deadline. Carries only the
    closed-vocabulary latched cause."""

    def __init__(self, cause: str) -> None:
        self.cause = cause
        super().__init__(f"InvocationDeadlineExceeded: cause={cause}")


def deadline_guarded(
    query_fn: Callable,
    *,
    run_ordinal: int,
    clock: SessionClock,
    latch: SessionLatch,
    registry: InvocationRegistry,
    journal,
    stall_budget_ms: int,
    config: ExecutionControlConfig,
    terminate: Callable[[ExecutionControlConfig], object],
    on_control_failure: Callable[[], None],
) -> Callable:
    if not inspect.iscoroutinefunction(query_fn):
        raise EnvelopeGuardError("the guarded query_fn must be a coroutine function")
    if isinstance(run_ordinal, bool) or run_ordinal not in (1, 2):
        raise EnvelopeGuardError("run_ordinal must be 1 or 2")
    if isinstance(stall_budget_ms, bool) or not isinstance(stall_budget_ms, int) or stall_budget_ms < 1:
        raise EnvelopeGuardError("stall_budget_ms must be a positive integer")
    if not isinstance(clock, SessionClock):
        raise EnvelopeGuardError("a SessionClock is required")
    if not isinstance(latch, SessionLatch):
        raise EnvelopeGuardError("a SessionLatch is required")
    if not isinstance(registry, InvocationRegistry) or registry.domain is not latch.domain:
        raise EnvelopeGuardError("the registry and latch must share one ExecutionSafetyDomain")
    if not isinstance(config, ExecutionControlConfig):
        raise EnvelopeGuardError("an ExecutionControlConfig is required")
    if not callable(terminate) or not callable(on_control_failure):
        raise EnvelopeGuardError("terminate and on_control_failure must be callable")

    domain = latch.domain
    ordinals = itertools.count(1)

    @functools.wraps(query_fn)
    async def guarded(check_class, reservation, state, user_prompt, model=None):
        refuse_cause = None
        won = False
        budget_ms = 0
        session_bound = False
        ordinal = 0
        with domain.lock:
            if latch.cause is not None:
                refuse_cause = latch.cause
            else:
                remaining = clock.remaining_ms()
                if remaining <= 0:
                    won = latch.trip("SESSION_DEADLINE", clock.monotonic_now())
                    refuse_cause = latch.cause
                else:
                    budget_ms = invocation_budget_ms(remaining, stall_budget_ms)
                    session_bound = remaining <= stall_budget_ms
                    ordinal = next(ordinals)
                    registry.start(
                        run_ordinal=run_ordinal, invocation_ordinal=ordinal,
                        budget_ms=budget_ms, at_monotonic=clock.monotonic_now(),
                    )
        if refuse_cause is not None:
            if won:
                journal.append("OBJECTIVE_CAUSE_LATCHED", cause="SESSION_DEADLINE")
            raise ProviderStartRefused(refuse_cause)
        journal.append("INVOCATION_STARTED", run_ordinal=run_ordinal, invocation_ordinal=ordinal)

        def finish(outcome: str) -> None:
            registry.finish(
                run_ordinal=run_ordinal, invocation_ordinal=ordinal,
                outcome=outcome, at_monotonic=clock.monotonic_now(),
            )
            journal.append(
                "INVOCATION_FINISHED", run_ordinal=run_ordinal,
                invocation_ordinal=ordinal, invocation_outcome=outcome,
            )

        scope = None
        deadline_exc: Exception | None = None
        result = None
        try:
            with anyio.move_on_after(budget_ms / 1000.0) as scope:
                result = await query_fn(check_class, reservation, state, user_prompt, model)
        except BaseException as exc:
            if isinstance(exc, Exception) and scope is not None and scope.cancel_called:
                deadline_exc = exc  # our deadline already fired; cleanup replaced its cancellation
            else:
                finish("RAISED")
                raise

        if deadline_exc is None and not scope.cancel_called:
            finish("RETURNED")
            return result

        cause = "SESSION_DEADLINE" if (session_bound or clock.expired()) else "INVOCATION_STALL_DEADLINE"
        if latch.trip(cause, clock.monotonic_now()):
            journal.append("OBJECTIVE_CAUSE_LATCHED", cause=cause)
        control_failed = False
        try:
            report = terminate(config)
            control_failed = getattr(report, "survivors", 1) != 0
        except Exception:  # noqa: BLE001 - a termination fault fails closed
            control_failed = True
        if control_failed:
            try:
                on_control_failure()
            except Exception:  # noqa: BLE001 - the hook must never mask the timeout
                pass
        finish("TIMED_OUT")
        latched = latch.cause
        raise InvocationDeadlineExceeded(latched if latched in STAGE2C_CAUSES else cause) from deadline_exc

    return guarded

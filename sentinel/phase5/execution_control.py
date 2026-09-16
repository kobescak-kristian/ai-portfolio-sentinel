"""P5-D execution safety domain, session latch and terminal arbiter
(ADR-0012 section 8, Amendment A2/A6; dispatch
q77-p5d-repair-stage2c1-implement-a, Stage 2C-1).

Pure in-process synchronization machinery only. ``ExecutionSafetyDomain``
owns the ONE ``threading.RLock`` in this module; the session latch, the
terminal arbiter and the invocation registry are all constructed on the
same domain, so a latch trip, a terminal commit and an invocation
bookkeeping step can never interleave. No thread is started here, no
file is written, no signal is sent, no process is inspected or killed,
and no provider is called.

``SessionLatch`` is session-local: it is not the later durable,
permanent single-attempt consumption latch. Only the three Stage-2C
causes may trip it; the first cause wins atomically and a later cause
never replaces the cause or the trip time.

``TerminalArbiter`` linearizes the deadline-versus-quality race at the
atomic replace. Its ``commit_quality`` / ``commit_invalid`` commit
points take an injected ``replace`` callable; in Stage 2C-1 those are
model-free test seams only and are NOT connected to the actual terminal
writer (``terminal.write_terminal_atomically`` is unchanged; its
optional commit-point seam is deferred to Stage 2C-3).

The name ``ExecutionState`` belongs to ``terminal.py``'s ADR-0012 state
vocabulary and is not reused here.

stdlib + pydantic only.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, replace as _dc_replace
from enum import Enum
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .evidence_records import TerminationCause
from .execution_envelope import SessionClock
from .models import canonical_json_bytes

STAGE2C_CAUSES: frozenset[str] = frozenset({"SESSION_DEADLINE", "INVOCATION_STALL_DEADLINE", "WATCHDOG"})
STAGE2B_CAUSES: frozenset[str] = frozenset({"PRE_PROVIDER_FAILURE", "RUNNER_EXCEPTION"})
CANONICAL_CAUSES: frozenset[str] = STAGE2C_CAUSES | STAGE2B_CAUSES


class ExecutionControlError(RuntimeError):
    """A control operation was refused. Carries only closed-vocabulary
    values, never free text, a token or a local path."""


class LatchCauseError(ValueError):
    """A cause outside the permitted closed vocabulary was supplied."""


class TerminalCommitState(Enum):
    NONE = "NONE"
    QUALITY_IN_PROGRESS = "QUALITY_IN_PROGRESS"
    QUALITY_COMMITTED = "QUALITY_COMMITTED"
    QUALITY_FAILED = "QUALITY_FAILED"
    INVALID_IN_PROGRESS = "INVALID_IN_PROGRESS"
    INVALID_COMMITTED = "INVALID_COMMITTED"
    INVALID_FAILED = "INVALID_FAILED"


COMMITTED_STATES: frozenset[TerminalCommitState] = frozenset(
    {TerminalCommitState.QUALITY_COMMITTED, TerminalCommitState.INVALID_COMMITTED}
)
_INVALID_PERMITTED_FROM: frozenset[TerminalCommitState] = frozenset(
    {TerminalCommitState.NONE, TerminalCommitState.QUALITY_FAILED}
)


class QualityRefused(ExecutionControlError):
    """``commit_quality`` refused: a cause is latched (``cause``) or the
    arbiter is not in ``NONE`` (``state``)."""

    def __init__(self, *, cause: TerminationCause | None, state: TerminalCommitState) -> None:
        self.cause = cause
        self.state = state
        super().__init__(f"quality commit refused: cause={cause} state={state.value}")


class InvalidRefused(ExecutionControlError):
    """``commit_invalid`` refused: the supplied cause is not the latched
    cause, or the arbiter state does not permit an invalid commit."""

    def __init__(self, *, cause: TerminationCause, latched: TerminationCause | None, state: TerminalCommitState) -> None:
        self.cause = cause
        self.latched = latched
        self.state = state
        super().__init__(f"invalid commit refused: cause={cause} latched={latched} state={state.value}")


def _require_monotonic(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExecutionControlError(f"{label} must be a monotonic-clock number")
    return float(value)


# ---------------------------------------------------------------------------
# Shared synchronization domain -- the one lock
# ---------------------------------------------------------------------------


class ExecutionSafetyDomain:
    """Owns exactly one re-entrant lock. Every object that needs
    synchronization in this module is constructed on a domain and takes
    that domain's lock; nothing else in this module creates a lock."""

    __slots__ = ("_lock",)

    def __init__(self) -> None:
        self._lock = threading.RLock()

    @property
    def lock(self) -> threading.RLock:
        return self._lock


# ---------------------------------------------------------------------------
# Session latch
# ---------------------------------------------------------------------------


class SessionLatch:
    """Session-local first-cause latch. Once tripped, later Stage-2C
    wiring refuses to start any new provider work (2C-2); the arbiter
    refuses any quality commit (below)."""

    __slots__ = ("_domain", "_cause", "_tripped_at")

    def __init__(self, domain: ExecutionSafetyDomain) -> None:
        if not isinstance(domain, ExecutionSafetyDomain):
            raise ExecutionControlError("a SessionLatch requires an ExecutionSafetyDomain")
        self._domain = domain
        self._cause: TerminationCause | None = None
        self._tripped_at: float | None = None

    @property
    def domain(self) -> ExecutionSafetyDomain:
        return self._domain

    def trip(self, cause: TerminationCause, at_monotonic: float) -> bool:
        """Atomically establish ``cause`` unless a cause is already
        latched. Returns True only for the call that set it. Only a
        Stage-2C cause may trip the latch."""
        if cause not in STAGE2C_CAUSES:
            raise LatchCauseError(f"{cause!r} is not a Stage-2C latch cause")
        at = _require_monotonic(at_monotonic, "at_monotonic")
        with self._domain.lock:
            if self._cause is not None:
                return False
            self._cause = cause
            self._tripped_at = at
            return True

    @property
    def is_set(self) -> bool:
        with self._domain.lock:
            return self._cause is not None

    @property
    def cause(self) -> TerminationCause | None:
        with self._domain.lock:
            return self._cause

    @property
    def tripped_at(self) -> float | None:
        with self._domain.lock:
            return self._tripped_at


# ---------------------------------------------------------------------------
# Terminal arbiter
# ---------------------------------------------------------------------------


class TerminalArbiter:
    """Exactly one terminal class can ever be committed. Both commit
    points hold the shared domain lock for their whole body, so a latch
    trip can never interleave with ``replace()``; the terminal commit
    state is final before the lock is released."""

    __slots__ = ("_domain", "_latch", "_clock", "_monotonic", "_state", "_deadline_established")

    def __init__(
        self,
        domain: ExecutionSafetyDomain,
        latch: SessionLatch,
        clock: SessionClock,
        *,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(domain, ExecutionSafetyDomain):
            raise ExecutionControlError("a TerminalArbiter requires an ExecutionSafetyDomain")
        if not isinstance(latch, SessionLatch) or latch.domain is not domain:
            raise ExecutionControlError("the arbiter and its latch must share one ExecutionSafetyDomain")
        if not isinstance(clock, SessionClock):
            raise ExecutionControlError("a TerminalArbiter requires a SessionClock")
        self._domain = domain
        self._latch = latch
        self._clock = clock
        self._monotonic = monotonic if monotonic is not None else clock.monotonic_now
        self._state = TerminalCommitState.NONE
        self._deadline_established = False

    @property
    def state(self) -> TerminalCommitState:
        with self._domain.lock:
            return self._state

    @property
    def deadline_established_by_commit_point(self) -> bool:
        with self._domain.lock:
            return self._deadline_established

    def commit_quality(self, replace: Callable[[], None]) -> None:
        """Commit the RUNNER's quality record through ``replace``.

        Under the domain lock: refuse if a cause is latched; re-check
        the session clock and, if expired, atomically establish
        ``SESSION_DEADLINE`` and refuse; refuse any state other than
        ``NONE``; mark ``QUALITY_IN_PROGRESS``; call ``replace`` exactly
        once; a normal return is ``QUALITY_COMMITTED``, any exception
        (``BaseException`` included) is ``QUALITY_FAILED`` and re-raised.
        """
        if not callable(replace):
            raise ExecutionControlError("replace must be callable")
        with self._domain.lock:
            latched = self._latch.cause
            if latched is not None:
                raise QualityRefused(cause=latched, state=self._state)
            if self._clock.expired():
                self._latch.trip("SESSION_DEADLINE", self._monotonic())
                self._deadline_established = True
                raise QualityRefused(cause="SESSION_DEADLINE", state=self._state)
            if self._state is not TerminalCommitState.NONE:
                raise QualityRefused(cause=None, state=self._state)
            self._state = TerminalCommitState.QUALITY_IN_PROGRESS
            try:
                replace()
            except BaseException:
                self._state = TerminalCommitState.QUALITY_FAILED
                raise
            self._state = TerminalCommitState.QUALITY_COMMITTED

    def commit_invalid(self, cause: TerminationCause, replace: Callable[[], None]) -> None:
        """Commit an execution-invalid record for ``cause`` through
        ``replace``.

        A Stage-2C cause must be exactly the latched cause; a Stage-2B
        cause is permitted only while the latch is clear. Refused from
        ``QUALITY_COMMITTED``, ``INVALID_COMMITTED``, ``INVALID_FAILED``
        and any ``*_IN_PROGRESS`` state; permitted from ``NONE`` and
        ``QUALITY_FAILED``. ``replace`` is called exactly once; failure
        is ``INVALID_FAILED`` and re-raised."""
        if cause not in CANONICAL_CAUSES:
            raise LatchCauseError(f"{cause!r} is not a canonical termination cause")
        if not callable(replace):
            raise ExecutionControlError("replace must be callable")
        with self._domain.lock:
            latched = self._latch.cause
            if cause in STAGE2C_CAUSES:
                if latched != cause:
                    raise InvalidRefused(cause=cause, latched=latched, state=self._state)
            elif latched is not None:
                raise InvalidRefused(cause=cause, latched=latched, state=self._state)
            if self._state not in _INVALID_PERMITTED_FROM:
                raise InvalidRefused(cause=cause, latched=latched, state=self._state)
            self._state = TerminalCommitState.INVALID_IN_PROGRESS
            try:
                replace()
            except BaseException:
                self._state = TerminalCommitState.INVALID_FAILED
                raise
            self._state = TerminalCommitState.INVALID_COMMITTED


# ---------------------------------------------------------------------------
# Execution-control configuration (no defaults, no committed instance)
# ---------------------------------------------------------------------------


class ExecutionControlConfig(BaseModel):
    """The Stage-2C control cadences and graces. Every field is required:
    no default exists and no production instance is committed in Stage
    2C-1 -- the values are justified and frozen in Stage 2C-2, and
    ``control_config_id`` is bound at readiness if material."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    heartbeat_interval_ms: int = Field(ge=1)
    watchdog_grace_ms: int = Field(ge=1)
    descendant_term_grace_ms: int = Field(ge=1)
    descendant_kill_wait_ms: int = Field(ge=1)
    monitor_tick_ms: int = Field(ge=1)

    @property
    def control_config_id(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self)).hexdigest()


# ---------------------------------------------------------------------------
# Invocation registry (minimal bookkeeping for the 2C-2 monitor)
# ---------------------------------------------------------------------------

InvocationOutcome = Literal["RETURNED", "RAISED", "TIMED_OUT"]
_INVOCATION_OUTCOMES: frozenset[str] = frozenset({"RETURNED", "RAISED", "TIMED_OUT"})


@dataclass(frozen=True)
class InvocationRecord:
    run_ordinal: int
    invocation_ordinal: int
    started_at_monotonic: float
    budget_ms: int
    finished_at_monotonic: float | None = None
    outcome: InvocationOutcome | None = None

    @property
    def in_flight(self) -> bool:
        return self.outcome is None

    @property
    def deadline_monotonic(self) -> float:
        return self.started_at_monotonic + self.budget_ms / 1000.0


class InvocationRegistry:
    """Records which logical invocations are in flight, with their
    monotonic start and budget, so a monitor can detect an overrun. No
    content, no text, no thread: bookkeeping under the domain lock only.
    """

    __slots__ = ("_domain", "_records")

    def __init__(self, domain: ExecutionSafetyDomain) -> None:
        if not isinstance(domain, ExecutionSafetyDomain):
            raise ExecutionControlError("an InvocationRegistry requires an ExecutionSafetyDomain")
        self._domain = domain
        self._records: dict[tuple[int, int], InvocationRecord] = {}

    @property
    def domain(self) -> ExecutionSafetyDomain:
        return self._domain

    def start(self, *, run_ordinal: int, invocation_ordinal: int, budget_ms: int, at_monotonic: float) -> InvocationRecord:
        for label, value in (("run_ordinal", run_ordinal), ("invocation_ordinal", invocation_ordinal), ("budget_ms", budget_ms)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ExecutionControlError(f"{label} must be a positive integer")
        at = _require_monotonic(at_monotonic, "at_monotonic")
        key = (run_ordinal, invocation_ordinal)
        with self._domain.lock:
            if key in self._records:
                raise ExecutionControlError("invocation already registered")
            record = InvocationRecord(
                run_ordinal=run_ordinal, invocation_ordinal=invocation_ordinal,
                started_at_monotonic=at, budget_ms=budget_ms,
            )
            self._records[key] = record
            return record

    def finish(self, *, run_ordinal: int, invocation_ordinal: int, outcome: InvocationOutcome, at_monotonic: float) -> InvocationRecord:
        if outcome not in _INVOCATION_OUTCOMES:
            raise ExecutionControlError("outcome must be RETURNED, RAISED or TIMED_OUT")
        at = _require_monotonic(at_monotonic, "at_monotonic")
        key = (run_ordinal, invocation_ordinal)
        with self._domain.lock:
            current = self._records.get(key)
            if current is None:
                raise ExecutionControlError("invocation was never registered")
            if not current.in_flight:
                raise ExecutionControlError("invocation already finished")
            record = _dc_replace(current, finished_at_monotonic=at, outcome=outcome)
            self._records[key] = record
            return record

    def in_flight(self) -> tuple[InvocationRecord, ...]:
        with self._domain.lock:
            return tuple(r for r in self._records.values() if r.in_flight)

    def overrun(self, *, now_monotonic: float, grace_ms: int) -> tuple[InvocationRecord, ...]:
        """In-flight invocations whose budget plus ``grace_ms`` has
        elapsed at ``now_monotonic``."""
        now = _require_monotonic(now_monotonic, "now_monotonic")
        if isinstance(grace_ms, bool) or not isinstance(grace_ms, int) or grace_ms < 0:
            raise ExecutionControlError("grace_ms must be a non-negative integer")
        with self._domain.lock:
            return tuple(
                r for r in self._records.values()
                if r.in_flight and now >= r.deadline_monotonic + grace_ms / 1000.0
            )

    def records(self) -> tuple[InvocationRecord, ...]:
        with self._domain.lock:
            return tuple(self._records.values())

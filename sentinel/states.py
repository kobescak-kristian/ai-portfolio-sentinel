"""Task state machine (BLUEPRINT §6 P2).

The frozen ``CheckTask.status`` Literal (contracts/schemas.py) only
freezes the value set; enforcing legal transitions is this module's
job. ``contracts/ledger_schema.sql`` has no trigger on ``tasks`` —
this state machine is entirely application-enforced, backed by a
compare-and-swap UPDATE in ``sentinel.ledger`` so an illegal or
racing transition fails atomically before it can corrupt a run's
accounting.
"""

from __future__ import annotations

PENDING = "PENDING"
IN_PROGRESS = "IN_PROGRESS"
DONE = "DONE"
FAILED = "FAILED"
DEAD_LETTER = "DEAD_LETTER"

ALL_STATUSES: tuple[str, ...] = (PENDING, IN_PROGRESS, DONE, FAILED, DEAD_LETTER)

TERMINAL_STATES: frozenset[str] = frozenset({DONE, FAILED, DEAD_LETTER})

# Phase 2 has no retry-then-park: a failure is immediately terminal
# (FAILED -> DEAD_LETTER happens in the same transaction as the
# failure, never left sitting in FAILED). PENDING -> FAILED covers a
# task swept during run-abort/crash-recovery before it was ever
# claimed.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({IN_PROGRESS, FAILED}),
    IN_PROGRESS: frozenset({DONE, FAILED}),
    FAILED: frozenset({DEAD_LETTER}),
    DONE: frozenset(),
    DEAD_LETTER: frozenset(),
}


class IllegalTransition(RuntimeError):
    """Raised before any SQL runs for a transition not in ALLOWED_TRANSITIONS."""


class TaskTransitionConflict(RuntimeError):
    """Raised when the compare-and-swap UPDATE affects zero rows —
    the task's status was not what the caller expected (a race, or a
    stale read)."""


def assert_legal(current: str, new: str) -> None:
    if new not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise IllegalTransition(f"{current} -> {new} is not a legal transition")

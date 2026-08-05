"""Task state machine: legal/illegal transitions, parity with the
frozen CheckTask.status Literal."""

from __future__ import annotations

from itertools import product
from typing import get_args

import pytest

from contracts.schemas import CheckTask
from sentinel.states import (
    ALLOWED_TRANSITIONS,
    ALL_STATUSES,
    TERMINAL_STATES,
    IllegalTransition,
    assert_legal,
)


def test_status_set_matches_frozen_contract():
    frozen_statuses = set(get_args(CheckTask.model_fields["status"].annotation))
    assert set(ALL_STATUSES) == frozen_statuses


def test_terminal_states_are_exactly_three():
    assert TERMINAL_STATES == {"DONE", "FAILED", "DEAD_LETTER"}


@pytest.mark.parametrize(
    "start,end",
    [
        ("PENDING", "IN_PROGRESS"),
        ("IN_PROGRESS", "DONE"),
        ("IN_PROGRESS", "FAILED"),
        ("FAILED", "DEAD_LETTER"),
        ("PENDING", "FAILED"),
    ],
)
def test_legal_transitions(start, end):
    assert_legal(start, end)  # must not raise


_ALL_PAIRS = list(product(ALL_STATUSES, ALL_STATUSES))
_LEGAL_PAIRS = {
    (start, end) for start, ends in ALLOWED_TRANSITIONS.items() for end in ends
}
_ILLEGAL_PAIRS = [pair for pair in _ALL_PAIRS if pair not in _LEGAL_PAIRS]


@pytest.mark.parametrize("start,end", _ILLEGAL_PAIRS)
def test_illegal_transitions_rejected(start, end):
    with pytest.raises(IllegalTransition):
        assert_legal(start, end)


def test_done_and_dead_letter_have_no_outgoing_transitions():
    """DONE and DEAD_LETTER are truly at-rest terminal. FAILED is
    terminal for ledger *counting* purposes (the frozen contract's own
    docstring names it a terminal status) but is deliberately not
    at-rest in this design — Phase 2 has no retry-then-park, so a task
    that fails always continues straight to DEAD_LETTER within the
    same transaction (fail_and_dead_letter). Its one legal outgoing
    transition is to DEAD_LETTER, and nowhere else."""
    assert ALLOWED_TRANSITIONS["DONE"] == frozenset()
    assert ALLOWED_TRANSITIONS["DEAD_LETTER"] == frozenset()
    assert ALLOWED_TRANSITIONS["FAILED"] == frozenset({"DEAD_LETTER"})

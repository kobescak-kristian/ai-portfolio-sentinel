"""Phase-5 cost accounting and cadence state machine (ADR-0011 §6, §7).

Pure, model-free. Trailing-spend evidence is fail-closed: a missing,
unreadable or malformed cost ledger never becomes zero spend — it raises
``CostEvidenceUnavailable`` and no caller in this package catches that
exception and defaults, so unknown spend can never authorize a provider
start. ``telemetry/cost_ledger.py`` is not modified; it is read only
through its existing public ``read_cost_rows``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from telemetry.cost_ledger import read_cost_rows

from .models import Phase5ControlState, QualificationWindowRecord, WindowConsumeReason

SCHEDULED_ORDINARY_ALLOWANCE_EUR_MICROS = 750_000
WIF_PROBE_ALLOWANCE_EUR_MICROS = 150_000
OFFICIAL_GATE_ALLOWANCE_EUR_MICROS = 5_000_000
LANE_HARD_CEILING_EUR_MICROS = 50_000_000
CADENCE_TRIGGER_EUR_MICROS = 40_000_000

_CADENCE_ORDER = ("DAILY", "EVERY_2_DAYS", "WEEKLY")
_NEXT_CADENCE = {"DAILY": "EVERY_2_DAYS", "EVERY_2_DAYS": "WEEKLY"}


class CostEvidenceUnavailable(Exception):
    """Trailing spend could not be established — never treated as zero."""


class CostCeilingExceeded(Exception):
    """The EUR50 lane hard ceiling would be exceeded by a prospective start."""


class Phase5StateCorruption(Exception):
    """A structurally-required invariant does not hold (e.g. a non-DAILY
    cadence with no anchor)."""


def trailing_30d_spend_eur_micros(cost_ledger_path: Path, now: datetime) -> int:
    """Sum of ``CostRow.cost_eur_micros`` for rows with
    ``T-30d <= recorded_at_utc <= T`` (closed interval). An existing,
    valid, empty ledger truthfully sums to 0; a missing, unreadable or
    malformed ledger raises ``CostEvidenceUnavailable`` with a generic
    message — the raw underlying exception text (which could contain a
    malformed row body or a path) is never interpolated into the public
    message, though it survives via ``__cause__`` for diagnostics."""
    if not cost_ledger_path.exists():
        raise CostEvidenceUnavailable("cost ledger missing")
    try:
        rows = read_cost_rows(cost_ledger_path)
    except (ValueError, OSError) as exc:
        raise CostEvidenceUnavailable("cost ledger unreadable or malformed") from exc
    cutoff = now - timedelta(days=30)
    return sum(row.cost_eur_micros for row in rows if cutoff <= row.recorded_at_utc <= now)


def assert_provider_start_permitted(actual_trailing_spend: int, nominal_allowance: int) -> None:
    """Prospective START control only — a start is refused when
    ``actual + allowance > 50_000_000``; exact equality is permitted."""
    if actual_trailing_spend + nominal_allowance > LANE_HARD_CEILING_EUR_MICROS:
        raise CostCeilingExceeded()


def window_freeze_headroom_ok(actual_trailing_spend: int, cadence_level: str) -> bool:
    """Pure preflight only — nothing here freezes a window. Requires
    DAILY cadence and ``actual + 5 * 750_000 <= 40_000_000``."""
    return (
        actual_trailing_spend + 5 * SCHEDULED_ORDINARY_ALLOWANCE_EUR_MICROS
        <= CADENCE_TRIGGER_EUR_MICROS
        and cadence_level == "DAILY"
    )


def is_slot_eligible(
    cadence_level: str, anchor_slot_utc: datetime | None, expected_slot_utc: datetime
) -> bool:
    """Eligibility is based on frozen expected daily UTC slot timestamps,
    never wall-clock execution time."""
    if cadence_level == "DAILY":
        return True
    if anchor_slot_utc is None:
        raise Phase5StateCorruption("non-DAILY cadence requires an anchor")
    offset_days = (expected_slot_utc.date() - anchor_slot_utc.date()).days
    if cadence_level == "EVERY_2_DAYS":
        return offset_days > 0 and offset_days % 2 == 0
    if cadence_level == "WEEKLY":
        return offset_days > 0 and offset_days % 7 == 0
    raise ValueError(f"unknown cadence level: {cadence_level!r}")


@dataclass(frozen=True)
class CadenceDecision:
    outcome: str
    provider_call_permitted: bool
    consume_active_window: bool = False
    window_consume_reason: WindowConsumeReason | None = None
    cadence_transition_to: str | None = None
    new_anchor: datetime | None = None


@dataclass(frozen=True)
class PostRunDecision:
    consume_active_window: bool
    window_consume_reason: WindowConsumeReason | None = None


def evaluate_scheduled_trigger(
    control_state: Phase5ControlState,
    actual_trailing_spend: int,
    active_window: QualificationWindowRecord | None,
    expected_slot_utc: datetime,
) -> CadenceDecision:
    """The single pure cadence evaluator — also used, unchanged, by
    ``bundle.reconstruct_refusal_decision`` to mechanically re-derive the
    decision a ``CONTROL_REFUSAL`` bundle claims to represent.

    Ordering: (1) cadence eligibility; (2) EUR50 prospective hard-start
    check and active-window >EUR40 breach, evaluated together — a
    refusal on either never suppresses the other's truthful
    consequence; (3) cadence downgrade when neither of those applies.
    """
    if not is_slot_eligible(
        control_state.cadence_level, control_state.cadence_anchor_slot_utc, expected_slot_utc
    ):
        return CadenceDecision(outcome="CADENCE_SKIP", provider_call_permitted=False)

    eur50_ok = (
        actual_trailing_spend + SCHEDULED_ORDINARY_ALLOWANCE_EUR_MICROS
        <= LANE_HARD_CEILING_EUR_MICROS
    )
    window_breach = active_window is not None and actual_trailing_spend > CADENCE_TRIGGER_EUR_MICROS

    if not eur50_ok or window_breach:
        return CadenceDecision(
            outcome="COST_CADENCE_REFUSAL",
            provider_call_permitted=False,
            consume_active_window=window_breach,
            window_consume_reason="COST_CADENCE_REFUSAL" if window_breach else None,
        )

    if control_state.cadence_level != "WEEKLY" and actual_trailing_spend > CADENCE_TRIGGER_EUR_MICROS:
        next_level = _NEXT_CADENCE[control_state.cadence_level]
        return CadenceDecision(
            outcome="CADENCE_SKIP",
            provider_call_permitted=False,
            cadence_transition_to=next_level,
            new_anchor=expected_slot_utc,
        )

    return CadenceDecision(outcome="PROCEED", provider_call_permitted=True)


def evaluate_post_run(
    active_window: QualificationWindowRecord | None, actual_trailing_spend_after: int
) -> PostRunDecision:
    """Honest, no retrospective clamping: a post-run crossing of EUR40
    while a window is active consumes it with a reason distinct from any
    pre-run refusal, leaving the run's own qualification outcome
    untouched."""
    if active_window is not None and actual_trailing_spend_after > CADENCE_TRIGGER_EUR_MICROS:
        return PostRunDecision(consume_active_window=True, window_consume_reason="POST_RUN_COST_TRIGGER")
    return PostRunDecision(consume_active_window=False, window_consume_reason=None)

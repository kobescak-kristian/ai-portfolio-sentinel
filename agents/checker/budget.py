"""Run-scoped EUR budget coordinator (dispatch q77-p3-a, section E).

One coordinator owns the *entire* run's agent budget — not a per-call
budget. It reserves conservatively before each call, deriving a capped
USD allowance for the SDK from the remaining EUR budget and the
resolved FX rate with an explicit safety margin.

What it does and does NOT guarantee (corrected by
adr/0008-judgment-call-execution-reliability section 7). This module
used to claim it "never lets the run's aggregate *charged* cost exceed
``RUN_BUDGET_EUR_MICROS``". That claim was false and is withdrawn: the
pinned SDK enforces its own per-call budget only *after* API-call
activity, so a call already in flight can overshoot its allowance
before the SDK halts it. The coordinator cannot retroactively prevent
spend that already happened.

The invariant it actually enforces is a **start** condition:

* a new model invocation begins only if a reservation can still be
  taken from remaining accounted/reserved capacity;
* every recoverable post-call estimate is accounted in full, including
  a known overshoot above that call's reservation;
* once accounted consumption reaches or exceeds the run cap,
  ``reserve()`` refuses — so remaining capacity may legitimately be
  zero or negative, and no further call starts.

A negative remaining figure is therefore truthful DETECTED OVERSHOOT,
not an accounting failure and not permission for further spend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal

from agents.checker.config import (
    MAX_PER_CALL_RESERVE_EUR_MICROS,
    RUN_BUDGET_EUR_MICROS,
    SDK_ALLOWANCE_SAFETY_MARGIN,
)
from agents.checker.fx import FxRate

_MICROS_PER_EUR = Decimal(1_000_000)


class BudgetExhausted(RuntimeError):
    """Raised by ``reserve()`` when no further reservation can be made
    within the run's remaining EUR budget. The caller must route the
    task to Inconclusive without making any further model call."""


@dataclass(frozen=True)
class Reservation:
    """A held slice of the run budget for one in-flight call.
    ``sdk_max_budget_usd`` is what's passed to
    ``ClaudeAgentOptions.max_budget_usd``; ``reserved_eur_micros`` is
    what's recorded on the ``agent_calls`` audit row and released/
    charged when the call is finalized via ``commit``/``commit_unresolved``."""

    reserved_eur_micros: int
    sdk_max_budget_usd: float


@dataclass
class RunBudgetCoordinator:
    fx_rate: FxRate
    total_eur_micros: int = RUN_BUDGET_EUR_MICROS
    _committed_eur_micros: int = field(default=0, init=False)  # charged, finalized calls
    _reserved_eur_micros: int = field(default=0, init=False)  # held by in-flight calls

    def remaining_eur_micros(self) -> int:
        """May be zero or NEGATIVE after a detected post-call overshoot
        is accounted (ADR-0008 section 7). ``reserve()`` refuses at
        ``<= 0``, so a negative figure is a truthful record of spend
        that already happened, never a licence for more."""
        return self.total_eur_micros - self._committed_eur_micros - self._reserved_eur_micros

    def reserve(self) -> Reservation:
        remaining = self.remaining_eur_micros()
        if remaining <= 0:
            raise BudgetExhausted(
                f"run budget exhausted: {self.total_eur_micros} EUR-micros total, "
                f"{self._committed_eur_micros} charged, {self._reserved_eur_micros} reserved"
            )
        amount = min(remaining, MAX_PER_CALL_RESERVE_EUR_MICROS)
        self._reserved_eur_micros += amount
        sdk_allowance = self._conservative_usd_allowance(amount)
        return Reservation(reserved_eur_micros=amount, sdk_max_budget_usd=sdk_allowance)

    def _conservative_usd_allowance(self, reserved_eur_micros: int) -> float:
        reserved_eur = Decimal(reserved_eur_micros) / _MICROS_PER_EUR
        margin = Decimal(SDK_ALLOWANCE_SAFETY_MARGIN)
        usd = reserved_eur * self.fx_rate.usd_per_eur * margin
        # Round DOWN — the SDK-facing allowance must never exceed what
        # the safety-margined EUR reservation actually covers.
        usd = usd.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        return float(usd)

    def commit(self, reservation: Reservation, *, charged_eur_micros: int) -> None:
        """A call finished with a known, final charge. Deterministic
        bookkeeping for an already-issued reservation and a charge the
        caller has already computed and validated: reserved drops by
        the full reservation, committed rises by the charged amount, so
        any unused remainder is released back to the budget.

        ``charged_eur_micros`` MAY exceed the reservation. That is not
        an error — it is ADR-0008's honest overshoot accounting, and
        refusing it here is what previously forced a known overshoot to
        be silently clamped away. Overshoot simply reduces (possibly
        past zero) what ``reserve()`` will hand out next.

        Ordering contract (ADR-0008): callers invoke this only AFTER
        the call's terminal evidence is durably committed to the
        ledger, so in-memory accounting can never run ahead of the
        audit trail."""
        if charged_eur_micros < 0:
            raise ValueError("charged_eur_micros must not be negative")
        self._reserved_eur_micros -= reservation.reserved_eur_micros
        self._committed_eur_micros += charged_eur_micros

    def commit_unresolved(self, reservation: Reservation) -> None:
        """A call started but ended without recoverable final usage
        (SDK/transport error caught in-process — not a process crash;
        see ``ledger.unresolved_agent_calls`` for the crash case,
        which never reaches this method at all). Charges the full
        reservation, never zero, per the binding decision."""
        self.commit(reservation, charged_eur_micros=reservation.reserved_eur_micros)

    def total_charged_eur_micros(self) -> int:
        return self._committed_eur_micros


def usd_to_charged_eur_micros(usd_amount: Decimal, fx_rate: FxRate) -> int:
    """Convert an SDK-reported USD cost into integer EUR micro-euros,
    rounded UP ("round charged EUR upward to integer micro-euros") —
    conservative in the charge's favor, never in the budget's."""
    if usd_amount < 0:
        raise ValueError("usd_amount must not be negative")
    eur = usd_amount / fx_rate.usd_per_eur
    micros = (eur * _MICROS_PER_EUR).to_integral_value(rounding=ROUND_CEILING)
    return int(micros)

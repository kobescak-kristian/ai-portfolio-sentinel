"""Mechanized judgment-call failure taxonomy and terminal cost
accounting (adr/0008-judgment-call-execution-reliability; dispatch
q77-p3-adr8-impl-a).

Two jobs, both deliberately free of any SDK/model I/O so they can be
proven model-free:

1. **Classification.** Turn the structured outcome of one SDK
   invocation into exactly one failure class from ADR-0008's fixed
   taxonomy. Classification is by failure *semantics*, never by parsing
   exception prose, and never by asking whether a retry would help any
   evaluation pass.
2. **Terminal accounting.** Compute what one finished call actually
   costs the run budget, per ADR-0008 section 6's four cases.

The retryable set has cardinality exactly one and is fixed here, at
implementation time, before any future validation exists. Expanding it
requires a new owner-governed decision, never an implementation-only
change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# --- ADR-0008 section 1: the fixed failure taxonomy -------------------

# Pre-call classes: no SDK invocation occurred.
AUTH_OVERRIDE = "AUTH_OVERRIDE"
RUN_BUDGET_EXHAUSTED = "RUN_BUDGET_EXHAUSTED"

# Execution classes: an SDK invocation was made and did not complete
# cleanly.
SDK_BUDGET_CEILING = "SDK_BUDGET_CEILING"
SDK_RESULT_ERROR_OTHER = "SDK_RESULT_ERROR_OTHER"
TRANSPORT_PROCESS_SDK_EXCEPTION_WITHOUT_CAPTURED_TYPED_RESULT = (
    "TRANSPORT_PROCESS_SDK_EXCEPTION_WITHOUT_CAPTURED_TYPED_RESULT"
)
NO_RESULT_MESSAGE = "NO_RESULT_MESSAGE"
TOOL_BREAKER = "TOOL_BREAKER"

# Non-execution classes: named by the taxonomy, but they describe a
# measured judgment-quality outcome or an accounting observation, not an
# execution failure. None of them ever authorizes a retry.
HOST_EVIDENCE_REJECTION = "HOST_EVIDENCE_REJECTION"
MISSING_FINAL_COST = "MISSING_FINAL_COST"
REPORTED_COST_OVERSHOOT = "REPORTED_COST_OVERSHOOT"

#: The SDK's own typed terminal subtype for its per-call budget ceiling.
#: This exact string is the ONLY mechanized retry trigger. The trailing
#: process exception the SDK raises afterwards is an untyped Exception
#: whose prose merely quotes the CLI error text, so exception text can
#: never authorize a retry (ADR-0008 section 2).
SDK_BUDGET_CEILING_SUBTYPE = "error_max_budget_usd"

#: Every class ADR-0008 recognizes, for completeness proofs.
ALL_FAILURE_CLASSES = frozenset(
    {
        AUTH_OVERRIDE,
        RUN_BUDGET_EXHAUSTED,
        SDK_BUDGET_CEILING,
        SDK_RESULT_ERROR_OTHER,
        TRANSPORT_PROCESS_SDK_EXCEPTION_WITHOUT_CAPTURED_TYPED_RESULT,
        NO_RESULT_MESSAGE,
        TOOL_BREAKER,
        HOST_EVIDENCE_REJECTION,
        MISSING_FINAL_COST,
        REPORTED_COST_OVERSHOOT,
    }
)

#: Cardinality exactly one. Bound at implementation time, before any
#: future validation exists. Expanding this requires a NEW
#: owner-governed decision backed by evidence (ADR-0008 section 1).
RETRYABLE_FAILURE_CLASSES = frozenset({SDK_BUDGET_CEILING})


def is_retryable(failure_class: Optional[str]) -> bool:
    return failure_class in RETRYABLE_FAILURE_CLASSES


@dataclass(frozen=True)
class QueryOutcome:
    """What one SDK invocation actually produced, kept structured so
    the harness never has to inspect exception prose.

    The pinned SDK delivers its terminal ResultMessage to the stream
    *before* the CLI's deliberate non-zero exit surfaces as a trailing
    untyped ``Exception``. Carrying both fields side by side is what
    stops that typed result being lost when the stream then raises -
    the exact information-loss path ADR-0008 was written to close.
    """

    result: object = None
    error: Optional[BaseException] = None

    @property
    def subtype(self) -> Optional[str]:
        return getattr(self.result, "subtype", None) if self.result is not None else None

    @property
    def is_error(self) -> Optional[bool]:
        return getattr(self.result, "is_error", None) if self.result is not None else None


def classify_invocation(outcome: QueryOutcome, *, breaker_tripped: bool) -> Optional[str]:
    """Classify one completed SDK invocation. ``None`` means the
    invocation completed cleanly and its findings may be returned.

    Order is load-bearing:

    1. A local containment failure wins outright. If the tool-call
       circuit breaker tripped, that is the failure - a coexisting
       budget subtype must NEVER promote a contained call into a retry.
    2. A captured typed error result classifies from its subtype alone.
    3. Only with no captured typed error result does a trailing
       exception classify, and it classifies as insufficient evidence
       for retry - fail-closed, non-retryable, whatever its prose says.
    4. No result message at all is likewise insufficient evidence.
    """
    if breaker_tripped:
        return TOOL_BREAKER

    result = outcome.result
    if result is not None and getattr(result, "is_error", False):
        if getattr(result, "subtype", None) == SDK_BUDGET_CEILING_SUBTYPE:
            return SDK_BUDGET_CEILING
        return SDK_RESULT_ERROR_OTHER

    if outcome.error is not None:
        return TRANSPORT_PROCESS_SDK_EXCEPTION_WITHOUT_CAPTURED_TYPED_RESULT

    if result is None:
        return NO_RESULT_MESSAGE

    return None


def failure_reason(failure_class: str, outcome: QueryOutcome) -> str:
    """A short, bounded, human-readable terminal reason. Derived from
    the mechanized class plus typed metadata - never from model text.
    The caller still passes it through the ledger redaction boundary."""
    if failure_class == TOOL_BREAKER:
        return "tool-call circuit breaker tripped"
    if failure_class == NO_RESULT_MESSAGE:
        return "no result message returned"
    if failure_class == SDK_BUDGET_CEILING:
        return f"{failure_class}: SDK per-call budget ceiling (subtype={outcome.subtype!r})"
    if failure_class == SDK_RESULT_ERROR_OTHER:
        return f"{failure_class}: SDK result error (subtype={outcome.subtype!r})"
    if failure_class == TRANSPORT_PROCESS_SDK_EXCEPTION_WITHOUT_CAPTURED_TYPED_RESULT:
        exc = outcome.error
        return f"{failure_class}: {type(exc).__name__}"
    return failure_class


# --- ADR-0008 section 6: terminal cost accounting ---------------------


def terminal_charge(
    *,
    completed: bool,
    reserved_eur_micros: int,
    estimate_eur_micros: Optional[int],
) -> int:
    """What one finished call costs the run budget, in EUR micro-euros.

    The four adopted cases:

    * **A - COMPLETED, estimate recoverable.** Charge the FULL
      conservatively converted estimate. Never
      ``min(estimate, reservation)``: silently clamping a known
      overshoot is the latent under-accounting defect ADR-0008 removes.
    * **B - FAILED, estimate recoverable.** Charge
      ``max(reservation, estimate)``. A failed invocation must never
      become cheaper than it was before re-execution existed, and a
      known estimate above the reservation must never be understated.
    * **C - FAILED, estimate not recoverable.** Charge the full
      reservation (the existing conservative unresolved-cost rule).
    * **D - COMPLETED, estimate not recoverable.** Also charge the full
      reservation, preserving today's conservative treatment.

    The SDK figure is an estimate / model-equivalent consumption
    signal, never authoritative provider billing.
    """
    if reserved_eur_micros < 0:
        raise ValueError("reserved_eur_micros must not be negative")
    if estimate_eur_micros is None:
        return reserved_eur_micros  # cases C and D
    if estimate_eur_micros < 0:
        raise ValueError("estimate_eur_micros must not be negative")
    if completed:
        return estimate_eur_micros  # case A - no clamp
    return max(reserved_eur_micros, estimate_eur_micros)  # case B


def is_reported_cost_overshoot(*, reserved_eur_micros: int, charged_eur_micros: int) -> bool:
    """Whether this call's accounted charge exceeded its reservation -
    ADR-0008's REPORTED_COST_OVERSHOOT observation. It is recorded
    honestly and never authorizes a retry; execution success or failure
    follows the underlying SDK result."""
    return charged_eur_micros > reserved_eur_micros

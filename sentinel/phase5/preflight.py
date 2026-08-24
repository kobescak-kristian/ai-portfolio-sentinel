"""Frozen scheduled/rehearsal preflight step orders and the ledger that
makes ordering a runtime invariant, not merely a code-review convention
(P5-B Part 3/3).

``SCHEDULED_STEP_ORDER`` is the exact ordering the dispatch freezes:
derive context, discover the active window, resolve the owned slot,
validate provenance, restore the predecessor bundle, evaluate spend and
cadence, assert WIF readiness, emit pre-provider evidence, request the
OIDC token, write/refresh the token file, permit the provider path,
execute, assert terminal ledger evidence, assert exactly one CostRow,
build and validate the successor, classify, stage the artifact, and
clean up. No caller can accidentally skip ahead: ``PreflightLedger``
raises unless each step is recorded in exactly this order, and the
provider path is unreachable (``guard_provider`` raises) until every
step through S11 has been recorded ``OK``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, TypeVar

SCHEDULED_STEP_ORDER: tuple[str, ...] = (
    "S01_DERIVE_GITHUB_CONTEXT",
    "S02_DISCOVER_ACTIVE_WINDOW",
    "S03_RESOLVE_OWNED_SLOT",
    "S04_VALIDATE_PROVENANCE",
    "S05_RESTORE_PREDECESSOR_BUNDLE",
    "S06_EVALUATE_SPEND_AND_CADENCE",
    "S07_ASSERT_WIF_CONFIG_READY",
    "S08_PRE_PROVIDER_EVIDENCE",
    "S09_REQUEST_OIDC_TOKEN",
    "S10_WRITE_TOKEN_FILE",
    "S11_PERMIT_PROVIDER_PATH",
    "S12_EXECUTE_LIVE_SENTINEL_RUN",
    "S13_ASSERT_TERMINAL_LEDGER_ROW",
    "S14_ASSERT_EXACTLY_ONE_COSTROW",
    "S15_BUILD_AND_VALIDATE_SUCCESSOR",
    "S16_CLASSIFY_QUALIFICATION",
    "S17_STAGE_SUCCESSOR_ARTIFACT",
    "S18_CLEANUP_TOKEN_FILE",
)

# Rehearsal exercises real bundle/state plumbing model-free: the same
# first six steps, then straight to pre-provider evidence, successor
# build/validate and staging. S07 and S09..S14 and S16 are structurally
# absent — a rehearsal ledger can never even attempt to record them.
REHEARSAL_STEP_ORDER: tuple[str, ...] = (
    "S01_DERIVE_GITHUB_CONTEXT",
    "S02_DISCOVER_ACTIVE_WINDOW",
    "S03_RESOLVE_OWNED_SLOT",
    "S04_VALIDATE_PROVENANCE",
    "S05_RESTORE_PREDECESSOR_BUNDLE",
    "S06_EVALUATE_SPEND_AND_CADENCE",
    "S08_PRE_PROVIDER_EVIDENCE",
    "S15_BUILD_AND_VALIDATE_SUCCESSOR",
    "S17_STAGE_SUCCESSOR_ARTIFACT",
)

PROVIDER_GATE_STEP = "S11_PERMIT_PROVIDER_PATH"

StepStatus = Literal["OK", "REFUSED", "EARLY_EXIT"]

T = TypeVar("T")


class StepOrderViolation(Exception):
    """A step was recorded out of its frozen order, or after the ledger
    already reached an early exit."""


class ProviderNotPermitted(Exception):
    """A provider-capable call was attempted before every step through
    ``PROVIDER_GATE_STEP`` had been recorded ``OK``."""


@dataclass(frozen=True)
class PreflightStepRecord:
    step_id: str
    status: StepStatus
    detail: str


@dataclass
class PreflightLedger:
    """Enforces one frozen step order at runtime. ``order`` defaults to
    the scheduled order; pass ``REHEARSAL_STEP_ORDER`` for the
    rehearsal path so its shorter order is enforced instead."""

    order: tuple[str, ...] = SCHEDULED_STEP_ORDER
    _records: list[PreflightStepRecord] = field(default_factory=list, init=False)
    _next_index: int = field(default=0, init=False)
    _terminated: bool = field(default=False, init=False)

    def record(self, step_id: str, status: StepStatus, detail: str = "") -> None:
        if self._terminated:
            raise StepOrderViolation(
                f"cannot record {step_id!r}: ledger already reached an early exit"
            )
        if self._next_index >= len(self.order) or step_id != self.order[self._next_index]:
            expected = self.order[self._next_index] if self._next_index < len(self.order) else None
            raise StepOrderViolation(
                f"expected step {expected!r} next, got {step_id!r}"
            )
        self._records.append(PreflightStepRecord(step_id=step_id, status=status, detail=detail))
        self._next_index += 1
        if status in ("REFUSED", "EARLY_EXIT"):
            self._terminated = True

    def mark_early_exit(self, step_id: str, detail: str = "") -> None:
        self.record(step_id, "EARLY_EXIT", detail)

    @property
    def provider_permitted(self) -> bool:
        gate_index = self.order.index(PROVIDER_GATE_STEP) if PROVIDER_GATE_STEP in self.order else None
        if gate_index is None:
            return False
        if len(self._records) <= gate_index:
            return False
        return all(r.status == "OK" for r in self._records[: gate_index + 1])

    def guard_provider(self, call: Callable[[], T]) -> T:
        if not self.provider_permitted:
            raise ProviderNotPermitted(
                "a provider-capable call was attempted before the preflight ledger "
                "reached S11_PERMIT_PROVIDER_PATH"
            )
        return call()

    def to_records(self) -> tuple[PreflightStepRecord, ...]:
        return tuple(self._records)

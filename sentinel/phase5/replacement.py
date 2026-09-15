"""P5-D replacement identity and durable replacement-history verdict
(ADR-0012 / Amendment A repair, Stage 2A; dispatch
q77-p5d-repair-stage2-implement-a).

This module is structural only. It defines the replacement purpose's
frozen identity constants and answers, from already-loaded durable
receipts plus any live one-shot markers, whether current history
permits at most one future replacement execution. It never itself
consumes a marker, calls a provider, dispatches a workflow, or
authorizes anything -- readiness and the owner GO stay entirely
outside this module and outside Stage 2.

No third-party import beyond ``pydantic`` (already used transitively
via ``.models``/``.receipts``) -- this module is stdlib(+pydantic)-only,
same discipline as the rest of ``sentinel/phase5/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .models import OneShotMarker
from .receipts import (
    Phase5Receipt,
    evidence_receipts,
    execution_dispositions,
    is_purpose_consumed,
    marker_receipts,
)

# ---------------------------------------------------------------------------
# Frozen replacement identity (owner ruling q77-p5d-replacement-owner-ruling-a;
# ADR-0012 section 3; Amendment A section A1)
# ---------------------------------------------------------------------------

ORIGINAL_PURPOSE = "P5D_OFFICIAL_SONNET_GATE"
REPLACEMENT_PURPOSE = "P5D_REPLACEMENT_SONNET_GATE"
REPLACEMENT_OF_RUN_ID = "32880880053"
OWNER_RULING_ID = "q77-p5d-replacement-owner-ruling-a"
ORIGINAL_INCIDENT_RECORD_REF = "q77-p5d-invalid-run-record-a"
MAX_REPLACEMENTS = 1


class ReplacementHistoryError(RuntimeError):
    """History does not support computing a replacement verdict --
    ambiguous durable evidence, or a shape that must return to owner
    governance rather than being silently interpreted. Never carries a
    default-permissive fallback."""


@dataclass(frozen=True)
class ReplacementHistoryVerdict:
    """The permission state derived from durable history alone. This is
    NOT authorization: a later readiness/owner-GO gate outside this
    module and outside Stage 2 is still required before any marker may
    be created."""

    permits_one_replacement: bool
    reason: str


def replacement_history_verdict(
    receipts: Sequence[Phase5Receipt],
    live_markers: Sequence[OneShotMarker],
    purpose: str,
) -> ReplacementHistoryVerdict:
    """Compute whether durable history + live markers currently permit
    a future replacement marker to be created for ``purpose``.

    Refuses (``permits_one_replacement=False``) unless ALL of:
      - ``purpose`` is exactly ``REPLACEMENT_PURPOSE``;
      - a durable ``ONESHOT_MARKER_CONSUMED`` receipt exists for the
        ORIGINAL purpose at exactly ``REPLACEMENT_OF_RUN_ID``;
      - a durable ``EXECUTION_DISPOSITION`` receipt exists for that
        same original run with the frozen owner ruling id;
      - no ``GATE_EVIDENCE`` receipt exists for the ORIGINAL purpose
        (the original is permanently non-qualifying for quality);
      - zero durable receipts of ANY class exist for the REPLACEMENT
        purpose (a marker, evidence, or disposition receipt for the
        replacement purpose means it is already consumed/decided);
      - zero live one-shot markers exist for the REPLACEMENT purpose.

    Every refusal path returns a verdict with ``permits_one_replacement
    =False`` and a stated reason; nothing here silently defaults to
    permissive. ``ReplacementHistoryError`` is reserved for a durable
    registry shape a later stage's receipt-recording widening
    discovers it cannot safely interpret -- Stage 2A's fixed vocabulary
    cannot yet produce such a shape, so it is not raised here.
    """
    if purpose != REPLACEMENT_PURPOSE:
        return ReplacementHistoryVerdict(
            permits_one_replacement=False,
            reason=f"purpose {purpose!r} is not the frozen replacement purpose",
        )

    original_marker_receipts = marker_receipts(receipts, ORIGINAL_PURPOSE)
    original_run_markers = [
        r for r in original_marker_receipts if r.github_run_id == REPLACEMENT_OF_RUN_ID
    ]
    if not original_run_markers:
        return ReplacementHistoryVerdict(
            permits_one_replacement=False,
            reason=(
                f"no durable ONESHOT_MARKER_CONSUMED receipt for original run "
                f"{REPLACEMENT_OF_RUN_ID!r} under purpose {ORIGINAL_PURPOSE!r}"
            ),
        )

    original_dispositions = execution_dispositions(receipts, REPLACEMENT_OF_RUN_ID)
    matching_ruling = [
        d
        for d in original_dispositions
        if d.purpose == ORIGINAL_PURPOSE and d.owner_ruling_id == OWNER_RULING_ID
    ]
    if not matching_ruling:
        return ReplacementHistoryVerdict(
            permits_one_replacement=False,
            reason=(
                f"no durable EXECUTION_DISPOSITION receipt for original run "
                f"{REPLACEMENT_OF_RUN_ID!r} under owner ruling {OWNER_RULING_ID!r}"
            ),
        )

    original_gate_evidence = evidence_receipts(receipts, "GATE_EVIDENCE")
    original_purpose_gate_evidence = [
        r for r in original_gate_evidence if r.purpose == ORIGINAL_PURPOSE
    ]
    if original_purpose_gate_evidence:
        return ReplacementHistoryVerdict(
            permits_one_replacement=False,
            reason=(
                f"a GATE_EVIDENCE receipt exists for the original purpose "
                f"{ORIGINAL_PURPOSE!r} -- the original is permanently non-qualifying"
            ),
        )

    if is_purpose_consumed(receipts, REPLACEMENT_PURPOSE):
        return ReplacementHistoryVerdict(
            permits_one_replacement=False,
            reason=(
                f"a durable ONESHOT_MARKER_CONSUMED receipt already exists for "
                f"{REPLACEMENT_PURPOSE!r} -- the single replacement is already consumed"
            ),
        )

    any_replacement_receipt = [r for r in receipts if r.purpose == REPLACEMENT_PURPOSE]
    if any_replacement_receipt:
        return ReplacementHistoryVerdict(
            permits_one_replacement=False,
            reason=(
                f"durable history already carries a receipt for "
                f"{REPLACEMENT_PURPOSE!r} of class "
                f"{any_replacement_receipt[0].receipt_class!r}"
            ),
        )

    live_replacement_markers = [m for m in live_markers if m.purpose == REPLACEMENT_PURPOSE]
    if live_replacement_markers:
        return ReplacementHistoryVerdict(
            permits_one_replacement=False,
            reason=f"a live one-shot marker already exists for {REPLACEMENT_PURPOSE!r}",
        )

    return ReplacementHistoryVerdict(
        permits_one_replacement=True,
        reason=(
            "original history establishes exactly one consumed, non-qualifying "
            "original run and zero prior replacement activity"
        ),
    )

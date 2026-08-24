"""Phase-5 qualification classification (ADR-0011 §5; P5-B Part 2/3).

Single mechanically closed classification path: the deterministic
outcome is computed WITHOUT ever reading a supplied successor bundle's
claimed ``qualification_outcome``; structural consistency (the claim
must equal the computed result) and cause-to-window-consumption binding
are then enforced inside ``classify_run`` itself, for every
execution-time outcome, so no external caller convention can skip them.
``MISSING_LOST`` is never producible by this module's single-run path —
only by ``independent_review_slots``, which also finalizes
``DUPLICATE_NONQUALIFYING`` for multiple distinct scheduled executions
discovered in one disjoint ownership interval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Sequence

from contracts.schemas import CostRow

from .bundle import (
    BrokenPredecessor,
    ControlStateTransitionError,
    GenesisManifest,
    SlotSuccessorCandidate,
    _candidate_for,
    authorized_predecessor,
    validate_control_state_transition,
)
from .cadence import CADENCE_TRIGGER_EUR_MICROS
from .models import (
    Phase5ControlState,
    QualificationSlotOutcome,
    QualificationWindowRecord,
    SentinelRunEvidence,
    sha256_hex_of_model,
)


class InconsistentEvidence(Exception):
    """A supplied successor bundle's claimed outcome, or its recorded
    window-consumption state, disagrees with what was independently and
    deterministically computed."""


class EvidenceCorruption(Exception):
    """The same GitHub execution identity was supplied more than once
    with conflicting metadata."""


# ---------------------------------------------------------------------------
# Typed classification inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GithubRunMetadata:
    workflow_identity: str
    github_run_id: str
    run_attempt: int
    event: str
    ref: str
    source_sha: str
    created_at: datetime
    run_started_at: datetime | None


@dataclass(frozen=True)
class QualificationChainContext:
    genesis_identity: str
    genesis: GenesisManifest
    genesis_state: Phase5ControlState
    slot_candidates: tuple[SlotSuccessorCandidate, ...] = field(default_factory=tuple)


def minutes_between(expected_at_utc: datetime, observed_at_utc: datetime) -> int:
    return int((observed_at_utc - expected_at_utc).total_seconds() // 60)


def _outcome(
    outcome: str,
    window_id: str,
    slot_n: int,
    reason: str,
    *,
    now: datetime,
    delay_minutes: int | None = None,
    determined_by: Literal["single_run_classification", "independent_review"] = "single_run_classification",
) -> QualificationSlotOutcome:
    return QualificationSlotOutcome(
        schema_version=1,
        window_id=window_id,
        slot_index=slot_n,
        outcome=outcome,  # type: ignore[arg-type]
        delay_minutes=delay_minutes,
        classified_at_utc=now,
        classification_reason=reason,
        determined_by=determined_by,
    )


# ---------------------------------------------------------------------------
# Successor provenance binding
# ---------------------------------------------------------------------------


def validate_successor_context(
    successor_manifest,
    *,
    window: QualificationWindowRecord,
    slot_n: int,
    github_run: GithubRunMetadata,
    evidence: SentinelRunEvidence,
) -> None:
    """Static context binding only — never reads the manifest's claimed
    qualification_outcome."""
    expected_slot = next(s for s in window.expected_slots if s.slot_index == slot_n)
    if (
        successor_manifest.github_run_id != github_run.github_run_id
        or successor_manifest.sentinel_run_id != evidence.run_id
        or successor_manifest.window_id != window.window_id
        or successor_manifest.window_record_sha256 != sha256_hex_of_model(window)
        or successor_manifest.slot_index != slot_n
        or successor_manifest.expected_slot_utc != expected_slot.expected_at_utc
        or successor_manifest.source_sha != window.source_sha
        or successor_manifest.workflow_identity != window.scheduled_workflow_identity
        or successor_manifest.ref != window.ref
        or successor_manifest.event != github_run.event
        or successor_manifest.run_attempt != github_run.run_attempt
        or successor_manifest.github_run_created_at_utc != github_run.created_at
        or successor_manifest.github_run_started_at_utc != github_run.run_started_at
    ):
        raise BrokenPredecessor("successor bundle does not match this run's context")


def _resolve_predecessor_state(
    window: QualificationWindowRecord, slot_n: int, chain: QualificationChainContext
) -> Phase5ControlState:
    if slot_n == 1:
        return chain.genesis_state
    pred_identity, _ = authorized_predecessor(
        window, slot_n, chain.genesis_identity, chain.genesis, chain.slot_candidates
    )
    return _candidate_for(pred_identity, chain.slot_candidates).control_state


# ---------------------------------------------------------------------------
# Cause-to-window-consumption binding
# ---------------------------------------------------------------------------


def _validate_consumption_binding_for_qualifying(
    slot_n: int, pred_state: Phase5ControlState, successor: SlotSuccessorCandidate
) -> None:
    s = successor.control_state
    spend = s.last_accounted_spend_eur_micros
    if spend > CADENCE_TRIGGER_EUR_MICROS and not pred_state.window_consumed:
        if not s.window_consumed or s.window_consume_reason != "POST_RUN_COST_TRIGGER":
            raise InconsistentEvidence(
                "a post-run EUR40 crossing must consume the window with POST_RUN_COST_TRIGGER"
            )
    elif slot_n == 5:
        if not (s.window_consumed and s.window_consume_reason is None):
            raise InconsistentEvidence(
                "a slot-5 qualifying successor must carry the clean-completion shape"
            )
    else:
        if s.window_consumed != pred_state.window_consumed:
            raise InconsistentEvidence("a qualifying mid-window successor must not consume the window")


def _validate_consumption_binding_for_nonqualifying(
    computed: QualificationSlotOutcome, successor: SlotSuccessorCandidate
) -> None:
    s = successor.control_state
    if not s.window_consumed or s.window_consume_reason != computed.outcome:
        raise InconsistentEvidence(
            "successor control state does not record the consuming outcome truthfully"
        )


def _finalize_against_manifest(successor: SlotSuccessorCandidate | None, computed: QualificationSlotOutcome) -> None:
    """Runs on EVERY execution-time result, inside classify_run itself —
    no external caller has to remember to invoke this."""
    if successor is None:
        return
    if successor.manifest.qualification_outcome != computed.outcome:
        raise InconsistentEvidence(
            f"manifest claims {successor.manifest.qualification_outcome!r}, "
            f"classifier computed {computed.outcome!r}"
        )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def derive_pre_successor_outcome(
    window: QualificationWindowRecord,
    slot_n: int,
    github_run: GithubRunMetadata,
    sentinel_run_evidence: Sequence[SentinelRunEvidence],
    cost_rows: Sequence[CostRow],
    *,
    window_already_consumed: bool,
) -> "tuple[str, str] | None":
    """P5-B Part 3/3 adapter seam (revision c, seam 2): owns ONLY the
    already-landed early branch order — consumed-window, event,
    workflow/ref/source, run_attempt, Sentinel evidence count,
    terminality, source, judgment mode, and runtime CostRow count.
    Extracted from ``_compute_outcome`` verbatim, not duplicated: this
    is the single source for that branch order, and it is never given
    a successor bundle to read.

    Returns a concrete ``(outcome, reason)`` pair when one of those
    branches is final; returns ``None`` when a valid successor bundle
    is required to continue classification — the caller must then
    validate the successor chain BEFORE ever reaching timing, exactly
    as before this extraction."""
    if window_already_consumed:
        return "STATE_CHAIN_FAILURE", "window already consumed"
    if github_run.event != "schedule":
        return "WRONG_PROVENANCE_NONQUALIFYING", "wrong event"
    if (
        github_run.workflow_identity != window.scheduled_workflow_identity
        or github_run.ref != window.ref
        or github_run.source_sha != window.source_sha
    ):
        return "WRONG_PROVENANCE_NONQUALIFYING", "wrong workflow/ref/sha"
    if github_run.run_attempt != 1:
        return "DUPLICATE_NONQUALIFYING", "run_attempt > 1"

    matching = [e for e in sentinel_run_evidence if e.github_run_id == github_run.github_run_id]
    if len(matching) != 1:
        return "FAILED_NONTERMINAL", f"{len(matching)} evidence candidates for this run"
    evidence = matching[0]
    if evidence.status != "COMPLETED":
        return "FAILED_NONTERMINAL", "non-terminal Sentinel run"
    if evidence.source != window.qualifying_source:
        return "WRONG_PROVENANCE_NONQUALIFYING", "not a live run"
    if evidence.judgment_mode != window.qualifying_judgment_mode:
        return "WRONG_PROVENANCE_NONQUALIFYING", "not agent judgment mode"
        # a scheduled stub run never qualifies even with perfect GitHub provenance

    cost_matches = [r for r in cost_rows if r.run_id == evidence.run_id]
    if len(cost_matches) != 1:
        return "COSTROW_INVALID", f"{len(cost_matches)} matching CostRows"

    return None


def derive_timing_outcome(
    window: QualificationWindowRecord, slot_n: int, github_run: GithubRunMetadata
) -> "tuple[str, str, int | None]":
    """P5-B Part 3/3 adapter seam (revision c, seam 2): owns ONLY the
    already-landed negative-delay / within-tolerance / outside-tolerance
    calculation. Extracted verbatim from ``_compute_outcome`` — the
    caller must reach this only AFTER the successor chain (predecessor
    hash, artifact identity, control-state transition, carried CostRow
    agreement) has already validated; this function never validates
    that chain and never runs ahead of it."""
    expected = next(s for s in window.expected_slots if s.slot_index == slot_n)
    delay = minutes_between(expected.expected_at_utc, github_run.created_at)
    if delay < 0:
        return "WRONG_PROVENANCE_NONQUALIFYING", "observed before expected slot", None
    if delay <= window.tolerance_minutes:
        return "QUALIFYING", "within tolerance", delay
    return "LATE_NONQUALIFYING", "outside tolerance", delay


def _compute_outcome(
    window: QualificationWindowRecord,
    slot_n: int,
    github_run: GithubRunMetadata,
    sentinel_run_evidence: Sequence[SentinelRunEvidence],
    cost_rows: Sequence[CostRow],
    successor: SlotSuccessorCandidate | None,
    chain: QualificationChainContext,
    *,
    window_already_consumed: bool,
    now: datetime,
) -> QualificationSlotOutcome:
    """Deterministic. NEVER reads a supplied successor's claimed outcome.

    Same semantic order as before the Part-3 extraction: early branches
    (``derive_pre_successor_outcome``) -> require/validate successor ->
    predecessor + transition + carried CostRow validation -> timing
    (``derive_timing_outcome``). Timing is reached only when every
    earlier branch, including the full successor-chain validation, has
    already passed."""
    wid = window.window_id

    early = derive_pre_successor_outcome(
        window,
        slot_n,
        github_run,
        sentinel_run_evidence,
        cost_rows,
        window_already_consumed=window_already_consumed,
    )
    if early is not None:
        outcome, reason = early
        return _outcome(outcome, wid, slot_n, reason, now=now)

    # from here a VALID successor bundle is required for qualification
    if successor is None:
        return _outcome(
            "STATE_CHAIN_FAILURE",
            wid,
            slot_n,
            "no successor bundle for an otherwise qualification-ready run",
            now=now,
        )

    evidence = next(e for e in sentinel_run_evidence if e.github_run_id == github_run.github_run_id)
    cost_matches = [r for r in cost_rows if r.run_id == evidence.run_id]

    try:
        validate_successor_context(
            successor.manifest, window=window, slot_n=slot_n, github_run=github_run, evidence=evidence
        )
        pred_identity, pred_manifest = authorized_predecessor(
            window, slot_n, chain.genesis_identity, chain.genesis, chain.slot_candidates
        )
        pred_state = _resolve_predecessor_state(window, slot_n, chain)
        if (
            successor.manifest.predecessor_manifest_sha256 != sha256_hex_of_model(pred_manifest)
            or successor.manifest.predecessor_artifact_id_or_name != pred_identity
        ):
            raise BrokenPredecessor("successor's predecessor link does not match the chain")
        validate_control_state_transition(window=window, predecessor_state=pred_state, successor=successor)

        # exactly one matching CostRow must be present in the bundle's OWN
        # validated carried ledger; separately-supplied runtime CostRow
        # evidence can never independently make a successor qualify while
        # its own carried ledger lacks the run
        carried_matches = [r for r in successor.cost_rows if r.run_id == evidence.run_id]
        if len(carried_matches) != 1:
            return _outcome(
                "COSTROW_INVALID",
                wid,
                slot_n,
                "successor's own carried cost ledger lacks exactly one matching CostRow",
                now=now,
            )
        if carried_matches[0] != cost_matches[0]:
            return _outcome(
                "COSTROW_INVALID",
                wid,
                slot_n,
                "runtime CostRow evidence disagrees with the successor's carried ledger",
                now=now,
            )
    except (BrokenPredecessor, ControlStateTransitionError):
        return _outcome(
            "STATE_CHAIN_FAILURE", wid, slot_n, "broken predecessor/successor state chain", now=now
        )

    # STATE_CHAIN_FAILURE above (an invalid successor/predecessor/transition)
    # is never overwritten by manufacturing a timing-based outcome — timing
    # is reached only once the successor chain has already validated clean.
    outcome, reason, delay = derive_timing_outcome(window, slot_n, github_run)
    return _outcome(outcome, wid, slot_n, reason, now=now, delay_minutes=delay)


def classify_run(
    window: QualificationWindowRecord,
    slot_n: int,
    github_run: GithubRunMetadata,
    sentinel_run_evidence: Sequence[SentinelRunEvidence],
    cost_rows: Sequence[CostRow],
    successor: SlotSuccessorCandidate | None,
    chain: QualificationChainContext,
    *,
    window_already_consumed: bool,
    now: datetime,
) -> QualificationSlotOutcome:
    computed = _compute_outcome(
        window,
        slot_n,
        github_run,
        sentinel_run_evidence,
        cost_rows,
        successor,
        chain,
        window_already_consumed=window_already_consumed,
        now=now,
    )
    if successor is not None:
        if computed.outcome == "QUALIFYING":
            pred_state = _resolve_predecessor_state(window, slot_n, chain)
            _validate_consumption_binding_for_qualifying(slot_n, pred_state, successor)
        else:
            _validate_consumption_binding_for_nonqualifying(computed, successor)
    _finalize_against_manifest(successor, computed)
    return computed


# ---------------------------------------------------------------------------
# Run-to-slot ownership + independent review
# ---------------------------------------------------------------------------


def _slot_ownership_interval(window: QualificationWindowRecord, slot_index: int) -> tuple[datetime, datetime]:
    by_index = {slot.slot_index: slot for slot in window.expected_slots}
    start = by_index[slot_index].expected_at_utc
    end = by_index[slot_index + 1].expected_at_utc if slot_index < 5 else start + timedelta(hours=24)
    return start, end


@dataclass(frozen=True)
class SlotAssociation:
    kind: Literal["MATCHED", "NO_MATCH", "DUPLICATE"]
    run: GithubRunMetadata | None = None
    duplicate_runs: tuple[GithubRunMetadata, ...] = ()


def _normalize_run_history(history: Sequence[GithubRunMetadata]) -> list[GithubRunMetadata]:
    by_key: dict[tuple[str, int], GithubRunMetadata] = {}
    for run in history:
        key = (run.github_run_id, run.run_attempt)
        if key in by_key:
            if by_key[key] != run:
                raise EvidenceCorruption("same execution identity with conflicting metadata")
            continue  # identical duplicate API representation: one execution
        by_key[key] = run
    return list(by_key.values())


def associate_runs_to_slots(
    window: QualificationWindowRecord, github_run_history: Sequence[GithubRunMetadata]
) -> dict[int, SlotAssociation]:
    normalized = _normalize_run_history(github_run_history)
    matching_identity = [
        run
        for run in normalized
        if run.event == "schedule"
        and run.workflow_identity == window.scheduled_workflow_identity
        and run.ref == window.ref
        and run.source_sha == window.source_sha
    ]
    result: dict[int, SlotAssociation] = {}
    for slot in window.expected_slots:
        start, end = _slot_ownership_interval(window, slot.slot_index)
        in_window = [run for run in matching_identity if start <= run.created_at < end]
        if not in_window:
            result[slot.slot_index] = SlotAssociation(kind="NO_MATCH")
        elif len(in_window) == 1:
            result[slot.slot_index] = SlotAssociation(kind="MATCHED", run=in_window[0])
        else:
            result[slot.slot_index] = SlotAssociation(kind="DUPLICATE", duplicate_runs=tuple(in_window))
    return result


def independent_review_slots(
    window: QualificationWindowRecord, github_run_history: Sequence[GithubRunMetadata], now: datetime
) -> list[QualificationSlotOutcome]:
    """MISSING_LOST is mechanically final only after a slot's entire
    ownership interval has closed. DUPLICATE_NONQUALIFYING is final
    immediately once two distinct executions are found in one interval
    — it is never MISSING_LOST and never QUALIFYING."""
    associations = associate_runs_to_slots(window, github_run_history)
    out: list[QualificationSlotOutcome] = []
    for slot in window.expected_slots:
        assoc = associations[slot.slot_index]
        if assoc.kind == "DUPLICATE":
            out.append(
                _outcome(
                    "DUPLICATE_NONQUALIFYING",
                    window.window_id,
                    slot.slot_index,
                    "multiple distinct scheduled executions in one ownership interval",
                    now=now,
                    determined_by="independent_review",
                )
            )
            continue
        _, ownership_close = _slot_ownership_interval(window, slot.slot_index)
        if now < ownership_close:
            continue  # MISSING_LOST final ONLY at ownership close
        if assoc.kind == "NO_MATCH":
            out.append(
                _outcome(
                    "MISSING_LOST",
                    window.window_id,
                    slot.slot_index,
                    "no matching scheduled execution found by ownership close",
                    now=now,
                    determined_by="independent_review",
                )
            )
        # MATCHED: never MISSING_LOST; that run is classified separately
    return out

"""Non-lineage Phase-5 Actions evidence records (P5-B Part 3/3).

Every record here is deliberately outside the lineage/chain vocabulary:
none carries a predecessor link, none is a ``StateBundleManifest``
variant, and none of this module's artifact names (see
``artifact_names.py``) can ever be mistaken for a GENESIS, slot
successor, control-refusal, or one-shot-marker artifact by
``bundle.select_active_window`` or any chain walker. They exist purely
to give every workflow run — including a designed pre-window refusal
or a cadence skip — a durable, inspectable, ``if: always()`` artifact.

Same canonicalization discipline as ``models.py``: ``extra="forbid"``,
canonical JSON via ``models.canonical_json_bytes``, UTC-only
timestamps, no credential/token/secret/local-path value ever stored.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts.schemas import CostRow

_HEX40 = re.compile(r"[0-9a-f]{40}")


def _require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError("naive datetimes are not permitted")
    if offset != timedelta(0):
        raise ValueError("UTC offset must be exactly zero")
    return value


def _require_identifier(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be empty after stripping")
    return value


def _require_hex40(value: str) -> str:
    if not _HEX40.fullmatch(value):
        raise ValueError("must be exactly 40 lowercase hexadecimal characters")
    return value


class StepEvidence(BaseModel):
    """Canonical mirror of ``preflight.PreflightStepRecord`` — the
    dataclass the runtime ledger uses is not itself a pydantic model
    (it never needs schema validation, only in-process ordering), but
    every evidence artifact serializes its ledger as a tuple of these
    so the run's own order proof travels with the artifact."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    status: Literal["OK", "REFUSED", "EARLY_EXIT"]
    detail: str

    @model_validator(mode="after")
    def _validate(self) -> "StepEvidence":
        _require_identifier(self.step_id)
        return self


class _IdentityFields(BaseModel):
    """Shared identity shape every evidence record carries: exactly the
    GitHub execution context it was produced by, never a token/secret."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    workflow_identity: str
    github_run_id: str
    run_attempt: int = Field(ge=1)
    event: str
    ref: str
    source_sha: str
    created_at_utc: datetime
    steps: tuple[StepEvidence, ...]

    @model_validator(mode="after")
    def _validate_identity(self) -> "_IdentityFields":
        _require_identifier(self.workflow_identity)
        _require_identifier(self.github_run_id)
        _require_identifier(self.event)
        _require_identifier(self.ref)
        _require_hex40(self.source_sha)
        _require_utc(self.created_at_utc)
        return self


class PreWindowRefusalEvidence(_IdentityFields):
    reason: Literal["NO_ACTIVE_WINDOW", "SLOT_NOT_OPEN"]


class ScheduledAttemptEvidence(_IdentityFields):
    disposition: str

    @model_validator(mode="after")
    def _validate(self) -> "ScheduledAttemptEvidence":
        _require_identifier(self.disposition)
        return self


class RehearsalEvidenceRecord(_IdentityFields):
    expected_source_sha: str
    outcome: str

    @model_validator(mode="after")
    def _validate(self) -> "RehearsalEvidenceRecord":
        _require_hex40(self.expected_source_sha)
        _require_identifier(self.outcome)
        return self


class ProbeEvidenceRecord(_IdentityFields):
    """Seam 3 (revision c): ``disposition`` is a closed vocabulary, and
    ``CAPABILITY_PASS`` is schema-unconstructible without accounting
    evidence — a source/preflight/WIF/OIDC/FX/setup failure can only
    ever be recorded as ``CAPABILITY_FAIL``.

    ``auth_mode`` (dispatch q77-p5c-execute-a, C0-C) is additive and
    optional for schema-version compatibility: it carries the runner's
    own persisted-row-derived auth provenance, never an assumed label,
    and ``CAPABILITY_PASS`` is additionally schema-unconstructible
    unless it exactly equals the WIF federation label."""

    expected_source_sha: str
    disposition: Literal["CAPABILITY_PASS", "CAPABILITY_FAIL"]
    cost_rows: tuple[CostRow, ...]
    accounted_total_eur_micros: int = Field(ge=0)
    auth_mode: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "ProbeEvidenceRecord":
        _require_hex40(self.expected_source_sha)
        if self.disposition == "CAPABILITY_PASS":
            if not self.cost_rows:
                raise ValueError("CAPABILITY_PASS requires at least one accounted CostRow")
            if self.auth_mode != "github-actions-wif-federation":
                raise ValueError(
                    "CAPABILITY_PASS requires auth_mode == 'github-actions-wif-federation'"
                )
        return self


# Replacement-provenance vocabularies (ADR-0012 section 18; Amendment A2
# rule 6; dispatch q77-p5d-repair-stage2-implement-a). Kept as two
# DISJOINT closed Literals so a raw observed signal can never even be
# assigned to ``termination_source`` -- the type system itself makes
# "SIGINT proves infrastructure failure" unconstructible, independent
# of any validator logic. A generic, manual, forced or unknown
# cancellation is never, by itself, evidence of objective
# infrastructure invalidity (owner-ruled correction, 2026-09-15).
TerminationCause = Literal[
    "SESSION_DEADLINE",
    "INVOCATION_STALL_DEADLINE",
    "WATCHDOG",
    "PRE_PROVIDER_FAILURE",
    "RUNNER_EXCEPTION",
]

ObservedSignal = Literal["SIGINT", "SIGTERM", "UNKNOWN_EXTERNAL_TERMINATION"]

# Consumed-but-unclassified termination (ADR-0012 Amendment A2 rule 6;
# dispatch q77-p5d-repair-stage2b1-implement-a). ``unclassified_basis``
# says only WHY no trusted runner terminal evidence exists -- never a
# cause. ``terminal_writer`` names which process authored a terminal
# record; the finalizer is structurally incapable of authoring quality.
UnclassifiedBasis = Literal[
    "OBSERVED_SIGNAL",
    "RUNNER_TERMINAL_EVIDENCE_ABSENT",
    "RUNNER_TERMINAL_EVIDENCE_UNTRUSTED",
]
TerminalWriter = Literal["RUNNER", "FINALIZER"]
_RUNNER_OBSERVED_CAUSES = frozenset({"PRE_PROVIDER_FAILURE", "RUNNER_EXCEPTION"})

# Deliberately duplicated string literals (this package's established
# convention -- see receipts.py's own duplicated-vs-imported note) so
# this module carries no new intra-package import edge.
# tests/test_phase5_replacement.py cross-pins these against
# sentinel/phase5/replacement.py's public constants.
_REPLACEMENT_WORKFLOW_IDENTITY = ".github/workflows/sentinel-official-gate.yml"
_REPLACEMENT_OF_RUN_ID = "32880880053"
_REPLACEMENT_OWNER_RULING_ID = "q77-p5d-replacement-owner-ruling-a"
_REPLACEMENT_MARKER_PURPOSE = "P5D_REPLACEMENT_SONNET_GATE"


class GateEvidenceRecord(_IdentityFields):
    """Seam 3 (revision c): the full reproducible gate result, never a
    summary integer. ``HONEST_FAIL`` requires the designated Sonnet
    execution to have actually entered the frozen gate — schema-
    unconstructible otherwise; ``INFRASTRUCTURE_FAILURE`` is the only
    disposition permitted for a source/preflight/WIF/OIDC/FX/setup
    failure, and it can never satisfy P5-D.

    ``auth_mode`` and ``failed_checks`` (dispatch
    q77-p5d-s1-evidence-repair-a) are additive and optional for
    schema-version compatibility, mirroring ``ProbeEvidenceRecord``'s
    own ``auth_mode`` precedent:

    - ``auth_mode`` carries the gate session's own persisted-row-
      derived auth provenance (never an assumed or hard-coded label),
      so an independent verifier can establish it from the uploaded
      artifact alone after the ephemeral runner is gone. ``GREEN`` and
      ``HONEST_FAIL`` are additionally schema-unconstructible unless it
      exactly equals the WIF federation label —
      ``INFRASTRUCTURE_FAILURE`` carries no such requirement, since a
      pre-provider failure legitimately has none.
    - ``failed_checks`` is the machine-derived record of every gate
      check line that did not pass (scoring, invariants, cost,
      execution-validity alike — whatever the gate's own ``checks``
      ledger actually failed), distinct from ``miss_patterns`` (which
      stays scoped to unmatched *scoring* findings only, unchanged).
      A gate that fails solely on cost or an invariant or execution-
      validity predicate — with perfect scoring, and therefore empty
      ``miss_patterns`` — remains a legitimate, schema-constructible
      ``HONEST_FAIL`` because ``failed_checks`` is never empty when the
      gate did not pass.

    ``UNCLASSIFIED_TERMINATION`` (dispatch
    q77-p5d-repair-stage2b1-implement-a; ADR-0012 Amendment A2 rule 6)
    records a consumed execution whose terminal state cannot be
    classified as objective infrastructure failure: a signal or an
    unknown cancellation was observed, or trusted runner terminal
    evidence is absent or untrusted. It carries no termination_source,
    no quality content, and is never runner-authored."""

    expected_source_sha: str
    model: str
    profile_name: str
    run_ids: tuple[str, ...]
    scoring: dict
    thresholds: dict
    invariant_results: dict
    execution_validity: dict
    miss_patterns: tuple[str, ...]
    failed_checks: tuple[str, ...] = ()
    cost_rows: tuple[CostRow, ...]
    accounted_total_eur_micros: int = Field(ge=0)
    disposition: Literal["GREEN", "HONEST_FAIL", "INFRASTRUCTURE_FAILURE", "UNCLASSIFIED_TERMINATION"]
    auth_mode: str | None = None

    # Replacement-provenance fields (ADR-0012 section 18; Amendment A2
    # rule 6; dispatch q77-p5d-repair-stage2-implement-a). Additive and
    # optional for schema-version compatibility -- the base schema
    # stays backward compatible and permissive; the SEPARATE, stricter
    # ``validate_replacement_provenance`` function below is what the
    # P5-E seam and (later) the finalizer actually apply to decide
    # replacement eligibility. Nothing in this dispatch's untouched
    # ``scripts/run_phase5_official_gate.py::cmd_execute`` sets any of
    # these fields, so its existing INFRASTRUCTURE_FAILURE construction
    # path is unaffected.
    replacement_of_run_id: str | None = None
    owner_ruling_id: str | None = None
    marker_purpose: str | None = None
    envelope_id: str | None = None
    envelope_version: str | None = None
    termination_source: TerminationCause | None = None
    observed_signals: tuple[ObservedSignal, ...] = ()
    # Stage 2B-1 (dispatch q77-p5d-repair-stage2b1-implement-a). Both
    # optional for general/historical parsing; the strict replacement
    # requirements live in ``validate_replacement_provenance``.
    unclassified_basis: UnclassifiedBasis | None = None
    terminal_writer: TerminalWriter | None = None

    @model_validator(mode="after")
    def _validate(self) -> "GateEvidenceRecord":
        _require_hex40(self.expected_source_sha)
        _require_identifier(self.model)
        _require_identifier(self.profile_name)
        if self.disposition in ("GREEN", "HONEST_FAIL"):
            if not (self.run_ids and self.scoring and self.execution_validity and self.cost_rows):
                raise ValueError(
                    f"{self.disposition} requires run_ids, scoring, execution_validity "
                    "and cost_rows to be non-empty — the gate must have actually run"
                )
            if self.auth_mode != "github-actions-wif-federation":
                raise ValueError(
                    f"{self.disposition} requires auth_mode == 'github-actions-wif-federation'"
                )
        if self.disposition == "HONEST_FAIL" and not self.failed_checks:
            raise ValueError(
                "HONEST_FAIL requires non-empty failed_checks evidence — the "
                "machine-derived record of which gate check(s) actually failed"
            )
        if self.termination_source is not None and self.disposition != "INFRASTRUCTURE_FAILURE":
            raise ValueError(
                "termination_source may be set only when disposition is INFRASTRUCTURE_FAILURE"
            )
        if len(set(self.observed_signals)) != len(self.observed_signals):
            raise ValueError("observed_signals must not contain duplicates")
        if self.termination_source in _RUNNER_OBSERVED_CAUSES and self.observed_signals:
            raise ValueError(
                f"termination_source {self.termination_source} must not be combined with an "
                "observed signal -- an exception seen alongside a signal is never an objective cause"
            )
        if self.unclassified_basis is not None and self.disposition != "UNCLASSIFIED_TERMINATION":
            raise ValueError(
                "unclassified_basis may be set only when disposition is UNCLASSIFIED_TERMINATION"
            )
        if self.disposition == "UNCLASSIFIED_TERMINATION":
            if self.unclassified_basis is None:
                raise ValueError("UNCLASSIFIED_TERMINATION requires unclassified_basis")
            if self.unclassified_basis == "OBSERVED_SIGNAL" and not self.observed_signals:
                raise ValueError("unclassified_basis OBSERVED_SIGNAL requires at least one observed signal")
            if self.unclassified_basis == "RUNNER_TERMINAL_EVIDENCE_ABSENT" and self.observed_signals:
                raise ValueError(
                    "unclassified_basis RUNNER_TERMINAL_EVIDENCE_ABSENT requires no observed signal"
                )
            if (
                self.run_ids or self.scoring or self.thresholds or self.invariant_results
                or self.execution_validity or self.miss_patterns or self.failed_checks
                or self.cost_rows or self.accounted_total_eur_micros != 0
            ):
                raise ValueError(
                    "UNCLASSIFIED_TERMINATION must carry no quality content -- run_ids, scoring, "
                    "thresholds, invariant_results, execution_validity, miss_patterns, "
                    "failed_checks and cost_rows must be empty and accounted total zero"
                )
            if self.terminal_writer == "RUNNER":
                raise ValueError("UNCLASSIFIED_TERMINATION is never authored by the RUNNER")
        if self.terminal_writer == "FINALIZER" and self.disposition in ("GREEN", "HONEST_FAIL"):
            raise ValueError(
                f"{self.disposition} can never be authored by the FINALIZER -- the finalizer is "
                "structurally incapable of authoring a quality result"
            )
        return self


def validate_replacement_provenance(
    record: GateEvidenceRecord, *, expected_source_sha: str
) -> None:
    """Strict replacement-provenance validation (ADR-0012 section 18;
    Amendment A2 rule 6; dispatch q77-p5d-repair-stage2-implement-a).

    Separate from, and stricter than, ``GateEvidenceRecord``'s own
    permissive schema: a record that parses is not automatically
    eligible replacement evidence. Raises ``ValueError`` on the first
    defect found (never returns a boolean), so a caller cannot
    accidentally ignore a falsy result. Stage 2A wires this into the
    P5-E seam (``scripts/run_phase5_window_freeze.py``) for any
    retained replacement evidence artifact; it constructs, uploads, or
    consumes no evidence itself.

    Correction (owner-ruled, 2026-09-15, ADR-0012 Amendment A2 rule 6):
    a generic, manual, forced or unknown cancellation is never, by
    itself, evidence of objective infrastructure invalidity. This
    function therefore never treats a value in ``observed_signals``
    (SIGINT, SIGTERM, UNKNOWN_EXTERNAL_TERMINATION) as satisfying or
    substituting for ``termination_source``: an INFRASTRUCTURE_FAILURE
    claim is accepted only when ``termination_source`` carries a
    positively-established ``TerminationCause`` value. The type system
    already makes assigning a raw signal name to ``termination_source``
    unconstructible, since ``TerminationCause`` and ``ObservedSignal``
    are disjoint closed vocabularies.

    Terminal-writer provenance (dispatch
    q77-p5d-repair-stage2b1-implement-a): replacement GREEN/HONEST_FAIL
    must be RUNNER-authored, UNCLASSIFIED_TERMINATION must be
    FINALIZER-authored, and INFRASTRUCTURE_FAILURE must name RUNNER or
    FINALIZER -- never None. ``terminal_writer`` stays optional for
    general/historical parsing; only this replacement gate requires it.
    """
    if not _HEX40.fullmatch(expected_source_sha):
        raise ValueError("expected_source_sha is not exactly 40 lowercase hexadecimal characters")
    if record.workflow_identity != _REPLACEMENT_WORKFLOW_IDENTITY:
        raise ValueError(
            f"replacement evidence workflow_identity must be exactly "
            f"{_REPLACEMENT_WORKFLOW_IDENTITY!r}"
        )
    if record.run_attempt != 1:
        raise ValueError("replacement evidence run_attempt must be exactly 1")
    if record.source_sha != expected_source_sha or record.expected_source_sha != expected_source_sha:
        raise ValueError("replacement evidence source_sha does not match expected_source_sha")
    if record.replacement_of_run_id != _REPLACEMENT_OF_RUN_ID:
        raise ValueError(
            f"replacement evidence replacement_of_run_id must be exactly "
            f"{_REPLACEMENT_OF_RUN_ID!r}"
        )
    if record.owner_ruling_id != _REPLACEMENT_OWNER_RULING_ID:
        raise ValueError(
            f"replacement evidence owner_ruling_id must be exactly "
            f"{_REPLACEMENT_OWNER_RULING_ID!r}"
        )
    if record.marker_purpose != _REPLACEMENT_MARKER_PURPOSE:
        raise ValueError(
            f"replacement evidence marker_purpose must be exactly "
            f"{_REPLACEMENT_MARKER_PURPOSE!r}"
        )
    if record.envelope_id is None or not record.envelope_id.strip():
        raise ValueError("replacement evidence requires a non-empty envelope_id")
    if record.envelope_version is None or not record.envelope_version.strip():
        raise ValueError("replacement evidence requires a non-empty envelope_version")

    if record.disposition == "INFRASTRUCTURE_FAILURE":
        if record.termination_source is None:
            raise ValueError(
                "replacement INFRASTRUCTURE_FAILURE evidence requires a positively "
                "established termination_source; a raw observed signal alone "
                "(ADR-0012 Amendment A2 rule 6) is never sufficient proof"
            )
        if record.terminal_writer not in ("RUNNER", "FINALIZER"):
            raise ValueError(
                "replacement INFRASTRUCTURE_FAILURE evidence requires terminal_writer "
                "RUNNER or FINALIZER; None is never accepted"
            )
    elif record.disposition in ("GREEN", "HONEST_FAIL"):
        if record.termination_source is not None:
            raise ValueError(
                f"{record.disposition} replacement evidence must not carry a termination_source"
            )
        if record.terminal_writer != "RUNNER":
            raise ValueError(
                f"{record.disposition} replacement evidence requires terminal_writer == 'RUNNER'"
            )
    elif record.disposition == "UNCLASSIFIED_TERMINATION":
        if record.termination_source is not None:
            raise ValueError(
                "replacement UNCLASSIFIED_TERMINATION evidence must not carry a termination_source"
            )
        if record.unclassified_basis is None:
            raise ValueError("replacement UNCLASSIFIED_TERMINATION evidence requires unclassified_basis")
        if record.terminal_writer != "FINALIZER":
            raise ValueError(
                "replacement UNCLASSIFIED_TERMINATION evidence requires terminal_writer == 'FINALIZER'"
            )
    else:
        raise ValueError(f"unrecognized disposition for replacement evidence: {record.disposition!r}")


class FreezeRefusalEvidence(_IdentityFields):
    expected_source_sha: str
    reason: str

    @model_validator(mode="after")
    def _validate(self) -> "FreezeRefusalEvidence":
        _require_hex40(self.expected_source_sha)
        _require_identifier(self.reason)
        return self

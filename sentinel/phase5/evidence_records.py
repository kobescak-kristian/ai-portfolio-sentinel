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


class GateEvidenceRecord(_IdentityFields):
    """Seam 3 (revision c): the full reproducible gate result, never a
    summary integer. ``HONEST_FAIL`` requires the designated Sonnet
    execution to have actually entered the frozen gate — schema-
    unconstructible otherwise; ``INFRASTRUCTURE_FAILURE`` is the only
    disposition permitted for a source/preflight/WIF/OIDC/FX/setup
    failure, and it can never satisfy P5-D."""

    expected_source_sha: str
    model: str
    profile_name: str
    run_ids: tuple[str, ...]
    scoring: dict
    thresholds: dict
    invariant_results: dict
    execution_validity: dict
    miss_patterns: tuple[str, ...]
    cost_rows: tuple[CostRow, ...]
    accounted_total_eur_micros: int = Field(ge=0)
    disposition: Literal["GREEN", "HONEST_FAIL", "INFRASTRUCTURE_FAILURE"]

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
        if self.disposition == "HONEST_FAIL" and not self.miss_patterns:
            raise ValueError("HONEST_FAIL requires non-empty miss_patterns evidence")
        return self


class FreezeRefusalEvidence(_IdentityFields):
    expected_source_sha: str
    reason: str

    @model_validator(mode="after")
    def _validate(self) -> "FreezeRefusalEvidence":
        _require_hex40(self.expected_source_sha)
        _require_identifier(self.reason)
        return self

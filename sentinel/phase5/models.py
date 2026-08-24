"""Phase-5 canonical records (ADR-0011 §2, §4, §5, §6, §7; P5-B Part 2/3).

Every authoritative record here is a ``pydantic.BaseModel`` with
``ConfigDict(extra="forbid")`` — unknown or malformed authoritative
fields fail closed, matching the ``contracts/schemas.py`` precedent this
package deliberately imitates rather than imports from (those validators
are private to that module). Canonical serialization is UTF-8, sorted
keys, compact separators, no NaN/Infinity, and every timestamp forced
through the same UTC whole-second shape used by the rest of the ledger
(``contracts.schemas.serialize_db_datetime``). SHA-256 is always taken
over canonical bytes, never over pretty-printed text.

No record here carries a credential, token, secret value or local
absolute path.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts.schemas import serialize_db_datetime

# ---------------------------------------------------------------------------
# Canonical serialization — one implementation, used everywhere in this
# package. Mirrors the json.dumps(..., sort_keys=True, separators=(",",
# ":"), allow_nan=False) idiom already independently duplicated in
# telemetry/cost_ledger.py, sentinel/logs.py and runner/iteration_log.py.
# ---------------------------------------------------------------------------


def _canonicalize(value: object) -> object:
    if isinstance(value, datetime):
        return serialize_db_datetime(value)
    if isinstance(value, dict):
        return {key: _canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    return value


def canonical_json_bytes(model: BaseModel) -> bytes:
    """Deterministic canonical bytes for any Phase-5 record."""
    data = _canonicalize(model.model_dump(mode="python"))
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def canonical_json(model: BaseModel) -> str:
    return canonical_json_bytes(model).decode("utf-8")


def sha256_hex_of_model(model: BaseModel) -> str:
    return hashlib.sha256(canonical_json_bytes(model)).hexdigest()


def sha256_hex_of_file(path) -> str:  # Path, but avoid importing pathlib just for typing
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Local validator helpers — small, deliberately duplicated rather than
# reaching into contracts/schemas.py's private names, matching this
# repo's own established convention for this exact situation.
# ---------------------------------------------------------------------------

_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError("naive datetimes are not permitted")
    if offset != timedelta(0):
        raise ValueError("UTC offset must be exactly zero")
    return value


def _require_utc_optional(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _require_utc(value)


def _require_daily_slot_instant(value: datetime) -> datetime:
    """A scheduled-slot instant must be UTC, whole-second and exactly
    06:37:00 — consistent with the frozen ``37 6 * * *`` cron."""
    value = _require_utc(value)
    if (value.hour, value.minute, value.second, value.microsecond) != (6, 37, 0, 0):
        raise ValueError("expected slot instant must be exactly 06:37:00 UTC")
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


def _require_hex64(value: str) -> str:
    if not _HEX64.fullmatch(value):
        raise ValueError("must be exactly 64 lowercase hexadecimal characters")
    return value


def _require_relative_posix_path(value: str) -> str:
    if "\\" in value:
        raise ValueError("carried-file path must use forward slashes only")
    if value.startswith("/"):
        raise ValueError("carried-file path must not be absolute")
    segments = value.split("/")
    if any(segment in ("", "..") for segment in segments):
        raise ValueError("carried-file path must not contain empty or '..' segments")
    return value


# ---------------------------------------------------------------------------
# Qualification-window record (ADR-0011 §5; dispatch P5)
# ---------------------------------------------------------------------------


class ExpectedSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_index: int = Field(ge=1, le=5)
    expected_at_utc: datetime

    @model_validator(mode="after")
    def _validate(self) -> "ExpectedSlot":
        _require_daily_slot_instant(self.expected_at_utc)
        return self


class QualificationWindowRecord(BaseModel):
    """Immutable, frozen before slot 1 (ADR-0011 §5)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    window_id: str
    created_at_utc: datetime
    control_workflow_identity: str
    control_run_id: str
    source_sha: str
    scheduled_workflow_identity: str
    ref: Literal["refs/heads/main"]
    cron: Literal["37 6 * * *"]
    timezone: Literal["UTC"]
    tolerance_minutes: Literal[120]
    expected_slots: tuple[ExpectedSlot, ...]
    qualifying_source: Literal["live"]
    qualifying_judgment_mode: Literal["agent"]
    supersedes_window_id: str | None = None
    supersedes_window_record_sha256: str | None = None
    windows_task_name: str | None = None
    disabled_at_utc: datetime | None = None
    final_legacy_db_sha256: str | None = None
    legacy_row_counts: dict[str, int] | None = None
    dual_scheduler_verification_at_utc: datetime | None = None

    @model_validator(mode="after")
    def _validate(self) -> "QualificationWindowRecord":
        _require_identifier(self.window_id)
        _require_identifier(self.control_workflow_identity)
        _require_identifier(self.control_run_id)
        _require_hex40(self.source_sha)
        _require_identifier(self.scheduled_workflow_identity)
        _require_utc(self.created_at_utc)

        if len(self.expected_slots) != 5:
            raise ValueError("a qualification window has exactly five expected slots")
        indices = tuple(slot.slot_index for slot in self.expected_slots)
        if indices != (1, 2, 3, 4, 5):
            raise ValueError("expected_slots must be ordered exactly slot_index 1..5")
        instants = [slot.expected_at_utc for slot in self.expected_slots]
        for earlier, later in zip(instants, instants[1:]):
            if later - earlier != timedelta(hours=24):
                raise ValueError("expected slots must be exactly 24 hours apart")
        if instants[0] <= self.created_at_utc:
            raise ValueError("slot 1 must be strictly prospective relative to creation")

        supersedes_pair = (self.supersedes_window_id, self.supersedes_window_record_sha256)
        if (supersedes_pair[0] is None) != (supersedes_pair[1] is None):
            raise ValueError(
                "supersedes_window_id and supersedes_window_record_sha256 "
                "must both be null or both be populated"
            )
        if self.supersedes_window_id is not None:
            _require_identifier(self.supersedes_window_id)
            _require_hex64(self.supersedes_window_record_sha256)

        migration_fields = (
            self.windows_task_name,
            self.disabled_at_utc,
            self.final_legacy_db_sha256,
            self.legacy_row_counts,
            self.dual_scheduler_verification_at_utc,
        )
        populated = [field is not None for field in migration_fields]
        if any(populated) and not all(populated):
            raise ValueError(
                "migration-evidence fields must be all null or all populated together"
            )
        if self.disabled_at_utc is not None:
            _require_utc(self.disabled_at_utc)
            _require_hex64(self.final_legacy_db_sha256)
            _require_utc(self.dual_scheduler_verification_at_utc)
            if self.disabled_at_utc + timedelta(hours=24) > instants[0]:
                raise ValueError(
                    "Windows-scheduler disable must be at least 24 hours before slot 1"
                )
        return self


# ---------------------------------------------------------------------------
# Sentinel-run evidence (classification input; dispatch item 4/8)
# ---------------------------------------------------------------------------


class SentinelRunEvidence(BaseModel):
    """Not a hashed/chained bundle artifact — classification input built
    by a later caller from trusted runtime context."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: str
    github_run_id: str
    status: Literal["RUNNING", "COMPLETED", "FAILED"]
    source: Literal["fixtures", "live"]
    judgment_mode: Literal["stub", "agent"]

    @model_validator(mode="after")
    def _validate(self) -> "SentinelRunEvidence":
        _require_identifier(self.run_id)
        _require_identifier(self.github_run_id)
        return self


# ---------------------------------------------------------------------------
# Outcome typing (dispatch item 9 / state-f item 4)
# ---------------------------------------------------------------------------

ExecutionTimeQualificationOutcome = Literal[
    "QUALIFYING",
    "LATE_NONQUALIFYING",
    "WRONG_PROVENANCE_NONQUALIFYING",
    "DUPLICATE_NONQUALIFYING",
    "FAILED_NONTERMINAL",
    "COSTROW_INVALID",
    "STATE_CHAIN_FAILURE",
    "CADENCE_SKIP",
    "COST_CADENCE_REFUSAL",
]

IndependentReviewOutcome = Literal["MISSING_LOST", "DUPLICATE_NONQUALIFYING"]

QualificationOutcome = Literal[
    "QUALIFYING",
    "LATE_NONQUALIFYING",
    "WRONG_PROVENANCE_NONQUALIFYING",
    "DUPLICATE_NONQUALIFYING",
    "FAILED_NONTERMINAL",
    "COSTROW_INVALID",
    "STATE_CHAIN_FAILURE",
    "CADENCE_SKIP",
    "COST_CADENCE_REFUSAL",
    "MISSING_LOST",
]

WindowConsumeReason = Literal[
    "LATE_NONQUALIFYING",
    "WRONG_PROVENANCE_NONQUALIFYING",
    "DUPLICATE_NONQUALIFYING",
    "FAILED_NONTERMINAL",
    "COSTROW_INVALID",
    "STATE_CHAIN_FAILURE",
    "MISSING_LOST",
    "COST_CADENCE_REFUSAL",
    "POST_RUN_COST_TRIGGER",
]

_EXECUTION_TIME_VALUES = frozenset(
    (
        "QUALIFYING",
        "LATE_NONQUALIFYING",
        "WRONG_PROVENANCE_NONQUALIFYING",
        "DUPLICATE_NONQUALIFYING",
        "FAILED_NONTERMINAL",
        "COSTROW_INVALID",
        "STATE_CHAIN_FAILURE",
        "CADENCE_SKIP",
        "COST_CADENCE_REFUSAL",
    )
)
_INDEPENDENT_REVIEW_VALUES = frozenset(("MISSING_LOST", "DUPLICATE_NONQUALIFYING"))
_SLOT_SUCCESSOR_OUTCOMES = frozenset(
    (
        "QUALIFYING",
        "LATE_NONQUALIFYING",
        "WRONG_PROVENANCE_NONQUALIFYING",
        "DUPLICATE_NONQUALIFYING",
        "FAILED_NONTERMINAL",
        "COSTROW_INVALID",
        "STATE_CHAIN_FAILURE",
    )
)
_REFUSAL_OUTCOMES = frozenset(("CADENCE_SKIP", "COST_CADENCE_REFUSAL"))


class QualificationSlotOutcome(BaseModel):
    """Persisted per-slot classification record. Origin-tied: an
    ``outcome`` of ``MISSING_LOST`` may only ever come from independent
    review, never from single-run classification — enforced here, not
    merely by convention."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    window_id: str
    slot_index: int = Field(ge=1, le=5)
    outcome: QualificationOutcome
    delay_minutes: int | None = None
    classified_at_utc: datetime
    classification_reason: str
    determined_by: Literal["single_run_classification", "independent_review"]

    @model_validator(mode="after")
    def _validate(self) -> "QualificationSlotOutcome":
        _require_identifier(self.window_id)
        _require_utc(self.classified_at_utc)
        _require_identifier(self.classification_reason)
        if self.determined_by == "single_run_classification":
            if self.outcome not in _EXECUTION_TIME_VALUES:
                raise ValueError(
                    "single-run classification may only produce an "
                    "execution-time outcome"
                )
        else:
            if self.outcome not in _INDEPENDENT_REVIEW_VALUES:
                raise ValueError(
                    "independent review may only produce MISSING_LOST or "
                    "DUPLICATE_NONQUALIFYING"
                )
        if self.outcome == "QUALIFYING":
            if self.delay_minutes is None or not (0 <= self.delay_minutes <= 120):
                raise ValueError("QUALIFYING requires delay_minutes in [0, 120]")
        if self.outcome == "LATE_NONQUALIFYING":
            if self.delay_minutes is None or self.delay_minutes <= 120:
                raise ValueError("LATE_NONQUALIFYING requires delay_minutes > 120")
        if self.outcome == "MISSING_LOST" and self.delay_minutes is not None:
            raise ValueError("MISSING_LOST never carries a delay")
        return self


# ---------------------------------------------------------------------------
# Carried files + bundle manifests (dispatch P6/P7; state-c/d/e/f corrections)
# ---------------------------------------------------------------------------


class CarriedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str
    sha256: str

    @model_validator(mode="after")
    def _validate(self) -> "CarriedFile":
        _require_relative_posix_path(self.relative_path)
        _require_hex64(self.sha256)
        return self


_AUTHORITATIVE_CARRIED_PATHS = frozenset(
    (
        "state/ledger.sqlite3",
        "state/FINDINGS.md",
        "state/cost_ledger.jsonl",
        "state/phase5_state.json",
    )
)


def _validate_carried_files(carried_files: tuple[CarriedFile, ...]) -> None:
    paths = [carried.relative_path for carried in carried_files]
    if len(set(paths)) != len(paths):
        raise ValueError("carried_files must not contain duplicate normalized paths")
    if set(paths) != _AUTHORITATIVE_CARRIED_PATHS:
        raise ValueError(
            "carried_files must be exactly the four authoritative state paths"
        )


class GenesisManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    bundle_kind: Literal["GENESIS"]
    workflow_identity: str
    github_run_id: str
    run_attempt: int = Field(ge=1)
    event: str
    ref: str
    source_sha: str
    window_id: str
    window_record_sha256: str
    slot_index: Literal[0]
    expected_slot_utc: None = None
    github_run_created_at_utc: datetime | None = None
    github_run_started_at_utc: datetime | None = None
    sentinel_run_id: None = None
    no_run_outcome: Literal["WINDOW_GENESIS"]
    qualification_outcome: None = None
    window_consumed: bool
    predecessor_artifact_id_or_name: None = None
    predecessor_manifest_sha256: None = None
    carried_files: tuple[CarriedFile, ...]

    @model_validator(mode="after")
    def _validate(self) -> "GenesisManifest":
        _require_identifier(self.workflow_identity)
        _require_identifier(self.github_run_id)
        _require_identifier(self.event)
        _require_identifier(self.ref)
        _require_hex40(self.source_sha)
        _require_identifier(self.window_id)
        _require_hex64(self.window_record_sha256)
        _require_utc_optional(self.github_run_created_at_utc)
        _require_utc_optional(self.github_run_started_at_utc)
        _validate_carried_files(self.carried_files)
        return self


class SlotSuccessorManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    bundle_kind: Literal["SLOT_SUCCESSOR"]
    workflow_identity: str
    github_run_id: str
    run_attempt: int = Field(ge=1)
    event: str
    ref: str
    source_sha: str
    window_id: str
    window_record_sha256: str
    slot_index: int = Field(ge=1, le=5)
    expected_slot_utc: datetime
    github_run_created_at_utc: datetime | None = None
    github_run_started_at_utc: datetime | None = None
    sentinel_run_id: str
    no_run_outcome: None = None
    qualification_outcome: ExecutionTimeQualificationOutcome
    window_consumed: bool
    predecessor_artifact_id_or_name: str
    predecessor_manifest_sha256: str
    carried_files: tuple[CarriedFile, ...]

    @model_validator(mode="after")
    def _validate(self) -> "SlotSuccessorManifest":
        _require_identifier(self.workflow_identity)
        _require_identifier(self.github_run_id)
        _require_identifier(self.event)
        _require_identifier(self.ref)
        _require_hex40(self.source_sha)
        _require_identifier(self.window_id)
        _require_hex64(self.window_record_sha256)
        _require_daily_slot_instant(self.expected_slot_utc)
        _require_utc_optional(self.github_run_created_at_utc)
        _require_utc_optional(self.github_run_started_at_utc)
        _require_identifier(self.sentinel_run_id)
        if self.qualification_outcome not in _SLOT_SUCCESSOR_OUTCOMES:
            raise ValueError(
                "a slot successor's qualification_outcome must be a "
                "non-refusal execution-time outcome"
            )
        _require_identifier(self.predecessor_artifact_id_or_name)
        _require_hex64(self.predecessor_manifest_sha256)
        _validate_carried_files(self.carried_files)
        return self


class ControlRefusalManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    bundle_kind: Literal["CONTROL_REFUSAL"]
    workflow_identity: str
    github_run_id: str
    run_attempt: Literal[1]
    event: Literal["schedule"]
    ref: str
    source_sha: str
    window_id: str
    window_record_sha256: str
    slot_index: None = None
    expected_slot_utc: datetime
    github_run_created_at_utc: datetime | None = None
    github_run_started_at_utc: datetime | None = None
    sentinel_run_id: None = None
    no_run_outcome: Literal["CADENCE_SKIP", "COST_CADENCE_REFUSAL"]
    qualification_outcome: Literal["CADENCE_SKIP", "COST_CADENCE_REFUSAL"]
    window_consumed: bool
    predecessor_artifact_id_or_name: str
    predecessor_manifest_sha256: str
    carried_files: tuple[CarriedFile, ...]

    @model_validator(mode="after")
    def _validate(self) -> "ControlRefusalManifest":
        _require_identifier(self.workflow_identity)
        _require_identifier(self.github_run_id)
        _require_identifier(self.ref)
        _require_hex40(self.source_sha)
        _require_identifier(self.window_id)
        _require_hex64(self.window_record_sha256)
        _require_daily_slot_instant(self.expected_slot_utc)
        _require_utc_optional(self.github_run_created_at_utc)
        _require_utc_optional(self.github_run_started_at_utc)
        if self.no_run_outcome != self.qualification_outcome:
            raise ValueError(
                "a control refusal's no_run_outcome and qualification_outcome "
                "must be identical"
            )
        _require_identifier(self.predecessor_artifact_id_or_name)
        _require_hex64(self.predecessor_manifest_sha256)
        _validate_carried_files(self.carried_files)
        return self


StateBundleManifest = Annotated[
    Union[GenesisManifest, SlotSuccessorManifest, ControlRefusalManifest],
    Field(discriminator="bundle_kind"),
]


# ---------------------------------------------------------------------------
# Durable Phase-5 control state (phase5_state.json; dispatch P9)
# ---------------------------------------------------------------------------


class Phase5ControlState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    window_id: str | None
    window_record_sha256: str | None
    latest_authoritative_slot_index: int = Field(ge=0, le=5)
    window_consumed: bool
    window_consume_reason: WindowConsumeReason | None
    cadence_level: Literal["DAILY", "EVERY_2_DAYS", "WEEKLY"]
    cadence_anchor_slot_utc: datetime | None
    last_accounted_spend_eur_micros: int = Field(ge=0)
    last_evaluated_at_utc: datetime

    @model_validator(mode="after")
    def _validate(self) -> "Phase5ControlState":
        if (self.window_id is None) != (self.window_record_sha256 is None):
            raise ValueError(
                "window_id and window_record_sha256 must both be null or both "
                "be populated"
            )
        if self.window_id is not None:
            _require_identifier(self.window_id)
            _require_hex64(self.window_record_sha256)

        if not self.window_consumed and self.window_consume_reason is not None:
            raise ValueError(
                "window_consume_reason must be null when window_consumed is False"
            )
        if (
            self.window_consumed
            and self.window_consume_reason is None
            and self.latest_authoritative_slot_index != 5
        ):
            raise ValueError(
                "window_consumed with no reason is permitted only at "
                "latest_authoritative_slot_index == 5 (clean five-slot "
                "completion) — an incomplete window can never masquerade "
                "as cleanly consumed"
            )

        if self.cadence_level == "DAILY" and self.cadence_anchor_slot_utc is not None:
            raise ValueError("DAILY cadence must not carry an anchor")
        if self.cadence_level != "DAILY" and self.cadence_anchor_slot_utc is None:
            raise ValueError("non-DAILY cadence requires a cadence anchor")
        if self.cadence_anchor_slot_utc is not None:
            _require_daily_slot_instant(self.cadence_anchor_slot_utc)

        _require_utc(self.last_evaluated_at_utc)
        return self


# ---------------------------------------------------------------------------
# One-shot attempt markers (dispatch P16)
# ---------------------------------------------------------------------------


class OneShotMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    purpose: Literal["P5C_WIF_PROBE", "P5D_OFFICIAL_SONNET_GATE"]
    created_at_utc: datetime
    workflow_identity: str
    github_run_id: str
    run_attempt: int = Field(ge=1)
    event: str
    source_sha: str

    @model_validator(mode="after")
    def _validate(self) -> "OneShotMarker":
        _require_utc(self.created_at_utc)
        _require_identifier(self.workflow_identity)
        _require_identifier(self.github_run_id)
        _require_identifier(self.event)
        _require_hex40(self.source_sha)
        return self

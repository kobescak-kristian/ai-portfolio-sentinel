"""Typed handoff contracts (BLUEPRINT §3, §4).

Phase 0 landed CostRow only — the cost-telemetry harness exists before
the thing it measures (BLUEPRINT §6 P0); CostRow is frozen. Phase 1
lands CheckTask, Finding and RunRecord with the eval-gate freeze
(ADR 0004: six check classes). Schema now, logic later: task
transitions, per-class content normalization and dedup execution are
Phase 2 control-plane work.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# Free-text path guard: ledger rows must never carry machine-local
# absolute paths (public repo; telemetry is committed evidence).
# Delimiter-aware: a separator character in front of "/" or "\\" marks
# the start of an embedded path; a slash inside a plain identifier
# such as "provider/model-name" does not.
_DELIM = r"[\s=:\"',;(\[{]"
_POSIX_ABSOLUTE = re.compile(rf"(?:^|(?<={_DELIM}))/")
_UNC = re.compile(rf"(?:^|(?<={_DELIM}))\\\\")
_DRIVE_ROOT = re.compile(r"[A-Za-z]:[\\/]")


def _contains_machine_local_path(value: str) -> bool:
    return bool(
        _DRIVE_ROOT.search(value)
        or _POSIX_ABSOLUTE.search(value)
        or _UNC.search(value)
    )


class CostRow(BaseModel):
    """One telemetry row per run — every run, including dev, writes one.

    Frozen contract: exactly these eight fields, no additions, no
    renames. Currency is integer micro-euros; floating-point currency
    never appears in the ledger.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: str
    recorded_at_utc: datetime
    run_kind: Literal["dev", "eval", "live"]
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_eur_micros: int = Field(ge=0)

    @field_validator("run_id", "model")
    @classmethod
    def _free_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty after stripping")
        if _contains_machine_local_path(value):
            raise ValueError("machine-local absolute paths are not permitted")
        return value

    @field_validator("recorded_at_utc")
    @classmethod
    def _utc_only(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if value.tzinfo is None or offset is None:
            raise ValueError("naive datetimes are not permitted")
        if offset != timedelta(0):
            raise ValueError("UTC offset must be exactly zero")
        return value


# ---------------------------------------------------------------------------
# Phase 1 contracts (BLUEPRINT §6 P1, ADR 0004). Everything below freezes
# shape and invariants only — the state machine, per-class normalization
# and dedup execution are Phase 2 control-plane work.
# ---------------------------------------------------------------------------

CheckClass = Literal[
    "broken-link",
    "number-mismatch",
    "stale-STATE-marker",
    "missing-required-file",
    "missing-synthetic-label",
    "readme-structure",
]

# Exact class tokens, exported for the parity test that compares this
# contract, the SPEC §2 machine block and (from the Phase 1 freeze) the
# eval_config classes list as exact sets.
CHECK_CLASSES: tuple[str, ...] = get_args(CheckClass)

_HEX64 = re.compile(r"[0-9a-f]{64}")
_LINE_SUFFIX = re.compile(r"[1-9][0-9]*")

# Detail free text permits ordinary URLs: this delimiter set drops ":"
# so "https://host/path" does not read as an embedded absolute path,
# while "/home/x" at a word boundary still does. The drive-root pattern
# likewise needs a lookbehind — the shared _DRIVE_ROOT would match the
# "s://" inside every https URL as a drive letter.
_DETAIL_DELIM = r"[\s=\"',;(\[{]"
_DETAIL_POSIX_ABSOLUTE = re.compile(rf"(?:^|(?<={_DETAIL_DELIM}))/")
_DETAIL_DRIVE_ROOT = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
_UNC_ANYWHERE = re.compile(r"\\\\")


def _require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError("naive datetimes are not permitted")
    if offset != timedelta(0):
        raise ValueError("UTC offset must be exactly zero")
    return value


def _validate_identifier(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be empty after stripping")
    if _contains_machine_local_path(value):
        raise ValueError("machine-local absolute paths are not permitted")
    return value


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _validate_surface(value: str) -> str:
    """Frozen surface grammar — fingerprints depend on it.

    Repo surfaces are <repo-name>/<repo-relative-path>, fixture surfaces
    <snapshot>/<repo-relative-path>, site surfaces site/<normalized-path>
    with root site/index. No scheme, host, colon, leading slash,
    backslash, ".." segment or control character; the two-segment
    minimum plus non-empty segments make duplicate and trailing slashes
    impossible.
    """
    value = value.strip()
    if not value:
        raise ValueError("must not be empty after stripping")
    if ":" in value:
        raise ValueError("surface must not contain a colon")
    if "\\" in value:
        raise ValueError("surface must not contain backslashes")
    if _has_control_chars(value):
        raise ValueError("surface must not contain control characters")
    segments = value.split("/")
    if len(segments) < 2:
        raise ValueError("surface must be <name>/<repo-relative-path>")
    if any(not segment for segment in segments):
        raise ValueError("surface must not contain empty path segments")
    if ".." in segments:
        raise ValueError("surface must not contain '..' segments")
    return value


def _validate_location(value: str) -> str:
    """Location is 'path' or 'path:line' with line a positive integer."""
    value = value.strip()
    if not value:
        raise ValueError("must not be empty after stripping")
    path, sep, tail = value.rpartition(":")
    if sep:
        if not _LINE_SUFFIX.fullmatch(tail):
            raise ValueError("location line suffix must be a positive integer")
    else:
        path = tail
    if not path:
        raise ValueError("location path must be non-empty")
    if ":" in path:
        raise ValueError("location path must not contain a colon")
    if "\\" in path:
        raise ValueError("location must use forward slashes only")
    if _has_control_chars(path):
        raise ValueError("location must not contain control characters")
    segments = path.split("/")
    if any(not segment for segment in segments):
        raise ValueError("location must not have leading or empty path segments")
    if ".." in segments:
        raise ValueError("location must not contain '..' segments")
    return value


def _validate_detail(value: str) -> str:
    """Free text that may carry ordinary URLs but never machine-local
    paths. The shared _contains_machine_local_path guard is deliberately
    NOT reused here — its delimiter set treats ":" as a boundary, so it
    would reject every "scheme://host/path" URL."""
    value = value.strip()
    if not value:
        raise ValueError("must not be empty after stripping")
    if (
        _DETAIL_DRIVE_ROOT.search(value)
        or _UNC_ANYWHERE.search(value)
        or _DETAIL_POSIX_ABSOLUTE.search(value)
    ):
        raise ValueError("machine-local absolute paths are not permitted")
    return value


def serialize_db_datetime(value: datetime) -> str:
    """Serialize a timezone-aware datetime to the frozen ledger TEXT shape.

    Output is exactly ``YYYY-MM-DDTHH:MM:SS+00:00`` (length 25): input
    must be timezone-aware, is normalized to UTC and truncated to whole
    seconds. Database rows always store this serializer's output —
    SQLite's default datetime adapters are never used.
    """
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError("naive datetimes are not permitted")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def compute_content_hash(location: str, normalized_finding_content: str) -> str:
    """Finding-local content hash: SHA-256 over location + "\\n" + content.

    The payload is the canonical finding location plus defect-specific
    normalized content — whole-file, whole-page and whole-surface hashes
    are prohibited, so two defects of the same class on the same surface
    hash differently and unrelated edits elsewhere in a file leave the
    hash unchanged. The per-class algorithm that produces
    normalized_finding_content is Phase 2 check-implementation work;
    this function freezes the payload shape only.
    """
    payload = f"{location}\n{normalized_finding_content}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_fingerprint(
    surface: str, check_class: CheckClass, content_hash: str
) -> str:
    """Dedup fingerprint (SPEC §1 step 4): SHA-256 over surface, check
    class and content hash joined by newlines, in that order."""
    payload = f"{surface}\n{check_class}\n{content_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CheckTask(BaseModel):
    """One check task per (surface × check class) per run (SPEC §1 step 1).

    Terminal statuses are DONE, FAILED and DEAD_LETTER. Transition
    enforcement (the state machine) is Phase 2 control-plane work — this
    model freezes the value set only.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    task_id: str
    run_id: str
    surface: str
    check_class: CheckClass
    created_at_utc: datetime
    status: Literal["PENDING", "IN_PROGRESS", "DONE", "FAILED", "DEAD_LETTER"]

    @field_validator("task_id", "run_id")
    @classmethod
    def _identifiers(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("surface")
    @classmethod
    def _surface(cls, value: str) -> str:
        return _validate_surface(value)

    @field_validator("created_at_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class Finding(BaseModel):
    """One deduplicated finding row (SPEC §1 step 4).

    Lifecycle is update-in-place, never delete: an OPEN row either
    advances last_seen or resolves OPEN→RESOLVED with resolved_at_utc
    and resolved_run_id stamped; recurrence after resolution inserts a
    new row with the same fingerprint. The fingerprint is recomputed
    and enforced on validation.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    fingerprint: str
    surface: str
    check_class: CheckClass
    content_hash: str
    location: str
    detail: str
    status: Literal["OPEN", "RESOLVED"]
    first_seen_utc: datetime
    last_seen_utc: datetime
    resolved_at_utc: datetime | None = None
    first_seen_run_id: str
    last_seen_run_id: str
    resolved_run_id: str | None = None

    @field_validator("fingerprint", "content_hash")
    @classmethod
    def _hex64(cls, value: str) -> str:
        if not _HEX64.fullmatch(value):
            raise ValueError("must be exactly 64 lowercase hexadecimal characters")
        return value

    @field_validator("surface")
    @classmethod
    def _surface(cls, value: str) -> str:
        return _validate_surface(value)

    @field_validator("location")
    @classmethod
    def _location(cls, value: str) -> str:
        return _validate_location(value)

    @field_validator("detail")
    @classmethod
    def _detail(cls, value: str) -> str:
        return _validate_detail(value)

    @field_validator("first_seen_run_id", "last_seen_run_id")
    @classmethod
    def _identifiers(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("resolved_run_id")
    @classmethod
    def _optional_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_identifier(value)

    @field_validator("first_seen_utc", "last_seen_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("resolved_at_utc")
    @classmethod
    def _optional_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_utc(value)

    @model_validator(mode="after")
    def _lifecycle(self) -> Finding:
        if self.last_seen_utc < self.first_seen_utc:
            raise ValueError("last_seen_utc must not precede first_seen_utc")
        has_resolved_at = self.resolved_at_utc is not None
        has_resolved_run = self.resolved_run_id is not None
        if self.status == "OPEN" and (has_resolved_at or has_resolved_run):
            raise ValueError(
                "OPEN findings must not carry resolved_at_utc or resolved_run_id"
            )
        if self.status == "RESOLVED" and not (has_resolved_at and has_resolved_run):
            raise ValueError(
                "RESOLVED findings require resolved_at_utc and resolved_run_id"
            )
        if has_resolved_at and self.resolved_at_utc < self.last_seen_utc:
            raise ValueError("resolved_at_utc must not precede last_seen_utc")
        expected = compute_fingerprint(
            self.surface, self.check_class, self.content_hash
        )
        if self.fingerprint != expected:
            raise ValueError(
                "fingerprint does not match surface + check_class + content_hash"
            )
        return self


class RunRecord(BaseModel):
    """One row per scheduled run (SPEC §1 step 5 counts).

    RUNNING rows carry no finished_at_utc; terminal rows (COMPLETED or
    FAILED) always do. COMPLETED requires every task terminal. The FI
    suite proves the invariants end-to-end at Phase 2 — this model
    enforces row-level coherence only.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: str
    run_kind: Literal["dev", "eval", "live"]
    status: Literal["RUNNING", "COMPLETED", "FAILED"]
    started_at_utc: datetime
    finished_at_utc: datetime | None = None
    tasks_created: int = Field(ge=0)
    tasks_terminal: int = Field(ge=0)
    findings_new: int = Field(ge=0)
    findings_still_open: int = Field(ge=0)
    findings_resolved: int = Field(ge=0)

    @field_validator("run_id")
    @classmethod
    def _identifier(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("started_at_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("finished_at_utc")
    @classmethod
    def _optional_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_utc(value)

    @model_validator(mode="after")
    def _coherence(self) -> RunRecord:
        if (self.status == "RUNNING") != (self.finished_at_utc is None):
            raise ValueError(
                "RUNNING iff finished_at_utc is absent; terminal iff it is set"
            )
        if (
            self.finished_at_utc is not None
            and self.finished_at_utc < self.started_at_utc
        ):
            raise ValueError("finished_at_utc must not precede started_at_utc")
        if self.tasks_terminal > self.tasks_created:
            raise ValueError("tasks_terminal must not exceed tasks_created")
        if self.status == "COMPLETED" and self.tasks_terminal != self.tasks_created:
            raise ValueError("COMPLETED requires tasks_terminal == tasks_created")
        return self

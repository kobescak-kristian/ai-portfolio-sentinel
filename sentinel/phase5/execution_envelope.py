"""P5-D execution envelope, job-start anchor and session clock (ADR-0012
sections 7 and 8, Amendment A4; dispatch
q77-p5d-repair-stage2c1-implement-a, Stage 2C-1).

Pure and parameterized only. This module computes the frozen envelope
formulas with exact integer arithmetic, gives an envelope a
deterministic identity over its canonical bytes, resolves the platform
job-start anchor from an already-fetched attempt-scoped jobs listing,
and derives a monotonic session clock from that anchor. It performs no
I/O except the strict ``load_committed_envelope`` reader, starts no
thread, makes no network request and touches no provider.

Frozen numerical contract (ADR-0012 section 7, Amendment A4):

    outer_seconds            = ceil(138 * max_observed_ms / 1000) + 1080
    workflow_timeout_minutes = ceil(outer_seconds / 60)
    session_duration_s       = outer_seconds - 480
    stall_budget_ms          = max(600000, 10 * max_observed_ms)
    invocation_budget_ms     = min(remaining_session_ms, stall_budget_ms)

where 138 is exactly 92 x 1.5 and 1080 s is the 600 s fixed overhead
plus the 480 s finalization reserve. An envelope is infeasible, and
unconstructible, when ``max_observed_ms > 148000`` or when the workflow
timeout exceeds the 360-minute platform job ceiling. The superseded
per-invocation formula ``max(180 s, 3 x max_observed)`` is not
implemented.

Session deadline (ADR-0012 section 7): an absolute instant anchored to
the platform job start, never to preflight start, execute start or the
current time. Checkout, setup and preflight time already consumed is
therefore subtracted by construction.

No envelope artifact is created here, no production envelope instance
is committed, and no runner constant changes: the official gate's
``ENVELOPE`` stays ``None`` through all of Stage 2C.

stdlib + pydantic only, same discipline as the rest of
``sentinel/phase5/``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .github_evidence import JobDetail
from .models import canonical_json_bytes
from .terminal import EnvelopeIdentity

# ---------------------------------------------------------------------------
# Frozen numerical contract
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
ENVELOPE_VERSION = "1"
INVOCATIONS_SIZED = 92
MARGIN_MULTIPLIER = "1.5"
FIXED_OVERHEAD_S = 600
FINALIZATION_RESERVE_S = 480
STALL_FLOOR_S = 600
STALL_MULTIPLIER = 10
PLATFORM_JOB_CEILING_MIN = 360
MAX_OBSERVED_CEILING_MS = 148_000

# 92 x 1.5 is exactly 138: the margin is applied as an integer numerator
# over a 1000 ms denominator, never as a float.
_SIZED_MARGIN_NUMERATOR = 138
_FIXED_PLUS_RESERVE_S = FIXED_OVERHEAD_S + FINALIZATION_RESERVE_S  # 1080

REHEARSAL_OBSERVATION_COUNT = 24
REHEARSAL_MODEL = "claude-sonnet-5"
REHEARSAL_SDK_PIN = "claude-agent-sdk==0.2.110"

MAX_ENVELOPE_BYTES = 64 * 1024

_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class EnvelopeError(ValueError):
    """An envelope formula input or contract value is invalid."""


class CommittedEnvelopeError(RuntimeError):
    """A committed envelope file is absent, malformed, non-canonical or
    infeasible. Never carries a local path."""


class JobStartAnchorError(RuntimeError):
    """The job-start anchor could not be resolved fail-closed from the
    attempt-scoped jobs listing."""


def _require_positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EnvelopeError(f"{label} must be a positive integer")
    return value


def _require_utc(value: datetime, label: str) -> datetime:
    offset = value.utcoffset() if isinstance(value, datetime) else None
    if not isinstance(value, datetime) or value.tzinfo is None or offset is None:
        raise ValueError(f"{label} must be an aware UTC datetime")
    if offset != timedelta(0):
        raise ValueError(f"{label} must be an aware UTC datetime")
    return value


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


# -- formula helpers (exact integer arithmetic, ceiling never floor) ---------


def outer_seconds(max_observed_ms: int) -> int:
    """``ceil(138 * max_observed_ms / 1000) + 1080``."""
    _require_positive_int(max_observed_ms, "max_observed_ms")
    return _ceil_div(_SIZED_MARGIN_NUMERATOR * max_observed_ms, 1000) + _FIXED_PLUS_RESERVE_S


def workflow_timeout_minutes(outer_s: int) -> int:
    """``ceil(outer_seconds / 60)`` -- the workflow value is the ceiling
    in whole minutes, never the floor."""
    _require_positive_int(outer_s, "outer_seconds")
    return _ceil_div(outer_s, 60)


def session_duration_s(outer_s: int) -> int:
    """``outer_seconds - 480``."""
    _require_positive_int(outer_s, "outer_seconds")
    return outer_s - FINALIZATION_RESERVE_S


def stall_budget_ms(max_observed_ms: int) -> int:
    """``max(600000, 10 * max_observed_ms)``."""
    _require_positive_int(max_observed_ms, "max_observed_ms")
    return max(STALL_FLOOR_S * 1000, STALL_MULTIPLIER * max_observed_ms)


def invocation_budget_ms(remaining_session_ms: int, stall_ms: int) -> int:
    """``min(remaining_session_ms, stall_budget_ms)``. The remaining
    session budget may already be zero or negative (an expired clock);
    the result is then never positive, so a caller can never start
    provider work against an expired session."""
    if isinstance(remaining_session_ms, bool) or not isinstance(remaining_session_ms, int):
        raise EnvelopeError("remaining_session_ms must be an integer")
    _require_positive_int(stall_ms, "stall_budget_ms")
    return min(remaining_session_ms, stall_ms)


def is_feasible(max_observed_ms: int) -> bool:
    """Feasible only when ``max_observed_ms <= 148000`` AND the derived
    workflow timeout is within the 360-minute platform job ceiling."""
    _require_positive_int(max_observed_ms, "max_observed_ms")
    if max_observed_ms > MAX_OBSERVED_CEILING_MS:
        return False
    return workflow_timeout_minutes(outer_seconds(max_observed_ms)) <= PLATFORM_JOB_CEILING_MIN


# ---------------------------------------------------------------------------
# Timing-rehearsal provenance
# ---------------------------------------------------------------------------


class TimingRehearsalProvenance(BaseModel):
    """Where ``max_observed_ms`` came from: the N=24 Sonnet timing
    rehearsal (ADR-0012 section 6). The rehearsal's own source SHA is
    named ``rehearsal_source_sha`` deliberately -- the execution source
    SHA is a later readiness binding and never lives here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rehearsal_workflow_identity: str
    rehearsal_run_id: str
    rehearsal_run_attempt: StrictInt = Field(ge=1)
    rehearsal_source_sha: str
    preregistration_sha256: str
    corpus_sha256: str
    observation_count: Literal[24]
    observations_ms: tuple[StrictInt, ...]
    model: Literal["claude-sonnet-5"]
    sdk_pin: Literal["claude-agent-sdk==0.2.110"]

    @model_validator(mode="after")
    def _validate(self) -> "TimingRehearsalProvenance":
        for name in ("rehearsal_workflow_identity", "rehearsal_run_id"):
            value = getattr(self, name)
            if not value or value.strip() != value or not value.strip():
                raise ValueError(f"{name} must be a non-empty identifier without surrounding whitespace")
        if not _HEX40.fullmatch(self.rehearsal_source_sha):
            raise ValueError("rehearsal_source_sha must be exactly 40 lowercase hexadecimal characters")
        for name in ("preregistration_sha256", "corpus_sha256"):
            if not _HEX64.fullmatch(getattr(self, name)):
                raise ValueError(f"{name} must be exactly 64 lowercase hexadecimal characters")
        if len(self.observations_ms) != self.observation_count:
            raise ValueError(f"exactly {self.observation_count} observations are required")
        for value in self.observations_ms:
            if value < 1:
                raise ValueError("every observation must be a positive integer millisecond duration")
        return self

    @property
    def max_observed_ms(self) -> int:
        return max(self.observations_ms)


# ---------------------------------------------------------------------------
# Execution envelope
# ---------------------------------------------------------------------------


class ExecutionEnvelope(BaseModel):
    """The frozen numerical envelope. Every constant is a ``Literal`` so
    a different value is unconstructible; every derived field must equal
    the formula output exactly; an infeasible ``max_observed_ms`` is
    refused at construction. The anchor, the session clock and the
    execution-control configuration are not fields, so they can never
    enter ``envelope_id``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    envelope_version: Literal["1"]
    invocations_sized: Literal[92]
    margin_multiplier: Literal["1.5"]
    fixed_overhead_s: Literal[600]
    finalization_reserve_s: Literal[480]
    stall_floor_s: Literal[600]
    stall_multiplier: Literal[10]
    platform_job_ceiling_min: Literal[360]
    max_observed_ceiling_ms: Literal[148000]
    max_observed_ms: StrictInt = Field(ge=1)
    outer_seconds: StrictInt = Field(ge=1)
    workflow_timeout_minutes: StrictInt = Field(ge=1)
    session_duration_s: StrictInt = Field(ge=1)
    stall_budget_ms: StrictInt = Field(ge=1)
    rehearsal: TimingRehearsalProvenance

    @model_validator(mode="after")
    def _validate(self) -> "ExecutionEnvelope":
        if self.max_observed_ms != self.rehearsal.max_observed_ms:
            raise ValueError("max_observed_ms must equal the maximum rehearsal observation")
        if self.max_observed_ms > MAX_OBSERVED_CEILING_MS:
            raise ValueError(
                f"infeasible envelope: max_observed_ms {self.max_observed_ms} exceeds the "
                f"frozen {MAX_OBSERVED_CEILING_MS} ms boundary"
            )
        expected_outer = outer_seconds(self.max_observed_ms)
        if self.outer_seconds != expected_outer:
            raise ValueError("outer_seconds does not equal ceil(138 * max_observed_ms / 1000) + 1080")
        expected_minutes = workflow_timeout_minutes(expected_outer)
        if self.workflow_timeout_minutes != expected_minutes:
            raise ValueError("workflow_timeout_minutes does not equal ceil(outer_seconds / 60)")
        if self.workflow_timeout_minutes > PLATFORM_JOB_CEILING_MIN:
            raise ValueError(
                f"infeasible envelope: workflow_timeout_minutes {self.workflow_timeout_minutes} "
                f"exceeds the {PLATFORM_JOB_CEILING_MIN}-minute platform job ceiling"
            )
        if self.session_duration_s != session_duration_s(expected_outer):
            raise ValueError("session_duration_s does not equal outer_seconds - 480")
        if self.stall_budget_ms != stall_budget_ms(self.max_observed_ms):
            raise ValueError("stall_budget_ms does not equal max(600000, 10 * max_observed_ms)")
        return self

    @property
    def envelope_id(self) -> str:
        """SHA-256 of the canonical envelope bytes (``models.canonical_json_bytes``)."""
        return hashlib.sha256(canonical_json_bytes(self)).hexdigest()

    def identity(self) -> EnvelopeIdentity:
        """Bridge to the unchanged Stage-2B ``EnvelopeIdentity``."""
        return EnvelopeIdentity(envelope_id=self.envelope_id, envelope_version=self.envelope_version)


def build_execution_envelope(rehearsal: TimingRehearsalProvenance) -> ExecutionEnvelope:
    """Derive every envelope value from the rehearsal provenance. Raises
    ``pydantic.ValidationError`` for an infeasible rehearsal maximum."""
    max_observed = rehearsal.max_observed_ms
    outer = outer_seconds(max_observed)
    return ExecutionEnvelope(
        schema_version=SCHEMA_VERSION,
        envelope_version=ENVELOPE_VERSION,
        invocations_sized=INVOCATIONS_SIZED,
        margin_multiplier=MARGIN_MULTIPLIER,
        fixed_overhead_s=FIXED_OVERHEAD_S,
        finalization_reserve_s=FINALIZATION_RESERVE_S,
        stall_floor_s=STALL_FLOOR_S,
        stall_multiplier=STALL_MULTIPLIER,
        platform_job_ceiling_min=PLATFORM_JOB_CEILING_MIN,
        max_observed_ceiling_ms=MAX_OBSERVED_CEILING_MS,
        max_observed_ms=max_observed,
        outer_seconds=outer,
        workflow_timeout_minutes=workflow_timeout_minutes(outer),
        session_duration_s=session_duration_s(outer),
        stall_budget_ms=stall_budget_ms(max_observed),
        rehearsal=rehearsal,
    )


def committed_envelope_bytes(envelope: ExecutionEnvelope) -> bytes:
    """Exactly the bytes a committed envelope file must contain."""
    return canonical_json_bytes(envelope)


def load_committed_envelope(path: Path) -> ExecutionEnvelope:
    """Strict reader for a committed envelope artifact: a regular,
    non-symlink, bounded file whose bytes are exactly the canonical JSON
    of a feasible ``ExecutionEnvelope``. Absent, malformed,
    non-canonical or infeasible content is refused. No such artifact
    exists in Stage 2C-1; the 2C-B binding creates it."""
    path = Path(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError as exc:
        raise CommittedEnvelopeError("committed envelope is absent") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise CommittedEnvelopeError("committed envelope is not a regular file")
    if st.st_size > MAX_ENVELOPE_BYTES:
        raise CommittedEnvelopeError("committed envelope exceeds the bounded size")
    data = path.read_bytes()
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CommittedEnvelopeError("committed envelope is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise CommittedEnvelopeError("committed envelope is not a JSON object")
    try:
        envelope = ExecutionEnvelope.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001 - pydantic ValidationError; identifier-only message
        raise CommittedEnvelopeError("committed envelope does not validate as a feasible envelope") from exc
    if canonical_json_bytes(envelope) != data:
        raise CommittedEnvelopeError("committed envelope bytes are not canonical")
    return envelope


# ---------------------------------------------------------------------------
# Job-start anchor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobStartAnchor:
    """The platform job start this session's deadline is anchored to.
    ``run_attempt`` comes from the REQUEST identity of the attempt-scoped
    jobs endpoint, never from a job body. ``workflow_job_id`` is the
    workflow-file job key (``GITHUB_JOB``), recorded for provenance
    only; matching used the explicit REST display name."""

    run_id: str
    run_attempt: int
    workflow_job_id: str
    api_job_id: int
    api_job_name: str
    runner_name: str
    job_started_at_utc: datetime
    resolved_at_utc: datetime
    monotonic_at_resolve: float


def resolve_job_start_anchor(
    jobs: Sequence[JobDetail],
    *,
    run_id: str,
    run_attempt: int,
    expected_workflow_job_id: str,
    expected_api_job_name: str,
    expected_runner_name: str,
    resolved_at_utc: datetime,
    monotonic_at_resolve: float,
) -> JobStartAnchor:
    """Pure, fail-closed resolver over an already-fetched attempt-scoped
    jobs listing. Refuses a listing containing any other run, zero or
    multiple jobs with the expected REST display name, a job that is
    not ``in_progress``, a runner-name mismatch, a missing or non-UTC
    ``started_at``, and a start later than the resolution instant."""
    for name, value in (
        ("run_id", run_id),
        ("expected_workflow_job_id", expected_workflow_job_id),
        ("expected_api_job_name", expected_api_job_name),
        ("expected_runner_name", expected_runner_name),
    ):
        if not isinstance(value, str) or not value.strip():
            raise JobStartAnchorError(f"{name} must be a non-empty string")
    if isinstance(run_attempt, bool) or not isinstance(run_attempt, int) or run_attempt < 1:
        raise JobStartAnchorError("run_attempt must be a positive integer")
    try:
        _require_utc(resolved_at_utc, "resolved_at_utc")
    except ValueError as exc:
        raise JobStartAnchorError(str(exc)) from exc
    if isinstance(monotonic_at_resolve, bool) or not isinstance(monotonic_at_resolve, (int, float)):
        raise JobStartAnchorError("monotonic_at_resolve must be a number")

    for job in jobs:
        if job.run_id != run_id:
            raise JobStartAnchorError("jobs listing contains a job from a different run")
    matches = [job for job in jobs if job.name == expected_api_job_name]
    if not matches:
        raise JobStartAnchorError("no job in the listing carries the expected API job name")
    if len(matches) > 1:
        raise JobStartAnchorError("more than one job in the listing carries the expected API job name")
    job = matches[0]
    if job.status != "in_progress":
        raise JobStartAnchorError("the anchoring job is not in_progress")
    if job.runner_name != expected_runner_name:
        raise JobStartAnchorError("the anchoring job's runner name does not match")
    if job.started_at is None:
        raise JobStartAnchorError("the anchoring job has no started_at")
    try:
        started = _require_utc(job.started_at, "started_at")
    except ValueError as exc:
        raise JobStartAnchorError(str(exc)) from exc
    if started > resolved_at_utc:
        raise JobStartAnchorError("the anchoring job's started_at is later than the resolution instant")
    return JobStartAnchor(
        run_id=run_id,
        run_attempt=run_attempt,
        workflow_job_id=expected_workflow_job_id,
        api_job_id=job.id,
        api_job_name=job.name,
        runner_name=job.runner_name,
        job_started_at_utc=started,
        resolved_at_utc=resolved_at_utc,
        monotonic_at_resolve=float(monotonic_at_resolve),
    )


# ---------------------------------------------------------------------------
# Session clock
# ---------------------------------------------------------------------------


class SessionClock:
    """The session deadline as a monotonic instant.

        session_deadline_utc       = job_started_at_utc + session_duration_s
        remaining_at_resolution    = session_deadline_utc - resolved_at_utc
        monotonic_session_deadline = monotonic_at_resolve
                                     + remaining_at_resolution.total_seconds()

    After construction only the monotonic clock is consulted: a later
    wall-clock change has no effect. The deadline is never derived from
    preflight start, execute start or the current time -- there is no
    such parameter."""

    def __init__(
        self,
        *,
        job_started_at_utc: datetime,
        resolved_at_utc: datetime,
        monotonic_at_resolve: float,
        session_duration_s: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        started = _require_utc(job_started_at_utc, "job_started_at_utc")
        resolved = _require_utc(resolved_at_utc, "resolved_at_utc")
        _require_positive_int(session_duration_s, "session_duration_s")
        if isinstance(monotonic_at_resolve, bool) or not isinstance(monotonic_at_resolve, (int, float)):
            raise EnvelopeError("monotonic_at_resolve must be a number")
        self._session_deadline_utc = started + timedelta(seconds=session_duration_s)
        self._remaining_at_resolution = self._session_deadline_utc - resolved
        self._monotonic_session_deadline = (
            float(monotonic_at_resolve) + self._remaining_at_resolution.total_seconds()
        )
        self._monotonic = monotonic

    @classmethod
    def from_anchor(
        cls,
        anchor: JobStartAnchor,
        envelope: ExecutionEnvelope,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> "SessionClock":
        return cls(
            job_started_at_utc=anchor.job_started_at_utc,
            resolved_at_utc=anchor.resolved_at_utc,
            monotonic_at_resolve=anchor.monotonic_at_resolve,
            session_duration_s=envelope.session_duration_s,
            monotonic=monotonic,
        )

    @property
    def session_deadline_utc(self) -> datetime:
        return self._session_deadline_utc

    @property
    def remaining_at_resolution(self) -> timedelta:
        return self._remaining_at_resolution

    @property
    def monotonic_session_deadline(self) -> float:
        return self._monotonic_session_deadline

    @property
    def already_expired_at_resolution(self) -> bool:
        return self._remaining_at_resolution <= timedelta(0)

    def monotonic_now(self) -> float:
        """The clock's own monotonic source (shared with the latch and
        arbiter so every timestamp in one session is comparable)."""
        return self._monotonic()

    def remaining_ms(self) -> int:
        """Whole milliseconds until the session deadline, floored, from
        the monotonic clock only. Negative once expired."""
        return math.floor((self._monotonic_session_deadline - self._monotonic()) * 1000)

    def expired(self) -> bool:
        return self.remaining_ms() <= 0

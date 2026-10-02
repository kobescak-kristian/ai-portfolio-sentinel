"""P5-D durable replacement latch (ADR-0012 sections 3, 20 and 21; the
STATE.md HARD PRE-ARMING GATE and ARMING CONTRACT; Stage 2C-B6-2 under the
owner-approved B6-2 plan and owner rulings of 2026-10-01).

Why it exists. A workflow can never write git, so a durable record of
replacement consumption written AFTER a run is a receipt commit, and
receipt recording may fail indefinitely while the marker artifact later
expires. This latch is therefore written BEFORE the irreversible point and
closes admission by itself, with no post-run write.

Store. ``artifacts/phase5_replacement_latch.jsonl``: committed, append-only,
hash-chained canonical JSONL in the ``receipts.py`` discipline. Exactly two
record kinds exist:

- ``GENESIS``: written once (Stage 2C-B6-2). The latch is UNARMED.
- ``ATTEMPT_AUTHORIZED``: written once, only at the final owner GO of a
  later stage, in an authorization commit A whose only change is that
  append. It opens one admission window of at most 24 hours.

No update, delete, truncate, extend, amend, consume, reset or reopen
record or API exists, and none may be added here.

Admission (owner rulings R1-R5, 2026-10-01). The window governs ADMISSION
of a replacement attempt, not marker-upload completion. A run is admitted
only when every check in ``latch_verdict`` passes, evaluated exactly once
at the preflight eligibility decision: commit A is identified and anchored
to GitHub's server-side push record of A (never to a client-chosen git
date); the current job started, and GitHub server time at evaluation is,
before the close; every earlier official-gate run in the run-number
interval ``F+1 .. N-1`` is present exactly once and proven to have stopped
before the marker step. The replacement marker upload stays the
irreversible consumption point (ADR-0012 sections 3 and 21).

Every timestamp written here is GitHub server time supplied by the caller.
This module never reads a local clock. Pure except ``load_latch`` and the
two append functions; stdlib + pydantic only.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .github_evidence import CommitDetail, JobEvidence, PushActivity, RunRef
from .models import canonical_json_bytes, sha256_hex_of_model
from .replacement import OWNER_RULING_ID, REPLACEMENT_OF_RUN_ID, REPLACEMENT_PURPOSE

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

LATCH_PATH = Path("artifacts/phase5_replacement_latch.jsonl")
SCHEMA_VERSION = 1
ZERO_SHA256 = "0" * 64
OFFICIAL_GATE_WORKFLOW = ".github/workflows/sentinel-official-gate.yml"
GENESIS_REF_ID = "q77-p5d-repair-stage2cb6-2-latch-genesis"
MAX_AUTHORIZATION_WINDOW = timedelta(hours=24)
MAX_RECORDS = 2

# Step and job identity of the official-gate workflow, pinned against the
# workflow YAML by tests/test_phase5_latch.py.
GATE_JOB_NAME = "gate"
MARKER_STEP_NAME = "upload one-shot marker"
EXECUTE_STEP_NAME = "execute"
MAIN_REF = "refs/heads/main"
MAIN_BRANCH = "main"
DISPATCH_EVENT = "workflow_dispatch"

_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")

LatchState = Literal["UNARMED", "ADMISSION_OPEN", "ADMISSION_CLOSED"]
RefusalReason = Literal[
    "LATCH_UNARMED",
    "FACTS_MISSING",
    "RERUN_ATTEMPT",
    "WORKFLOW_MISMATCH",
    "CURRENT_RUN_IDENTITY_MISMATCH",
    "RUN_NUMBER_NOT_ABOVE_FLOOR",
    "COMMIT_A_NOT_DISPATCHED_SHA",
    "COMMIT_A_PARENT_MISMATCH",
    "COMMIT_A_DIFF_MISMATCH",
    "PUSH_ANCHOR_MISSING_OR_AMBIGUOUS",
    "TIMESTAMP_INCONSISTENT",
    "JOB_STARTED_AT_OR_AFTER_CLOSE",
    "ADMISSION_WINDOW_CLOSED",
    "RUN_LISTING_INCOMPLETE",
    "RUN_NUMBER_MISSING",
    "RUN_NUMBER_DUPLICATE",
    "PRIOR_RUN_IDENTITY_MISMATCH",
    "PRIOR_RUN_UNRESOLVED",
    "PRIOR_RUN_EVIDENCE_INCOMPLETE",
    "PRIOR_JOB_MISSING_OR_AMBIGUOUS",
    "PRIOR_JOB_NOT_PRE_MARKER_FAILURE",
    "PRIOR_MARKER_STEP_NOT_PROVEN_SKIPPED",
    "PRIOR_EXECUTE_STEP_NOT_PROVEN_SKIPPED",
]


class LatchError(RuntimeError):
    """The latch is missing, malformed, non-canonical, chain-broken, or an
    append could not be verified. Never carries a token or local path."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def _require_server_utc(value: datetime, label: str) -> datetime:
    offset = value.utcoffset() if isinstance(value, datetime) else None
    if not isinstance(value, datetime) or value.tzinfo is None or offset is None:
        raise ValueError(f"{label} must be an aware UTC datetime")
    if offset != timedelta(0):
        raise ValueError(f"{label} must be an aware UTC datetime")
    if value.microsecond != 0:
        raise ValueError(f"{label} must have whole-second precision (GitHub server time)")
    return value


class _LatchIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    purpose: str
    replacement_of_run_id: str
    owner_ruling_id: str
    workflow_identity: str
    prev_record_sha256: str
    recorded_at_utc: datetime

    def _check_identity(self) -> None:
        if self.purpose != REPLACEMENT_PURPOSE:
            raise ValueError("purpose must be the frozen replacement purpose")
        if self.replacement_of_run_id != REPLACEMENT_OF_RUN_ID:
            raise ValueError("replacement_of_run_id must be the frozen original run id")
        if self.owner_ruling_id != OWNER_RULING_ID:
            raise ValueError("owner_ruling_id must be the frozen owner ruling id")
        if self.workflow_identity != OFFICIAL_GATE_WORKFLOW:
            raise ValueError("workflow_identity must be the official-gate workflow")
        if not _HEX64.fullmatch(self.prev_record_sha256):
            raise ValueError("prev_record_sha256 must be 64 lowercase hexadecimal characters")
        _require_server_utc(self.recorded_at_utc, "recorded_at_utc")


class LatchGenesis(_LatchIdentity):
    record_kind: Literal["GENESIS"]
    governance_ref: str

    @model_validator(mode="after")
    def _validate(self) -> "LatchGenesis":
        self._check_identity()
        if self.governance_ref != GENESIS_REF_ID:
            raise ValueError("governance_ref must be the frozen genesis reference")
        if self.prev_record_sha256 != ZERO_SHA256:
            raise ValueError("GENESIS must point at the zero predecessor")
        return self


class LatchAttemptAuthorized(_LatchIdentity):
    record_kind: Literal["ATTEMPT_AUTHORIZED"]
    readiness_source_sha: str
    owner_go_ref: str
    window_closes_at_utc: datetime
    prior_official_gate_run_number: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def _validate(self) -> "LatchAttemptAuthorized":
        self._check_identity()
        if not _HEX40.fullmatch(self.readiness_source_sha):
            raise ValueError("readiness_source_sha must be 40 lowercase hexadecimal characters")
        if not _IDENTIFIER.fullmatch(self.owner_go_ref):
            raise ValueError("owner_go_ref must be a bounded identifier")
        if self.prev_record_sha256 == ZERO_SHA256:
            raise ValueError("ATTEMPT_AUTHORIZED must follow GENESIS")
        _require_server_utc(self.window_closes_at_utc, "window_closes_at_utc")
        if not self.recorded_at_utc < self.window_closes_at_utc <= self.recorded_at_utc + MAX_AUTHORIZATION_WINDOW:
            raise ValueError("window_closes_at_utc must be after recorded_at_utc and at most 24 hours later")
        return self


LatchRecord = LatchGenesis | LatchAttemptAuthorized
_MODEL_BY_KIND: dict[str, type[BaseModel]] = {
    "GENESIS": LatchGenesis,
    "ATTEMPT_AUTHORIZED": LatchAttemptAuthorized,
}


def record_sha256(record: LatchRecord) -> str:
    return sha256_hex_of_model(record)


def record_line_bytes(record: LatchRecord) -> bytes:
    """Exactly the bytes one latch line occupies: canonical JSON + LF."""
    return canonical_json_bytes(record) + b"\n"


# ---------------------------------------------------------------------------
# Read -- strict, complete-chain validation, no repair
# ---------------------------------------------------------------------------


def load_latch(path: Path) -> tuple[LatchRecord, ...]:
    """Strict-load the whole latch. A missing file is always an error:
    absence is never "unarmed"."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        raise LatchError("replacement latch is missing -- absence is never a latch state")
    if path.is_symlink() or not path.is_file():
        raise LatchError("replacement latch path is not a regular file")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LatchError("replacement latch is not valid UTF-8") from exc
    if "\r" in text:
        # Written with LF; a Windows checkout under core.autocrlf may present
        # consistent CRLF, which is a transport artifact. A lone CR is never valid.
        if text.count("\r") != text.count("\r\n"):
            raise LatchError("replacement latch contains a bare carriage return")
        text = text.replace("\r\n", "\n")
    if text == "":
        raise LatchError("replacement latch exists but is empty")
    if not text.endswith("\n"):
        raise LatchError("replacement latch has a trailing fragment (no final newline)")
    lines = text[:-1].split("\n")
    if len(lines) > MAX_RECORDS:
        raise LatchError("replacement latch carries more records than GENESIS and one ATTEMPT_AUTHORIZED")

    records: list[LatchRecord] = []
    expected_prev = ZERO_SHA256
    for index, line in enumerate(lines, start=1):
        if line.strip() == "":
            raise LatchError(f"replacement latch line {index} is blank")
        try:
            parsed = json.loads(line)
        except ValueError as exc:
            raise LatchError(f"replacement latch line {index} is not valid JSON") from exc
        kind = parsed.get("record_kind") if isinstance(parsed, dict) else None
        model = _MODEL_BY_KIND.get(kind) if isinstance(kind, str) else None
        if model is None:
            raise LatchError(f"replacement latch line {index} has an unknown record kind")
        expected_kind = "GENESIS" if index == 1 else "ATTEMPT_AUTHORIZED"
        if kind != expected_kind:
            raise LatchError(f"replacement latch line {index} must be {expected_kind}")
        try:
            record = model.model_validate_json(line)
        except Exception as exc:  # pydantic ValidationError; identifier-only message
            raise LatchError(f"replacement latch line {index} failed strict validation") from exc
        if canonical_json_bytes(record) != line.encode("utf-8"):
            raise LatchError(f"replacement latch line {index} is not in canonical form")
        if record.prev_record_sha256 != expected_prev:
            raise LatchError(f"replacement latch line {index} breaks the hash chain")
        if records and record.recorded_at_utc < records[-1].recorded_at_utc:
            raise LatchError(f"replacement latch line {index} is earlier than its predecessor")
        records.append(record)
        expected_prev = record_sha256(record)
    return tuple(records)


# ---------------------------------------------------------------------------
# Append -- the ONLY write operations
# ---------------------------------------------------------------------------


def _append_verified(path: Path, record: LatchRecord, existing: tuple[LatchRecord, ...]) -> LatchRecord:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as handle:
        handle.write(record_line_bytes(record))
        handle.flush()
        os.fsync(handle.fileno())
    reloaded = load_latch(path)
    if len(reloaded) != len(existing) + 1 or reloaded[-1] != record:
        raise LatchError("post-append verification failed: head is not the appended record")
    return record


def append_genesis(path: Path, *, server_now_utc: datetime) -> LatchGenesis:
    """Create the latch with its single GENESIS record. Refuses if any
    file already exists at ``path``. ``server_now_utc`` must be GitHub
    server time; there is no default clock."""
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise LatchError("replacement latch already exists; GENESIS is written exactly once")
    record = LatchGenesis(
        schema_version=SCHEMA_VERSION, record_kind="GENESIS", purpose=REPLACEMENT_PURPOSE,
        replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID,
        workflow_identity=OFFICIAL_GATE_WORKFLOW, governance_ref=GENESIS_REF_ID,
        prev_record_sha256=ZERO_SHA256, recorded_at_utc=server_now_utc,
    )
    return _append_verified(path, record, ())


def append_attempt_authorized(
    path: Path,
    *,
    server_now_utc: datetime,
    readiness_source_sha: str,
    owner_go_ref: str,
    window_closes_at_utc: datetime,
    prior_official_gate_run_number: int,
) -> LatchAttemptAuthorized:
    """The single UNARMED -> ADMISSION_OPEN transition, for the final-GO
    stage only. Refuses unless the latch is exactly GENESIS. Every
    argument is required; there is no default clock or run number."""
    path = Path(path)
    existing = load_latch(path)
    if len(existing) != 1:
        raise LatchError("ATTEMPT_AUTHORIZED is written exactly once, only onto an UNARMED latch")
    record = LatchAttemptAuthorized(
        schema_version=SCHEMA_VERSION, record_kind="ATTEMPT_AUTHORIZED", purpose=REPLACEMENT_PURPOSE,
        replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID,
        workflow_identity=OFFICIAL_GATE_WORKFLOW, readiness_source_sha=readiness_source_sha,
        owner_go_ref=owner_go_ref, window_closes_at_utc=window_closes_at_utc,
        prior_official_gate_run_number=prior_official_gate_run_number,
        prev_record_sha256=record_sha256(existing[0]), recorded_at_utc=server_now_utc,
    )
    if record.recorded_at_utc < existing[0].recorded_at_utc:
        raise LatchError("ATTEMPT_AUTHORIZED cannot be earlier than GENESIS")
    return _append_verified(path, record, existing)


# ---------------------------------------------------------------------------
# State and the single admission decision (pure)
# ---------------------------------------------------------------------------


def admission_state(records: Sequence[LatchRecord], server_now_utc: datetime) -> LatchState:
    """Admission state only. It never says whether an already-admitted run
    is still in flight or whether its marker was uploaded."""
    if len(records) == 1:
        return "UNARMED"
    authorization = records[-1]
    assert isinstance(authorization, LatchAttemptAuthorized)
    return "ADMISSION_OPEN" if server_now_utc < authorization.window_closes_at_utc else "ADMISSION_CLOSED"


@dataclass(frozen=True)
class LatchFacts:
    """Everything the admission decision reads, gathered from GitHub by
    ``scripts/_phase5_common.gather_latch_facts``. ``server_now_utc`` is
    the HTTP ``Date`` of the last GitHub read before the decision."""

    ctx_run_id: str
    ctx_run_attempt: int
    ctx_sha: str
    ctx_workflow_identity: str
    current_run: RunRef
    commit: CommitDetail
    push_activities: tuple[PushActivity, ...]
    job_started_at_utc: datetime
    workflow_runs: tuple[RunRef, ...]
    prior_attempt_jobs: Mapping[tuple[str, int], tuple[JobEvidence, ...]]
    server_now_utc: datetime


@dataclass(frozen=True)
class LatchVerdict:
    admitted: bool
    state: LatchState
    reason: RefusalReason | None = None


def _refuse(state: LatchState, reason: RefusalReason) -> LatchVerdict:
    return LatchVerdict(admitted=False, state=state, reason=reason)


def _patch_plus_minus_lines(patch: str) -> tuple[list[str], list[str]]:
    added, removed = [], []
    for line in patch.split("\n"):
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def _exactly_one(items: Sequence, predicate) -> object | None:
    matches = [item for item in items if predicate(item)]
    return matches[0] if len(matches) == 1 else None


def _attempt_proven_pre_marker(jobs: Sequence[JobEvidence]) -> RefusalReason | None:
    job = _exactly_one(jobs, lambda j: j.name == GATE_JOB_NAME)
    if job is None or len(jobs) != 1:
        return "PRIOR_JOB_MISSING_OR_AMBIGUOUS"
    if job.status != "completed" or job.conclusion != "failure":
        return "PRIOR_JOB_NOT_PRE_MARKER_FAILURE"
    marker = _exactly_one(job.steps, lambda s: s.name == MARKER_STEP_NAME)
    if marker is None or marker.status != "completed" or marker.conclusion != "skipped":
        return "PRIOR_MARKER_STEP_NOT_PROVEN_SKIPPED"
    execute = _exactly_one(job.steps, lambda s: s.name == EXECUTE_STEP_NAME)
    if execute is None or execute.status != "completed" or execute.conclusion != "skipped":
        return "PRIOR_EXECUTE_STEP_NOT_PROVEN_SKIPPED"
    return None


def latch_verdict(records: Sequence[LatchRecord], facts: LatchFacts | None) -> LatchVerdict:
    """The single admission decision. Admits only when every R1-R5 check
    passes; every refusal names one closed-vocabulary reason."""
    if len(records) == 1:
        return _refuse("UNARMED", "LATCH_UNARMED")
    if facts is None:
        return _refuse("UNARMED", "FACTS_MISSING")
    authorization = records[-1]
    assert isinstance(authorization, LatchAttemptAuthorized)
    close = authorization.window_closes_at_utc
    state = admission_state(records, facts.server_now_utc)

    # R2/R4: admission closes on GitHub server time at evaluation.
    if facts.server_now_utc >= close:
        return _refuse("ADMISSION_CLOSED", "ADMISSION_WINDOW_CLOSED")
    if facts.job_started_at_utc >= close:
        return _refuse(state, "JOB_STARTED_AT_OR_AFTER_CLOSE")
    if facts.server_now_utc < facts.job_started_at_utc:
        return _refuse(state, "TIMESTAMP_INCONSISTENT")

    if facts.ctx_run_attempt != 1:
        return _refuse(state, "RERUN_ATTEMPT")
    if facts.ctx_workflow_identity != authorization.workflow_identity:
        return _refuse(state, "WORKFLOW_MISMATCH")

    # R5: the current run's own identity and number.
    run = facts.current_run
    if (
        run.run_id != facts.ctx_run_id or run.workflow_path != OFFICIAL_GATE_WORKFLOW
        or run.event != DISPATCH_EVENT or run.head_branch != MAIN_BRANCH
        or run.sha != facts.ctx_sha or run.run_attempt != 1 or run.run_number is None
    ):
        return _refuse(state, "CURRENT_RUN_IDENTITY_MISMATCH")
    floor = authorization.prior_official_gate_run_number
    current_number = run.run_number
    if current_number <= floor:
        return _refuse(state, "RUN_NUMBER_NOT_ABOVE_FLOOR")

    # R1: commit A is the dispatched SHA, a child of R, appending exactly
    # this ATTEMPT_AUTHORIZED line and nothing else.
    commit = facts.commit
    if commit.sha != facts.ctx_sha:
        return _refuse(state, "COMMIT_A_NOT_DISPATCHED_SHA")
    if commit.parents != (authorization.readiness_source_sha,):
        return _refuse(state, "COMMIT_A_PARENT_MISMATCH")
    expected_line = record_line_bytes(authorization)[:-1].decode("utf-8")
    if len(commit.files) != 1:
        return _refuse(state, "COMMIT_A_DIFF_MISMATCH")
    changed = commit.files[0]
    if (
        changed.filename != LATCH_PATH.as_posix() or changed.status != "modified"
        or changed.additions != 1 or changed.deletions != 0 or changed.patch is None
    ):
        return _refuse(state, "COMMIT_A_DIFF_MISMATCH")
    added, removed = _patch_plus_minus_lines(changed.patch)
    if removed or added != [expected_line]:
        return _refuse(state, "COMMIT_A_DIFF_MISMATCH")

    # R1: T_A is GitHub's server-recorded push of A onto main, from R.
    pushes = [p for p in facts.push_activities if p.after == facts.ctx_sha]
    if len(pushes) != 1:
        return _refuse(state, "PUSH_ANCHOR_MISSING_OR_AMBIGUOUS")
    push = pushes[0]
    if push.before != authorization.readiness_source_sha or push.ref != MAIN_REF or push.activity_type != "push":
        return _refuse(state, "PUSH_ANCHOR_MISSING_OR_AMBIGUOUS")
    anchor = push.timestamp
    if not (
        authorization.recorded_at_utc <= anchor < close <= anchor + MAX_AUTHORIZATION_WINDOW
        and anchor <= facts.job_started_at_utc
    ):
        return _refuse(state, "TIMESTAMP_INCONSISTENT")

    # R5: every run number F+1 .. N-1 present exactly once; N itself
    # visible exactly once.
    numbers = Counter(r.run_number for r in facts.workflow_runs)
    if None in numbers:
        return _refuse(state, "RUN_LISTING_INCOMPLETE")
    if numbers.get(current_number, 0) != 1:
        return _refuse(state, "RUN_LISTING_INCOMPLETE")
    for number in range(floor + 1, current_number):
        count = numbers.get(number, 0)
        if count == 0:
            return _refuse(state, "RUN_NUMBER_MISSING")
        if count > 1:
            return _refuse(state, "RUN_NUMBER_DUPLICATE")

    # R3: each accounted earlier run proven to have stopped before the marker.
    for prior in sorted(
        (r for r in facts.workflow_runs if floor < r.run_number < current_number), key=lambda r: r.run_number
    ):
        if prior.workflow_path != OFFICIAL_GATE_WORKFLOW or prior.event != DISPATCH_EVENT or prior.head_branch != MAIN_BRANCH:
            return _refuse(state, "PRIOR_RUN_IDENTITY_MISMATCH")
        if prior.status != "completed":
            return _refuse(state, "PRIOR_RUN_UNRESOLVED")
        if prior.run_attempt < 1:
            return _refuse(state, "PRIOR_RUN_EVIDENCE_INCOMPLETE")
        for attempt in range(1, prior.run_attempt + 1):
            jobs = facts.prior_attempt_jobs.get((prior.run_id, attempt))
            if jobs is None:
                return _refuse(state, "PRIOR_RUN_EVIDENCE_INCOMPLETE")
            reason = _attempt_proven_pre_marker(jobs)
            if reason is not None:
                return _refuse(state, reason)

    return LatchVerdict(admitted=True, state="ADMISSION_OPEN", reason=None)

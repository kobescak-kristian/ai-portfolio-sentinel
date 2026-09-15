"""P5-D terminal-publication library (ADR-0012 sections 9, 10, 21 and
Amendment A2/A6; dispatch q77-p5d-repair-stage2b1-implement-a, Stage 2B-1).

Pure local machinery only: the execution/publication state model, the
terminal-evidence file contract, strict candidate verification, the
atomic terminal writer, publication-root inventory and quarantine, and
the finalizer decision table. No network, no workflow, no provider, no
OIDC/WIF, no marker. Nothing in ``scripts/`` imports this module in
Stage 2B-1 -- wiring is Stage 2B-2, and the replacement stays unarmed.

Frozen rules this module enforces:

- exactly ONE terminal candidate path per publication root
  (``phase5_official_gate.json``); no globbing, no "newest file";
- staging and quarantine directories are siblings of the publication
  root on the same device, never inside it, so a temporary or rejected
  file is never a publication candidate;
- a trusted terminal record is never replaced (A6);
- a raw observed signal never becomes an objective infrastructure
  cause, and the finalizer is structurally incapable of authoring a
  quality result.

stdlib(+pydantic via ``evidence_records``) only, same discipline as the
rest of ``sentinel/phase5/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Sequence

from .evidence_records import (
    GateEvidenceRecord,
    ObservedSignal,
    TerminalWriter,
    UnclassifiedBasis,
    validate_replacement_provenance,
)
from .receipts import GATE_PAYLOAD_FILENAME
from .replacement import OWNER_RULING_ID, REPLACEMENT_OF_RUN_ID, REPLACEMENT_PURPOSE

# ---------------------------------------------------------------------------
# File contract
# ---------------------------------------------------------------------------

TERMINAL_FILENAME = GATE_PAYLOAD_FILENAME  # "phase5_official_gate.json"
CHECKS_FILENAME = "phase5_official_gate_checks.json"
JOURNAL_FILENAME = "phase5_gate_journal.jsonl"
PUBLICATION_ALLOWLIST = frozenset({TERMINAL_FILENAME, CHECKS_FILENAME, JOURNAL_FILENAME})
ANCILLARY_FILENAMES = frozenset({CHECKS_FILENAME})
MAX_TERMINAL_BYTES = 16 * 1024 * 1024

_STAGING_PREFIX = "tmp-"
_STAGING_SUFFIX = ".part"


class TerminalEvidenceError(RuntimeError):
    """A terminal-evidence file-contract, write or quarantine operation
    was refused. Never carries a token, secret or local absolute path."""


class TerminalStateError(ValueError):
    """An execution/publication state transition is not permitted."""


# ---------------------------------------------------------------------------
# State model (ADR-0012 section 21)
# ---------------------------------------------------------------------------

ExecutionState = Literal[
    "PREFLIGHTED",
    "REPLACEMENT_MARKED",
    "EXECUTING",
    "SCORED_PROVISIONAL",
    "TERMINAL_EVIDENCE_WRITTEN",
    "TERMINAL_EVIDENCE_PUBLISHED",
    "INVALID_EVIDENCE_WRITTEN",
    "INVALID_EVIDENCE_PUBLISHED",
    "PUBLICATION_UNCONFIRMED",
    "PUBLICATION_FAILED",
]

PUBLICATION_STATES: frozenset[str] = frozenset(
    {
        "TERMINAL_EVIDENCE_PUBLISHED",
        "INVALID_EVIDENCE_PUBLISHED",
        "PUBLICATION_UNCONFIRMED",
        "PUBLICATION_FAILED",
    }
)
ABSORBING_STATES: frozenset[str] = frozenset(
    {"TERMINAL_EVIDENCE_PUBLISHED", "INVALID_EVIDENCE_PUBLISHED", "PUBLICATION_FAILED"}
)
RUNNER_ONLY_STATES: frozenset[str] = frozenset({"SCORED_PROVISIONAL", "TERMINAL_EVIDENCE_WRITTEN"})

ALLOWED_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"PREFLIGHTED"}),
    "PREFLIGHTED": frozenset({"REPLACEMENT_MARKED"}),
    "REPLACEMENT_MARKED": frozenset({"EXECUTING", "INVALID_EVIDENCE_WRITTEN", "PUBLICATION_FAILED"}),
    "EXECUTING": frozenset({"SCORED_PROVISIONAL", "INVALID_EVIDENCE_WRITTEN", "PUBLICATION_FAILED"}),
    "SCORED_PROVISIONAL": frozenset(
        {"TERMINAL_EVIDENCE_WRITTEN", "INVALID_EVIDENCE_WRITTEN", "PUBLICATION_FAILED"}
    ),
    # A6: a written trusted terminal record never downgrades to invalid.
    "TERMINAL_EVIDENCE_WRITTEN": frozenset(
        {"TERMINAL_EVIDENCE_PUBLISHED", "PUBLICATION_UNCONFIRMED", "PUBLICATION_FAILED"}
    ),
    "INVALID_EVIDENCE_WRITTEN": frozenset(
        {"INVALID_EVIDENCE_PUBLISHED", "PUBLICATION_UNCONFIRMED", "PUBLICATION_FAILED"}
    ),
    # Resolved only by a later governed confirmation, never assumed.
    "PUBLICATION_UNCONFIRMED": frozenset(
        {"TERMINAL_EVIDENCE_PUBLISHED", "INVALID_EVIDENCE_PUBLISHED", "PUBLICATION_FAILED"}
    ),
    "TERMINAL_EVIDENCE_PUBLISHED": frozenset(),
    "INVALID_EVIDENCE_PUBLISHED": frozenset(),
    "PUBLICATION_FAILED": frozenset(),
}


def assert_transition(
    previous: str | None, nxt: str, *, writer: TerminalWriter | None = None
) -> None:
    """Raise ``TerminalStateError`` unless ``previous -> nxt`` is permitted.

    When ``writer`` is given (a journal-writing process), two further
    invariants hold: RUNNER-only states can never be recorded by the
    FINALIZER, which may record only ``INVALID_EVIDENCE_WRITTEN``; and
    no journal writer ever records a publication state (the journal is
    already immutable once published)."""
    if previous not in ALLOWED_TRANSITIONS:
        raise TerminalStateError(f"unknown previous state {previous!r}")
    if nxt not in ALLOWED_TRANSITIONS:
        raise TerminalStateError(f"unknown next state {nxt!r}")
    if nxt not in ALLOWED_TRANSITIONS[previous]:
        raise TerminalStateError(f"transition {previous!r} -> {nxt!r} is not permitted")
    if writer is not None:
        if nxt in PUBLICATION_STATES:
            raise TerminalStateError("publication states are never recorded by a journal writer")
        if writer == "FINALIZER" and nxt != "INVALID_EVIDENCE_WRITTEN":
            raise TerminalStateError("the FINALIZER may record only INVALID_EVIDENCE_WRITTEN")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TerminalIdentity:
    """The exact execution a terminal record must belong to."""

    workflow_identity: str
    run_id: str
    run_attempt: int
    event: str
    ref: str
    source_sha: str
    expected_source_sha: str
    purpose: str


@dataclass(frozen=True)
class EnvelopeIdentity:
    """Execution-envelope identity. Produced by Stage 2C; Stage 2B-1
    only threads it through (``None`` until then)."""

    envelope_id: str
    envelope_version: str


# ---------------------------------------------------------------------------
# Candidate verification
# ---------------------------------------------------------------------------

CandidateVerdictKind = Literal[
    "ABSENT",
    "NOT_REGULAR_FILE",
    "OVERSIZE",
    "UNPARSEABLE",
    "SCHEMA_INVALID",
    "IDENTITY_INVALID",
    "PROVENANCE_INVALID",
    "TRUSTED_QUALITY",
    "TRUSTED_INFRASTRUCTURE_INVALID",
    "TRUSTED_UNCLASSIFIED",
]
TRUSTED_KINDS: frozenset[str] = frozenset(
    {"TRUSTED_QUALITY", "TRUSTED_INFRASTRUCTURE_INVALID", "TRUSTED_UNCLASSIFIED"}
)
UNTRUSTED_KINDS: frozenset[str] = frozenset(
    {"NOT_REGULAR_FILE", "OVERSIZE", "UNPARSEABLE", "SCHEMA_INVALID", "IDENTITY_INVALID", "PROVENANCE_INVALID"}
)

_PROVENANCE_FIELDS = (
    "replacement_of_run_id",
    "owner_ruling_id",
    "marker_purpose",
    "envelope_id",
    "envelope_version",
    "terminal_writer",
)


@dataclass(frozen=True)
class CandidateVerdict:
    kind: CandidateVerdictKind
    sha256: str | None = None
    # A symlink can be atomically replaced; a directory or other special
    # file cannot, and routes the finalizer to INTERNAL_ERROR.
    replaceable: bool = True
    record: GateEvidenceRecord | None = field(default=None, repr=False, compare=False)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity_matches(record: GateEvidenceRecord, identity: TerminalIdentity) -> bool:
    return (
        record.workflow_identity == identity.workflow_identity
        and record.github_run_id == identity.run_id
        and record.run_attempt == identity.run_attempt
        and record.event == identity.event
        and record.ref == identity.ref
        and record.source_sha == identity.source_sha
        and record.expected_source_sha == identity.expected_source_sha
        and record.source_sha == record.expected_source_sha
    )


def verify_terminal_bytes(data: bytes, identity: TerminalIdentity) -> CandidateVerdict:
    """Strict, deterministic trust decision over raw terminal bytes. The
    single function the finalizer, publication confirmation and later
    durable receipt recording all share.

    Under the replacement purpose, ``validate_replacement_provenance``
    (including strict terminal-writer provenance) must pass. Under any
    other purpose every replacement-provenance field and
    ``terminal_writer`` must be ``None`` -- so no Stage-2B-authored record
    can ever be trusted under the permanently non-qualifying original
    purpose."""
    digest = _sha256(data)
    if len(data) > MAX_TERMINAL_BYTES:
        return CandidateVerdict(kind="OVERSIZE", sha256=digest)
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return CandidateVerdict(kind="UNPARSEABLE", sha256=digest)
    if not isinstance(parsed, dict):
        return CandidateVerdict(kind="UNPARSEABLE", sha256=digest)
    try:
        record = GateEvidenceRecord.model_validate(parsed)
    except Exception:  # noqa: BLE001 - pydantic ValidationError; identifier-only verdict
        return CandidateVerdict(kind="SCHEMA_INVALID", sha256=digest)
    if not _identity_matches(record, identity):
        return CandidateVerdict(kind="IDENTITY_INVALID", sha256=digest)
    if identity.purpose == REPLACEMENT_PURPOSE:
        try:
            validate_replacement_provenance(record, expected_source_sha=identity.expected_source_sha)
        except ValueError:
            return CandidateVerdict(kind="PROVENANCE_INVALID", sha256=digest)
    elif any(getattr(record, name) is not None for name in _PROVENANCE_FIELDS):
        return CandidateVerdict(kind="PROVENANCE_INVALID", sha256=digest)
    if record.disposition in ("GREEN", "HONEST_FAIL"):
        kind: CandidateVerdictKind = "TRUSTED_QUALITY"
    elif record.disposition == "INFRASTRUCTURE_FAILURE":
        kind = "TRUSTED_INFRASTRUCTURE_INVALID"
    else:
        kind = "TRUSTED_UNCLASSIFIED"
    return CandidateVerdict(kind=kind, sha256=digest, record=record)


def _assert_real_directory(path: Path, label: str) -> os.stat_result:
    try:
        st = os.lstat(path)
    except FileNotFoundError as exc:
        raise TerminalEvidenceError(f"{label} directory does not exist") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise TerminalEvidenceError(f"{label} is not a real directory")
    return st


def classify_candidate(publication_root: Path, identity: TerminalIdentity) -> CandidateVerdict:
    """Classify the ONE fixed terminal candidate path. Never globs, never
    scans for alternatives, never inspects staging files."""
    root = Path(publication_root)
    _assert_real_directory(root, "publication root")
    target = root / TERMINAL_FILENAME
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return CandidateVerdict(kind="ABSENT")
    if stat.S_ISLNK(st.st_mode):
        return CandidateVerdict(kind="NOT_REGULAR_FILE", replaceable=True)
    if not stat.S_ISREG(st.st_mode):
        return CandidateVerdict(kind="NOT_REGULAR_FILE", replaceable=False)
    if st.st_size > MAX_TERMINAL_BYTES:
        return CandidateVerdict(kind="OVERSIZE")
    return verify_terminal_bytes(target.read_bytes(), identity)


# ---------------------------------------------------------------------------
# Invalid-record construction (never quality)
# ---------------------------------------------------------------------------


def build_invalid_record(
    *,
    identity: TerminalIdentity,
    envelope: EnvelopeIdentity | None,
    created_at_utc: datetime,
    model: str,
    profile_name: str,
    infrastructure_cause: str | None = None,
    writer: TerminalWriter | None = None,
    unclassified_basis: UnclassifiedBasis | None = None,
    observed_signals: Sequence[ObservedSignal] = (),
    auth_mode: str | None = None,
) -> GateEvidenceRecord:
    """Build an execution-invalid terminal record with every quality
    field empty. There is deliberately no parameter that can produce
    GREEN or HONEST_FAIL.

    Exactly one of ``infrastructure_cause`` (INFRASTRUCTURE_FAILURE,
    requires an explicit ``writer`` RUNNER or FINALIZER) or
    ``unclassified_basis`` (UNCLASSIFIED_TERMINATION, writer is always
    FINALIZER) must be given."""
    if (infrastructure_cause is None) == (unclassified_basis is None):
        raise TerminalEvidenceError(
            "exactly one of infrastructure_cause or unclassified_basis must be given"
        )
    if infrastructure_cause is not None:
        if infrastructure_cause not in ("PRE_PROVIDER_FAILURE", "RUNNER_EXCEPTION"):
            # Stage 2B produces only runner-observed causes; deadline and
            # watchdog causes arrive with their Stage-2C producers.
            raise TerminalEvidenceError(f"unsupported Stage-2B infrastructure cause {infrastructure_cause!r}")
        if writer not in ("RUNNER", "FINALIZER"):
            raise TerminalEvidenceError("an INFRASTRUCTURE_FAILURE record requires writer RUNNER or FINALIZER")
        disposition = "INFRASTRUCTURE_FAILURE"
        resolved_writer: TerminalWriter = writer
    else:
        if writer not in (None, "FINALIZER"):
            raise TerminalEvidenceError("an UNCLASSIFIED_TERMINATION record is only ever FINALIZER-authored")
        disposition = "UNCLASSIFIED_TERMINATION"
        resolved_writer = "FINALIZER"

    provenance: dict = {}
    if identity.purpose == REPLACEMENT_PURPOSE:
        provenance = dict(
            replacement_of_run_id=REPLACEMENT_OF_RUN_ID,
            owner_ruling_id=OWNER_RULING_ID,
            marker_purpose=REPLACEMENT_PURPOSE,
            envelope_id=envelope.envelope_id if envelope else None,
            envelope_version=envelope.envelope_version if envelope else None,
        )
    return GateEvidenceRecord(
        schema_version=1,
        workflow_identity=identity.workflow_identity,
        github_run_id=identity.run_id,
        run_attempt=identity.run_attempt,
        event=identity.event,
        ref=identity.ref,
        source_sha=identity.source_sha,
        created_at_utc=created_at_utc,
        steps=(),
        expected_source_sha=identity.expected_source_sha,
        model=model,
        profile_name=profile_name,
        run_ids=(),
        scoring={},
        thresholds={},
        invariant_results={},
        execution_validity={},
        miss_patterns=(),
        failed_checks=(),
        cost_rows=(),
        accounted_total_eur_micros=0,
        disposition=disposition,
        auth_mode=auth_mode,
        termination_source=infrastructure_cause,
        observed_signals=tuple(observed_signals),
        unclassified_basis=unclassified_basis,
        terminal_writer=resolved_writer,
        **provenance,
    )


# ---------------------------------------------------------------------------
# Atomic writers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AbsentPrior:
    """The runner's guard: the terminal path must not exist."""


@dataclass(frozen=True)
class UntrustedPrior:
    """The finalizer's guard: the current bytes must still hash to
    ``sha256`` and must still classify as untrusted."""

    sha256: str


PRIOR_ABSENT = AbsentPrior()
PriorCandidate = AbsentPrior | UntrustedPrior


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _assert_layout(publication_root: Path, other_root: Path, label: str) -> None:
    root_st = _assert_real_directory(publication_root, "publication root")
    other_st = _assert_real_directory(other_root, label)
    root_resolved = publication_root.resolve()
    other_resolved = other_root.resolve()
    if _is_within(other_resolved, root_resolved) or _is_within(root_resolved, other_resolved):
        raise TerminalEvidenceError(f"{label} directory must be a sibling of the publication root, never nested")
    if root_st.st_dev != other_st.st_dev:
        raise TerminalEvidenceError(f"{label} directory must be on the same device as the publication root")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":  # directory fsync is not supported on Windows
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _stage_bytes(staging_root: Path, data: bytes) -> Path:
    tmp = staging_root / f"{_STAGING_PREFIX}{uuid.uuid4().hex}{_STAGING_SUFFIX}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return tmp


def _discard_staged(tmp: Path) -> None:
    try:
        tmp.unlink()
    except OSError:
        pass


def write_terminal_atomically(
    record: GateEvidenceRecord,
    *,
    publication_root: Path,
    staging_root: Path,
    prior: PriorCandidate,
    identity: TerminalIdentity,
) -> str:
    """Write ``record`` to the one terminal path: temp file in staging,
    write, fsync, atomic ``os.replace``, directory fsync where supported.
    Returns the SHA-256 of the written bytes.

    ``prior`` is mandatory. ``PRIOR_ABSENT`` refuses an existing target.
    ``UntrustedPrior(sha)`` re-reads the target immediately before the
    replace and refuses unless it still hashes to ``sha`` and still
    classifies as untrusted -- a trusted terminal record is never
    replaced (A6)."""
    publication_root = Path(publication_root)
    staging_root = Path(staging_root)
    _assert_layout(publication_root, staging_root, "staging")
    if not isinstance(prior, (AbsentPrior, UntrustedPrior)):
        raise TerminalEvidenceError("prior must be PRIOR_ABSENT or UntrustedPrior")
    data = record.model_dump_json(indent=2).encode("utf-8")
    target = publication_root / TERMINAL_FILENAME
    tmp = _stage_bytes(staging_root, data)
    try:
        if isinstance(prior, AbsentPrior):
            if os.path.lexists(target):
                raise TerminalEvidenceError("terminal evidence already exists; refusing to write")
        else:
            current = classify_candidate(publication_root, identity)
            if current.kind in TRUSTED_KINDS:
                raise TerminalEvidenceError("trusted terminal evidence exists; it is never replaced")
            if current.kind == "ABSENT" or current.sha256 != prior.sha256:
                raise TerminalEvidenceError("terminal candidate changed since it was classified; refusing")
            if not current.replaceable:
                raise TerminalEvidenceError("terminal candidate is not a replaceable file")
        os.replace(tmp, target)
    except BaseException:
        _discard_staged(tmp)
        raise
    _fsync_directory(publication_root)
    return _sha256(data)


def write_ancillary_atomically(
    name: str, data: bytes, *, publication_root: Path, staging_root: Path
) -> str:
    """Atomically write an ancillary (never-candidate) file. Refuses the
    terminal filename, the journal (which has its own append writer) and
    every name outside the ancillary allowlist, and refuses to replace
    an existing file."""
    if name not in ANCILLARY_FILENAMES:
        raise TerminalEvidenceError(f"{name!r} is not an ancillary publication filename")
    publication_root = Path(publication_root)
    staging_root = Path(staging_root)
    _assert_layout(publication_root, staging_root, "staging")
    target = publication_root / name
    tmp = _stage_bytes(staging_root, data)
    try:
        if os.path.lexists(target):
            raise TerminalEvidenceError(f"ancillary file {name!r} already exists; refusing to replace")
        os.replace(tmp, target)
    except BaseException:
        _discard_staged(tmp)
        raise
    _fsync_directory(publication_root)
    return _sha256(data)


# ---------------------------------------------------------------------------
# Publication-root inventory and quarantine
# ---------------------------------------------------------------------------

QuarantinePathClass = Literal["STAGING", "UNEXPECTED", "ANCILLARY_WITHOUT_TRUSTED_QUALITY"]


@dataclass(frozen=True)
class PublicationInventory:
    present_allowlisted: frozenset[str]
    unexpected: tuple[str, ...]


def inventory_publication_root(publication_root: Path) -> PublicationInventory:
    """Name-based inventory: allowlisted names present, and every other
    entry (including any stray staging file) as UNEXPECTED."""
    root = Path(publication_root)
    _assert_real_directory(root, "publication root")
    names = sorted(entry.name for entry in os.scandir(root))
    present = frozenset(n for n in names if n in PUBLICATION_ALLOWLIST)
    unexpected = tuple(n for n in names if n not in PUBLICATION_ALLOWLIST)
    return PublicationInventory(present_allowlisted=present, unexpected=unexpected)


def _regular_file_bytes(path: Path) -> bytes:
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise TerminalEvidenceError("only a regular file can be quarantined")
    return path.read_bytes()


def copy_to_quarantine(path: Path, quarantine_root: Path, *, publication_root: Path) -> str:
    """Copy an untrusted terminal candidate's bytes into quarantine
    (the candidate path itself is later atomically replaced, so it is
    never empty in between). Returns the SHA-256 of the copied bytes."""
    _assert_layout(Path(publication_root), Path(quarantine_root), "quarantine")
    data = _regular_file_bytes(Path(path))
    digest = _sha256(data)
    dest = Path(quarantine_root) / f"untrusted-candidate-{digest}.bin"
    if dest.exists():
        return digest
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(dest, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return digest


def move_to_quarantine(
    path: Path, quarantine_root: Path, *, publication_root: Path, path_class: QuarantinePathClass
) -> str:
    """Move a regular file out of the publication root into quarantine
    (atomic, same device). Returns the SHA-256 of the moved bytes."""
    if path_class not in ("STAGING", "UNEXPECTED", "ANCILLARY_WITHOUT_TRUSTED_QUALITY"):
        raise TerminalEvidenceError(f"unknown quarantine path class {path_class!r}")
    _assert_layout(Path(publication_root), Path(quarantine_root), "quarantine")
    digest = _sha256(_regular_file_bytes(Path(path)))
    dest = Path(quarantine_root) / f"{path_class.lower()}-{digest}.bin"
    os.replace(path, dest)
    return digest


# ---------------------------------------------------------------------------
# Finalizer decision table (pure)
# ---------------------------------------------------------------------------

MarkerConsumption = Literal["CONSUMED", "NOT_CONSUMED_BY_THIS_ATTEMPT", "ASSUMED_CONSUMED_REST_UNAVAILABLE"]
JournalIntegrity = Literal["ABSENT", "OK", "TRAILING_FRAGMENT", "CORRUPT"]
FinalizerAction = Literal[
    "PRESERVE_RUNNER_EVIDENCE",
    "WRITE_INFRASTRUCTURE_INVALID",
    "WRITE_UNCLASSIFIED",
    "NO_TERMINAL_REQUIRED",
    "INTERNAL_ERROR",
]
InfrastructureCause = Literal["PRE_PROVIDER_FAILURE", "RUNNER_EXCEPTION"]


@dataclass(frozen=True)
class JournalSummary:
    integrity: JournalIntegrity
    signals: tuple[ObservedSignal, ...] = ()
    runner_exception_cause: InfrastructureCause | None = None
    terminal_write_failed: bool = False
    last_state: str | None = None


@dataclass(frozen=True)
class FinalizerDecision:
    action: FinalizerAction
    reason: str | None = None
    infrastructure_cause: InfrastructureCause | None = None
    unclassified_basis: UnclassifiedBasis | None = None
    observed_signals: tuple[ObservedSignal, ...] = ()
    quarantine_candidate: bool = False
    quarantine_ancillary: bool = False
    quarantine_unexpected: bool = False


def consumption_from(marker_step_outcome: str, rest_marker_visible: bool | None) -> MarkerConsumption:
    """Consumption by THIS attempt. ``skipped`` means the marker step never
    ran; ``success`` means the marker was uploaded. Any other outcome
    (failure, cancelled, empty) defers to a REST visibility check, and an
    unavailable REST answer is assumed consumed (fail toward preservation)."""
    if marker_step_outcome == "skipped":
        return "NOT_CONSUMED_BY_THIS_ATTEMPT"
    if marker_step_outcome == "success":
        return "CONSUMED"
    if rest_marker_visible is True:
        return "CONSUMED"
    if rest_marker_visible is False:
        return "NOT_CONSUMED_BY_THIS_ATTEMPT"
    return "ASSUMED_CONSUMED_REST_UNAVAILABLE"


def decide_finalization(
    *,
    run_attempt: int,
    consumption: MarkerConsumption,
    candidate: CandidateVerdictKind,
    journal: JournalSummary,
    execute_step_outcome: str,
    candidate_replaceable: bool = True,
) -> FinalizerDecision:
    """The frozen Stage-2B finalizer decision table. Pure; no I/O.

    Signal != cause: any observed signal (or a platform-cancelled execute
    step) yields UNCLASSIFIED_TERMINATION, never INFRASTRUCTURE_FAILURE.
    No row ever writes a quality result, and no row ever infers a Stage-2C
    deadline or watchdog cause."""
    if run_attempt != 1:
        return FinalizerDecision(action="NO_TERMINAL_REQUIRED", reason="RUN_ATTEMPT_GT_1")
    if consumption == "NOT_CONSUMED_BY_THIS_ATTEMPT":
        return FinalizerDecision(action="NO_TERMINAL_REQUIRED", reason="NOT_CONSUMED_BY_THIS_ATTEMPT")

    signals: tuple[ObservedSignal, ...] = tuple(dict.fromkeys(journal.signals))
    if execute_step_outcome == "cancelled" and not signals:
        signals = ("UNKNOWN_EXTERNAL_TERMINATION",)

    if candidate in TRUSTED_KINDS:
        return FinalizerDecision(
            action="PRESERVE_RUNNER_EVIDENCE",
            quarantine_ancillary=candidate != "TRUSTED_QUALITY",
            quarantine_unexpected=True,
        )

    if candidate == "ABSENT":
        if signals:
            return FinalizerDecision(
                action="WRITE_UNCLASSIFIED", unclassified_basis="OBSERVED_SIGNAL",
                observed_signals=signals, quarantine_ancillary=True, quarantine_unexpected=True,
            )
        if journal.integrity in ("OK", "TRAILING_FRAGMENT") and (
            journal.runner_exception_cause is not None or journal.terminal_write_failed
        ):
            return FinalizerDecision(
                action="WRITE_INFRASTRUCTURE_INVALID",
                infrastructure_cause=journal.runner_exception_cause or "RUNNER_EXCEPTION",
                quarantine_ancillary=True, quarantine_unexpected=True,
            )
        return FinalizerDecision(
            action="WRITE_UNCLASSIFIED", unclassified_basis="RUNNER_TERMINAL_EVIDENCE_ABSENT",
            quarantine_ancillary=True, quarantine_unexpected=True,
        )

    if candidate in UNTRUSTED_KINDS:
        if candidate == "NOT_REGULAR_FILE" and not candidate_replaceable:
            return FinalizerDecision(action="INTERNAL_ERROR", reason="CANDIDATE_NOT_REPLACEABLE")
        return FinalizerDecision(
            action="WRITE_UNCLASSIFIED", unclassified_basis="RUNNER_TERMINAL_EVIDENCE_UNTRUSTED",
            observed_signals=signals, quarantine_candidate=candidate != "NOT_REGULAR_FILE",
            quarantine_ancillary=True, quarantine_unexpected=True,
        )

    return FinalizerDecision(action="INTERNAL_ERROR", reason="UNKNOWN_CANDIDATE_VERDICT")

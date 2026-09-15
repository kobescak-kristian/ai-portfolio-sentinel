"""P5-D operational journal (ADR-0012 section 9 and Amendment A2 rule 4;
dispatch q77-p5d-repair-stage2b1-implement-a, Stage 2B-1).

A serialized, thread-safe, append-only, fsynced journal of NON-CONTENT
operational metadata only. Every field is a closed Literal, a bounded
integer, a 64-hex digest or a bounded exception class name, so no free
text can enter it: no model response, finding, answer, score, quality
disposition, credential, token or local path is representable. The
journal is never echoed to workflow logs; before publication the only
operator-visible liveness surface is ``liveness_line``.

Failure policy: a journal fault must never change gate execution. Any
open or append failure latches ``broken`` and later appends do nothing;
nothing here raises into the caller. A broken or partial journal can
only make a later finalizer classification weaker (unclassified), never
objective.

Signal hooks observe and then deliver the original default behaviour
unchanged (SIGINT still raises ``KeyboardInterrupt``; SIGTERM with the
default disposition still terminates the process). They never convert
cancellation into anything else. Graceful cancellation is Stage 2C.

Not wired into any entrypoint in Stage 2B-1.
"""

from __future__ import annotations

import os
import re
import signal as _signal
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence_records import ObservedSignal
from .models import canonical_json_bytes
from .terminal import (
    CandidateVerdictKind,
    ExecutionState,
    FinalizerAction,
    InfrastructureCause,
    JournalIntegrity,
    JournalSummary,
    MarkerConsumption,
    QuarantinePathClass,
)

MAX_LINE_BYTES = 2048
MAX_JOURNAL_BYTES = 8 * 1024 * 1024
MAX_JOURNAL_EVENTS = 20_000

_EXCEPTION_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}")
_HEX64 = re.compile(r"[0-9a-f]{64}")

JournalWriterRole = Literal["RUNNER", "FINALIZER"]
JournalEventType = Literal[
    "JOURNAL_OPENED",
    "STATE_TRANSITION",
    "RUN_STARTED",
    "RUN_FINISHED",
    "INVOCATION_STARTED",
    "INVOCATION_FINISHED",
    "HEARTBEAT",
    "SIGNAL_OBSERVED",
    "RUNNER_EXCEPTION",
    "TERMINAL_WRITE_STARTED",
    "TERMINAL_WRITE_COMPLETED",
    "TERMINAL_WRITE_FAILED",
    "JOURNAL_FRAGMENT_OBSERVED",
    "FINALIZER_CONSUMPTION",
    "FINALIZER_CANDIDATE",
    "FINALIZER_QUARANTINED",
    "FINALIZER_DECISION",
]
RecordKind = Literal["QUALITY", "INFRASTRUCTURE_INVALID", "UNCLASSIFIED"]

_DETAIL_FIELDS = (
    "state_from",
    "state_to",
    "run_ordinal",
    "invocation_ordinal",
    "invocation_outcome",
    "signal",
    "cause",
    "exception_type",
    "record_kind",
    "sha256",
    "candidate_verdict",
    "action",
    "consumption",
    "path_class",
    "byte_length",
)

# event -> (required detail fields, additionally permitted detail fields)
_EVENT_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "JOURNAL_OPENED": (frozenset(), frozenset()),
    "STATE_TRANSITION": (frozenset({"state_to"}), frozenset({"state_from"})),
    "RUN_STARTED": (frozenset({"run_ordinal"}), frozenset()),
    "RUN_FINISHED": (frozenset({"run_ordinal"}), frozenset()),
    "INVOCATION_STARTED": (frozenset({"run_ordinal", "invocation_ordinal"}), frozenset()),
    "INVOCATION_FINISHED": (
        frozenset({"run_ordinal", "invocation_ordinal", "invocation_outcome"}), frozenset()
    ),
    "HEARTBEAT": (frozenset(), frozenset()),
    "SIGNAL_OBSERVED": (frozenset({"signal"}), frozenset()),
    "RUNNER_EXCEPTION": (frozenset({"cause", "exception_type"}), frozenset()),
    "TERMINAL_WRITE_STARTED": (frozenset({"record_kind"}), frozenset()),
    "TERMINAL_WRITE_COMPLETED": (frozenset({"record_kind", "sha256"}), frozenset()),
    "TERMINAL_WRITE_FAILED": (frozenset({"record_kind", "exception_type"}), frozenset()),
    "JOURNAL_FRAGMENT_OBSERVED": (frozenset({"byte_length"}), frozenset()),
    "FINALIZER_CONSUMPTION": (frozenset({"consumption"}), frozenset()),
    "FINALIZER_CANDIDATE": (frozenset({"candidate_verdict"}), frozenset({"sha256"})),
    "FINALIZER_QUARANTINED": (frozenset({"path_class", "sha256"}), frozenset()),
    "FINALIZER_DECISION": (frozenset({"action"}), frozenset()),
}
_FINALIZER_ONLY_EVENTS = frozenset(
    {"FINALIZER_CONSUMPTION", "FINALIZER_CANDIDATE", "FINALIZER_QUARANTINED", "FINALIZER_DECISION"}
)
_RUNNER_ONLY_EVENTS = frozenset(
    {
        "RUN_STARTED", "RUN_FINISHED", "INVOCATION_STARTED", "INVOCATION_FINISHED",
        "RUNNER_EXCEPTION", "TERMINAL_WRITE_STARTED", "TERMINAL_WRITE_COMPLETED",
        "TERMINAL_WRITE_FAILED",
    }
)


class JournalEvent(BaseModel):
    """One journal line. Closed, bounded, content-free by construction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    seq: int = Field(ge=1)
    recorded_at_utc: datetime
    elapsed_ms: int = Field(ge=0)
    writer: JournalWriterRole
    event: JournalEventType
    state_from: ExecutionState | None = None
    state_to: ExecutionState | None = None
    run_ordinal: Literal[1, 2] | None = None
    invocation_ordinal: int | None = Field(default=None, ge=1, le=200)
    invocation_outcome: Literal["RETURNED", "RAISED"] | None = None
    signal: ObservedSignal | None = None
    cause: InfrastructureCause | None = None
    exception_type: str | None = None
    record_kind: RecordKind | None = None
    sha256: str | None = None
    candidate_verdict: CandidateVerdictKind | None = None
    action: FinalizerAction | None = None
    consumption: MarkerConsumption | None = None
    path_class: QuarantinePathClass | None = None
    byte_length: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate(self) -> "JournalEvent":
        offset = self.recorded_at_utc.utcoffset()
        if self.recorded_at_utc.tzinfo is None or offset is None or offset != timedelta(0):
            raise ValueError("recorded_at_utc must be an aware UTC datetime")
        required, permitted = _EVENT_FIELDS[self.event]
        allowed = required | permitted
        for name in _DETAIL_FIELDS:
            value = getattr(self, name)
            if name in required and value is None:
                raise ValueError(f"{self.event} requires {name}")
            if name not in allowed and value is not None:
                raise ValueError(f"{self.event} does not permit {name}")
        if self.exception_type is not None and not _EXCEPTION_TYPE.fullmatch(self.exception_type):
            raise ValueError("exception_type must be a bare class name of at most 80 identifier characters")
        if self.sha256 is not None and not _HEX64.fullmatch(self.sha256):
            raise ValueError("sha256 must be exactly 64 lowercase hexadecimal characters")
        if self.event == "STATE_TRANSITION" and self.state_from is None and self.state_to != "PREFLIGHTED":
            raise ValueError("state_from may be omitted only for the first transition to PREFLIGHTED")
        if self.event in _FINALIZER_ONLY_EVENTS and self.writer != "FINALIZER":
            raise ValueError(f"{self.event} is written only by the FINALIZER")
        if self.event in _RUNNER_ONLY_EVENTS and self.writer != "RUNNER":
            raise ValueError(f"{self.event} is written only by the RUNNER")
        return self


def event_line_bytes(event: JournalEvent) -> bytes:
    return canonical_json_bytes(event) + b"\n"


def liveness_line(elapsed_s: float) -> str:
    """The ONLY pre-publication liveness surface: bounded elapsed time."""
    return f"HEARTBEAT elapsed_s={int(max(0, elapsed_s))}"


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JournalReadResult:
    events: tuple[JournalEvent, ...]
    integrity: JournalIntegrity
    fragment_bytes: int = 0
    size: int = 0

    @property
    def last_seq(self) -> int:
        return self.events[-1].seq if self.events else 0


def _parse_line(line: bytes) -> JournalEvent | None:
    try:
        event = JournalEvent.model_validate_json(line)
    except Exception:  # noqa: BLE001 - pydantic ValidationError / JSON error
        return None
    if canonical_json_bytes(event) != line:
        return None
    return event


def read_journal(path: Path) -> JournalReadResult:
    """Strict reader. Returns the valid event prefix and an integrity
    verdict: ABSENT, OK, TRAILING_FRAGMENT (an incomplete final write),
    or CORRUPT (an unacknowledged invalid line, a seq gap, a symlink or an
    oversize file). An invalid line immediately followed by a matching
    JOURNAL_FRAGMENT_OBSERVED event is an acknowledged fragment."""
    path = Path(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return JournalReadResult(events=(), integrity="ABSENT")
    if not os.path.isfile(path) or os.path.islink(path):
        return JournalReadResult(events=(), integrity="CORRUPT")
    if st.st_size > MAX_JOURNAL_BYTES:
        return JournalReadResult(events=(), integrity="CORRUPT", size=st.st_size)
    data = path.read_bytes()
    complete, _sep, trailing = data.rpartition(b"\n")
    lines = complete.split(b"\n") if _sep else []
    if not _sep:
        trailing = data

    events: list[JournalEvent] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        event = _parse_line(line)
        if event is None:
            follower = _parse_line(lines[index + 1]) if index + 1 < len(lines) else None
            if (
                follower is not None
                and follower.event == "JOURNAL_FRAGMENT_OBSERVED"
                and follower.byte_length == len(line)
            ):
                index += 1
                continue
            return JournalReadResult(events=tuple(events), integrity="CORRUPT", size=len(data))
        expected = events[-1].seq + 1 if events else 1
        if event.seq != expected:
            return JournalReadResult(events=tuple(events), integrity="CORRUPT", size=len(data))
        events.append(event)
        index += 1
    if trailing:
        return JournalReadResult(
            events=tuple(events), integrity="TRAILING_FRAGMENT", fragment_bytes=len(trailing), size=len(data)
        )
    return JournalReadResult(events=tuple(events), integrity="OK", size=len(data))


def summarize_journal(result: JournalReadResult) -> JournalSummary:
    """Reduce a read result to the finalizer's inputs. Signals come from
    any writer; runner exception and terminal-write failure only from the
    RUNNER."""
    signals: list[str] = []
    cause = None
    write_failed = False
    last_state = None
    for event in result.events:
        if event.event == "SIGNAL_OBSERVED" and event.signal not in signals:
            signals.append(event.signal)
        elif event.event == "RUNNER_EXCEPTION" and event.writer == "RUNNER" and cause is None:
            cause = event.cause
        elif event.event == "TERMINAL_WRITE_FAILED" and event.writer == "RUNNER":
            write_failed = True
        elif event.event == "STATE_TRANSITION":
            last_state = event.state_to
    return JournalSummary(
        integrity=result.integrity,
        signals=tuple(signals),
        runner_exception_cause=cause,
        terminal_write_failed=write_failed,
        last_state=last_state,
    )


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OperationalJournal:
    """Serialized append-only journal writer for one process role."""

    def __init__(
        self,
        path: Path,
        *,
        writer: JournalWriterRole,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        max_bytes: int = MAX_JOURNAL_BYTES,
        max_events: int = MAX_JOURNAL_EVENTS,
        fsync: Callable[[int], None] = os.fsync,
    ) -> None:
        if writer not in ("RUNNER", "FINALIZER"):
            raise ValueError("writer must be RUNNER or FINALIZER")
        self._path = Path(path)
        self._writer = writer
        self._clock = clock
        self._monotonic = monotonic
        self._max_bytes = max_bytes
        self._max_events = max_events
        self._fsync = fsync
        self._lock = threading.Lock()
        self._pending: deque[tuple[str, dict]] = deque()
        self._signals: list[str] = []
        self._fd: int | None = None
        self._broken = False
        self._next_seq = 1
        self._bytes = 0
        self._events = 0
        self._t0 = monotonic()

    # -- state -----------------------------------------------------------

    @property
    def broken(self) -> bool:
        return self._broken

    @property
    def signals_observed(self) -> tuple[str, ...]:
        return tuple(self._signals)

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> "OperationalJournal":
        try:
            if os.path.islink(self._path):
                raise OSError("journal path is a symlink")
            existing = read_journal(self._path)
            flags = (
                os.O_WRONLY | os.O_APPEND | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            )
            self._fd = os.open(self._path, flags, 0o600)
            self._next_seq = existing.last_seq + 1
            self._bytes = existing.size
            self._events = len(existing.events)
            with self._lock:
                if existing.integrity == "TRAILING_FRAGMENT":
                    self._write_locked(b"\n")
                    self._append_locked("JOURNAL_FRAGMENT_OBSERVED", {"byte_length": existing.fragment_bytes})
                self._append_locked("JOURNAL_OPENED", {})
                self._drain_locked()
        except Exception:  # noqa: BLE001 - journal faults latch, never raise
            self._latch()
        return self

    def close(self) -> None:
        if self._fd is None:
            return
        with self._lock:
            self._drain_locked()
            fd, self._fd = self._fd, None
        try:
            os.close(fd)
        except OSError:
            pass

    def __enter__(self) -> "OperationalJournal":
        return self.open()

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- appends -----------------------------------------------------------

    def append(self, event: str, **fields) -> JournalEvent | None:
        if self._broken or self._fd is None:
            return None
        with self._lock:
            written = self._append_locked(event, fields)
            self._drain_locked()
        self._drain_stranded()
        return written

    def observe_signal(self, signal_name: ObservedSignal) -> None:
        """Signal-handler safe: records in memory first, then journals
        without ever blocking on the lock (a handler may run while this
        same thread holds it). A deferred event is drained by the lock
        holder."""
        if signal_name not in self._signals:
            self._signals.append(signal_name)
        if self._broken or self._fd is None:
            return
        if self._lock.acquire(blocking=False):
            try:
                self._append_locked("SIGNAL_OBSERVED", {"signal": signal_name})
                self._drain_locked()
            finally:
                self._lock.release()
        else:
            self._pending.append(("SIGNAL_OBSERVED", {"signal": signal_name}))

    # -- internals ---------------------------------------------------------

    def _latch(self) -> None:
        self._broken = True

    def _drain_stranded(self) -> None:
        if self._pending and self._lock.acquire(blocking=False):
            try:
                self._drain_locked()
            finally:
                self._lock.release()

    def _drain_locked(self) -> None:
        while self._pending and not self._broken:
            event, fields = self._pending.popleft()
            self._append_locked(event, fields)

    def _write_locked(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]
        self._fsync(self._fd)
        self._bytes += len(data)

    def _append_locked(self, event: str, fields: dict) -> JournalEvent | None:
        if self._broken or self._fd is None:
            return None
        try:
            record = JournalEvent(
                schema_version=1,
                seq=self._next_seq,
                recorded_at_utc=self._clock(),
                elapsed_ms=max(0, int((self._monotonic() - self._t0) * 1000)),
                writer=self._writer,
                event=event,
                **fields,
            )
            line = event_line_bytes(record)
            if len(line) > MAX_LINE_BYTES:
                raise ValueError("journal line exceeds the bounded line size")
            if self._bytes + len(line) > self._max_bytes or self._events + 1 > self._max_events:
                raise ValueError("journal capacity exceeded")
            self._write_locked(line)
        except Exception:  # noqa: BLE001 - journal faults latch, never raise
            self._latch()
            return None
        self._next_seq += 1
        self._events += 1
        return record


# ---------------------------------------------------------------------------
# Observe-and-redeliver signal hooks
# ---------------------------------------------------------------------------

_OBSERVABLE_SIGNALS: tuple[tuple[str, str], ...] = (("SIGINT", "SIGINT"), ("SIGTERM", "SIGTERM"))


def _redeliver(signum: int, previous, frame) -> None:
    """Deliver exactly the behaviour the process had before the hook."""
    if callable(previous):
        previous(signum, frame)
        return
    if previous == _signal.SIG_IGN:
        return
    # SIG_DFL (or an unknown non-callable): restore the default and
    # re-raise the signal so the process terminates as it would have.
    _signal.signal(signum, _signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def make_observing_handler(journal: OperationalJournal, signal_name: ObservedSignal, previous) -> Callable:
    def handler(signum, frame):
        journal.observe_signal(signal_name)
        _redeliver(signum, previous, frame)

    return handler


def install_observing_signal_handlers(journal: OperationalJournal) -> Callable[[], None]:
    """Install SIGINT/SIGTERM observers (main thread only). Returns a
    callable that restores the previous handlers."""
    installed: dict[int, object] = {}
    for attr, name in _OBSERVABLE_SIGNALS:
        signum = getattr(_signal, attr)
        previous = _signal.getsignal(signum)
        _signal.signal(signum, make_observing_handler(journal, name, previous))
        installed[signum] = previous

    def restore() -> None:
        for signum, previous in installed.items():
            _signal.signal(signum, previous)

    return restore

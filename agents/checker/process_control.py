"""P5-D process-control library: frozen execution-control values, Linux
descendant termination and the session monitor with watchdog escalation
(ADR-0012 section 8, Amendment A4; dispatch
q77-p5d-repair-stage2c2-implement-a, Stage 2C-2).

Library only. Nothing in ``scripts/`` or any workflow imports this
module in Stage 2C-2: the monitor is never started against a real gate
session, no runner is terminated, and ``os._exit`` does not exist here.
Runner wiring and runner termination belong to Stage 2C-3.

Why descendant termination exists at all: on a cancellation-driven
timeout the pinned SDK's transport close raises at its first cancelled
checkpoint before it reaches ``terminate()``/``kill()``, so the bundled
CLI child survives. Cancelling the async task is therefore not enough;
the actual descendant processes must be signalled.

Process discovery is Linux ``/proc`` only, stdlib only: no shell, no
psutil, no ``killpg``, no ``waitpid``. Only descendants of the root are
ever signalled; the calling process, the root, the parent and every
ancestor are excluded. Every signal is preceded by a fresh
``(pid, starttime)`` identity check, so a reused PID is never signalled.
Descendants are rescanned throughout the TERM phase, immediately before
SIGKILL, throughout the KILL phase and in a final pass.

Residual limitation: a process that daemonizes or is reparented outside
the root's descendant tree before it is discovered cannot be caught by
descendant walking. The real GitHub kill rehearsal must exercise the
actual CLI process topology.

The monitor journals bounded non-content events only (``HEARTBEAT``,
``OBJECTIVE_CAUSE_LATCHED``, ``WATCHDOG_ESCALATED``). It reads no model
output, score, finding or disposition, writes nothing to stdout or
stderr, and fails closed: any internal fault establishes ``WATCHDOG``
when no cause is latched, preserves an existing cause otherwise, and
escalates exactly once.
"""

from __future__ import annotations

import math
import os
import signal as _signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from sentinel.phase5.execution_control import (
    ExecutionControlConfig,
    InvocationRegistry,
    SessionLatch,
    TerminalArbiter,
)
from sentinel.phase5.execution_envelope import SessionClock

# ---------------------------------------------------------------------------
# Frozen execution-control values (prospective; see the Stage-2C-2 plan)
# ---------------------------------------------------------------------------

CONTROL_CONFIG = ExecutionControlConfig(
    schema_version=1,
    heartbeat_interval_ms=30_000,
    watchdog_grace_ms=30_000,
    descendant_term_grace_ms=2_000,
    descendant_kill_wait_ms=2_000,
    monitor_tick_ms=1_000,
)
CONTROL_CONFIG_ID = CONTROL_CONFIG.control_config_id

# Rescan/poll cadence inside the bounded TERM and KILL phases.
POLL_INTERVAL_MS = 50
# The pinned SDK transport's own close bound: two fail_after(5) waits.
SDK_TRANSPORT_CLOSE_BOUND_MS = 10_000
# Longest feasible session: the 360-minute platform ceiling minus the
# 480 s finalization reserve.
MAX_SESSION_MS = 21_120_000

MAX_DESCENDANT_DEPTH = 32
_DEAD_STATES = frozenset({"Z", "X", "x"})


class ProcessControlError(RuntimeError):
    """A process-control operation was refused. Closed text only."""


class ProcessControlUnsupported(ProcessControlError):
    """Descendant termination is implemented for Linux only."""


class ProcessControlRootRefused(ProcessControlError):
    """The requested root is neither this process nor its descendant."""


# ---------------------------------------------------------------------------
# /proc parsing and discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessStat:
    pid: int
    ppid: int
    state: str
    starttime: int


def parse_proc_stat(text: str) -> ProcessStat:
    """Parse ``/proc/<pid>/stat``. The command name may contain spaces
    and parentheses, so the split is on the LAST ``)``. In the remainder
    the state is field 1, the ppid field 2 and the starttime field 20."""
    open_index = text.find("(")
    close_index = text.rfind(")")
    if open_index < 1 or close_index < open_index:
        raise ValueError("malformed stat line")
    pid = int(text[:open_index].strip())
    rest = text[close_index + 1:].split()
    if len(rest) < 20:
        raise ValueError("truncated stat line")
    return ProcessStat(pid=pid, ppid=int(rest[1]), state=rest[0], starttime=int(rest[19]))


def read_process_stat(pid: int, proc_root: Path = Path("/proc")) -> ProcessStat | None:
    """A fresh stat read, or None when the process is gone or unreadable."""
    try:
        return parse_proc_stat((Path(proc_root) / str(pid) / "stat").read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError, IndexError):
        return None


def read_process_table(proc_root: Path = Path("/proc")) -> dict[int, ProcessStat]:
    """Every readable process. Vanished, unreadable and unparseable
    entries are skipped."""
    table: dict[int, ProcessStat] = {}
    try:
        entries = list(os.scandir(proc_root))
    except OSError:
        return table
    for entry in entries:
        if not entry.name.isdigit():
            continue
        stat = read_process_stat(int(entry.name), proc_root)
        if stat is not None:
            table[stat.pid] = stat
    return table


def descendants_of(
    root_pid: int, table: Mapping[int, ProcessStat], *, max_depth: int = MAX_DESCENDANT_DEPTH
) -> tuple[tuple[ProcessStat, int], ...]:
    """Descendants of ``root_pid`` (never the root itself) with their
    depth, ordered deepest-first then by pid."""
    children: dict[int, list[ProcessStat]] = {}
    for stat in table.values():
        children.setdefault(stat.ppid, []).append(stat)
    found: list[tuple[ProcessStat, int]] = []
    seen = {root_pid}
    frontier = [root_pid]
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        next_frontier: list[int] = []
        for parent in frontier:
            for child in children.get(parent, ()):
                if child.pid in seen:
                    continue
                seen.add(child.pid)
                found.append((child, depth))
                next_frontier.append(child.pid)
        frontier = next_frontier
    found.sort(key=lambda item: (-item[1], item[0].pid))
    return tuple(found)


def ancestors_of(pid: int, table: Mapping[int, ProcessStat], *, limit: int = 256) -> frozenset[int]:
    result: set[int] = set()
    current = table.get(pid)
    while current is not None and len(result) < limit:
        parent = current.ppid
        if parent <= 0 or parent in result:
            break
        result.add(parent)
        current = table.get(parent)
    return frozenset(result)


# ---------------------------------------------------------------------------
# Bounded TERM/KILL with rescans
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TerminationReport:
    """Structural counts only: never a PID, command line or environment."""

    discovered: int
    late_discovered: int
    max_depth: int
    scans: int
    term_signalled: int
    kill_signalled: int
    terminated: int
    survivors: int
    elapsed_ms: int


def terminate_descendants(
    config: ExecutionControlConfig,
    *,
    root_pid: int | None = None,
    proc_root: Path = Path("/proc"),
    table_reader: Callable[[], Mapping[int, ProcessStat]] | None = None,
    stat_reader: Callable[[int], ProcessStat | None] | None = None,
    kill: Callable[[int, int], None] = os.kill,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    platform: str | None = None,
    self_pid: int | None = None,
    parent_pid: int | None = None,
) -> TerminationReport:
    """SIGTERM every live descendant of ``root_pid``, rescan during the
    bounded TERM phase, rescan immediately before SIGKILL, SIGKILL every
    live tracked identity, rescan during the bounded KILL phase, then a
    final rescan. Survivors are reported, never waited for beyond the
    configured bounds."""
    if not isinstance(config, ExecutionControlConfig):
        raise ProcessControlError("an ExecutionControlConfig is required")
    if not (platform if platform is not None else sys.platform).startswith("linux"):
        raise ProcessControlUnsupported("descendant termination requires Linux /proc")

    read_table = table_reader if table_reader is not None else (lambda: read_process_table(proc_root))
    read_stat = stat_reader if stat_reader is not None else (lambda pid: read_process_stat(pid, proc_root))
    me = self_pid if self_pid is not None else os.getpid()
    my_parent = parent_pid if parent_pid is not None else os.getppid()
    root = me if root_pid is None else root_pid
    term_signal = int(_signal.SIGTERM)
    kill_signal = int(getattr(_signal, "SIGKILL", 9))

    t0 = monotonic()
    if root != me:
        table = read_table()
        if root not in {stat.pid for stat, _depth in descendants_of(me, table)}:
            raise ProcessControlRootRefused("the root must be this process or one of its descendants")

    tracked: dict[tuple[int, int], int] = {}
    counters = {"scans": 0, "late": 0, "term": 0, "kill": 0, "max_depth": 0}

    def scan() -> list[tuple[int, int]]:
        counters["scans"] += 1
        table = read_table()
        excluded = {me, root, my_parent} | ancestors_of(me, table)
        new: list[tuple[int, int]] = []
        for stat, depth in descendants_of(root, table):
            if stat.pid in excluded or stat.state in _DEAD_STATES:
                continue
            identity = (stat.pid, stat.starttime)
            if identity not in tracked:
                tracked[identity] = depth
                counters["max_depth"] = max(counters["max_depth"], depth)
                new.append(identity)
        return new

    def live(identity: tuple[int, int]) -> bool:
        stat = read_stat(identity[0])
        return stat is not None and stat.starttime == identity[1] and stat.state not in _DEAD_STATES

    def send(identity: tuple[int, int], sig: int) -> bool:
        if not live(identity):
            return False
        try:
            kill(identity[0], sig)
        except OSError:
            return False
        return True

    def ordered() -> list[tuple[int, int]]:
        return sorted(tracked, key=lambda identity: (-tracked[identity], identity[0]))

    def any_live() -> bool:
        return any(live(identity) for identity in tracked)

    def pause(deadline: float) -> None:
        sleep(max(0.0, min(POLL_INTERVAL_MS / 1000.0, deadline - monotonic())))

    # 1. initial discovery and SIGTERM
    initial = scan()
    for identity in initial:
        if send(identity, term_signal):
            counters["term"] += 1

    # 2. bounded TERM phase with rescans
    term_deadline = t0 + config.descendant_term_grace_ms / 1000.0
    while monotonic() < term_deadline:
        pause(term_deadline)
        new = scan()
        counters["late"] += len(new)
        for identity in new:
            if send(identity, term_signal):
                counters["term"] += 1
        if not new and not any_live():
            break

    # 3. rescan immediately before SIGKILL
    counters["late"] += len(scan())

    # 4. SIGKILL every live tracked identity, deepest first
    for identity in ordered():
        if send(identity, kill_signal):
            counters["kill"] += 1

    # 5. bounded KILL phase with rescans
    if counters["kill"] or any_live():
        kill_deadline = monotonic() + config.descendant_kill_wait_ms / 1000.0
        while monotonic() < kill_deadline:
            pause(kill_deadline)
            new = scan()
            counters["late"] += len(new)
            for identity in new:
                if send(identity, kill_signal):
                    counters["kill"] += 1
            if not new and not any_live():
                break

    # 6. final rescan; survivors are counted, not waited for
    counters["late"] += len(scan())
    survivors = sum(1 for identity in tracked if live(identity))
    return TerminationReport(
        discovered=len(tracked),
        late_discovered=counters["late"],
        max_depth=counters["max_depth"],
        scans=counters["scans"],
        term_signalled=counters["term"],
        kill_signalled=counters["kill"],
        terminated=len(tracked) - survivors,
        survivors=survivors,
        elapsed_ms=max(0, int((monotonic() - t0) * 1000)),
    )


# ---------------------------------------------------------------------------
# Session monitor and watchdog escalation
# ---------------------------------------------------------------------------


class SessionMonitor:
    """Heartbeat, session-deadline trip, invocation-overrun detection and
    exactly-once watchdog escalation. ``tick`` is public and synchronous
    so every rule is testable without a thread; ``start`` drives it from
    a daemon thread. ``stop`` is the only normal-completion signal.

    Escalation is eligible once ANY objective cause has been latched for
    ``watchdog_grace_ms`` without ``stop``, whether or not an invocation
    is in flight, and whatever the terminal arbiter state: a committed
    quality record changes what evidence may be written (decided by the
    ``escalate`` callback), never whether a stuck runner is terminated.
    """

    def __init__(
        self,
        clock: SessionClock,
        latch: SessionLatch,
        registry: InvocationRegistry,
        arbiter: TerminalArbiter,
        config: ExecutionControlConfig,
        journal,
        *,
        terminate: Callable[[ExecutionControlConfig], TerminationReport] | None = None,
        escalate: Callable[[str, object], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(clock, SessionClock):
            raise ProcessControlError("a SessionMonitor requires a SessionClock")
        if not isinstance(latch, SessionLatch):
            raise ProcessControlError("a SessionMonitor requires a SessionLatch")
        if not isinstance(registry, InvocationRegistry) or registry.domain is not latch.domain:
            raise ProcessControlError("the monitor's registry and latch must share one ExecutionSafetyDomain")
        if not isinstance(arbiter, TerminalArbiter):
            raise ProcessControlError("a SessionMonitor requires a TerminalArbiter")
        if not isinstance(config, ExecutionControlConfig):
            raise ProcessControlError("a SessionMonitor requires an ExecutionControlConfig")
        self._clock = clock
        self._latch = latch
        self._registry = registry
        self._arbiter = arbiter
        self._config = config
        self._journal = journal
        self._terminate = terminate if terminate is not None else terminate_descendants
        self._escalate_callback = escalate
        self._fallback_monotonic = monotonic
        self._lock = latch.domain.lock
        self._next_heartbeat_at = clock.monotonic_now() + config.heartbeat_interval_ms / 1000.0
        self._escalated = False
        self._escalation_cause: str | None = None
        self._escalation_requested = False
        self._heartbeats = 0
        self._internal_faults = 0
        self._termination_faults = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # -- read-only state ---------------------------------------------------

    @property
    def escalated(self) -> bool:
        with self._lock:
            return self._escalated

    @property
    def escalation_cause(self) -> str | None:
        with self._lock:
            return self._escalation_cause

    @property
    def heartbeats(self) -> int:
        with self._lock:
            return self._heartbeats

    @property
    def internal_faults(self) -> int:
        with self._lock:
            return self._internal_faults

    @property
    def termination_faults(self) -> int:
        with self._lock:
            return self._termination_faults

    # -- control -----------------------------------------------------------

    def request_escalation(self) -> None:
        """Fail-closed request from the invocation guard (termination
        survivors or a termination fault). Processed on the next tick,
        or synchronously by ``stop``."""
        with self._lock:
            self._escalation_requested = True

    def tick(self, now: float | None = None) -> None:
        """One monitor step. Never raises."""
        try:
            self._tick(now)
        except Exception:  # noqa: BLE001 - an internal fault is an execution-control failure
            with self._lock:
                self._internal_faults += 1
            self._fail_closed()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="p5-session-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        with self._lock:
            pending = self._escalation_requested and not self._escalated
        if pending:
            self._fail_closed()

    # -- internals ---------------------------------------------------------

    def _run(self) -> None:
        interval = self._config.monitor_tick_ms / 1000.0
        while not self._stop_event.wait(interval):
            self.tick()

    def _tick(self, now: float | None) -> None:
        current = float(self._clock.monotonic_now()) if now is None else float(now)

        if current >= self._next_heartbeat_at:
            self._journal.append("HEARTBEAT")
            interval = self._config.heartbeat_interval_ms / 1000.0
            steps = math.floor((current - self._next_heartbeat_at) / interval) + 1
            with self._lock:
                self._heartbeats += 1
                self._next_heartbeat_at += steps * interval

        if self._clock.expired():
            if self._latch.trip("SESSION_DEADLINE", current):
                self._journal.append("OBJECTIVE_CAUSE_LATCHED", cause="SESSION_DEADLINE")

        if self._latch.cause is None:
            if self._registry.overrun(now_monotonic=current, grace_ms=self._config.watchdog_grace_ms):
                if self._latch.trip("WATCHDOG", current):
                    self._journal.append("OBJECTIVE_CAUSE_LATCHED", cause="WATCHDOG")
                self._escalate()
        else:
            tripped_at = self._latch.tripped_at
            if tripped_at is not None and current >= tripped_at + self._config.watchdog_grace_ms / 1000.0:
                self._escalate()

        with self._lock:
            requested = self._escalation_requested
        if requested:
            self._fail_closed()

    def _safe_now(self) -> float:
        try:
            return float(self._clock.monotonic_now())
        except Exception:  # noqa: BLE001 - fall back to the process monotonic clock
            return float(self._fallback_monotonic())

    def _fail_closed(self) -> None:
        """Establish WATCHDOG only when no cause is latched, preserve any
        earlier cause, then escalate exactly once."""
        with self._lock:
            self._escalation_requested = True
        try:
            if self._latch.cause is None:
                if self._latch.trip("WATCHDOG", self._safe_now()):
                    self._journal.append("OBJECTIVE_CAUSE_LATCHED", cause="WATCHDOG")
            self._escalate()
        except Exception:  # noqa: BLE001 - retried on the next tick
            with self._lock:
                self._internal_faults += 1

    def _escalate(self) -> None:
        with self._lock:
            if self._escalated:
                return
            self._escalated = True
            cause = self._latch.cause
        if cause is None:  # defensive: every caller latches first
            self._latch.trip("WATCHDOG", self._safe_now())
            cause = self._latch.cause
        with self._lock:
            self._escalation_cause = cause
        self._journal.append("WATCHDOG_ESCALATED", cause=cause)
        try:
            self._terminate(self._config)
        except Exception:  # noqa: BLE001 - bounded; counted, never re-entered
            with self._lock:
                self._termination_faults += 1
        if self._escalate_callback is not None:
            try:
                self._escalate_callback(cause, self._arbiter.state)
            except Exception:  # noqa: BLE001 - counted, never re-entered
                with self._lock:
                    self._termination_faults += 1

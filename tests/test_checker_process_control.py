"""Tests for agents/checker/process_control.py (ADR-0012 section 8; dispatch
q77-p5d-repair-stage2c2-implement-a, Stage 2C-2).

Model-free and provider-free. The TERM/KILL algorithm and the monitor are
exercised against deterministic fakes on every platform. The real
process-termination tests run only on Linux and are skipped by exactly
one mechanism (``sys.platform != "linux"``); a CI guard test fails if CI
is not Linux, so on exact-SHA CI they cannot be skipped.
"""

from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.checker import process_control as pc
from sentinel.phase5 import execution_control as ec
from sentinel.phase5 import execution_envelope as ee
from sentinel.phase5.journal import MAX_JOURNAL_EVENTS, OperationalJournal, read_journal

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
TERM = int(signal.SIGTERM)
KILL = int(getattr(signal, "SIGKILL", 9))
LINUX_ONLY = pytest.mark.skipif(sys.platform != "linux", reason="real /proc process termination is Linux-only")

STAGE2C2_TEST_MODULES = (
    REPO_ROOT / "tests" / "test_checker_process_control.py",
    REPO_ROOT / "tests" / "test_checker_envelope_guard.py",
    REPO_ROOT / "tests" / "test_phase5_runtime_identity.py",
)
STAGE2C2_SOURCE_MODULES = (
    REPO_ROOT / "agents" / "checker" / "process_control.py",
    REPO_ROOT / "agents" / "checker" / "envelope_guard.py",
    REPO_ROOT / "sentinel" / "phase5" / "runtime_identity.py",
)


# ======================================================================
# Frozen control configuration
# ======================================================================


def test_control_config_frozen_values_and_identity():
    config = pc.CONTROL_CONFIG
    assert config.model_dump() == {
        "schema_version": 1,
        "heartbeat_interval_ms": 30_000,
        "watchdog_grace_ms": 30_000,
        "descendant_term_grace_ms": 2_000,
        "descendant_kill_wait_ms": 2_000,
        "monitor_tick_ms": 1_000,
    }
    assert pc.CONTROL_CONFIG_ID == config.control_config_id
    assert pc.CONTROL_CONFIG_ID == "9c0d4c5a805b633ad17d413059e8d9da6b25cba68cb49b559719d87681a67962"
    assert all(info.is_required() for info in ec.ExecutionControlConfig.model_fields.values())
    with pytest.raises(ValidationError):
        config.watchdog_grace_ms = 1  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ec.ExecutionControlConfig(**config.model_dump(), extra=1)
    assert pc.POLL_INTERVAL_MS == 50
    assert pc.SDK_TRANSPORT_CLOSE_BOUND_MS == 10_000
    assert pc.MAX_SESSION_MS == 360 * 60 * 1000 - ee.FINALIZATION_RESERVE_S * 1000


def test_control_config_prospective_invariants():
    c = pc.CONTROL_CONFIG
    termination_bound = c.descendant_term_grace_ms + c.descendant_kill_wait_ms + 2 * pc.POLL_INTERVAL_MS
    assert c.heartbeat_interval_ms % c.monitor_tick_ms == 0
    assert c.monitor_tick_ms <= c.watchdog_grace_ms / 10
    assert termination_bound <= c.watchdog_grace_ms / 2
    assert c.watchdog_grace_ms >= 2 * (pc.SDK_TRANSPORT_CLOSE_BOUND_MS + termination_bound)
    assert c.watchdog_grace_ms + termination_bound <= ee.FINALIZATION_RESERVE_S * 1000 / 10
    assert pc.MAX_SESSION_MS / c.heartbeat_interval_ms <= MAX_JOURNAL_EVENTS / 10


# ======================================================================
# /proc parsing and discovery (pure)
# ======================================================================


def _stat_line(pid: int, comm: str, state: str, ppid: int, starttime: int) -> str:
    middle = " ".join(["0"] * 17)
    tail = " ".join(["0"] * 30)
    return f"{pid} ({comm}) {state} {ppid} {middle} {starttime} {tail}\n"


def test_parse_proc_stat_handles_spaces_and_parentheses_in_comm():
    parsed = pc.parse_proc_stat(_stat_line(4242, "we (ird) name)", "S", 17, 987654))
    assert parsed == pc.ProcessStat(pid=4242, ppid=17, state="S", starttime=987654)
    with pytest.raises(ValueError):
        pc.parse_proc_stat("garbage")
    with pytest.raises(ValueError):
        pc.parse_proc_stat("12 (x) S 1 2 3")


def test_read_process_table_skips_vanished_and_malformed_entries(tmp_path):
    for pid, text in ((10, _stat_line(10, "a", "S", 1, 5)), (11, "not a stat line"), (12, None)):
        d = tmp_path / str(pid)
        d.mkdir()
        if text is not None:
            (d / "stat").write_text(text, encoding="utf-8")
    (tmp_path / "self").mkdir()
    table = pc.read_process_table(tmp_path)
    assert set(table) == {10}
    assert pc.read_process_stat(12, tmp_path) is None
    assert pc.read_process_stat(999, tmp_path) is None


def _table(*rows):
    return {pid: pc.ProcessStat(pid=pid, ppid=ppid, state=state, starttime=start) for pid, ppid, state, start in rows}


def test_descendants_are_deepest_first_and_exclude_unrelated_and_root():
    table = _table((1000, 999, "S", 1), (1100, 1000, "S", 2), (1200, 1000, "S", 3),
                   (1110, 1100, "S", 4), (1111, 1110, "S", 5), (2000, 1, "S", 6))
    found = pc.descendants_of(1000, table)
    assert [(stat.pid, depth) for stat, depth in found] == [(1111, 3), (1110, 2), (1100, 1), (1200, 1)]
    assert pc.ancestors_of(1111, table) == frozenset({1110, 1100, 1000, 999})
    cyclic = _table((1, 2, "S", 1), (2, 1, "S", 1))
    assert {s.pid for s, _ in pc.descendants_of(1, cyclic)} == {2}


# ======================================================================
# TERM/KILL algorithm against a deterministic fake process world
# ======================================================================

SELF, PARENT = 1000, 999


class FakeWorld:
    """A fake /proc with a fake clock. Signals mutate processes; hooks
    can spawn descendants to model late creation."""

    def __init__(self):
        self.now = 0.0
        self.procs: dict[int, dict] = {}
        self.signals: list[tuple[int, int]] = []
        self.table_reads = 0
        self.on_monotonic = None
        self.add(1, 0)
        self.add(PARENT, 1)
        self.add(SELF, PARENT)

    def add(self, pid, ppid, *, start=None, state="S", ignore_term=False, ignore_kill=False,
            on_term=None, on_kill=None):
        self.procs[pid] = dict(ppid=ppid, start=pid * 10 if start is None else start, state=state,
                               ignore_term=ignore_term, ignore_kill=ignore_kill, on_term=on_term, on_kill=on_kill)

    def table(self):
        self.table_reads += 1
        return {pid: pc.ProcessStat(pid, p["ppid"], p["state"], p["start"]) for pid, p in self.procs.items()}

    def stat(self, pid):
        p = self.procs.get(pid)
        return None if p is None else pc.ProcessStat(pid, p["ppid"], p["state"], p["start"])

    def kill(self, pid, sig):
        self.signals.append((pid, sig))
        p = self.procs[pid]
        if sig == TERM:
            if p["on_term"]:
                p["on_term"](self)
            if not p["ignore_term"]:
                p["state"] = "Z"
        elif sig == KILL:
            if p["on_kill"]:
                p["on_kill"](self)
            if not p["ignore_kill"]:
                p["state"] = "Z"

    def sleep(self, seconds):
        self.now = round(self.now + seconds, 6)

    def monotonic(self):
        if self.on_monotonic is not None:
            self.on_monotonic(self)
        return self.now

    def run(self, **overrides):
        kwargs = dict(table_reader=self.table, stat_reader=self.stat, kill=self.kill, sleep=self.sleep,
                      monotonic=self.monotonic, platform="linux", self_pid=SELF, parent_pid=PARENT)
        kwargs.update(overrides)
        return pc.terminate_descendants(pc.CONTROL_CONFIG, **kwargs)

    def sent(self, pid):
        return [sig for p, sig in self.signals if p == pid]


def test_cooperative_child_gets_term_only():
    world = FakeWorld()
    world.add(1100, SELF)
    report = world.run()
    assert world.sent(1100) == [TERM]
    assert (report.discovered, report.term_signalled, report.kill_signalled, report.survivors) == (1, 1, 0, 0)
    assert report.terminated == 1 and report.late_discovered == 0


def test_term_ignoring_child_is_killed():
    world = FakeWorld()
    world.add(1100, SELF, ignore_term=True)
    report = world.run()
    assert world.sent(1100) == [TERM, KILL]
    assert report.kill_signalled == 1 and report.survivors == 0
    assert report.elapsed_ms <= pc.CONTROL_CONFIG.descendant_term_grace_ms + pc.CONTROL_CONFIG.descendant_kill_wait_ms + 2 * pc.POLL_INTERVAL_MS


def test_kill_order_is_deepest_first_and_unrelated_self_parent_never_signalled():
    world = FakeWorld()
    world.add(1100, SELF, ignore_term=True)
    world.add(1110, 1100, ignore_term=True)
    world.add(2000, 1, ignore_term=True)
    report = world.run()
    kills = [pid for pid, sig in world.signals if sig == KILL]
    assert kills == [1110, 1100]
    assert report.max_depth == 2 and report.survivors == 0
    for pid in (1, PARENT, SELF, 2000):
        assert world.sent(pid) == []


def test_late_descendant_during_term_phase_is_rescanned_termed_and_killed():
    world = FakeWorld()
    world.add(1100, SELF, ignore_term=True,
              on_term=lambda w: w.add(1110, 1100, ignore_term=True) if 1110 not in w.procs else None)
    report = world.run()
    assert world.sent(1110) == [TERM, KILL]
    assert world.sent(1100) == [TERM, KILL]
    assert report.late_discovered == 1 and report.discovered == 2 and report.survivors == 0


def test_late_descendant_during_kill_phase_is_killed_without_term():
    world = FakeWorld()
    world.add(1100, SELF, ignore_term=True,
              on_kill=lambda w: w.add(1110, 1100, ignore_term=True) if 1110 not in w.procs else None)
    report = world.run()
    assert world.sent(1110) == [KILL]
    assert report.late_discovered == 1 and report.survivors == 0


def test_descendant_appearing_only_at_final_rescan_is_a_survivor():
    world = FakeWorld()
    world.add(1100, SELF, ignore_term=True, ignore_kill=True)  # unkillable: the phases run to their deadlines
    kill_deadline = (pc.CONTROL_CONFIG.descendant_term_grace_ms + pc.CONTROL_CONFIG.descendant_kill_wait_ms) / 1000.0

    def spawn_at_kill_deadline(w):
        if w.now >= kill_deadline and 1110 not in w.procs:
            w.add(1110, 1100)

    world.on_monotonic = spawn_at_kill_deadline
    report = world.run()
    assert world.sent(1110) == []  # found only by the final pass
    assert report.survivors == 2 and report.discovered == 2 and report.terminated == 0
    assert report.elapsed_ms == pc.CONTROL_CONFIG.descendant_term_grace_ms + pc.CONTROL_CONFIG.descendant_kill_wait_ms


def test_pid_reuse_is_never_signalled():
    world = FakeWorld()

    def reuse(w):
        w.procs[1100] = dict(ppid=1, start=99999, state="S", ignore_term=True, ignore_kill=True, on_term=None, on_kill=None)

    world.add(1100, SELF, ignore_term=True, on_term=reuse)
    report = world.run()
    assert world.sent(1100) == [TERM]  # the reused PID never receives SIGKILL
    assert report.survivors == 0 and report.kill_signalled == 0


def test_disappearing_pid_and_zombies_are_tolerated():
    world = FakeWorld()
    world.add(1100, SELF, ignore_term=True, on_term=lambda w: w.procs.pop(1100))
    world.add(1200, SELF, state="Z")
    report = world.run()
    assert world.sent(1200) == []
    assert report.discovered == 1 and report.survivors == 0 and report.kill_signalled == 0


def test_cyclic_parent_is_excluded_as_an_ancestor():
    world = FakeWorld()
    world.procs[PARENT]["ppid"] = SELF  # pathological cycle: the parent appears under self
    world.add(1100, SELF)
    world.run()
    assert world.sent(PARENT) == [] and world.sent(1) == []


def test_unsupported_platform_raises_before_any_io():
    def forbidden(*_a, **_k):
        raise AssertionError("no I/O may happen on an unsupported platform")

    with pytest.raises(pc.ProcessControlUnsupported):
        pc.terminate_descendants(pc.CONTROL_CONFIG, platform="win32", table_reader=forbidden,
                                 stat_reader=forbidden, kill=forbidden)
    with pytest.raises(pc.ProcessControlError):
        pc.terminate_descendants(object(), platform="linux")  # type: ignore[arg-type]


def test_root_that_is_not_a_descendant_is_refused():
    world = FakeWorld()
    world.add(2000, 1)
    with pytest.raises(pc.ProcessControlRootRefused):
        world.run(root_pid=2000)
    with pytest.raises(pc.ProcessControlRootRefused):
        world.run(root_pid=PARENT)
    assert world.signals == []
    world.add(1100, SELF)
    world.add(1110, 1100)
    report = world.run(root_pid=1100)
    assert world.sent(1100) == [] and world.sent(1110) == [TERM] and report.discovered == 1


def test_empty_tree_finishes_quickly():
    world = FakeWorld()
    report = world.run()
    assert report.discovered == 0 and report.survivors == 0 and world.signals == []
    assert report.elapsed_ms <= 2 * pc.POLL_INTERVAL_MS


# ======================================================================
# Real Linux process termination
# ======================================================================

_HELPER = r'''
import os, signal, subprocess, sys, time
role, workdir = sys.argv[1], sys.argv[2]
HERE = os.path.abspath(__file__)

def mark(name):
    tmp = os.path.join(workdir, name + ".tmp")
    with open(tmp, "w") as handle:
        handle.write(str(os.getpid()))
    os.replace(tmp, os.path.join(workdir, name))

def spawn(child_role):
    return subprocess.Popen([sys.executable, HERE, child_role, workdir],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def idle():
    while True:
        time.sleep(0.05)

if role.startswith("root-"):
    spawn("child-" + role[len("root-"):])
    mark("root")
    idle()
elif role == "child-cooperative":
    mark("child")
    idle()
elif role == "child-ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    mark("child")
    idle()
elif role == "child-tree":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    spawn("grandchild-ignore")
    mark("child")
    idle()
elif role == "child-zombie":
    zombie = spawn("grandchild-exit")
    while not os.path.exists(os.path.join(workdir, "gone")):
        time.sleep(0.02)
    mark("child")
    idle()
elif role == "child-late":
    spawned = []
    def on_term(signum, frame):
        if not spawned:
            spawned.append(spawn("grandchild-ignore"))
    signal.signal(signal.SIGTERM, on_term)
    mark("child")
    idle()
elif role == "grandchild-ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    mark("grandchild")
    idle()
elif role == "grandchild-exit":
    mark("gone")
    sys.exit(0)
'''


def _wait_for(path: Path, timeout_s: float = 20.0) -> int:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            return int(path.read_text(encoding="utf-8"))
        time.sleep(0.02)
    raise AssertionError(f"{path.name} was not marked in time")


def _live_descendants(root_pid: int) -> list[pc.ProcessStat]:
    return [s for s, _ in pc.descendants_of(root_pid, pc.read_process_table()) if s.state not in ("Z", "X", "x")]


@pytest.fixture
def process_tree(tmp_path):
    helper = tmp_path / "helper.py"
    helper.write_text(_HELPER, encoding="utf-8")
    started: list[subprocess.Popen] = []

    def _start(mode: str, *marks: str):
        workdir = tmp_path / mode
        workdir.mkdir()
        proc = subprocess.Popen([sys.executable, str(helper), f"root-{mode}", str(workdir)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started.append(proc)
        _wait_for(workdir / "root")
        for name in ("child", *marks):
            _wait_for(workdir / name)
        return proc, workdir

    yield _start
    for proc in started:
        if proc.poll() is None:
            try:
                pc.terminate_descendants(pc.CONTROL_CONFIG, root_pid=proc.pid)
            except pc.ProcessControlError:
                pass
            proc.kill()
        proc.wait(timeout=10)


def _bound_ms() -> int:
    c = pc.CONTROL_CONFIG
    return c.descendant_term_grace_ms + c.descendant_kill_wait_ms + 2 * pc.POLL_INTERVAL_MS + 1500


@LINUX_ONLY
def test_linux_cooperative_child_exits_on_term(process_tree):
    root, _ = process_tree("cooperative")
    report = pc.terminate_descendants(pc.CONTROL_CONFIG, root_pid=root.pid)
    assert report.discovered == 1 and report.term_signalled == 1 and report.kill_signalled == 0
    assert report.survivors == 0 and _live_descendants(root.pid) == []
    assert root.poll() is None and report.elapsed_ms <= _bound_ms()


@LINUX_ONLY
def test_linux_term_ignoring_child_is_killed(process_tree):
    root, _ = process_tree("ignore")
    report = pc.terminate_descendants(pc.CONTROL_CONFIG, root_pid=root.pid)
    assert report.kill_signalled == 1 and report.survivors == 0
    assert _live_descendants(root.pid) == [] and root.poll() is None
    assert report.elapsed_ms <= _bound_ms()


@LINUX_ONLY
def test_linux_grandchild_is_discovered_and_killed(process_tree):
    root, _ = process_tree("tree", "grandchild")
    report = pc.terminate_descendants(pc.CONTROL_CONFIG, root_pid=root.pid)
    assert report.discovered == 2 and report.max_depth == 2
    assert report.kill_signalled == 2 and report.survivors == 0
    assert _live_descendants(root.pid) == [] and root.poll() is None


@LINUX_ONLY
def test_linux_zombie_is_not_signalled_and_counts_as_terminated(process_tree):
    root, workdir = process_tree("zombie")
    time.sleep(0.2)
    zombies = [s for s, _ in pc.descendants_of(root.pid, pc.read_process_table()) if s.state == "Z"]
    assert zombies, "the helper's grandchild should be an unreaped zombie"
    report = pc.terminate_descendants(pc.CONTROL_CONFIG, root_pid=root.pid)
    assert report.discovered == 1 and report.survivors == 0
    assert (workdir / "gone").exists()


@LINUX_ONLY
def test_linux_late_spawned_descendant_is_found_by_rescan(process_tree):
    root, _ = process_tree("late")
    report = pc.terminate_descendants(pc.CONTROL_CONFIG, root_pid=root.pid)
    assert report.late_discovered >= 1
    assert report.discovered >= 2 and report.kill_signalled >= 2
    assert report.survivors == 0 and _live_descendants(root.pid) == []
    assert root.poll() is None and report.elapsed_ms <= _bound_ms()


@LINUX_ONLY
def test_linux_unrelated_sibling_and_test_process_survive(process_tree, tmp_path):
    root, _ = process_tree("ignore")
    sibling_dir = tmp_path / "sibling"
    sibling_dir.mkdir()
    sibling = subprocess.Popen([sys.executable, str(tmp_path / "helper.py"), "child-cooperative", str(sibling_dir)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_for(sibling_dir / "child")
        report = pc.terminate_descendants(pc.CONTROL_CONFIG, root_pid=root.pid)
        assert report.survivors == 0
        assert sibling.poll() is None and root.poll() is None
        assert pc.read_process_stat(os.getpid()) is not None
    finally:
        sibling.kill()
        sibling.wait(timeout=10)


@LINUX_ONLY
def test_linux_ancestor_root_is_refused():
    with pytest.raises(pc.ProcessControlRootRefused):
        pc.terminate_descendants(pc.CONTROL_CONFIG, root_pid=os.getppid())
    table = pc.read_process_table()
    me = table[os.getpid()]
    assert me.starttime > 0 and me.ppid == os.getppid()


def test_stage2c2_linux_tests_cannot_be_skipped_in_ci():
    """On GitHub Actions the Stage-2C-2 Linux-only tests must run: CI that
    is not Linux fails here instead of silently skipping them."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        assert sys.platform == "linux"


def test_stage2c2_test_modules_use_only_the_linux_platform_skip():
    for module in STAGE2C2_TEST_MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                dotted = ast.unparse(node)
                assert dotted not in (
                    "pytest.skip", "pytest.importorskip", "pytest.xfail", "pytest.mark.skip", "pytest.mark.xfail",
                ), f"{module.name}: forbidden skip mechanism {dotted}"
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "pytest.mark.skipif":
                assert node.args and ast.unparse(node.args[0]) == "sys.platform != 'linux'", module.name


# ======================================================================
# Session monitor
# ======================================================================


class _Mono:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _report(survivors: int = 0) -> pc.TerminationReport:
    return pc.TerminationReport(0, 0, 0, 1, 0, 0, 0, survivors, 0)


class _World:
    def __init__(self, tmp_path, *, remaining_s=3_600, config=pc.CONTROL_CONFIG, registry_cls=ec.InvocationRegistry,
                 clock_cls=ee.SessionClock, terminate=None, escalate="record", fallback=None):
        self.mono = _Mono()
        self.domain = ec.ExecutionSafetyDomain()
        self.latch = ec.SessionLatch(self.domain)
        self.registry = registry_cls(self.domain)
        self.clock = clock_cls(job_started_at_utc=NOW, resolved_at_utc=NOW, monotonic_at_resolve=self.mono.value,
                               session_duration_s=remaining_s, monotonic=self.mono)
        self.arbiter = ec.TerminalArbiter(self.domain, self.latch, self.clock)
        self.path = tmp_path / "journal.jsonl"
        self.journal = OperationalJournal(self.path, writer="RUNNER", monotonic=self.mono).open()
        self.terminations: list = []
        self.escalations: list = []

        def _terminate(cfg):
            self.terminations.append(cfg)
            return _report()

        kwargs = dict(terminate=terminate or _terminate)
        if escalate == "record":
            kwargs["escalate"] = lambda cause, state: self.escalations.append((cause, state))
        if fallback is not None:
            kwargs["monotonic"] = fallback
        self.monitor = pc.SessionMonitor(self.clock, self.latch, self.registry, self.arbiter, config, self.journal, **kwargs)

    def events(self, name=None):
        events = read_journal(self.path).events
        return [e for e in events if name is None or e.event == name]


def test_monitor_heartbeat_cadence_without_burst_and_without_fields(tmp_path):
    w = _World(tmp_path)
    t0 = w.mono.value
    w.monitor.tick(now=t0 + 29.999)
    assert w.events("HEARTBEAT") == []
    w.monitor.tick(now=t0 + 30)
    w.monitor.tick(now=t0 + 95)  # a late tick produces ONE heartbeat, never a burst
    w.monitor.tick(now=t0 + 119.9)
    assert len(w.events("HEARTBEAT")) == 2
    w.monitor.tick(now=t0 + 120)
    beats = w.events("HEARTBEAT")
    assert len(beats) == 3 and w.monitor.heartbeats == 3
    for beat in beats:
        assert all(getattr(beat, f) is None for f in ("cause", "run_ordinal", "invocation_ordinal", "signal", "sha256"))
    assert not w.monitor.escalated and w.terminations == []


def test_monitor_session_deadline_trips_exactly_once(tmp_path):
    w = _World(tmp_path, remaining_s=60)
    w.mono.value += 60
    for _ in range(5):
        w.monitor.tick()
    assert w.latch.cause == "SESSION_DEADLINE"
    assert [e.cause for e in w.events("OBJECTIVE_CAUSE_LATCHED")] == ["SESSION_DEADLINE"]


def test_monitor_does_not_duplicate_a_cause_a_guard_already_latched(tmp_path):
    w = _World(tmp_path, remaining_s=60)
    w.mono.value += 60
    assert w.latch.trip("SESSION_DEADLINE", w.mono.value)
    w.monitor.tick()
    assert w.events("OBJECTIVE_CAUSE_LATCHED") == []


def test_session_deadline_between_invocations_escalates_after_grace(tmp_path):
    w = _World(tmp_path, remaining_s=60)
    w.registry.start(run_ordinal=1, invocation_ordinal=1, budget_ms=600_000, at_monotonic=w.mono.value)
    w.registry.finish(run_ordinal=1, invocation_ordinal=1, outcome="RETURNED", at_monotonic=w.mono.value + 5)
    w.mono.value += 60
    w.monitor.tick()
    tripped = w.latch.tripped_at
    assert w.latch.cause == "SESSION_DEADLINE" and w.registry.in_flight() == ()
    w.monitor.tick(now=tripped + 29.999)
    assert not w.monitor.escalated and w.terminations == []
    w.monitor.tick(now=tripped + 30)
    assert w.monitor.escalated and w.monitor.escalation_cause == "SESSION_DEADLINE"
    assert [e.cause for e in w.events("WATCHDOG_ESCALATED")] == ["SESSION_DEADLINE"]
    assert len(w.terminations) == 1 and w.terminations[0] is pc.CONTROL_CONFIG


def test_session_deadline_during_post_processing_escalates_after_grace(tmp_path):
    w = _World(tmp_path, remaining_s=120)
    for n in range(1, 4):
        w.registry.start(run_ordinal=1, invocation_ordinal=n, budget_ms=600_000, at_monotonic=w.mono.value)
        w.registry.finish(run_ordinal=1, invocation_ordinal=n, outcome="RETURNED", at_monotonic=w.mono.value + 1)
    w.mono.value += 30  # "scoring" in progress, nothing in flight
    w.monitor.tick()
    assert w.latch.cause is None
    w.mono.value += 90
    w.monitor.tick()
    w.mono.value += 30
    w.monitor.tick()
    assert w.monitor.escalated and w.escalations == [("SESSION_DEADLINE", ec.TerminalCommitState.NONE)]


def test_latched_cause_escalates_without_any_invocation_record(tmp_path):
    w = _World(tmp_path)
    assert w.registry.records() == ()
    w.latch.trip("INVOCATION_STALL_DEADLINE", w.mono.value)
    w.monitor.tick(now=w.mono.value + 29.5)
    assert not w.monitor.escalated
    w.monitor.tick(now=w.mono.value + 30)
    assert w.monitor.escalation_cause == "INVOCATION_STALL_DEADLINE" and len(w.terminations) == 1


def test_stop_before_grace_does_not_escalate(tmp_path):
    w = _World(tmp_path)
    w.latch.trip("SESSION_DEADLINE", w.mono.value)
    w.monitor.tick(now=w.mono.value + 1)
    w.monitor.stop()
    assert not w.monitor.escalated and w.terminations == []


def test_quality_committed_keeps_process_backstop_but_forbids_competing_invalid(tmp_path):
    w = _World(tmp_path, remaining_s=60)
    w.arbiter.commit_quality(lambda: None)
    assert w.arbiter.state is ec.TerminalCommitState.QUALITY_COMMITTED
    w.mono.value += 60  # the runner is stuck after committing quality
    w.monitor.tick()
    w.monitor.tick(now=w.latch.tripped_at + 30)
    assert w.monitor.escalated and len(w.terminations) == 1
    assert w.escalations == [("SESSION_DEADLINE", ec.TerminalCommitState.QUALITY_COMMITTED)]
    with pytest.raises(ec.InvalidRefused):
        w.arbiter.commit_invalid("SESSION_DEADLINE", lambda: None)


def test_invocation_overrun_with_clear_latch_trips_watchdog_once(tmp_path):
    w = _World(tmp_path)
    w.registry.start(run_ordinal=1, invocation_ordinal=1, budget_ms=1_000, at_monotonic=w.mono.value)
    w.monitor.tick(now=w.mono.value + 1 + 29.999)
    assert w.latch.cause is None and not w.monitor.escalated
    w.monitor.tick(now=w.mono.value + 1 + 30)
    w.monitor.tick(now=w.mono.value + 1 + 31)
    assert w.latch.cause == "WATCHDOG"
    assert len(w.events("OBJECTIVE_CAUSE_LATCHED")) == 1 and len(w.events("WATCHDOG_ESCALATED")) == 1
    assert len(w.terminations) == 1


def test_earlier_cause_is_never_replaced_by_watchdog(tmp_path):
    w = _World(tmp_path)
    w.registry.start(run_ordinal=1, invocation_ordinal=1, budget_ms=1_000, at_monotonic=w.mono.value)
    tripped = w.mono.value + 40
    w.latch.trip("SESSION_DEADLINE", tripped)
    w.monitor.tick(now=tripped + 1)  # overrun exists, but a cause is latched
    assert w.latch.cause == "SESSION_DEADLINE" and not w.monitor.escalated
    w.monitor.tick(now=tripped + 30)
    assert w.monitor.escalation_cause == "SESSION_DEADLINE"
    assert [e.cause for e in w.events("WATCHDOG_ESCALATED")] == ["SESSION_DEADLINE"]


def test_healthy_session_never_escalates(tmp_path):
    w = _World(tmp_path)
    for n in range(1, 11):
        start = w.mono.value + n * 50
        w.registry.start(run_ordinal=1, invocation_ordinal=n, budget_ms=600_000, at_monotonic=start)
        w.registry.finish(run_ordinal=1, invocation_ordinal=n, outcome="RETURNED", at_monotonic=start + 40)
        w.monitor.tick(now=start + 45)
    assert w.latch.cause is None and not w.monitor.escalated and w.terminations == []
    assert w.events("OBJECTIVE_CAUSE_LATCHED") == [] and w.events("WATCHDOG_ESCALATED") == []


def test_escalation_without_callback_journals_and_terminates_only(tmp_path):
    w = _World(tmp_path, escalate=None)
    w.latch.trip("WATCHDOG", w.mono.value)
    w.monitor.tick(now=w.mono.value + 30)
    assert w.monitor.escalated and len(w.terminations) == 1 and w.escalations == []


class _FaultyRegistry(ec.InvocationRegistry):
    def overrun(self, *, now_monotonic, grace_ms):
        raise RuntimeError("internal fault detail that must never be journaled")


class _FaultyClock(ee.SessionClock):
    fail_expired = False
    fail_now = False

    def expired(self) -> bool:
        if self.fail_expired:
            raise RuntimeError("internal fault detail that must never be journaled")
        return super().expired()

    def monotonic_now(self) -> float:
        if self.fail_now:
            raise RuntimeError("internal fault detail that must never be journaled")
        return super().monotonic_now()


def test_internal_fault_with_clear_latch_establishes_watchdog_and_escalates_once(tmp_path):
    w = _World(tmp_path, registry_cls=_FaultyRegistry)
    w.monitor.tick()
    assert w.latch.cause == "WATCHDOG" and w.monitor.internal_faults == 1
    assert [e.cause for e in w.events("OBJECTIVE_CAUSE_LATCHED")] == ["WATCHDOG"]
    assert [e.cause for e in w.events("WATCHDOG_ESCALATED")] == ["WATCHDOG"]
    assert len(w.terminations) == 1
    assert b"internal fault detail" not in w.path.read_bytes()


def test_internal_fault_with_existing_cause_preserves_it_and_escalates_once(tmp_path):
    w = _World(tmp_path, clock_cls=_FaultyClock)
    w.latch.trip("SESSION_DEADLINE", w.mono.value)
    w.clock.fail_expired = True
    w.monitor.tick()
    assert w.latch.cause == "SESSION_DEADLINE"
    assert w.events("OBJECTIVE_CAUSE_LATCHED") == []
    assert [e.cause for e in w.events("WATCHDOG_ESCALATED")] == ["SESSION_DEADLINE"]
    assert w.monitor.escalation_cause == "SESSION_DEADLINE" and len(w.terminations) == 1


def test_repeated_faulting_ticks_never_duplicate_events(tmp_path):
    w = _World(tmp_path, clock_cls=_FaultyClock)
    w.clock.fail_expired = True  # faults on every tick, before and after the latch
    for _ in range(100):
        w.monitor.tick()
    assert w.monitor.internal_faults == 100
    assert len(w.events("OBJECTIVE_CAUSE_LATCHED")) == 1 and len(w.events("WATCHDOG_ESCALATED")) == 1
    assert len(w.terminations) == 1 and len(w.escalations) == 1


def test_termination_fault_during_escalation_is_counted_and_never_reentered(tmp_path):
    calls = []

    def failing(cfg):
        calls.append(cfg)
        raise OSError("process-control failure")

    w = _World(tmp_path, terminate=failing)
    w.latch.trip("WATCHDOG", w.mono.value)
    for offset in (30, 31, 60):
        w.monitor.tick(now=w.mono.value + offset)
    assert len(calls) == 1 and w.monitor.termination_faults == 1
    assert len(w.events("WATCHDOG_ESCALATED")) == 1 and len(w.escalations) == 1


def test_faulting_clock_uses_fallback_time_and_fails_closed(tmp_path):
    w = _World(tmp_path, clock_cls=_FaultyClock, fallback=lambda: 5555.0)
    w.clock.fail_now = True
    w.monitor.tick()
    assert w.latch.cause == "WATCHDOG" and w.latch.tripped_at == 5555.0
    assert w.monitor.escalated and len(w.events("WATCHDOG_ESCALATED")) == 1


def test_request_escalation_is_processed_on_next_tick_once(tmp_path):
    w = _World(tmp_path)
    w.monitor.request_escalation()
    w.monitor.tick(now=w.mono.value + 1)
    w.monitor.tick(now=w.mono.value + 2)
    assert w.latch.cause == "WATCHDOG" and len(w.events("WATCHDOG_ESCALATED")) == 1 and len(w.terminations) == 1


def test_request_escalation_then_stop_escalates_synchronously(tmp_path):
    w = _World(tmp_path)
    w.latch.trip("INVOCATION_STALL_DEADLINE", w.mono.value)
    w.monitor.request_escalation()
    w.monitor.stop()
    assert w.monitor.escalation_cause == "INVOCATION_STALL_DEADLINE" and len(w.terminations) == 1


def test_monitor_real_thread_cadence(tmp_path):
    domain = ec.ExecutionSafetyDomain()
    latch = ec.SessionLatch(domain)
    registry = ec.InvocationRegistry(domain)
    real_now = time.monotonic()
    clock = ee.SessionClock(job_started_at_utc=NOW, resolved_at_utc=NOW, monotonic_at_resolve=real_now,
                            session_duration_s=3_600)
    arbiter = ec.TerminalArbiter(domain, latch, clock)
    config = ec.ExecutionControlConfig(schema_version=1, heartbeat_interval_ms=20, watchdog_grace_ms=30_000,
                                       descendant_term_grace_ms=2_000, descendant_kill_wait_ms=2_000, monitor_tick_ms=10)
    journal = OperationalJournal(tmp_path / "j.jsonl", writer="RUNNER").open()
    monitor = pc.SessionMonitor(clock, latch, registry, arbiter, config, journal, terminate=lambda cfg: _report())
    monitor.start()
    time.sleep(0.25)
    monitor.stop()
    journal.close()
    assert monitor.heartbeats >= 3 and not monitor.escalated and latch.cause is None
    assert not any(t.name == "p5-session-monitor" and t.is_alive() for t in threading.enumerate())


def test_monitor_requires_shared_domain_and_typed_inputs(tmp_path):
    w = _World(tmp_path)
    other = ec.InvocationRegistry(ec.ExecutionSafetyDomain())
    with pytest.raises(pc.ProcessControlError):
        pc.SessionMonitor(w.clock, w.latch, other, w.arbiter, pc.CONTROL_CONFIG, w.journal)
    with pytest.raises(pc.ProcessControlError):
        pc.SessionMonitor(w.clock, w.latch, w.registry, w.arbiter, object(), w.journal)  # type: ignore[arg-type]


# ======================================================================
# Boundaries
# ======================================================================


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_new_modules_import_boundaries():
    process_control, guard, identity = STAGE2C2_SOURCE_MODULES
    assert not _imports(process_control) & {"anyio", "claude_agent_sdk", "subprocess", "psutil", "asyncio"}
    assert not _imports(guard) & {"claude_agent_sdk", "subprocess", "psutil"}
    assert not _imports(identity) & {"claude_agent_sdk", "anyio", "subprocess", "psutil"}


def test_new_modules_have_no_operator_output_exit_or_group_kill():
    for path in STAGE2C2_SOURCE_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert "logging" not in _imports(path), path.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", path.name
            if isinstance(node, ast.Attribute):
                assert node.attr not in ("_exit", "killpg", "stdout", "stderr"), f"{path.name}: {node.attr}"


def test_nothing_outside_tests_imports_the_new_modules():
    """Stage 2C-2's original invariant was absolute: no script or
    workflow anywhere referenced process_control/envelope_guard/
    runtime_identity. Stage 2C-3 (dispatch
    q77-p5d-repair-stage2c3-implement-b, owner-authorized narrowing) is
    specifically authorized to wire process_control and envelope_guard
    into exactly one runner path, scripts/run_phase5_official_gate.py;
    every other script/workflow file, and runtime_identity everywhere
    (including that one runner), remain forbidden -- runtime-identity
    readiness binding is still deferred past this stage.

    Stage 2C-B1 (dispatch q77-p5d-repair-stage2cb1-implement-b, owner
    ruling q77-p5d-stage2cb1-finalizer-ruling-a) narrows this once
    more, for exactly one additional path: the model-free kill-rehearsal
    driver scripts/run_phase5_kill_rehearsal.py may reference
    process_control (it exercises descendant termination against its own
    synthetic probe tree) and runtime_identity (it captures the Linux
    runtime identity as rehearsal evidence only, bound to no readiness
    claim). envelope_guard stays forbidden there, and runtime_identity
    stays forbidden in the official gate runner. Per-path allowances
    only -- never a blanket allowance."""
    all_tokens = ("process_control", "envelope_guard", "runtime_identity")
    permitted_by_path = {
        REPO_ROOT / "scripts" / "run_phase5_official_gate.py": {"process_control", "envelope_guard"},
        REPO_ROOT / "scripts" / "run_phase5_kill_rehearsal.py": {"process_control", "runtime_identity"},
    }
    for root in ("scripts", ".github"):
        for path in (REPO_ROOT / root).rglob("*"):
            if path.is_file() and path.suffix in (".py", ".yml", ".yaml"):
                text = path.read_text(encoding="utf-8")
                permitted = permitted_by_path.get(path, set())
                for token in all_tokens:
                    if token not in permitted:
                        assert token not in text, (path, token)

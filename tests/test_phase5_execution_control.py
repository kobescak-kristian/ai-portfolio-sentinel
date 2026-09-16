"""Tests for sentinel/phase5/execution_control.py (ADR-0012 section 8,
Amendment A2/A6; dispatch q77-p5d-repair-stage2c1-implement-a, Stage
2C-1). Model-free: injected fake replace callbacks only; the real
terminal writer is never connected here."""

from __future__ import annotations

import ast
import inspect
import random
import threading
import typing
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from sentinel.phase5 import execution_control as ec
from sentinel.phase5 import execution_envelope as ee
from sentinel.phase5 import terminal as t
from sentinel.phase5.evidence_records import TerminationCause

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "sentinel" / "phase5" / "execution_control.py"
ENVELOPE_PATH = REPO_ROOT / "sentinel" / "phase5" / "execution_envelope.py"
NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
S = ec.TerminalCommitState


class _Mono:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _Counter:
    def __init__(self, error: BaseException | None = None) -> None:
        self.calls = 0
        self.error = error

    def __call__(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


def _setup(remaining_s: int = 3_600, mono: _Mono | None = None):
    mono = mono or _Mono()
    domain = ec.ExecutionSafetyDomain()
    latch = ec.SessionLatch(domain)
    clock = ee.SessionClock(
        job_started_at_utc=NOW, resolved_at_utc=NOW, monotonic_at_resolve=mono.value,
        session_duration_s=remaining_s, monotonic=mono,
    )
    arbiter = ec.TerminalArbiter(domain, latch, clock)
    return domain, latch, clock, arbiter, mono


# ======================================================================
# Vocabulary and shared domain
# ======================================================================


def test_cause_vocabularies_are_the_canonical_five():
    assert ec.STAGE2C_CAUSES == {"SESSION_DEADLINE", "INVOCATION_STALL_DEADLINE", "WATCHDOG"}
    assert ec.STAGE2B_CAUSES == {"PRE_PROVIDER_FAILURE", "RUNNER_EXCEPTION"}
    assert ec.CANONICAL_CAUSES == set(typing.get_args(TerminationCause))
    assert not hasattr(ec, "ExecutionState")
    assert t.ExecutionState is not None  # the Stage-2B state vocabulary keeps its name


def test_exactly_one_lock_in_the_module_owned_by_the_domain():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    lock_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) or getattr(node.func, "id", None))
        in ("Lock", "RLock", "Condition", "Semaphore", "BoundedSemaphore", "Event", "Barrier")
    ]
    assert len(lock_calls) == 1
    owner = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "ExecutionSafetyDomain"
    )
    assert any(node is lock_calls[0] for node in ast.walk(owner))
    for node in tree.body:  # no module-level lock, config instance or thread
        assert not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(
            getattr(node, "value", None), ast.Call
        ) or getattr(node.value.func, "id", "") == "frozenset" or getattr(node.value.func, "attr", "") == "union"
    domain = ec.ExecutionSafetyDomain()
    assert type(domain.lock) is type(threading.RLock())
    assert domain.lock is domain.lock


def test_latch_arbiter_and_registry_share_the_domain():
    domain, latch, clock, arbiter, _ = _setup()
    registry = ec.InvocationRegistry(domain)
    assert latch.domain is domain and registry.domain is domain
    other = ec.SessionLatch(ec.ExecutionSafetyDomain())
    with pytest.raises(ec.ExecutionControlError):
        ec.TerminalArbiter(domain, other, clock)
    with pytest.raises(ec.ExecutionControlError):
        ec.SessionLatch(object())  # type: ignore[arg-type]


@pytest.mark.parametrize("module", [MODULE_PATH, ENVELOPE_PATH])
def test_no_threads_signals_proc_anyio_or_sdk(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {"anyio", "claude_agent_sdk", "subprocess", "signal", "multiprocessing", "asyncio", "socket"}
    assert not any(name.split(".")[0] in forbidden for name in imported), imported
    text = module.read_text(encoding="utf-8")
    for token in ("Thread(", "/proc", "os.kill", "killpg", "start_new_session", "os._exit"):
        assert token not in text, token


# ======================================================================
# Session latch
# ======================================================================


def test_first_cause_wins_under_concurrency():
    for _ in range(20):
        domain = ec.ExecutionSafetyDomain()
        latch = ec.SessionLatch(domain)
        causes = ["SESSION_DEADLINE", "INVOCATION_STALL_DEADLINE", "WATCHDOG"]
        barrier = threading.Barrier(32)
        results: list[tuple[str, float, bool]] = []
        lock = threading.Lock()

        def worker(i: int) -> None:
            cause = causes[i % 3]
            barrier.wait()
            won = latch.trip(cause, 1000.0 + i)
            with lock:
                results.append((cause, 1000.0 + i, won))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        winners = [r for r in results if r[2]]
        assert len(winners) == 1
        assert latch.cause == winners[0][0] and latch.tripped_at == winners[0][1] and latch.is_set


def test_later_cause_never_replaces_cause_or_trip_time():
    _, latch, _, _, _ = _setup()
    assert latch.trip("INVOCATION_STALL_DEADLINE", 5.0) is True
    assert latch.trip("SESSION_DEADLINE", 6.0) is False
    assert latch.trip("WATCHDOG", 7.0) is False
    assert latch.trip("INVOCATION_STALL_DEADLINE", 8.0) is False
    assert (latch.cause, latch.tripped_at) == ("INVOCATION_STALL_DEADLINE", 5.0)


@pytest.mark.parametrize("cause", ["PRE_PROVIDER_FAILURE", "RUNNER_EXCEPTION", "SIGTERM", "free text", "", None])
def test_only_stage2c_causes_can_trip(cause):
    _, latch, _, _, _ = _setup()
    with pytest.raises(ec.LatchCauseError):
        latch.trip(cause, 1.0)
    assert not latch.is_set


def test_latch_has_no_text_capable_field():
    assert ec.SessionLatch.__slots__ == ("_domain", "_cause", "_tripped_at")
    assert set(inspect.signature(ec.SessionLatch.trip).parameters) == {"self", "cause", "at_monotonic"}
    _, latch, _, _, _ = _setup()
    with pytest.raises(ec.ExecutionControlError):
        latch.trip("WATCHDOG", "not a number")
    with pytest.raises(ec.ExecutionControlError):
        latch.trip("WATCHDOG", True)
    with pytest.raises(AttributeError):
        latch.detail = "free text"  # type: ignore[attr-defined]


# ======================================================================
# Terminal arbiter -- commit points with injected fake replace callbacks
# ======================================================================


def test_cause_before_quality_commit_refuses_quality():
    _, latch, _, arbiter, _ = _setup()
    latch.trip("INVOCATION_STALL_DEADLINE", 1000.0)
    replace = _Counter()
    with pytest.raises(ec.QualityRefused) as info:
        arbiter.commit_quality(replace)
    assert info.value.cause == "INVOCATION_STALL_DEADLINE" and info.value.state is S.NONE
    assert replace.calls == 0 and arbiter.state is S.NONE
    invalid = _Counter()
    arbiter.commit_invalid("INVOCATION_STALL_DEADLINE", invalid)
    assert invalid.calls == 1 and arbiter.state is S.INVALID_COMMITTED


def test_expired_clock_at_commit_point_latches_session_deadline_and_refuses():
    _, latch, clock, arbiter, mono = _setup(remaining_s=60)
    mono.value += 60
    assert clock.expired() and not latch.is_set
    replace = _Counter()
    with pytest.raises(ec.QualityRefused) as info:
        arbiter.commit_quality(replace)
    assert info.value.cause == "SESSION_DEADLINE"
    assert latch.cause == "SESSION_DEADLINE" and latch.tripped_at == mono.value
    assert arbiter.deadline_established_by_commit_point is True
    assert replace.calls == 0 and arbiter.state is S.NONE
    invalid = _Counter()
    arbiter.commit_invalid("SESSION_DEADLINE", invalid)
    assert invalid.calls == 1 and arbiter.state is S.INVALID_COMMITTED


def test_quality_commit_succeeds_first_then_later_cause_cannot_authorize_invalid():
    _, latch, _, arbiter, _ = _setup()
    replace = _Counter()
    arbiter.commit_quality(replace)
    assert replace.calls == 1 and arbiter.state is S.QUALITY_COMMITTED
    assert arbiter.deadline_established_by_commit_point is False
    assert latch.trip("WATCHDOG", 2000.0) is True  # the cause is recorded ...
    invalid = _Counter()
    with pytest.raises(ec.InvalidRefused) as info:
        arbiter.commit_invalid("WATCHDOG", invalid)  # ... but can never replace committed quality
    assert info.value.state is S.QUALITY_COMMITTED and invalid.calls == 0
    assert arbiter.state is S.QUALITY_COMMITTED
    with pytest.raises(ec.QualityRefused):
        arbiter.commit_quality(_Counter())  # and quality is committed exactly once


@pytest.mark.parametrize("error", [OSError("disk"), RuntimeError("x"), KeyboardInterrupt(), SystemExit(3)])
def test_replace_failure_is_failed_never_committed_and_reraised(error):
    _, latch, _, arbiter, _ = _setup()
    replace = _Counter(error)
    with pytest.raises(type(error)):
        arbiter.commit_quality(replace)
    assert replace.calls == 1 and arbiter.state is S.QUALITY_FAILED
    with pytest.raises(ec.QualityRefused):
        arbiter.commit_quality(_Counter())  # no implicit retry
    # invalid after a failed quality attempt: Stage-2B cause with latch clear ...
    invalid = _Counter()
    arbiter.commit_invalid("RUNNER_EXCEPTION", invalid)
    assert invalid.calls == 1 and arbiter.state is S.INVALID_COMMITTED


def test_failed_quality_permits_invalid_only_with_legitimate_cause():
    _, latch, _, arbiter, _ = _setup()
    with pytest.raises(OSError):
        arbiter.commit_quality(_Counter(OSError()))
    assert arbiter.state is S.QUALITY_FAILED
    with pytest.raises(ec.InvalidRefused):
        arbiter.commit_invalid("SESSION_DEADLINE", _Counter())  # not latched
    latch.trip("SESSION_DEADLINE", 1.0)
    with pytest.raises(ec.InvalidRefused):
        arbiter.commit_invalid("WATCHDOG", _Counter())  # wrong cause
    with pytest.raises(ec.InvalidRefused):
        arbiter.commit_invalid("RUNNER_EXCEPTION", _Counter())  # Stage-2B cause while latched
    with pytest.raises(ec.LatchCauseError):
        arbiter.commit_invalid("SIGTERM", _Counter())  # a raw signal is never a cause
    assert arbiter.state is S.QUALITY_FAILED
    invalid = _Counter()
    arbiter.commit_invalid("SESSION_DEADLINE", invalid)
    assert invalid.calls == 1 and arbiter.state is S.INVALID_COMMITTED


def test_invalid_failure_is_failed_and_terminal():
    _, latch, _, arbiter, _ = _setup()
    latch.trip("WATCHDOG", 1.0)
    with pytest.raises(OSError):
        arbiter.commit_invalid("WATCHDOG", _Counter(OSError()))
    assert arbiter.state is S.INVALID_FAILED
    with pytest.raises(ec.InvalidRefused):
        arbiter.commit_invalid("WATCHDOG", _Counter())
    with pytest.raises(ec.QualityRefused):
        arbiter.commit_quality(_Counter())


def test_quality_cannot_follow_committed_invalid_and_later_cause_never_mutates_committed_class():
    _, latch, _, arbiter, _ = _setup()
    latch.trip("SESSION_DEADLINE", 1.0)
    arbiter.commit_invalid("SESSION_DEADLINE", _Counter())
    assert arbiter.state is S.INVALID_COMMITTED
    with pytest.raises(ec.QualityRefused):
        arbiter.commit_quality(_Counter())
    assert latch.trip("WATCHDOG", 2.0) is False
    with pytest.raises(ec.InvalidRefused):
        arbiter.commit_invalid("SESSION_DEADLINE", _Counter())
    assert arbiter.state is S.INVALID_COMMITTED and latch.cause == "SESSION_DEADLINE"


def test_mixed_concurrency_never_yields_two_committed_terminal_classes():
    rng = random.Random(2026)
    for _ in range(25):
        domain, latch, clock, arbiter, mono = _setup(remaining_s=3_600)
        barrier = threading.Barrier(32)
        quality_calls = _Counter()
        invalid_calls = _Counter()
        lock = threading.Lock()
        outcomes: list[str] = []

        def record(label: str) -> None:
            with lock:
                outcomes.append(label)

        def worker(kind: str, cause: str) -> None:
            barrier.wait()
            try:
                if kind == "trip":
                    record("trip" if latch.trip(cause, 5.0) else "trip-lost")
                elif kind == "quality":
                    arbiter.commit_quality(quality_calls)
                    record("quality")
                else:
                    arbiter.commit_invalid(cause, invalid_calls)
                    record("invalid")
            except (ec.ExecutionControlError, ec.LatchCauseError):
                record("refused")

        specs = []
        for i in range(32):
            kind = rng.choice(["trip", "quality", "invalid"])
            pool = ec.STAGE2C_CAUSES if kind == "trip" else ec.CANONICAL_CAUSES
            cause = rng.choice(sorted(pool))
            specs.append((kind, cause))
        threads = [threading.Thread(target=worker, args=spec) for spec in specs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        committed = outcomes.count("quality") + outcomes.count("invalid")
        assert committed <= 1
        assert quality_calls.calls + invalid_calls.calls == committed
        assert arbiter.state in (S.NONE, S.QUALITY_COMMITTED, S.INVALID_COMMITTED)
        if arbiter.state is S.INVALID_COMMITTED:
            assert "invalid" in outcomes
        if arbiter.state is S.QUALITY_COMMITTED:
            assert "quality" in outcomes


def test_lock_not_held_during_staging_so_a_trip_lands_immediately():
    domain, latch, _, arbiter, _ = _setup()
    staging_started = threading.Event()
    release_staging = threading.Event()
    trip_done = threading.Event()

    def caller() -> None:
        # Stage-2B placement: staging happens OUTSIDE any lock, then the commit point.
        staging_started.set()
        release_staging.wait(5)
        try:
            arbiter.commit_quality(_Counter())
        except ec.QualityRefused:
            pass

    thread = threading.Thread(target=caller)
    thread.start()
    assert staging_started.wait(5)
    assert latch.trip("INVOCATION_STALL_DEADLINE", 1.0) is True  # returns while staging is still "running"
    trip_done.set()
    release_staging.set()
    thread.join(5)
    assert arbiter.state is S.NONE and latch.cause == "INVOCATION_STALL_DEADLINE"


def test_trip_blocks_only_across_replace_and_then_finds_quality_committed():
    domain, latch, _, arbiter, _ = _setup()
    inside_replace = threading.Event()
    release_replace = threading.Event()
    order: list[str] = []

    def replace() -> None:
        inside_replace.set()
        order.append("replace-start")
        release_replace.wait(5)
        order.append("replace-end")

    def tripper() -> None:
        inside_replace.wait(5)
        latch.trip("WATCHDOG", 9.0)
        order.append("trip-returned")

    committer = threading.Thread(target=lambda: arbiter.commit_quality(replace))
    trip_thread = threading.Thread(target=tripper)
    committer.start()
    trip_thread.start()
    assert inside_replace.wait(5)
    trip_thread.join(0.2)
    assert trip_thread.is_alive()  # the trip is blocked while replace() holds the domain lock
    release_replace.set()
    committer.join(5)
    trip_thread.join(5)
    assert order == ["replace-start", "replace-end", "trip-returned"]
    assert arbiter.state is S.QUALITY_COMMITTED and latch.cause == "WATCHDOG"
    with pytest.raises(ec.InvalidRefused):
        arbiter.commit_invalid("WATCHDOG", _Counter())


def test_replace_is_called_exactly_once_per_commit_point():
    _, latch, _, arbiter, _ = _setup()
    replace = _Counter()
    arbiter.commit_quality(replace)
    assert replace.calls == 1
    _, latch2, _, arbiter2, _ = _setup()
    latch2.trip("WATCHDOG", 1.0)
    invalid = _Counter()
    arbiter2.commit_invalid("WATCHDOG", invalid)
    assert invalid.calls == 1
    with pytest.raises(ec.ExecutionControlError):
        arbiter2.commit_invalid("WATCHDOG", "not callable")  # type: ignore[arg-type]


def test_refusal_exceptions_carry_only_closed_vocabulary():
    _, latch, _, arbiter, _ = _setup()
    latch.trip("WATCHDOG", 1.0)
    with pytest.raises(ec.QualityRefused) as info:
        arbiter.commit_quality(_Counter())
    assert info.value.cause in ec.CANONICAL_CAUSES and isinstance(info.value.state, S)
    with pytest.raises(ec.InvalidRefused) as info2:
        arbiter.commit_invalid("SESSION_DEADLINE", _Counter())
    assert info2.value.cause in ec.CANONICAL_CAUSES and info2.value.latched in ec.CANONICAL_CAUSES
    assert set(S.__members__) == {
        "NONE", "QUALITY_IN_PROGRESS", "QUALITY_COMMITTED", "QUALITY_FAILED",
        "INVALID_IN_PROGRESS", "INVALID_COMMITTED", "INVALID_FAILED",
    }


def test_arbiter_is_not_connected_to_the_real_terminal_writer():
    """Stage 2C-1 deliberately adds no commit-point seam to
    write_terminal_atomically; the arbiter is exercised only through
    injected callbacks."""
    params = set(inspect.signature(t.write_terminal_atomically).parameters)
    assert params == {"record", "publication_root", "staging_root", "prior", "identity"}
    assert "commit_point" not in params
    terminal_text = (REPO_ROOT / "sentinel" / "phase5" / "terminal.py").read_text(encoding="utf-8")
    assert "execution_control" not in terminal_text and "commit_point" not in terminal_text
    control_text = MODULE_PATH.read_text(encoding="utf-8")
    assert "write_terminal_atomically" not in control_text.replace("``terminal.write_terminal_atomically`` is unchanged", "")


# ======================================================================
# Execution-control configuration
# ======================================================================


def test_control_config_has_no_defaults_and_no_committed_instance():
    fields = ec.ExecutionControlConfig.model_fields
    assert set(fields) == {
        "schema_version", "heartbeat_interval_ms", "watchdog_grace_ms",
        "descendant_term_grace_ms", "descendant_kill_wait_ms", "monitor_tick_ms",
    }
    assert all(info.is_required() for info in fields.values())
    with pytest.raises(ValidationError):
        ec.ExecutionControlConfig(schema_version=1)
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(getattr(node, "value", None), ast.Call):
            assert getattr(node.value.func, "id", "") != "ExecutionControlConfig"
    for path in sorted((REPO_ROOT / "sentinel").rglob("*.py")) + sorted((REPO_ROOT / "scripts").rglob("*.py")):
        if path == MODULE_PATH:
            continue
        assert "ExecutionControlConfig(" not in path.read_text(encoding="utf-8"), path


def test_control_config_is_frozen_closed_and_identified():
    config = ec.ExecutionControlConfig(
        schema_version=1, heartbeat_interval_ms=1, watchdog_grace_ms=2,
        descendant_term_grace_ms=3, descendant_kill_wait_ms=4, monitor_tick_ms=5,
    )
    assert len(config.control_config_id) == 64
    same = ec.ExecutionControlConfig(**config.model_dump())
    assert same.control_config_id == config.control_config_id
    with pytest.raises(ValidationError):
        config.monitor_tick_ms = 6  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ec.ExecutionControlConfig(**config.model_dump(), extra=1)
    with pytest.raises(ValidationError):
        ec.ExecutionControlConfig(**{**config.model_dump(), "monitor_tick_ms": 0})


# ======================================================================
# Invocation registry
# ======================================================================


def test_invocation_registry_bookkeeping():
    domain = ec.ExecutionSafetyDomain()
    registry = ec.InvocationRegistry(domain)
    first = registry.start(run_ordinal=1, invocation_ordinal=1, budget_ms=600_000, at_monotonic=100.0)
    assert first.in_flight and first.deadline_monotonic == 700.0
    assert registry.in_flight() == (first,)
    with pytest.raises(ec.ExecutionControlError):
        registry.start(run_ordinal=1, invocation_ordinal=1, budget_ms=1, at_monotonic=101.0)
    assert registry.overrun(now_monotonic=699.9, grace_ms=0) == ()
    assert registry.overrun(now_monotonic=700.0, grace_ms=0) == (first,)
    assert registry.overrun(now_monotonic=700.0, grace_ms=1_000) == ()
    assert registry.overrun(now_monotonic=701.0, grace_ms=1_000) == (first,)
    done = registry.finish(run_ordinal=1, invocation_ordinal=1, outcome="TIMED_OUT", at_monotonic=701.0)
    assert done.outcome == "TIMED_OUT" and done.finished_at_monotonic == 701.0 and not done.in_flight
    assert registry.in_flight() == () and registry.records() == (done,)
    with pytest.raises(ec.ExecutionControlError):
        registry.finish(run_ordinal=1, invocation_ordinal=1, outcome="RETURNED", at_monotonic=702.0)
    with pytest.raises(ec.ExecutionControlError):
        registry.finish(run_ordinal=2, invocation_ordinal=1, outcome="RETURNED", at_monotonic=702.0)
    registry.start(run_ordinal=2, invocation_ordinal=1, budget_ms=1, at_monotonic=800.0)
    with pytest.raises(ec.ExecutionControlError):
        registry.finish(run_ordinal=2, invocation_ordinal=1, outcome="GREEN", at_monotonic=801.0)
    for outcome in ("RETURNED", "RAISED", "TIMED_OUT"):
        assert outcome in typing.get_args(ec.InvocationOutcome)
    with pytest.raises(ec.ExecutionControlError):
        registry.start(run_ordinal=0, invocation_ordinal=1, budget_ms=1, at_monotonic=1.0)
    with pytest.raises(ec.ExecutionControlError):
        registry.overrun(now_monotonic=1.0, grace_ms=-1)

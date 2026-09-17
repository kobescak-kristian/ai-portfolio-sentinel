"""Tests for agents/checker/envelope_guard.py (ADR-0012 section 8, Amendment
A4; dispatch q77-p5d-repair-stage2c2-implement-a, Stage 2C-2).

Model-free and provider-free: every guarded callable is a local async
fake. Coroutines run on a real asyncio loop (the AnyIO asyncio backend).
On Windows the loop's local self-pipe needs ``socket.connect``, which
conftest's network guard blocks, so the guard is lifted ONLY around loop
construction; every test still runs with the network guard in place.
"""

from __future__ import annotations

import _socket
import asyncio
import inspect
import socket
import time
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agents.checker import envelope_guard as eg
from agents.checker import failures, harness
from agents.checker import process_control as pc
from agents.checker.budget import RunBudgetCoordinator
from agents.checker.failures import QueryOutcome
from agents.checker.fx import FxRate
from agents.checker.oidc import health_gated
from checks.judgment.stubs import JudgmentRequest
from contracts.schemas import RunRecord
from sentinel import ledger
from sentinel.phase5 import execution_control as ec
from sentinel.phase5 import execution_envelope as ee
from sentinel.phase5.journal import OperationalJournal, read_journal

NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)


def _loop_factory():
    guarded_connect = socket.socket.connect
    socket.socket.connect = _socket.socket.connect  # local self-pipe only, restored immediately
    try:
        return asyncio.new_event_loop()
    finally:
        socket.socket.connect = guarded_connect


def run_async(fn, *args):
    with asyncio.Runner(loop_factory=_loop_factory) as runner:
        return runner.run(fn(*args))


class _FixedClock(ee.SessionClock):
    """A session clock whose remaining budget is fixed by the test."""

    def __init__(self, remaining_ms: int, expired: bool = False) -> None:
        super().__init__(job_started_at_utc=NOW, resolved_at_utc=NOW, monotonic_at_resolve=time.monotonic(),
                         session_duration_s=3_600)
        self.fixed_remaining_ms = remaining_ms
        self.force_expired = expired

    def remaining_ms(self) -> int:
        return self.fixed_remaining_ms

    def expired(self) -> bool:
        return self.force_expired or self.fixed_remaining_ms <= 0


class _SpyRegistry(ec.InvocationRegistry):
    __slots__ = ("finish_calls",)

    def __init__(self, domain) -> None:
        super().__init__(domain)
        self.finish_calls: dict[tuple[int, int], int] = {}

    def finish(self, *, run_ordinal, invocation_ordinal, outcome, at_monotonic):
        key = (run_ordinal, invocation_ordinal)
        self.finish_calls[key] = self.finish_calls.get(key, 0) + 1
        return super().finish(run_ordinal=run_ordinal, invocation_ordinal=invocation_ordinal,
                              outcome=outcome, at_monotonic=at_monotonic)


class _ExternalAbort(BaseException):
    """An external, non-Exception abort (models KeyboardInterrupt-class
    termination without disturbing the test runner)."""


class _Env:
    def __init__(self, tmp_path, *, remaining_ms=3_600_000, stall_budget_ms=600_000, expired=False,
                 survivors=0, terminate_raises=False, failure_hook_raises=False):
        self.domain = ec.ExecutionSafetyDomain()
        self.latch = ec.SessionLatch(self.domain)
        self.registry = _SpyRegistry(self.domain)
        self.clock = _FixedClock(remaining_ms, expired)
        self.path = tmp_path / "journal.jsonl"
        self.journal = OperationalJournal(self.path, writer="RUNNER").open()
        self.stall_budget_ms = stall_budget_ms
        self.terminations: list = []
        self.failures: list = []
        self._survivors = survivors
        self._terminate_raises = terminate_raises
        self._failure_hook_raises = failure_hook_raises

    def _terminate(self, config):
        self.terminations.append(config)
        if self._terminate_raises:
            raise OSError("termination failed")
        return pc.TerminationReport(1, 0, 1, 3, 1, 0, 1 - min(1, self._survivors), self._survivors, 5)

    def _on_failure(self):
        self.failures.append(1)
        if self._failure_hook_raises:
            raise RuntimeError("hook failure")

    def guard(self, fn, run_ordinal=1):
        return eg.deadline_guarded(
            fn, run_ordinal=run_ordinal, clock=self.clock, latch=self.latch, registry=self.registry,
            journal=self.journal, stall_budget_ms=self.stall_budget_ms, config=pc.CONTROL_CONFIG,
            terminate=self._terminate, on_control_failure=self._on_failure,
        )

    def events(self, name=None):
        return [e for e in read_journal(self.path).events if name is None or e.event == name]

    def outcomes(self):
        return [record.outcome for record in self.registry.records()]

    def assert_finished_once(self):
        assert all(count == 1 for count in self.registry.finish_calls.values()), self.registry.finish_calls
        assert set(self.registry.finish_calls) == {(r.run_ordinal, r.invocation_ordinal) for r in self.registry.records()}


def _args():
    return ("missing-synthetic-label", "reservation", "state", "prompt", "model")


# ======================================================================
# Construction and coroutine identity
# ======================================================================


def test_guard_preserves_coroutine_function_and_refuses_sync(tmp_path):
    env = _Env(tmp_path)

    async def fake(check_class, reservation, state, user_prompt, model=None):
        return "ok"

    guarded = env.guard(fake)
    assert inspect.iscoroutinefunction(guarded) and guarded.__name__ == "fake"
    with pytest.raises(eg.EnvelopeGuardError):
        env.guard(lambda *a: "sync")
    for bad in (0, 3, True):
        with pytest.raises(eg.EnvelopeGuardError):
            env.guard(fake, run_ordinal=bad)
    other_registry = ec.InvocationRegistry(ec.ExecutionSafetyDomain())
    with pytest.raises(eg.EnvelopeGuardError):
        eg.deadline_guarded(fake, run_ordinal=1, clock=env.clock, latch=env.latch, registry=other_registry,
                            journal=env.journal, stall_budget_ms=1, config=pc.CONTROL_CONFIG,
                            terminate=env._terminate, on_control_failure=env._on_failure)


def test_guard_composes_with_health_gated_in_both_orders(tmp_path):
    class Session:
        def assert_healthy(self):
            return None

    async def fake(check_class, reservation, state, user_prompt, model=None):
        return "value"

    env = _Env(tmp_path)
    inner = env.guard(health_gated(fake, Session()))
    outer = health_gated(env.guard(fake, run_ordinal=2), Session())
    for wrapped in (inner, outer):
        assert inspect.iscoroutinefunction(wrapped)
        assert run_async(wrapped, *_args()) == "value"
    assert env.outcomes() == ["RETURNED", "RETURNED"]
    env.assert_finished_once()


# ======================================================================
# Pre-start refusal
# ======================================================================


def test_latched_cause_refuses_before_the_wrapped_callable(tmp_path):
    env = _Env(tmp_path)
    calls = []

    async def fake(*args):
        calls.append(args)

    env.latch.trip("WATCHDOG", 1.0)
    with pytest.raises(eg.ProviderStartRefused) as info:
        run_async(env.guard(fake), *_args())
    assert info.value.cause == "WATCHDOG" and str(info.value) == "ProviderStartRefused: cause=WATCHDOG"
    assert calls == [] and env.registry.records() == ()
    assert env.events("INVOCATION_STARTED") == [] and env.events("OBJECTIVE_CAUSE_LATCHED") == []


def test_expired_session_trips_session_deadline_once_and_refuses(tmp_path):
    env = _Env(tmp_path, remaining_ms=0)
    calls = []

    async def fake(*args):
        calls.append(args)

    guarded = env.guard(fake)
    for _ in range(2):
        with pytest.raises(eg.ProviderStartRefused) as info:
            run_async(guarded, *_args())
        assert info.value.cause == "SESSION_DEADLINE"
    assert calls == [] and env.registry.records() == ()
    assert [e.cause for e in env.events("OBJECTIVE_CAUSE_LATCHED")] == ["SESSION_DEADLINE"]


# ======================================================================
# Budget, ordering and the session-bound tie
# ======================================================================


@pytest.mark.parametrize("remaining, stall, expected", [(5_000, 600_000, 5_000), (10_000_000, 600_000, 600_000)])
def test_budget_is_the_a4_invocation_budget_exactly(tmp_path, remaining, stall, expected):
    env = _Env(tmp_path, remaining_ms=remaining, stall_budget_ms=stall)
    seen = {}

    async def fake(*args):
        seen["in_flight"] = env.registry.in_flight()
        seen["started"] = [e.event for e in env.events()]
        return "ok"

    assert run_async(env.guard(fake), *_args()) == "ok"
    assert env.registry.records()[0].budget_ms == expected == ee.invocation_budget_ms(remaining, stall)
    assert len(seen["in_flight"]) == 1 and seen["started"][-1] == "INVOCATION_STARTED"
    finished = env.events("INVOCATION_FINISHED")
    assert [e.invocation_outcome for e in finished] == ["RETURNED"]
    env.assert_finished_once()


def test_session_bound_tie_is_session_deadline(tmp_path):
    env = _Env(tmp_path, remaining_ms=60, stall_budget_ms=60)

    async def fake(*args):
        await asyncio.sleep(5)

    with pytest.raises(eg.InvocationDeadlineExceeded) as info:
        run_async(env.guard(fake), *_args())
    assert env.registry.records()[0].budget_ms == 60
    assert info.value.cause == "SESSION_DEADLINE" and env.latch.cause == "SESSION_DEADLINE"
    assert [e.cause for e in env.events("OBJECTIVE_CAUSE_LATCHED")] == ["SESSION_DEADLINE"]


def test_one_millisecond_above_the_tie_is_invocation_stall_deadline(tmp_path):
    env = _Env(tmp_path, remaining_ms=61, stall_budget_ms=60)

    async def fake(*args):
        await asyncio.sleep(5)

    with pytest.raises(eg.InvocationDeadlineExceeded) as info:
        run_async(env.guard(fake), *_args())
    assert env.registry.records()[0].budget_ms == 60
    assert info.value.cause == "INVOCATION_STALL_DEADLINE" == env.latch.cause


def test_stall_bound_timeout_after_session_expiry_is_session_deadline(tmp_path):
    env = _Env(tmp_path, remaining_ms=10_000, stall_budget_ms=50)

    async def fake(*args):
        env.clock.force_expired = True  # the session ends while the call is stalled
        await asyncio.sleep(5)

    with pytest.raises(eg.InvocationDeadlineExceeded) as info:
        run_async(env.guard(fake), *_args())
    assert info.value.cause == "SESSION_DEADLINE"


# ======================================================================
# Outcome classification: A (own deadline), B (ordinary), C (external), D
# ======================================================================


def test_A_own_deadline_is_timed_out_exactly_once(tmp_path):
    env = _Env(tmp_path, stall_budget_ms=50)

    async def fake(*args):
        await asyncio.sleep(5)

    with pytest.raises(eg.InvocationDeadlineExceeded) as info:
        run_async(env.guard(fake), *_args())
    assert info.value.cause == "INVOCATION_STALL_DEADLINE"
    assert env.outcomes() == ["TIMED_OUT"]
    assert [e.invocation_outcome for e in env.events("INVOCATION_FINISHED")] == ["TIMED_OUT"]
    assert len(env.terminations) == 1 and env.terminations[0] is pc.CONTROL_CONFIG
    assert env.failures == []
    env.assert_finished_once()


def test_B_ordinary_exception_is_raised_never_timed_out(tmp_path):
    env = _Env(tmp_path)
    error = ValueError("provider transport failure")

    async def fake(*args):
        raise error

    with pytest.raises(ValueError) as info:
        run_async(env.guard(fake), *_args())
    assert info.value is error
    assert env.outcomes() == ["RAISED"] and env.latch.cause is None
    assert env.terminations == [] and env.events("OBJECTIVE_CAUSE_LATCHED") == []
    env.assert_finished_once()


def test_B_sdk_originated_timeout_error_is_not_our_deadline(tmp_path):
    env = _Env(tmp_path)

    async def fake(*args):
        raise TimeoutError("sdk control request timed out")

    with pytest.raises(TimeoutError):
        run_async(env.guard(fake), *_args())
    assert env.outcomes() == ["RAISED"] and env.latch.cause is None and env.terminations == []
    env.assert_finished_once()


def test_C_external_task_cancellation_is_raised_without_a_cause(tmp_path):
    env = _Env(tmp_path)
    guarded = env.guard(_sleeper)

    async def main():
        task = asyncio.ensure_future(guarded(*_args()))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        return "not cancelled"

    assert run_async(main) == "cancelled"
    assert env.outcomes() == ["RAISED"] and env.latch.cause is None
    assert env.terminations == [] and env.events("OBJECTIVE_CAUSE_LATCHED") == []
    env.assert_finished_once()


def test_C_shorter_outer_deadline_is_raised_without_a_cause(tmp_path):
    env = _Env(tmp_path)
    guarded = env.guard(_sleeper)

    async def main():
        try:
            async with asyncio.timeout(0.05):
                await guarded(*_args())
        except TimeoutError:
            return "outer timeout"
        return "no timeout"

    assert run_async(main) == "outer timeout"
    assert env.outcomes() == ["RAISED"] and env.latch.cause is None and env.terminations == []
    env.assert_finished_once()


def test_C_external_base_exception_is_raised_without_a_cause(tmp_path):
    env = _Env(tmp_path)

    async def fake(*args):
        raise _ExternalAbort()

    with pytest.raises(_ExternalAbort):
        run_async(env.guard(fake), *_args())
    assert env.outcomes() == ["RAISED"] and env.latch.cause is None and env.terminations == []
    env.assert_finished_once()


def test_C_external_base_exception_after_own_deadline_is_still_raised_without_a_cause(tmp_path):
    env = _Env(tmp_path, stall_budget_ms=50)

    async def fake(*args):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise _ExternalAbort()

    with pytest.raises(_ExternalAbort):
        run_async(env.guard(fake), *_args())
    assert env.outcomes() == ["RAISED"]
    assert env.latch.cause is None and env.terminations == [] and env.events("OBJECTIVE_CAUSE_LATCHED") == []
    env.assert_finished_once()


def test_D_swallowed_own_cancellation_is_timed_out_and_result_discarded(tmp_path):
    env = _Env(tmp_path, stall_budget_ms=50)

    async def fake(*args):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return "late result that must be discarded"

    with pytest.raises(eg.InvocationDeadlineExceeded) as info:
        run_async(env.guard(fake), *_args())
    assert info.value.__cause__ is None
    assert env.outcomes() == ["TIMED_OUT"] and env.latch.cause == "INVOCATION_STALL_DEADLINE"
    env.assert_finished_once()


def test_D_cleanup_exception_after_own_deadline_is_timed_out_and_chained(tmp_path):
    env = _Env(tmp_path, stall_budget_ms=50)
    cleanup = RuntimeError("cleanup replaced the cancellation")

    async def fake(*args):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise cleanup

    with pytest.raises(eg.InvocationDeadlineExceeded) as info:
        run_async(env.guard(fake), *_args())
    assert info.value.__cause__ is cleanup
    assert env.outcomes() == ["TIMED_OUT"] and len(env.terminations) == 1
    assert b"cleanup replaced" not in env.path.read_bytes()
    env.assert_finished_once()


async def _sleeper(*args):
    await asyncio.sleep(5)


# ======================================================================
# First cause wins; fail-closed termination reporting
# ======================================================================


def test_losing_trip_journals_no_second_cause_and_reports_the_first(tmp_path):
    env = _Env(tmp_path, stall_budget_ms=50)

    async def fake(*args):
        env.latch.trip("WATCHDOG", time.monotonic())  # e.g. the monitor latched first
        await asyncio.sleep(5)

    with pytest.raises(eg.InvocationDeadlineExceeded) as info:
        run_async(env.guard(fake), *_args())
    assert info.value.cause == "WATCHDOG" and env.latch.cause == "WATCHDOG"
    assert env.events("OBJECTIVE_CAUSE_LATCHED") == []
    env.assert_finished_once()


@pytest.mark.parametrize("survivors, raises", [(1, False), (0, True)])
def test_termination_survivors_or_fault_call_the_fail_closed_hook_once(tmp_path, survivors, raises):
    env = _Env(tmp_path, stall_budget_ms=50, survivors=survivors, terminate_raises=raises)
    with pytest.raises(eg.InvocationDeadlineExceeded):
        run_async(env.guard(_sleeper), *_args())
    assert env.failures == [1] and env.outcomes() == ["TIMED_OUT"]
    env.assert_finished_once()


def test_failing_hook_never_masks_the_timeout(tmp_path):
    env = _Env(tmp_path, stall_budget_ms=50, survivors=1, failure_hook_raises=True)
    with pytest.raises(eg.InvocationDeadlineExceeded):
        run_async(env.guard(_sleeper), *_args())
    assert env.failures == [1] and env.outcomes() == ["TIMED_OUT"]


def test_ordinals_increment_per_wrapper_and_journal_is_content_free(tmp_path):
    env = _Env(tmp_path)

    async def fake(*args):
        return "secret model output text"

    guarded = env.guard(fake, run_ordinal=2)
    for _ in range(3):
        run_async(guarded, *_args())
    assert [(r.run_ordinal, r.invocation_ordinal) for r in env.registry.records()] == [(2, 1), (2, 2), (2, 3)]
    assert b"secret model output" not in env.path.read_bytes()
    env.assert_finished_once()


# ======================================================================
# Harness classification: no timeout ever becomes a retry
# ======================================================================


@pytest.mark.parametrize("exc", [eg.InvocationDeadlineExceeded("SESSION_DEADLINE"), eg.ProviderStartRefused("WATCHDOG")])
def test_guard_exceptions_classify_as_non_retryable_transport_failures(exc):
    failure_class = failures.classify_invocation(QueryOutcome(result=None, error=exc), breaker_tripped=False)
    assert failure_class == failures.TRANSPORT_PROCESS_SDK_EXCEPTION_WITHOUT_CAPTURED_TYPED_RESULT
    assert not failures.is_retryable(failure_class)
    assert isinstance(exc, RuntimeError) and not isinstance(exc, TimeoutError)


_RATE = FxRate(source="ecb-eurofxref-daily", rate_date="2026-09-17", retrieved_at_utc=NOW, usd_per_eur=Decimal("1.1554"))


@pytest.fixture
def ledger_conn(tmp_path):
    conn = ledger.open_ledger(tmp_path / "sentinel.sqlite3")
    with ledger.unit_of_work(conn):
        ledger.insert_run(conn, RunRecord(
            schema_version=1, run_id="r-1", run_kind="dev", status="RUNNING", started_at_utc=NOW,
            tasks_created=0, tasks_terminal=0, findings_new=0, findings_still_open=0, findings_resolved=0,
        ))
    yield conn
    conn.close()


def _request(n: int = 1) -> JudgmentRequest:
    return JudgmentRequest(surface=f"acme{n}/EVAL_RESULTS.md", check_class="missing-synthetic-label",
                           path="EVAL_RESULTS.md", text="# Results\n- Runs: 12\n")


def _judge(stub, request):
    with patch("agents.checker.harness.auth.assert_no_auth_override_risk", return_value=None):
        return stub.judge(request)


def _stub(ledger_conn, guarded):
    with patch("agents.checker.harness.auth.assert_no_auth_override_risk", return_value=None):
        return harness.CagedCheckerStub(
            run_id="r-1", conn=ledger_conn, coordinator=RunBudgetCoordinator(fx_rate=_RATE),
            clock=lambda: NOW, query_fn=guarded,
        )


def test_harness_attempt_two_never_reaches_provider_after_latch(tmp_path, ledger_conn, monkeypatch):
    monkeypatch.setattr(harness.anyio, "run", run_async)
    env = _Env(tmp_path)
    calls = []

    async def fake(check_class, reservation, state, user_prompt, model=None):
        calls.append(check_class)
        env.latch.trip("WATCHDOG", time.monotonic())  # a cause is latched during attempt 1
        budget_ceiling = SimpleNamespace(is_error=True, subtype="error_max_budget_usd", total_cost_usd=0.001,
                                         usage={"input_tokens": 1, "output_tokens": 1}, num_turns=1)
        return QueryOutcome(result=budget_ceiling, error=None)

    stub = _stub(ledger_conn, env.guard(fake))
    with pytest.raises(harness.CheckerAgentError):
        _judge(stub, _request(1))
    assert calls == ["missing-synthetic-label"]  # attempt 2 was refused before the provider
    rows = ledger.list_agent_calls_for_run(ledger_conn, "r-1")
    assert len(rows) == 2 and all(row.state == "FAILED" for row in rows)
    with pytest.raises(harness.CheckerAgentError):
        _judge(stub, _request(2))
    assert calls == ["missing-synthetic-label"]  # a later task starts no provider work either
    env.assert_finished_once()


def test_harness_timeout_is_not_retried(tmp_path, ledger_conn, monkeypatch):
    monkeypatch.setattr(harness.anyio, "run", run_async)
    env = _Env(tmp_path, stall_budget_ms=50)
    calls = []

    async def fake(check_class, reservation, state, user_prompt, model=None):
        calls.append(1)
        await asyncio.sleep(5)

    stub = _stub(ledger_conn, env.guard(fake))
    with pytest.raises(harness.CheckerAgentError):
        _judge(stub, _request(1))
    assert calls == [1] and env.outcomes() == ["TIMED_OUT"]
    assert len(ledger.list_agent_calls_for_run(ledger_conn, "r-1")) == 1
    env.assert_finished_once()

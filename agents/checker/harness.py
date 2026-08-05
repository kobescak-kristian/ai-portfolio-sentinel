"""Real caged checker agent entry point (dispatch q77-p3-a, section A):
builds the caged ``ClaudeAgentOptions``, runs one ``query()`` per
``JudgmentRequest``, and returns ``Sequence[ObservedFinding]`` — the
same shape ``NullJudgmentStub`` returns — so ``CagedCheckerStub`` drops
in unchanged as ``Deps.judgment`` for ``--judgment-mode agent``. No
changes were needed to ``checks/judgment/stubs.py``'s ``JudgmentStub``
Protocol, or to ``checks/judgment/stale_state.py`` /
``synthetic_label.py``.

Failure signaling: on any budget exhaustion, cage/auth refusal, or SDK
error, ``judge()`` *raises* ``CheckerAgentError`` rather than returning
an empty sequence — ``sentinel/pipeline.py`` already converts any
checker exception to ``Inconclusive`` -> ``DEAD_LETTER``, so a failed
judgment call is never silently indistinguishable from "nothing wrong
found" (an empty *successful* return is still a legitimate
``Confirmed([])`` result — same semantics as ``NullJudgmentStub``).
"""

from __future__ import annotations

import inspect
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional, Sequence

import anyio
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, create_sdk_mcp_server, query

from agents.checker import auth
from agents.checker.budget import (
    BudgetExhausted,
    Reservation,
    RunBudgetCoordinator,
    usd_to_charged_eur_micros,
)
from agents.checker.config import (
    AUTH_MODE_LABEL,
    MAX_TURNS,
    MCP_SERVER_NAME,
    MODEL,
    QUALIFIED_TOOL_NAME,
)
from agents.checker.fx import resolve_ecb_usd_per_eur
from agents.checker.prompts import build_system_prompt, build_user_prompt
from agents.checker.tools import CheckerToolState, build_emit_finding_tool
from checks.base import ObservedFinding
from checks.judgment.stubs import JudgmentRequest
from sentinel import ledger


class CheckerAgentError(RuntimeError):
    """Any condition under which a judgment call must not be treated
    as a completed, trustworthy result — budget exhaustion, an
    auth-override risk, an SDK-level error, or unrecoverable final
    usage. Always raised, never swallowed into an empty finding list."""


def build_options(check_class: str, reservation: Reservation) -> ClaudeAgentOptions:
    """The cage (dispatch section C): no built-in tools, exactly one
    qualified custom tool, a bounded turn count, a per-call USD
    ceiling derived from the run's EUR budget, and no inherited
    settings/subagents/skills. ``mcp_servers`` is attached by the
    caller once the tool is built for this specific request."""
    return ClaudeAgentOptions(
        model=MODEL,
        system_prompt=build_system_prompt(check_class),
        tools=[],  # disable every built-in tool (Read, Bash, Write, Edit, ...)
        allowed_tools=[QUALIFIED_TOOL_NAME],  # exactly the one custom tool
        max_turns=MAX_TURNS,
        max_budget_usd=reservation.sdk_max_budget_usd,
        setting_sources=[],  # no inherited user/project/local settings
        agents=None,  # no subagents
        skills=None,  # no skills
    )


async def run_query(
    check_class: str,
    reservation: Reservation,
    state: CheckerToolState,
    user_prompt: str,
) -> Optional[ResultMessage]:
    """The real SDK call. Kept as a free function (not a method) so
    tests can substitute an entirely different async callable via
    ``CagedCheckerStub.query_fn`` without touching ``claude_agent_sdk``
    at all — conftest.py's ``block_network`` fixture would fail any
    test that reached the real subprocess/network regardless."""
    server = create_sdk_mcp_server(
        name=MCP_SERVER_NAME, version="1.0.0", tools=[build_emit_finding_tool(state)]
    )
    options = build_options(check_class, reservation)
    options.mcp_servers = {MCP_SERVER_NAME: server}

    result: Optional[ResultMessage] = None
    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message
    return result


@dataclass
class CagedCheckerStub:
    """A real ``JudgmentStub`` implementation
    (``checks.judgment.stubs.JudgmentStub``) — constructed once per
    run in agent mode via ``build_caged_judgment_stub`` and wired into
    ``Deps.judgment`` in place of ``NullJudgmentStub``."""

    run_id: str
    conn: sqlite3.Connection
    coordinator: RunBudgetCoordinator
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    query_fn: Callable = field(default=run_query)

    def judge(self, request: JudgmentRequest) -> Sequence[ObservedFinding]:
        now = self.clock()
        task_key = f"{request.surface}::{request.check_class}"

        try:
            auth.assert_no_auth_override_risk()
        except auth.AuthOverrideRisk as exc:
            self._record_terminal(
                task_key=task_key,
                request=request,
                at_utc=now,
                state="REJECTED",
                rejection_reason=str(exc),
            )
            raise CheckerAgentError(str(exc)) from exc

        try:
            reservation = self.coordinator.reserve()
        except BudgetExhausted as exc:
            self._record_terminal(
                task_key=task_key,
                request=request,
                at_utc=now,
                state="EXHAUSTED",
                rejection_reason=str(exc),
            )
            raise CheckerAgentError(str(exc)) from exc

        call_id = self._insert_reserved(
            task_key=task_key, request=request, at_utc=now, reservation=reservation
        )

        state = CheckerToolState(request=request)
        user_prompt = build_user_prompt(request)
        try:
            # The real query_fn (run_query) is async, so production
            # always takes the anyio.run() path. Tests may inject a
            # plain sync callable instead — nothing in a fake needs to
            # await anything, and a plain call sidesteps an unrelated
            # Windows-specific interaction where even a fully local,
            # no-I/O event loop's self-pipe socketpair() trips
            # conftest.py's blanket network-connect guard.
            if inspect.iscoroutinefunction(self.query_fn):
                result = anyio.run(
                    self.query_fn, request.check_class, reservation, state, user_prompt
                )
            else:
                result = self.query_fn(request.check_class, reservation, state, user_prompt)
        except Exception as exc:  # noqa: BLE001 - any SDK/transport failure
            self.coordinator.commit_unresolved(reservation)
            self._finalize(
                call_id,
                state="FAILED",
                at_utc=self.clock(),
                charged_eur_micros=reservation.reserved_eur_micros,
                tool_attempts=state.tool_attempts,
                accepted=False,
                rejection_reason=f"{type(exc).__name__}: {exc}",
            )
            raise CheckerAgentError(f"checker agent call failed: {exc}") from exc

        if result is None or result.is_error or state.breaker_tripped():
            self.coordinator.commit_unresolved(reservation)
            if state.breaker_tripped():
                reason = "tool-call circuit breaker tripped"
            elif result is None:
                reason = "no result message returned"
            else:
                reason = f"SDK result error (subtype={result.subtype!r})"
            self._finalize(
                call_id,
                state="FAILED",
                at_utc=self.clock(),
                charged_eur_micros=reservation.reserved_eur_micros,
                sdk_turns=getattr(result, "num_turns", None),
                sdk_is_error=getattr(result, "is_error", None),
                sdk_subtype=getattr(result, "subtype", None),
                tool_attempts=state.tool_attempts,
                accepted=False,
                rejection_reason=reason,
            )
            raise CheckerAgentError(f"checker agent call did not complete cleanly: {reason}")

        usd_cost = result.total_cost_usd
        if usd_cost is None:
            # Successful result but no recoverable cost figure: treat
            # as unresolved usage, charge the full reservation (never
            # zero) — the binding decision applies to this case too.
            self.coordinator.commit_unresolved(reservation)
            charged = reservation.reserved_eur_micros
        else:
            charged = min(
                usd_to_charged_eur_micros(Decimal(str(usd_cost)), self.coordinator.fx_rate),
                reservation.reserved_eur_micros,
            )
            self.coordinator.commit(reservation, charged_eur_micros=charged)

        usage = result.usage or {}
        self._finalize(
            call_id,
            state="COMPLETED",
            at_utc=self.clock(),
            charged_eur_micros=charged,
            sdk_turns=result.num_turns,
            sdk_is_error=result.is_error,
            sdk_subtype=result.subtype,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            usd_cost_estimate=(str(usd_cost) if usd_cost is not None else None),
            tool_attempts=state.tool_attempts,
            accepted=bool(state.findings),
            rejection_reason=state.last_rejection_reason if not state.findings else None,
        )
        return tuple(state.findings)

    # -- ledger plumbing (main-ledger audit — no second database) -----

    def _insert_reserved(
        self, *, task_key: str, request: JudgmentRequest, at_utc: datetime, reservation: Reservation
    ) -> int:
        fx_rate = self.coordinator.fx_rate
        with ledger.unit_of_work(self.conn):
            return ledger.insert_agent_call_reserved(
                self.conn,
                run_id=self.run_id,
                task_key=task_key,
                surface=request.surface,
                check_class=request.check_class,
                model=MODEL,
                auth_mode=AUTH_MODE_LABEL,
                started_at_utc=at_utc,
                reserved_eur_micros=reservation.reserved_eur_micros,
                fx_source=fx_rate.source,
                fx_rate_date=fx_rate.rate_date,
                fx_retrieved_at_utc=fx_rate.retrieved_at_utc,
                fx_rate_decimal=str(fx_rate.usd_per_eur),
            )

    def _finalize(self, call_id: int, *, at_utc: datetime, **kwargs) -> None:
        with ledger.unit_of_work(self.conn):
            ledger.finalize_agent_call(self.conn, call_id, finished_at_utc=at_utc, **kwargs)

    def _record_terminal(
        self,
        *,
        task_key: str,
        request: JudgmentRequest,
        at_utc: datetime,
        state: str,
        rejection_reason: str,
    ) -> None:
        """For EXHAUSTED/REJECTED: nothing was ever reserved with the
        coordinator (reserve() itself failed, or was never attempted),
        so insert and finalize in one transaction, reserved=charged=0."""
        fx_rate = self.coordinator.fx_rate
        with ledger.unit_of_work(self.conn):
            call_id = ledger.insert_agent_call_reserved(
                self.conn,
                run_id=self.run_id,
                task_key=task_key,
                surface=request.surface,
                check_class=request.check_class,
                model=MODEL,
                auth_mode=AUTH_MODE_LABEL,
                started_at_utc=at_utc,
                reserved_eur_micros=0,
                fx_source=fx_rate.source,
                fx_rate_date=fx_rate.rate_date,
                fx_retrieved_at_utc=fx_rate.retrieved_at_utc,
                fx_rate_decimal=str(fx_rate.usd_per_eur),
            )
            ledger.finalize_agent_call(
                self.conn,
                call_id,
                state=state,
                finished_at_utc=at_utc,
                charged_eur_micros=0,
                tool_attempts=0,
                accepted=False,
                rejection_reason=rejection_reason,
            )


def build_caged_judgment_stub(
    *,
    run_id: str,
    db_path: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> CagedCheckerStub:
    """Factory used by ``sentinel/cli.py`` when ``--judgment-mode agent``
    is selected. Fails closed, before any model call and before
    ``execute_run`` creates any run row, if the auth-override check or
    FX resolution cannot succeed (``auth.AuthOverrideRisk`` /
    ``fx.FxResolutionError`` propagate to the caller)."""
    auth.assert_no_auth_override_risk()
    now = clock()
    fx_rate = resolve_ecb_usd_per_eur(now=now)
    conn = ledger.open_ledger(db_path)
    coordinator = RunBudgetCoordinator(fx_rate=fx_rate)
    return CagedCheckerStub(run_id=run_id, conn=conn, coordinator=coordinator, clock=clock)

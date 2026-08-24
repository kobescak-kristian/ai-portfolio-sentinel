"""Real caged checker agent entry point (dispatch q77-p3-a, section A):
builds the caged ``ClaudeAgentOptions``, runs ``query()`` per
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

ADR-0008 additions (dispatch q77-p3-adr8-impl-a):

* the terminal ``ResultMessage`` is captured OUT of the stream, so the
  SDK's trailing non-zero-exit exception can no longer destroy the one
  typed signal that identifies a per-call budget-ceiling event;
* failures are mechanically classified (``agents/checker/failures.py``)
  and exactly ONE class — a captured terminal subtype
  ``error_max_budget_usd`` — permits exactly ONE second invocation, in
  the same run, on an ordinary reservation from the same coordinator;
* each invocation gets FRESH tool state, so a failed attempt's findings
  are audit evidence only and never become live findings;
* terminal evidence is made durable BEFORE in-memory budget accounting
  advances, and known cost overshoot is accounted instead of clamped;
* if that in-memory accounting then fails, a run-lifetime latch on the
  stub fails every later judgment closed, so no further model
  invocation starts in the run on budget state known to be wrong.
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

from agents.checker import auth, failures
from agents.checker.auth import AuthProfile
from agents.checker.budget import (
    BudgetExhausted,
    Reservation,
    RunBudgetCoordinator,
    usd_to_charged_eur_micros,
)
from agents.checker.config import (
    HAIKU_ORDINARY,
    MAX_MODEL_ATTEMPTS_PER_TASK,
    MAX_TURNS,
    MCP_SERVER_NAME,
    MODEL,
    QUALIFIED_TOOL_NAME,
    ExecutionProfile,
)
from agents.checker.failures import QueryOutcome
from agents.checker.fx import resolve_ecb_usd_per_eur
from agents.checker.prompts import build_system_prompt, build_user_prompt
from agents.checker.tools import CheckerToolState, build_emit_finding_tool
from checks.base import ObservedFinding
from checks.judgment.stubs import JudgmentRequest
from sentinel import ledger
from sentinel.logs import redact


class CheckerAgentError(RuntimeError):
    """Any condition under which a judgment call must not be treated
    as a completed, trustworthy result — budget exhaustion, an
    auth-override risk, an SDK-level error, or unrecoverable final
    usage. Always raised, never swallowed into an empty finding list."""


class TerminalAccountingError(CheckerAgentError):
    """The call's terminal evidence was durably committed but in-memory
    budget accounting then failed. Fails closed: no further model
    invocation is started, and the durable audit row is never rewritten
    or deleted to make the two layers agree."""


def build_options(
    check_class: str, reservation: Reservation, model: str = MODEL
) -> ClaudeAgentOptions:
    """The cage (dispatch section C): no built-in tools, exactly one
    qualified custom tool, a bounded turn count, a per-call USD
    ceiling derived from the run's EUR budget, and no inherited
    settings/subagents/skills. ``mcp_servers`` is attached by the
    caller once the tool is built for this specific request.

    A second (ADR-0008) invocation is built through this very function
    from the same request and its own ordinary reservation, so it gets
    the same model, the same one-tool cage and the same turn allowance
    — only the SDK budget allowance differs, and only because it is
    derived from whatever run capacity actually remains.

    ``model`` defaults to the ordinary Haiku constant (dispatch
    q77-p5b-foundation-a: a non-default value is only ever supplied by
    a non-default ``ExecutionProfile``, threaded in via ``run_query``)."""
    return ClaudeAgentOptions(
        model=model,
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
    model: str = MODEL,
) -> QueryOutcome:
    """The real SDK call. Kept as a free function (not a method) so
    tests can substitute an entirely different async callable via
    ``CagedCheckerStub.query_fn`` without touching ``claude_agent_sdk``
    at all — conftest.py's ``block_network`` fixture would fail any
    test that reached the real subprocess/network regardless.

    Returns a ``QueryOutcome`` rather than an ``Optional[ResultMessage]``
    and never re-raises. That is the ADR-0008 information-loss fix: the
    pinned SDK delivers its terminal ResultMessage to the stream first
    and only then converts the CLI's deliberate non-zero exit into an
    untyped ``Exception``. Collecting the result in a local and letting
    that exception propagate — the previous behaviour — discarded the
    subtype, token counts and cost estimate that the terminal message
    had already carried. Both halves are returned side by side instead;
    the caller classifies from the typed half and never from prose."""
    server = create_sdk_mcp_server(
        name=MCP_SERVER_NAME, version="1.0.0", tools=[build_emit_finding_tool(state)]
    )
    options = build_options(check_class, reservation, model)
    options.mcp_servers = {MCP_SERVER_NAME: server}

    result: Optional[ResultMessage] = None
    try:
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, ResultMessage):
                result = message
    except Exception as exc:  # noqa: BLE001 - any SDK/transport failure
        return QueryOutcome(result=result, error=exc)
    return QueryOutcome(result=result, error=None)


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
    # Phase-5 (q77-p5b-foundation-a): which model this stub calls and
    # which auth profile it checks. Defaults reproduce today's ordinary
    # Haiku/local-OAuth behavior exactly for every existing caller.
    model: str = MODEL
    auth_profile: AuthProfile = field(default=auth.LOCAL_OAUTH)

    #: Run-lifetime fail-closed latch. Set once ``_advance_budget``
    #: catches a coordinator accounting failure, i.e. after this call's
    #: terminal evidence was already durably committed. From that point
    #: the in-memory budget state is known to be wrong, so no further
    #: model invocation may start in this run.
    _terminal_accounting_faulted: bool = field(default=False, init=False)

    def judge(self, request: JudgmentRequest) -> Sequence[ObservedFinding]:
        # Deterministic absent-file short-circuit (adr/0005). A
        # confirmed-absent file is already established deterministically
        # upstream by the three-state fetch contract (``ConfirmedAbsent``
        # in sentinel/inventory/base.py) and needs no model judgment, so
        # this returns the empty result *before* the auth check, before
        # any budget reservation or SDK allowance construction, and
        # before any model call — leaving no ``agent_calls`` row at all
        # for a request that never entered the model path. An empty
        # successful return is a legitimate ``Confirmed([])``, the same
        # semantics ``NullJudgmentStub`` has always had; it is not an
        # agent failure and never dead-letters. Every *actual* agent
        # call below keeps its unchanged fail-closed behavior.
        if request.text is None:
            return ()

        # Run-wide fail-closed latch. An earlier call in this run
        # committed its terminal evidence durably and then failed to
        # account it, so the coordinator's in-memory figures no longer
        # describe what was actually spent. Detected here — before the
        # auth check, before reserve(), before query_fn — so this
        # judgment makes ZERO model invocations. Nothing is written:
        # the faulted call's own terminal row is already durable and is
        # never rewritten to make the two layers agree.
        if self._terminal_accounting_faulted:
            raise TerminalAccountingError(
                "run-budget accounting faulted on an earlier judgment call in this run; "
                "no further model invocation is started"
            )

        now = self.clock()
        task_key = f"{request.surface}::{request.check_class}"

        try:
            self.auth_profile.check(None)
        except auth.AuthCheckFailure as exc:
            self._record_terminal(
                task_key=task_key,
                request=request,
                at_utc=now,
                state="REJECTED",
                rejection_reason=f"{failures.AUTH_OVERRIDE}: {exc}",
            )
            raise CheckerAgentError(str(exc)) from exc

        # ADR-0008 section 2: the loop bound IS the contract. At most
        # MAX_MODEL_ATTEMPTS_PER_TASK actual SDK invocations happen for
        # one logical judgment task, and the only way to reach a second
        # iteration is a cleanly classified SDK_BUDGET_CEILING.
        for attempt_index in range(MAX_MODEL_ATTEMPTS_PER_TASK):
            try:
                reservation = self.coordinator.reserve()
            except BudgetExhausted as exc:
                # No SDK call occurs here. The audit row records the
                # pre-call exhaustion and does NOT count as one of the
                # bounded model invocations.
                self._record_terminal(
                    task_key=task_key,
                    request=request,
                    at_utc=self.clock(),
                    state="EXHAUSTED",
                    rejection_reason=f"{failures.RUN_BUDGET_EXHAUSTED}: {exc}",
                )
                raise CheckerAgentError(str(exc)) from exc

            call_id = self._insert_reserved(
                task_key=task_key,
                request=request,
                at_utc=now if attempt_index == 0 else self.clock(),
                reservation=reservation,
            )

            # Fresh per-invocation host state: findings, within-call
            # dedup, breaker and the bounded tool-attempt buffer. A
            # failed attempt's accepted findings therefore cannot leak
            # into a retry's live result.
            state = CheckerToolState(request=request)
            user_prompt = build_user_prompt(request)
            outcome = self._invoke(request.check_class, reservation, state, user_prompt)

            failure_class = failures.classify_invocation(
                outcome, breaker_tripped=state.breaker_tripped()
            )
            completed = failure_class is None
            estimate = self._estimate_eur_micros(outcome.result)
            charged = failures.terminal_charge(
                completed=completed,
                reserved_eur_micros=reservation.reserved_eur_micros,
                estimate_eur_micros=estimate,
            )

            self._terminalize(
                call_id,
                completed=completed,
                failure_class=failure_class,
                outcome=outcome,
                tool_state=state,
                charged_eur_micros=charged,
                estimate_eur_micros=estimate,
            )
            # Durable first, in-memory second: the ledger transaction
            # above has committed before any reservation is released,
            # so a persistence failure can never leave the coordinator
            # believing a call settled while its row is still RESERVED.
            self._advance_budget(reservation, charged_eur_micros=charged)

            if completed:
                return tuple(state.findings)

            retry_available = (
                failures.is_retryable(failure_class)
                and attempt_index + 1 < MAX_MODEL_ATTEMPTS_PER_TASK
            )
            if not retry_available:
                raise CheckerAgentError(
                    "checker agent call did not complete cleanly: "
                    f"{failures.failure_reason(failure_class, outcome)}"
                )

        # Unreachable: the final iteration always returns or raises.
        raise CheckerAgentError("checker agent exhausted its bounded model attempts")

    # -- invocation / accounting helpers -------------------------------

    def _invoke(
        self,
        check_class: str,
        reservation: Reservation,
        state: CheckerToolState,
        user_prompt: str,
    ) -> QueryOutcome:
        """Run one actual SDK/model invocation and normalize whatever
        it produced into a ``QueryOutcome``.

        The real ``run_query`` is async, so production always takes the
        anyio.run() path. Tests may inject a plain sync callable
        instead — nothing in a fake needs to await anything, and a
        plain call sidesteps an unrelated Windows-specific interaction
        where even a fully local, no-I/O event loop's self-pipe
        socketpair() trips conftest.py's blanket network-connect guard.

        A fake (or a transport failure escaping any future query_fn)
        that raises is captured here rather than propagating, so every
        failure reaches the one mechanized classification path instead
        of bypassing it."""
        try:
            if inspect.iscoroutinefunction(self.query_fn):
                raw = anyio.run(
                    self.query_fn, check_class, reservation, state, user_prompt, self.model
                )
            else:
                raw = self.query_fn(check_class, reservation, state, user_prompt, self.model)
        except Exception as exc:  # noqa: BLE001 - any SDK/transport failure
            return QueryOutcome(result=None, error=exc)
        if isinstance(raw, QueryOutcome):
            return raw
        return QueryOutcome(result=raw, error=None)

    def _estimate_eur_micros(self, result: object) -> Optional[int]:
        """The SDK's own reported cost, conservatively converted. This
        is an ESTIMATE / model-equivalent consumption signal — never
        authoritative provider billing. ``None`` means no final figure
        was recoverable, which drives the conservative
        full-reservation charge rather than a retry."""
        if result is None:
            return None
        usd_cost = getattr(result, "total_cost_usd", None)
        if usd_cost is None:
            return None
        return usd_to_charged_eur_micros(Decimal(str(usd_cost)), self.coordinator.fx_rate)

    def _advance_budget(self, reservation: Reservation, *, charged_eur_micros: int) -> None:
        try:
            self.coordinator.commit(reservation, charged_eur_micros=charged_eur_micros)
        except Exception as exc:  # noqa: BLE001 - must not be swallowed
            # Latch BEFORE raising: this stub must not start another
            # model invocation for any later task in the same run.
            self._terminal_accounting_faulted = True
            raise TerminalAccountingError(
                "terminal evidence was durably recorded but run-budget accounting failed: "
                f"{type(exc).__name__}"
            ) from exc

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
                model=self.model,
                auth_mode=self.auth_profile.label,
                started_at_utc=at_utc,
                reserved_eur_micros=reservation.reserved_eur_micros,
                fx_source=fx_rate.source,
                fx_rate_date=fx_rate.rate_date,
                fx_retrieved_at_utc=fx_rate.retrieved_at_utc,
                fx_rate_decimal=str(fx_rate.usd_per_eur),
            )

    def _terminalize(
        self,
        call_id: int,
        *,
        completed: bool,
        failure_class: Optional[str],
        outcome: QueryOutcome,
        tool_state: CheckerToolState,
        charged_eur_micros: int,
        estimate_eur_micros: Optional[int],
    ) -> None:
        """Persist this invocation's buffered tool-attempt audit AND
        finalize its ``agent_calls`` row in ONE transaction.

        ``unit_of_work`` rolls back on any exception, so a failure on
        either leg aborts both: the call stays visibly RESERVED and is
        never finalized with its per-proposal audit silently dropped.
        That is the fail-closed half of ADR-0008 section 5.

        Call-level ``accepted`` stays conservative. A FAILED call is
        ``accepted=false`` even when one of its proposals passed host
        validation before the later SDK failure — the per-attempt audit
        is where that accepted proposal survives, and a failed call must
        never read as successful output."""
        result = outcome.result
        usage = getattr(result, "usage", None) or {}
        if completed:
            state_name = "COMPLETED"
            accepted = bool(tool_state.findings)
            reason = tool_state.last_rejection_reason if not tool_state.findings else None
        else:
            state_name = "FAILED"
            accepted = False
            reason = failures.failure_reason(failure_class, outcome)

        with ledger.unit_of_work(self.conn):
            ledger.insert_tool_attempts(self.conn, call_id, tool_state.attempts)
            ledger.finalize_agent_call(
                self.conn,
                call_id,
                state=state_name,
                finished_at_utc=self.clock(),
                charged_eur_micros=charged_eur_micros,
                sdk_turns=getattr(result, "num_turns", None),
                sdk_is_error=getattr(result, "is_error", None),
                sdk_subtype=getattr(result, "subtype", None),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                usd_cost_estimate=(
                    str(getattr(result, "total_cost_usd", None))
                    if estimate_eur_micros is not None
                    else None
                ),
                tool_attempts=tool_state.tool_attempts,
                accepted=accepted,
                rejection_reason=redact(reason) if reason else None,
            )

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
        so insert and finalize in one transaction, reserved=charged=0.
        No SDK invocation occurred, so there is no tool-attempt audit
        and this row never counts toward the model-attempt bound."""
        fx_rate = self.coordinator.fx_rate
        with ledger.unit_of_work(self.conn):
            call_id = ledger.insert_agent_call_reserved(
                self.conn,
                run_id=self.run_id,
                task_key=task_key,
                surface=request.surface,
                check_class=request.check_class,
                model=self.model,
                auth_mode=self.auth_profile.label,
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
                rejection_reason=redact(rejection_reason),
            )


def build_caged_judgment_stub(
    *,
    run_id: str,
    db_path: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    profile: ExecutionProfile = HAIKU_ORDINARY,
    auth_profile: AuthProfile = auth.LOCAL_OAUTH,
) -> CagedCheckerStub:
    """Factory used by ``sentinel/cli.py`` when ``--judgment-mode agent``
    is selected. Fails closed, before any model call and before
    ``execute_run`` creates any run row, if the auth check or FX
    resolution cannot succeed (``auth.AuthCheckFailure`` /
    ``fx.FxResolutionError`` propagate to the caller).

    ``sentinel/cli.py`` calls this with neither ``profile`` nor
    ``auth_profile`` overridden, so its defaults — ``HAIKU_ORDINARY``
    and ``auth.LOCAL_OAUTH`` — are what make ordinary
    ``--judgment-mode agent`` behavior byte-for-byte unchanged
    (dispatch q77-p5b-foundation-a). A non-default profile is only ever
    supplied by a dedicated, separate entry point outside this
    dispatch's scope."""
    auth_profile.check(None)
    now = clock()
    fx_rate = resolve_ecb_usd_per_eur(now=now)
    conn = ledger.open_ledger(db_path)
    coordinator = RunBudgetCoordinator(
        fx_rate=fx_rate,
        total_eur_micros=profile.run_budget_eur_micros,
        max_per_call_reserve_eur_micros=profile.max_per_call_reserve_eur_micros,
        sdk_allowance_safety_margin=profile.sdk_allowance_safety_margin,
    )
    return CagedCheckerStub(
        run_id=run_id,
        conn=conn,
        coordinator=coordinator,
        clock=clock,
        model=profile.model,
        auth_profile=auth_profile,
    )

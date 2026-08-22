"""The one place where the generic loop supervisor meets Sentinel.

``runner/loop.py`` and ``runner/breakers.py`` are domain-free and stay
that way. This module is the opposite: it is the integration boundary,
and it is allowed to import the first-party surfaces it needs —
``sentinel.*`` for run execution and recovery, ``telemetry.cost_ledger``
for durable cost reconstruction, and ``agents.checker.budget`` for the
reduced allowance (owner amendment, 2026-08-22).

What it must NOT touch, in this session or by accident later:
``claude_agent_sdk``, ``agents.checker.harness``, ``CagedCheckerStub``,
any query/run_query surface, any provider or auth execution path, or any
model-capable runner path. No provider-capable bounded-loop execution
exists here. ``tests/test_read_only_boundary.py`` enforces this
statically, because a boundary that is only documented is a boundary
that erodes.

Three jobs:

1. bind the supervisor's ``planned_run_id`` to ``RunConfig.run_id``, so
   the ADR-0010 section 4 invariant reaches the pipeline unchanged;
2. reconstruct durable accounted consumption from committed
   ``CostRow``s — never from an in-memory counter;
3. construct the reduced ``RunBudgetCoordinator`` and refuse fail-closed
   if that reduced allowance cannot be enforced.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional, Sequence

from agents.checker.budget import RunBudgetCoordinator
from agents.checker.config import RUN_BUDGET_EUR_MICROS
from runner.loop import RunProbe
from sentinel import costs, ledger
from sentinel.config import RunConfig
from sentinel.ids import RandomIdFactory, SystemClock
from sentinel.logs import RunLogger
from sentinel.pipeline import Deps, RunOutcome, execute_run
from telemetry.cost_ledger import read_cost_rows

# Re-exported so callers configure the loop from one place rather than
# reaching into agents.checker.config themselves.
PER_RUN_CAP_EUR_MICROS = RUN_BUDGET_EUR_MICROS

# Re-exported so runner/__main__.py can wire a loop without importing a
# Sentinel module itself — this file stays the single integration
# boundary, which is what the static boundary test asserts.
__all__ = [
    "AllowanceNotEnforceable",
    "PER_RUN_CAP_EUR_MICROS",
    "ProviderModeRefused",
    "RandomIdFactory",
    "RunConfig",
    "RunLogger",
    "SentinelIterationExecutor",
    "SystemClock",
    "build_iteration_budget",
    "durable_accounted_cost",
]


class AllowanceNotEnforceable(RuntimeError):
    """The reduced iteration allowance could not be enforced downward.
    ADR-0010 section 2: the loop refuses the iteration fail-closed, and
    the normal EUR 0.75 allowance is never silently restored."""


class ProviderModeRefused(RuntimeError):
    """A provider/agent-capable loop was requested. Not authorised in
    this implementation — refused before any provider construction."""


def build_iteration_budget(
    *, allowance_eur_micros: int, fx_rate: object
) -> RunBudgetCoordinator:
    """Construct the existing run-budget coordinator with the REDUCED
    loop allowance rather than the full per-run cap.

    ``RunBudgetCoordinator`` already accepts ``total_eur_micros``, so
    propagation needs no change to ``agents/checker/*`` at all — the
    coordinator is simply built with ``min(per_run_cap,
    remaining_loop_budget)``.

    ``fx_rate`` is injected. In a model-free context it is a
    deterministic fake; nothing here contacts a provider, resolves a
    live FX rate, or constructs an SDK client.

    Fail-closed conditions, both of which refuse rather than fall back:
    a non-positive allowance, and an allowance above the per-run cap
    (which would mean the reduction was computed wrongly and the full
    EUR 0.75 was about to be restored)."""
    if allowance_eur_micros <= 0:
        raise AllowanceNotEnforceable(
            f"reduced allowance {allowance_eur_micros} is not positive; refusing fail-closed"
        )
    if allowance_eur_micros > PER_RUN_CAP_EUR_MICROS:
        raise AllowanceNotEnforceable(
            f"reduced allowance {allowance_eur_micros} exceeds the per-run cap "
            f"{PER_RUN_CAP_EUR_MICROS}; the loop allowance is never raised"
        )
    coordinator = RunBudgetCoordinator(
        fx_rate=fx_rate, total_eur_micros=allowance_eur_micros
    )
    # Proof, not decoration: if the constructed coordinator does not
    # actually carry the reduced figure, the propagation did not happen
    # and the iteration must not start.
    if coordinator.remaining_eur_micros() != allowance_eur_micros:
        raise AllowanceNotEnforceable(
            "constructed coordinator does not carry the reduced allowance"
        )
    return coordinator


def durable_accounted_cost(cost_ledger_path: Path, run_ids: Sequence[str]) -> int:
    """ADR-0010 section 2's accounting source: durable ``CostRow``s
    belonging to the loop's own iteration run ids.

    Known overshoot is summed in full and never clamped — a row above
    the per-run cap is a truthful record of spend that already happened.
    A missing or unparseable ledger contributes nothing rather than
    raising, which keeps a cost read from masking the loop's real stop
    reason; the loop tables retain what was accounted at each finalize."""
    wanted = set(run_ids)
    if not wanted or not Path(cost_ledger_path).exists():
        return 0
    try:
        rows = read_cost_rows(Path(cost_ledger_path))
    except ValueError:
        return 0
    return sum(row.cost_eur_micros for row in rows if row.run_id in wanted)


@dataclass
class SentinelIterationExecutor:
    """``runner.loop.IterationExecutor`` bound to ``execute_run``.

    ``base_config`` carries every non-identity setting for an iteration;
    the executor overrides only ``run_id``, with the supervisor's exact
    ``planned_run_id``. ``deps_factory`` builds the per-iteration
    ``Deps``; in stub mode that is the plain deterministic control plane
    and no budget coordinator exists at all, because a stub-mode run
    makes zero model calls.
    """

    base_config: RunConfig
    deps_factory: Optional[Callable[[str, int], Deps]] = None
    # Records what the supervisor actually asked for, so a test can
    # prove the reduced allowance reached this seam.
    allowances_seen: list[int] = None  # type: ignore[assignment]
    executed_run_ids: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.allowances_seen is None:
            self.allowances_seen = []
        if self.executed_run_ids is None:
            self.executed_run_ids = []

    # --- probing ----------------------------------------------------------

    def probe(self, planned_run_id: str) -> RunProbe:
        conn = ledger.open_ledger(self.base_config.db_path)
        try:
            run = ledger.get_run(conn, planned_run_id)
            if run is None:
                return RunProbe(status=None, outputs_complete=False)
            if run.status == "RUNNING":
                return RunProbe(status="RUNNING", outputs_complete=False)
            from sentinel.report import is_section_complete

            outputs_complete = is_section_complete(
                self.base_config.findings_path, planned_run_id
            ) and costs.has_cost_row_for_run(
                self.base_config.cost_ledger_path, planned_run_id
            )
            return RunProbe(status=run.status, outputs_complete=outputs_complete)
        finally:
            conn.close()

    # --- execution and recovery -------------------------------------------

    def execute(self, planned_run_id: str, *, allowance_eur_micros: int) -> str:
        """Recovery case A and the normal path. The exact
        ``planned_run_id`` becomes ``RunConfig.run_id``, so the run the
        pipeline creates is the run the loop already committed to."""
        self.allowances_seen.append(allowance_eur_micros)
        self.executed_run_ids.append(planned_run_id)
        config = replace(self.base_config, run_id=planned_run_id)
        deps = (
            self.deps_factory(planned_run_id, allowance_eur_micros)
            if self.deps_factory is not None
            else None
        )
        outcome: RunOutcome = execute_run(config, deps)
        return outcome.status

    def recover_interrupted(self, planned_run_id: str) -> str:
        """Recovery case C. Uses Sentinel's OWN interrupted-run
        recovery (``recover_interrupted_runs``), which sweeps the run's
        non-terminal tasks and closes it FAILED. No replacement run id
        is invented, and the recovered terminal run is the iteration's
        result. Its outputs are then reconciled through the existing
        terminal-output path."""
        from datetime import datetime, timezone

        from sentinel.pipeline import recover_interrupted_runs

        conn = ledger.open_ledger(self.base_config.db_path)
        try:
            with RunLogger(self.base_config.log_path) as logger:
                recover_interrupted_runs(
                    conn, now=datetime.now(timezone.utc), logger=logger
                )
                from sentinel.pipeline import reconcile_terminal_run_outputs

                reconcile_terminal_run_outputs(conn, self.base_config, logger=logger)
            run = ledger.get_run(conn, planned_run_id)
            if run is None or run.status == "RUNNING":
                raise RuntimeError(
                    f"interrupted-run recovery did not make {planned_run_id!r} terminal"
                )
            return run.status
        finally:
            conn.close()

    def reconcile_outputs(self, planned_run_id: str) -> None:
        """Recovery case D. Delegates to the existing terminal-output
        reconciliation, which backfills FINDINGS.md and the CostRow for
        an already-terminal run. No rerun, no second cost source."""
        from sentinel.pipeline import reconcile_terminal_run_outputs

        conn = ledger.open_ledger(self.base_config.db_path)
        try:
            with RunLogger(self.base_config.log_path) as logger:
                reconcile_terminal_run_outputs(conn, self.base_config, logger=logger)
        finally:
            conn.close()

    # --- cost -------------------------------------------------------------

    def accounted_cost(self, run_ids: Sequence[str]) -> int:
        return durable_accounted_cost(self.base_config.cost_ledger_path, run_ids)

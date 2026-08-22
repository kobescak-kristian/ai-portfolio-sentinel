#!/usr/bin/env python
"""Phase-4 bounded-loop technical gate (ADR-0010 section 7, dispatch
q77-p4-gate-a).

**MODEL-FREE.** No Haiku. No Sonnet. No provider contact. No SDK client.
No live GitHub inventory. No external HTTP. No agent mode. The gate proves
ADR-0010's loop controls mechanically, using seeded model-free faults and
the real landed supervisor over real durable state. Frozen fixture data is
READ; nothing under ``fixtures/`` or ``evals/`` is written.

This module is the JUDGE. It is frozen BEFORE the judged gate is executed,
so the executing session cannot decide what counts: ``PREDICATE_IDS`` is a
closed tuple, an unknown or duplicated predicate is refused, and a missing
predicate forces overall FAIL. Exit 0 happens only when EVERY frozen
predicate PASSes — never because most of them did.

Four legs, per ADR-0010 section 7:

* **Leg 1** — normal N = 10 through the REAL Sentinel integration
  (``run_loop`` + ``SqliteLoopStateStore`` + ``SentinelIterationExecutor``,
  ``source=fixtures``, ``judgment_mode=stub``): iteration identity,
  contiguity, terminal runs and tasks, one CostRow per run, continuity
  across iterations, normal completion, exit 0.
* **Leg 2** — the cost breaker at the fixed 750000 micro-EUR ceiling, five
  cases covering section 7 leg 2's consequences a-g, including the
  deliberate strict-``>`` versus remaining-``<= 0`` asymmetry and the
  reduced-allowance propagation seam.
* **Leg 3** — the consecutive-failure breaker: trip at exactly three, the
  reset sequence, terminal-boundary precedence, and all four parts of the
  section-5 alert contract.
* **Leg 4** — crash/recovery for the section-4 invariant: the primary
  terminal-run-before-finalize seam, the no-run-yet case (4A) and the
  terminal-output reconciliation case (4D).

Every case renders one ITERATION_LOG section and then CHECKS IT BACK: the
written bytes are reread from disk, reparsed, and compared field by field
against a freshly queried durable snapshot. A rendered figure that does not
match the ledger fails the gate.

**Cost cross-check literals.** ``LOOP_CEILING_EUR_MICROS``,
``FAILURE_THRESHOLD`` and ``PER_RUN_CAP_EUR_MICROS`` are restated here as
local literals and deliberately NOT imported from ``runner.breakers``, for
the same anti-tautology reason ``scripts/run_phase3_dev_gate.py`` restates
its own cost caps: a judge that imports the enforcement mechanism's
constants agrees with it by construction instead of checking it.
``tests/test_phase4_gate.py`` pins these against ``runner.breakers`` so they
cannot silently drift.

**This script does not decide policy.** It refuses to expose any operator
option that changes the loop ceiling, the failure threshold, the predicate
set or the model/provider mode. ADR-0010 section 2 forbids such a flag and
this file offers none.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from contracts.schemas import CostRow, RunRecord  # noqa: E402
from runner.iteration_log import (  # noqa: E402
    PHASE4_FAILURE_ALERT,
    SEEDED_FAULT,
    SYNTHETIC,
    IterationLogError,
    IterationMachineRow,
    SectionMeta,
    append_section,
    iteration_log_sha256,
    parse_sections,
    render_section,
)
from runner.loop import FINALIZED, LoopConfig, LoopHooks, RunProbe, run_loop  # noqa: E402
from runner.sentinel_adapter import (  # noqa: E402
    SentinelIterationExecutor,
    build_iteration_budget,
    durable_accounted_cost,
)
from runner.state import SqliteLoopStateStore, open_loop_state  # noqa: E402
from sentinel import ledger  # noqa: E402
from sentinel.config import RunConfig  # noqa: E402
from sentinel.ids import FrozenClock  # noqa: E402
from sentinel.logs import RunLogger, redact  # noqa: E402
from sentinel.pipeline import Deps, RunHooks  # noqa: E402
from telemetry.cost_ledger import append_cost_row  # noqa: E402

FIXTURES_ROOT = REPO_ROOT / "fixtures" / "repos"

# --- frozen gate contract ---------------------------------------------------

SCHEMA_VERSION = 1
GATE = "phase4_bounded_loop"
GATE_CONTRACT = "ADR-0010-section-7"

#: ADR-0010 section 2's fixed Phase-4 loop ceiling. Restated as a literal
#: (see the module docstring) and never raisable from the command line.
LOOP_CEILING_EUR_MICROS = 750_000
#: ADR-0010 section 1's threshold: the breaker trips at exactly three.
FAILURE_THRESHOLD = 3
#: The existing, unchanged EUR 0.75 per-run cap.
PER_RUN_CAP_EUR_MICROS = 750_000
#: ADR-0010 section 7 leg 1.
LEG1_ITERATIONS = 10

#: The gate is model-free by construction, so both of these are facts about
#: this run, not aspirations: nothing here can reach a provider.
MODEL_CALLS = 0
PROVIDER_SPEND_EUR_MICROS = 0

#: ADR-0010 section 6's exit-code mapping, restated locally for the same
#: anti-tautology reason as the cost literals. The self-check compares the
#: rendered exit code against THIS map and the durable stop reason; importing
#: ``runner.breakers.EXIT_CODES`` would compare the runner's derivation
#: against itself and prove nothing.
EXPECTED_EXIT_CODES: dict[str, int] = {
    "COMPLETED_ITERATION_CAP": 0,
    "COST_BREAKER_TRIPPED": 1,
    "CONSECUTIVE_FAILURE_BREAKER_TRIPPED": 1,
    "LOOP_ABORTED_ERROR": 1,
}

PASS = "PASS"
FAIL = "FAIL"

#: The CLOSED predicate set. ``q77-p4-gate-exec-a`` must not dynamically
#: decide what counts, so this tuple is frozen here, before the judged gate
#: runs. It is deliberately MORE granular than ADR-0010 section 7's prose in
#: two places (``LEG1_NORMAL_N10_ALL_FINALIZED``,
#: ``LEG1_NORMAL_N10_COST_WITHIN_CEILING``) so that every section-7 leg-1
#: requirement maps to a named predicate; it is nowhere less complete.
PREDICATE_IDS: tuple[str, ...] = (
    "LEG1_NORMAL_N10_ITERATION_COUNT",
    "LEG1_NORMAL_N10_INDEX_CONTIGUITY",
    "LEG1_NORMAL_N10_ALL_FINALIZED",
    "LEG1_NORMAL_N10_IDENTITY_UNIQUE",
    "LEG1_NORMAL_N10_RUNS_TERMINAL",
    "LEG1_NORMAL_N10_TASKS_TERMINAL",
    "LEG1_NORMAL_N10_ONE_COSTROW_PER_RUN",
    "LEG1_NORMAL_N10_CONTINUITY",
    "LEG1_NORMAL_N10_COST_WITHIN_CEILING",
    "LEG1_NORMAL_N10_STOP_REASON",
    "LEG1_NORMAL_N10_EXIT_ZERO",
    "LEG2_749999_MIDLOOP_CONTINUES",
    "LEG2_REDUCED_ALLOWANCE_PROPAGATED",
    "LEG2_EXACT_CAP_MIDLOOP_REFUSED",
    "LEG2_NO_NEXT_RUN_AT_EXACT_CAP",
    "LEG2_OVERSHOOT_TRIPS",
    "LEG2_OVERSHOOT_FULL_NOT_CLAMPED",
    "LEG2_NO_NEXT_RUN_AFTER_OVERSHOOT",
    "LEG2_TERMINAL_EXACT_CAP_NORMAL",
    "LEG2_TERMINAL_OVERSHOOT_TRIPS",
    "LEG3_TRIP_AT_THREE",
    "LEG3_NO_FOURTH_ITERATION",
    "LEG3_FOUR_PART_ALERT",
    "LEG3_RESET_SEQUENCE",
    "LEG3_TERMINAL_STREAK_PRECEDENCE",
    "LEG4_TERMINAL_BEFORE_FINALIZE_ADOPTED",
    "LEG4_SAME_PLANNED_RUN_ID",
    "LEG4_NO_DUPLICATE_RUN",
    "LEG4_NO_SKIPPED_ITERATION",
    "LEG4_INTENT_BEFORE_RUN_REUSED",
    "LEG4_TERMINAL_OUTPUTS_RECONCILED",
    "ITERATION_LOG_MATCHES_DURABLE_STATE",
    "PUBLIC_OUTPUT_CLEAN",
)

#: Which predicates each leg owns. A leg that raises records ITS OWN
#: predicates FAIL and the remaining legs still run — no frozen predicate
#: may disappear because an earlier case failed.
LEG_PREDICATES: dict[str, tuple[str, ...]] = {
    "LEG1": tuple(p for p in PREDICATE_IDS if p.startswith("LEG1_")),
    "LEG2": tuple(p for p in PREDICATE_IDS if p.startswith("LEG2_")),
    "LEG3": tuple(p for p in PREDICATE_IDS if p.startswith("LEG3_")),
    "LEG4": tuple(p for p in PREDICATE_IDS if p.startswith("LEG4_")),
}

#: A fixed synthetic epoch for every loop clock, so machine rows are
#: deterministic. It is deliberately not a real execution timestamp.
CLOCK_EPOCH = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

_SOURCE_SHA_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
#: Ids and case names in the artifact. No underscore, for the same reason
#: ``runner.iteration_log`` excludes it: it makes ``ghp_``/``github_pat_``
#: unrepresentable without this file owning a notion of what a secret is.
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,127}")
#: Closed-vocabulary values (stop reasons, leg names, PASS/FAIL). These are
#: SCREAMING_SNAKE by convention and therefore need the underscore; the
#: leading-uppercase requirement keeps the lowercase secret prefixes out.
_ENUM_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")

#: Literal raw-byte backstops. Not a second path/secret taxonomy — the
#: structured schema above is what actually enforces hygiene. These are the
#: belt-and-braces substrings that must never appear in public evidence
#: whatever else happened: a traceback block, an environment dump, or raw
#: provider/auth material.
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "Traceback (most recent call last)",
    "os.environ",
    "ANTHROPIC_",
    "CLAUDE_CODE_",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_PAT",
    "Authorization:",
    "Bearer ",
    "ghp_",
    "github_pat_",
    "-----BEGIN",
)


class GateContractError(RuntimeError):
    """The gate's own frozen contract was violated — an unknown predicate,
    a duplicate predicate, or an artifact that does not satisfy its closed
    schema. This is a defect in the gate, not a finding about the runner."""


class _SimulatedProcessLoss(BaseException):
    """Simulated process loss for the ADR-0010 section 7 leg 4 seams.

    Deliberately a direct ``BaseException`` subclass. ``run_loop`` catches
    ordinary ``Exception`` and fails closed to ``LOOP_ABORTED_ERROR``, and
    ``execute_run`` contains ``Exception`` the same way; a ``RuntimeError``
    double would therefore be swallowed and would prove nothing about a real
    SIGKILL or power loss. This escapes both, exactly as a real crash
    would."""


# --- predicate recording ----------------------------------------------------


def _safe_detail(value: object) -> str:
    """The ONLY free text permitted anywhere near public evidence, and it is
    sanitized with the repository's existing hygiene mechanism BEFORE it is
    inserted — never cleaned up afterwards, and never applied to a whole
    serialized object."""
    return redact(str(value))


@dataclass
class PredicateRecorder:
    """Closed-set predicate bookkeeping.

    Refuses an unknown id (the executing session cannot invent a predicate),
    refuses a duplicate id (a predicate cannot be recorded twice and quietly
    overwritten), and reports every frozen id that was never recorded as a
    FAIL rather than omitting it."""

    _results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __contains__(self, predicate_id: str) -> bool:
        return predicate_id in self._results

    def record(self, predicate_id: str, passed: bool, **evidence: Any) -> None:
        if predicate_id not in PREDICATE_IDS:
            raise GateContractError(
                f"{predicate_id!r} is not in the frozen predicate set; the gate's "
                "predicate set is closed and may not be extended at run time"
            )
        if predicate_id in self._results:
            raise GateContractError(
                f"{predicate_id!r} was already recorded; a frozen predicate is "
                "recorded exactly once"
            )
        row: dict[str, Any] = {"id": predicate_id, "result": PASS if passed else FAIL}
        detail = evidence.pop("detail", None)
        if detail is not None:
            row["detail"] = _safe_detail(detail)
        if evidence:
            row["evidence"] = _public_evidence(evidence)
        self._results[predicate_id] = row

    def record_missing_as_failed(self, predicate_ids: Iterable[str], detail: object) -> None:
        for predicate_id in predicate_ids:
            if predicate_id not in self._results:
                self.record(predicate_id, False, detail=detail)

    def recorded(self) -> list[dict[str, Any]]:
        """Only the predicates recorded so far, in frozen order. The hygiene
        scan runs against these, so it covers every sanitized diagnostic
        except its own — which is sanitized by construction."""
        return [self._results[p] for p in PREDICATE_IDS if p in self._results]

    def results(self) -> list[dict[str, Any]]:
        """Every frozen predicate, in frozen order. One that was never
        recorded appears as FAIL — a missing predicate can never be read as
        a silent pass."""
        rows: list[dict[str, Any]] = []
        for predicate_id in PREDICATE_IDS:
            rows.append(
                self._results.get(
                    predicate_id,
                    {
                        "id": predicate_id,
                        "result": FAIL,
                        "detail": "frozen predicate was never recorded",
                    },
                )
            )
        return rows

    def overall(self) -> str:
        rows = self.results()
        return PASS if all(row["result"] == PASS for row in rows) else FAIL


def _public_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Optional per-predicate evidence, restricted to public-safe scalars.
    Strings must satisfy the identifier/enum shape; anything narrative
    belongs in ``detail``, which is sanitized."""
    safe: dict[str, Any] = {}
    for key, value in sorted(evidence.items()):
        if isinstance(value, bool) or isinstance(value, int) or value is None:
            safe[key] = value
        elif isinstance(value, str):
            if not (_IDENTIFIER_RE.fullmatch(value) or _ENUM_RE.fullmatch(value)):
                raise GateContractError(
                    f"evidence field {key!r} is neither a public-safe identifier "
                    "nor a closed-vocabulary value; use the sanitized 'detail' "
                    "field for free text"
                )
            safe[key] = value
        elif isinstance(value, (list, tuple)):
            safe[key] = [_public_evidence({"item": v})["item"] for v in value]
        else:
            raise GateContractError(f"evidence field {key!r} has unsupported type")
    return safe


# --- isolated per-case working state ---------------------------------------


@dataclass(frozen=True)
class CaseEnv:
    """One case's isolated durable state. Every path lives under the gate's
    internal temporary work root, which never appears in public output."""

    db_path: Path
    findings_path: Path
    log_path: Path
    cost_ledger_path: Path


def _case_env(work_root: Path, name: str) -> CaseEnv:
    root = work_root / name
    root.mkdir(parents=True, exist_ok=False)
    return CaseEnv(
        db_path=root / "loop.sqlite3",
        findings_path=root / "FINDINGS.md",
        log_path=root / "loop.jsonl",
        cost_ledger_path=root / "cost_ledger.jsonl",
    )


def _clock() -> FrozenClock:
    return FrozenClock(ticks=[CLOCK_EPOCH + timedelta(seconds=i) for i in range(2000)])


class GateRunIds:
    """Deterministic ``planned_run_id``s so evidence can name exactly which
    runs were and were not created. Hyphenated, so every id satisfies the
    ITERATION_LOG's closed identifier charset."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.issued: list[str] = []

    def new_run_id(self) -> str:
        value = f"{self.prefix}-{len(self.issued):03d}"
        self.issued.append(value)
        return value


def _seed_run(conn: sqlite3.Connection, run_id: str, status: str) -> None:
    """A REAL ``runs`` row through the ledger's own primitives.
    ``loop_iterations.bound_run_id`` is foreign-keyed to ``runs(run_id)``, so
    a dictionary stand-in would side-step the integrity SQLite actually
    enforces."""
    with ledger.unit_of_work(conn):
        ledger.insert_run(
            conn,
            RunRecord(
                schema_version=1,
                run_id=run_id,
                run_kind="dev",
                status="RUNNING",
                started_at_utc=CLOCK_EPOCH,
                tasks_created=0,
                tasks_terminal=0,
                findings_new=0,
                findings_still_open=0,
                findings_resolved=0,
            ),
        )
        ledger.close_run(
            conn,
            run_id,
            status=status,
            finished_at_utc=CLOCK_EPOCH + timedelta(hours=1),
            counts=ledger.RunCounts(0, 0, 0, 0),
        )


def _write_cost_row(path: Path, run_id: str, micros: int) -> None:
    """A REAL durable ``CostRow`` — ADR-0010 section 2's accounting source.
    Seeding cost here rather than in a counter is the point: a seeded
    overspend is indistinguishable from a real one as far as the loop is
    concerned, because both are reconstructed from committed records."""
    append_cost_row(
        path,
        CostRow(
            schema_version=1,
            run_id=run_id,
            recorded_at_utc=CLOCK_EPOCH,
            run_kind="dev",
            model="none-deterministic",
            input_tokens=0,
            output_tokens=0,
            cost_eur_micros=micros,
        ),
    )


@dataclass
class SeededIterationExecutor:
    """A gate-local ``runner.loop.IterationExecutor`` with seeded terminal
    outcomes, for legs 2 and 3.

    It is a fault injector, not a Sentinel simulator — leg 1 and leg 4 use
    the real adapter. It still receives real ``planned_run_id``s from
    ``run_loop``, writes a real terminal ``runs`` row and a real ``CostRow``
    per iteration, and answers every probe and cost query from that durable
    state through ``durable_accounted_cost``. No in-memory cumulative
    counter is authoritative anywhere.

    Model-free by construction: no network, no SDK, no provider, no query
    surface, and no path around the supervisor or its durable loop state."""

    conn: sqlite3.Connection
    cost_ledger_path: Path
    statuses: Sequence[str] = ("COMPLETED",)
    costs: Sequence[int] = (0,)
    execute_calls: list[str] = field(default_factory=list)
    allowances_seen: list[int] = field(default_factory=list)

    def _scheduled(self, sequence: Sequence[Any]) -> Any:
        index = len(self.execute_calls)
        return sequence[index] if index < len(sequence) else sequence[-1]

    def probe(self, planned_run_id: str) -> RunProbe:
        run = ledger.get_run(self.conn, planned_run_id)
        if run is None:
            return RunProbe(status=None, outputs_complete=False)
        if run.status == "RUNNING":
            return RunProbe(status="RUNNING", outputs_complete=False)
        return RunProbe(status=run.status, outputs_complete=True)

    def execute(self, planned_run_id: str, *, allowance_eur_micros: int) -> str:
        status = self._scheduled(self.statuses)
        cost = self._scheduled(self.costs)
        self.execute_calls.append(planned_run_id)
        self.allowances_seen.append(allowance_eur_micros)
        _seed_run(self.conn, planned_run_id, status)
        _write_cost_row(self.cost_ledger_path, planned_run_id, cost)
        return status

    def recover_interrupted(self, planned_run_id: str) -> str:
        raise GateContractError("no interrupted run is seeded by these gate cases")

    def reconcile_outputs(self, planned_run_id: str) -> None:
        raise GateContractError("no incomplete-output run is seeded by these gate cases")

    def accounted_cost(self, run_ids: Sequence[str]) -> int:
        return durable_accounted_cost(self.cost_ledger_path, run_ids)


def _seed_finalized_iteration(
    store: SqliteLoopStateStore,
    conn: sqlite3.Connection,
    env: CaseEnv,
    *,
    loop_id: str,
    max_iterations: int,
    run_id: str,
    status: str,
    cost_eur_micros: int,
) -> None:
    """Seed one legitimate finalized iteration through the REAL store:
    durable intent, a real terminal run row, a real CostRow, then a real
    finalize binding the two. No row is hand-written and no loop total is
    faked."""
    from runner.loop import LoopSummary

    store.begin_or_load_loop(
        loop_id=loop_id,
        max_iterations=max_iterations,
        loop_budget_eur_micros=LOOP_CEILING_EUR_MICROS,
        failure_threshold=FAILURE_THRESHOLD,
        now=CLOCK_EPOCH,
    )
    store.record_intent(
        loop_id=loop_id, iteration_index=0, planned_run_id=run_id, now=CLOCK_EPOCH
    )
    _seed_run(conn, run_id, status)
    _write_cost_row(env.cost_ledger_path, run_id, cost_eur_micros)
    store.finalize_iteration(
        loop_id=loop_id,
        iteration_index=0,
        bound_run_id=run_id,
        run_status=status,
        accounted_cost_eur_micros=cost_eur_micros,
        consecutive_failures_after=0 if status == "COMPLETED" else 1,
        summary=LoopSummary(1, 1, 0 if status == "COMPLETED" else 1, cost_eur_micros),
        now=CLOCK_EPOCH + timedelta(seconds=1),
    )


# --- durable snapshots and the ITERATION_LOG self-check ---------------------


def _durable_iteration_rows(
    conn: sqlite3.Connection, loop_id: str, allowances: dict[str, int], stop_reason: str
) -> list[IterationMachineRow]:
    """Build the canonical machine rows FROM durable state.

    ``effective_allowance_eur_micros`` is the one field the ledger does not
    hold — it is what the supervisor handed the executor — so it is recorded
    by the executor and is left NULL for an iteration that never executed
    (an adopted or reconciled run). Values are never invented to make rows
    look uniform."""
    rows = conn.execute(
        """
        SELECT i.iteration_index, i.planned_run_id, i.bound_run_id, i.iteration_state,
               i.run_status, i.accounted_cost_eur_micros, i.consecutive_failures_after,
               i.started_at_utc, i.finished_at_utc,
               r.tasks_created, r.tasks_terminal, r.findings_new,
               r.findings_still_open, r.findings_resolved
          FROM loop_iterations i
          LEFT JOIN runs r ON r.run_id = i.bound_run_id
         WHERE i.loop_id = ?
         ORDER BY i.iteration_index
        """,
        (loop_id,),
    ).fetchall()

    machine_rows: list[IterationMachineRow] = []
    cumulative = 0
    breaker_reasons = {"COST_BREAKER_TRIPPED", "CONSECUTIVE_FAILURE_BREAKER_TRIPPED"}
    for position, row in enumerate(rows):
        cumulative += row["accounted_cost_eur_micros"]
        is_last = position == len(rows) - 1
        machine_rows.append(
            IterationMachineRow(
                iteration_index=row["iteration_index"],
                planned_run_id=row["planned_run_id"],
                iteration_state=row["iteration_state"],
                bound_run_id=row["bound_run_id"],
                run_status=row["run_status"],
                tasks_created=row["tasks_created"],
                tasks_terminal=row["tasks_terminal"],
                findings_new=row["findings_new"],
                findings_still_open=row["findings_still_open"],
                findings_resolved=row["findings_resolved"],
                iteration_cost_eur_micros=row["accounted_cost_eur_micros"],
                cumulative_cost_eur_micros=cumulative,
                effective_allowance_eur_micros=allowances.get(row["planned_run_id"]),
                consecutive_failures_after=row["consecutive_failures_after"],
                breaker=stop_reason if (is_last and stop_reason in breaker_reasons) else None,
                started_at_utc=row["started_at_utc"],
                finished_at_utc=row["finished_at_utc"],
            )
        )
    return machine_rows


#: The fields ADR-0010 section 7's self-check compares back to durable
#: state. ``effective_allowance_eur_micros`` and ``breaker`` are excluded
#: deliberately: neither is a durable ledger column, so comparing them would
#: check the renderer against itself.
SELF_CHECK_FIELDS: tuple[str, ...] = (
    "iteration_index",
    "planned_run_id",
    "bound_run_id",
    "iteration_state",
    "run_status",
    "tasks_created",
    "tasks_terminal",
    "findings_new",
    "findings_still_open",
    "findings_resolved",
    "iteration_cost_eur_micros",
    "cumulative_cost_eur_micros",
    "consecutive_failures_after",
)


def self_check_section(
    log_path: Path,
    section_id: str,
    conn: sqlite3.Connection,
    loop_id: str,
    allowances: dict[str, int],
) -> tuple[bool, str]:
    """ADR-0010 section 7's self-check.

    Rereads the written ITERATION_LOG BYTES from disk, reparses the machine
    rows out of them, then re-queries durable state independently and
    compares. The in-memory render object is never consulted — that is the
    whole point: a figure that was rendered correctly but written wrongly,
    or written correctly and then edited, must be caught here."""
    try:
        text = Path(log_path).read_text(encoding="utf-8")
        sections = parse_sections(text)
    except (IterationLogError, ValueError) as exc:
        return False, _safe_detail(f"ITERATION_LOG did not reparse: {exc}")

    section = sections.get(section_id)
    if section is None:
        return False, f"section {section_id} is absent from the written ITERATION_LOG"

    loop_row = conn.execute(
        "SELECT stop_reason, status FROM loop_runs WHERE loop_id = ?", (loop_id,)
    ).fetchone()
    if loop_row is None or loop_row["stop_reason"] is None:
        return False, f"loop {loop_id} has no durable stop_reason to check against"

    if section.metadata["stop_reason"] != loop_row["stop_reason"]:
        return False, (
            f"section stop_reason {section.metadata['stop_reason']} != durable "
            f"{loop_row['stop_reason']}"
        )
    if section.metadata["exit_code"] != EXPECTED_EXIT_CODES[loop_row["stop_reason"]]:
        return False, "section exit_code does not match the durable stop reason's exit code"

    expected = [
        row.as_machine_dict()
        for row in _durable_iteration_rows(
            conn, loop_id, allowances, loop_row["stop_reason"]
        )
    ]
    if len(section.rows) != len(expected):
        return False, (
            f"section carries {len(section.rows)} machine rows, durable state has "
            f"{len(expected)}"
        )
    for written, durable in zip(section.rows, expected):
        for name in SELF_CHECK_FIELDS:
            if written[name] != durable[name]:
                return False, (
                    f"iteration {durable['iteration_index']} field {name}: written "
                    f"{written[name]!r} != durable {durable[name]!r}"
                )
    return True, "written ITERATION_LOG figures match durable state"


@dataclass
class GateEvidence:
    """Accumulates what the legs produce: the ITERATION_LOG path, the
    per-case self-check outcomes, and the cases actually written."""

    source_sha: str
    log_path: Path
    self_checks: list[tuple[str, bool, str]] = field(default_factory=list)
    cases: list[str] = field(default_factory=list)

    def write_section(
        self,
        conn: sqlite3.Connection,
        *,
        loop_id: str,
        gate_leg: str,
        gate_case: str,
        classification: str,
        max_iterations: int,
        stop_reason: str,
        exit_code: int,
        allowances: dict[str, int],
        alert_label: Optional[str] = None,
    ) -> str:
        """Render one section from durable state, append it, then check the
        written bytes back against durable state."""
        rows = _durable_iteration_rows(conn, loop_id, allowances, stop_reason)
        meta = SectionMeta(
            loop_id=loop_id,
            gate_leg=gate_leg,
            gate_case=gate_case,
            classification=classification,
            source_sha=self.source_sha,
            max_iterations=max_iterations,
            loop_budget_eur_micros=LOOP_CEILING_EUR_MICROS,
            failure_threshold=FAILURE_THRESHOLD,
            stop_reason=stop_reason,
            exit_code=exit_code,
            iterations_recorded=len(rows),
            alert_label=alert_label,
        )
        section_id = meta.section_id
        append_section(self.log_path, section_id, render_section(meta, rows))
        ok, detail = self_check_section(self.log_path, section_id, conn, loop_id, allowances)
        self.self_checks.append((section_id, ok, detail))
        self.cases.append(gate_case)
        return section_id


# --- shared assertions ------------------------------------------------------


def _loop_record(conn: sqlite3.Connection, loop_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM loop_runs WHERE loop_id = ?", (loop_id,)).fetchone()
    if row is None:
        raise GateContractError(f"loop {loop_id!r} has no durable row")
    return row


def _severities_for(log_path: Path, event: str) -> list[str]:
    """Severity of every record for one event, read back from the REAL JSONL
    the loop wrote. Reading the file rather than an in-memory recorder is
    what makes this ADR-0010 section 5 part 1 evidence: the event had to pass
    ``RunLogger``'s closed-vocabulary check and land on disk."""
    records = [
        json.loads(line)
        for line in Path(log_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [r["severity"] for r in records if r["event"] == event]


def _cost_row_counts(path: Path) -> dict[str, int]:
    from telemetry.cost_ledger import read_cost_rows

    counts: dict[str, int] = {}
    if not Path(path).exists():
        return counts
    for row in read_cost_rows(Path(path)):
        counts[row.run_id] = counts.get(row.run_id, 0) + 1
    return counts


# --- LEG 1: normal N = 10 through the real Sentinel integration -------------


def leg1_normal(work_root: Path, recorder: PredicateRecorder, evidence: GateEvidence) -> None:
    env = _case_env(work_root, "leg1")
    loop_id = "loop-p4g-leg1"
    base_config = RunConfig(
        run_kind="dev",
        source="fixtures",
        db_path=env.db_path,
        findings_path=env.findings_path,
        log_path=env.log_path,
        cost_ledger_path=env.cost_ledger_path,
        fixtures_root=FIXTURES_ROOT,
        judgment_mode="stub",
    )
    executor = SentinelIterationExecutor(base_config=base_config)
    ids = GateRunIds("r-p4g-l1")

    conn = open_loop_state(env.db_path)
    logger = RunLogger(env.log_path)
    try:
        outcome = run_loop(
            LoopConfig(
                loop_id=loop_id,
                max_iterations=LEG1_ITERATIONS,
                per_run_cap_eur_micros=PER_RUN_CAP_EUR_MICROS,
                loop_budget_eur_micros=LOOP_CEILING_EUR_MICROS,
                failure_threshold=FAILURE_THRESHOLD,
            ),
            store=SqliteLoopStateStore(conn),
            executor=executor,
            clock=_clock(),
            ids=ids,
            logger=logger,
        )
    finally:
        logger.close()
        conn.close()

    allowances = dict(zip(executor.executed_run_ids, executor.allowances_seen))
    conn = open_loop_state(env.db_path)
    try:
        rows = _durable_iteration_rows(conn, loop_id, allowances, outcome.stop_reason)
        runs = {r.run_id: r for r in ledger.list_runs(conn)}
        loop_row = _loop_record(conn, loop_id)

        recorder.record(
            "LEG1_NORMAL_N10_ITERATION_COUNT",
            len(rows) == LEG1_ITERATIONS
            and outcome.iterations_completed == LEG1_ITERATIONS
            and loop_row["iterations_completed"] == LEG1_ITERATIONS,
            iterations=len(rows),
        )
        recorder.record(
            "LEG1_NORMAL_N10_INDEX_CONTIGUITY",
            [r.iteration_index for r in rows] == list(range(LEG1_ITERATIONS)),
        )
        recorder.record(
            "LEG1_NORMAL_N10_ALL_FINALIZED",
            all(r.iteration_state == FINALIZED for r in rows),
        )
        planned = [r.planned_run_id for r in rows]
        bound = [r.bound_run_id for r in rows]
        recorder.record(
            "LEG1_NORMAL_N10_IDENTITY_UNIQUE",
            len(set(planned)) == LEG1_ITERATIONS
            and len(set(bound)) == LEG1_ITERATIONS
            and planned == bound,
            unique_planned=len(set(planned)),
            unique_bound=len(set(bound)),
        )
        recorder.record(
            "LEG1_NORMAL_N10_RUNS_TERMINAL",
            len(runs) == LEG1_ITERATIONS
            and all(runs[i].status in ("COMPLETED", "FAILED") for i in planned),
            runs=len(runs),
        )
        recorder.record(
            "LEG1_NORMAL_N10_TASKS_TERMINAL",
            all(
                runs[i].tasks_created == runs[i].tasks_terminal and runs[i].tasks_created > 0
                for i in planned
            ),
        )
        counts = _cost_row_counts(env.cost_ledger_path)
        recorder.record(
            "LEG1_NORMAL_N10_ONE_COSTROW_PER_RUN",
            sorted(counts) == sorted(planned) and all(v == 1 for v in counts.values()),
        )
        # Continuity: iterations 2..10 observe nothing new, which is what a
        # persistent finding lifecycle across consecutive runs looks like.
        # This demonstrates continuity only; ADR-0009 already supplied the
        # stronger real-model identity evidence and no new cross-run dedup
        # acceptance gate is introduced here.
        recorder.record(
            "LEG1_NORMAL_N10_CONTINUITY",
            all(r.findings_new == 0 for r in rows[1:]) and rows[0].findings_new is not None,
            later_iterations=len(rows) - 1,
        )
        recorder.record(
            "LEG1_NORMAL_N10_COST_WITHIN_CEILING",
            outcome.accounted_cost_eur_micros <= LOOP_CEILING_EUR_MICROS,
            accounted_cost_eur_micros=outcome.accounted_cost_eur_micros,
        )
        recorder.record(
            "LEG1_NORMAL_N10_STOP_REASON",
            outcome.stop_reason == "COMPLETED_ITERATION_CAP"
            and loop_row["stop_reason"] == "COMPLETED_ITERATION_CAP",
            stop_reason=outcome.stop_reason,
        )
        recorder.record("LEG1_NORMAL_N10_EXIT_ZERO", outcome.exit_code == 0)

        evidence.write_section(
            conn,
            loop_id=loop_id,
            gate_leg="LEG1",
            gate_case="leg1-normal-n10",
            classification=SYNTHETIC,
            max_iterations=LEG1_ITERATIONS,
            stop_reason=outcome.stop_reason,
            exit_code=outcome.exit_code,
            allowances=allowances,
        )
    finally:
        conn.close()


# --- LEG 2: the cost breaker ------------------------------------------------


@dataclass
class _SeededLoopResult:
    outcome: Any
    executor: SeededIterationExecutor
    env: CaseEnv
    loop_id: str
    allowances: dict[str, int]


def _run_seeded_loop(
    work_root: Path,
    name: str,
    *,
    loop_id: str,
    max_iterations: int,
    statuses: Sequence[str],
    costs: Sequence[int],
    seed_first: Optional[tuple[str, str, int]] = None,
) -> _SeededLoopResult:
    """Drive the REAL supervisor over REAL durable loop state with a seeded
    executor. ``seed_first`` pre-seeds one already-finalized iteration
    (run id, status, cost) so the loop resumes with durable consumption
    already accounted."""
    env = _case_env(work_root, name)
    conn = open_loop_state(env.db_path)
    logger = RunLogger(env.log_path)
    executor = SeededIterationExecutor(
        conn=conn, cost_ledger_path=env.cost_ledger_path, statuses=statuses, costs=costs
    )
    try:
        store = SqliteLoopStateStore(conn)
        if seed_first is not None:
            run_id, status, cost = seed_first
            _seed_finalized_iteration(
                store,
                conn,
                env,
                loop_id=loop_id,
                max_iterations=max_iterations,
                run_id=run_id,
                status=status,
                cost_eur_micros=cost,
            )
        outcome = run_loop(
            LoopConfig(
                loop_id=loop_id,
                max_iterations=max_iterations,
                per_run_cap_eur_micros=PER_RUN_CAP_EUR_MICROS,
                loop_budget_eur_micros=LOOP_CEILING_EUR_MICROS,
                failure_threshold=FAILURE_THRESHOLD,
            ),
            store=store,
            executor=executor,
            clock=_clock(),
            ids=GateRunIds(f"r-{name}"),
            logger=logger,
        )
    finally:
        logger.close()
        conn.close()
    return _SeededLoopResult(
        outcome=outcome,
        executor=executor,
        env=env,
        loop_id=loop_id,
        allowances=dict(zip(executor.execute_calls, executor.allowances_seen)),
    )


def _record_seeded_section(
    result: _SeededLoopResult,
    evidence: GateEvidence,
    *,
    gate_leg: str,
    gate_case: str,
    max_iterations: int,
    alert_label: Optional[str] = None,
) -> None:
    conn = open_loop_state(result.env.db_path)
    try:
        evidence.write_section(
            conn,
            loop_id=result.loop_id,
            gate_leg=gate_leg,
            gate_case=gate_case,
            classification=SEEDED_FAULT,
            max_iterations=max_iterations,
            stop_reason=result.outcome.stop_reason,
            exit_code=result.outcome.exit_code,
            allowances=result.allowances,
            alert_label=alert_label,
        )
    finally:
        conn.close()


def leg2_cost_breaker(
    work_root: Path, recorder: PredicateRecorder, evidence: GateEvidence
) -> None:
    # --- 2A: cumulative 749999 mid-loop does NOT trip on cost alone --------
    a = _run_seeded_loop(
        work_root,
        "leg2a",
        loop_id="loop-p4g-leg2a",
        max_iterations=3,
        statuses=("COMPLETED",),
        costs=(0,),
        seed_first=("r-p4g-l2a-seed", "COMPLETED", 749_999),
    )
    recorder.record(
        "LEG2_749999_MIDLOOP_CONTINUES",
        a.outcome.stop_reason == "COMPLETED_ITERATION_CAP"
        and a.outcome.exit_code == 0
        and len(a.executor.execute_calls) == 2
        and a.outcome.accounted_cost_eur_micros == 749_999,
        further_iterations=len(a.executor.execute_calls),
        accounted_cost_eur_micros=a.outcome.accounted_cost_eur_micros,
    )
    # The next iteration's allowance is min(per_run_cap, remaining) = 1, and
    # the normal EUR 0.75 allowance is never silently restored.
    reduced_ok = a.executor.allowances_seen and all(
        allowance == 1 for allowance in a.executor.allowances_seen
    )
    # The downward budget-construction seam itself: a reduced allowance must
    # actually reach the existing RunBudgetCoordinator. The FX rate is an
    # injected deterministic object; nothing here resolves a live rate,
    # constructs an SDK client or contacts a provider.
    coordinator = build_iteration_budget(
        allowance_eur_micros=1,
        fx_rate=SimpleNamespace(usd_per_eur=Decimal("1.1554")),
    )
    recorder.record(
        "LEG2_REDUCED_ALLOWANCE_PROPAGATED",
        bool(reduced_ok)
        and coordinator.total_eur_micros == 1
        and coordinator.remaining_eur_micros() == 1
        and PER_RUN_CAP_EUR_MICROS not in a.executor.allowances_seen,
        allowance_seen=a.executor.allowances_seen[0] if a.executor.allowances_seen else None,
        coordinator_remaining=coordinator.remaining_eur_micros(),
    )
    _record_seeded_section(
        a, evidence, gate_leg="LEG2", gate_case="leg2a-749999-midloop", max_iterations=3
    )

    # --- 2B: cumulative EXACTLY at the ceiling refuses the next iteration --
    b = _run_seeded_loop(
        work_root,
        "leg2b",
        loop_id="loop-p4g-leg2b",
        max_iterations=3,
        statuses=("COMPLETED",),
        costs=(0,),
        seed_first=("r-p4g-l2b-seed", "COMPLETED", 750_000),
    )
    recorder.record(
        "LEG2_EXACT_CAP_MIDLOOP_REFUSED",
        b.outcome.stop_reason == "COST_BREAKER_TRIPPED"
        and b.outcome.exit_code != 0
        and b.outcome.iterations_completed < 3,
        stop_reason=b.outcome.stop_reason,
    )
    conn = open_loop_state(b.env.db_path)
    try:
        iterations = SqliteLoopStateStore(conn).list_iterations(b.loop_id)
        run_ids = [r.run_id for r in ledger.list_runs(conn)]
    finally:
        conn.close()
    recorder.record(
        "LEG2_NO_NEXT_RUN_AT_EXACT_CAP",
        len(iterations) == 1
        and run_ids == ["r-p4g-l2b-seed"]
        and b.executor.execute_calls == [],
        intents=len(iterations),
        runs=len(run_ids),
    )
    _record_seeded_section(
        b, evidence, gate_leg="LEG2", gate_case="leg2b-exact-cap-midloop", max_iterations=3
    )

    # --- 2C: overshoot mid-loop trips and is never clamped ------------------
    c = _run_seeded_loop(
        work_root,
        "leg2c",
        loop_id="loop-p4g-leg2c",
        max_iterations=3,
        statuses=("COMPLETED",),
        costs=(0,),
        seed_first=("r-p4g-l2c-seed", "COMPLETED", 750_001),
    )
    recorder.record(
        "LEG2_OVERSHOOT_TRIPS",
        c.outcome.stop_reason == "COST_BREAKER_TRIPPED" and c.outcome.exit_code != 0,
        stop_reason=c.outcome.stop_reason,
    )
    conn = open_loop_state(c.env.db_path)
    try:
        loop_row = _loop_record(conn, c.loop_id)
        iterations = SqliteLoopStateStore(conn).list_iterations(c.loop_id)
        run_ids = [r.run_id for r in ledger.list_runs(conn)]
    finally:
        conn.close()
    recorder.record(
        "LEG2_OVERSHOOT_FULL_NOT_CLAMPED",
        c.outcome.accounted_cost_eur_micros == 750_001
        and loop_row["accounted_cost_eur_micros"] == 750_001,
        accounted_cost_eur_micros=c.outcome.accounted_cost_eur_micros,
    )
    recorder.record(
        "LEG2_NO_NEXT_RUN_AFTER_OVERSHOOT",
        len(iterations) == 1
        and run_ids == ["r-p4g-l2c-seed"]
        and c.executor.execute_calls == [],
        intents=len(iterations),
        runs=len(run_ids),
    )
    _record_seeded_section(
        c, evidence, gate_leg="LEG2", gate_case="leg2c-overshoot-midloop", max_iterations=3
    )

    # --- 2D: N reached at EXACTLY the ceiling is normal completion ---------
    d = _run_seeded_loop(
        work_root,
        "leg2d",
        loop_id="loop-p4g-leg2d",
        max_iterations=1,
        statuses=("COMPLETED",),
        costs=(750_000,),
    )
    recorder.record(
        "LEG2_TERMINAL_EXACT_CAP_NORMAL",
        d.outcome.stop_reason == "COMPLETED_ITERATION_CAP"
        and d.outcome.exit_code == 0
        and d.outcome.accounted_cost_eur_micros == 750_000,
        stop_reason=d.outcome.stop_reason,
        accounted_cost_eur_micros=d.outcome.accounted_cost_eur_micros,
    )
    _record_seeded_section(
        d, evidence, gate_leg="LEG2", gate_case="leg2d-terminal-exact-cap", max_iterations=1
    )

    # --- 2E: N reached ABOVE the ceiling trips; overshoot retained exactly --
    e = _run_seeded_loop(
        work_root,
        "leg2e",
        loop_id="loop-p4g-leg2e",
        max_iterations=1,
        statuses=("COMPLETED",),
        costs=(750_001,),
    )
    recorder.record(
        "LEG2_TERMINAL_OVERSHOOT_TRIPS",
        e.outcome.stop_reason == "COST_BREAKER_TRIPPED"
        and e.outcome.exit_code != 0
        and e.outcome.accounted_cost_eur_micros == 750_001,
        stop_reason=e.outcome.stop_reason,
        accounted_cost_eur_micros=e.outcome.accounted_cost_eur_micros,
    )
    _record_seeded_section(
        e, evidence, gate_leg="LEG2", gate_case="leg2e-terminal-overshoot", max_iterations=1
    )


# --- LEG 3: the consecutive-failure breaker ---------------------------------


def leg3_consecutive_failure(
    work_root: Path, recorder: PredicateRecorder, evidence: GateEvidence
) -> None:
    # --- trip: exactly three consecutive failures --------------------------
    trip = _run_seeded_loop(
        work_root,
        "leg3trip",
        loop_id="loop-p4g-leg3trip",
        max_iterations=4,
        statuses=("FAILED", "FAILED", "FAILED"),
        costs=(0,),
    )
    recorder.record(
        "LEG3_TRIP_AT_THREE",
        trip.outcome.iterations_started == 3
        and trip.outcome.iterations_completed == 3
        and trip.outcome.consecutive_failures == 3
        and trip.outcome.stop_reason == "CONSECUTIVE_FAILURE_BREAKER_TRIPPED"
        and trip.outcome.exit_code != 0,
        iterations=trip.outcome.iterations_completed,
        streak=trip.outcome.consecutive_failures,
    )
    conn = open_loop_state(trip.env.db_path)
    try:
        iterations = SqliteLoopStateStore(conn).list_iterations(trip.loop_id)
        run_ids = sorted(r.run_id for r in ledger.list_runs(conn))
        loop_row = _loop_record(conn, trip.loop_id)
    finally:
        conn.close()
    recorder.record(
        "LEG3_NO_FOURTH_ITERATION",
        [r.iteration_index for r in iterations] == [0, 1, 2]
        and run_ids == sorted(trip.executor.execute_calls)
        and len(trip.executor.execute_calls) == 3,
        intents=len(iterations),
        runs=len(run_ids),
    )
    # ADR-0010 section 5, all four parts. Part 4 is the labeled section that
    # is written below and then reread from disk.
    section_id = None
    conn = open_loop_state(trip.env.db_path)
    try:
        section_id = evidence.write_section(
            conn,
            loop_id=trip.loop_id,
            gate_leg="LEG3",
            gate_case="leg3-trip-at-three",
            classification=SEEDED_FAULT,
            max_iterations=4,
            stop_reason=trip.outcome.stop_reason,
            exit_code=trip.outcome.exit_code,
            allowances=trip.allowances,
            alert_label=PHASE4_FAILURE_ALERT,
        )
    finally:
        conn.close()

    part1 = _severities_for(trip.env.log_path, "breaker.consecutive_failure_tripped") == ["ERROR"]
    part2 = loop_row["stop_reason"] == "CONSECUTIVE_FAILURE_BREAKER_TRIPPED"
    part3 = trip.outcome.exit_code != 0
    written = parse_sections(Path(evidence.log_path).read_text(encoding="utf-8"))
    alert = written.get(section_id)
    part4 = (
        alert is not None
        and alert.metadata["alert_label"] == PHASE4_FAILURE_ALERT
        and alert.metadata["loop_id"] == trip.loop_id
        and alert.metadata["stop_reason"] == "CONSECUTIVE_FAILURE_BREAKER_TRIPPED"
        and PHASE4_FAILURE_ALERT in Path(evidence.log_path).read_text(encoding="utf-8")
    )
    recorder.record(
        "LEG3_FOUR_PART_ALERT",
        part1 and part2 and part3 and part4,
        structured_error_event=part1,
        durable_stop_reason=part2,
        nonzero_exit=part3,
        labeled_iteration_log_section=part4,
    )

    # --- reset: F, F, C, F, F must NOT trip from stale streak state --------
    reset = _run_seeded_loop(
        work_root,
        "leg3reset",
        loop_id="loop-p4g-leg3reset",
        max_iterations=5,
        statuses=("FAILED", "FAILED", "COMPLETED", "FAILED", "FAILED"),
        costs=(0,),
    )
    recorder.record(
        "LEG3_RESET_SEQUENCE",
        reset.outcome.iterations_completed == 5
        and reset.outcome.consecutive_failures == 2
        and reset.outcome.stop_reason == "COMPLETED_ITERATION_CAP"
        and reset.outcome.exit_code == 0,
        iterations=reset.outcome.iterations_completed,
        streak=reset.outcome.consecutive_failures,
    )
    _record_seeded_section(
        reset, evidence, gate_leg="LEG3", gate_case="leg3-reset-sequence", max_iterations=5
    )

    # --- terminal precedence: N reached WITH streak 3 is the breaker -------
    precedence = _run_seeded_loop(
        work_root,
        "leg3prec",
        loop_id="loop-p4g-leg3prec",
        max_iterations=3,
        statuses=("FAILED", "FAILED", "FAILED"),
        costs=(0,),
    )
    recorder.record(
        "LEG3_TERMINAL_STREAK_PRECEDENCE",
        precedence.outcome.iterations_completed == 3
        and precedence.outcome.consecutive_failures == 3
        and precedence.outcome.stop_reason == "CONSECUTIVE_FAILURE_BREAKER_TRIPPED"
        and precedence.outcome.exit_code != 0,
        stop_reason=precedence.outcome.stop_reason,
    )
    _record_seeded_section(
        precedence,
        evidence,
        gate_leg="LEG3",
        gate_case="leg3-terminal-precedence",
        max_iterations=3,
    )


# --- LEG 4: crash / recovery ------------------------------------------------


@dataclass
class RecordingSentinelExecutor:
    """A thin recording proxy around the REAL ``SentinelIterationExecutor``.

    Every call is delegated unchanged — this adds no behaviour and replaces
    no logic. It exists only so the gate can observe WHICH recovery path the
    supervisor took (``reconcile_outputs`` for case D, ``recover_interrupted``
    for case C) without modifying ``runner/sentinel_adapter.py``, which this
    session must not touch: the adapter is part of the system under gate."""

    inner: SentinelIterationExecutor
    reconciled_run_ids: list[str] = field(default_factory=list)
    recovered_run_ids: list[str] = field(default_factory=list)

    @property
    def executed_run_ids(self) -> list[str]:
        return self.inner.executed_run_ids

    @property
    def allowances_seen(self) -> list[int]:
        return self.inner.allowances_seen

    def probe(self, planned_run_id: str) -> RunProbe:
        return self.inner.probe(planned_run_id)

    def execute(self, planned_run_id: str, *, allowance_eur_micros: int) -> str:
        return self.inner.execute(
            planned_run_id, allowance_eur_micros=allowance_eur_micros
        )

    def recover_interrupted(self, planned_run_id: str) -> str:
        self.recovered_run_ids.append(planned_run_id)
        return self.inner.recover_interrupted(planned_run_id)

    def reconcile_outputs(self, planned_run_id: str) -> None:
        self.reconciled_run_ids.append(planned_run_id)
        self.inner.reconcile_outputs(planned_run_id)

    def accounted_cost(self, run_ids: Sequence[str]) -> int:
        return self.inner.accounted_cost(run_ids)


def _sentinel_executor(
    env: CaseEnv, deps_factory: Optional[Callable[[str, int], Deps]] = None
) -> RecordingSentinelExecutor:
    return RecordingSentinelExecutor(
        inner=SentinelIterationExecutor(
            base_config=RunConfig(
                run_kind="dev",
                source="fixtures",
                db_path=env.db_path,
                findings_path=env.findings_path,
                log_path=env.log_path,
                cost_ledger_path=env.cost_ledger_path,
                fixtures_root=FIXTURES_ROOT,
                judgment_mode="stub",
            ),
            deps_factory=deps_factory,
        )
    )


def _drive(
    env: CaseEnv,
    *,
    loop_id: str,
    max_iterations: int,
    executor: Any,
    ids: GateRunIds,
    hooks: LoopHooks = LoopHooks(),
) -> Any:
    """One process-lifetime's worth of loop execution. Each call opens and
    closes its own connection, which is what makes the second call a genuine
    restart rather than a continuation."""
    conn = open_loop_state(env.db_path)
    logger = RunLogger(env.log_path)
    try:
        return run_loop(
            LoopConfig(
                loop_id=loop_id,
                max_iterations=max_iterations,
                per_run_cap_eur_micros=PER_RUN_CAP_EUR_MICROS,
                loop_budget_eur_micros=LOOP_CEILING_EUR_MICROS,
                failure_threshold=FAILURE_THRESHOLD,
            ),
            store=SqliteLoopStateStore(conn),
            executor=executor,
            clock=_clock(),
            ids=ids,
            logger=logger,
            hooks=hooks,
        )
    finally:
        logger.close()
        conn.close()


def leg4_crash_recovery(
    work_root: Path, recorder: PredicateRecorder, evidence: GateEvidence
) -> None:
    # --- primary seam: run terminal and durable, finalization not committed -
    env = _case_env(work_root, "leg4primary")
    loop_id = "loop-p4g-leg4primary"
    ids = GateRunIds("r-p4g-l4p")

    def crash_before_finalize(_loop_id: str, index: int, _run_id: str) -> None:
        if index == 0:
            raise _SimulatedProcessLoss("process lost after run terminal, before finalize")

    crashed = False
    try:
        _drive(
            env,
            loop_id=loop_id,
            max_iterations=2,
            executor=_sentinel_executor(env),
            ids=ids,
            hooks=LoopHooks(after_run_terminal_before_finalize=crash_before_finalize),
        )
    except _SimulatedProcessLoss:
        crashed = True

    first_planned = ids.issued[0]
    conn = open_loop_state(env.db_path)
    try:
        before = SqliteLoopStateStore(conn).list_iterations(loop_id)
        run_before = ledger.get_run(conn, first_planned)
    finally:
        conn.close()

    # Restart the SAME loop with a FRESH executor, so "execute was not called
    # again for this iteration" is observable as an empty call log rather
    # than asserted.
    restarted = _sentinel_executor(env)
    outcome = _drive(
        env, loop_id=loop_id, max_iterations=2, executor=restarted, ids=ids
    )

    conn = open_loop_state(env.db_path)
    try:
        after = SqliteLoopStateStore(conn).list_iterations(loop_id)
        run_ids = sorted(r.run_id for r in ledger.list_runs(conn))
        loop_row = _loop_record(conn, loop_id)
        allowances = dict(zip(restarted.executed_run_ids, restarted.allowances_seen))

        recorder.record(
            "LEG4_TERMINAL_BEFORE_FINALIZE_ADOPTED",
            crashed
            and len(before) == 1
            and before[0].iteration_state == "INTENT"
            and run_before is not None
            and run_before.status in ("COMPLETED", "FAILED")
            and first_planned not in restarted.executed_run_ids
            and after[0].iteration_state == FINALIZED
            and after[0].bound_run_id == first_planned
            and outcome.stop_reason == "COMPLETED_ITERATION_CAP"
            and outcome.exit_code == 0,
            adopted_without_reexecution=first_planned not in restarted.executed_run_ids,
            stop_reason=outcome.stop_reason,
        )
        recorder.record(
            "LEG4_SAME_PLANNED_RUN_ID",
            before[0].planned_run_id == after[0].planned_run_id == first_planned
            and after[0].iteration_index == before[0].iteration_index == 0,
            planned_run_id=first_planned,
        )
        recorder.record(
            "LEG4_NO_DUPLICATE_RUN",
            len(run_ids) == 2 and len(set(run_ids)) == 2 and first_planned in run_ids,
            runs=len(run_ids),
        )
        recorder.record(
            "LEG4_NO_SKIPPED_ITERATION",
            [r.iteration_index for r in after] == [0, 1]
            and all(r.iteration_state == FINALIZED for r in after)
            and loop_row["iterations_completed"] == 2,
            iterations=len(after),
        )

        evidence.write_section(
            conn,
            loop_id=loop_id,
            gate_leg="LEG4",
            gate_case="leg4-primary-before-finalize",
            classification=SEEDED_FAULT,
            max_iterations=2,
            stop_reason=outcome.stop_reason,
            exit_code=outcome.exit_code,
            allowances=allowances,
        )
    finally:
        conn.close()

    # --- case A: intent committed, no run started yet ----------------------
    env_a = _case_env(work_root, "leg4casea")
    loop_a = "loop-p4g-leg4casea"
    ids_a = GateRunIds("r-p4g-l4a")

    def crash_after_intent(_loop_id: str, index: int, _run_id: str) -> None:
        if index == 0:
            raise _SimulatedProcessLoss("process lost after intent, before any run")

    crashed_a = False
    try:
        _drive(
            env_a,
            loop_id=loop_a,
            max_iterations=1,
            executor=_sentinel_executor(env_a),
            ids=ids_a,
            hooks=LoopHooks(after_iteration_intent=crash_after_intent),
        )
    except _SimulatedProcessLoss:
        crashed_a = True

    planned_a = ids_a.issued[0]
    conn = open_loop_state(env_a.db_path)
    try:
        before_a = SqliteLoopStateStore(conn).list_iterations(loop_a)
        run_absent = ledger.get_run(conn, planned_a) is None
    finally:
        conn.close()

    restarted_a = _sentinel_executor(env_a)
    outcome_a = _drive(
        env_a, loop_id=loop_a, max_iterations=1, executor=restarted_a, ids=ids_a
    )

    conn = open_loop_state(env_a.db_path)
    try:
        after_a = SqliteLoopStateStore(conn).list_iterations(loop_a)
        run_ids_a = [r.run_id for r in ledger.list_runs(conn)]
        recorder.record(
            "LEG4_INTENT_BEFORE_RUN_REUSED",
            crashed_a
            and len(before_a) == 1
            and before_a[0].iteration_state == "INTENT"
            and run_absent
            and restarted_a.executed_run_ids == [planned_a]
            and len(after_a) == 1
            and after_a[0].planned_run_id == planned_a
            and after_a[0].bound_run_id == planned_a
            and run_ids_a == [planned_a]
            and outcome_a.stop_reason == "COMPLETED_ITERATION_CAP"
            and outcome_a.exit_code == 0,
            planned_run_id=planned_a,
            runs=len(run_ids_a),
        )
        evidence.write_section(
            conn,
            loop_id=loop_a,
            gate_leg="LEG4",
            gate_case="leg4-intent-before-run",
            classification=SEEDED_FAULT,
            max_iterations=1,
            stop_reason=outcome_a.stop_reason,
            exit_code=outcome_a.exit_code,
            allowances=dict(zip(restarted_a.executed_run_ids, restarted_a.allowances_seen)),
        )
    finally:
        conn.close()

    # --- case D: terminal run, derived outputs incomplete ------------------
    #
    # Injected at an EXISTING pipeline seam (``RunHooks.before_report_append``
    # fires after the terminal run close is durably committed and before
    # ``_write_run_outputs``), so ``sentinel/pipeline.py`` is not modified to
    # create this fault. N = 1, so the reconciliation observed afterwards can
    # only be the loop's own case-D path — no later run exists to have done
    # it incidentally.
    env_d = _case_env(work_root, "leg4cased")
    loop_d = "loop-p4g-leg4cased"
    ids_d = GateRunIds("r-p4g-l4d")

    def crash_before_outputs(_run_id: str) -> None:
        raise _SimulatedProcessLoss("process lost after run close, before output writes")

    def crashing_deps(_run_id: str, _allowance: int) -> Deps:
        return Deps(hooks=RunHooks(before_report_append=crash_before_outputs))

    crashed_d = False
    try:
        _drive(
            env_d,
            loop_id=loop_d,
            max_iterations=1,
            executor=_sentinel_executor(env_d, deps_factory=crashing_deps),
            ids=ids_d,
        )
    except _SimulatedProcessLoss:
        crashed_d = True

    planned_d = ids_d.issued[0]
    conn = open_loop_state(env_d.db_path)
    try:
        run_d = ledger.get_run(conn, planned_d)
    finally:
        conn.close()
    from sentinel.report import is_section_complete as findings_section_complete

    outputs_incomplete = not findings_section_complete(env_d.findings_path, planned_d)
    cost_missing = _cost_row_counts(env_d.cost_ledger_path).get(planned_d, 0) == 0

    restarted_d = _sentinel_executor(env_d)
    outcome_d = _drive(
        env_d, loop_id=loop_d, max_iterations=1, executor=restarted_d, ids=ids_d
    )

    findings_text = (
        env_d.findings_path.read_text(encoding="utf-8")
        if env_d.findings_path.exists()
        else ""
    )
    conn = open_loop_state(env_d.db_path)
    try:
        after_d = SqliteLoopStateStore(conn).list_iterations(loop_d)
        run_ids_d = [r.run_id for r in ledger.list_runs(conn)]
        recorder.record(
            "LEG4_TERMINAL_OUTPUTS_RECONCILED",
            crashed_d
            and run_d is not None
            and run_d.status in ("COMPLETED", "FAILED")
            and outputs_incomplete
            and cost_missing
            # not rerun: the restarted executor never executed this run id
            and restarted_d.executed_run_ids == []
            and restarted_d.reconciled_run_ids == [planned_d]
            # reconciled exactly once, from exactly one cost source
            and _cost_row_counts(env_d.cost_ledger_path).get(planned_d) == 1
            and findings_text.count(f"sentinel:run {planned_d}") == 2
            and len(after_d) == 1
            and after_d[0].planned_run_id == planned_d
            and after_d[0].bound_run_id == planned_d
            and after_d[0].iteration_state == FINALIZED
            and run_ids_d == [planned_d]
            and outcome_d.stop_reason == "COMPLETED_ITERATION_CAP",
            planned_run_id=planned_d,
            cost_rows=_cost_row_counts(env_d.cost_ledger_path).get(planned_d),
        )
        evidence.write_section(
            conn,
            loop_id=loop_d,
            gate_leg="LEG4",
            gate_case="leg4-terminal-outputs",
            classification=SEEDED_FAULT,
            max_iterations=1,
            stop_reason=outcome_d.stop_reason,
            exit_code=outcome_d.exit_code,
            allowances=dict(zip(restarted_d.executed_run_ids, restarted_d.allowances_seen)),
        )
    finally:
        conn.close()


# --- artifact assembly and public-output hygiene ----------------------------

ARTIFACT_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "gate",
        "gate_contract",
        "source_sha",
        "overall",
        "model_calls",
        "provider_spend_eur_micros",
        "loop_budget_eur_micros",
        "failure_threshold",
        "legs",
        "predicate_results",
        "iteration_log_sha256",
    }
)


def validate_artifact(artifact: dict[str, Any], *, complete: bool = True) -> None:
    """Closed-schema validation of the gate artifact.

    Recursive and field-aware: each structured string passes its own
    validator; ``detail`` is the ONLY free-text field and was already
    sanitized before insertion. ``redact`` is never applied to a whole
    serialized object or machine-row line."""
    required = set(ARTIFACT_KEYS)
    if not complete:
        # An incomplete artifact still carries the predicates recorded so
        # far — that is what makes the hygiene scan able to see their
        # sanitized diagnostics — but not yet the overall verdict.
        required -= {"overall"}
    if set(artifact) != required:
        raise GateContractError(
            f"artifact keys {sorted(artifact)} do not match the frozen schema "
            f"{sorted(required)}"
        )
    if artifact["schema_version"] != SCHEMA_VERSION:
        raise GateContractError("schema_version is frozen at 1")
    if artifact["gate"] != GATE:
        raise GateContractError(f"gate is frozen at {GATE!r}")
    if artifact["gate_contract"] != GATE_CONTRACT:
        raise GateContractError(f"gate_contract is frozen at {GATE_CONTRACT!r}")
    if not _SOURCE_SHA_RE.fullmatch(str(artifact["source_sha"])):
        raise GateContractError("source_sha must be exactly 40 lowercase hex characters")
    if artifact["model_calls"] != 0:
        raise GateContractError("the Phase-4 technical gate is model-free: model_calls must be 0")
    if artifact["provider_spend_eur_micros"] != 0:
        raise GateContractError("the Phase-4 technical gate is model-free: provider spend must be 0")
    if artifact["loop_budget_eur_micros"] != LOOP_CEILING_EUR_MICROS:
        raise GateContractError("loop_budget_eur_micros is frozen at the ADR-0010 ceiling")
    if artifact["failure_threshold"] != FAILURE_THRESHOLD:
        raise GateContractError("failure_threshold is frozen at three")
    if not _SHA256_RE.fullmatch(str(artifact["iteration_log_sha256"])):
        raise GateContractError("iteration_log_sha256 must be 64 lowercase hex characters")
    for leg in artifact["legs"]:
        if set(leg) != {"leg", "cases", "result"}:
            raise GateContractError("a leg entry does not match the frozen schema")
        if not _IDENTIFIER_RE.fullmatch(leg["leg"]) or leg["result"] not in (PASS, FAIL):
            raise GateContractError("a leg entry carries an invalid name or result")
        for case in leg["cases"]:
            if not _IDENTIFIER_RE.fullmatch(case):
                raise GateContractError(f"case name {case!r} is not a public-safe identifier")
    seen: set[str] = set()
    for row in artifact["predicate_results"]:
        if not set(row) <= {"id", "result", "detail", "evidence"}:
            raise GateContractError("a predicate result carries an unknown field")
        if row["id"] not in PREDICATE_IDS:
            raise GateContractError(f"{row['id']!r} is not a frozen predicate")
        if row["id"] in seen:
            raise GateContractError(f"{row['id']!r} appears more than once")
        seen.add(row["id"])
        if row["result"] not in (PASS, FAIL):
            raise GateContractError("a predicate result is neither PASS nor FAIL")
    if not complete:
        return
    if artifact["overall"] not in (PASS, FAIL):
        raise GateContractError("overall must be PASS or FAIL")
    if seen != set(PREDICATE_IDS):
        raise GateContractError(
            f"frozen predicates missing from the artifact: {sorted(set(PREDICATE_IDS) - seen)}"
        )
    declared = artifact["overall"]
    computed = (
        PASS
        if all(row["result"] == PASS for row in artifact["predicate_results"])
        else FAIL
    )
    if declared != computed:
        raise GateContractError("overall does not match the recorded predicate results")


def _diagnostic_strings(payload: Any) -> list[str]:
    """Every free-text ``detail`` in the artifact — the only values that were
    sanitized rather than schema-validated."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "detail" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_diagnostic_strings(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found.extend(_diagnostic_strings(item))
    return found


def _forbidden_needles(work_root: Path) -> list[str]:
    """The temporary gate-root path — in either separator form — and its
    distinctive directory name must not appear verbatim in public evidence.

    Deliberately NOT every path component: a machine's temp root contains
    ordinary words (``Temp``, ``Local``, ``Users``) whose appearance in an
    English sentence would be a false positive, and a hygiene check that
    cries wolf gets weakened later by someone tidying it up."""
    return [str(work_root), work_root.as_posix(), work_root.name]


def public_output_clean(
    log_path: Path, artifact_core: dict[str, Any], work_root: Path
) -> tuple[bool, str]:
    """ADR-0010 section 7 / dispatch section 10 hygiene, field-aware.

    Four independent conditions, all of which must hold:

    1. the FINAL written ITERATION_LOG bytes reparse, and every metadata
       field and machine-row field re-validates through its own validator;
    2. the artifact satisfies its closed schema, recursively;
    3. every sanitized diagnostic is stable under the repository's existing
       hygiene mechanism (applied per diagnostic string, never to a whole
       serialized object or machine-row line);
    4. the raw-byte backstops pass on BOTH public outputs.
    """
    problems: list[str] = []
    try:
        text = Path(log_path).read_text(encoding="utf-8")
        sections = parse_sections(text)
        if not sections:
            problems.append("the ITERATION_LOG holds no parseable section")
    except (IterationLogError, ValueError) as exc:
        return False, _safe_detail(f"ITERATION_LOG failed structured revalidation: {exc}")

    try:
        validate_artifact(artifact_core, complete=False)
    except GateContractError as exc:
        problems.append(_safe_detail(f"artifact schema: {exc}"))

    for diagnostic in _diagnostic_strings(artifact_core):
        if redact(diagnostic) != diagnostic:
            problems.append("a diagnostic string was not sanitized before insertion")

    core_text = json.dumps(artifact_core, indent=2, sort_keys=True)
    for blob, label in ((text, "ITERATION_LOG"), (core_text, "artifact")):
        for needle in _forbidden_needles(work_root):
            if needle and needle in blob:
                problems.append(f"{label} contains the temporary gate-root path")
                break
        for needle in FORBIDDEN_SUBSTRINGS:
            if needle in blob:
                problems.append(f"{label} contains a forbidden substring")
                break

    if problems:
        return False, "; ".join(sorted(set(problems)))
    return True, "structured revalidation, artifact schema and raw-byte backstops all clean"


# --- orchestration ----------------------------------------------------------


def _run_leg(
    recorder: PredicateRecorder,
    name: str,
    fn: Callable[..., None],
    *args: Any,
) -> Optional[str]:
    """Run one leg. A leg that raises records ITS OWN predicates as FAIL and
    the remaining legs still run, so no frozen predicate can disappear
    because an earlier case failed. The failure detail is sanitized; no raw
    traceback reaches public evidence."""
    try:
        fn(*args)
        return None
    except (Exception, _SimulatedProcessLoss) as exc:  # noqa: BLE001 - recorded, not swallowed
        detail = _safe_detail(f"{name} raised {type(exc).__name__}: {exc}")
        recorder.record_missing_as_failed(LEG_PREDICATES[name], detail)
        return detail


def run_gate(*, source_sha: str, iteration_log_path: Path) -> dict[str, Any]:
    """Execute the frozen gate and return the artifact. The caller writes it;
    this function owns no repository path of its own."""
    if not _SOURCE_SHA_RE.fullmatch(source_sha):
        raise GateContractError(
            "--source-sha must be exactly 40 lowercase hexadecimal characters; the "
            "gate never substitutes another branch SHA"
        )
    recorder = PredicateRecorder()
    leg_errors: dict[str, str] = {}

    with tempfile.TemporaryDirectory(prefix="p4gate-") as tmp:
        work_root = Path(tmp)
        evidence = GateEvidence(source_sha=source_sha, log_path=iteration_log_path)

        for name, fn in (
            ("LEG1", leg1_normal),
            ("LEG2", leg2_cost_breaker),
            ("LEG3", leg3_consecutive_failure),
            ("LEG4", leg4_crash_recovery),
        ):
            error = _run_leg(recorder, name, fn, work_root, recorder, evidence)
            if error:
                leg_errors[name] = error

        self_check_ok = bool(evidence.self_checks) and all(
            ok for _sid, ok, _detail in evidence.self_checks
        )
        first_failure = next(
            (detail for _sid, ok, detail in evidence.self_checks if not ok), None
        )
        recorder.record(
            "ITERATION_LOG_MATCHES_DURABLE_STATE",
            self_check_ok,
            sections_checked=len(evidence.self_checks),
            detail=first_failure if first_failure else None,
        )

        legs = [
            {
                "leg": name,
                "cases": [c for c in evidence.cases if c.startswith(name.lower())],
                "result": PASS
                if name not in leg_errors
                and all(
                    row["result"] == PASS
                    for row in recorder.results()
                    if row["id"] in LEG_PREDICATES[name]
                )
                else FAIL,
            }
            for name in ("LEG1", "LEG2", "LEG3", "LEG4")
        ]

        core: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "gate": GATE,
            "gate_contract": GATE_CONTRACT,
            "source_sha": source_sha,
            "model_calls": MODEL_CALLS,
            "provider_spend_eur_micros": PROVIDER_SPEND_EUR_MICROS,
            "loop_budget_eur_micros": LOOP_CEILING_EUR_MICROS,
            "failure_threshold": FAILURE_THRESHOLD,
            "legs": legs,
            "iteration_log_sha256": iteration_log_sha256(iteration_log_path)
            if Path(iteration_log_path).exists()
            else "0" * 64,
            "predicate_results": recorder.recorded(),
        }
        clean, clean_detail = public_output_clean(iteration_log_path, core, work_root)
        recorder.record("PUBLIC_OUTPUT_CLEAN", clean, detail=clean_detail)

    artifact = dict(core)
    artifact["predicate_results"] = recorder.results()
    artifact["overall"] = recorder.overall()
    validate_artifact(artifact)
    return artifact


def write_artifact(path: Path, artifact: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(artifact, handle, indent=2, sort_keys=True)
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_phase4_loop_gate",
        description=(
            "ADR-0010 section 7 Phase-4 bounded-loop technical gate. MODEL-FREE. "
            "No flag changes the loop ceiling, the failure threshold, the predicate "
            "set or the model/provider mode: ADR-0010 section 2 forbids one."
        ),
    )
    parser.add_argument(
        "--source-sha",
        required=True,
        help="the exact 40-hex commit SHA of the implementation under gate",
    )
    parser.add_argument("--iteration-log", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    return parser


def main(argv: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv))
    try:
        artifact = run_gate(
            source_sha=args.source_sha, iteration_log_path=args.iteration_log
        )
    except GateContractError as exc:
        # Operator console diagnostics may be more detailed than public
        # evidence, but never expose credentials or secrets. No artifact is
        # written for a contract violation: a malformed artifact is worse
        # than none.
        print(f"GATE CONTRACT ERROR: {exc}", file=sys.stderr)
        return 1

    write_artifact(args.artifact, artifact)
    print(json.dumps(artifact, indent=2, sort_keys=True))
    print()
    for row in artifact["predicate_results"]:
        if row["result"] != PASS:
            print(f"FAILED PREDICATE: {row['id']} — {row.get('detail', '')}")
    print("OVERALL:", artifact["overall"])
    return 0 if artifact["overall"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

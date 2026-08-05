"""The ordered Phase-2 run (BLUEPRINT §6 P2).

Order (C4-corrected — terminal DB state always commits before any
output write): open logger -> open ledger -> recover interrupted runs
-> reconcile terminal-run outputs -> create RUNNING run -> enumerate
surfaces (+ all-class carry-forward) -> persist tasks -> execute each
task -> auto-resolve -> compute counts -> **close the run to terminal
status** -> *then* append FINDINGS.md (idempotent) -> *then* append
the zero-cost CostRow (idempotent) -> flush/close the logger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

# Import every checker module for its @register_checker side effect.
import checks.deterministic.files  # noqa: F401
import checks.deterministic.links  # noqa: F401
import checks.deterministic.numbers  # noqa: F401
import checks.deterministic.readme  # noqa: F401
import checks.judgment.stale_state  # noqa: F401
import checks.judgment.synthetic_label  # noqa: F401
from checks.base import CHECKERS, CheckContext, Confirmed, Inconclusive
from checks.judgment.stubs import JudgmentStub, NullJudgmentStub
from contracts.schemas import CheckTask, RunRecord
from sentinel import costs, ledger, lifecycle
from sentinel.config import (
    HTTP_BACKOFF_SECONDS,
    HTTP_MAX_ATTEMPTS,
    HTTP_RETRY_STATUSES,
    RunConfig,
)
from sentinel.ids import Clock, IdFactory, RandomIdFactory, Sleeper, SystemClock, SystemSleeper
from sentinel.inventory.base import RepoSurface, WorkUnitSpec, build_work_units
from sentinel.inventory.fixtures import FixtureSurfaceProvider
from sentinel.inventory import github_live, site
from sentinel.logs import RunLogger
from sentinel.net.client import BudgetedHttpClient, HttpClient, RetryingHttpClient, UrllibHttpClient
from sentinel.net.links import LinkResolver, LinkTruthResolver, HttpLinkResolver
from sentinel.report import ReportInput, append_run_section, is_section_complete, render_run_section
from sentinel.states import DEAD_LETTER, DONE, FAILED, IN_PROGRESS, PENDING


@dataclass(frozen=True)
class RunHooks:
    """FI test seams — each defaults to a no-op. Named after the
    exact pipeline step it fires before/after."""

    after_run_created: Optional[Callable[[str], None]] = None
    after_tasks_created: Optional[Callable[[str, int], None]] = None
    before_task_execute: Optional[Callable[[CheckTask], None]] = None
    after_findings_applied: Optional[Callable[[CheckTask], None]] = None
    before_resolve_absent: Optional[Callable[[str], None]] = None
    before_report_append: Optional[Callable[[str], None]] = None
    before_run_close: Optional[Callable[[str], None]] = None


@dataclass(frozen=True)
class Deps:
    clock: Clock = field(default_factory=SystemClock)
    ids: IdFactory = field(default_factory=RandomIdFactory)
    sleeper: Sleeper = field(default_factory=SystemSleeper)
    http: Optional[HttpClient] = None
    surface_provider: Optional[object] = None  # duck-typed: has .repos()
    link_resolver: Optional[LinkResolver] = None
    judgment: JudgmentStub = field(default_factory=NullJudgmentStub)
    hooks: RunHooks = field(default_factory=RunHooks)


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    status: str
    exit_code: int
    tasks_created: int
    tasks_terminal: int
    findings_new: int
    findings_still_open: int
    findings_resolved: int


def recover_interrupted_runs(conn, *, now: datetime, logger: RunLogger) -> list[str]:
    """A RunRecord left RUNNING is, by definition, an interrupted run
    (Phase 2 is a single local process). Sweep its tasks to
    FAILED->DEAD_LETTER and close it FAILED — never touching
    findings, so nothing is lost or falsely resolved."""
    recovered: list[str] = []
    for run in ledger.list_runs(conn, status="RUNNING"):
        with ledger.unit_of_work(conn):
            ledger.sweep_non_terminal(conn, run.run_id)
            counts = lifecycle.compute_run_counts(conn, run.run_id)
            ledger.close_run(conn, run.run_id, status="FAILED", finished_at_utc=now, counts=counts)
        logger.log("WARNING", "run.recovered", now=now, run_id=run.run_id)
        recovered.append(run.run_id)
    return recovered


def _write_run_outputs(conn, config: RunConfig, run_id: str, *, logger: RunLogger) -> None:
    run = ledger.get_run(conn, run_id)
    assert run is not None
    tasks_done = ledger.count_tasks(conn, run_id, statuses=[DONE])
    tasks_failed = ledger.count_tasks(conn, run_id, statuses=[FAILED])
    tasks_dead = ledger.count_tasks(conn, run_id, statuses=[DEAD_LETTER])
    new_findings = [row.finding for row in ledger.list_findings_for_run(conn, run_id, role="first_seen")]
    resolved_findings = [row.finding for row in ledger.list_findings_for_run(conn, run_id, role="resolved")]
    report_input = ReportInput(
        run=run,
        tasks_done=tasks_done,
        tasks_failed=tasks_failed,
        tasks_dead_letter=tasks_dead,
        new_findings=new_findings,
        resolved_findings=resolved_findings,
    )
    section = render_run_section(report_input)
    stamp = run.finished_at_utc or run.started_at_utc
    appended = append_run_section(config.findings_path, run_id, section)
    logger.log(
        "INFO",
        "report.appended" if appended else "report.section_already_present",
        now=stamp,
        run_id=run_id,
    )
    if not costs.has_cost_row_for_run(config.cost_ledger_path, run_id):
        costs.repair_trailing_fragment(config.cost_ledger_path)
        if not costs.has_cost_row_for_run(config.cost_ledger_path, run_id):
            # Checked from ledger state, not a Deps flag, so the same
            # reconciliation path (crash recovery included) always
            # builds the right row regardless of how the run was
            # invoked (dispatch q77-p3-a, section F).
            if costs.has_agent_calls_for_run(conn, run_id):
                row = costs.append_agent_cost_row(
                    config.cost_ledger_path, conn, run_id=run_id, run_kind=run.run_kind, recorded_at_utc=stamp
                )
            else:
                row = costs.append_zero_cost_row(
                    config.cost_ledger_path, run_id=run_id, run_kind=run.run_kind, recorded_at_utc=stamp
                )
            logger.log("INFO", "cost.row_appended", now=row.recorded_at_utc, run_id=run_id)


def reconcile_terminal_run_outputs(conn, config: RunConfig, *, logger: RunLogger) -> None:
    """At the start of every invocation: backfill FINDINGS.md /
    cost-ledger output for any already-terminal run that is missing
    either — this repairs a crash landing after TX-close but before
    the output writes (C4f)."""
    terminal_runs = ledger.list_runs(conn, status="COMPLETED") + ledger.list_runs(conn, status="FAILED")
    for run in terminal_runs:
        needs_report = not is_section_complete(config.findings_path, run.run_id)
        needs_cost = not costs.has_cost_row_for_run(config.cost_ledger_path, run.run_id)
        if needs_report or needs_cost:
            _write_run_outputs(conn, config, run.run_id, logger=logger)


def _default_http_client(config: RunConfig, deps: Deps) -> HttpClient:
    base = UrllibHttpClient()
    retrying = RetryingHttpClient(
        inner=base,
        sleeper=deps.sleeper,
        max_attempts=HTTP_MAX_ATTEMPTS,
        backoff_seconds=HTTP_BACKOFF_SECONDS,
        retry_statuses=HTTP_RETRY_STATUSES,
    )
    return BudgetedHttpClient(inner=retrying, max_requests=config.max_http_requests)


@dataclass(frozen=True)
class LiveSurfaceProvider:
    http: HttpClient
    github_user: str
    timeout: float
    open_scopes: frozenset
    site_repo: Optional[str] = None

    def repos(self) -> list[RepoSurface]:
        surfaces = github_live.build_repo_surfaces(
            self.http, self.github_user, timeout=self.timeout, open_scopes=self.open_scopes
        )
        surfaces.append(
            site.build_site_surface(
                self.http,
                self.github_user,
                timeout=self.timeout,
                open_scopes=self.open_scopes,
            )
        )
        return surfaces


def _build_surface_provider(config: RunConfig, deps: Deps, open_scopes: frozenset):
    if config.source == "fixtures":
        return FixtureSurfaceProvider(fixtures_root=config.fixtures_root)
    assert config.source == "live"
    assert config.github_user
    http = deps.http or _default_http_client(config, deps)
    return LiveSurfaceProvider(
        http=http,
        github_user=config.github_user,
        timeout=config.http_timeout_seconds,
        open_scopes=open_scopes,
        site_repo=config.site_repo,
    )


def _build_link_resolver(config: RunConfig, deps: Deps, http: Optional[HttpClient]) -> LinkResolver:
    if deps.link_resolver is not None:
        return deps.link_resolver
    if config.source == "fixtures":
        # link_truth.jsonl is a sibling of fixtures_root's parent
        # (fixtures/repos -> fixtures/link_truth.jsonl) — derived from
        # the given fixtures_root, not a hardcoded CWD-relative path,
        # so this works regardless of the process's working directory.
        link_truth_path = config.fixtures_root.parent / "link_truth.jsonl"
        return LinkTruthResolver.from_file(link_truth_path)
    assert http is not None
    user_agent = github_live.user_agent(config.github_user or "")
    return HttpLinkResolver(http=http, user_agent=user_agent, timeout=config.http_timeout_seconds)


def _build_check_context(unit: WorkUnitSpec, deps: Deps, link_resolver: LinkResolver) -> CheckContext:
    policy = unit.repo.policy
    return CheckContext(
        owner=unit.repo.owner,
        detail_path=unit.detail_path,
        fetch=unit.repo.fetch,
        link_resolver=link_resolver,
        judgment=deps.judgment,
        required_readme_sections=policy.required_readme_sections,
        enforce_readme_order=policy.enforce_readme_order,
        policy_parse_required=(unit.detail_path == policy.policy_source_path),
    )


def _outcome_from_ledger(conn, run_id: str, *, exit_code: int) -> RunOutcome:
    run = ledger.get_run(conn, run_id)
    assert run is not None
    return RunOutcome(
        run_id=run_id,
        status=run.status,
        exit_code=exit_code,
        tasks_created=run.tasks_created,
        tasks_terminal=run.tasks_terminal,
        findings_new=run.findings_new,
        findings_still_open=run.findings_still_open,
        findings_resolved=run.findings_resolved,
    )


def execute_run(config: RunConfig, deps: Optional[Deps] = None) -> RunOutcome:
    deps = deps or Deps()
    logger = RunLogger(config.log_path)
    try:
        conn = ledger.open_ledger(config.db_path)
        try:
            if config.recover:
                recover_interrupted_runs(conn, now=deps.clock.now(), logger=logger)
            reconcile_terminal_run_outputs(conn, config, logger=logger)

            run_id = config.run_id or deps.ids.new_run_id()
            start_now = deps.clock.now()
            with ledger.unit_of_work(conn):
                run = RunRecord(
                    schema_version=1,
                    run_id=run_id,
                    run_kind=config.run_kind,
                    status="RUNNING",
                    started_at_utc=start_now,
                    tasks_created=0,
                    tasks_terminal=0,
                    findings_new=0,
                    findings_still_open=0,
                    findings_resolved=0,
                )
                ledger.insert_run(conn, run)
            logger.log("INFO", "run.started", now=start_now, run_id=run_id)
            if deps.hooks.after_run_created:
                deps.hooks.after_run_created(run_id)

            try:
                open_scopes = frozenset(
                    (row.finding.surface, row.finding.check_class)
                    for row in ledger.list_open_findings(conn)
                )
                surface_provider = deps.surface_provider or _build_surface_provider(
                    config, deps, open_scopes
                )
                repos = surface_provider.repos()
                work_units = build_work_units(repos)

                tasks: list[CheckTask] = []
                unit_by_task_id: dict[str, WorkUnitSpec] = {}
                for unit in work_units:
                    task_id = deps.ids.new_task_id()
                    task = CheckTask(
                        schema_version=1,
                        task_id=task_id,
                        run_id=run_id,
                        surface=unit.surface,
                        check_class=unit.check_class,
                        created_at_utc=deps.clock.now(),
                        status=PENDING,
                    )
                    tasks.append(task)
                    unit_by_task_id[task_id] = unit

                with ledger.unit_of_work(conn):
                    ledger.insert_tasks(conn, tasks)
                    ledger.bump_run_counts(conn, run_id, tasks_created=len(tasks))
                if deps.hooks.after_tasks_created:
                    deps.hooks.after_tasks_created(run_id, len(tasks))

                link_resolver = deps.link_resolver
                if link_resolver is None:
                    live_http = getattr(surface_provider, "http", None)
                    link_resolver = _build_link_resolver(config, deps, live_http)

                all_observed = []
                done_scopes: set[tuple[str, str]] = set()
                dead_letter_count = 0

                for task in tasks:
                    unit = unit_by_task_id[task.task_id]
                    if deps.hooks.before_task_execute:
                        deps.hooks.before_task_execute(task)

                    with ledger.unit_of_work(conn):
                        ledger.transition_task(
                            conn, run_id, task.task_id, expected=PENDING, new=IN_PROGRESS
                        )
                    logger.log(
                        "DEBUG",
                        "task.claimed",
                        now=deps.clock.now(),
                        run_id=run_id,
                        task_id=task.task_id,
                        check_class=task.check_class,
                        surface=task.surface,
                    )

                    ctx = _build_check_context(unit, deps, link_resolver)
                    checker = CHECKERS[task.check_class]
                    try:
                        outcome = checker(ctx)
                    except Exception as exc:  # noqa: BLE001 - any checker failure dead-letters
                        outcome = Inconclusive(f"{type(exc).__name__}: {exc}")

                    if isinstance(outcome, Confirmed):
                        now = deps.clock.now()
                        with ledger.unit_of_work(conn):
                            lifecycle.apply_observed(conn, run_id, now, outcome.findings)
                            ledger.transition_task(
                                conn, run_id, task.task_id, expected=IN_PROGRESS, new=DONE
                            )
                            ledger.bump_run_counts(conn, run_id, tasks_terminal_delta=1)
                        done_scopes.add((task.surface, task.check_class))
                        all_observed.extend(outcome.findings)
                        logger.log(
                            "INFO",
                            "task.done",
                            now=now,
                            run_id=run_id,
                            task_id=task.task_id,
                            check_class=task.check_class,
                            surface=task.surface,
                        )
                        if deps.hooks.after_findings_applied:
                            deps.hooks.after_findings_applied(task)
                    else:
                        now = deps.clock.now()
                        with ledger.unit_of_work(conn):
                            ledger.fail_and_dead_letter(
                                conn, run_id, task.task_id, expected=IN_PROGRESS
                            )
                            ledger.bump_run_counts(conn, run_id, tasks_terminal_delta=1)
                        dead_letter_count += 1
                        logger.log(
                            "ERROR",
                            "task.dead_letter",
                            now=now,
                            run_id=run_id,
                            task_id=task.task_id,
                            check_class=task.check_class,
                            surface=task.surface,
                            error_type="Inconclusive",
                            error_message=outcome.reason,
                        )

                if deps.hooks.before_resolve_absent:
                    deps.hooks.before_resolve_absent(run_id)
                resolve_now = deps.clock.now()
                observed_fps = {
                    lifecycle.compute_content_and_fingerprint(obs)[1] for obs in all_observed
                }
                with ledger.unit_of_work(conn):
                    lifecycle.resolve_absent(
                        conn,
                        run_id,
                        resolve_now,
                        scanned_scopes=done_scopes,
                        observed_fingerprints=observed_fps,
                    )

                counts = lifecycle.compute_run_counts(conn, run_id)
                final_status = "COMPLETED"
                if config.fail_run_on_task_failure and dead_letter_count > 0:
                    final_status = "FAILED"

                if deps.hooks.before_run_close:
                    deps.hooks.before_run_close(run_id)
                finish_now = deps.clock.now()
                with ledger.unit_of_work(conn):
                    ledger.close_run(
                        conn, run_id, status=final_status, finished_at_utc=finish_now, counts=counts
                    )
                logger.log(
                    "INFO" if final_status == "COMPLETED" else "ERROR",
                    "run.completed" if final_status == "COMPLETED" else "run.failed",
                    now=finish_now,
                    run_id=run_id,
                )

                if deps.hooks.before_report_append:
                    deps.hooks.before_report_append(run_id)
                _write_run_outputs(conn, config, run_id, logger=logger)
                return _outcome_from_ledger(conn, run_id, exit_code=(0 if final_status == "COMPLETED" else 1))

            except Exception as exc:
                fail_now = deps.clock.now()
                with ledger.unit_of_work(conn):
                    ledger.sweep_non_terminal(conn, run_id)
                    fail_counts = lifecycle.compute_run_counts(conn, run_id)
                    ledger.close_run(
                        conn, run_id, status="FAILED", finished_at_utc=fail_now, counts=fail_counts
                    )
                logger.log(
                    "ERROR",
                    "run.failed",
                    now=fail_now,
                    run_id=run_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                _write_run_outputs(conn, config, run_id, logger=logger)
                return _outcome_from_ledger(conn, run_id, exit_code=1)
        finally:
            conn.close()
    finally:
        logger.close()

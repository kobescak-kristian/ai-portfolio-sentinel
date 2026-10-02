#!/usr/bin/env python
"""P5-D durable-latch GitHub-read probe driver (ADR-0012 Amendment B row 24;
Stage 2C-B6-3a, owner ruling D1 of 2026-10-02).

Proves, on a real GitHub runner and with the workflow's actual
``GITHUB_TOKEN``, that every GitHub surface the B6-2 durable replacement
latch and the official execute step depend on can be read and resolved. It
uses the very same ``GithubEvidenceClient`` methods that
``scripts._phase5_common.gather_latch_facts`` and the official runner call.

Structurally model-free and read-only. The workflow that runs it holds
``contents: read`` and ``actions: read`` only (the official gate's
permissions minus ``id-token``), so OIDC is unreachable rather than merely
unused. This driver issues GET requests only, creates no marker, writes no
latch record, imports no OIDC, auth, SDK or marker-writing code, never
executes the bundled CLI, never reads a local clock (GitHub server time
only) and writes exactly one evidence file.

Every check is run and recorded independently, so a failed run still
publishes diagnosable evidence. The process exits 0 only when every
required check passed and 1 otherwise; the evidence file is written either
way.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._phase5_common import (  # noqa: E402
    Phase5ScriptError,
    assert_expected_source_on_disk,
    build_evidence_client,
    discover_oneshot_markers,
    establish_preflight_journal,
    prepare_fresh_work_root,
    write_json_artifact,
)
from sentinel.phase5 import artifact_names  # noqa: E402
from sentinel.phase5.execution_envelope import resolve_job_start_anchor  # noqa: E402
from sentinel.phase5.github_context import GithubContextError, derive_github_context  # noqa: E402
from sentinel.phase5.latch import (  # noqa: E402
    EXECUTE_STEP_NAME,
    GATE_JOB_NAME,
    MAIN_REF,
    MARKER_STEP_NAME,
    OFFICIAL_GATE_WORKFLOW,
)
from sentinel.phase5.readiness import (  # noqa: E402
    PROBE_REQUIRED_CHECKS,
    PROBE_WORKFLOW,
    ProbeCheck,
    ProbeEvidence,
    evaluate_env_names,
    normalize_distribution,
    parse_direct_pins,
)
from sentinel.phase5.replacement import ORIGINAL_PURPOSE, REPLACEMENT_OF_RUN_ID  # noqa: E402
from sentinel.phase5.runtime_identity import (  # noqa: E402
    RuntimeIdentityError,
    capture_runtime_identity,
    sdk_pin_matches,
)

CI_WORKFLOW = ".github/workflows/ci.yml"
LISTING_FLOOR_UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)
PAGINATION_PER_PAGE = 20
RUN_3_ID = "32869033063"
RUN_4_ID = REPLACEMENT_OF_RUN_ID  # official run number 4 (the original, consumed run)
ORIGINAL_MARKER_EXPIRES_UTC = datetime(2026, 11, 23, 17, 56, 54, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parent.parent
_WHOLE_SECOND = {"microsecond": 0}


def _iso(value: datetime) -> str:
    return value.replace(**_WHOLE_SECOND).isoformat()


class _Recorder:
    """Runs each check independently; an exception becomes a failed check
    carrying only the exception type (never a message, body or path)."""

    def __init__(self) -> None:
        self.checks: list[ProbeCheck] = []
        self.server_times: list[datetime] = []

    def run(self, name: str, func) -> object:
        try:
            ok, detail, value = func()
        except Exception as exc:  # noqa: BLE001 - any fault is evidence, never a crash
            self.checks.append(ProbeCheck(name=name, ok=False, detail={"error": type(exc).__name__}))
            return None
        self.checks.append(ProbeCheck(name=name, ok=bool(ok), detail=detail))
        return value


def identity_document(identity) -> dict:
    """The runtime identity in the exact shape the B5 timing evidence used,
    so the readiness evaluator compares like with like."""
    return {
        "runtime_identity_id": identity.runtime_identity_id,
        "python_version": identity.python_version,
        "python_implementation": identity.python_implementation,
        "sys_platform": identity.sys_platform,
        "machine": identity.machine,
        "os_release": identity.os_release,
        "runner_image": identity.runner.model_dump(),
        "sdk": {
            "distribution_name": identity.sdk.distribution_name,
            "version": identity.sdk.version,
            "wheel_tags": list(identity.sdk.wheel_tags),
            "record_sha256": identity.sdk.record_sha256,
            "transport_module": identity.sdk.transport_module.model_dump(),
            "bundled_cli": identity.sdk.bundled_cli.model_dump(),
            "cli_selection": identity.sdk.cli_selection,
        },
        "sdk_pin_matches": sdk_pin_matches(identity),
        "distribution_count": len(identity.distributions),
        "distributions": [[name, version] for name, version in identity.distributions],
    }


def requires_closure(direct_pins) -> dict:
    """Requires-Dist closure of the direct pins (extras ignored): for each
    reachable distribution the sorted list of distributions that require
    it. An over-approximation of what can influence the path, used only to
    make owner adjudication of a transitive difference fast."""
    edges: dict[str, set[str]] = {}
    pending = sorted(direct_pins)
    seen: set[str] = set(pending)
    while pending:
        name = pending.pop()
        try:
            requires = importlib.metadata.distribution(name).requires or []
        except importlib.metadata.PackageNotFoundError:
            continue
        for spec in requires:
            head, _sep, marker = spec.partition(";")
            if "extra" in marker:
                continue
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", head)
            if match is None:
                continue
            requirement = normalize_distribution(match.group(1))
            edges.setdefault(requirement, set()).add(name)
            if requirement not in seen:
                seen.add(requirement)
                pending.append(requirement)
    return {name: sorted(parents) for name, parents in sorted(edges.items())}


def import_audit() -> dict:
    """Import the official runner and finalizer (no execution) and report
    which installed distributions the process then has imported."""
    importlib.import_module("scripts.run_phase5_official_gate")
    importlib.import_module("scripts.run_phase5_gate_finalizer")
    stdlib = set(sys.stdlib_module_names)
    tops = sorted({name.split(".")[0] for name in sys.modules if name and not name.startswith("_")})
    third_party = [t for t in tops if t not in stdlib and t not in {"scripts", "sentinel", "agents", "contracts",
                                                                     "checks", "telemetry", "runner"}]
    mapping = importlib.metadata.packages_distributions()
    distributions = sorted({normalize_distribution(d) for t in third_party for d in mapping.get(t, [])})
    return {"third_party_modules": len(third_party), "imported_distributions": distributions}


def run_probe(args: argparse.Namespace, env) -> int:
    ctx = derive_github_context(env)
    client = build_evidence_client(env)  # pops GITHUB_TOKEN
    if ctx.run_attempt != 1:
        print("PROBE REFUSED: run_attempt must be 1", file=sys.stderr)
        return 2
    assert_expected_source_on_disk(args.expected_source_sha)
    if ctx.sha != args.expected_source_sha:
        print("PROBE REFUSED: GITHUB_SHA differs from the expected source sha", file=sys.stderr)
        return 2

    rec = _Recorder()
    first_time = client.server_time_utc()
    rec.server_times.append(first_time)
    window_end = first_time + timedelta(hours=24)
    direct_pins = parse_direct_pins((REPO_ROOT / "requirements.txt").read_text(encoding="utf-8"))
    state: dict = {}

    def current_run():
        run = client.get_run(ctx.run_id)
        ok = (
            run.run_id == ctx.run_id and run.event == "workflow_dispatch" and run.head_branch == "main"
            and run.sha == args.expected_source_sha and run.run_attempt == 1
            and run.workflow_path == PROBE_WORKFLOW and run.status == "in_progress"
            and isinstance(run.run_number, int) and run.run_number >= 1
        )
        return ok, {"run_number": run.run_number, "status": run.status}, run

    def official_listing():
        runs = client.list_workflow_runs_counted(
            OFFICIAL_GATE_WORKFLOW, created_after=LISTING_FLOOR_UTC, created_before=window_end
        )
        numbers = sorted(r.run_number for r in runs if r.run_number is not None)
        return numbers == [1, 2, 3, 4], {"run_numbers": numbers, "entries": len(runs)}, numbers

    def pagination():
        stats: dict = {}
        client.list_workflow_runs_counted(
            CI_WORKFLOW, created_after=LISTING_FLOOR_UTC, created_before=window_end,
            per_page=PAGINATION_PER_PAGE, stats=stats,
        )
        ok = stats.get("pages", 0) >= 2 and stats.get("entries") == stats.get("total_count")
        return ok, {**stats, "per_page": PAGINATION_PER_PAGE}, stats

    def attempt_jobs():
        jobs = client.list_run_attempt_jobs(ctx.run_id, ctx.run_attempt)
        state["jobs"] = jobs
        named = [j for j in jobs if j.name == GATE_JOB_NAME]
        ok = len(jobs) == 1 and len(named) == 1 and named[0].started_at is not None and bool(named[0].runner_name)
        return ok, {"jobs": len(jobs), "job_name": named[0].name if named else "", "has_started_at": bool(
            named and named[0].started_at is not None)}, jobs

    def job_steps():
        own = client.list_run_attempt_job_evidence(ctx.run_id, ctx.run_attempt)
        hist: dict[str, dict] = {}
        for label, run_id in (("run_3", RUN_3_ID), ("run_4", RUN_4_ID)):
            jobs = client.list_run_attempt_job_evidence(run_id, 1)
            gate = [j for j in jobs if j.name == GATE_JOB_NAME]
            steps = {s.name: s.conclusion for j in gate for s in j.steps}
            hist[label] = {"marker": steps.get(MARKER_STEP_NAME), "execute": steps.get(EXECUTE_STEP_NAME),
                           "job_conclusion": gate[0].conclusion if len(gate) == 1 else None}
        own_steps = sum(len(j.steps) for j in own)
        ok = (
            own_steps > 0 and hist["run_3"] == {"marker": "skipped", "execute": "skipped", "job_conclusion": "failure"}
            and hist["run_4"]["marker"] == "success"
        )
        return ok, {"own_steps": own_steps, "run_3_marker": str(hist["run_3"]["marker"]),
                    "run_3_execute": str(hist["run_3"]["execute"]), "run_4_marker": str(hist["run_4"]["marker"])}, hist

    def anchor():
        jobs = state.get("jobs")
        if jobs is None:
            raise Phase5ScriptError("prerequisite attempt-jobs check failed")
        resolved_at = client.server_time_utc()
        rec.server_times.append(resolved_at)
        # monotonic_at_resolve is unused by this read-only proof; the driver reads no local clock.
        found = resolve_job_start_anchor(
            jobs, run_id=ctx.run_id, run_attempt=ctx.run_attempt,
            expected_workflow_job_id=env.get("GITHUB_JOB", ""), expected_api_job_name=GATE_JOB_NAME,
            expected_runner_name=env.get("RUNNER_NAME", ""), resolved_at_utc=resolved_at, monotonic_at_resolve=0.0,
        )
        return True, {"resolved": True, "job_started_at_utc": _iso(found.job_started_at_utc),
                      "api_job_name": found.api_job_name, "workflow_job_id": found.workflow_job_id}, found

    def commit():
        detail = client.get_commit(ctx.sha)
        patches = [f.patch is not None for f in detail.files]
        ok = detail.sha == ctx.sha and len(detail.parents) >= 1 and len(detail.files) >= 1 and any(patches)
        return ok, {"parents": len(detail.parents), "files": len(detail.files), "with_patch": sum(patches)}, detail

    def push_activity():
        entries = client.list_push_activity(MAIN_REF)
        matching = [e for e in entries if e.after == ctx.sha]
        ok = len(matching) == 1 and matching[0].ref == MAIN_REF and matching[0].activity_type == "push"
        detail = {"entries": len(entries), "matching": len(matching)}
        if matching:
            detail["timestamp_utc"] = _iso(matching[0].timestamp)
        return ok, detail, matching

    def artifact_discovery():
        gate_evidence = client.list_artifacts(artifact_names.GATE_EVIDENCE_PREFIX)
        oneshot = client.list_artifacts(artifact_names.ONESHOT_PREFIX)
        replacement = [a for a in oneshot if "p5d-replacement-sonnet-gate" in a.name]
        ok = not gate_evidence and not replacement
        return ok, {"gate_evidence_prefix_count": len(gate_evidence), "oneshot_prefix_count": len(oneshot),
                    "replacement_marker_count": len(replacement)}, None

    def layout():
        work_root = prepare_fresh_work_root(args.work_root)
        state["work_root"] = work_root
        artifacts = work_root / "artifacts"
        journal = establish_preflight_journal(artifacts)
        return True, {"journal_established": journal.name == "phase5_gate_journal.jsonl"}, None

    def original_marker():
        work_root = state.get("work_root")
        if work_root is None:
            raise Phase5ScriptError("prerequisite layout check failed")
        markers = discover_oneshot_markers(client, work_root)
        found = [m for m in markers if m.purpose == ORIGINAL_PURPOSE and m.github_run_id == RUN_4_ID]
        expired = rec.server_times[-1] > ORIGINAL_MARKER_EXPIRES_UTC
        ok = len(found) == 1 or (not found and expired)
        return ok, {"found": len(found), "markers_discovered": len(markers),
                    "expired_covered_by_receipt": bool(not found and expired)}, None

    def import_closure():
        audit = import_audit()
        return True, {"imported": True, **audit}, None

    def env_scan():
        outcome = evaluate_env_names(env.keys())
        return outcome.status == "PASS", {"flagged": list(outcome.evidence.get("present", [])),
                                          "env_names": len(list(env.keys()))}, None

    identity = None

    def runtime_identity():
        nonlocal identity
        try:
            identity = capture_runtime_identity(env=env)
        except RuntimeIdentityError:
            return False, {"captured": False}, None
        doc = identity_document(identity)
        doc["requires_closure"] = requires_closure(direct_pins)
        state["identity_doc"] = doc
        return bool(doc["sdk_pin_matches"]), {"runtime_identity_id": doc["runtime_identity_id"],
                                              "sdk_pin_matches": bool(doc["sdk_pin_matches"]),
                                              "distribution_count": doc["distribution_count"]}, doc

    rec.run("current_run", current_run)
    rec.run("official_listing", official_listing)
    rec.run("pagination", pagination)
    rec.run("attempt_jobs", attempt_jobs)
    rec.run("job_steps", job_steps)
    rec.run("anchor", anchor)
    rec.run("commit", commit)
    rec.run("push_activity", push_activity)
    rec.run("artifact_discovery", artifact_discovery)
    rec.run("layout", layout)
    rec.run("original_marker", original_marker)
    rec.run("import_closure", import_closure)
    rec.run("env_scan", env_scan)
    rec.run("runtime_identity", runtime_identity)

    def server_time():
        last = client.server_time_utc()
        rec.server_times.append(last)
        times = rec.server_times
        monotonic = all(b >= a for a, b in zip(times, times[1:]))
        return monotonic, {"monotonic": monotonic, "reads": len(times)}, last

    rec.run("server_time", server_time)

    failed = [c.name for c in rec.checks if not c.ok]
    missing = [n for n in PROBE_REQUIRED_CHECKS if n not in {c.name for c in rec.checks}]
    result = "PASS" if not failed and not missing else "STOP"
    evidence = ProbeEvidence(
        schema_version=1, lane="latch-read-probe", run_id=ctx.run_id, run_attempt=ctx.run_attempt,
        event=ctx.event, ref=ctx.ref, sha=ctx.sha, workflow_identity=ctx.workflow_path, result=result,
        stop_reason=None if result == "PASS" else "failed checks: " + ", ".join(failed + missing),
        server_time_first_utc=_iso(rec.server_times[0]), server_time_last_utc=_iso(rec.server_times[-1]),
        checks=tuple(sorted(rec.checks, key=lambda c: c.name)),
        runtime_identity=state.get("identity_doc"),
    )
    write_json_artifact(evidence.model_dump(mode="json"), args.evidence_out)
    print(f"PROBE {result}: {len(rec.checks)} checks, {len(failed)} failed")
    return 0 if result == "PASS" else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--evidence-out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return run_probe(args, os.environ)
    except (Phase5ScriptError, GithubContextError) as exc:
        print(f"PROBE REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

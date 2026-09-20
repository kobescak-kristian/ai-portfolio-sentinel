#!/usr/bin/env python
"""P5-D model-free GitHub job-level kill-rehearsal driver (ADR-0012
section 12 and Amendment A5/A8; approved plan
q77-p5d-repair-stage2cb1-plan-d; owner ruling
q77-p5d-stage2cb1-finalizer-ruling-a, Stage 2C-B1).

This driver builds the REHEARSAL SURFACE only. Landing it executes
nothing: the workflow that runs it is ``workflow_dispatch``-only and is
never dispatched by the implementing stage.

Structurally model-free. There is no provider call, no Agent-SDK query,
no bundled-CLI execution, no OIDC/WIF exchange, no federation rule, no
one-shot marker of any purpose, and no network contact of any kind. The
SDK is only ever INSPECTED -- its installed transport source is parsed
with ``ast`` and never imported or run.

Two subcommands, run by ``.github/workflows/sentinel-kill-rehearsal.yml``:

``probe`` establishes the real terminal layout and RUNNER operational
journal, captures the Linux runtime identity, performs the C-static
transport-source inspection, runs the class-A and class-B process
topology probes, and writes the observations JSON. It writes that JSON
OUTSIDE the publication root, so the finalizer's allowlist never sees
it as an unexpected publication file.

``fake-execute`` installs the signal-observing journal handlers and then
sleeps far past the job-level timeout. It deliberately writes NO
terminal evidence: the whole point is that the platform kills it and
the real finalizer has to produce execution-invalid evidence afterwards.

Process topology is reasoned in PPID ancestry terms throughout, because
``agents.checker.process_control`` discovers descendants purely through
``/proc`` parent-child edges: ``ProcessStat`` carries no PGID and no
SID, and no process-group signalling exists. A new session or process
group therefore does NOT by itself remove a process from the descendant
closure, and is recorded here as descriptive metadata only.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._phase5_common import (  # noqa: E402
    Phase5ScriptError,
    assert_expected_source_on_disk,
    establish_preflight_journal,
    prepare_fresh_work_root,
    write_json_artifact,
)
from scripts.run_phase5_gate_finalizer import (  # noqa: E402
    REHEARSAL_MODEL,
    REHEARSAL_PROFILE_NAME,
    REHEARSAL_PURPOSE,
)
from sentinel.phase5.github_context import derive_github_context  # noqa: E402
from sentinel.phase5.journal import (  # noqa: E402
    OperationalJournal,
    install_observing_signal_handlers,
)
from sentinel.phase5.runtime_identity import (  # noqa: E402
    RuntimeIdentityError,
    capture_runtime_identity,
    sdk_pin_matches,
)
from sentinel.phase5.terminal import JOURNAL_FILENAME  # noqa: E402

from agents.checker.process_control import (  # noqa: E402
    CONTROL_CONFIG,
    ProcessControlUnsupported,
    descendants_of,
    read_process_stat,
    read_process_table,
    terminate_descendants,
)

OBSERVATIONS_FILENAME = "phase5_kill_rehearsal_observations.json"
SCHEMA_VERSION = 1
LANE = "P5D_KILL_REHEARSAL"

C_DYNAMIC_RESIDUAL = "REAL_CLI_TOPOLOGY_UNOBSERVED"
C_DYNAMIC_CLOSURE_SPEC = "ADR-0012 s13 N=24 Sonnet timing rehearsal"

ANCESTRY_PRESERVED = "ANCESTRY_PRESERVED_AT_PYTHON_LAYER"
ANCESTRY_ESCAPE = "ANCESTRY_ESCAPE_MECHANISM_PRESENT"
ANCESTRY_UNKNOWN = "UNKNOWN"

DEFAULT_SLEEP_SECONDS = 1800
DEFAULT_HEARTBEAT_SECONDS = 30
PROBE_LIFETIME_SECONDS = 600
PROBE_SETTLE_SECONDS = 2.0
PROBE_READ_TIMEOUT_SECONDS = 30.0

# Spawn APIs that create a DIRECT child of this interpreter. None of
# these, by itself, breaks PPID ancestry.
_DIRECT_SPAWN_APIS = frozenset({
    "anyio.open_process",
    "anyio.run_process",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "subprocess.Popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "os.posix_spawn",
    "os.posix_spawnp",
})

# Keyword arguments that isolate the child's SESSION or PROCESS GROUP.
# Recorded descriptively; NEVER an ancestry-escape signal, because
# descendant discovery walks PPID and never consults SID or PGID.
_SESSION_ISOLATION_KEYWORDS = ("start_new_session", "preexec_fn", "process_group", "creationflags")

# Calls whose purpose or effect is reparenting execution away from this
# interpreter's PPID ancestry before discovery could occur.
_ANCESTRY_BREAKING_CALLS = frozenset({"os.fork", "os.forkpty", "os.daemon", "os._exit_after_fork"})

# Command words that hand execution to a launcher which then exits, or
# to a service manager, leaving the real process outside our ancestry.
_ANCESTRY_BREAKING_COMMAND_WORDS = ("nohup", "setsid", "systemd-run", "disown", "start-stop-daemon", "daemonize")


class KillRehearsalError(Phase5ScriptError):
    """The rehearsal harness refused before fake execution began."""


# ---------------------------------------------------------------------------
# C-static: transport-source inspection (no import, no execution)
# ---------------------------------------------------------------------------


def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    else:
        return ""
    return ".".join(reversed(parts))


def inspect_transport_source(source_text: str) -> dict:
    """Statically classify how the pinned SDK transport creates the CLI
    process. Parses only; never imports and never executes.

    Reports three SEPARATE groups, per approved plan-d:

    1. child creation mechanism;
    2. session / process-group configuration -- DESCRIPTIVE ONLY;
    3. source-visible ancestry-breaking mechanisms.

    The verdict concerns group 3 alone. ``start_new_session=True``,
    ``preexec_fn=os.setsid``, an explicit ``process_group`` or any
    differing SID/PGID are recorded in group 2 and can never, on their
    own, produce an escape verdict -- ``descendants_of`` follows PPID
    edges and does not consult sessions or process groups."""
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return {
            "child_creation": {"spawn_apis": [], "direct_child": ANCESTRY_UNKNOWN},
            "session_isolation": {key: False for key in _SESSION_ISOLATION_KEYWORDS},
            "ancestry_breaking": {"mechanisms_found": [], "verdict": ANCESTRY_UNKNOWN},
            "parse_error": True,
        }

    spawn_apis: set[str] = set()
    session_isolation = {key: False for key in _SESSION_ISOLATION_KEYWORDS}
    mechanisms: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted_name(node.func)
            if name in _DIRECT_SPAWN_APIS:
                spawn_apis.add(name)
            # A dotted tail still identifies the API when imported as a
            # module attribute chain (e.g. ``anyio.open_process``).
            for api in _DIRECT_SPAWN_APIS:
                if name and name.endswith("." + api.split(".")[-1]) and api.split(".")[-1] == name.split(".")[-1]:
                    if name.split(".")[0] == api.split(".")[0]:
                        spawn_apis.add(api)
            if name in _ANCESTRY_BREAKING_CALLS:
                mechanisms.add(name)
            for keyword in node.keywords:
                if keyword.arg in session_isolation:
                    session_isolation[keyword.arg] = True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.strip().lower()
            for word in _ANCESTRY_BREAKING_COMMAND_WORDS:
                if lowered == word or lowered.startswith(word + " ") or lowered.endswith("/" + word):
                    mechanisms.add(f"command:{word}")

    if mechanisms:
        verdict = ANCESTRY_ESCAPE
        direct_child = False
    elif spawn_apis:
        verdict = ANCESTRY_PRESERVED
        direct_child = True
    else:
        verdict = ANCESTRY_UNKNOWN
        direct_child = ANCESTRY_UNKNOWN

    return {
        "child_creation": {"spawn_apis": sorted(spawn_apis), "direct_child": direct_child},
        "session_isolation": session_isolation,
        "ancestry_breaking": {"mechanisms_found": sorted(mechanisms), "verdict": verdict},
        "parse_error": False,
    }


def _transport_source_path(identity) -> Path:
    """Resolve the installed transport module recorded by
    ``runtime_identity`` back to a real path, without importing it."""
    import importlib.metadata

    distribution = importlib.metadata.distribution("claude-agent-sdk")
    record_path = identity.sdk.transport_module.record_path
    resolved = distribution.locate_file(record_path)
    return Path(str(resolved))


def capture_c_static(identity) -> dict:
    recorded = identity.sdk.transport_module
    path = _transport_source_path(identity)
    result = inspect_transport_source(path.read_text(encoding="utf-8", errors="replace"))
    result["transport_source"] = {
        "record_path": recorded.record_path,
        "sha256_declared": recorded.sha256_declared,
        "sha256_actual": recorded.sha256_actual,
        "matches": recorded.sha256_actual == recorded.sha256_declared,
    }
    result["inference_limits"] = [
        "Static inspection covers the Python transport layer only.",
        "It cannot establish whether the bundled Node CLI forks, daemonizes or "
        "reparents itself after launch; that remains C-dynamic.",
        "A verdict of UNKNOWN is an honest non-result and never evidence of safety.",
    ]
    return result


# ---------------------------------------------------------------------------
# Process topology probes (PPID ancestry)
# ---------------------------------------------------------------------------

_CLASS_A_CHILD = (
    "import subprocess, sys, time\n"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep({lifetime})'])\n"
    "sys.stdout.write(str(g.pid) + '\\n'); sys.stdout.flush()\n"
    "time.sleep({lifetime})\n"
)

# Deliberately adversarial: the intermediate child exits immediately so
# the survivor is reparented to init (or the nearest subreaper) and
# leaves this interpreter's PPID descendant closure before discovery.
_CLASS_B_CHILD = (
    "import os, sys, time\n"
    "if os.fork() > 0:\n"
    "    os._exit(0)\n"
    "sys.stdout.write(str(os.getpid()) + '\\n'); sys.stdout.flush()\n"
    "time.sleep({lifetime})\n"
)


def _identity_of(pid: int, proc_root: Path) -> "tuple[int, int] | None":
    stat = read_process_stat(pid, proc_root)
    return None if stat is None else (stat.pid, stat.starttime)


def _kill_if_same_identity(identity: "tuple[int, int] | None", proc_root: Path) -> bool:
    """Reuse-safe kill: only signals when (pid, starttime) still match,
    so a recycled PID is never hit. Returns True when the pid is gone."""
    if identity is None:
        return True
    pid, starttime = identity
    for _ in range(40):
        current = read_process_stat(pid, proc_root)
        if current is None or current.starttime != starttime:
            return True
        if current.state == "Z":
            return True
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    current = read_process_stat(pid, proc_root)
    return current is None or current.starttime != starttime or current.state == "Z"


def _read_pid_line(process) -> "int | None":
    deadline = time.monotonic() + PROBE_READ_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if line:
            try:
                return int(line.strip())
            except ValueError:
                return None
        if process.poll() is not None and not line:
            return None
    return None


def run_topology_probes(*, proc_root: Path = Path("/proc")) -> dict:
    """Class A: an ordinary child -> grandchild tree whose ancestry is
    intact; it MUST be discovered and terminated.

    Class B: a deliberately double-forked survivor whose intermediate
    parent exits, reparenting it out of this interpreter's PPID closure.
    Its escape confirms the documented boundary of descendant walking
    and is EVIDENCE, not a failure -- but the harness must never leak
    it, so it is always cleaned up by reuse-safe identity.
    """
    import subprocess

    if not sys.platform.startswith("linux"):
        raise ProcessControlUnsupported("the topology probes require Linux /proc")

    me = os.getpid()
    class_a = None
    class_b = None
    a_child = None
    b_child = None
    a_child_identity = None
    a_grandchild_identity = None
    b_survivor_identity = None
    try:
        a_child = subprocess.Popen(
            [sys.executable, "-c", _CLASS_A_CHILD.format(lifetime=PROBE_LIFETIME_SECONDS)],
            stdout=subprocess.PIPE, text=True,
        )
        a_grandchild_pid = _read_pid_line(a_child)
        b_child = subprocess.Popen(
            [sys.executable, "-c", _CLASS_B_CHILD.format(lifetime=PROBE_LIFETIME_SECONDS)],
            stdout=subprocess.PIPE, text=True,
        )
        b_survivor_pid = _read_pid_line(b_child)
        time.sleep(PROBE_SETTLE_SECONDS)

        a_child_identity = _identity_of(a_child.pid, proc_root)
        a_grandchild_identity = _identity_of(a_grandchild_pid, proc_root) if a_grandchild_pid else None
        b_survivor_identity = _identity_of(b_survivor_pid, proc_root) if b_survivor_pid else None

        table = read_process_table(proc_root)
        closure = {stat.pid for stat, _depth in descendants_of(me, table)}

        def _describe(pid: "int | None") -> dict:
            stat = table.get(pid) if pid else None
            return {
                "pid": pid,
                "ppid": stat.ppid if stat else None,
                "starttime": stat.starttime if stat else None,
                "in_ppid_descendant_closure": bool(pid in closure) if pid else False,
            }

        class_a_processes = [_describe(a_child.pid), _describe(a_grandchild_pid)]
        b_stat = table.get(b_survivor_pid) if b_survivor_pid else None
        class_b = {
            "pid": b_survivor_pid,
            "ppid_before_parent_exit": b_child.pid if b_child else None,
            "ppid_after_reparent": b_stat.ppid if b_stat else None,
            "starttime": b_stat.starttime if b_stat else None,
            "in_ppid_descendant_closure": bool(b_survivor_pid in closure) if b_survivor_pid else False,
            "reparented": bool(b_survivor_pid) and b_survivor_pid not in closure,
            "note": (
                "Escape here confirms the documented PPID-walking boundary and is evidence, "
                "not a failure. It does not describe the real bundled CLI."
            ),
        }

        report = terminate_descendants(CONTROL_CONFIG, proc_root=proc_root)
        after = read_process_table(proc_root)
        for entry in class_a_processes:
            pid = entry["pid"]
            still = after.get(pid) if pid else None
            entry["survived_termination"] = bool(
                still is not None and still.starttime == entry["starttime"] and still.state != "Z"
            )
        class_a = {
            "root_pid_is_driver": True,
            "processes": class_a_processes,
            "termination_report": {
                "discovered": report.discovered,
                "late_discovered": report.late_discovered,
                "max_depth": report.max_depth,
                "scans": report.scans,
                "term_signalled": report.term_signalled,
                "kill_signalled": report.kill_signalled,
                "terminated": report.terminated,
                "survivors": report.survivors,
                "elapsed_ms": report.elapsed_ms,
            },
        }
        return {"class_a": class_a, "class_b": class_b}
    finally:
        # Deterministic cleanup of EVERY synthetic process this harness
        # created, including the deliberately escaped class-B survivor.
        leaked: list[str] = []
        for label, identity in (
            ("class_a_grandchild", a_grandchild_identity),
            ("class_a_child", a_child_identity),
            ("class_b_survivor", b_survivor_identity),
        ):
            if not _kill_if_same_identity(identity, proc_root):
                leaked.append(label)
        for process in (a_child, b_child):
            if process is None:
                continue
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
            except Exception:  # noqa: BLE001 - best-effort reap; leak check below is authoritative
                pass
            finally:
                if process.stdout is not None:
                    process.stdout.close()
        if leaked:
            raise KillRehearsalError(f"synthetic probe processes were not cleaned up: {sorted(leaked)}")


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def _no_provider_path(env) -> dict:
    """Structural self-report: this lane has no provider surface at all."""
    return {
        "id_token_permission_present": False,
        "anthropic_env_present": any(key.startswith("ANTHROPIC_") for key in env),
        "oidc_attempted": False,
        "provider_calls": 0,
        "marker_created": False,
        "oneshot_consumed": False,
        "bundled_cli_executed": False,
    }


def cmd_probe(args: argparse.Namespace) -> int:
    env = os.environ
    ctx = derive_github_context(env)
    source_sha = assert_expected_source_on_disk(args.expected_source_sha)
    prepare_fresh_work_root(args.work_root)
    establish_preflight_journal(args.artifacts_dir)

    try:
        identity = capture_runtime_identity(env=env)
    except RuntimeIdentityError as exc:
        raise KillRehearsalError(f"runtime identity capture failed: {exc}") from exc

    observations = {
        "schema_version": SCHEMA_VERSION,
        "lane": LANE,
        "identity": {
            "workflow_identity": ctx.workflow_path,
            "run_id": ctx.run_id,
            "run_attempt": ctx.run_attempt,
            "event": ctx.event,
            "ref": ctx.ref,
            "source_sha": source_sha,
            "expected_source_sha": args.expected_source_sha,
            "purpose": REHEARSAL_PURPOSE,
            "model": REHEARSAL_MODEL,
            "profile_name": REHEARSAL_PROFILE_NAME,
        },
        "no_provider_path": _no_provider_path(env),
        "runtime_identity": {
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
            "distribution_count": len(identity.distributions),
            "sdk_pin_matches": sdk_pin_matches(identity),
        },
        "topology": {
            **run_topology_probes(),
            "class_c_static": capture_c_static(identity),
            "class_c_dynamic": {
                "observed": False,
                "residual": C_DYNAMIC_RESIDUAL,
                "closure_spec": C_DYNAMIC_CLOSURE_SPEC,
                "note": (
                    "The model-free rehearsal never spawns the bundled CLI, so the real "
                    "CLI process ancestry under an actual query is not observed here. "
                    "This rehearsal alone does not close the reparenting residual."
                ),
            },
        },
        "fake_execution": {
            "planned_sleep_seconds": args.sleep_seconds,
            "heartbeat_interval_seconds": args.heartbeat_seconds,
        },
    }
    write_json_artifact(observations, args.observations_out)
    print(
        "PROBE: lane=%s c_static=%s class_a_survivors=%s class_b_reparented=%s"
        % (
            LANE,
            observations["topology"]["class_c_static"]["ancestry_breaking"]["verdict"],
            observations["topology"]["class_a"]["termination_report"]["survivors"],
            observations["topology"]["class_b"]["reparented"],
        )
    )
    return 0


# ---------------------------------------------------------------------------
# fake-execute
# ---------------------------------------------------------------------------


def cmd_fake_execute(args: argparse.Namespace) -> int:
    """Deliberately outlive the job-level timeout.

    Writes NO terminal evidence: the real finalizer must produce the
    execution-invalid record afterwards. Records no state transition
    either -- the only transition permitted out of PREFLIGHTED names a
    marker, and this lane has none."""
    journal_path = Path(args.artifacts_dir) / JOURNAL_FILENAME
    journal = OperationalJournal(journal_path, writer="RUNNER").open()
    restore_signal_handlers = install_observing_signal_handlers(journal)
    started = datetime.now(timezone.utc)
    print(f"FAKE-EXECUTE: started_at_utc={started.isoformat()} sleep_seconds={args.sleep_seconds}")
    print("FAKE-EXECUTE: no provider call, no OIDC, no marker, no terminal evidence is written here")
    sys.stdout.flush()
    deadline = time.monotonic() + args.sleep_seconds
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(args.heartbeat_seconds, remaining))
            elapsed = int(args.sleep_seconds - max(0.0, deadline - time.monotonic()))
            print(f"FAKE-EXECUTE: heartbeat elapsed_seconds={elapsed}")
            sys.stdout.flush()
    finally:
        restore_signal_handlers()
        journal.close()
    print("FAKE-EXECUTE: slept to completion without being killed")
    return 0


def main(argv: "list[str]") -> int:
    parser = argparse.ArgumentParser(description="P5-D model-free kill-rehearsal driver")
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe")
    probe.add_argument("--expected-source-sha", required=True)
    probe.add_argument("--work-root", type=Path, required=True)
    probe.add_argument("--artifacts-dir", type=Path, required=True)
    probe.add_argument("--observations-out", type=Path, required=True)
    probe.add_argument("--sleep-seconds", type=int, default=DEFAULT_SLEEP_SECONDS)
    probe.add_argument("--heartbeat-seconds", type=int, default=DEFAULT_HEARTBEAT_SECONDS)

    fake = sub.add_parser("fake-execute")
    fake.add_argument("--artifacts-dir", type=Path, required=True)
    fake.add_argument("--sleep-seconds", type=int, default=DEFAULT_SLEEP_SECONDS)
    fake.add_argument("--heartbeat-seconds", type=int, default=DEFAULT_HEARTBEAT_SECONDS)

    args = parser.parse_args(argv)
    try:
        if args.command == "probe":
            return cmd_probe(args)
        return cmd_fake_execute(args)
    except Phase5ScriptError as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

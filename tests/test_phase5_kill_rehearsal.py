"""Tests for scripts/run_phase5_kill_rehearsal.py (ADR-0012 section 12
and Amendment A5/A8; approved plan q77-p5d-repair-stage2cb1-plan-d;
owner ruling q77-p5d-stage2cb1-finalizer-ruling-a, Stage 2C-B1).

Model-free and network-blocked (tests/conftest.py ``block_network``).
Nothing here executes the rehearsal workflow, calls a provider, runs the
bundled CLI, performs OIDC, or creates/consumes a marker of any purpose.

The topology assertions are written in PPID ancestry terms on purpose:
``agents.checker.process_control`` discovers descendants purely through
``/proc`` parent-child edges (``ProcessStat`` carries no PGID and no
SID), so session or process-group isolation can never, on its own,
remove a process from the descendant closure.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

from sentinel.phase5 import terminal as t
from sentinel.phase5.artifact_names import ArtifactNameError, oneshot_marker_name
from sentinel.phase5.journal import read_journal

REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVER_PATH = REPO_ROOT / "scripts" / "run_phase5_kill_rehearsal.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "sentinel-kill-rehearsal.yml"

LINUX_ONLY = pytest.mark.skipif(
    sys.platform != "linux", reason="real /proc ancestry probes are Linux-only"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kr():
    return _load(DRIVER_PATH, "run_phase5_kill_rehearsal")


# ---------------------------------------------------------------------------
# Structural: no provider / OIDC / marker surface
# ---------------------------------------------------------------------------


def test_driver_imports_no_provider_network_or_sdk_module():
    """Structural, not substring-based: the evidence payload legitimately
    NAMES the provider surfaces it is attesting are absent (for example
    ``anthropic_env_present``), so the real assertion is that no such
    module is imported and no network call exists."""
    tree = ast.parse(DRIVER_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    for banned in ("claude_agent_sdk", "anthropic", "urllib", "requests", "http", "socket", "ssl", "anyio"):
        assert banned not in imported, banned

    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            parts: list[str] = []
            while isinstance(func, ast.Attribute):
                parts.append(func.attr)
                func = func.value
            if isinstance(func, ast.Name):
                parts.append(func.id)
            called.add(".".join(reversed(parts)))
    for banned in ("urlopen", "requests.get", "requests.post", "socket.socket", "os.execv"):
        assert banned not in called, banned


def test_driver_carries_no_credential_federation_or_endpoint_literal():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    for token in (
        "ANTHROPIC_IDENTITY_TOKEN_FILE", "ANTHROPIC_FEDERATION_RULE_ID",
        "ANTHROPIC_API_KEY", "id-token", "FEDERATION_RULE", "sk-ant",
        "http://", "https://", "sentinel-p5-oneshot", "oneshot_marker_name",
    ):
        assert token not in source, token


def test_driver_reports_every_absent_provider_surface_as_evidence(kr):
    report = kr._no_provider_path({})
    assert report["id_token_permission_present"] is False
    assert report["anthropic_env_present"] is False
    assert report["oidc_attempted"] is False
    assert report["provider_calls"] == 0
    assert report["marker_created"] is False
    assert report["oneshot_consumed"] is False
    assert report["bundled_cli_executed"] is False
    # The env probe is real, not hard-coded.
    assert kr._no_provider_path({"ANTHROPIC_API_KEY": "x"})["anthropic_env_present"] is True


def test_driver_never_names_the_official_gate_profile_or_builds_a_control_config():
    """Both literals are banned outside their allowlisted files by
    repo-wide scans (tests/test_execution_profile.py and
    tests/test_phase5_execution_control.py). The driver uses the frozen
    CONTROL_CONFIG instance instead of constructing one."""
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "SONNET_OFFICIAL_GATE" not in source
    assert "ExecutionControlConfig(" not in source
    assert "CONTROL_CONFIG" in source


def test_driver_imports_no_agent_sdk_and_only_inspects_its_source(kr):
    tree = ast.parse(DRIVER_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert "claude_agent_sdk" not in imported
    assert "ast" in imported  # static inspection is the only SDK contact


def test_rehearsal_purpose_can_never_name_a_one_shot_artifact(kr):
    """The lane is structurally incapable of naming a marker, not merely
    abstaining from one: its purpose is absent from _PURPOSE_SLUGS."""
    assert kr.REHEARSAL_PURPOSE == "P5D_KILL_REHEARSAL"
    with pytest.raises(ArtifactNameError):
        oneshot_marker_name(kr.REHEARSAL_PURPOSE, "4242")


def test_observations_filename_is_outside_the_publication_allowlist(kr):
    """If observations landed in the publication root the finalizer would
    quarantine them as UNEXPECTED, so they must live elsewhere."""
    assert kr.OBSERVATIONS_FILENAME not in t.PUBLICATION_ALLOWLIST
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "/rehearsal/" + kr.OBSERVATIONS_FILENAME in workflow
    assert "/artifacts/" + kr.OBSERVATIONS_FILENAME not in workflow


# ---------------------------------------------------------------------------
# C-static: PPID ancestry, NOT session / process-group isolation
# ---------------------------------------------------------------------------

_SESSION_ISOLATED_SOURCES = {
    "start_new_session": "import anyio\nasync def go():\n    return await anyio.open_process(cmd, start_new_session=True)\n",
    "preexec_fn_setsid": "import os, subprocess\nsubprocess.Popen(cmd, preexec_fn=os.setsid)\n",
    "process_group": "import subprocess\nsubprocess.Popen(cmd, process_group=0)\n",
    "creationflags": "import subprocess\nsubprocess.Popen(cmd, creationflags=8)\n",
}


@pytest.mark.parametrize("label,source", sorted(_SESSION_ISOLATED_SOURCES.items()))
def test_session_or_process_group_isolation_alone_is_never_an_ancestry_escape(kr, label, source):
    """descendants_of walks PPID edges and never consults SID or PGID, so
    a new session or process group leaves ancestry intact. These are
    recorded descriptively and can never produce an escape verdict."""
    result = kr.inspect_transport_source(source)
    assert result["ancestry_breaking"]["verdict"] == kr.ANCESTRY_PRESERVED
    assert result["ancestry_breaking"]["mechanisms_found"] == []
    assert any(result["session_isolation"].values())
    assert result["child_creation"]["direct_child"] is True


@pytest.mark.parametrize(
    "source",
    [
        "import os\ndef go():\n    if os.fork() > 0:\n        os._exit(0)\n",
        "import subprocess\nsubprocess.Popen(['nohup', 'claude'])\n",
        "import subprocess\nsubprocess.Popen(['setsid', 'claude'])\n",
        "import subprocess\nsubprocess.Popen(['systemd-run', 'claude'])\n",
    ],
)
def test_real_ancestry_breaking_mechanism_yields_escape_verdict(kr, source):
    result = kr.inspect_transport_source(source)
    assert result["ancestry_breaking"]["verdict"] == kr.ANCESTRY_ESCAPE
    assert result["ancestry_breaking"]["mechanisms_found"]


@pytest.mark.parametrize("source", ["def go():\n    return magic_runner(cmd)\n", "def (", ""])
def test_unresolvable_source_is_unknown_and_never_a_manufactured_pass(kr, source):
    result = kr.inspect_transport_source(source)
    assert result["ancestry_breaking"]["verdict"] == kr.ANCESTRY_UNKNOWN
    assert result["child_creation"]["direct_child"] == kr.ANCESTRY_UNKNOWN
    assert result["ancestry_breaking"]["mechanisms_found"] == []


def test_c_static_verdict_vocabulary_is_exactly_three_values(kr):
    assert {kr.ANCESTRY_PRESERVED, kr.ANCESTRY_ESCAPE, kr.ANCESTRY_UNKNOWN} == {
        "ANCESTRY_PRESERVED_AT_PYTHON_LAYER", "ANCESTRY_ESCAPE_MECHANISM_PRESENT", "UNKNOWN",
    }
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "spawn_detached" not in source


def test_c_static_records_transport_identity_and_inference_limits(kr):
    """The Python-layer result must carry its own bounds: it can never
    close behaviour inside the bundled Node CLI."""
    from sentinel.phase5.runtime_identity import capture_runtime_identity

    result = kr.capture_c_static(capture_runtime_identity(env=os.environ))
    assert result["transport_source"]["matches"] is True
    assert result["transport_source"]["record_path"].endswith("subprocess_cli.py")
    joined = " ".join(result["inference_limits"]).lower()
    assert "bundled node cli" in joined
    assert "unknown" in joined


def test_c_dynamic_residual_is_carried_forward_unclosed(kr):
    assert kr.C_DYNAMIC_RESIDUAL == "REAL_CLI_TOPOLOGY_UNOBSERVED"
    assert "timing rehearsal" in kr.C_DYNAMIC_CLOSURE_SPEC


# ---------------------------------------------------------------------------
# Class A / Class B ancestry probes
# ---------------------------------------------------------------------------


def test_topology_probes_refuse_off_linux(kr, monkeypatch):
    from agents.checker.process_control import ProcessControlUnsupported

    monkeypatch.setattr(kr.sys, "platform", "win32")
    with pytest.raises(ProcessControlUnsupported):
        kr.run_topology_probes()


@LINUX_ONLY
def test_class_a_tree_is_discovered_and_terminated_and_class_b_escapes(kr):
    result = kr.run_topology_probes()

    class_a = result["class_a"]
    assert class_a["termination_report"]["survivors"] == 0
    assert len(class_a["processes"]) == 2
    for entry in class_a["processes"]:
        assert entry["pid"] is not None
        assert entry["in_ppid_descendant_closure"] is True, entry
        assert entry["survived_termination"] is False, entry

    class_b = result["class_b"]
    assert class_b["pid"] is not None
    # Its intermediate parent exited, so it is no longer our descendant.
    assert class_b["in_ppid_descendant_closure"] is False
    assert class_b["reparented"] is True
    assert class_b["ppid_after_reparent"] != os.getpid()


@LINUX_ONLY
def test_probes_leak_no_synthetic_process_including_the_escaped_one(kr):
    """Class-B escape is evidence of the documented boundary, never
    permission to leave an orphan alive."""
    from agents.checker.process_control import read_process_stat

    result = kr.run_topology_probes()
    pids = [entry["pid"] for entry in result["class_a"]["processes"]]
    pids.append(result["class_b"]["pid"])
    starts = [entry["starttime"] for entry in result["class_a"]["processes"]]
    starts.append(result["class_b"]["starttime"])
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        alive = [
            pid for pid, start in zip(pids, starts)
            if pid is not None
            and (stat := read_process_stat(pid, Path("/proc"))) is not None
            and stat.starttime == start and stat.state != "Z"
        ]
        if not alive:
            break
        time.sleep(0.2)
    assert not alive, alive


def test_cleanup_failure_raises_before_fake_execution_can_begin(kr, monkeypatch):
    """A probe that cannot clean up must fail the step, not proceed."""
    monkeypatch.setattr(kr, "_kill_if_same_identity", lambda identity, proc_root: False)
    monkeypatch.setattr(kr.sys, "platform", "linux")

    class _Boom(Exception):
        pass

    def _explode(*args, **kwargs):
        raise _Boom("probe body stopped early on purpose")

    monkeypatch.setattr(kr, "read_process_table", _explode)
    with pytest.raises(kr.KillRehearsalError, match="not cleaned up"):
        kr.run_topology_probes()


def test_reuse_safe_kill_never_signals_a_recycled_pid(kr, monkeypatch):
    """Only a matching (pid, starttime) identity is ever signalled."""
    from agents.checker.process_control import ProcessStat

    signalled: list = []
    monkeypatch.setattr(kr.os, "kill", lambda pid, sig: signalled.append((pid, sig)))
    recycled = ProcessStat(pid=4242, ppid=1, state="S", starttime=999)
    monkeypatch.setattr(kr, "read_process_stat", lambda pid, proc_root: recycled)
    assert kr._kill_if_same_identity((4242, 111), Path("/proc")) is True
    assert signalled == []


# ---------------------------------------------------------------------------
# fake-execute: signal-observing journal wiring
# ---------------------------------------------------------------------------


def test_fake_execute_installs_signal_observers_and_journals_observed_signals(kr, tmp_path, capsys):
    artifacts = tmp_path / "artifacts"
    (tmp_path / "unused").mkdir()
    # Reached through the driver's own namespace: tests never import the
    # ``scripts`` package (tests/test_dependency_surface.py).
    kr.establish_preflight_journal(artifacts)

    class _Args:
        artifacts_dir = artifacts
        sleep_seconds = 3
        heartbeat_seconds = 1

    installed: dict = {}
    real_install = kr.install_observing_signal_handlers

    def _spy(journal):
        installed["journal"] = journal
        restore = real_install(journal)
        journal.observe_signal("SIGTERM")
        return restore

    kr.install_observing_signal_handlers = _spy
    try:
        assert kr.cmd_fake_execute(_Args()) == 0
    finally:
        kr.install_observing_signal_handlers = real_install

    assert installed["journal"] is not None
    events = read_journal(artifacts / t.JOURNAL_FILENAME).events
    kinds = [e.event for e in events]
    assert "SIGNAL_OBSERVED" in kinds
    assert [e.signal for e in events if e.event == "SIGNAL_OBSERVED"] == ["SIGTERM"]
    # The rehearsal never writes terminal evidence itself: the real
    # finalizer must produce the execution-invalid record afterwards.
    assert not (artifacts / t.TERMINAL_FILENAME).exists()
    out = capsys.readouterr().out
    assert "FAKE-EXECUTE: started_at_utc=" in out
    assert "heartbeat elapsed_seconds=" in out


def test_fake_execute_records_no_state_transition_beyond_preflighted(kr, tmp_path):
    """The only transition permitted out of PREFLIGHTED names a marker,
    and this lane has none -- so none is recorded."""
    artifacts = tmp_path / "artifacts"
    (tmp_path / "unused").mkdir()
    # Reached through the driver's own namespace: tests never import the
    # ``scripts`` package (tests/test_dependency_surface.py).
    kr.establish_preflight_journal(artifacts)

    class _Args:
        artifacts_dir = artifacts
        sleep_seconds = 1
        heartbeat_seconds = 1

    assert kr.cmd_fake_execute(_Args()) == 0
    states = [e.state_to for e in read_journal(artifacts / t.JOURNAL_FILENAME).events
              if e.event == "STATE_TRANSITION"]
    assert states == ["PREFLIGHTED"]
    assert "REPLACEMENT_MARKED" not in states


def test_observations_payload_shape_is_the_planned_b2_schema(kr):
    """The B2 evidence schema is a plan-d contract; pin its top level and
    the four topology classes so a later stage cannot quietly drop one."""
    source = DRIVER_PATH.read_text(encoding="utf-8")
    for key in ("schema_version", "lane", "identity", "no_provider_path",
                "runtime_identity", "topology", "fake_execution"):
        assert f'"{key}"' in source, key
    for key in ("class_a", "class_b", "class_c_static", "class_c_dynamic"):
        assert f'"{key}"' in source, key
    assert kr.LANE == "P5D_KILL_REHEARSAL"

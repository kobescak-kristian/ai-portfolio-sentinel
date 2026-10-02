"""Tests for the model-free durable-latch GitHub-read probe driver
(scripts/run_phase5_latch_read_probe.py; ADR-0012 Amendment B row 24;
Stage 2C-B6-3a, owner ruling D1 of 2026-10-02).

The driver is exercised end to end against a fake GitHub client. No
network, no provider, no OIDC, no marker. The workflow contract pins for
the probe live in tests/test_phase5_workflow_contracts.py.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sentinel.phase5 import readiness as rd
from sentinel.phase5.github_evidence import (
    CommitDetail,
    CommitFile,
    GithubEvidenceError,
    JobDetail,
    JobEvidence,
    JobStep,
    PushActivity,
    RunRef,
)
from sentinel.phase5.models import OneShotMarker
from sentinel.phase5.replacement import ORIGINAL_PURPOSE, REPLACEMENT_OF_RUN_ID

REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVER_PATH = REPO_ROOT / "scripts" / "run_phase5_latch_read_probe.py"
UTC = timezone.utc
SHA = "d" * 40
PARENT = "c" * 40
RUN_ID = "50005"
T0 = datetime(2026, 10, 3, 10, 0, 0, tzinfo=UTC)
PROBE_WF = ".github/workflows/sentinel-latch-read-probe.yml"
OFFICIAL_WF = ".github/workflows/sentinel-official-gate.yml"
TOKEN = "ghs_TESTTOKEN0123456789"


def _load_driver():
    spec = importlib.util.spec_from_file_location("run_phase5_latch_read_probe", DRIVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


driver = _load_driver()


def _env(**overrides):
    env = {
        "GITHUB_REPOSITORY": "kobescak-kristian/ai-portfolio-sentinel", "GITHUB_REPOSITORY_OWNER": "kobescak-kristian",
        "GITHUB_RUN_ID": RUN_ID, "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": SHA,
        "GITHUB_WORKFLOW_REF": f"kobescak-kristian/ai-portfolio-sentinel/{PROBE_WF}@refs/heads/main",
        "GITHUB_API_URL": "https://api.github.com", "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_JOB": "gate", "RUNNER_NAME": "GitHub Actions 7", "GITHUB_TOKEN": TOKEN,
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


class _FakeClient:
    def __init__(self, *, fail=(), pages=5, run_numbers=(1, 2, 3, 4), push_matches=1, run3=None, status="in_progress"):
        self.fail, self.pages, self.run_numbers = set(fail), pages, run_numbers
        self.push_matches, self.run3, self.status = push_matches, run3, status
        self.ticks = 0
        self.calls: list[str] = []

    def _maybe_fail(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise GithubEvidenceError("injected")

    def server_time_utc(self):
        self._maybe_fail("server_time_utc")
        self.ticks += 1
        return T0 + timedelta(seconds=10 * self.ticks)

    def get_run(self, run_id):
        self._maybe_fail("get_run")
        return RunRef(run_id=run_id, run_attempt=1, event="workflow_dispatch", ref="refs/heads/main", sha=SHA,
                      workflow_path=PROBE_WF, created_at=T0, run_started_at=T0, run_number=5, status=self.status,
                      conclusion=None, head_branch="main")

    def list_workflow_runs_counted(self, workflow_path, *, created_after, created_before, per_page=100, stats=None):
        self._maybe_fail("list_workflow_runs_counted:" + workflow_path.rsplit("/", 1)[1])
        if workflow_path == OFFICIAL_WF:
            return [RunRef(run_id=str(9000 + n), run_attempt=1, event="workflow_dispatch", ref="refs/heads/main",
                           sha=PARENT, workflow_path=workflow_path, created_at=T0, run_started_at=None, run_number=n,
                           status="completed", conclusion="failure", head_branch="main") for n in self.run_numbers]
        assert per_page == driver.PAGINATION_PER_PAGE and stats is not None
        stats.update({"pages": self.pages, "total_count": 87, "entries": 87})
        return []

    def list_run_attempt_jobs(self, run_id, attempt):
        self._maybe_fail("list_run_attempt_jobs")
        return [JobDetail(id=1, run_id=run_id, name="gate", status="in_progress", started_at=T0 - timedelta(minutes=1),
                          runner_name="GitHub Actions 7")]

    def list_run_attempt_job_evidence(self, run_id, attempt):
        self._maybe_fail("list_run_attempt_job_evidence")

        def steps(marker, execute):
            return (JobStep("Set up job", "completed", "success", 1), JobStep("upload one-shot marker", "completed", marker, 7),
                    JobStep("execute", "completed", execute, 8))

        if run_id == driver.RUN_3_ID:
            marker, execute, conclusion = (self.run3 or ("skipped", "skipped", "failure"))
            return [JobEvidence(id=3, run_id=run_id, name="gate", status="completed", conclusion=conclusion, steps=steps(marker, execute))]
        if run_id == driver.RUN_4_ID:
            return [JobEvidence(id=4, run_id=run_id, name="gate", status="completed", conclusion="cancelled", steps=steps("success", "cancelled"))]
        return [JobEvidence(id=1, run_id=run_id, name="gate", status="in_progress", conclusion=None,
                            steps=(JobStep("probe", "in_progress", None, 5),))]

    def get_commit(self, sha):
        self._maybe_fail("get_commit")
        return CommitDetail(sha=sha, parents=(PARENT,), files=(CommitFile("a.py", "modified", 1, 0, "@@ -1 +1,2 @@\n x\n+y"),))

    def list_push_activity(self, ref):
        self._maybe_fail("list_push_activity")
        entries = [PushActivity(before=PARENT, after=SHA, ref=ref, activity_type="push", timestamp=T0 - timedelta(minutes=5))
                   for _ in range(self.push_matches)]
        return entries + [PushActivity(before="1" * 40, after="2" * 40, ref=ref, activity_type="push", timestamp=T0 - timedelta(days=1))]

    def list_artifacts(self, prefix):
        self._maybe_fail("list_artifacts")
        from sentinel.phase5.github_evidence import ArtifactRef
        if prefix == "sentinel-p5-gate-evidence-":
            return []
        return [ArtifactRef(id=1, name="sentinel-p5-oneshot-p5c-wif-probe-r1", workflow_run_id="1"),
                ArtifactRef(id=2, name="sentinel-p5-oneshot-p5d-official-sonnet-gate-r32880880053", workflow_run_id="32880880053")]


def _identity_object():
    def dump(**fields):
        return types.SimpleNamespace(model_dump=lambda: dict(fields))

    sdk = types.SimpleNamespace(
        distribution_name="claude-agent-sdk", version="0.2.110", wheel_tags=("py3-none-manylinux",), record_sha256="r" * 64,
        transport_module=dump(record_path="x", sha256_actual="t" * 64, sha256_declared="t" * 64),
        bundled_cli=dump(record_path="y", sha256_actual="c" * 64, sha256_declared="c" * 64, size_bytes=1,
                         is_executable=True, cli_version_declared="2.1.191"),
        cli_selection="BUNDLED_FIRST",
    )
    return types.SimpleNamespace(
        runtime_identity_id="i" * 64, python_version="3.12.14", python_implementation="CPython", sys_platform="linux",
        machine="x86_64", os_release="6.17", runner=dump(image_version="20260927.320.1"), sdk=sdk,
        distributions=(("anyio", "4.14.0"), ("pydantic", "2.13.4")),
    )


@pytest.fixture
def probe_world(monkeypatch, tmp_path):
    state = types.SimpleNamespace(client=_FakeClient(), identity=_identity_object(), markers=None)

    def build_client(env, **kw):
        env.pop("GITHUB_TOKEN", None)
        return state.client

    monkeypatch.setattr(driver, "build_evidence_client", build_client)
    monkeypatch.setattr(driver, "assert_expected_source_on_disk", lambda sha: sha)
    monkeypatch.setattr(driver, "capture_runtime_identity", lambda env: state.identity)
    # The real audit and closure walks scan every installed distribution (seconds);
    # they are exercised once, directly, in test_requires_closure_and_import_audit_*.
    monkeypatch.setattr(driver, "import_audit", lambda: {"third_party_modules": 3, "imported_distributions": ["pydantic"]})
    monkeypatch.setattr(driver, "requires_closure", lambda pins: {"pydantic-core": ["pydantic"]})
    original = OneShotMarker(
        schema_version=1, purpose=ORIGINAL_PURPOSE, created_at_utc=T0, workflow_identity=OFFICIAL_WF,
        github_run_id=REPLACEMENT_OF_RUN_ID, run_attempt=1, event="workflow_dispatch", source_sha=PARENT,
    )
    monkeypatch.setattr(driver, "discover_oneshot_markers",
                        lambda client, work_root: [original] if state.markers is None else state.markers)
    state.args = argparse.Namespace(expected_source_sha=SHA, work_root=tmp_path / "p5-latch-probe",
                                    evidence_out=tmp_path / "p5-latch-probe" / "phase5_latch_read_probe.json")
    return state


def _read(state):
    return rd.ProbeEvidence.model_validate_json(state.args.evidence_out.read_bytes())


def test_a_healthy_world_passes_every_required_check_and_row_24(probe_world):
    env = _env()
    assert driver.run_probe(probe_world.args, env) == 0
    assert "GITHUB_TOKEN" not in env  # popped before anything else could see it
    evidence = _read(probe_world)
    assert evidence.result == "PASS" and evidence.stop_reason is None
    assert sorted(c.name for c in evidence.checks) == sorted(rd.PROBE_REQUIRED_CHECKS)
    assert all(c.ok for c in evidence.checks)
    assert rd.evaluate_probe_for_row24(evidence, expected_sha=SHA).status == "PASS"
    assert evidence.runtime_identity["runtime_identity_id"] == "i" * 64 and evidence.runtime_identity["requires_closure"]
    assert evidence.server_time_first_utc < evidence.server_time_last_utc
    raw = probe_world.args.evidence_out.read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw and TOKEN.encode() not in raw and b"GITHUB_TOKEN" not in raw


def test_the_probe_reads_pagination_with_a_small_page_size_and_the_anchor_with_the_production_job_name(probe_world):
    assert driver.run_probe(probe_world.args, _env()) == 0
    checks = {c.name: c for c in _read(probe_world).checks}
    assert checks["pagination"].detail == {"pages": 5, "total_count": 87, "entries": 87, "per_page": 20}
    assert checks["anchor"].detail["api_job_name"] == "gate" and checks["anchor"].detail["workflow_job_id"] == "gate"
    assert checks["job_steps"].detail["run_3_marker"] == "skipped" and checks["job_steps"].detail["run_4_marker"] == "success"
    assert checks["push_activity"].detail["matching"] == 1
    assert checks["official_listing"].detail["run_numbers"] == [1, 2, 3, 4]


@pytest.mark.parametrize("failing,check", [
    ("get_run", "current_run"), ("list_workflow_runs_counted:sentinel-official-gate.yml", "official_listing"),
    ("list_workflow_runs_counted:ci.yml", "pagination"), ("list_run_attempt_jobs", "attempt_jobs"),
    ("list_run_attempt_job_evidence", "job_steps"), ("get_commit", "commit"), ("list_push_activity", "push_activity"),
    ("list_artifacts", "artifact_discovery"),
])
def test_any_failed_github_read_fails_its_check_and_still_publishes_evidence(probe_world, failing, check):
    probe_world.client = _FakeClient(fail=(failing,))
    assert driver.run_probe(probe_world.args, _env()) == 1
    evidence = _read(probe_world)
    assert evidence.result == "STOP" and check in (evidence.stop_reason or "")
    failed = {c.name: c for c in evidence.checks if not c.ok}
    assert check in failed and failed[check].detail == {"error": "GithubEvidenceError"}
    assert rd.evaluate_probe_for_row24(evidence, expected_sha=SHA).status == "FAIL"


def test_a_failed_attempt_jobs_read_also_fails_the_anchor_as_a_missing_prerequisite(probe_world):
    probe_world.client = _FakeClient(fail=("list_run_attempt_jobs",))
    assert driver.run_probe(probe_world.args, _env()) == 1
    checks = {c.name: c for c in _read(probe_world).checks}
    assert checks["anchor"].ok is False and checks["anchor"].detail == {"error": "Phase5ScriptError"}


@pytest.mark.parametrize("make,check", [
    (lambda: _FakeClient(pages=1), "pagination"),
    (lambda: _FakeClient(run_numbers=(1, 2, 3, 4, 5)), "official_listing"),
    (lambda: _FakeClient(run_numbers=(1, 2, 3)), "official_listing"),
    (lambda: _FakeClient(push_matches=0), "push_activity"),
    (lambda: _FakeClient(push_matches=2), "push_activity"),
    (lambda: _FakeClient(run3=("success", "skipped", "failure")), "job_steps"),
    (lambda: _FakeClient(run3=("skipped", "skipped", "success")), "job_steps"),
    (lambda: _FakeClient(status="queued"), "current_run"),
])
def test_unproven_or_unexpected_surfaces_fail_without_an_exception(probe_world, make, check):
    probe_world.client = make()
    assert driver.run_probe(probe_world.args, _env()) == 1
    assert {c.name: c.ok for c in _read(probe_world).checks}[check] is False


def test_any_provider_or_override_variable_in_the_environment_fails_the_env_scan(probe_world):
    for name in ("ANTHROPIC_MODEL", "ANTHROPIC_API_KEY", "CLAUDE_CODE_SUBAGENT_MODEL", "ANTHROPIC_FEDERATION_RULE_ID"):
        if probe_world.args.work_root.exists():
            import shutil
            shutil.rmtree(probe_world.args.work_root)
        assert driver.run_probe(probe_world.args, _env(**{name: ""})) == 1
        scan = {c.name: c for c in _read(probe_world).checks}["env_scan"]
        assert scan.ok is False and name in scan.detail["flagged"]


def test_an_expired_original_marker_is_covered_by_the_durable_receipt_after_expiry(probe_world, monkeypatch):
    probe_world.markers = []
    assert driver.run_probe(probe_world.args, _env()) == 1  # server time is before the marker expiry: absence is a failure
    import shutil
    shutil.rmtree(probe_world.args.work_root)

    class Late(_FakeClient):
        def server_time_utc(self):
            self.ticks += 1
            return datetime(2026, 12, 1, 0, 0, 0, tzinfo=UTC) + timedelta(seconds=self.ticks)

    probe_world.client = Late()
    assert driver.run_probe(probe_world.args, _env()) == 0  # every other check is independent of the marker's expiry
    marker = {c.name: c for c in _read(probe_world).checks}["original_marker"]
    assert marker.ok is True and marker.detail["expired_covered_by_receipt"] is True


def test_runtime_identity_capture_failure_fails_the_check(probe_world, monkeypatch):
    from sentinel.phase5.runtime_identity import RuntimeIdentityError

    def boom(env):
        raise RuntimeIdentityError("x")

    monkeypatch.setattr(driver, "capture_runtime_identity", boom)
    assert driver.run_probe(probe_world.args, _env()) == 1
    evidence = _read(probe_world)
    assert {c.name: c for c in evidence.checks}["runtime_identity"].ok is False and evidence.runtime_identity is None


def test_a_rerun_or_a_source_mismatch_is_refused_before_any_check_or_evidence(probe_world, capsys):
    assert driver.run_probe(probe_world.args, _env(GITHUB_RUN_ATTEMPT="2")) == 2
    assert driver.run_probe(probe_world.args, _env(GITHUB_SHA="e" * 40)) == 2
    assert not probe_world.args.evidence_out.exists()
    assert "PROBE REFUSED" in capsys.readouterr().err


def test_main_turns_context_and_source_faults_into_a_refusal(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(driver.os, "environ", {})
    assert driver.main(["--expected-source-sha", SHA, "--work-root", str(tmp_path / "w"),
                        "--evidence-out", str(tmp_path / "w" / "e.json")]) == 2
    assert "PROBE REFUSED" in capsys.readouterr().err


def test_requires_closure_and_import_audit_report_installed_distributions():
    closure = driver.requires_closure({"pydantic": "2.13.4"})
    assert "pydantic-core" in closure and closure["pydantic-core"] == ["pydantic"]
    audit = driver.import_audit()
    assert "pydantic" in audit["imported_distributions"] and audit["third_party_modules"] > 0
    assert driver.requires_closure({"definitely-not-installed-pkg": "1"}) == {}


# ======================================================================
# The driver is read-only, model-free and clockless by construction
# ======================================================================


def _tree():
    return ast.parse(DRIVER_PATH.read_text(encoding="utf-8"))


def _imported_names():
    names = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(alias.name for alias in node.names)
    return names


def test_driver_imports_no_provider_oidc_sdk_marker_writer_process_or_clock_module():
    imported = _imported_names()
    for forbidden in ("subprocess", "time", "urllib", "urllib.request", "requests", "httpx", "socket", "claude_agent_sdk",
                      "agents", "agents.checker", "write_marker_json", "acquire_oidc", "assert_oneshot_not_consumed_durably",
                      "sentinel.phase5.oneshot", "sentinel.phase5.oidc"):
        assert forbidden not in imported, forbidden
    assert not any(n.startswith("agents") or "oidc" in n.lower() for n in imported if "readiness" not in n)


def test_driver_never_reads_a_local_clock_and_never_writes_outside_one_evidence_file():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    for needle in ("datetime.now", "utcnow", "time.time", "time.monotonic", "date.today", "perf_counter"):
        assert needle not in source, needle
    writes = [n for n in ast.walk(_tree()) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr in {"write_text", "write_bytes", "open", "mkdir", "unlink", "rmdir", "touch", "rename"}]
    assert writes == []
    json_writers = [n for n in ast.walk(_tree()) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "write_json_artifact"]
    assert len(json_writers) == 1


def test_driver_has_no_non_get_http_method_and_no_dispatch_or_settings_call():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    for needle in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"', "gh workflow", "gh api", "workflow run", "variable set",
                   ".post(", ".put(", ".delete("):
        assert needle not in source, needle


def test_driver_requires_exactly_the_readiness_check_names():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    recorded = {node.args[0].value for node in ast.walk(_tree())
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run"
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)}
    assert recorded == set(rd.PROBE_REQUIRED_CHECKS), recorded ^ set(rd.PROBE_REQUIRED_CHECKS)
    assert "PROBE_REQUIRED_CHECKS" in source

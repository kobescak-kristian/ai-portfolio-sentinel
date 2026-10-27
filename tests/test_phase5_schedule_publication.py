"""P5-E slot-1 recovery (E1, tests T1 and T2): the scheduled workflow's
upload steps against every artifact shape ``run_phase5_scheduled.py`` can
emit.

Slot 1 of window p5w-40148573833 (run 40191045830) exited 0 with only a
successor bundle staged; the evidence upload ran unconditionally on an empty
path, failed, and the bundle upload (implicit ``success()``) was skipped.

``_job`` is a hand-written model of the two ``if:`` expressions under
GitHub's documented rules (a step without a status function gets an
implicit ``success()``; a skipped step does not fail the job; an
``upload-artifact`` step with an empty or missing path fails). It is tied
to the workflow only by the verbatim-string test below, not by executing
it; the real GitHub behaviour is exercised by
``sentinel-publication-rehearsal.yml``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from sentinel.phase5 import artifact_names as an
from sentinel.phase5.orchestrator import ScheduledResult, StagedArtifact

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sentinel-schedule.yml"

EVIDENCE_IF = (
    "always() && (steps.orchestrate.outputs.evidence_name != '' "
    "|| steps.orchestrate.outputs.bundle_name == '')"
)
BUNDLE_IF = "steps.orchestrate.outputs.bundle_name != ''"

RUN_ID = "40191045830"
WINDOW_ID = "p5w-40148573833"


def _steps() -> dict:
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["scheduled"]
    return {s.get("name"): s for s in job["steps"]}


def test_t1_schedule_upload_conditions_are_exact():
    steps = _steps()
    assert steps["upload attempt evidence"]["if"] == EVIDENCE_IF
    assert steps["upload successor or refusal bundle"]["if"] == BUNDLE_IF
    for name, prefix in (("upload attempt evidence", "evidence"), ("upload successor or refusal bundle", "bundle")):
        upload = steps[name]["with"]
        assert upload["name"] == f"${{{{ steps.orchestrate.outputs.{prefix}_name }}}}"
        assert upload["path"] == f"${{{{ steps.orchestrate.outputs.{prefix}_path }}}}"
        assert upload["if-no-files-found"] == "error"


def test_t1_upload_steps_follow_orchestrate_in_order():
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["scheduled"]
    names = [s.get("name") for s in job["steps"]]
    i = names.index("orchestrate")
    assert names[i + 1:i + 3] == ["upload attempt evidence", "upload successor or refusal bundle"]


def _job(orchestrate_ok: bool, outputs: dict) -> dict:
    """Outcome of the orchestrate step and the two upload steps."""
    evidence_name, bundle_name = outputs.get("evidence_name", ""), outputs.get("bundle_name", "")

    def upload(path_key: str) -> str:
        path = outputs.get(path_key, "")
        return "success" if path and Path(path).exists() else "failure"

    evidence = upload("evidence_path") if (evidence_name != "" or bundle_name == "") else "skipped"
    previous_ok = orchestrate_ok and evidence != "failure"
    bundle = upload("bundle_path") if (previous_ok and bundle_name != "") else "skipped"
    failed = not orchestrate_ok or "failure" in (evidence, bundle)
    return {"evidence": evidence, "bundle": bundle, "job": "failure" if failed else "success"}


_script_spec = importlib.util.spec_from_file_location(
    "run_phase5_scheduled", REPO_ROOT / "scripts" / "run_phase5_scheduled.py"
)
scheduled = importlib.util.module_from_spec(_script_spec)
_script_spec.loader.exec_module(scheduled)


def _emitted_outputs(tmp_path, monkeypatch, result: ScheduledResult) -> tuple[int, dict]:
    output_file = tmp_path / "github_output"
    output_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("PHASE5_WORK_ROOT", str(tmp_path / "work"))
    monkeypatch.setattr(scheduled, "build_evidence_client", lambda env: None)
    monkeypatch.setattr(scheduled, "derive_github_context", lambda env: SimpleNamespace(repository_owner="o"))
    monkeypatch.setattr(scheduled, "run_scheduled", lambda ports, work_trusted_root: result)
    exit_code = scheduled.main()
    outputs = dict(line.split("=", 1) for line in output_file.read_text(encoding="utf-8").splitlines())
    return exit_code, outputs


def _file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text("{}", encoding="utf-8")
    return path


def _dir(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.mkdir()
    (path / "manifest.json").write_text("{}", encoding="utf-8")
    return path


SHAPES = {
    # name: (exit code, staged artifact builder, expected evidence, bundle, job)
    "no_active_window_or_slot_not_open": (
        0, lambda t: [StagedArtifact(an.prewindow_evidence_name(RUN_ID, 1), _file(t, "prewindow_evidence.json"))],
        "success", "skipped", "success"),
    "attempt_failure": (
        1, lambda t: [StagedArtifact(an.attempt_evidence_name(RUN_ID, 1), _file(t, "attempt_evidence.json"))],
        "success", "skipped", "failure"),
    "slot_successor": (
        0, lambda t: [StagedArtifact(an.slot_name(WINDOW_ID, 1, RUN_ID), _dir(t, "slot-out"))],
        "skipped", "success", "success"),
    "control_refusal": (
        0, lambda t: [StagedArtifact(an.refusal_name(WINDOW_ID, RUN_ID), _dir(t, "refusal-out"))],
        "skipped", "success", "success"),
    "exit_zero_nothing_staged": (0, lambda t: [], "failure", "skipped", "failure"),
    "exit_one_nothing_staged": (1, lambda t: [], "failure", "skipped", "failure"),
    "bundle_declared_files_missing": (
        0, lambda t: [StagedArtifact(an.slot_name(WINDOW_ID, 1, RUN_ID), t / "never-built")],
        "skipped", "failure", "failure"),
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_t2_entrypoint_outputs_drive_the_expected_uploads(tmp_path, monkeypatch, shape):
    exit_code, build, evidence, bundle, job = SHAPES[shape]
    staged = tuple(build(tmp_path))
    code, outputs = _emitted_outputs(tmp_path, monkeypatch, ScheduledResult(exit_code, None, staged))
    assert code == exit_code
    assert _job(exit_code == 0, outputs) == {"evidence": evidence, "bundle": bundle, "job": job}

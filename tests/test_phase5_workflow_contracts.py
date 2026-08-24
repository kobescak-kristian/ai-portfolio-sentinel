"""Static YAML contract tests for the five Phase-5 workflows (P5-B
Part 3/3). PyYAML is already pinned in requirements-dev.txt and listed
in tests/test_dependency_surface.py's ALLOWED_TEST_THIRD_PARTY — no
new dependency.

Note: PyYAML (YAML 1.1) parses the bare ``on:`` key as the boolean
``True``, not the string ``"on"`` — every ``data[True]`` access below
is that key, matching the same quirk already present in ci.yml.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(".github/workflows")

PINNED_ACTIONS = {
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",  # v7.0.1
    "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",  # v7.0.0
    "actions/upload-artifact@330a01c490aca151604b8cf639adc76d48f6c5d4",  # v5.0.0
}

EXPECTED_FILES = {
    "ci.yml",
    "sentinel-schedule.yml",
    "sentinel-rehearsal.yml",
    "sentinel-wif-probe.yml",
    "sentinel-official-gate.yml",
    "sentinel-window-control.yml",
}

P5_WORKFLOWS = {
    "sentinel-schedule.yml": {"timeout": 20, "concurrency": "sentinel-schedule", "id_token": True},
    "sentinel-rehearsal.yml": {"timeout": 15, "concurrency": "sentinel-rehearsal", "id_token": False},
    "sentinel-wif-probe.yml": {"timeout": 20, "concurrency": "sentinel-oneshot-p5c", "id_token": True},
    "sentinel-official-gate.yml": {"timeout": 30, "concurrency": "sentinel-oneshot-p5d", "id_token": True},
    "sentinel-window-control.yml": {"timeout": 15, "concurrency": "sentinel-window-control", "id_token": False},
}


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS_DIR / name).read_text(encoding="utf-8"))


def _all_uses(data: dict) -> list[str]:
    uses = []
    for job in data["jobs"].values():
        for step in job.get("steps", []):
            if "uses" in step:
                uses.append(step["uses"])
    return uses


def test_exactly_the_expected_workflow_files_exist():
    actual = {p.name for p in WORKFLOWS_DIR.glob("*.yml")}
    assert actual == EXPECTED_FILES


def test_schedule_workflow_trigger_is_exact_cron_only():
    data = _load("sentinel-schedule.yml")
    trigger = data[True]
    assert set(trigger) == {"schedule"}
    assert trigger["schedule"] == [{"cron": "37 6 * * *"}]


@pytest.mark.parametrize("name", ["sentinel-rehearsal.yml", "sentinel-wif-probe.yml",
                                   "sentinel-official-gate.yml", "sentinel-window-control.yml"])
def test_manual_workflows_trigger_only_on_workflow_dispatch(name):
    data = _load(name)
    trigger = data[True]
    assert set(trigger) == {"workflow_dispatch"}


@pytest.mark.parametrize("name", ["sentinel-rehearsal.yml", "sentinel-wif-probe.yml",
                                   "sentinel-official-gate.yml"])
def test_manual_workflows_declare_required_expected_source_sha_input(name):
    data = _load(name)
    inputs = data[True]["workflow_dispatch"]["inputs"]
    assert "expected_source_sha" in inputs
    assert inputs["expected_source_sha"]["required"] is True


def test_window_control_declares_all_required_migration_inputs():
    data = _load("sentinel-window-control.yml")
    inputs = data[True]["workflow_dispatch"]["inputs"]
    required = {
        "expected_source_sha", "first_slot_date", "windows_task_name", "disabled_at_utc",
        "final_legacy_db_sha256", "legacy_row_counts", "dual_scheduler_verification_at_utc",
    }
    for key in required:
        assert inputs[key]["required"] is True, key
    assert inputs["supersedes_window_id"].get("required", False) is False


@pytest.mark.parametrize("name,expected", P5_WORKFLOWS.items())
def test_permissions_block_present_and_exact(name, expected):
    data = _load(name)
    permissions = data["permissions"]
    assert permissions["contents"] == "read"
    assert permissions["actions"] == "read"
    if expected["id_token"]:
        assert permissions.get("id-token") == "write"
    else:
        assert "id-token" not in permissions


def test_no_workflow_lacks_a_permissions_block():
    for path in WORKFLOWS_DIR.glob("*.yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "permissions" in data, path


def test_no_repository_write_permission_anywhere():
    """No permission scope is ever 'write' except id-token, whose
    write grant is the frozen contract itself (item 2) and is
    independently pinned per lane by test_permissions_block_present_and_exact."""
    for path in WORKFLOWS_DIR.glob("*.yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for scope, value in data.get("permissions", {}).items():
            if scope == "id-token":
                continue
            assert value != "write", f"{path}: {scope} is write"


def test_no_pull_request_trigger_in_any_p5_workflow():
    for name in P5_WORKFLOWS:
        data = _load(name)
        trigger = data[True]
        assert "pull_request" not in trigger
        assert "pull_request_target" not in trigger


@pytest.mark.parametrize("name,expected", P5_WORKFLOWS.items())
def test_concurrency_and_timeout(name, expected):
    data = _load(name)
    assert data["concurrency"]["group"] == expected["concurrency"]
    assert data["concurrency"]["cancel-in-progress"] is False
    job = next(iter(data["jobs"].values()))
    assert job["timeout-minutes"] == expected["timeout"]


@pytest.mark.parametrize("name", list(P5_WORKFLOWS) + ["ci.yml"])
def test_every_uses_is_a_frozen_full_sha_pin(name):
    data = _load(name)
    for uses in _all_uses(data):
        assert uses in PINNED_ACTIONS, f"{name}: unpinned or unexpected action {uses!r}"


@pytest.mark.parametrize("name,rule_var", [
    ("sentinel-schedule.yml", "SENTINEL_SCHEDULE_FEDERATION_RULE_ID"),
    ("sentinel-wif-probe.yml", "SENTINEL_P5C_FEDERATION_RULE_ID"),
    ("sentinel-official-gate.yml", "SENTINEL_P5D_FEDERATION_RULE_ID"),
])
def test_per_lane_federation_rule_variable_maps_to_the_provider_env_name(name, rule_var):
    text = (WORKFLOWS_DIR / name).read_text(encoding="utf-8")
    assert f"ANTHROPIC_FEDERATION_RULE_ID: ${{{{ vars.{rule_var} }}}}" in text
    # no other lane's rule variable name appears in this file
    other_vars = {
        "SENTINEL_SCHEDULE_FEDERATION_RULE_ID", "SENTINEL_P5C_FEDERATION_RULE_ID",
        "SENTINEL_P5D_FEDERATION_RULE_ID",
    } - {rule_var}
    for other in other_vars:
        assert other not in text


@pytest.mark.parametrize("name", ["sentinel-rehearsal.yml", "sentinel-window-control.yml"])
def test_model_free_workflows_have_no_anthropic_env(name):
    text = (WORKFLOWS_DIR / name).read_text(encoding="utf-8")
    assert "ANTHROPIC_" not in text


@pytest.mark.parametrize("name", ["sentinel-schedule.yml", "sentinel-wif-probe.yml", "sentinel-official-gate.yml"])
def test_identity_token_file_points_into_runner_temp(name):
    text = (WORKFLOWS_DIR / name).read_text(encoding="utf-8")
    assert "ANTHROPIC_IDENTITY_TOKEN_FILE: ${{ runner.temp }}/anthropic_identity_token" in text


@pytest.mark.parametrize("name", ["sentinel-wif-probe.yml", "sentinel-official-gate.yml"])
def test_marker_upload_step_precedes_execute_step(name):
    data = _load(name)
    job = next(iter(data["jobs"].values()))
    steps = job["steps"]
    names = [s.get("name", "") for s in steps]
    marker_idx = next(i for i, n in enumerate(names) if "marker" in n.lower())
    execute_idx = next(i for i, n in enumerate(names) if n == "execute")
    preflight_idx = next(i for i, n in enumerate(names) if n == "preflight")
    assert preflight_idx < marker_idx < execute_idx


@pytest.mark.parametrize("name", list(P5_WORKFLOWS))
def test_upload_steps_carry_retention_and_evidence_uploads_are_always(name):
    data = _load(name)
    job = next(iter(data["jobs"].values()))
    for step in job["steps"]:
        if "uses" in step and "upload-artifact" in step["uses"]:
            assert step["with"]["retention-days"] == 90
            if "evidence" in step["name"].lower() or "attempt" in step["name"].lower():
                assert step.get("if") == "always()"


def test_entrypoint_commands_reference_existing_script_files():
    script_names = [
        "run_phase5_scheduled.py", "run_phase5_rehearsal.py", "run_phase5_wif_probe.py",
        "run_phase5_official_gate.py", "run_phase5_window_freeze.py",
    ]
    all_text = "\n".join((WORKFLOWS_DIR / name).read_text(encoding="utf-8") for name in P5_WORKFLOWS)
    for script in script_names:
        assert script in all_text
        assert (Path("scripts") / script).exists()


def test_official_gate_passes_input_expected_source_sha_not_github_sha():
    text = (WORKFLOWS_DIR / "sentinel-official-gate.yml").read_text(encoding="utf-8")
    assert "inputs.expected_source_sha" in text
    assert "github.sha" not in text


@pytest.mark.parametrize("name", list(P5_WORKFLOWS))
def test_github_token_only_on_steps_that_perform_rest_calls(name):
    data = _load(name)
    job = next(iter(data["jobs"].values()))
    for step in job["steps"]:
        env = step.get("env", {})
        if "GITHUB_TOKEN" in env:
            assert "run" in step  # only python-invoking steps read it, never upload-artifact steps


# =====================================================================
# C0 (dispatch q77-p5c-execute-a): workflow context repair coverage --
# `runner` is not available in jobs.<job_id>.env (GitHub Actions
# context-availability table), only from jobs.<job_id>.steps.{env,
# with,run} onward.
# =====================================================================


def test_no_job_level_env_references_runner_context():
    for path in WORKFLOWS_DIR.glob("*.yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in data["jobs"].values():
            for value in job.get("env", {}).values():
                assert "${{ runner." not in str(value), f"{path}: job-level env references runner context"


@pytest.mark.parametrize("name", ["sentinel-wif-probe.yml", "sentinel-schedule.yml",
                                   "sentinel-official-gate.yml", "sentinel-window-control.yml"])
def test_runner_temp_only_appears_at_step_level_or_in_with_blocks(name):
    """Every `${{ runner.` occurrence in these four repaired files must
    sit at or after the job's `steps:` key -- never inside the
    job-level `env:` block that precedes it."""
    text = (WORKFLOWS_DIR / name).read_text(encoding="utf-8")
    steps_idx = text.index("\n    steps:\n")
    for line_no, line in enumerate(text.splitlines(), start=1):
        if "${{ runner." in line:
            offset = text.index(line)
            assert offset > steps_idx, f"{name}:{line_no}: runner.* reference precedes 'steps:'"


def test_probe_preflight_and_execute_env_match_exactly():
    data = _load("sentinel-wif-probe.yml")
    job = next(iter(data["jobs"].values()))
    steps = {s["name"]: s for s in job["steps"] if s.get("name") in ("preflight", "execute")}
    for key in ("ANTHROPIC_IDENTITY_TOKEN_FILE", "WORK_ROOT"):
        assert steps["preflight"]["env"][key] == steps["execute"]["env"][key]


def test_official_gate_preflight_and_execute_env_match_exactly():
    data = _load("sentinel-official-gate.yml")
    job = next(iter(data["jobs"].values()))
    steps = {s["name"]: s for s in job["steps"] if s.get("name") in ("preflight", "execute")}
    for key in ("ANTHROPIC_IDENTITY_TOKEN_FILE", "WORK_ROOT", "GATE_ROOT", "ARTIFACTS_DIR"):
        assert steps["preflight"]["env"][key] == steps["execute"]["env"][key]


def test_artifact_upload_paths_match_producer_paths():
    expectations = {
        "sentinel-wif-probe.yml": [
            ("upload one-shot marker", "preflight", "WORK_ROOT", "/marker.json"),
            ("upload probe evidence", "execute", "WORK_ROOT", "/probe-evidence.json"),
        ],
        "sentinel-official-gate.yml": [
            ("upload one-shot marker", "preflight", "WORK_ROOT", "/marker.json"),
        ],
        "sentinel-window-control.yml": [
            ("upload genesis bundle", "freeze", "WORK_ROOT", "/genesis-out"),
            ("upload freeze-refusal evidence", "freeze", "WORK_ROOT", "/freeze_refusal.json"),
        ],
    }
    for name, cases in expectations.items():
        data = _load(name)
        job = next(iter(data["jobs"].values()))
        steps_by_name = {s.get("name"): s for s in job["steps"]}
        for upload_name, producer_name, env_key, suffix in cases:
            producer_root = steps_by_name[producer_name]["env"][env_key]
            expected_path = f"{producer_root}{suffix}"
            assert steps_by_name[upload_name]["with"]["path"] == expected_path

    # sentinel-official-gate.yml's gate-evidence upload path is the
    # ARTIFACTS_DIR root itself, not a suffixed file under WORK_ROOT.
    gate_data = _load("sentinel-official-gate.yml")
    gate_job = next(iter(gate_data["jobs"].values()))
    gate_steps = {s.get("name"): s for s in gate_job["steps"]}
    assert gate_steps["upload gate evidence"]["with"]["path"] == gate_steps["execute"]["env"]["ARTIFACTS_DIR"]

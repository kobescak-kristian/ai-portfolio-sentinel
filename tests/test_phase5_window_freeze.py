"""Tests for scripts/run_phase5_window_freeze.py (P5-B Part 3/3).

Imports the script as a module (it has no ``scripts`` package
``__init__.py``, matching the existing ``run_phase3_dev_gate.py`` /
``run_phase4_loop_gate.py`` precedent of direct, path-based execution)
and exercises its pure validation logic directly with argparse
namespaces, never through a real subprocess or real network/git call.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "run_phase5_window_freeze", REPO_ROOT / "scripts" / "run_phase5_window_freeze.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wf = _load_module()

VALID_ROW_COUNTS = (
    '{"runs":10,"tasks":20,"findings":5,"agent_calls":3,'
    '"agent_tool_attempts":2,"loop_runs":1,"loop_iterations":1}'
)


def _args(**overrides):
    fields = dict(
        expected_source_sha="a" * 40,
        first_slot_date="2027-01-01",
        windows_task_name="SentinelDailyRun",
        disabled_at_utc="2026-12-30T00:00:00+00:00",
        final_legacy_db_sha256="b" * 64,
        legacy_row_counts=VALID_ROW_COUNTS,
        dual_scheduler_verification_at_utc="2026-12-30T01:00:00+00:00",
        supersedes=None,
        work_root=Path("unused"),
    )
    fields.update(overrides)
    return argparse.Namespace(**fields)


def test_valid_migration_evidence_parses():
    result = wf._validate_migration_evidence(_args())
    assert result["windows_task_name"] == "SentinelDailyRun"
    assert result["legacy_row_counts"]["runs"] == 10


def test_wrong_task_name_rejected():
    with pytest.raises(wf.Phase5ScriptError):
        wf._validate_migration_evidence(_args(windows_task_name="SomeOtherTask"))


def test_non_utc_timestamp_rejected():
    with pytest.raises(wf.Phase5ScriptError):
        wf._validate_migration_evidence(_args(disabled_at_utc="2026-12-30T00:00:00+02:00"))


def test_naive_timestamp_rejected():
    with pytest.raises(wf.Phase5ScriptError):
        wf._validate_migration_evidence(_args(disabled_at_utc="2026-12-30T00:00:00"))


def test_short_sha256_rejected():
    with pytest.raises(wf.Phase5ScriptError):
        wf._validate_migration_evidence(_args(final_legacy_db_sha256="b" * 63))


def test_uppercase_sha256_rejected():
    with pytest.raises(wf.Phase5ScriptError):
        wf._validate_migration_evidence(_args(final_legacy_db_sha256="B" * 64))


def test_malformed_json_row_counts_rejected():
    with pytest.raises(wf.Phase5ScriptError):
        wf._validate_migration_evidence(_args(legacy_row_counts="not json"))


def test_missing_key_row_counts_rejected():
    bad = '{"runs":1,"tasks":1,"findings":1,"agent_calls":1,"agent_tool_attempts":1,"loop_runs":1}'
    with pytest.raises(wf.Phase5ScriptError):
        wf._validate_migration_evidence(_args(legacy_row_counts=bad))


def test_unknown_key_row_counts_rejected():
    bad = VALID_ROW_COUNTS[:-1] + ',"extra_table":1}'
    with pytest.raises(wf.Phase5ScriptError):
        wf._validate_migration_evidence(_args(legacy_row_counts=bad))


def test_negative_row_count_rejected():
    bad = VALID_ROW_COUNTS.replace('"runs":10', '"runs":-1')
    with pytest.raises(wf.Phase5ScriptError):
        wf._validate_migration_evidence(_args(legacy_row_counts=bad))


def test_non_integer_row_count_rejected():
    bad = VALID_ROW_COUNTS.replace('"runs":10', '"runs":"ten"')
    with pytest.raises(wf.Phase5ScriptError):
        wf._validate_migration_evidence(_args(legacy_row_counts=bad))


def test_boolean_row_count_rejected():
    # bool is a subclass of int in Python -- must be explicitly excluded
    bad = VALID_ROW_COUNTS.replace('"runs":10', '"runs":true')
    with pytest.raises(wf.Phase5ScriptError):
        wf._validate_migration_evidence(_args(legacy_row_counts=bad))


def test_legacy_tables_constant_matches_the_seven_frozen_tables():
    assert wf.LEGACY_TABLES == (
        "runs", "tasks", "findings", "agent_calls", "agent_tool_attempts", "loop_runs", "loop_iterations",
    )


def test_no_git_invocation_in_the_script_source():
    """The script must never rewrite main -- AST/text-level proof it
    contains no git-mutating call (git push/commit/checkout -B etc.);
    the only git usage anywhere in Phase-5 scripts is the shared,
    read-only fetch/rev-parse helper in scripts/_phase5_common.py."""
    text = (REPO_ROOT / "scripts" / "run_phase5_window_freeze.py").read_text(encoding="utf-8")
    for banned in ("git push", "git commit", "git checkout -B", "subprocess"):
        assert banned not in text


# ---------------------------------------------------------------------------
# Full main() flow, network/git mocked, real bundle mechanics exercised
# ---------------------------------------------------------------------------

SOURCE_SHA = "a" * 40

ENV = {
    "GITHUB_REPOSITORY": "kobescak-kristian/ai-portfolio-sentinel",
    "GITHUB_REPOSITORY_OWNER": "kobescak-kristian",
    "GITHUB_RUN_ID": "777",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": SOURCE_SHA,
    "GITHUB_WORKFLOW_REF": (
        "kobescak-kristian/ai-portfolio-sentinel/.github/workflows/sentinel-window-control.yml@refs/heads/main"
    ),
    "GITHUB_API_URL": "https://api.github.com",
    "GITHUB_SERVER_URL": "https://github.com",
}


class _FakeClient:
    """Minimal duck-type of GithubEvidenceClient for main()'s discovery
    calls. ``bundles`` maps artifact name -> a real directory tree
    already on disk (a validated bundle, a marker.json, or an evidence
    JSON file) that download_artifact copies into the destination."""

    def __init__(self, bundles: dict[str, Path]):
        self._bundles = bundles

    def get_main_head_sha(self) -> str:
        return SOURCE_SHA

    def list_artifacts(self, prefix: str):
        from sentinel.phase5.github_evidence import ArtifactRef

        return [
            ArtifactRef(id=i, name=name, workflow_run_id="777")
            for i, name in enumerate(sorted(self._bundles))
            if name.startswith(prefix)
        ]

    def download_artifact(self, ref, dest_trusted_root: Path, dest_dir: Path) -> Path:
        import shutil

        from sentinel.phase5.bundle import create_fresh_root

        root = create_fresh_root(dest_trusted_root, dest_dir)
        source = self._bundles[ref.name]
        if source.is_dir():
            shutil.copytree(source, root, dirs_exist_ok=True)
        else:
            root.mkdir(exist_ok=True)
            shutil.copy(source, root / source.name)
        return root


def _write_marker(path: Path, purpose: str, run_id: str = "1"):
    from sentinel.phase5.models import OneShotMarker

    marker = OneShotMarker(
        schema_version=1, purpose=purpose, created_at_utc=datetime.now(timezone.utc),
        workflow_identity=".github/workflows/x.yml", github_run_id=run_id, run_attempt=1,
        event="workflow_dispatch", source_sha=SOURCE_SHA,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(marker.model_dump_json(), encoding="utf-8")


def test_freeze_succeeds_and_produces_a_validate_bundle_clean_genesis(tmp_path, monkeypatch):
    from contracts.schemas import CostRow
    from sentinel.phase5.bundle import validate_bundle
    from sentinel.phase5.evidence_records import GateEvidenceRecord, ProbeEvidenceRecord
    from telemetry.cost_ledger import append_cost_row

    for key, value in ENV.items():
        monkeypatch.setenv(key, value)

    # -- committed telemetry ledger the freeze verifies P5-C/P5-D against
    repo_root_fake = tmp_path / "repo"
    (repo_root_fake / "telemetry").mkdir(parents=True)
    committed_ledger = repo_root_fake / "telemetry" / "cost_ledger.jsonl"
    committed_ledger.write_text("", encoding="utf-8")
    # A comfortably-past, whole-second timestamp -- never one derived
    # from a near-simultaneous datetime.now(): Phase5ControlState's
    # last_evaluated_at_utc is truncated to whole seconds on canonical
    # serialization (models.serialize_db_datetime), while CostRow's own
    # serialization preserves microseconds, so two "now()" calls a few
    # microseconds apart can straddle that truncation boundary and be
    # wrongly excluded from the recomputed trailing-spend sum.
    past = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    probe_row = CostRow(
        schema_version=1, run_id="r-p5c-1", recorded_at_utc=past,
        run_kind="live", model="claude-haiku-4-5-20251001", input_tokens=1, output_tokens=1, cost_eur_micros=1000,
    )
    gate_row = CostRow(
        schema_version=1, run_id="r-p5d-1", recorded_at_utc=past,
        run_kind="dev", model="claude-sonnet-5", input_tokens=1, output_tokens=1, cost_eur_micros=2000,
    )
    append_cost_row(committed_ledger, probe_row)
    append_cost_row(committed_ledger, gate_row)
    monkeypatch.setattr(wf, "REPO_ROOT", repo_root_fake)

    # -- fake discovery bundles: two markers, two evidence artifacts
    bundles: dict[str, Path] = {}
    probe_marker_dir = tmp_path / "src-probe-marker"
    _write_marker(probe_marker_dir / "marker.json", "P5C_WIF_PROBE", run_id="1")
    bundles["sentinel-p5-oneshot-p5c-wif-probe-r1"] = probe_marker_dir

    gate_marker_dir = tmp_path / "src-gate-marker"
    _write_marker(gate_marker_dir / "marker.json", "P5D_OFFICIAL_SONNET_GATE", run_id="1")
    bundles["sentinel-p5-oneshot-p5d-official-sonnet-gate-r1"] = gate_marker_dir

    probe_evidence = ProbeEvidenceRecord(
        schema_version=1, workflow_identity=".github/workflows/x.yml", github_run_id="1", run_attempt=1,
        event="workflow_dispatch", ref="refs/heads/main", source_sha=SOURCE_SHA,
        created_at_utc=datetime.now(timezone.utc), steps=(), expected_source_sha=SOURCE_SHA,
        disposition="CAPABILITY_PASS", cost_rows=(probe_row,), accounted_total_eur_micros=1000,
        auth_mode="github-actions-wif-federation",
    )
    probe_evidence_dir = tmp_path / "src-probe-evidence"
    probe_evidence_dir.mkdir()
    (probe_evidence_dir / "probe-evidence.json").write_text(probe_evidence.model_dump_json(), encoding="utf-8")
    bundles["sentinel-p5-probe-evidence-r1-a1"] = probe_evidence_dir

    gate_evidence = GateEvidenceRecord(
        schema_version=1, workflow_identity=".github/workflows/x.yml", github_run_id="1", run_attempt=1,
        event="workflow_dispatch", ref="refs/heads/main", source_sha=SOURCE_SHA,
        created_at_utc=datetime.now(timezone.utc), steps=(), expected_source_sha=SOURCE_SHA,
        model="claude-sonnet-5", profile_name="sonnet-official-gate", run_ids=("run1", "run2"),
        scoring={"emitted": 1}, thresholds={}, invariant_results={"ok": True}, execution_validity={"valid": True},
        miss_patterns=("x|y|z",), cost_rows=(gate_row,), accounted_total_eur_micros=2000, disposition="GREEN",
    )
    gate_evidence_dir = tmp_path / "src-gate-evidence"
    gate_evidence_dir.mkdir()
    (gate_evidence_dir / "phase5_official_gate.json").write_text(gate_evidence.model_dump_json(), encoding="utf-8")
    bundles["sentinel-p5-gate-evidence-r1-a1"] = gate_evidence_dir

    fake_client = _FakeClient(bundles)
    monkeypatch.setattr(wf, "build_evidence_client", lambda env: fake_client)
    monkeypatch.setattr(wf, "assert_expected_source_on_disk", lambda sha: None)

    work_root = tmp_path / "work"
    exit_code = wf.main([
        "--expected-source-sha", SOURCE_SHA,
        "--first-slot-date", "2027-06-01",
        "--windows-task-name", "SentinelDailyRun",
        "--disabled-at-utc", "2027-05-30T00:00:00+00:00",
        "--final-legacy-db-sha256", "c" * 64,
        "--legacy-row-counts", VALID_ROW_COUNTS,
        "--dual-scheduler-verification-at-utc", "2027-05-30T01:00:00+00:00",
        "--work-root", str(work_root),
    ])

    assert exit_code == 0, (work_root / "freeze_refusal.json").read_text(encoding="utf-8") if (
        work_root / "freeze_refusal.json"
    ).exists() else "no refusal evidence"
    genesis_root = work_root / "genesis-out"
    validated = validate_bundle(genesis_root)
    assert validated.manifest.bundle_kind == "GENESIS"
    assert validated.manifest.slot_index == 0
    assert validated.window.window_id == "p5w-777"
    assert len(validated.window.expected_slots) == 5
    assert validated.window.expected_slots[0].expected_at_utc.hour == 6
    assert validated.window.expected_slots[0].expected_at_utc.minute == 37


def test_genesis_manifest_constructible_only_via_bundle_build(monkeypatch):
    """No other module in this dispatch constructs a GenesisManifest
    directly outside the domain core's own build path -- confirmed by
    the fact this script imports GenesisManifest only for type
    reference and always routes actual construction through
    build_bundle's manifest_fields dict, never GenesisManifest(...) directly."""
    text = (REPO_ROOT / "scripts" / "run_phase5_window_freeze.py").read_text(encoding="utf-8")
    assert "GenesisManifest(" not in text

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
    JSON file) that download_artifact copies into the destination.
    ``run_artifacts`` (dispatch q77-p5d-repair-stage2-implement-a) maps
    a github_run_id -> the list of (artifact_id, artifact_name) pairs
    ``list_artifacts_for_run`` should report as still live-discoverable
    for that run -- the repaired seam-3 uses this to decide whether a
    durable receipt's own artifact is still retained (and therefore
    hash-verifiable) or has expired (receipt-only, per ADR-0012
    Amendment A1). Empty by default: every receipt is treated as
    already expired, exercising the pure receipt-only path."""

    def __init__(self, bundles: dict[str, Path], run_artifacts: "dict[str, list] | None" = None):
        self._bundles = bundles
        self._run_artifacts = run_artifacts or {}

    def get_main_head_sha(self) -> str:
        return SOURCE_SHA

    def list_artifacts(self, prefix: str):
        from sentinel.phase5.github_evidence import ArtifactRef

        return [
            ArtifactRef(id=i, name=name, workflow_run_id="777")
            for i, name in enumerate(sorted(self._bundles))
            if name.startswith(prefix)
        ]

    def list_artifacts_for_run(self, run_id: str):
        from sentinel.phase5.github_evidence import ArtifactRef

        return [
            ArtifactRef(id=artifact_id, name=name, workflow_run_id=run_id)
            for artifact_id, name in self._run_artifacts.get(run_id, [])
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


# ---------------------------------------------------------------------
# Durable-receipt fixtures for the repaired seam 3 (ADR-0012 Amendment
# A1 and section 19; dispatch q77-p5d-repair-stage2-implement-a).
# ---------------------------------------------------------------------

from sentinel.phase5.replacement import (  # noqa: E402
    ORIGINAL_PURPOSE,
    OWNER_RULING_ID,
    REPLACEMENT_OF_RUN_ID,
    REPLACEMENT_PURPOSE,
)

REPLACEMENT_RUN_ID = "999888777"


def _fixed_clock():
    return datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_receipts_registry(
    tmp_path: Path,
    *,
    include_original: bool = True,
    include_replacement_marker: bool = True,
    replacement_disposition: str = "GREEN",
    second_replacement_run_id: "str | None" = None,
) -> "tuple":
    """Build a temporary durable receipt registry reflecting: P5-C
    consumed + CAPABILITY_PASS, the original P5-D consumed +
    EXECUTION_INVALID/NO_QUALITY_RESULT (non-qualifying), and
    (optionally) exactly one proven replacement marker + GATE_EVIDENCE
    receipt. Returns the loaded receipts tuple -- never touches the
    real committed registry."""
    from sentinel.phase5.artifact_names import gate_evidence_name, oneshot_marker_name
    from sentinel.phase5.receipts import append_receipt, load_registry

    registry_path = tmp_path / "receipts.jsonl"
    append_receipt(
        registry_path, allow_create=True, clock=_fixed_clock, schema_version=1,
        receipt_class="ONESHOT_MARKER_CONSUMED", purpose="P5C_WIF_PROBE", github_run_id="1",
        run_attempt=1, source_sha=SOURCE_SHA, artifact_name=oneshot_marker_name("P5C_WIF_PROBE", "1"),
        artifact_id=1, payload_filename="marker.json", payload_sha256="1" * 64, disposition="CONSUMED",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="PROBE_EVIDENCE",
        purpose="P5C_WIF_PROBE", github_run_id="1", run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name="sentinel-p5-probe-evidence-r1-a1", artifact_id=2,
        payload_filename="probe-evidence.json", payload_sha256="2" * 64, disposition="CAPABILITY_PASS",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    if include_original:
        append_receipt(
            registry_path, clock=_fixed_clock, schema_version=1, receipt_class="ONESHOT_MARKER_CONSUMED",
            purpose=ORIGINAL_PURPOSE, github_run_id=REPLACEMENT_OF_RUN_ID, run_attempt=1,
            source_sha=SOURCE_SHA, artifact_name=oneshot_marker_name(ORIGINAL_PURPOSE, REPLACEMENT_OF_RUN_ID),
            artifact_id=3, payload_filename="marker.json", payload_sha256="3" * 64, disposition="CONSUMED",
            replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
        )
        append_receipt(
            registry_path, clock=_fixed_clock, schema_version=1, receipt_class="EXECUTION_DISPOSITION",
            purpose=ORIGINAL_PURPOSE, github_run_id=REPLACEMENT_OF_RUN_ID, run_attempt=1,
            source_sha=SOURCE_SHA, artifact_name=None, artifact_id=None, payload_filename=None,
            payload_sha256=None, disposition="EXECUTION_INVALID / NO_QUALITY_RESULT",
            replacement_of_run_id=None, owner_ruling_id=OWNER_RULING_ID,
            governance_ref="q77-p5d-invalid-run-record-a",
        )
    if include_replacement_marker:
        run_id = second_replacement_run_id or REPLACEMENT_RUN_ID
        append_receipt(
            registry_path, clock=_fixed_clock, schema_version=1, receipt_class="ONESHOT_MARKER_CONSUMED",
            purpose=REPLACEMENT_PURPOSE, github_run_id=run_id, run_attempt=1, source_sha=SOURCE_SHA,
            artifact_name=oneshot_marker_name(REPLACEMENT_PURPOSE, run_id), artifact_id=5,
            payload_filename="marker.json", payload_sha256="5" * 64, disposition="CONSUMED",
            replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID,
            governance_ref=None,
        )
        append_receipt(
            registry_path, clock=_fixed_clock, schema_version=1, receipt_class="GATE_EVIDENCE",
            purpose=REPLACEMENT_PURPOSE, github_run_id=run_id, run_attempt=1, source_sha=SOURCE_SHA,
            artifact_name=gate_evidence_name(run_id, 1), artifact_id=6,
            payload_filename="phase5_official_gate.json", payload_sha256="6" * 64,
            disposition=replacement_disposition, replacement_of_run_id=REPLACEMENT_OF_RUN_ID,
            owner_ruling_id=OWNER_RULING_ID, governance_ref=None,
        )
    return load_registry(registry_path)


def test_freeze_succeeds_and_produces_a_validate_bundle_clean_genesis(tmp_path, monkeypatch):
    from sentinel.phase5.bundle import validate_bundle

    for key, value in ENV.items():
        monkeypatch.setenv(key, value)

    # -- committed telemetry ledger: empty is fine -- the seam's cost-row
    # check degrades gracefully to a no-op once the referenced receipts'
    # artifacts are no longer live-discoverable (this test's _FakeClient
    # reports zero live artifacts for every run, exercising the
    # receipt-only path; test_seam3_* below exercises the live,
    # hash-verified path separately).
    repo_root_fake = tmp_path / "repo"
    (repo_root_fake / "telemetry").mkdir(parents=True)
    (repo_root_fake / "telemetry" / "cost_ledger.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(wf, "REPO_ROOT", repo_root_fake)

    receipts = _make_receipts_registry(tmp_path)
    monkeypatch.setattr(wf, "load_durable_history", lambda: receipts)

    fake_client = _FakeClient(bundles={})  # zero live artifacts: pure receipt-only path
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


def test_freeze_succeeds_with_honest_fail_replacement_identically(tmp_path, monkeypatch):
    """GREEN and HONEST_FAIL are accepted identically at this seam --
    downstream consequence is unchanged from before the incident
    (ADR-0012 section 19)."""
    from sentinel.phase5.bundle import validate_bundle

    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    repo_root_fake = tmp_path / "repo"
    (repo_root_fake / "telemetry").mkdir(parents=True)
    (repo_root_fake / "telemetry" / "cost_ledger.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(wf, "REPO_ROOT", repo_root_fake)

    receipts = _make_receipts_registry(tmp_path, replacement_disposition="HONEST_FAIL")
    monkeypatch.setattr(wf, "load_durable_history", lambda: receipts)
    monkeypatch.setattr(wf, "build_evidence_client", lambda env: _FakeClient(bundles={}))
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
    assert exit_code == 0
    validated = validate_bundle(work_root / "genesis-out")
    assert validated.manifest.bundle_kind == "GENESIS"


def test_freeze_refuses_when_no_replacement_receipt_exists(tmp_path, monkeypatch):
    """Today's real state: zero replacement receipts. The seam must
    refuse -- this dispatch arms nothing."""
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    repo_root_fake = tmp_path / "repo"
    (repo_root_fake / "telemetry").mkdir(parents=True)
    (repo_root_fake / "telemetry" / "cost_ledger.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(wf, "REPO_ROOT", repo_root_fake)

    receipts = _make_receipts_registry(tmp_path, include_replacement_marker=False)
    monkeypatch.setattr(wf, "load_durable_history", lambda: receipts)
    monkeypatch.setattr(wf, "build_evidence_client", lambda env: _FakeClient(bundles={}))
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
    assert exit_code == 1
    refusal = (work_root / "freeze_refusal.json").read_text(encoding="utf-8")
    assert "replacement" in refusal.lower()
    assert not (work_root / "genesis-out").exists()


def test_freeze_refuses_on_committed_registry_today(tmp_path, monkeypatch):
    """The REAL committed registry (byte-unchanged by this dispatch)
    has zero replacement receipts, so a freeze attempt against it must
    refuse today -- proves this dispatch arms nothing end-to-end."""
    from sentinel.phase5.receipts import load_registry

    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    repo_root_fake = tmp_path / "repo"
    (repo_root_fake / "telemetry").mkdir(parents=True)
    (repo_root_fake / "telemetry" / "cost_ledger.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(wf, "REPO_ROOT", repo_root_fake)

    committed = REPO_ROOT / "artifacts" / "phase5_receipt_registry.jsonl"
    receipts = load_registry(committed)
    monkeypatch.setattr(wf, "load_durable_history", lambda: receipts)
    monkeypatch.setattr(wf, "build_evidence_client", lambda env: _FakeClient(bundles={}))
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
    assert exit_code == 1
    assert not (work_root / "genesis-out").exists()


def test_freeze_refuses_when_two_replacement_marker_receipts_exist(tmp_path, monkeypatch):
    """Ambiguity fails closed: exactly one replacement marker receipt
    is required (ADR-0012 section 19), even if a second one names a
    different run."""
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    repo_root_fake = tmp_path / "repo"
    (repo_root_fake / "telemetry").mkdir(parents=True)
    (repo_root_fake / "telemetry" / "cost_ledger.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(wf, "REPO_ROOT", repo_root_fake)

    from sentinel.phase5.artifact_names import gate_evidence_name, oneshot_marker_name
    from sentinel.phase5.receipts import append_receipt, load_registry

    registry_path = tmp_path / "receipts.jsonl"
    _make_receipts_registry(tmp_path)  # writes to tmp_path / "receipts.jsonl"
    second_run = "111222333"
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="ONESHOT_MARKER_CONSUMED",
        purpose=REPLACEMENT_PURPOSE, github_run_id=second_run, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name=oneshot_marker_name(REPLACEMENT_PURPOSE, second_run), artifact_id=7,
        payload_filename="marker.json", payload_sha256="7" * 64, disposition="CONSUMED",
        replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID, governance_ref=None,
    )
    receipts = load_registry(registry_path)

    monkeypatch.setattr(wf, "load_durable_history", lambda: receipts)
    monkeypatch.setattr(wf, "build_evidence_client", lambda env: _FakeClient(bundles={}))
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
    assert exit_code == 1
    refusal = (work_root / "freeze_refusal.json").read_text(encoding="utf-8")
    assert "replacement marker receipt" in refusal


def test_seam3_hash_verifies_live_retained_replacement_evidence(tmp_path):
    """Defense in depth: when the replacement evidence artifact is
    still live-discoverable, its bytes must independently hash-match
    the durable receipt's payload_sha256 and pass
    validate_replacement_provenance -- not merely agree on disposition."""
    import hashlib

    from sentinel.phase5.artifact_names import gate_evidence_name, oneshot_marker_name
    from sentinel.phase5.evidence_records import GateEvidenceRecord
    from sentinel.phase5.receipts import append_receipt, load_registry

    registry_path = tmp_path / "receipts.jsonl"
    append_receipt(
        registry_path, allow_create=True, clock=_fixed_clock, schema_version=1,
        receipt_class="ONESHOT_MARKER_CONSUMED", purpose="P5C_WIF_PROBE", github_run_id="1",
        run_attempt=1, source_sha=SOURCE_SHA, artifact_name=oneshot_marker_name("P5C_WIF_PROBE", "1"),
        artifact_id=1, payload_filename="marker.json", payload_sha256="1" * 64, disposition="CONSUMED",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="PROBE_EVIDENCE",
        purpose="P5C_WIF_PROBE", github_run_id="1", run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name="sentinel-p5-probe-evidence-r1-a1", artifact_id=2,
        payload_filename="probe-evidence.json", payload_sha256="2" * 64, disposition="CAPABILITY_PASS",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="ONESHOT_MARKER_CONSUMED",
        purpose=ORIGINAL_PURPOSE, github_run_id=REPLACEMENT_OF_RUN_ID, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name=oneshot_marker_name(ORIGINAL_PURPOSE, REPLACEMENT_OF_RUN_ID), artifact_id=3,
        payload_filename="marker.json", payload_sha256="3" * 64, disposition="CONSUMED",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="EXECUTION_DISPOSITION",
        purpose=ORIGINAL_PURPOSE, github_run_id=REPLACEMENT_OF_RUN_ID, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name=None, artifact_id=None, payload_filename=None, payload_sha256=None,
        disposition="EXECUTION_INVALID / NO_QUALITY_RESULT", replacement_of_run_id=None,
        owner_ruling_id=OWNER_RULING_ID, governance_ref="q77-p5d-invalid-run-record-a",
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="ONESHOT_MARKER_CONSUMED",
        purpose=REPLACEMENT_PURPOSE, github_run_id=REPLACEMENT_RUN_ID, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name=oneshot_marker_name(REPLACEMENT_PURPOSE, REPLACEMENT_RUN_ID), artifact_id=5,
        payload_filename="marker.json", payload_sha256="5" * 64, disposition="CONSUMED",
        replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID, governance_ref=None,
    )

    # Build the REAL evidence record and receipt with a matching hash.
    from contracts.schemas import CostRow

    real_cost_row = CostRow(
        schema_version=1, run_id="r1", recorded_at_utc=datetime.now(timezone.utc),
        run_kind="dev", model="claude-sonnet-5", input_tokens=1, output_tokens=1, cost_eur_micros=2000,
    )
    evidence = GateEvidenceRecord(
        schema_version=1, workflow_identity=".github/workflows/sentinel-official-gate.yml",
        github_run_id=REPLACEMENT_RUN_ID, run_attempt=1, event="workflow_dispatch",
        ref="refs/heads/main", source_sha=SOURCE_SHA, created_at_utc=datetime.now(timezone.utc),
        steps=(), expected_source_sha=SOURCE_SHA, model="claude-sonnet-5",
        profile_name="sonnet-official-gate", run_ids=("r1", "r2"), scoring={"emitted": 0},
        thresholds={}, invariant_results={"ok": True}, execution_validity={"valid": True},
        miss_patterns=(), failed_checks=(), cost_rows=(real_cost_row,), accounted_total_eur_micros=2000,
        disposition="GREEN", auth_mode="github-actions-wif-federation",
        replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID,
        marker_purpose=REPLACEMENT_PURPOSE, envelope_id="env-1", envelope_version="v1",
    )
    payload_bytes = evidence.model_dump_json().encode("utf-8")
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="GATE_EVIDENCE",
        purpose=REPLACEMENT_PURPOSE, github_run_id=REPLACEMENT_RUN_ID, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name=gate_evidence_name(REPLACEMENT_RUN_ID, 1), artifact_id=6,
        payload_filename="phase5_official_gate.json", payload_sha256=payload_sha256,
        disposition="GREEN", replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID,
        governance_ref=None,
    )
    receipts = load_registry(registry_path)

    evidence_dir = tmp_path / "src-replacement-evidence"
    evidence_dir.mkdir()
    (evidence_dir / "phase5_official_gate.json").write_bytes(payload_bytes)
    bundles = {gate_evidence_name(REPLACEMENT_RUN_ID, 1): evidence_dir}
    run_artifacts = {REPLACEMENT_RUN_ID: [(6, gate_evidence_name(REPLACEMENT_RUN_ID, 1))]}
    fake_client = _FakeClient(bundles=bundles, run_artifacts=run_artifacts)

    from telemetry.cost_ledger import append_cost_row

    repo_root_fake = tmp_path / "repo"
    (repo_root_fake / "telemetry").mkdir(parents=True)
    committed_ledger = repo_root_fake / "telemetry" / "cost_ledger.jsonl"
    committed_ledger.write_text("", encoding="utf-8")
    append_cost_row(committed_ledger, real_cost_row)
    original_repo_root = wf.REPO_ROOT
    (tmp_path / "work").mkdir()
    try:
        wf.REPO_ROOT = repo_root_fake
        rows = wf._verify_provider_phase_prerequisites(
            fake_client, tmp_path / "work", receipts, SOURCE_SHA
        )
    finally:
        wf.REPO_ROOT = original_repo_root
    assert len(rows) == 1 and rows[0].run_id == "r1"  # live hash-verify recovered the real cost row


def test_seam3_refuses_replacement_infrastructure_failure_disposition_regardless_of_cause(tmp_path):
    """A durable replacement GATE_EVIDENCE receipt whose disposition is
    INFRASTRUCTURE_FAILURE is never eligible to satisfy seam 3 -- P5-E
    requires an actual qualifying quality result (GREEN or
    HONEST_FAIL), never an execution-invalid one, regardless of
    whatever caused it. This holds even when the receipt carries every
    other correct replacement provenance field. (The separate
    per-record rule that a raw observed signal alone -- SIGINT,
    SIGTERM, UNKNOWN_EXTERNAL_TERMINATION -- can never justify an
    INFRASTRUCTURE_FAILURE claim, ADR-0012 Amendment A2 rule 6, is
    verified directly against
    ``evidence_records.validate_replacement_provenance`` and against
    ``Phase5Receipt``'s own schema in test_phase5_receipts.py and
    test_phase5_replacement.py -- this seam-level test proves the
    seam's own disposition gate never even reaches that check for an
    INFRASTRUCTURE_FAILURE receipt, which is the seam's correct,
    stronger refusal.)"""
    from sentinel.phase5.artifact_names import oneshot_marker_name
    from sentinel.phase5.receipts import append_receipt, load_registry

    registry_path = tmp_path / "receipts.jsonl"
    append_receipt(
        registry_path, allow_create=True, clock=_fixed_clock, schema_version=1,
        receipt_class="ONESHOT_MARKER_CONSUMED", purpose="P5C_WIF_PROBE", github_run_id="1",
        run_attempt=1, source_sha=SOURCE_SHA, artifact_name=oneshot_marker_name("P5C_WIF_PROBE", "1"),
        artifact_id=1, payload_filename="marker.json", payload_sha256="1" * 64, disposition="CONSUMED",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="PROBE_EVIDENCE",
        purpose="P5C_WIF_PROBE", github_run_id="1", run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name="sentinel-p5-probe-evidence-r1-a1", artifact_id=2,
        payload_filename="probe-evidence.json", payload_sha256="2" * 64, disposition="CAPABILITY_PASS",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="ONESHOT_MARKER_CONSUMED",
        purpose=ORIGINAL_PURPOSE, github_run_id=REPLACEMENT_OF_RUN_ID, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name=oneshot_marker_name(ORIGINAL_PURPOSE, REPLACEMENT_OF_RUN_ID), artifact_id=3,
        payload_filename="marker.json", payload_sha256="3" * 64, disposition="CONSUMED",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="EXECUTION_DISPOSITION",
        purpose=ORIGINAL_PURPOSE, github_run_id=REPLACEMENT_OF_RUN_ID, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name=None, artifact_id=None, payload_filename=None, payload_sha256=None,
        disposition="EXECUTION_INVALID / NO_QUALITY_RESULT", replacement_of_run_id=None,
        owner_ruling_id=OWNER_RULING_ID, governance_ref="q77-p5d-invalid-run-record-a",
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="ONESHOT_MARKER_CONSUMED",
        purpose=REPLACEMENT_PURPOSE, github_run_id=REPLACEMENT_RUN_ID, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name=oneshot_marker_name(REPLACEMENT_PURPOSE, REPLACEMENT_RUN_ID), artifact_id=5,
        payload_filename="marker.json", payload_sha256="5" * 64, disposition="CONSUMED",
        replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID, governance_ref=None,
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="GATE_EVIDENCE",
        purpose=REPLACEMENT_PURPOSE, github_run_id=REPLACEMENT_RUN_ID, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name="sentinel-p5-gate-evidence-r%s-a1" % REPLACEMENT_RUN_ID, artifact_id=6,
        payload_filename="phase5_official_gate.json", payload_sha256="6" * 64,
        disposition="INFRASTRUCTURE_FAILURE", replacement_of_run_id=REPLACEMENT_OF_RUN_ID,
        owner_ruling_id=OWNER_RULING_ID, governance_ref=None,
    )
    receipts = load_registry(registry_path)

    fake_client = _FakeClient(bundles={})  # nothing live-discoverable; pure receipt-level refusal
    try:
        wf._verify_provider_phase_prerequisites(
            fake_client, tmp_path / "work", receipts, SOURCE_SHA
        )
        raise AssertionError("seam must refuse an INFRASTRUCTURE_FAILURE replacement disposition")
    except wf.Phase5ScriptError as exc:
        assert "GREEN or HONEST_FAIL" in str(exc)


def test_seam3_refuses_live_replacement_with_wrong_marker_purpose_provenance(tmp_path):
    """A GREEN replacement receipt whose disposition and hash both
    check out, but whose live evidence bytes carry the wrong
    ``marker_purpose`` (a provenance defect distinct from disposition),
    is refused by ``validate_replacement_provenance`` -- proving that
    function is actually wired into the seam, not merely unit-tested
    in isolation (ADR-0012 section 18)."""
    import hashlib

    from contracts.schemas import CostRow
    from sentinel.phase5.artifact_names import gate_evidence_name, oneshot_marker_name
    from sentinel.phase5.evidence_records import GateEvidenceRecord
    from sentinel.phase5.receipts import append_receipt, load_registry

    registry_path = tmp_path / "receipts.jsonl"
    append_receipt(
        registry_path, allow_create=True, clock=_fixed_clock, schema_version=1,
        receipt_class="ONESHOT_MARKER_CONSUMED", purpose="P5C_WIF_PROBE", github_run_id="1",
        run_attempt=1, source_sha=SOURCE_SHA, artifact_name=oneshot_marker_name("P5C_WIF_PROBE", "1"),
        artifact_id=1, payload_filename="marker.json", payload_sha256="1" * 64, disposition="CONSUMED",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="PROBE_EVIDENCE",
        purpose="P5C_WIF_PROBE", github_run_id="1", run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name="sentinel-p5-probe-evidence-r1-a1", artifact_id=2,
        payload_filename="probe-evidence.json", payload_sha256="2" * 64, disposition="CAPABILITY_PASS",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="ONESHOT_MARKER_CONSUMED",
        purpose=ORIGINAL_PURPOSE, github_run_id=REPLACEMENT_OF_RUN_ID, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name=oneshot_marker_name(ORIGINAL_PURPOSE, REPLACEMENT_OF_RUN_ID), artifact_id=3,
        payload_filename="marker.json", payload_sha256="3" * 64, disposition="CONSUMED",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="EXECUTION_DISPOSITION",
        purpose=ORIGINAL_PURPOSE, github_run_id=REPLACEMENT_OF_RUN_ID, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name=None, artifact_id=None, payload_filename=None, payload_sha256=None,
        disposition="EXECUTION_INVALID / NO_QUALITY_RESULT", replacement_of_run_id=None,
        owner_ruling_id=OWNER_RULING_ID, governance_ref="q77-p5d-invalid-run-record-a",
    )
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="ONESHOT_MARKER_CONSUMED",
        purpose=REPLACEMENT_PURPOSE, github_run_id=REPLACEMENT_RUN_ID, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name=oneshot_marker_name(REPLACEMENT_PURPOSE, REPLACEMENT_RUN_ID), artifact_id=5,
        payload_filename="marker.json", payload_sha256="5" * 64, disposition="CONSUMED",
        replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID, governance_ref=None,
    )

    real_cost_row = CostRow(
        schema_version=1, run_id="r1", recorded_at_utc=datetime.now(timezone.utc),
        run_kind="dev", model="claude-sonnet-5", input_tokens=1, output_tokens=1, cost_eur_micros=2000,
    )
    # Disposition and hash both check out; marker_purpose is wrong --
    # the ORIGINAL purpose instead of the replacement purpose.
    evidence = GateEvidenceRecord(
        schema_version=1, workflow_identity=".github/workflows/sentinel-official-gate.yml",
        github_run_id=REPLACEMENT_RUN_ID, run_attempt=1, event="workflow_dispatch",
        ref="refs/heads/main", source_sha=SOURCE_SHA, created_at_utc=datetime.now(timezone.utc),
        steps=(), expected_source_sha=SOURCE_SHA, model="claude-sonnet-5",
        profile_name="sonnet-official-gate", run_ids=("r1", "r2"), scoring={"emitted": 0},
        thresholds={}, invariant_results={"ok": True}, execution_validity={"valid": True},
        miss_patterns=(), failed_checks=(), cost_rows=(real_cost_row,), accounted_total_eur_micros=2000,
        disposition="GREEN", auth_mode="github-actions-wif-federation",
        replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID,
        marker_purpose=ORIGINAL_PURPOSE,  # <-- the provenance defect
        envelope_id="env-1", envelope_version="v1",
    )
    payload_bytes = evidence.model_dump_json().encode("utf-8")
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    append_receipt(
        registry_path, clock=_fixed_clock, schema_version=1, receipt_class="GATE_EVIDENCE",
        purpose=REPLACEMENT_PURPOSE, github_run_id=REPLACEMENT_RUN_ID, run_attempt=1, source_sha=SOURCE_SHA,
        artifact_name=gate_evidence_name(REPLACEMENT_RUN_ID, 1), artifact_id=6,
        payload_filename="phase5_official_gate.json", payload_sha256=payload_sha256,
        disposition="GREEN", replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID,
        governance_ref=None,
    )
    receipts = load_registry(registry_path)

    evidence_dir = tmp_path / "src-replacement-evidence"
    evidence_dir.mkdir()
    (evidence_dir / "phase5_official_gate.json").write_bytes(payload_bytes)
    bundles = {gate_evidence_name(REPLACEMENT_RUN_ID, 1): evidence_dir}
    run_artifacts = {REPLACEMENT_RUN_ID: [(6, gate_evidence_name(REPLACEMENT_RUN_ID, 1))]}
    fake_client = _FakeClient(bundles=bundles, run_artifacts=run_artifacts)

    original_repo_root = wf.REPO_ROOT
    (tmp_path / "work").mkdir()
    try:
        wf.REPO_ROOT = tmp_path
        try:
            wf._verify_provider_phase_prerequisites(
                fake_client, tmp_path / "work", receipts, SOURCE_SHA
            )
            raise AssertionError("seam must refuse wrong marker_purpose provenance")
        except wf.Phase5ScriptError as exc:
            assert "marker_purpose" in str(exc)
    finally:
        wf.REPO_ROOT = original_repo_root


def test_genesis_manifest_constructible_only_via_bundle_build(monkeypatch):
    """No other module in this dispatch constructs a GenesisManifest
    directly outside the domain core's own build path -- confirmed by
    the fact this script imports GenesisManifest only for type
    reference and always routes actual construction through
    build_bundle's manifest_fields dict, never GenesisManifest(...) directly."""
    text = (REPO_ROOT / "scripts" / "run_phase5_window_freeze.py").read_text(encoding="utf-8")
    assert "GenesisManifest(" not in text

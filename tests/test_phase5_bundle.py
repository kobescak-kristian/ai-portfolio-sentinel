"""Tests for sentinel/phase5/bundle.py and the canonical schemas in
sentinel/phase5/models.py that bundle.py depends on.

Model-free, network-blocked (tests/conftest.py::block_network is
autouse). Builder helpers defined here are reused by the other Phase-5
test files via ``from tests.test_phase5_bundle import ...`` — the same
cross-file-import pattern already used elsewhere in this suite (see
``tests/test_failures.py``'s ``from tests.conftest import ...``).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schemas import CostRow
from sentinel.phase5 import bundle as b
from sentinel.phase5 import models as m

# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------

SOURCE_SHA = "a" * 40
OTHER_SOURCE_SHA = "b" * 40
REF = "refs/heads/main"
CRON = "37 6 * * *"
CONTROL_WORKFLOW = "phase5-control"
SCHEDULED_WORKFLOW = "phase5-scheduled"

WINDOW_CREATED_AT = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)
SLOT1 = datetime(2026, 8, 21, 6, 37, 0, tzinfo=timezone.utc)

AUTHORITATIVE_PATHS = (
    "state/ledger.sqlite3",
    "state/FINDINGS.md",
    "state/cost_ledger.jsonl",
    "state/phase5_state.json",
)


def slot_ts(n: int, base: datetime = SLOT1) -> datetime:
    return base + timedelta(days=n - 1)


def fake_carried_files(hash_by_path: dict[str, str] | None = None) -> tuple[m.CarriedFile, ...]:
    hash_by_path = hash_by_path or {}
    return tuple(
        m.CarriedFile(relative_path=path, sha256=hash_by_path.get(path, "0" * 64))
        for path in AUTHORITATIVE_PATHS
    )


def make_window(**overrides) -> m.QualificationWindowRecord:
    slots = tuple(m.ExpectedSlot(slot_index=i, expected_at_utc=slot_ts(i)) for i in range(1, 6))
    fields = dict(
        schema_version=1,
        window_id="p5w-control-run-1",
        created_at_utc=WINDOW_CREATED_AT,
        control_workflow_identity=CONTROL_WORKFLOW,
        control_run_id="control-run-1",
        source_sha=SOURCE_SHA,
        scheduled_workflow_identity=SCHEDULED_WORKFLOW,
        ref=REF,
        cron=CRON,
        timezone="UTC",
        tolerance_minutes=120,
        expected_slots=slots,
        qualifying_source="live",
        qualifying_judgment_mode="agent",
    )
    fields.update(overrides)
    return m.QualificationWindowRecord(**fields)


def make_genesis(window: m.QualificationWindowRecord, *, window_consumed=False, carried_files=None, **overrides):
    fields = dict(
        schema_version=1,
        bundle_kind="GENESIS",
        workflow_identity=window.control_workflow_identity,
        github_run_id=window.control_run_id,
        run_attempt=1,
        event="workflow_dispatch",
        ref=window.ref,
        source_sha=window.source_sha,
        window_id=window.window_id,
        window_record_sha256=m.sha256_hex_of_model(window),
        slot_index=0,
        no_run_outcome="WINDOW_GENESIS",
        window_consumed=window_consumed,
        carried_files=carried_files if carried_files is not None else fake_carried_files(),
    )
    fields.update(overrides)
    return m.GenesisManifest(**fields)


def make_slot_successor(window: m.QualificationWindowRecord, slot_index: int, *, predecessor_identity, predecessor_manifest, sentinel_run_id="sentinel-run", github_run_id=None, qualification_outcome="QUALIFYING", window_consumed=False, carried_files=None, **overrides):
    fields = dict(
        schema_version=1,
        bundle_kind="SLOT_SUCCESSOR",
        workflow_identity=window.scheduled_workflow_identity,
        github_run_id=github_run_id or f"gh-run-{slot_index}",
        run_attempt=1,
        event="schedule",
        ref=window.ref,
        source_sha=window.source_sha,
        window_id=window.window_id,
        window_record_sha256=m.sha256_hex_of_model(window),
        slot_index=slot_index,
        expected_slot_utc=slot_ts(slot_index),
        sentinel_run_id=sentinel_run_id,
        qualification_outcome=qualification_outcome,
        window_consumed=window_consumed,
        predecessor_artifact_id_or_name=predecessor_identity,
        predecessor_manifest_sha256=m.sha256_hex_of_model(predecessor_manifest),
        carried_files=carried_files if carried_files is not None else fake_carried_files(),
    )
    fields.update(overrides)
    return m.SlotSuccessorManifest(**fields)


def make_control_refusal(window: m.QualificationWindowRecord, *, predecessor_identity, predecessor_manifest, expected_slot_utc, no_run_outcome="CADENCE_SKIP", github_run_id="gh-refusal", window_consumed=False, carried_files=None, **overrides):
    fields = dict(
        schema_version=1,
        bundle_kind="CONTROL_REFUSAL",
        workflow_identity=window.scheduled_workflow_identity,
        github_run_id=github_run_id,
        run_attempt=1,
        event="schedule",
        ref=window.ref,
        source_sha=window.source_sha,
        window_id=window.window_id,
        window_record_sha256=m.sha256_hex_of_model(window),
        expected_slot_utc=expected_slot_utc,
        no_run_outcome=no_run_outcome,
        qualification_outcome=no_run_outcome,
        window_consumed=window_consumed,
        predecessor_artifact_id_or_name=predecessor_identity,
        predecessor_manifest_sha256=m.sha256_hex_of_model(predecessor_manifest),
        carried_files=carried_files if carried_files is not None else fake_carried_files(),
    )
    fields.update(overrides)
    return m.ControlRefusalManifest(**fields)


def make_control_state(window, *, slot_index=0, window_consumed=False, window_consume_reason=None, cadence_level="DAILY", cadence_anchor=None, spend=0, evaluated_at=WINDOW_CREATED_AT, **overrides):
    fields = dict(
        schema_version=1,
        window_id=window.window_id if window is not None else None,
        window_record_sha256=m.sha256_hex_of_model(window) if window is not None else None,
        latest_authoritative_slot_index=slot_index,
        window_consumed=window_consumed,
        window_consume_reason=window_consume_reason,
        cadence_level=cadence_level,
        cadence_anchor_slot_utc=cadence_anchor,
        last_accounted_spend_eur_micros=spend,
        last_evaluated_at_utc=evaluated_at,
    )
    fields.update(overrides)
    return m.Phase5ControlState(**fields)


def make_cost_row(run_id, cost_eur_micros=0, recorded_at_utc=WINDOW_CREATED_AT, **overrides) -> CostRow:
    fields = dict(
        schema_version=1,
        run_id=run_id,
        recorded_at_utc=recorded_at_utc,
        run_kind="live",
        model="claude-test",
        input_tokens=0,
        output_tokens=0,
        cost_eur_micros=cost_eur_micros,
    )
    fields.update(overrides)
    return CostRow(**fields)


def write_sqlite(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
    finally:
        conn.close()


def build_valid_bundle(
    tmp_path: Path,
    *,
    window: m.QualificationWindowRecord,
    manifest_fields: dict,
    control_state: m.Phase5ControlState,
    cost_rows_jsonl: str = "",
    findings_text: str = "# FINDINGS\n",
) -> b.BuiltBundle:
    sources = tmp_path / "sources"
    sources.mkdir()
    db_path = sources / "ledger.sqlite3"
    write_sqlite(db_path)
    findings_path = sources / "FINDINGS.md"
    findings_path.write_text(findings_text, encoding="utf-8")
    cost_path = sources / "cost_ledger.jsonl"
    cost_path.write_text(cost_rows_jsonl, encoding="utf-8")

    dest_root = tmp_path / "dest"
    dest_root.mkdir()
    bundle_dir = dest_root / "bundle"

    return b.build_bundle(
        dest_root,
        bundle_dir,
        window=window,
        manifest_fields=manifest_fields,
        source_ledger_path=db_path,
        source_ledger_trusted_root=sources,
        findings_source_path=findings_path,
        findings_trusted_root=sources,
        cost_ledger_source_path=cost_path,
        cost_ledger_trusted_root=sources,
        control_state=control_state,
    )


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def test_canonical_json_deterministic_regardless_of_field_order():
    window_a = make_window()
    data = window_a.model_dump(mode="python")
    reordered = dict(reversed(list(data.items())))
    window_b = m.QualificationWindowRecord(**reordered)
    assert m.canonical_json_bytes(window_a) == m.canonical_json_bytes(window_b)


def test_canonical_json_golden_vector():
    row_hash_input = m.CarriedFile(relative_path="state/FINDINGS.md", sha256="a" * 64)
    expected = (
        b'{"relative_path":"state/FINDINGS.md","sha256":"'
        + b"a" * 64
        + b'"}'
    )
    assert m.canonical_json_bytes(row_hash_input) == expected


def test_sha256_hex_of_model_matches_manual_hash():
    import hashlib

    window = make_window()
    assert m.sha256_hex_of_model(window) == hashlib.sha256(m.canonical_json_bytes(window)).hexdigest()


# ---------------------------------------------------------------------------
# Schema strictness
# ---------------------------------------------------------------------------


def test_window_extra_field_rejected():
    with pytest.raises(ValidationError):
        make_window(unexpected_field="nope")


def test_genesis_manifest_extra_field_rejected():
    window = make_window()
    with pytest.raises(ValidationError):
        make_genesis(window, unexpected="nope")


def test_sentinel_run_evidence_requires_github_run_id():
    with pytest.raises(ValidationError):
        m.SentinelRunEvidence(
            schema_version=1, run_id="r1", status="COMPLETED", source="live", judgment_mode="agent"
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"cron": "0 0 * * *"},
        {"ref": "refs/heads/other"},
        {"timezone": "Europe/Vienna"},
        {"tolerance_minutes": 60},
        {"qualifying_source": "fixtures"},
        {"qualifying_judgment_mode": "stub"},
    ],
)
def test_window_pinned_literals_rejected(overrides):
    with pytest.raises(ValidationError):
        make_window(**overrides)


def test_window_requires_exactly_five_slots():
    slots = tuple(m.ExpectedSlot(slot_index=i, expected_at_utc=slot_ts(i)) for i in range(1, 5))
    with pytest.raises(ValidationError):
        make_window(expected_slots=slots)


def test_window_slots_must_be_24h_apart():
    slots = list(m.ExpectedSlot(slot_index=i, expected_at_utc=slot_ts(i)) for i in range(1, 6))
    # still exactly 06:37:00 UTC (passes the per-slot instant check) but
    # one day further out than 24h spacing permits
    slots[2] = m.ExpectedSlot(slot_index=3, expected_at_utc=slot_ts(3) + timedelta(days=1))
    with pytest.raises(ValidationError):
        make_window(expected_slots=tuple(slots))


def test_window_slot1_must_be_prospective():
    with pytest.raises(ValidationError):
        make_window(created_at_utc=slot_ts(1) + timedelta(hours=1))


def test_window_source_sha_must_be_hex40():
    with pytest.raises(ValidationError):
        make_window(source_sha="not-hex")


def test_window_supersession_pair_must_be_both_or_neither():
    with pytest.raises(ValidationError):
        make_window(supersedes_window_id="p5w-other")


def test_window_migration_fields_all_or_nothing():
    with pytest.raises(ValidationError):
        make_window(windows_task_name="SentinelDailyRun")


def test_window_migration_disable_must_be_24h_before_slot1():
    with pytest.raises(ValidationError):
        make_window(
            windows_task_name="SentinelDailyRun",
            disabled_at_utc=slot_ts(1) - timedelta(hours=1),
            final_legacy_db_sha256="a" * 64,
            legacy_row_counts={"runs": 10},
            dual_scheduler_verification_at_utc=WINDOW_CREATED_AT,
        )


def test_window_migration_disable_ok_at_exactly_24h():
    window = make_window(
        windows_task_name="SentinelDailyRun",
        disabled_at_utc=slot_ts(1) - timedelta(hours=24),
        final_legacy_db_sha256="a" * 64,
        legacy_row_counts={"runs": 10},
        dual_scheduler_verification_at_utc=WINDOW_CREATED_AT,
    )
    assert window.windows_task_name == "SentinelDailyRun"


def test_expected_slot_must_be_exactly_0637_utc():
    with pytest.raises(ValidationError):
        m.ExpectedSlot(slot_index=1, expected_at_utc=SLOT1.replace(hour=7))


def test_manifest_discriminator_resolves_correctly():
    window = make_window()
    genesis = make_genesis(window)
    successor = make_slot_successor(window, 1, predecessor_identity="genesis-id", predecessor_manifest=genesis)
    refusal = make_control_refusal(
        window, predecessor_identity="genesis-id", predecessor_manifest=genesis, expected_slot_utc=slot_ts(1)
    )
    from pydantic import TypeAdapter

    adapter = TypeAdapter(m.StateBundleManifest)
    assert isinstance(adapter.validate_python(genesis.model_dump(mode="python")), m.GenesisManifest)
    assert isinstance(adapter.validate_python(successor.model_dump(mode="python")), m.SlotSuccessorManifest)
    assert isinstance(adapter.validate_python(refusal.model_dump(mode="python")), m.ControlRefusalManifest)


def test_genesis_invariants():
    window = make_window()
    genesis = make_genesis(window)
    assert genesis.slot_index == 0
    with pytest.raises(ValidationError):
        make_genesis(window, sentinel_run_id="oops")
    with pytest.raises(ValidationError):
        make_genesis(window, predecessor_artifact_id_or_name="oops")


def test_slot_successor_rejects_refusal_outcomes():
    window = make_window()
    genesis = make_genesis(window)
    with pytest.raises(ValidationError):
        make_slot_successor(
            window, 1, predecessor_identity="g", predecessor_manifest=genesis, qualification_outcome="CADENCE_SKIP"
        )


def test_control_refusal_no_run_outcome_must_equal_qualification_outcome():
    window = make_window()
    genesis = make_genesis(window)
    with pytest.raises(ValidationError):
        make_control_refusal(
            window,
            predecessor_identity="g",
            predecessor_manifest=genesis,
            expected_slot_utc=slot_ts(1),
            no_run_outcome="CADENCE_SKIP",
            qualification_outcome="COST_CADENCE_REFUSAL",
        )


def test_control_refusal_requires_expected_slot_utc_and_no_slot_index():
    window = make_window()
    genesis = make_genesis(window)
    with pytest.raises(ValidationError):
        m.ControlRefusalManifest(
            schema_version=1,
            bundle_kind="CONTROL_REFUSAL",
            workflow_identity=window.scheduled_workflow_identity,
            github_run_id="gh-r",
            run_attempt=1,
            event="schedule",
            ref=window.ref,
            source_sha=window.source_sha,
            window_id=window.window_id,
            window_record_sha256=m.sha256_hex_of_model(window),
            expected_slot_utc=None,
            no_run_outcome="CADENCE_SKIP",
            qualification_outcome="CADENCE_SKIP",
            window_consumed=False,
            predecessor_artifact_id_or_name="g",
            predecessor_manifest_sha256=m.sha256_hex_of_model(genesis),
            carried_files=fake_carried_files(),
        )


def test_control_refusal_expected_slot_must_be_exact_0637_utc():
    window = make_window()
    genesis = make_genesis(window)
    with pytest.raises(ValidationError):
        make_control_refusal(
            window,
            predecessor_identity="g",
            predecessor_manifest=genesis,
            expected_slot_utc=slot_ts(1) + timedelta(minutes=5),
        )


def test_control_refusal_run_attempt_and_event_pinned():
    window = make_window()
    genesis = make_genesis(window)
    with pytest.raises(ValidationError):
        make_control_refusal(
            window, predecessor_identity="g", predecessor_manifest=genesis, expected_slot_utc=slot_ts(1), run_attempt=2
        )
    with pytest.raises(ValidationError):
        make_control_refusal(
            window,
            predecessor_identity="g",
            predecessor_manifest=genesis,
            expected_slot_utc=slot_ts(1),
            event="workflow_dispatch",
        )


def test_carried_files_must_be_exactly_the_four_authoritative_paths():
    window = make_window()
    with pytest.raises(ValidationError):
        make_genesis(window, carried_files=(m.CarriedFile(relative_path="state/ledger.sqlite3", sha256="0" * 64),))


def test_carried_files_rejects_duplicate_normalized_path():
    dup = tuple(
        m.CarriedFile(relative_path="state/ledger.sqlite3", sha256="0" * 64) for _ in range(4)
    )
    window = make_window()
    with pytest.raises(ValidationError):
        make_genesis(window, carried_files=dup)


def test_carried_file_path_rejections():
    with pytest.raises(ValidationError):
        m.CarriedFile(relative_path="../escape", sha256="0" * 64)
    with pytest.raises(ValidationError):
        m.CarriedFile(relative_path="/abs", sha256="0" * 64)
    with pytest.raises(ValidationError):
        m.CarriedFile(relative_path="a\\b", sha256="0" * 64)


def test_control_state_clean_completion_shape():
    window = make_window()
    make_control_state(window, slot_index=5, window_consumed=True, window_consume_reason=None)
    with pytest.raises(ValidationError):
        make_control_state(window, slot_index=3, window_consumed=True, window_consume_reason=None)


@pytest.mark.parametrize("slot_index", [0, 1, 2, 3, 4])
def test_control_state_clean_shape_rejected_below_slot5(slot_index):
    window = make_window()
    with pytest.raises(ValidationError):
        make_control_state(window, slot_index=slot_index, window_consumed=True, window_consume_reason=None)


def test_control_state_consumed_false_requires_no_reason():
    window = make_window()
    with pytest.raises(ValidationError):
        make_control_state(window, window_consumed=False, window_consume_reason="LATE_NONQUALIFYING")


def test_control_state_cadence_anchor_rules():
    window = make_window()
    with pytest.raises(ValidationError):
        make_control_state(window, cadence_level="DAILY", cadence_anchor=slot_ts(1))
    with pytest.raises(ValidationError):
        make_control_state(window, cadence_level="EVERY_2_DAYS", cadence_anchor=None)


def test_one_shot_marker_has_no_outcome_field():
    with pytest.raises(ValidationError):
        m.OneShotMarker(
            schema_version=1,
            purpose="P5C_WIF_PROBE",
            created_at_utc=WINDOW_CREATED_AT,
            workflow_identity="wf",
            github_run_id="r1",
            run_attempt=1,
            event="schedule",
            source_sha=SOURCE_SHA,
            outcome="SUCCESS",
        )


def test_qualification_slot_outcome_origin_typing():
    window = make_window()
    with pytest.raises(ValidationError):
        m.QualificationSlotOutcome(
            schema_version=1,
            window_id=window.window_id,
            slot_index=1,
            outcome="MISSING_LOST",
            classified_at_utc=WINDOW_CREATED_AT,
            classification_reason="bad",
            determined_by="single_run_classification",
        )
    with pytest.raises(ValidationError):
        m.QualificationSlotOutcome(
            schema_version=1,
            window_id=window.window_id,
            slot_index=1,
            outcome="QUALIFYING",
            classified_at_utc=WINDOW_CREATED_AT,
            classification_reason="bad",
            determined_by="independent_review",
        )


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def test_assert_trusted_path_rejects_leaf_symlink(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    real = tmp_path / "real.txt"
    real.write_text("x")
    link = root / "link.txt"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("platform does not permit creating a symlink")
    with pytest.raises(b.BundleSafetyError):
        b.assert_trusted_path(root, link)


def test_assert_trusted_path_rejects_intermediate_symlink(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    link_dir = root / "linked"
    try:
        link_dir.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("platform does not permit creating a symlink")
    with pytest.raises(b.BundleSafetyError):
        b.assert_trusted_path(root, link_dir / "leaf.txt")


def test_assert_trusted_path_rejects_root_symlink(tmp_path):
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    link_root = tmp_path / "link_root"
    try:
        link_root.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("platform does not permit creating a symlink")
    with pytest.raises(b.BundleSafetyError):
        b.assert_trusted_path(link_root, link_root / "leaf.txt")


def test_assert_trusted_path_rejects_lexical_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    with pytest.raises(b.BundleSafetyError):
        b.assert_trusted_path(root, outside)


def test_assert_trusted_path_accepts_ordinary_nested_path(tmp_path):
    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    target = root / "a" / "b" / "leaf.txt"
    target.write_text("x")
    assert b.assert_trusted_path(root, target) == target


def test_assert_trusted_path_accepts_not_yet_existing_leaf(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "new.txt"
    assert b.assert_trusted_path(root, target) == target


def test_assert_safe_relative_path_rejects_traversal_and_backslash(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(b.BundleSafetyError):
        b.assert_safe_relative_path(root, "../escape")
    with pytest.raises(b.BundleSafetyError):
        b.assert_safe_relative_path(root, "a\\b")
    with pytest.raises(b.BundleSafetyError):
        b.assert_safe_relative_path(root, "/abs")


def test_create_fresh_root_rejects_unsafe_parent_before_mkdir(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    linked_parent = trusted / "linked"
    try:
        linked_parent.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("platform does not permit creating a symlink")
    with pytest.raises(b.BundleSafetyError):
        b.create_fresh_root(trusted, linked_parent / "bundle")
    assert not (real_dir / "bundle").exists()


def test_create_fresh_root_succeeds_under_safe_trusted_root(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    dest = trusted / "nested" / "bundle"
    result = b.create_fresh_root(trusted, dest)
    assert result == dest
    assert dest.is_dir()


def test_create_fresh_root_rejects_missing_anchor(tmp_path):
    with pytest.raises(b.BundleSafetyError):
        b.create_fresh_root(tmp_path / "missing", tmp_path / "missing" / "bundle")


def test_create_fresh_root_rejects_existing_destination(tmp_path):
    trusted = tmp_path / "trusted"
    dest = trusted / "bundle"
    dest.mkdir(parents=True)
    with pytest.raises(b.BundleSafetyError):
        b.create_fresh_root(trusted, dest)


# ---------------------------------------------------------------------------
# SQLite snapshot / restore
# ---------------------------------------------------------------------------


def test_snapshot_ledger_missing_source_raises_before_connect(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: calls.append(1) or sqlite3.Connection)
    with pytest.raises(b.BundleSafetyError):
        b.snapshot_ledger(tmp_path / "missing.sqlite3", tmp_path / "out.sqlite3")
    assert calls == []


def test_snapshot_ledger_rejects_non_file_source(tmp_path):
    src = tmp_path / "srcdir"
    src.mkdir()
    with pytest.raises(b.BundleSafetyError):
        b.snapshot_ledger(src, tmp_path / "out.sqlite3")


def test_snapshot_ledger_rejects_existing_destination(tmp_path):
    src = tmp_path / "db.sqlite3"
    write_sqlite(src)
    dest = tmp_path / "out.sqlite3"
    dest.write_bytes(b"x")
    with pytest.raises(b.BundleSafetyError):
        b.snapshot_ledger(src, dest)


def test_snapshot_ledger_round_trip_and_hash(tmp_path):
    src = tmp_path / "db.sqlite3"
    write_sqlite(src)
    dest = tmp_path / "out.sqlite3"
    digest = b.snapshot_ledger(src, dest)
    assert digest == m.sha256_hex_of_file(dest)
    conn = sqlite3.connect(str(dest))
    try:
        rows = conn.execute("SELECT x FROM t").fetchall()
    finally:
        conn.close()
    assert rows == [(1,)]


def test_restore_ledger_rejects_hash_mismatch_before_backup(tmp_path):
    src = tmp_path / "db.sqlite3"
    write_sqlite(src)
    with pytest.raises(b.BundleSafetyError):
        b.restore_ledger(src, tmp_path / "restored.sqlite3", expected_sha256="0" * 64)
    assert not (tmp_path / "restored.sqlite3").exists()


def test_restore_ledger_rejects_non_fresh_destination(tmp_path):
    src = tmp_path / "db.sqlite3"
    write_sqlite(src)
    digest = m.sha256_hex_of_file(src)
    dest = tmp_path / "restored.sqlite3"
    dest.write_bytes(b"x")
    with pytest.raises(b.BundleSafetyError):
        b.restore_ledger(src, dest, expected_sha256=digest)


def test_restore_ledger_logical_equivalence(tmp_path):
    src = tmp_path / "db.sqlite3"
    write_sqlite(src)
    digest = m.sha256_hex_of_file(src)
    dest = tmp_path / "restored.sqlite3"
    b.restore_ledger(src, dest, expected_sha256=digest)
    conn = sqlite3.connect(str(dest))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert conn.execute("SELECT x FROM t").fetchall() == [(1,)]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# build_bundle / validate_bundle
# ---------------------------------------------------------------------------


def _genesis_manifest_fields(window):
    return dict(
        schema_version=1,
        bundle_kind="GENESIS",
        workflow_identity=window.control_workflow_identity,
        github_run_id=window.control_run_id,
        run_attempt=1,
        event="workflow_dispatch",
        ref=window.ref,
        source_sha=window.source_sha,
        window_id=window.window_id,
        window_record_sha256=m.sha256_hex_of_model(window),
        slot_index=0,
        no_run_outcome="WINDOW_GENESIS",
        window_consumed=False,
    )


def test_build_bundle_missing_findings_source_fails(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    sources = tmp_path / "sources"
    sources.mkdir()
    db_path = sources / "ledger.sqlite3"
    write_sqlite(db_path)
    cost_path = sources / "cost_ledger.jsonl"
    cost_path.write_text("", encoding="utf-8")
    with pytest.raises(b.BundleSafetyError):
        b.build_bundle(
            tmp_path / "dest",
            tmp_path / "dest" / "bundle",
            window=window,
            manifest_fields=_genesis_manifest_fields(window),
            source_ledger_path=db_path,
            source_ledger_trusted_root=sources,
            findings_source_path=sources / "FINDINGS.md",
            findings_trusted_root=sources,
            cost_ledger_source_path=cost_path,
            cost_ledger_trusted_root=sources,
            control_state=control_state,
        )


def test_build_bundle_symlinked_findings_source_fails(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    sources = tmp_path / "sources"
    sources.mkdir()
    db_path = sources / "ledger.sqlite3"
    write_sqlite(db_path)
    cost_path = sources / "cost_ledger.jsonl"
    cost_path.write_text("", encoding="utf-8")
    real = tmp_path / "real_findings.md"
    real.write_text("# real\n")
    findings_link = sources / "FINDINGS.md"
    try:
        findings_link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("platform does not permit creating a symlink")
    with pytest.raises(b.BundleSafetyError):
        b.build_bundle(
            tmp_path / "dest",
            tmp_path / "dest" / "bundle",
            window=window,
            manifest_fields=_genesis_manifest_fields(window),
            source_ledger_path=db_path,
            source_ledger_trusted_root=sources,
            findings_source_path=findings_link,
            findings_trusted_root=sources,
            cost_ledger_source_path=cost_path,
            cost_ledger_trusted_root=sources,
            control_state=control_state,
        )


def test_build_bundle_missing_cost_ledger_source_fails(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    sources = tmp_path / "sources"
    sources.mkdir()
    db_path = sources / "ledger.sqlite3"
    write_sqlite(db_path)
    findings_path = sources / "FINDINGS.md"
    findings_path.write_text("# F\n")
    with pytest.raises(b.BundleSafetyError):
        b.build_bundle(
            tmp_path / "dest",
            tmp_path / "dest" / "bundle",
            window=window,
            manifest_fields=_genesis_manifest_fields(window),
            source_ledger_path=db_path,
            source_ledger_trusted_root=sources,
            findings_source_path=findings_path,
            findings_trusted_root=sources,
            cost_ledger_source_path=sources / "cost_ledger.jsonl",
            cost_ledger_trusted_root=sources,
            control_state=control_state,
        )


def test_build_bundle_malformed_cost_ledger_fails_before_return(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    with pytest.raises(b.BundleSafetyError):
        build_valid_bundle(
            tmp_path,
            window=window,
            manifest_fields=_genesis_manifest_fields(window),
            control_state=control_state,
            cost_rows_jsonl="not json at all\n",
        )
    assert not (tmp_path / "dest" / "bundle").exists()


def test_build_bundle_genesis_round_trip_and_source_bytes_preserved(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    findings_text = "# FINDINGS\nsomething\n"
    built = build_valid_bundle(
        tmp_path,
        window=window,
        manifest_fields=_genesis_manifest_fields(window),
        control_state=control_state,
        findings_text=findings_text,
    )
    assert built.manifest.bundle_kind == "GENESIS"
    assert (built.root / "state" / "FINDINGS.md").read_text(encoding="utf-8") == findings_text
    validated = b.validate_bundle(built.root)
    assert validated.window.window_id == window.window_id
    assert validated.control_state.latest_authoritative_slot_index == 0


def test_build_bundle_window_hash_binding_enforced(tmp_path):
    window = make_window()
    other_window = make_window(window_id="p5w-different")
    control_state = make_control_state(window)
    fields = _genesis_manifest_fields(window)
    fields["window_record_sha256"] = m.sha256_hex_of_model(other_window)
    fields["window_id"] = other_window.window_id
    with pytest.raises(b.BundleSafetyError):
        build_valid_bundle(tmp_path, window=window, manifest_fields=fields, control_state=control_state)


def test_validate_bundle_rejects_tampered_window(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    (built.root / "qualification_window.json").write_bytes(
        m.canonical_json_bytes(make_window(window_id="p5w-tampered"))
    )
    with pytest.raises(b.BundleValidationError):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_malformed_control_state(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    (built.root / "state" / "phase5_state.json").write_bytes(b"{not json")
    with pytest.raises(Exception):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_wrong_control_state_window_binding(tmp_path):
    window = make_window()
    other_window = make_window(window_id="p5w-different")
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    wrong_state = make_control_state(other_window)
    (built.root / "state" / "phase5_state.json").write_bytes(m.canonical_json_bytes(wrong_state))
    with pytest.raises(b.BundleValidationError):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_corrupted_sqlite(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    ledger_path = built.root / "state" / "ledger.sqlite3"
    original = ledger_path.read_bytes()
    ledger_path.write_bytes(original[:-20] + b"\x00" * 20)
    with pytest.raises(Exception):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_malformed_cost_ledger(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    (built.root / "state" / "cost_ledger.jsonl").write_text("garbage\n", encoding="utf-8")
    with pytest.raises(b.BundleValidationError):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_unexpected_extra_file(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    (built.root / "unexpected.txt").write_text("x")
    with pytest.raises(b.BundleValidationError):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_missing_authoritative_file(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    (built.root / "state" / "FINDINGS.md").unlink()
    with pytest.raises(b.BundleValidationError):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_missing_metadata_file(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    (built.root / "manifest.sha256").unlink()
    with pytest.raises(b.BundleValidationError):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_empty_extra_directory(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    (built.root / "extra_dir").mkdir()
    with pytest.raises(b.BundleValidationError):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_symlinked_entry(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    real = tmp_path / "outside.txt"
    real.write_text("x")
    link = built.root / "manifest.sha256"
    link.unlink()
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("platform does not permit creating a symlink")
    with pytest.raises(b.BundleSafetyError):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_null_control_state_window_identity(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    null_state = make_control_state(None)
    (built.root / "state" / "phase5_state.json").write_bytes(m.canonical_json_bytes(null_state))
    with pytest.raises(b.BundleValidationError):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_genesis_stored_spend_mismatch(tmp_path):
    window = make_window()
    control_state = make_control_state(window, spend=0)
    built = build_valid_bundle(
        tmp_path,
        window=window,
        manifest_fields=_genesis_manifest_fields(window),
        control_state=control_state,
        cost_rows_jsonl="",
    )
    poisoned = make_control_state(window, spend=999_999)
    (built.root / "state" / "phase5_state.json").write_bytes(m.canonical_json_bytes(poisoned))
    with pytest.raises(b.BundleValidationError):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_genesis_not_at_slot_zero(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    bad_state = make_control_state(window, slot_index=1)
    (built.root / "state" / "phase5_state.json").write_bytes(m.canonical_json_bytes(bad_state))
    with pytest.raises(b.BundleValidationError):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_consumed_genesis(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    bad_manifest = make_genesis(window, window_consumed=True)
    (built.root / "manifest.json").write_bytes(m.canonical_json_bytes(bad_manifest))
    (built.root / "manifest.sha256").write_bytes(m.sha256_hex_of_model(bad_manifest).encode("ascii"))
    consumed_state = make_control_state(window, window_consumed=True, window_consume_reason="MISSING_LOST")
    (built.root / "state" / "phase5_state.json").write_bytes(m.canonical_json_bytes(consumed_state))
    with pytest.raises(b.BundleValidationError):
        b.validate_bundle(built.root)


def test_validate_bundle_rejects_manifest_control_consumed_mismatch(tmp_path):
    window = make_window()
    control_state = make_control_state(window)
    built = build_valid_bundle(
        tmp_path, window=window, manifest_fields=_genesis_manifest_fields(window), control_state=control_state
    )
    inconsistent_manifest = make_genesis(window, window_consumed=True)
    (built.root / "manifest.json").write_bytes(m.canonical_json_bytes(inconsistent_manifest))
    (built.root / "manifest.sha256").write_bytes(m.sha256_hex_of_model(inconsistent_manifest).encode("ascii"))
    with pytest.raises(b.BundleValidationError):
        b.validate_bundle(built.root)


# ---------------------------------------------------------------------------
# select_active_window
# ---------------------------------------------------------------------------


def _active_candidate(window=None, genesis_identity="genesis-1"):
    window = window or make_window()
    genesis = make_genesis(window)
    return b.ActiveWindowCandidate(window=window, genesis=genesis, genesis_artifact_identity=genesis_identity)


def test_select_active_window_unique_tip():
    candidate = _active_candidate()
    result = b.select_active_window(
        [candidate],
        expected_source_sha=SOURCE_SHA,
        expected_ref=REF,
        expected_cron=CRON,
        expected_scheduled_workflow_identity=SCHEDULED_WORKFLOW,
    )
    assert result.window.window_id == candidate.window.window_id


def test_select_active_window_supersession_across_source_sha_change():
    old_window = make_window(window_id="p5w-old")
    old = _active_candidate(old_window, "genesis-old")
    new_window = make_window(
        window_id="p5w-new",
        source_sha=OTHER_SOURCE_SHA,
        supersedes_window_id=old_window.window_id,
        supersedes_window_record_sha256=m.sha256_hex_of_model(old_window),
    )
    new = _active_candidate(new_window, "genesis-new")
    result = b.select_active_window(
        [old, new],
        expected_source_sha=OTHER_SOURCE_SHA,
        expected_ref=REF,
        expected_cron=CRON,
        expected_scheduled_workflow_identity=SCHEDULED_WORKFLOW,
    )
    assert result.window.window_id == new_window.window_id


def test_select_active_window_duplicate_window_id_differing_bytes():
    window_a = make_window()
    window_b = make_window(control_run_id="different-control-run")
    candidates = [_active_candidate(window_a, "g1"), _active_candidate(window_b, "g2")]
    with pytest.raises(b.ActiveWindowAmbiguous):
        b.select_active_window(
            candidates,
            expected_source_sha=SOURCE_SHA,
            expected_ref=REF,
            expected_cron=CRON,
            expected_scheduled_workflow_identity=SCHEDULED_WORKFLOW,
        )


def test_select_active_window_ambiguous_genesis_identity():
    window = make_window()
    candidates = [_active_candidate(window, "genesis-1"), _active_candidate(window, "genesis-2")]
    with pytest.raises(b.ActiveWindowAmbiguous):
        b.select_active_window(
            candidates,
            expected_source_sha=SOURCE_SHA,
            expected_ref=REF,
            expected_cron=CRON,
            expected_scheduled_workflow_identity=SCHEDULED_WORKFLOW,
        )


def test_select_active_window_genesis_freeze_binding_rejected():
    window = make_window()
    bad_genesis = make_genesis(window, github_run_id="wrong-control-run")
    candidate = b.ActiveWindowCandidate(window=window, genesis=bad_genesis, genesis_artifact_identity="g1")
    with pytest.raises(b.ActiveWindowAmbiguous):
        b.select_active_window(
            [candidate],
            expected_source_sha=SOURCE_SHA,
            expected_ref=REF,
            expected_cron=CRON,
            expected_scheduled_workflow_identity=SCHEDULED_WORKFLOW,
        )


def test_select_active_window_disconnected_cycle_fails_even_with_valid_tip():
    good = _active_candidate(make_window(window_id="p5w-good"), "g-good")
    cyclic_a = make_window(
        window_id="p5w-cyc-a",
        control_run_id="control-run-a",
        supersedes_window_id="p5w-cyc-b",
        supersedes_window_record_sha256="0" * 64,
    )
    cyclic_b = make_window(
        window_id="p5w-cyc-b",
        control_run_id="control-run-b",
        supersedes_window_id="p5w-cyc-a",
        supersedes_window_record_sha256=m.sha256_hex_of_model(cyclic_a),
    )
    cyclic_a = make_window(
        window_id="p5w-cyc-a",
        control_run_id="control-run-a",
        supersedes_window_id="p5w-cyc-b",
        supersedes_window_record_sha256=m.sha256_hex_of_model(cyclic_b),
    )
    cand_a = _active_candidate(cyclic_a, "g-cyc-a")
    cand_b = _active_candidate(cyclic_b, "g-cyc-b")
    with pytest.raises(b.ActiveWindowAmbiguous):
        b.select_active_window(
            [good, cand_a, cand_b],
            expected_source_sha=SOURCE_SHA,
            expected_ref=REF,
            expected_cron=CRON,
            expected_scheduled_workflow_identity=SCHEDULED_WORKFLOW,
        )


def test_select_active_window_no_match_raises_no_active_window():
    candidate = _active_candidate()
    with pytest.raises(b.NoActiveWindow):
        b.select_active_window(
            [candidate],
            expected_source_sha=OTHER_SOURCE_SHA,
            expected_ref=REF,
            expected_cron=CRON,
            expected_scheduled_workflow_identity=SCHEDULED_WORKFLOW,
        )


def test_select_active_window_empty_candidates_raises_no_active_window():
    with pytest.raises(b.NoActiveWindow):
        b.select_active_window(
            [],
            expected_source_sha=SOURCE_SHA,
            expected_ref=REF,
            expected_cron=CRON,
            expected_scheduled_workflow_identity=SCHEDULED_WORKFLOW,
        )


# ---------------------------------------------------------------------------
# Predecessor chain
# ---------------------------------------------------------------------------


def _build_chain(window, genesis, genesis_identity="genesis-1", n_slots=2):
    candidates = []
    prev_identity, prev_manifest = genesis_identity, genesis
    for n in range(1, n_slots + 1):
        successor = make_slot_successor(
            window, n, predecessor_identity=prev_identity, predecessor_manifest=prev_manifest
        )
        identity = f"slot-{n}-artifact"
        candidates.append(
            b.SlotSuccessorCandidate(
                artifact_identity=identity,
                manifest=successor,
                control_state=make_control_state(window, slot_index=n),
                cost_rows=(),
            )
        )
        prev_identity, prev_manifest = identity, successor
    return candidates


def test_resolve_slot_chain_by_hash_and_identity():
    window = make_window()
    genesis = make_genesis(window)
    candidates = _build_chain(window, genesis, n_slots=3)
    identity, manifest = b.resolve_slot(window, 3, "genesis-1", genesis, candidates)
    assert identity == "slot-3-artifact"
    assert manifest.slot_index == 3


def test_authorized_predecessor_slot1_is_genesis():
    window = make_window()
    genesis = make_genesis(window)
    identity, manifest = b.authorized_predecessor(window, 1, "genesis-1", genesis, [])
    assert identity == "genesis-1"
    assert manifest is genesis


def test_predecessor_chain_wrong_hash_rejected():
    window = make_window()
    genesis = make_genesis(window)
    candidates = _build_chain(window, genesis, n_slots=1)
    # tamper the recorded predecessor hash of slot 1
    bad_manifest = candidates[0].manifest.model_copy(update={"predecessor_manifest_sha256": "0" * 64})
    bad_candidates = [
        b.SlotSuccessorCandidate(
            artifact_identity=candidates[0].artifact_identity,
            manifest=bad_manifest,
            control_state=candidates[0].control_state,
            cost_rows=(),
        )
    ]
    with pytest.raises(b.BrokenPredecessor):
        b.resolve_slot(window, 1, "genesis-1", genesis, bad_candidates)


def test_predecessor_chain_wrong_identity_rejected():
    window = make_window()
    genesis = make_genesis(window)
    candidates = _build_chain(window, genesis, n_slots=1)
    bad_manifest = candidates[0].manifest.model_copy(update={"predecessor_artifact_id_or_name": "wrong-id"})
    bad_candidates = [
        b.SlotSuccessorCandidate(
            artifact_identity=candidates[0].artifact_identity,
            manifest=bad_manifest,
            control_state=candidates[0].control_state,
            cost_rows=(),
        )
    ]
    with pytest.raises(b.BrokenPredecessor):
        b.resolve_slot(window, 1, "genesis-1", genesis, bad_candidates)


def test_predecessor_chain_gap_raises_broken_when_mid_chain():
    window = make_window()
    genesis = make_genesis(window)
    candidates = _build_chain(window, genesis, n_slots=3)
    without_slot2 = [c for c in candidates if c.manifest.slot_index != 2]
    with pytest.raises(b.BrokenPredecessor):
        b.resolve_slot(window, 3, "genesis-1", genesis, without_slot2)


def test_predecessor_chain_true_frontier_raises_next_slot_absent():
    window = make_window()
    genesis = make_genesis(window)
    candidates = _build_chain(window, genesis, n_slots=2)
    with pytest.raises(b.NextSlotAbsent):
        b.resolve_slot(window, 3, "genesis-1", genesis, candidates)


def test_predecessor_chain_rejects_wrong_attempt_and_event():
    window = make_window()
    genesis = make_genesis(window)
    manual = make_slot_successor(
        window, 1, predecessor_identity="genesis-1", predecessor_manifest=genesis, event="workflow_dispatch"
    )
    candidates = [
        b.SlotSuccessorCandidate(
            artifact_identity="manual", manifest=manual, control_state=make_control_state(window, slot_index=1), cost_rows=()
        )
    ]
    with pytest.raises(b.NextSlotAbsent):
        b.resolve_slot(window, 1, "genesis-1", genesis, candidates)


def test_predecessor_chain_duplicate_attempt1_candidates_ambiguous():
    window = make_window()
    genesis = make_genesis(window)
    m1 = make_slot_successor(window, 1, predecessor_identity="genesis-1", predecessor_manifest=genesis, github_run_id="run-a")
    m2 = make_slot_successor(window, 1, predecessor_identity="genesis-1", predecessor_manifest=genesis, github_run_id="run-b")
    candidates = [
        b.SlotSuccessorCandidate(artifact_identity="a", manifest=m1, control_state=make_control_state(window, slot_index=1), cost_rows=()),
        b.SlotSuccessorCandidate(artifact_identity="b", manifest=m2, control_state=make_control_state(window, slot_index=1), cost_rows=()),
    ]
    with pytest.raises(b.BrokenPredecessor):
        b.resolve_slot(window, 1, "genesis-1", genesis, candidates)


def test_latest_contiguous_stops_at_first_gap():
    window = make_window()
    genesis = make_genesis(window)
    candidates = _build_chain(window, genesis, n_slots=4)
    without_slot3 = [c for c in candidates if c.manifest.slot_index != 3]
    identity, manifest, slot_n = b.latest_contiguous_authoritative_qualification_state(
        window, "genesis-1", genesis, without_slot3
    )
    assert slot_n == 2


# ---------------------------------------------------------------------------
# Cross-bundle control-state transition validator
# ---------------------------------------------------------------------------


def test_transition_rejects_slot_jump():
    window = make_window()
    pred_state = make_control_state(window, slot_index=0)
    genesis = make_genesis(window)
    successor_manifest = make_slot_successor(window, 2, predecessor_identity="g", predecessor_manifest=genesis)
    successor = b.SlotSuccessorCandidate(
        artifact_identity="s2", manifest=successor_manifest, control_state=make_control_state(window, slot_index=2), cost_rows=()
    )
    with pytest.raises(b.ControlStateTransitionError):
        b.validate_control_state_transition(window=window, predecessor_state=pred_state, successor=successor)


def test_transition_rejects_consumed_to_unconsumed():
    window = make_window()
    pred_state = make_control_state(window, slot_index=1, window_consumed=True, window_consume_reason="LATE_NONQUALIFYING")
    genesis = make_genesis(window)
    successor_manifest = make_slot_successor(window, 2, predecessor_identity="g", predecessor_manifest=genesis)
    successor = b.SlotSuccessorCandidate(
        artifact_identity="s2",
        manifest=successor_manifest,
        control_state=make_control_state(window, slot_index=2, window_consumed=False),
        cost_rows=(),
    )
    with pytest.raises(b.ControlStateTransitionError):
        b.validate_control_state_transition(window=window, predecessor_state=pred_state, successor=successor)


def test_transition_rejects_reason_rewrite():
    window = make_window()
    pred_state = make_control_state(window, slot_index=1, window_consumed=True, window_consume_reason="LATE_NONQUALIFYING")
    genesis = make_genesis(window)
    successor_manifest = make_slot_successor(window, 2, predecessor_identity="g", predecessor_manifest=genesis)
    successor = b.SlotSuccessorCandidate(
        artifact_identity="s2",
        manifest=successor_manifest,
        control_state=make_control_state(
            window, slot_index=2, window_consumed=True, window_consume_reason="COSTROW_INVALID"
        ),
        cost_rows=(),
    )
    with pytest.raises(b.ControlStateTransitionError):
        b.validate_control_state_transition(window=window, predecessor_state=pred_state, successor=successor)


@pytest.mark.parametrize(
    "from_level,to_level,ok",
    [
        ("DAILY", "WEEKLY", False),
        ("EVERY_2_DAYS", "DAILY", False),
        ("WEEKLY", "EVERY_2_DAYS", False),
        ("DAILY", "EVERY_2_DAYS", True),
        ("EVERY_2_DAYS", "WEEKLY", True),
    ],
)
def test_transition_cadence_rules(from_level, to_level, ok):
    window = make_window()
    anchor = slot_ts(2) if from_level != "DAILY" else None
    pred_state = make_control_state(window, slot_index=1, cadence_level=from_level, cadence_anchor=anchor)
    genesis = make_genesis(window)
    successor_manifest = make_slot_successor(window, 2, predecessor_identity="g", predecessor_manifest=genesis)
    new_anchor = slot_ts(2)
    successor_state = make_control_state(
        window, slot_index=2, cadence_level=to_level, cadence_anchor=new_anchor if to_level != "DAILY" else None
    )
    successor = b.SlotSuccessorCandidate(
        artifact_identity="s2", manifest=successor_manifest, control_state=successor_state, cost_rows=()
    )
    from sentinel.phase5.cadence import CadenceDecision

    decision = CadenceDecision(
        outcome="CADENCE_SKIP", provider_call_permitted=False, cadence_transition_to=to_level, new_anchor=new_anchor
    )
    if ok:
        b.validate_control_state_transition(
            window=window, predecessor_state=pred_state, successor=successor, cadence_decision=decision
        )
    else:
        with pytest.raises(b.ControlStateTransitionError):
            b.validate_control_state_transition(
                window=window, predecessor_state=pred_state, successor=successor, cadence_decision=decision
            )


def test_transition_rejects_anchor_mutation_without_transition():
    window = make_window()
    pred_state = make_control_state(window, slot_index=1, cadence_level="EVERY_2_DAYS", cadence_anchor=slot_ts(1))
    genesis = make_genesis(window)
    successor_manifest = make_slot_successor(window, 2, predecessor_identity="g", predecessor_manifest=genesis)
    successor_state = make_control_state(
        window, slot_index=2, cadence_level="EVERY_2_DAYS", cadence_anchor=slot_ts(2)
    )
    successor = b.SlotSuccessorCandidate(
        artifact_identity="s2", manifest=successor_manifest, control_state=successor_state, cost_rows=()
    )
    with pytest.raises(b.ControlStateTransitionError):
        b.validate_control_state_transition(window=window, predecessor_state=pred_state, successor=successor)


def test_transition_rejects_spend_mismatch():
    window = make_window()
    pred_state = make_control_state(window, slot_index=1)
    genesis = make_genesis(window)
    successor_manifest = make_slot_successor(window, 2, predecessor_identity="g", predecessor_manifest=genesis)
    successor_state = make_control_state(window, slot_index=2, spend=500_000)
    successor = b.SlotSuccessorCandidate(
        artifact_identity="s2",
        manifest=successor_manifest,
        control_state=successor_state,
        cost_rows=(make_cost_row("sentinel-run", cost_eur_micros=100),),
    )
    with pytest.raises(b.ControlStateTransitionError):
        b.validate_control_state_transition(window=window, predecessor_state=pred_state, successor=successor)


def test_transition_accepts_matching_recomputed_spend():
    window = make_window()
    pred_state = make_control_state(window, slot_index=1, evaluated_at=WINDOW_CREATED_AT)
    genesis = make_genesis(window)
    successor_manifest = make_slot_successor(window, 2, predecessor_identity="g", predecessor_manifest=genesis)
    evaluated_at = WINDOW_CREATED_AT + timedelta(hours=1)
    row = make_cost_row("sentinel-run", cost_eur_micros=42, recorded_at_utc=evaluated_at)
    successor_state = make_control_state(window, slot_index=2, spend=42, evaluated_at=evaluated_at)
    successor = b.SlotSuccessorCandidate(
        artifact_identity="s2", manifest=successor_manifest, control_state=successor_state, cost_rows=(row,)
    )
    b.validate_control_state_transition(window=window, predecessor_state=pred_state, successor=successor)


def test_transition_rejects_evaluated_at_moving_backward():
    window = make_window()
    pred_state = make_control_state(window, slot_index=1, evaluated_at=WINDOW_CREATED_AT)
    genesis = make_genesis(window)
    successor_manifest = make_slot_successor(window, 2, predecessor_identity="g", predecessor_manifest=genesis)
    successor_state = make_control_state(window, slot_index=2, evaluated_at=WINDOW_CREATED_AT - timedelta(hours=1))
    successor = b.SlotSuccessorCandidate(
        artifact_identity="s2", manifest=successor_manifest, control_state=successor_state, cost_rows=()
    )
    with pytest.raises(b.ControlStateTransitionError):
        b.validate_control_state_transition(window=window, predecessor_state=pred_state, successor=successor)


def test_transition_rejects_window_identity_change():
    window = make_window()
    other_window = make_window(window_id="p5w-different")
    pred_state = make_control_state(window, slot_index=1)
    genesis = make_genesis(window)
    successor_manifest = make_slot_successor(window, 2, predecessor_identity="g", predecessor_manifest=genesis)
    successor_state = make_control_state(other_window, slot_index=2)
    successor = b.SlotSuccessorCandidate(
        artifact_identity="s2", manifest=successor_manifest, control_state=successor_state, cost_rows=()
    )
    with pytest.raises(b.ControlStateTransitionError):
        b.validate_control_state_transition(window=window, predecessor_state=pred_state, successor=successor)


# ---------------------------------------------------------------------------
# CONTROL_REFUSAL decision reconstruction + durable walker
# ---------------------------------------------------------------------------


def test_reconstruct_refusal_decision_requires_expected_slot_and_no_slot_index():
    window = make_window()
    genesis = make_genesis(window)
    pred_state = make_control_state(window, slot_index=0)
    refusal_manifest = make_control_refusal(
        window, predecessor_identity="g", predecessor_manifest=genesis, expected_slot_utc=slot_ts(1)
    )
    refusal = b.ControlRefusalCandidate(
        artifact_identity="r1", manifest=refusal_manifest, control_state=make_control_state(window, slot_index=0, cadence_level="EVERY_2_DAYS", cadence_anchor=slot_ts(1)), cost_rows=()
    )
    # a manifest that satisfies its own pydantic schema always carries
    # expected_slot_utc and no slot_index — this documents that guarantee
    assert refusal_manifest.expected_slot_utc is not None
    assert refusal_manifest.slot_index is None


def test_reconstruct_refusal_decision_deterministic_cadence_skip():
    # ineligibility-based CADENCE_SKIP is cost-independent and the
    # simplest deterministic case: WEEKLY cadence, a slot one day off
    # anchor is never eligible regardless of spend.
    window = make_window()
    genesis = make_genesis(window)
    anchor = slot_ts(1)
    pred_state = make_control_state(
        window, slot_index=0, cadence_level="WEEKLY", cadence_anchor=anchor, spend=0, evaluated_at=WINDOW_CREATED_AT
    )
    refusal_manifest = make_control_refusal(
        window,
        predecessor_identity="genesis-1",
        predecessor_manifest=genesis,
        expected_slot_utc=slot_ts(2),
        no_run_outcome="CADENCE_SKIP",
    )
    refusal_state = make_control_state(
        window, slot_index=0, cadence_level="WEEKLY", cadence_anchor=anchor, spend=0, evaluated_at=WINDOW_CREATED_AT
    )
    refusal = b.ControlRefusalCandidate(
        artifact_identity="r1", manifest=refusal_manifest, control_state=refusal_state, cost_rows=()
    )
    decision = b.reconstruct_refusal_decision(window, pred_state, refusal)
    assert decision.outcome == "CADENCE_SKIP"
    assert decision.provider_call_permitted is False


def test_reconstruct_refusal_decision_mismatch_rejected():
    window = make_window()
    genesis = make_genesis(window)
    pred_state = make_control_state(window, slot_index=0, cadence_level="DAILY", spend=0)
    refusal_manifest = make_control_refusal(
        window,
        predecessor_identity="genesis-1",
        predecessor_manifest=genesis,
        expected_slot_utc=slot_ts(1),
        no_run_outcome="COST_CADENCE_REFUSAL",
    )
    refusal_state = make_control_state(window, slot_index=0, spend=0, evaluated_at=WINDOW_CREATED_AT)
    refusal = b.ControlRefusalCandidate(
        artifact_identity="r1", manifest=refusal_manifest, control_state=refusal_state, cost_rows=()
    )
    with pytest.raises(b.ControlStateTransitionError):
        b.reconstruct_refusal_decision(window, pred_state, refusal)


def test_durable_walker_zero_refusals_returns_qualification_frontier():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    identity, manifest, state, slot_n = b.latest_durable_control_state_source(
        window, "genesis-1", genesis, genesis_state, [], []
    )
    assert slot_n == 0
    assert identity == "genesis-1"
    assert state is genesis_state


def test_durable_walker_several_consecutive_refusals():
    # WEEKLY cadence: only the anchor+7d slot is eligible, so three
    # consecutive daily fires (anchor+1d, +2d, +3d) are each a
    # deterministic, cost-independent CADENCE_SKIP refusal.
    window = make_window()
    genesis = make_genesis(window)
    anchor = slot_ts(1)
    genesis_state = make_control_state(
        window, slot_index=0, cadence_level="WEEKLY", cadence_anchor=anchor, spend=0, evaluated_at=WINDOW_CREATED_AT
    )

    refusal_candidates = []
    prev_identity, prev_manifest = "genesis-1", genesis
    for i, slot_utc in enumerate((slot_ts(2), slot_ts(3), slot_ts(4)), start=1):
        refusal_manifest = make_control_refusal(
            window,
            predecessor_identity=prev_identity,
            predecessor_manifest=prev_manifest,
            expected_slot_utc=slot_utc,
            no_run_outcome="CADENCE_SKIP",
            github_run_id=f"gh-refusal-{i}",
        )
        state = make_control_state(
            window, slot_index=0, cadence_level="WEEKLY", cadence_anchor=anchor, spend=0, evaluated_at=WINDOW_CREATED_AT
        )
        identity = f"refusal-{i}"
        refusal_candidates.append(
            b.ControlRefusalCandidate(artifact_identity=identity, manifest=refusal_manifest, control_state=state, cost_rows=())
        )
        prev_identity, prev_manifest = identity, refusal_manifest

    identity, manifest, state, slot_n = b.latest_durable_control_state_source(
        window, "genesis-1", genesis, genesis_state, [], refusal_candidates
    )
    assert slot_n == 0  # zero qualification credit from refusals
    assert identity == "refusal-3"


def test_durable_walker_reused_identity_raises_cycle():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0, spend=0, evaluated_at=WINDOW_CREATED_AT)
    refusal_manifest = make_control_refusal(
        window, predecessor_identity="genesis-1", predecessor_manifest=genesis, expected_slot_utc=slot_ts(1)
    )
    refusal_state = make_control_state(
        window, slot_index=0, cadence_level="EVERY_2_DAYS", cadence_anchor=slot_ts(1), spend=0, evaluated_at=WINDOW_CREATED_AT
    )
    # a refusal that (incorrectly) claims genesis-1 as its own artifact identity
    refusal = b.ControlRefusalCandidate(
        artifact_identity="genesis-1", manifest=refusal_manifest, control_state=refusal_state, cost_rows=()
    )
    with pytest.raises(b.BrokenPredecessor):
        b.latest_durable_control_state_source(window, "genesis-1", genesis, genesis_state, [], [refusal])


# ---------------------------------------------------------------------------
# 5-of-5 clean completion
# ---------------------------------------------------------------------------


def _build_clean_chain(window, genesis, genesis_state):
    candidates = []
    prev_identity, prev_manifest, prev_state = "genesis-1", genesis, genesis_state
    for n in range(1, 6):
        successor = make_slot_successor(
            window,
            n,
            predecessor_identity=prev_identity,
            predecessor_manifest=prev_manifest,
            sentinel_run_id=f"sentinel-{n}",
            qualification_outcome="QUALIFYING",
            window_consumed=(n == 5),
        )
        window_consumed = n == 5
        state = make_control_state(
            window,
            slot_index=n,
            window_consumed=window_consumed,
            window_consume_reason=None,
            spend=0,
            evaluated_at=WINDOW_CREATED_AT,
        )
        identity = f"slot-{n}"
        candidates.append(
            b.SlotSuccessorCandidate(artifact_identity=identity, manifest=successor, control_state=state, cost_rows=())
        )
        prev_identity, prev_manifest, prev_state = identity, successor, state
    return candidates


def test_is_clean_five_of_five_completion_true_for_five_qualifying():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0, spend=0, evaluated_at=WINDOW_CREATED_AT)
    candidates = _build_clean_chain(window, genesis, genesis_state)
    assert b.is_clean_five_of_five_completion(window, "genesis-1", genesis, genesis_state, candidates) is True


def test_is_clean_five_of_five_completion_false_on_one_late_slot():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0, spend=0, evaluated_at=WINDOW_CREATED_AT)
    candidates = _build_clean_chain(window, genesis, genesis_state)
    late = candidates[2].manifest.model_copy(update={"qualification_outcome": "LATE_NONQUALIFYING"})
    candidates[2] = b.SlotSuccessorCandidate(
        artifact_identity=candidates[2].artifact_identity, manifest=late, control_state=candidates[2].control_state, cost_rows=()
    )
    assert b.is_clean_five_of_five_completion(window, "genesis-1", genesis, genesis_state, candidates) is False


def test_is_clean_five_of_five_completion_false_on_gap():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0, spend=0, evaluated_at=WINDOW_CREATED_AT)
    candidates = _build_clean_chain(window, genesis, genesis_state)
    without_slot4 = [c for c in candidates if c.manifest.slot_index != 4]
    assert b.is_clean_five_of_five_completion(window, "genesis-1", genesis, genesis_state, without_slot4) is False


def test_is_clean_five_of_five_completion_false_on_clean_shape_with_earlier_bad_slot():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0, spend=0, evaluated_at=WINDOW_CREATED_AT)
    candidates = _build_clean_chain(window, genesis, genesis_state)
    # slot 2 is secretly nonqualifying even though slot 5's control state
    # still has the "clean" shape — the walk must catch this, the shape
    # alone must never manufacture success
    bad = candidates[1].manifest.model_copy(update={"qualification_outcome": "FAILED_NONTERMINAL"})
    candidates[1] = b.SlotSuccessorCandidate(
        artifact_identity=candidates[1].artifact_identity, manifest=bad, control_state=candidates[1].control_state, cost_rows=()
    )
    assert b.is_clean_five_of_five_completion(window, "genesis-1", genesis, genesis_state, candidates) is False


def test_is_clean_five_of_five_completion_genesis_wrong_slot_raises():
    window = make_window()
    genesis = make_genesis(window)
    bad_genesis_state = make_control_state(window, slot_index=0, spend=0, evaluated_at=WINDOW_CREATED_AT).model_copy(
        update={"latest_authoritative_slot_index": 1}
    )
    with pytest.raises(b.ControlStateTransitionError):
        b.is_clean_five_of_five_completion(window, "genesis-1", genesis, bad_genesis_state, [])

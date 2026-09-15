"""Tests for sentinel/phase5/receipts.py, the historical establishment
script's pure helpers, and the pre-push append-only guard (ADR-0012
Amendment A rule A1; dispatch q77-p5d-repair-stage1-implement-a).

Model-free and network-blocked (tests/conftest.py ``block_network``).
No one-shot marker is created or consumed anywhere here.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from sentinel.phase5 import receipts as rc

REPO_ROOT = Path(__file__).resolve().parent.parent
RECEIPTS_MODULE_PATH = REPO_ROOT / "sentinel" / "phase5" / "receipts.py"
SCRIPT_PATH = REPO_ROOT / "scripts" / "establish_phase5_historical_receipts.py"
PRE_PUSH_PATH = REPO_ROOT / ".githooks" / "pre-push"
COMMITTED_REGISTRY = REPO_ROOT / "artifacts" / "phase5_receipt_registry.jsonl"

NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)
SHA_A = "a" * 40
HEX64_1 = "1" * 64
HEX64_2 = "2" * 64


def fixed_clock():
    return NOW


def marker_fields(purpose="P5C_WIF_PROBE", run_id="100", **overrides) -> dict:
    fields = dict(
        schema_version=1,
        receipt_class="ONESHOT_MARKER_CONSUMED",
        purpose=purpose,
        github_run_id=run_id,
        run_attempt=1,
        source_sha=SHA_A,
        artifact_name=f"sentinel-p5-oneshot-{'p5c-wif-probe' if purpose == 'P5C_WIF_PROBE' else 'p5d-official-sonnet-gate'}-r{run_id}",
        artifact_id=1001,
        payload_filename="marker.json",
        payload_sha256=HEX64_1,
        disposition="CONSUMED",
        replacement_of_run_id=None,
        owner_ruling_id=None,
        governance_ref=None,
    )
    fields.update(overrides)
    return fields


def probe_fields(run_id="100", **overrides) -> dict:
    fields = dict(
        schema_version=1,
        receipt_class="PROBE_EVIDENCE",
        purpose="P5C_WIF_PROBE",
        github_run_id=run_id,
        run_attempt=1,
        source_sha=SHA_A,
        artifact_name=f"sentinel-p5-probe-evidence-r{run_id}-a1",
        artifact_id=1002,
        payload_filename="probe-evidence.json",
        payload_sha256=HEX64_2,
        disposition="CAPABILITY_PASS",
        replacement_of_run_id=None,
        owner_ruling_id=None,
        governance_ref=None,
    )
    fields.update(overrides)
    return fields


def gate_fields(run_id="200", **overrides) -> dict:
    fields = dict(
        schema_version=1,
        receipt_class="GATE_EVIDENCE",
        purpose="P5D_OFFICIAL_SONNET_GATE",
        github_run_id=run_id,
        run_attempt=1,
        source_sha=SHA_A,
        artifact_name=f"sentinel-p5-gate-evidence-r{run_id}-a1",
        artifact_id=1003,
        payload_filename="phase5_official_gate.json",
        payload_sha256=HEX64_2,
        disposition="GREEN",
        replacement_of_run_id=None,
        owner_ruling_id=None,
        governance_ref=None,
    )
    fields.update(overrides)
    return fields


def disposition_fields(run_id="200", **overrides) -> dict:
    fields = dict(
        schema_version=1,
        receipt_class="EXECUTION_DISPOSITION",
        purpose="P5D_OFFICIAL_SONNET_GATE",
        github_run_id=run_id,
        run_attempt=1,
        source_sha=SHA_A,
        artifact_name=None,
        artifact_id=None,
        payload_filename=None,
        payload_sha256=None,
        disposition="EXECUTION_INVALID / NO_QUALITY_RESULT",
        replacement_of_run_id=None,
        owner_ruling_id="ruling-x",
        governance_ref="record-y",
    )
    fields.update(overrides)
    return fields


def build(fields: dict, prev: str = rc.ZERO_SHA256) -> rc.Phase5Receipt:
    return rc.Phase5Receipt(**fields, prev_receipt_sha256=prev, recorded_at_utc=NOW)


def make_registry(tmp_path: Path, *field_sets: dict) -> Path:
    path = tmp_path / "registry.jsonl"
    for index, fields in enumerate(field_sets):
        rc.append_receipt(path, allow_create=(index == 0), clock=fixed_clock, **fields)
    return path


# ======================================================================
# 1-4: record construction
# ======================================================================


def test_1_valid_marker_receipt_constructs():
    receipt = build(marker_fields())
    assert receipt.semantic_key == ("ONESHOT_MARKER_CONSUMED", "P5C_WIF_PROBE", "100", 1)
    assert receipt.disposition == "CONSUMED"


def test_2_valid_p5c_evidence_receipt_constructs():
    receipt = build(probe_fields())
    assert receipt.receipt_class == "PROBE_EVIDENCE"
    assert receipt.disposition == "CAPABILITY_PASS"


def test_3_execution_invalid_disposition_has_null_artifact_fields():
    receipt = build(disposition_fields())
    assert (receipt.artifact_name, receipt.artifact_id, receipt.payload_filename, receipt.payload_sha256) == (
        None, None, None, None,
    )
    assert receipt.governance_ref == "record-y" and receipt.owner_ruling_id == "ruling-x"


@pytest.mark.parametrize(
    "override",
    [
        {"artifact_name": "sentinel-p5-gate-evidence-r200-a1"},
        {"artifact_id": 5},
        {"payload_filename": "phase5_official_gate.json"},
        {"payload_sha256": HEX64_1},
    ],
)
def test_4_artifact_bearing_execution_disposition_rejected(override):
    with pytest.raises(ValidationError):
        build(disposition_fields(**override))


@pytest.mark.parametrize("missing", ["governance_ref", "owner_ruling_id"])
def test_4b_execution_disposition_requires_governance_ref_and_ruling(missing):
    with pytest.raises(ValidationError):
        build(disposition_fields(**{missing: None}))


def test_4c_execution_disposition_rejects_any_other_disposition():
    with pytest.raises(ValidationError):
        build(disposition_fields(disposition="HONEST_FAIL"))


@pytest.mark.parametrize(
    "fields",
    [
        marker_fields(disposition="CAPABILITY_PASS"),
        marker_fields(artifact_name="sentinel-p5-oneshot-p5c-wif-probe-r999"),
        marker_fields(payload_filename="probe-evidence.json"),
        marker_fields(artifact_id=None),
        probe_fields(purpose="P5D_OFFICIAL_SONNET_GATE", artifact_name="sentinel-p5-probe-evidence-r100-a1"),
        probe_fields(disposition="GREEN"),
        probe_fields(payload_filename="marker.json"),
        gate_fields(purpose="P5C_WIF_PROBE"),
        gate_fields(disposition="CAPABILITY_PASS"),
        gate_fields(artifact_name="sentinel-p5-gate-evidence-r200-a2"),
        gate_fields(payload_filename="marker.json"),
        marker_fields(source_sha="not-hex"),
        marker_fields(github_run_id="gh-run-1"),
        marker_fields(run_attempt=0),
    ],
)
def test_cross_field_rules_reject_every_wrong_shape(fields):
    with pytest.raises(ValidationError):
        build(fields)


def test_gate_evidence_valid_shapes_construct():
    for disposition in ("GREEN", "HONEST_FAIL", "INFRASTRUCTURE_FAILURE"):
        assert build(gate_fields(disposition=disposition)).disposition == disposition


def test_receipt_is_frozen_and_forbids_extra():
    receipt = build(marker_fields())
    with pytest.raises(ValidationError):
        receipt.disposition = "GREEN"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        rc.Phase5Receipt(**marker_fields(), prev_receipt_sha256=rc.ZERO_SHA256, recorded_at_utc=NOW, extra="x")


# ======================================================================
# 5, 15, 16: consumption truth is durable and independent
# ======================================================================


def test_5_consumed_marker_remains_consumed_with_no_external_artifact(tmp_path):
    path = make_registry(tmp_path, marker_fields())
    receipts = rc.load_registry(path)
    # No artifact client, no evidence receipt, nothing external: the durable
    # receipt alone is the consumption truth.
    assert rc.is_purpose_consumed(receipts, "P5C_WIF_PROBE") is True
    assert rc.consumed_purposes(receipts) == frozenset({"P5C_WIF_PROBE"})


def test_15_marker_consumption_stands_independently_of_any_evidence_receipt(tmp_path):
    path = make_registry(tmp_path, marker_fields(purpose="P5D_OFFICIAL_SONNET_GATE", run_id="200"))
    receipts = rc.load_registry(path)
    assert rc.is_purpose_consumed(receipts, "P5D_OFFICIAL_SONNET_GATE") is True
    assert rc.evidence_receipts(receipts, "GATE_EVIDENCE") == ()
    assert rc.authoritative_quality_receipt(receipts, "P5D_OFFICIAL_SONNET_GATE") is None
    assert rc.is_purpose_consumed(receipts, "P5C_WIF_PROBE") is False


def test_16_consumed_marker_plus_execution_invalid_remains_consumed_and_unresolved(tmp_path):
    path = make_registry(
        tmp_path,
        marker_fields(purpose="P5D_OFFICIAL_SONNET_GATE", run_id="200"),
        disposition_fields(run_id="200"),
    )
    receipts = rc.load_registry(path)
    assert rc.is_purpose_consumed(receipts, "P5D_OFFICIAL_SONNET_GATE") is True
    assert rc.authoritative_quality_receipt(receipts, "P5D_OFFICIAL_SONNET_GATE") is None
    dispositions = rc.execution_dispositions(receipts, "200")
    assert len(dispositions) == 1
    assert dispositions[0].disposition == "EXECUTION_INVALID / NO_QUALITY_RESULT"
    assert rc.execution_dispositions(receipts, "999") == ()


def test_authoritative_quality_receipt_selects_exactly_one_and_fails_on_ambiguity(tmp_path):
    path = make_registry(tmp_path, gate_fields(run_id="200"))
    receipts = rc.load_registry(path)
    assert rc.authoritative_quality_receipt(receipts, "P5D_OFFICIAL_SONNET_GATE").github_run_id == "200"
    # INFRASTRUCTURE_FAILURE is never authoritative quality
    path2 = make_registry(tmp_path / "b", gate_fields(run_id="201", disposition="INFRASTRUCTURE_FAILURE"))
    assert rc.authoritative_quality_receipt(rc.load_registry(path2), "P5D_OFFICIAL_SONNET_GATE") is None
    ambiguous = rc.load_registry(
        make_registry(tmp_path / "c", gate_fields(run_id="300"), gate_fields(run_id="301", disposition="HONEST_FAIL"))
    )
    with pytest.raises(rc.AmbiguousQualityHistory):
        rc.authoritative_quality_receipt(ambiguous, "P5D_OFFICIAL_SONNET_GATE")


# ======================================================================
# 6-12, 14: chain validation fails closed on every corruption
# ======================================================================


def test_6_complete_registry_chain_validates(tmp_path):
    path = make_registry(tmp_path, marker_fields(), probe_fields(), marker_fields("P5D_OFFICIAL_SONNET_GATE", "200"))
    receipts = rc.load_registry(path)
    assert len(receipts) == 3
    assert receipts[0].prev_receipt_sha256 == rc.ZERO_SHA256
    assert receipts[1].prev_receipt_sha256 == rc.receipt_sha256(receipts[0])
    assert receipts[2].prev_receipt_sha256 == rc.receipt_sha256(receipts[1])
    assert rc.registry_head_sha256(receipts) == rc.receipt_sha256(receipts[2])
    assert path.read_bytes() == b"".join(rc.receipt_line_bytes(r) for r in receipts)


def _lines(path: Path) -> list[bytes]:
    return path.read_bytes().split(b"\n")[:-1]


def test_7_rewritten_established_line_breaks_validation(tmp_path):
    path = make_registry(tmp_path, marker_fields(), probe_fields())
    lines = _lines(path)
    lines[0] = lines[0].replace(b'"artifact_id":1001', b'"artifact_id":1009')
    path.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)


def test_8_deleted_intermediate_line_breaks_validation(tmp_path):
    path = make_registry(tmp_path, marker_fields(), probe_fields(), marker_fields("P5D_OFFICIAL_SONNET_GATE", "200"))
    lines = _lines(path)
    del lines[1]
    path.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)


def test_9_blank_line_rejected(tmp_path):
    path = make_registry(tmp_path, marker_fields(), probe_fields())
    lines = _lines(path)
    lines.insert(1, b"")
    path.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)
    path.write_bytes(b"\n".join(_lines(path)) + b"\n\n")
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)


def test_10_truncated_trailing_line_rejected(tmp_path):
    path = make_registry(tmp_path, marker_fields(), probe_fields())
    raw = path.read_bytes()
    path.write_bytes(raw[:-1])  # drop the final LF
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)
    path.write_bytes(raw[:-20])  # a torn final line
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)


def test_11_extra_field_rejected(tmp_path):
    path = make_registry(tmp_path, marker_fields())
    line = _lines(path)[0]
    tampered = line[:-1] + b',"note":"x"}'
    path.write_bytes(tampered + b"\n")
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)


def test_12_invalid_hashes_rejected(tmp_path):
    path = make_registry(tmp_path, marker_fields(), probe_fields())
    lines = _lines(path)
    # wrong prev hash on line 2
    bad_prev = re.sub(rb'"prev_receipt_sha256":"[0-9a-f]{64}"', b'"prev_receipt_sha256":"' + b"f" * 64 + b'"', lines[1])
    path.write_bytes(lines[0] + b"\n" + bad_prev + b"\n")
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)
    # first receipt must chain from all zeroes
    bad_first = lines[0].replace(b'"prev_receipt_sha256":"' + b"0" * 64, b'"prev_receipt_sha256":"' + b"1" * 64)
    path.write_bytes(bad_first + b"\n")
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)
    # malformed hex payload hash fails schema validation
    bad_payload = lines[0].replace(HEX64_1.encode(), b"Z" * 64)
    path.write_bytes(bad_payload + b"\n")
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)


def test_malformed_json_non_canonical_and_crlf_rejected(tmp_path):
    path = make_registry(tmp_path, marker_fields())
    line = _lines(path)[0]
    path.write_bytes(b"{not json\n")
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)
    path.write_bytes(line.replace(b",", b", ") + b"\n")  # same content, non-canonical bytes
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)
    path.write_bytes(line + b"\r\n")  # consistent CRLF (a Windows autocrlf checkout) reads as the same chain
    assert rc.load_registry(path) == (build(marker_fields()),)
    path.write_bytes(line.replace(b",", b"\r,", 1) + b"\n")  # a bare CR is never valid
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)
    path.write_bytes(b"")
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)


def test_13_second_receipt_with_same_semantic_key_refused_even_if_artifact_differs(tmp_path):
    path = make_registry(tmp_path, marker_fields())
    before = path.read_bytes()
    with pytest.raises(rc.DuplicateSemanticEvent):
        rc.append_receipt(path, clock=fixed_clock, **marker_fields(artifact_id=4242, payload_sha256=HEX64_2))
    with pytest.raises(rc.DuplicateSemanticEvent):
        rc.append_receipt(path, clock=fixed_clock, **marker_fields())  # byte-identical content too
    assert path.read_bytes() == before  # nothing was written


def test_14_ambiguous_duplicate_semantic_history_fails_closed_on_load(tmp_path):
    first = build(marker_fields())
    second = build(marker_fields(artifact_id=4242, payload_sha256=HEX64_2), prev=rc.receipt_sha256(first))
    path = tmp_path / "registry.jsonl"
    path.write_bytes(rc.receipt_line_bytes(first) + rc.receipt_line_bytes(second))  # chain is valid
    with pytest.raises(rc.DuplicateSemanticEvent):
        rc.load_registry(path)


def test_append_verifies_reloaded_head_and_refuses_append_established_fields(tmp_path):
    path = tmp_path / "registry.jsonl"
    receipt = rc.append_receipt(path, allow_create=True, clock=fixed_clock, **marker_fields())
    assert rc.load_registry(path) == (receipt,)
    assert receipt.recorded_at_utc == NOW
    with pytest.raises(rc.ReceiptRegistryError):
        rc.append_receipt(path, clock=fixed_clock, **probe_fields(), prev_receipt_sha256=rc.ZERO_SHA256)
    with pytest.raises(rc.ReceiptRegistryError):
        rc.append_receipt(path, clock=fixed_clock, **probe_fields(), recorded_at_utc=NOW)
    with pytest.raises(ValueError):
        rc.append_receipt(path, clock=lambda: datetime(2026, 9, 15, 12, 0, 0), **probe_fields())  # naive clock
    assert rc.load_registry(path) == (receipt,)  # nothing was written by any refused call


def test_append_refuses_when_existing_registry_is_corrupt(tmp_path):
    path = make_registry(tmp_path, marker_fields())
    raw = path.read_bytes()
    path.write_bytes(raw[:-1])
    with pytest.raises(rc.ReceiptRegistryError):
        rc.append_receipt(path, clock=fixed_clock, **probe_fields())
    with pytest.raises(rc.ReceiptRegistryError):
        rc.append_receipt(path, allow_create=True, clock=fixed_clock, **probe_fields())
    assert path.read_bytes() == raw[:-1]


# ======================================================================
# Missing registry fails closed (owner correction r2-1)
# ======================================================================


def test_missing_registry_fails_closed_by_default(tmp_path):
    missing = tmp_path / "registry.jsonl"
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(missing)
    with pytest.raises(rc.ReceiptRegistryError):
        rc.append_receipt(missing, clock=fixed_clock, **marker_fields())
    assert not missing.exists()


def test_explicit_initial_creation_works_and_only_once(tmp_path):
    missing = tmp_path / "registry.jsonl"
    assert rc.load_registry(missing, allow_missing=True) == ()
    first = rc.append_receipt(missing, allow_create=True, clock=fixed_clock, **marker_fields())
    assert first.prev_receipt_sha256 == rc.ZERO_SHA256
    assert rc.load_registry(missing) == (first,)
    # allow_create on an EXISTING registry still fully validates and refuses duplicates
    with pytest.raises(rc.DuplicateSemanticEvent):
        rc.append_receipt(missing, allow_create=True, clock=fixed_clock, **marker_fields())
    second = rc.append_receipt(missing, allow_create=True, clock=fixed_clock, **probe_fields())
    assert second.prev_receipt_sha256 == rc.receipt_sha256(first)


def test_after_establishment_deletion_is_never_empty_history(tmp_path):
    path = make_registry(tmp_path, marker_fields(), probe_fields())
    path.unlink()
    with pytest.raises(rc.ReceiptRegistryError):
        rc.load_registry(path)
    with pytest.raises(rc.ReceiptRegistryError):
        rc.append_receipt(path, clock=fixed_clock, **marker_fields("P5D_OFFICIAL_SONNET_GATE", "200"))
    assert not path.exists()
    # Consumption can never be computed from an absent registry: the only
    # way to get a receipts tuple is a successful strict load.
    with pytest.raises(rc.ReceiptRegistryError):
        rc.is_purpose_consumed(rc.load_registry(path), "P5C_WIF_PROBE")


# ======================================================================
# 17: no repair API; 20: no provider/model path reachable
# ======================================================================


def test_17_module_exposes_no_update_delete_truncate_replace_repair_api():
    banned = ("update", "delete", "truncate", "replace", "rewrite", "repair", "remove", "reset")
    public = [name for name in dir(rc) if not name.startswith("_")]
    offenders = [name for name in public if any(word in name.lower() for word in banned)]
    assert offenders == []
    assert "append_receipt" in public and "load_registry" in public


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(("." * node.level) + node.module)
    return modules


def test_20_no_provider_or_model_path_reachable_from_receipts_module():
    modules = _import_roots(RECEIPTS_MODULE_PATH)
    stdlib = set(sys.stdlib_module_names)
    allowed_relative = {".artifact_names", ".models"}
    for module in modules:
        root = module.split(".")[0]
        assert not root.startswith("agents") and root not in {"claude_agent_sdk", "anthropic", "scripts"}, module
        if module.startswith("."):
            assert module in allowed_relative, module
        else:
            assert root in stdlib or root == "pydantic", module


def test_20b_establishment_script_reaches_no_provider_oidc_or_common_seam():
    modules = _import_roots(SCRIPT_PATH)
    banned_roots = {"agents", "claude_agent_sdk", "anthropic"}
    for module in modules:
        assert module.split(".")[0] not in banned_roots, module
    assert "scripts._phase5_common" not in modules
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "acquire_oidc" not in text and "query_fn" not in text
    assert "print(token" not in text and 'f"{token' not in text


# ======================================================================
# Establishment script: pure fact-bundle helpers (no network)
# ======================================================================


def _load_script():
    spec = importlib.util.spec_from_file_location("establish_phase5_historical_receipts", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses + postponed annotations need the module registered
    spec.loader.exec_module(module)
    return module


def _four_facts(module) -> list[dict]:
    return [
        module._fact(receipt_class="ONESHOT_MARKER_CONSUMED", purpose="P5C_WIF_PROBE", github_run_id="1", run_attempt=1,
                     source_sha=SHA_A, artifact_name="n1", artifact_id=1, payload_filename="marker.json",
                     payload_sha256=HEX64_1, disposition="CONSUMED"),
        module._fact(receipt_class="PROBE_EVIDENCE", purpose="P5C_WIF_PROBE", github_run_id="1", run_attempt=1,
                     source_sha=SHA_A, artifact_name="n2", artifact_id=2, payload_filename="probe-evidence.json",
                     payload_sha256=HEX64_2, disposition="CAPABILITY_PASS"),
        module._fact(receipt_class="ONESHOT_MARKER_CONSUMED", purpose="P5D_OFFICIAL_SONNET_GATE", github_run_id="2",
                     run_attempt=1, source_sha=SHA_A, artifact_name="n3", artifact_id=3, payload_filename="marker.json",
                     payload_sha256=HEX64_1, disposition="CONSUMED"),
        module._fact(receipt_class="EXECUTION_DISPOSITION", purpose="P5D_OFFICIAL_SONNET_GATE", github_run_id="2",
                     run_attempt=1, source_sha=SHA_A, disposition="EXECUTION_INVALID / NO_QUALITY_RESULT",
                     governance_ref="g", owner_ruling_id="o"),
    ]


def test_fact_bundle_is_deterministic_and_carries_only_the_twelve_source_fields():
    module = _load_script()
    assert set(module.FACT_KEYS) == {
        "receipt_class", "purpose", "github_run_id", "run_attempt", "source_sha", "artifact_name", "artifact_id",
        "payload_filename", "payload_sha256", "disposition", "governance_ref", "owner_ruling_id",
    }
    facts = _four_facts(module)
    bundle = module.build_fact_bundle(facts)
    assert module.facts_sha256(bundle) == module.facts_sha256(module.build_fact_bundle(_four_facts(module)))
    assert re.fullmatch(r"[0-9a-f]{64}", module.facts_sha256(bundle))
    for forbidden in (b"recorded_at_utc", b"prev_receipt_sha256", b"receipt_sha256", b"head"):
        assert forbidden not in bundle
    with pytest.raises(module.EstablishmentRefused):
        module.build_fact_bundle(facts[:3])
    with pytest.raises(module.EstablishmentRefused):
        module.build_fact_bundle([dict(f, recorded_at_utc="x") for f in facts])
    with pytest.raises(module.EstablishmentRefused):
        module._fact(receipt_class="PROBE_EVIDENCE", prev_receipt_sha256=rc.ZERO_SHA256)


def test_script_historical_constants_are_the_dispatch_values():
    module = _load_script()
    assert (module.P5C_RUN.run_id, module.P5C_RUN.run_attempt, module.P5C_RUN.source_sha) == (
        "32783229864", 1, "f5b2ae6e393252594efa5b48e1f86a1f2296f797",
    )
    assert (module.P5C_MARKER.artifact_id, module.P5C_MARKER.artifact_name) == (
        9540505807, "sentinel-p5-oneshot-p5c-wif-probe-r32783229864",
    )
    assert (module.P5C_PROBE_EVIDENCE.artifact_id, module.P5C_PROBE_EVIDENCE.artifact_name) == (
        9540511349, "sentinel-p5-probe-evidence-r32783229864-a1",
    )
    assert (module.P5D_ORIGINAL_RUN.run_id, module.P5D_ORIGINAL_RUN.source_sha) == (
        "32880880053", "eef88a289cf465ad352ee223221d5497465469b3",
    )
    assert (module.P5D_ORIGINAL_MARKER.artifact_id, module.P5D_ORIGINAL_MARKER.artifact_name) == (
        9575720463, "sentinel-p5-oneshot-p5d-official-sonnet-gate-r32880880053",
    )
    assert module.P5D_ORIGINAL_INCIDENT_RECORD_REF == "q77-p5d-invalid-run-record-a"
    assert module.P5D_ORIGINAL_OWNER_RULING_ID == "q77-p5d-replacement-owner-ruling-a"
    # The names the script pins equal the canonical naming contract.
    from sentinel.phase5 import artifact_names as an

    assert an.oneshot_marker_name("P5C_WIF_PROBE", "32783229864") == module.P5C_MARKER.artifact_name
    assert an.probe_evidence_name("32783229864", 1) == module.P5C_PROBE_EVIDENCE.artifact_name
    assert an.oneshot_marker_name("P5D_OFFICIAL_SONNET_GATE", "32880880053") == module.P5D_ORIGINAL_MARKER.artifact_name


def test_script_real_run_refuses_before_any_network_or_write_on_bad_arguments(tmp_path, monkeypatch):
    module = _load_script()
    registry = tmp_path / "registry.jsonl"
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert module.main(["--dry-run", "--expect-facts-sha256", "a" * 64, "--registry", str(registry)]) == 2
    assert module.main(["--registry", str(registry)]) == 2  # real run without expected hash
    assert module.main(["--registry", str(registry), "--expect-facts-sha256", "nothex"]) == 2
    registry.write_bytes(b"")
    assert module.main(["--registry", str(registry), "--expect-facts-sha256", "a" * 64]) == 2  # already exists
    registry.unlink()
    assert module.main(["--dry-run", "--registry", str(registry)]) == 2  # no token: refused before any GET
    assert not registry.exists()


def test_script_real_run_stops_on_facts_hash_mismatch_before_any_append(tmp_path, monkeypatch):
    module = _load_script()
    registry = tmp_path / "registry.jsonl"
    facts = _four_facts(module)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-never-printed")
    monkeypatch.setattr(module, "verify_all_sources", lambda client, work_root, ledger: (facts, {"n1": "2026-11-22T22:09:06Z"}))
    wrong = "b" * 64
    assert module.main(["--registry", str(registry), "--expect-facts-sha256", wrong]) == 1
    assert not registry.exists()
    right = module.facts_sha256(module.build_fact_bundle(facts))
    # With the right hash the four appends proceed (facts here use short
    # synthetic names, so the receipt schema itself refuses them: that
    # refusal must surface as a STOP, never a partial registry).
    assert module.main(["--registry", str(registry), "--expect-facts-sha256", right]) == 1
    assert not registry.exists()


# ======================================================================
# 18-19: the committed historical registry (pins captured AFTER the real append)
# ======================================================================

COMMITTED_EXPECTED_EVENTS = (
    ("ONESHOT_MARKER_CONSUMED", "P5C_WIF_PROBE", "32783229864", 1, "CONSUMED", 9540505807),
    ("PROBE_EVIDENCE", "P5C_WIF_PROBE", "32783229864", 1, "CAPABILITY_PASS", 9540511349),
    ("ONESHOT_MARKER_CONSUMED", "P5D_OFFICIAL_SONNET_GATE", "32880880053", 1, "CONSUMED", 9575720463),
    ("EXECUTION_DISPOSITION", "P5D_OFFICIAL_SONNET_GATE", "32880880053", 1, "EXECUTION_INVALID / NO_QUALITY_RESULT", None),
)
COMMITTED_EXPECTED_COUNT = 4
COMMITTED_EXPECTED_HEAD_SHA256 = "9f060888ea963305a512f534873fe056e8f7fe0c08d05137d26c6d95aeccfc39"


def test_18_committed_registry_loads_as_exactly_the_four_historical_events():
    receipts = rc.load_registry(COMMITTED_REGISTRY)
    observed = tuple(
        (r.receipt_class, r.purpose, r.github_run_id, r.run_attempt, r.disposition, r.artifact_id) for r in receipts
    )
    assert observed == COMMITTED_EXPECTED_EVENTS
    assert receipts[0].source_sha == receipts[1].source_sha == "f5b2ae6e393252594efa5b48e1f86a1f2296f797"
    assert receipts[2].source_sha == receipts[3].source_sha == "eef88a289cf465ad352ee223221d5497465469b3"
    assert receipts[3].governance_ref == "q77-p5d-invalid-run-record-a"
    assert receipts[3].owner_ruling_id == "q77-p5d-replacement-owner-ruling-a"
    assert rc.is_purpose_consumed(receipts, "P5C_WIF_PROBE")
    assert rc.is_purpose_consumed(receipts, "P5D_OFFICIAL_SONNET_GATE")
    assert rc.authoritative_quality_receipt(receipts, "P5D_OFFICIAL_SONNET_GATE") is None
    assert rc.evidence_receipts(receipts, "GATE_EVIDENCE") == ()


def test_19_committed_registry_head_hash_and_count_match_established_pins():
    receipts = rc.load_registry(COMMITTED_REGISTRY)
    assert len(receipts) == COMMITTED_EXPECTED_COUNT
    assert rc.registry_head_sha256(receipts) == COMMITTED_EXPECTED_HEAD_SHA256
    assert COMMITTED_REGISTRY.read_bytes().count(b"\n") == COMMITTED_EXPECTED_COUNT


# ======================================================================
# Pre-push append-only guard (implementation E)
# ======================================================================

GUARD_PREDICATE = re.compile(r"^-[^-]")


def test_pre_push_hook_carries_the_append_only_guard_in_the_required_position():
    text = PRE_PUSH_PATH.read_text(encoding="utf-8")
    guard_start = text.index('REGISTRY="artifacts/phase5_receipt_registry.jsonl"')
    guard_block = text[guard_start:]
    assert 'if git diff "$base" HEAD -- "$REGISTRY" | grep -E \'^-[^-]\' >/dev/null; then' in guard_block
    assert "PRE-PUSH BLOCK: $REGISTRY has removed or rewritten established receipt content (append-only)." in guard_block
    assert "exit 1" in guard_block
    # after the leak-grep's $base selection and before the final PASS echo
    assert text.index('base="origin/main"') < guard_start
    assert text.index("PRE-PUSH BLOCK: possible secret detected") < guard_start
    assert guard_start < text.rindex('echo "pre-push: Tier 0 + leak-grep PASS"')
    registry_lines = [line for line in guard_block.splitlines() if "$REGISTRY" in line and "git diff" in line]
    assert registry_lines and all("--diff-filter" not in line for line in registry_lines)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def _removed_content_lines(cwd: Path, base: str, path: str) -> list[str]:
    diff = _git(cwd, "diff", base, "HEAD", "--", path)
    return [line for line in diff.splitlines() if GUARD_PREDICATE.match(line)]


def test_pre_push_guard_predicate_allows_creation_and_append_but_blocks_rewrite_and_deletion(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "config", "core.hooksPath", ".git/no-hooks")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    registry = repo / "artifacts" / "phase5_receipt_registry.jsonl"

    # initial creation: allowed
    rc.append_receipt(registry, allow_create=True, clock=fixed_clock, **marker_fields())
    _git(repo, "add", "artifacts/phase5_receipt_registry.jsonl")
    _git(repo, "commit", "-q", "-m", "create")
    assert _removed_content_lines(repo, base, "artifacts/phase5_receipt_registry.jsonl") == []
    created = _git(repo, "rev-parse", "HEAD").strip()

    # pure append: allowed
    rc.append_receipt(registry, clock=fixed_clock, **probe_fields())
    _git(repo, "add", "artifacts/phase5_receipt_registry.jsonl")
    _git(repo, "commit", "-q", "-m", "append")
    assert _removed_content_lines(repo, created, "artifacts/phase5_receipt_registry.jsonl") == []
    appended = _git(repo, "rev-parse", "HEAD").strip()

    # rewrite of an established line: blocked
    lines = registry.read_bytes().split(b"\n")[:-1]
    lines[0] = lines[0].replace(b'"artifact_id":1001', b'"artifact_id":1009')
    registry.write_bytes(b"\n".join(lines) + b"\n")
    _git(repo, "add", "artifacts/phase5_receipt_registry.jsonl")
    _git(repo, "commit", "-q", "-m", "rewrite")
    assert _removed_content_lines(repo, appended, "artifacts/phase5_receipt_registry.jsonl") != []
    _git(repo, "reset", "-q", "--hard", appended)

    # whole-file deletion: blocked
    _git(repo, "rm", "-q", "artifacts/phase5_receipt_registry.jsonl")
    _git(repo, "commit", "-q", "-m", "delete")
    assert _removed_content_lines(repo, appended, "artifacts/phase5_receipt_registry.jsonl") != []


def test_guard_predicate_ignores_diff_headers_but_catches_removed_content():
    assert not GUARD_PREDICATE.match("--- a/artifacts/phase5_receipt_registry.jsonl")
    assert not GUARD_PREDICATE.match("+{...}")
    assert GUARD_PREDICATE.match('-{"artifact_id":1001}')
    assert not GUARD_PREDICATE.match("-- not a content line")

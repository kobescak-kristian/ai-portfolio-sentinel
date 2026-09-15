"""Tests for sentinel/phase5/replacement.py, the OneShotMarker
replacement-identity fields in sentinel/phase5/models.py, the durable
consumption helper sentinel.phase5.oneshot.assert_purpose_not_yet_consumed_durably,
and sentinel.phase5.evidence_records.validate_replacement_provenance
(ADR-0012 / Amendment A repair, Stage 2A; dispatch
q77-p5d-repair-stage2-implement-a).

New coverage lands here rather than in tests/test_phase5_oneshot.py or
a new tests/test_phase5_models.py -- both are outside this dispatch's
frozen 19-path write set.

Model-free and network-blocked (tests/conftest.py's ``block_network``
fixture). No one-shot marker is created or consumed anywhere here, and
this dispatch arms nothing: the replacement purpose is structural only.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schemas import CostRow
from sentinel.phase5 import models as m
from sentinel.phase5 import oneshot as o
from sentinel.phase5 import receipts as rc
from sentinel.phase5 import replacement as repl
from sentinel.phase5.artifact_names import gate_evidence_name, oneshot_marker_name
from sentinel.phase5.evidence_records import (
    GateEvidenceRecord,
    ObservedSignal,
    TerminationCause,
    validate_replacement_provenance,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_REGISTRY = REPO_ROOT / "artifacts" / "phase5_receipt_registry.jsonl"

NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)
SHA_A = "a" * 40
WIF = "github-actions-wif-federation"


def fixed_clock():
    return NOW


# ======================================================================
# 1-4: frozen identity constants and cross-module cross-pins
# (anti-tautology precedent, matching
# tests/test_phase5_gate_runner.py::test_gate_cost_literals_cross_pinned_against_sonnet_official_gate)
# ======================================================================


def test_1_frozen_identity_constants_match_the_dispatch_values():
    assert repl.ORIGINAL_PURPOSE == "P5D_OFFICIAL_SONNET_GATE"
    assert repl.REPLACEMENT_PURPOSE == "P5D_REPLACEMENT_SONNET_GATE"
    assert repl.REPLACEMENT_OF_RUN_ID == "32880880053"
    assert repl.OWNER_RULING_ID == "q77-p5d-replacement-owner-ruling-a"
    assert repl.ORIGINAL_INCIDENT_RECORD_REF == "q77-p5d-invalid-run-record-a"
    assert repl.MAX_REPLACEMENTS == 1


def test_2_receipts_module_duplicated_constants_cross_pin_against_replacement():
    assert rc._REPLACEMENT_PURPOSE == repl.REPLACEMENT_PURPOSE
    assert rc._REPLACEMENT_OF_RUN_ID == repl.REPLACEMENT_OF_RUN_ID
    assert rc._REPLACEMENT_OWNER_RULING_ID == repl.OWNER_RULING_ID


def test_3_evidence_records_duplicated_constants_cross_pin_against_replacement():
    from sentinel.phase5 import evidence_records as er

    assert er._REPLACEMENT_OF_RUN_ID == repl.REPLACEMENT_OF_RUN_ID
    assert er._REPLACEMENT_OWNER_RULING_ID == repl.OWNER_RULING_ID
    assert er._REPLACEMENT_MARKER_PURPOSE == repl.REPLACEMENT_PURPOSE
    assert er._REPLACEMENT_WORKFLOW_IDENTITY == ".github/workflows/sentinel-official-gate.yml"


def test_4_artifact_name_slug_matches_replacement_purpose():
    from sentinel.phase5 import artifact_names as an

    name = an.oneshot_marker_name(repl.REPLACEMENT_PURPOSE, "42")
    assert name == "sentinel-p5-oneshot-p5d-replacement-sonnet-gate-r42"
    parsed = an.parse_artifact_name(name)
    assert parsed.kind == "ONESHOT_MARKER"
    assert parsed.purpose == repl.REPLACEMENT_PURPOSE


# ======================================================================
# 5-8: OneShotMarker replacement-identity field pairing (models.py)
# ======================================================================


def _marker_fields(purpose="P5C_WIF_PROBE", **overrides) -> dict:
    fields = dict(
        schema_version=1, purpose=purpose, created_at_utc=NOW, workflow_identity="wf",
        github_run_id="1", run_attempt=1, event="workflow_dispatch", source_sha=SHA_A,
        replacement_of_run_id=None, owner_ruling_id=None,
    )
    fields.update(overrides)
    return fields


def test_5_replacement_purpose_marker_requires_both_provenance_fields():
    marker = m.OneShotMarker(**_marker_fields(
        purpose=repl.REPLACEMENT_PURPOSE, replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID,
        owner_ruling_id=repl.OWNER_RULING_ID,
    ))
    assert marker.purpose == repl.REPLACEMENT_PURPOSE


@pytest.mark.parametrize("missing", ["replacement_of_run_id", "owner_ruling_id"])
def test_6_replacement_purpose_marker_missing_one_field_rejected(missing):
    fields = _marker_fields(
        purpose=repl.REPLACEMENT_PURPOSE, replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID,
        owner_ruling_id=repl.OWNER_RULING_ID,
    )
    fields[missing] = None
    with pytest.raises(ValidationError):
        m.OneShotMarker(**fields)


@pytest.mark.parametrize("purpose", ["P5C_WIF_PROBE", "P5D_OFFICIAL_SONNET_GATE"])
def test_7_non_replacement_purpose_marker_rejects_either_field_populated(purpose):
    with pytest.raises(ValidationError):
        m.OneShotMarker(**_marker_fields(purpose=purpose, replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID))
    with pytest.raises(ValidationError):
        m.OneShotMarker(**_marker_fields(purpose=purpose, owner_ruling_id=repl.OWNER_RULING_ID))


def test_8_non_replacement_purpose_marker_with_both_null_constructs_fine():
    marker = m.OneShotMarker(**_marker_fields(purpose="P5C_WIF_PROBE"))
    assert marker.replacement_of_run_id is None
    assert marker.owner_ruling_id is None


def test_8b_purpose_literal_accepts_all_three_known_purposes():
    for purpose in ("P5C_WIF_PROBE", "P5D_OFFICIAL_SONNET_GATE", repl.REPLACEMENT_PURPOSE):
        extra = (
            dict(replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID, owner_ruling_id=repl.OWNER_RULING_ID)
            if purpose == repl.REPLACEMENT_PURPOSE
            else {}
        )
        marker = m.OneShotMarker(**_marker_fields(purpose=purpose, **extra))
        assert marker.purpose == purpose


# ======================================================================
# 9: durable one-shot consumption helper (oneshot.py)
# ======================================================================


def _write_registry(tmp_path: Path, *field_sets: dict) -> Path:
    path = tmp_path / "registry.jsonl"
    for index, fields in enumerate(field_sets):
        rc.append_receipt(path, allow_create=(index == 0), clock=fixed_clock, **fields)
    return path


def _marker_receipt_fields(purpose="P5C_WIF_PROBE", run_id="100", **overrides) -> dict:
    fields = dict(
        schema_version=1, receipt_class="ONESHOT_MARKER_CONSUMED", purpose=purpose,
        github_run_id=run_id, run_attempt=1, source_sha=SHA_A,
        artifact_name=oneshot_marker_name(purpose, run_id), artifact_id=1001,
        payload_filename="marker.json", payload_sha256="1" * 64, disposition="CONSUMED",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    fields.update(overrides)
    return fields


def test_9_durable_consumption_blocks_even_with_zero_live_markers(tmp_path):
    path = _write_registry(tmp_path, _marker_receipt_fields())
    receipts = rc.load_registry(path)
    with pytest.raises(o.OneShotAlreadyConsumed):
        o.assert_purpose_not_yet_consumed_durably("P5C_WIF_PROBE", receipts, [])


def test_9b_durable_check_falls_through_to_live_check_when_not_durably_consumed(tmp_path):
    """No durable receipt for this purpose: falls through to the
    existing live-marker check, which still catches a live-only
    marker."""
    path = _write_registry(tmp_path, _marker_receipt_fields(purpose="P5D_OFFICIAL_SONNET_GATE", run_id="200"))
    receipts = rc.load_registry(path)
    # not durably consumed for P5C_WIF_PROBE
    o.assert_purpose_not_yet_consumed_durably("P5C_WIF_PROBE", receipts, [])
    # but a live marker for it still blocks via the fallthrough
    live_marker = m.OneShotMarker(**_marker_fields(purpose="P5C_WIF_PROBE"))
    with pytest.raises(o.OneShotAlreadyConsumed):
        o.assert_purpose_not_yet_consumed_durably("P5C_WIF_PROBE", receipts, [live_marker])


def test_9c_committed_registry_blocks_both_historical_purposes_durably():
    """The real committed Stage-1 registry (byte-unchanged by this
    dispatch) already shows both P5C_WIF_PROBE and
    P5D_OFFICIAL_SONNET_GATE durably consumed -- proving Stage 2A's
    wiring correctly refuses both today, with zero live markers."""
    receipts = rc.load_registry(COMMITTED_REGISTRY)
    with pytest.raises(o.OneShotAlreadyConsumed):
        o.assert_purpose_not_yet_consumed_durably("P5C_WIF_PROBE", receipts, [])
    with pytest.raises(o.OneShotAlreadyConsumed):
        o.assert_purpose_not_yet_consumed_durably("P5D_OFFICIAL_SONNET_GATE", receipts, [])


def test_9d_committed_registry_never_shows_replacement_purpose_consumed():
    """The replacement purpose has zero receipts in the real committed
    registry -- it is structural, not armed."""
    receipts = rc.load_registry(COMMITTED_REGISTRY)
    o.assert_purpose_not_yet_consumed_durably(repl.REPLACEMENT_PURPOSE, receipts, [])
    assert rc.is_purpose_consumed(receipts, repl.REPLACEMENT_PURPOSE) is False


# ======================================================================
# 10-20: replacement_history_verdict
# ======================================================================


def _valid_history_registry(tmp_path: Path, *, run_id: str = "200") -> "tuple":
    path = tmp_path / "registry.jsonl"
    rc.append_receipt(
        path, allow_create=True, clock=fixed_clock,
        **_marker_receipt_fields(purpose=repl.ORIGINAL_PURPOSE, run_id=repl.REPLACEMENT_OF_RUN_ID),
    )
    rc.append_receipt(
        path, clock=fixed_clock, schema_version=1, receipt_class="EXECUTION_DISPOSITION",
        purpose=repl.ORIGINAL_PURPOSE, github_run_id=repl.REPLACEMENT_OF_RUN_ID, run_attempt=1,
        source_sha=SHA_A, artifact_name=None, artifact_id=None, payload_filename=None,
        payload_sha256=None, disposition="EXECUTION_INVALID / NO_QUALITY_RESULT",
        replacement_of_run_id=None, owner_ruling_id=repl.OWNER_RULING_ID,
        governance_ref=repl.ORIGINAL_INCIDENT_RECORD_REF,
    )
    return rc.load_registry(path)


def test_10_verdict_permits_exactly_the_valid_original_only_history(tmp_path):
    receipts = _valid_history_registry(tmp_path)
    verdict = repl.replacement_history_verdict(receipts, [], repl.REPLACEMENT_PURPOSE)
    assert verdict.permits_one_replacement is True


def test_11_verdict_refuses_for_non_replacement_purpose():
    verdict = repl.replacement_history_verdict((), [], repl.ORIGINAL_PURPOSE)
    assert verdict.permits_one_replacement is False
    assert "not the frozen replacement purpose" in verdict.reason


def test_12_verdict_refuses_when_no_original_marker_receipt_exists():
    verdict = repl.replacement_history_verdict((), [], repl.REPLACEMENT_PURPOSE)
    assert verdict.permits_one_replacement is False
    assert "no durable ONESHOT_MARKER_CONSUMED receipt" in verdict.reason


def test_13_verdict_refuses_when_original_marker_exists_for_a_different_run(tmp_path):
    path = tmp_path / "registry.jsonl"
    rc.append_receipt(
        path, allow_create=True, clock=fixed_clock,
        **_marker_receipt_fields(purpose=repl.ORIGINAL_PURPOSE, run_id="999"),
    )
    receipts = rc.load_registry(path)
    verdict = repl.replacement_history_verdict(receipts, [], repl.REPLACEMENT_PURPOSE)
    assert verdict.permits_one_replacement is False


def test_14_verdict_refuses_when_no_execution_disposition_receipt_exists(tmp_path):
    path = tmp_path / "registry.jsonl"
    rc.append_receipt(
        path, allow_create=True, clock=fixed_clock,
        **_marker_receipt_fields(purpose=repl.ORIGINAL_PURPOSE, run_id=repl.REPLACEMENT_OF_RUN_ID),
    )
    receipts = rc.load_registry(path)
    verdict = repl.replacement_history_verdict(receipts, [], repl.REPLACEMENT_PURPOSE)
    assert verdict.permits_one_replacement is False
    assert "EXECUTION_DISPOSITION" in verdict.reason


def test_15_verdict_refuses_when_execution_disposition_has_wrong_owner_ruling(tmp_path):
    path = tmp_path / "registry.jsonl"
    rc.append_receipt(
        path, allow_create=True, clock=fixed_clock,
        **_marker_receipt_fields(purpose=repl.ORIGINAL_PURPOSE, run_id=repl.REPLACEMENT_OF_RUN_ID),
    )
    rc.append_receipt(
        path, clock=fixed_clock, schema_version=1, receipt_class="EXECUTION_DISPOSITION",
        purpose=repl.ORIGINAL_PURPOSE, github_run_id=repl.REPLACEMENT_OF_RUN_ID, run_attempt=1,
        source_sha=SHA_A, artifact_name=None, artifact_id=None, payload_filename=None,
        payload_sha256=None, disposition="EXECUTION_INVALID / NO_QUALITY_RESULT",
        replacement_of_run_id=None, owner_ruling_id="some-other-ruling",
        governance_ref="ref",
    )
    receipts = rc.load_registry(path)
    verdict = repl.replacement_history_verdict(receipts, [], repl.REPLACEMENT_PURPOSE)
    assert verdict.permits_one_replacement is False


def test_16_verdict_refuses_when_replacement_already_consumed(tmp_path):
    receipts = _valid_history_registry(tmp_path)
    path = tmp_path / "registry.jsonl"
    rc.append_receipt(
        path, clock=fixed_clock,
        **_marker_receipt_fields(
            purpose=repl.REPLACEMENT_PURPOSE, run_id="500",
            replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID, owner_ruling_id=repl.OWNER_RULING_ID,
        ),
    )
    receipts = rc.load_registry(path)
    verdict = repl.replacement_history_verdict(receipts, [], repl.REPLACEMENT_PURPOSE)
    assert verdict.permits_one_replacement is False
    assert "already established" in verdict.reason or "already consumed" in verdict.reason


def test_17_verdict_refuses_when_any_replacement_receipt_exists_regardless_of_class(tmp_path):
    """Even a non-marker receipt (e.g. a PUBLICATION_FAILED execution
    disposition) for the replacement purpose blocks a fresh verdict --
    the single replacement is decided, whatever its outcome."""
    path = tmp_path / "registry.jsonl"
    rc.append_receipt(
        path, allow_create=True, clock=fixed_clock,
        **_marker_receipt_fields(purpose=repl.ORIGINAL_PURPOSE, run_id=repl.REPLACEMENT_OF_RUN_ID),
    )
    rc.append_receipt(
        path, clock=fixed_clock, schema_version=1, receipt_class="EXECUTION_DISPOSITION",
        purpose=repl.ORIGINAL_PURPOSE, github_run_id=repl.REPLACEMENT_OF_RUN_ID, run_attempt=1,
        source_sha=SHA_A, artifact_name=None, artifact_id=None, payload_filename=None,
        payload_sha256=None, disposition="EXECUTION_INVALID / NO_QUALITY_RESULT",
        replacement_of_run_id=None, owner_ruling_id=repl.OWNER_RULING_ID,
        governance_ref=repl.ORIGINAL_INCIDENT_RECORD_REF,
    )
    rc.append_receipt(
        path, clock=fixed_clock, schema_version=1, receipt_class="EXECUTION_DISPOSITION",
        purpose=repl.REPLACEMENT_PURPOSE, github_run_id="500", run_attempt=1, source_sha=SHA_A,
        artifact_name=None, artifact_id=None, payload_filename=None, payload_sha256=None,
        disposition="PUBLICATION_FAILED", replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID,
        owner_ruling_id=repl.OWNER_RULING_ID, governance_ref="some-record-ref",
    )
    receipts = rc.load_registry(path)
    verdict = repl.replacement_history_verdict(receipts, [], repl.REPLACEMENT_PURPOSE)
    assert verdict.permits_one_replacement is False


def test_18_verdict_refuses_when_original_purpose_has_gate_evidence():
    """Structurally unconstructible via receipts.py's own schema (a
    GATE_EVIDENCE receipt can only ever carry the replacement purpose),
    so this proves the impossibility at the registry-construction
    layer rather than merely trusting the verdict function's own
    defensive check."""
    with pytest.raises(ValidationError):
        rc.Phase5Receipt(
            schema_version=1, receipt_class="GATE_EVIDENCE", purpose=repl.ORIGINAL_PURPOSE,
            github_run_id=repl.REPLACEMENT_OF_RUN_ID, run_attempt=1, source_sha=SHA_A,
            artifact_name=gate_evidence_name(repl.REPLACEMENT_OF_RUN_ID, 1), artifact_id=1,
            payload_filename="phase5_official_gate.json", payload_sha256="1" * 64,
            disposition="GREEN", replacement_of_run_id=None, owner_ruling_id=None,
            governance_ref=None, prev_receipt_sha256=rc.ZERO_SHA256, recorded_at_utc=NOW,
        )


def test_19_verdict_refuses_when_live_replacement_marker_exists_even_without_durable_receipt(tmp_path):
    receipts = _valid_history_registry(tmp_path)
    live_marker = m.OneShotMarker(**_marker_fields(
        purpose=repl.REPLACEMENT_PURPOSE, replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID,
        owner_ruling_id=repl.OWNER_RULING_ID,
    ))
    verdict = repl.replacement_history_verdict(receipts, [live_marker], repl.REPLACEMENT_PURPOSE)
    assert verdict.permits_one_replacement is False
    assert "live one-shot marker" in verdict.reason


def test_20_committed_registry_permits_replacement_and_refuses_original():
    """The real committed Stage-1 registry (byte-unchanged) currently
    permits exactly one future replacement and refuses any verdict for
    the original purpose -- proving Stage 2A's structural wiring
    against the actual authoritative history, not a synthetic fixture."""
    receipts = rc.load_registry(COMMITTED_REGISTRY)
    verdict = repl.replacement_history_verdict(receipts, [], repl.REPLACEMENT_PURPOSE)
    assert verdict.permits_one_replacement is True
    verdict2 = repl.replacement_history_verdict(receipts, [], repl.ORIGINAL_PURPOSE)
    assert verdict2.permits_one_replacement is False


# ======================================================================
# 21-32: validate_replacement_provenance (evidence_records.py)
# ======================================================================


def _cost_row(run_id="run1") -> CostRow:
    return CostRow(
        schema_version=1, run_id=run_id, recorded_at_utc=datetime.now(timezone.utc),
        run_kind="dev", model="claude-sonnet-5", input_tokens=1, output_tokens=1, cost_eur_micros=2_000,
    )


def _replacement_gate_kwargs(**overrides) -> dict:
    base = dict(
        schema_version=1, workflow_identity=".github/workflows/sentinel-official-gate.yml",
        github_run_id="200", run_attempt=1, event="workflow_dispatch", ref="refs/heads/main",
        source_sha=SHA_A, created_at_utc=datetime.now(timezone.utc), steps=(),
        expected_source_sha=SHA_A, model="claude-sonnet-5", profile_name="sonnet-official-gate",
        run_ids=("run1", "run2"), scoring={"emitted": 1}, thresholds={},
        invariant_results={"ok": True}, execution_validity={"valid": True},
        miss_patterns=(), failed_checks=(), cost_rows=(_cost_row(),),
        accounted_total_eur_micros=2_000, disposition="GREEN", auth_mode=WIF,
        replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID, owner_ruling_id=repl.OWNER_RULING_ID,
        marker_purpose=repl.REPLACEMENT_PURPOSE, envelope_id="env-1", envelope_version="v1",
    )
    base.update(overrides)
    return base


def test_21_valid_green_passes():
    record = GateEvidenceRecord(**_replacement_gate_kwargs())
    validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_22_valid_honest_fail_passes():
    record = GateEvidenceRecord(**_replacement_gate_kwargs(
        disposition="HONEST_FAIL", failed_checks=("pooled_recall: 1/2 -> FAIL",),
    ))
    validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_23_infrastructure_failure_with_objective_cause_passes():
    for cause in ("SESSION_DEADLINE", "INVOCATION_STALL_DEADLINE", "WATCHDOG", "PRE_PROVIDER_FAILURE", "RUNNER_EXCEPTION"):
        record = GateEvidenceRecord(**_replacement_gate_kwargs(
            disposition="INFRASTRUCTURE_FAILURE", run_ids=(), scoring={}, execution_validity={},
            miss_patterns=(), failed_checks=(), cost_rows=(), accounted_total_eur_micros=0,
            auth_mode=None, termination_source=cause,
        ))
        validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_24_infrastructure_failure_with_no_termination_source_refused():
    record = GateEvidenceRecord(**_replacement_gate_kwargs(
        disposition="INFRASTRUCTURE_FAILURE", run_ids=(), scoring={}, execution_validity={},
        miss_patterns=(), failed_checks=(), cost_rows=(), accounted_total_eur_micros=0, auth_mode=None,
    ))
    with pytest.raises(ValueError, match="positively established termination_source"):
        validate_replacement_provenance(record, expected_source_sha=SHA_A)


@pytest.mark.parametrize("signal", ["SIGINT", "SIGTERM", "UNKNOWN_EXTERNAL_TERMINATION"])
def test_25_infrastructure_failure_with_only_a_signal_never_suffices(signal):
    """Correction (owner-ruled, 2026-09-15, ADR-0012 Amendment A2 rule
    6): a raw observed signal alone -- with no positively established
    termination_source -- must never satisfy an INFRASTRUCTURE_FAILURE
    replacement claim, whatever the signal."""
    record = GateEvidenceRecord(**_replacement_gate_kwargs(
        disposition="INFRASTRUCTURE_FAILURE", run_ids=(), scoring={}, execution_validity={},
        miss_patterns=(), failed_checks=(), cost_rows=(), accounted_total_eur_micros=0, auth_mode=None,
        observed_signals=(signal,),
    ))
    with pytest.raises(ValueError, match="positively established termination_source"):
        validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_25b_termination_cause_and_observed_signal_are_disjoint_vocabularies():
    assert set(TerminationCause.__args__) & set(ObservedSignal.__args__) == set()
    for signal in ObservedSignal.__args__:
        assert signal not in TerminationCause.__args__


def test_25c_observed_signal_can_never_be_assigned_as_termination_source():
    """Type-level impossibility, independent of any validator: pydantic
    rejects a signal name in the termination_source field outright."""
    for signal in ("SIGINT", "SIGTERM", "UNKNOWN_EXTERNAL_TERMINATION"):
        with pytest.raises(ValidationError):
            GateEvidenceRecord(**_replacement_gate_kwargs(
                disposition="INFRASTRUCTURE_FAILURE", run_ids=(), scoring={}, execution_validity={},
                miss_patterns=(), failed_checks=(), cost_rows=(), accounted_total_eur_micros=0,
                auth_mode=None, termination_source=signal,
            ))


@pytest.mark.parametrize("disposition", ["GREEN", "HONEST_FAIL"])
def test_26_green_or_honest_fail_with_termination_source_refused_at_schema_level(disposition):
    extra = {"failed_checks": ("x: FAIL",)} if disposition == "HONEST_FAIL" else {}
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_replacement_gate_kwargs(
            disposition=disposition, termination_source="SESSION_DEADLINE", **extra,
        ))


def test_27_wrong_replacement_of_run_id_refused():
    record = GateEvidenceRecord(**_replacement_gate_kwargs(replacement_of_run_id="1"))
    with pytest.raises(ValueError, match="replacement_of_run_id"):
        validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_28_wrong_owner_ruling_id_refused():
    record = GateEvidenceRecord(**_replacement_gate_kwargs(owner_ruling_id="some-other-ruling"))
    with pytest.raises(ValueError, match="owner_ruling_id"):
        validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_29_wrong_marker_purpose_refused():
    record = GateEvidenceRecord(**_replacement_gate_kwargs(marker_purpose=repl.ORIGINAL_PURPOSE))
    with pytest.raises(ValueError, match="marker_purpose"):
        validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_30_wrong_workflow_identity_refused():
    record = GateEvidenceRecord(**_replacement_gate_kwargs(
        workflow_identity=".github/workflows/sentinel-wif-probe.yml",
    ))
    with pytest.raises(ValueError, match="workflow_identity"):
        validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_31_run_attempt_gt_1_refused():
    record = GateEvidenceRecord(**_replacement_gate_kwargs(run_attempt=2))
    with pytest.raises(ValueError, match="run_attempt"):
        validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_32_source_sha_mismatch_refused():
    other_sha = "b" * 40
    record = GateEvidenceRecord(**_replacement_gate_kwargs())
    with pytest.raises(ValueError, match="source_sha"):
        validate_replacement_provenance(record, expected_source_sha=other_sha)


@pytest.mark.parametrize("missing", ["envelope_id", "envelope_version"])
def test_33_missing_envelope_identity_refused(missing):
    record = GateEvidenceRecord(**_replacement_gate_kwargs(**{missing: None}))
    with pytest.raises(ValueError, match="envelope"):
        validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_34_backward_compatible_record_with_no_new_fields_still_constructs():
    """A pre-Stage-2A GateEvidenceRecord (no replacement fields at all)
    still constructs successfully -- the schema stays additive/optional
    for backward compatibility (ADR-0012 section 18)."""
    record = GateEvidenceRecord(
        schema_version=1, workflow_identity=".github/workflows/sentinel-official-gate.yml",
        github_run_id="1", run_attempt=1, event="workflow_dispatch", ref="refs/heads/main",
        source_sha=SHA_A, created_at_utc=datetime.now(timezone.utc), steps=(),
        expected_source_sha=SHA_A, model="claude-sonnet-5", profile_name="sonnet-official-gate",
        run_ids=("run1", "run2"), scoring={"emitted": 1}, thresholds={},
        invariant_results={"ok": True}, execution_validity={"valid": True},
        miss_patterns=(), failed_checks=(), cost_rows=(_cost_row(),),
        accounted_total_eur_micros=2_000, disposition="GREEN", auth_mode=WIF,
    )
    assert record.replacement_of_run_id is None
    assert record.termination_source is None
    assert record.observed_signals == ()

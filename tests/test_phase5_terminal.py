"""Tests for sentinel/phase5/terminal.py and the Stage-2B-1 widening of
sentinel/phase5/evidence_records.py and sentinel/phase5/receipts.py
(ADR-0012 sections 9, 10, 18, 21 and Amendment A1/A2/A6; dispatch
q77-p5d-repair-stage2b1-implement-a).

Model-free and network-blocked (tests/conftest.py ``block_network``).
Nothing here creates or consumes a marker, touches a workflow, or arms
the replacement.
"""

from __future__ import annotations

import ast
import itertools
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schemas import CostRow
from sentinel.phase5 import receipts as rc
from sentinel.phase5 import replacement as repl
from sentinel.phase5 import terminal as t
from sentinel.phase5.artifact_names import gate_evidence_name, oneshot_marker_name
from sentinel.phase5.evidence_records import (
    GateEvidenceRecord,
    TerminationCause,
    validate_replacement_provenance,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_REGISTRY = REPO_ROOT / "artifacts" / "phase5_receipt_registry.jsonl"
COMMITTED_REGISTRY_HEAD = "9f060888ea963305a512f534873fe056e8f7fe0c08d05137d26c6d95aeccfc39"

NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)
SHA_A = "a" * 40
SHA_B = "b" * 40
WIF = "github-actions-wif-federation"
WORKFLOW = ".github/workflows/sentinel-official-gate.yml"
ENVELOPE = t.EnvelopeIdentity(envelope_id="env-1", envelope_version="v1")


def _identity(purpose=repl.REPLACEMENT_PURPOSE, **overrides) -> t.TerminalIdentity:
    fields = dict(
        workflow_identity=WORKFLOW, run_id="700", run_attempt=1, event="workflow_dispatch",
        ref="refs/heads/main", source_sha=SHA_A, expected_source_sha=SHA_A, purpose=purpose,
    )
    fields.update(overrides)
    return t.TerminalIdentity(**fields)


def _cost_row(run_id="run1") -> CostRow:
    return CostRow(
        schema_version=1, run_id=run_id, recorded_at_utc=NOW, run_kind="dev",
        model="claude-sonnet-5", input_tokens=1, output_tokens=1, cost_eur_micros=2_000,
    )


def _quality_kwargs(disposition="GREEN", **overrides) -> dict:
    base = dict(
        schema_version=1, workflow_identity=WORKFLOW, github_run_id="700", run_attempt=1,
        event="workflow_dispatch", ref="refs/heads/main", source_sha=SHA_A, created_at_utc=NOW,
        steps=(), expected_source_sha=SHA_A, model="claude-sonnet-5", profile_name="sonnet-official-gate",
        run_ids=("run1", "run2"), scoring={"emitted": 1}, thresholds={}, invariant_results={"ok": True},
        execution_validity={"valid": True}, miss_patterns=(),
        failed_checks=("pooled_recall: 1/2 -> FAIL",) if disposition == "HONEST_FAIL" else (),
        cost_rows=(_cost_row(),), accounted_total_eur_micros=2_000, disposition=disposition,
        auth_mode=WIF, replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID,
        owner_ruling_id=repl.OWNER_RULING_ID, marker_purpose=repl.REPLACEMENT_PURPOSE,
        envelope_id="env-1", envelope_version="v1", terminal_writer="RUNNER",
    )
    base.update(overrides)
    return base


def _empty_quality() -> dict:
    return dict(
        run_ids=(), scoring={}, thresholds={}, invariant_results={}, execution_validity={},
        miss_patterns=(), failed_checks=(), cost_rows=(), accounted_total_eur_micros=0, auth_mode=None,
    )


def _infra_kwargs(cause="RUNNER_EXCEPTION", writer="RUNNER", **overrides) -> dict:
    base = _quality_kwargs(
        disposition="INFRASTRUCTURE_FAILURE", termination_source=cause, terminal_writer=writer,
        **_empty_quality(),
    )
    base.update(overrides)
    return base


def _unclassified_kwargs(basis="RUNNER_TERMINAL_EVIDENCE_ABSENT", writer="FINALIZER", signals=(), **overrides) -> dict:
    base = _quality_kwargs(
        disposition="UNCLASSIFIED_TERMINATION", unclassified_basis=basis, terminal_writer=writer,
        observed_signals=signals, **_empty_quality(),
    )
    base.update(overrides)
    return base


def _dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "artifacts"
    staging = tmp_path / "terminal-staging"
    quarantine = tmp_path / "terminal-quarantine"
    for d in (root, staging, quarantine):
        d.mkdir()
    return root, staging, quarantine


# ======================================================================
# Correction 1: strict terminal-writer provenance
# ======================================================================


@pytest.mark.parametrize("disposition", ["GREEN", "HONEST_FAIL"])
def test_replacement_quality_with_none_writer_refused(disposition):
    record = GateEvidenceRecord(**_quality_kwargs(disposition, terminal_writer=None))
    with pytest.raises(ValueError, match="terminal_writer"):
        validate_replacement_provenance(record, expected_source_sha=SHA_A)


@pytest.mark.parametrize("disposition", ["GREEN", "HONEST_FAIL"])
def test_replacement_quality_with_finalizer_writer_refused(disposition):
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_quality_kwargs(disposition, terminal_writer="FINALIZER"))
    bypassed = GateEvidenceRecord.model_construct(**_quality_kwargs(disposition, terminal_writer="FINALIZER"))
    with pytest.raises(ValueError, match="terminal_writer"):
        validate_replacement_provenance(bypassed, expected_source_sha=SHA_A)


@pytest.mark.parametrize("disposition", ["GREEN", "HONEST_FAIL"])
def test_replacement_quality_with_runner_writer_passes(disposition):
    record = GateEvidenceRecord(**_quality_kwargs(disposition))
    validate_replacement_provenance(record, expected_source_sha=SHA_A)


@pytest.mark.parametrize("writer", [None, "RUNNER"])
def test_replacement_unclassified_writer_must_be_finalizer(writer):
    if writer == "RUNNER":
        with pytest.raises(ValidationError):
            GateEvidenceRecord(**_unclassified_kwargs(writer="RUNNER"))
        record = GateEvidenceRecord.model_construct(**_unclassified_kwargs(writer="RUNNER"))
    else:
        record = GateEvidenceRecord(**_unclassified_kwargs(writer=None))
    with pytest.raises(ValueError, match="terminal_writer"):
        validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_replacement_unclassified_with_finalizer_writer_passes():
    record = GateEvidenceRecord(**_unclassified_kwargs())
    validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_replacement_infrastructure_failure_with_none_writer_refused():
    record = GateEvidenceRecord(**_infra_kwargs(writer=None))
    with pytest.raises(ValueError, match="terminal_writer"):
        validate_replacement_provenance(record, expected_source_sha=SHA_A)


@pytest.mark.parametrize(
    "writer,cause", list(itertools.product(["RUNNER", "FINALIZER"], TerminationCause.__args__))
)
def test_replacement_infrastructure_failure_accepts_runner_or_finalizer(writer, cause):
    record = GateEvidenceRecord(**_infra_kwargs(cause=cause, writer=writer))
    validate_replacement_provenance(record, expected_source_sha=SHA_A)


def test_general_schema_terminal_writer_remains_optional():
    for kwargs in (
        _quality_kwargs("GREEN", terminal_writer=None),
        _quality_kwargs("HONEST_FAIL", terminal_writer=None),
        _infra_kwargs(writer=None),
    ):
        record = GateEvidenceRecord(**kwargs)
        assert record.terminal_writer is None
        assert GateEvidenceRecord.model_validate_json(record.model_dump_json()) == record


def test_build_invalid_record_cannot_author_quality():
    import inspect

    params = set(inspect.signature(t.build_invalid_record).parameters)
    assert "disposition" not in params
    identity = _identity()
    unclassified = t.build_invalid_record(
        identity=identity, envelope=ENVELOPE, created_at_utc=NOW, model="claude-sonnet-5",
        profile_name="sonnet-official-gate", unclassified_basis="RUNNER_TERMINAL_EVIDENCE_ABSENT",
    )
    assert unclassified.disposition == "UNCLASSIFIED_TERMINATION"
    assert unclassified.terminal_writer == "FINALIZER"
    with pytest.raises(t.TerminalEvidenceError):
        t.build_invalid_record(
            identity=identity, envelope=ENVELOPE, created_at_utc=NOW, model="m", profile_name="p",
            unclassified_basis="RUNNER_TERMINAL_EVIDENCE_ABSENT", writer="RUNNER",
        )
    with pytest.raises(t.TerminalEvidenceError):
        t.build_invalid_record(
            identity=identity, envelope=ENVELOPE, created_at_utc=NOW, model="m", profile_name="p",
            infrastructure_cause="RUNNER_EXCEPTION",
        )
    for writer in ("RUNNER", "FINALIZER"):
        infra = t.build_invalid_record(
            identity=identity, envelope=ENVELOPE, created_at_utc=NOW, model="m", profile_name="p",
            infrastructure_cause="PRE_PROVIDER_FAILURE", writer=writer,
        )
        assert infra.disposition == "INFRASTRUCTURE_FAILURE" and infra.terminal_writer == writer
        validate_replacement_provenance(infra, expected_source_sha=SHA_A)
    with pytest.raises(t.TerminalEvidenceError):
        t.build_invalid_record(
            identity=identity, envelope=ENVELOPE, created_at_utc=NOW, model="m", profile_name="p",
            infrastructure_cause="SESSION_DEADLINE", writer="RUNNER",
        )
    with pytest.raises(t.TerminalEvidenceError):
        t.build_invalid_record(identity=identity, envelope=ENVELOPE, created_at_utc=NOW, model="m", profile_name="p")


def _all_summaries():
    for integrity in ("ABSENT", "OK", "TRAILING_FRAGMENT", "CORRUPT"):
        for signals in ((), ("SIGINT",), ("SIGTERM",), ("SIGINT", "SIGTERM")):
            for cause in (None, "PRE_PROVIDER_FAILURE", "RUNNER_EXCEPTION"):
                for write_failed in (False, True):
                    yield t.JournalSummary(
                        integrity=integrity, signals=signals, runner_exception_cause=cause,
                        terminal_write_failed=write_failed,
                    )


def test_decide_finalization_never_writes_quality_and_signal_never_yields_infra():
    kinds = t.CandidateVerdictKind.__args__
    for summary, kind, outcome, consumption, replaceable in itertools.product(
        list(_all_summaries()), kinds, ("success", "failure", "cancelled", "skipped", ""),
        t.MarkerConsumption.__args__, (True, False),
    ):
        decision = t.decide_finalization(
            run_attempt=1, consumption=consumption, candidate=kind, journal=summary,
            execute_step_outcome=outcome, candidate_replaceable=replaceable,
        )
        assert decision.action in t.FinalizerAction.__args__
        if kind == "TRUSTED_QUALITY" and consumption != "NOT_CONSUMED_BY_THIS_ATTEMPT":
            assert decision.action == "PRESERVE_RUNNER_EVIDENCE"
        if decision.action == "WRITE_INFRASTRUCTURE_INVALID":
            assert not summary.signals and outcome != "cancelled"
            assert decision.infrastructure_cause in ("PRE_PROVIDER_FAILURE", "RUNNER_EXCEPTION")


# ======================================================================
# Schema rules (E4)
# ======================================================================


@pytest.mark.parametrize(
    "basis,signals",
    [
        ("OBSERVED_SIGNAL", ("SIGINT",)),
        ("OBSERVED_SIGNAL", ("SIGTERM", "UNKNOWN_EXTERNAL_TERMINATION")),
        ("RUNNER_TERMINAL_EVIDENCE_ABSENT", ()),
        ("RUNNER_TERMINAL_EVIDENCE_UNTRUSTED", ()),
        ("RUNNER_TERMINAL_EVIDENCE_UNTRUSTED", ("SIGINT",)),
    ],
)
def test_unclassified_constructs_per_basis(basis, signals):
    record = GateEvidenceRecord(**_unclassified_kwargs(basis=basis, signals=signals))
    assert record.termination_source is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_ids": ("r1",)}, {"scoring": {"x": 1}}, {"thresholds": {"x": 1}},
        {"invariant_results": {"ok": True}}, {"execution_validity": {"valid": True}},
        {"miss_patterns": ("a",)}, {"failed_checks": ("a",)}, {"cost_rows": (_cost_row(),)},
        {"accounted_total_eur_micros": 1},
    ],
)
def test_unclassified_with_any_quality_content_refused(overrides):
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_unclassified_kwargs(**overrides))


def test_unclassified_schema_refusals():
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_unclassified_kwargs(termination_source="RUNNER_EXCEPTION"))
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_unclassified_kwargs(unclassified_basis=None))
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_unclassified_kwargs(basis="OBSERVED_SIGNAL", signals=()))
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_unclassified_kwargs(basis="RUNNER_TERMINAL_EVIDENCE_ABSENT", signals=("SIGINT",)))


def test_unclassified_basis_only_with_unclassified_disposition():
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_infra_kwargs(unclassified_basis="OBSERVED_SIGNAL"))


@pytest.mark.parametrize("cause", ["PRE_PROVIDER_FAILURE", "RUNNER_EXCEPTION"])
@pytest.mark.parametrize("signal", ["SIGINT", "SIGTERM", "UNKNOWN_EXTERNAL_TERMINATION"])
def test_runner_observed_cause_with_any_signal_refused(cause, signal):
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_infra_kwargs(cause=cause, observed_signals=(signal,)))


def test_duplicate_observed_signals_refused():
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_unclassified_kwargs(basis="OBSERVED_SIGNAL", signals=("SIGINT", "SIGINT")))


def test_replacement_unclassified_provenance_refusals():
    bypassed = GateEvidenceRecord.model_construct(**_unclassified_kwargs(termination_source="WATCHDOG"))
    with pytest.raises(ValueError, match="termination_source"):
        validate_replacement_provenance(bypassed, expected_source_sha=SHA_A)
    bypassed = GateEvidenceRecord.model_construct(**_unclassified_kwargs(unclassified_basis=None))
    with pytest.raises(ValueError, match="unclassified_basis"):
        validate_replacement_provenance(bypassed, expected_source_sha=SHA_A)


# ======================================================================
# Receipt vocabulary (I)
# ======================================================================


def _history_prefix(path: Path) -> None:
    rc.append_receipt(
        path, allow_create=True, clock=lambda: NOW, schema_version=1, receipt_class="ONESHOT_MARKER_CONSUMED",
        purpose=repl.ORIGINAL_PURPOSE, github_run_id=repl.REPLACEMENT_OF_RUN_ID, run_attempt=1, source_sha=SHA_A,
        artifact_name=oneshot_marker_name(repl.ORIGINAL_PURPOSE, repl.REPLACEMENT_OF_RUN_ID), artifact_id=1,
        payload_filename="marker.json", payload_sha256="1" * 64, disposition="CONSUMED",
        replacement_of_run_id=None, owner_ruling_id=None, governance_ref=None,
    )
    rc.append_receipt(
        path, clock=lambda: NOW, schema_version=1, receipt_class="EXECUTION_DISPOSITION",
        purpose=repl.ORIGINAL_PURPOSE, github_run_id=repl.REPLACEMENT_OF_RUN_ID, run_attempt=1, source_sha=SHA_A,
        artifact_name=None, artifact_id=None, payload_filename=None, payload_sha256=None,
        disposition="EXECUTION_INVALID / NO_QUALITY_RESULT", replacement_of_run_id=None,
        owner_ruling_id=repl.OWNER_RULING_ID, governance_ref=repl.ORIGINAL_INCIDENT_RECORD_REF,
    )


def _gate_receipt_fields(disposition, purpose=repl.REPLACEMENT_PURPOSE, run_id="700") -> dict:
    replacement = purpose == repl.REPLACEMENT_PURPOSE
    return dict(
        schema_version=1, receipt_class="GATE_EVIDENCE", purpose=purpose, github_run_id=run_id,
        run_attempt=1, source_sha=SHA_A, artifact_name=gate_evidence_name(run_id, 1), artifact_id=9,
        payload_filename="phase5_official_gate.json", payload_sha256="9" * 64, disposition=disposition,
        replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID if replacement else None,
        owner_ruling_id=repl.OWNER_RULING_ID if replacement else None, governance_ref=None,
    )


def _disposition_receipt_fields(disposition, purpose=repl.REPLACEMENT_PURPOSE, run_id="700") -> dict:
    replacement = purpose == repl.REPLACEMENT_PURPOSE
    return dict(
        schema_version=1, receipt_class="EXECUTION_DISPOSITION", purpose=purpose, github_run_id=run_id,
        run_attempt=1, source_sha=SHA_A, artifact_name=None, artifact_id=None, payload_filename=None,
        payload_sha256=None, disposition=disposition,
        replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID if replacement else None,
        owner_ruling_id=repl.OWNER_RULING_ID, governance_ref="some-record-ref",
    )


def test_receipt_vocabulary_widened_for_replacement_purpose_only():
    assert rc.UNCLASSIFIED_TERMINATION == "UNCLASSIFIED_TERMINATION"
    assert rc.EXECUTION_INVALID_UNCLASSIFIED_TERMINATION == "EXECUTION_INVALID / UNCLASSIFIED_TERMINATION"
    rc.Phase5Receipt(**_gate_receipt_fields("UNCLASSIFIED_TERMINATION"), prev_receipt_sha256="0" * 64, recorded_at_utc=NOW)
    rc.Phase5Receipt(
        **_disposition_receipt_fields("EXECUTION_INVALID / UNCLASSIFIED_TERMINATION"),
        prev_receipt_sha256="0" * 64, recorded_at_utc=NOW,
    )
    with pytest.raises(ValidationError):
        rc.Phase5Receipt(
            **_gate_receipt_fields("UNCLASSIFIED_TERMINATION", purpose=repl.ORIGINAL_PURPOSE),
            prev_receipt_sha256="0" * 64, recorded_at_utc=NOW,
        )
    with pytest.raises(ValidationError):
        rc.Phase5Receipt(
            **_disposition_receipt_fields("EXECUTION_INVALID / UNCLASSIFIED_TERMINATION", purpose=repl.ORIGINAL_PURPOSE),
            prev_receipt_sha256="0" * 64, recorded_at_utc=NOW,
        )


def test_committed_registry_unchanged_four_lines_same_head():
    receipts = rc.load_registry(COMMITTED_REGISTRY)
    assert len(receipts) == 4
    assert rc.registry_head_sha256(receipts) == COMMITTED_REGISTRY_HEAD


def test_unclassified_receipt_blocks_verdict_and_is_never_seam_quality(tmp_path):
    path = tmp_path / "registry.jsonl"
    _history_prefix(path)
    rc.append_receipt(path, clock=lambda: NOW, **_disposition_receipt_fields("EXECUTION_INVALID / UNCLASSIFIED_TERMINATION"))
    rc.append_receipt(path, clock=lambda: NOW, **_gate_receipt_fields("UNCLASSIFIED_TERMINATION"))
    receipts = rc.load_registry(path)
    verdict = repl.replacement_history_verdict(receipts, [], repl.REPLACEMENT_PURPOSE)
    assert verdict.permits_one_replacement is False
    assert rc.authoritative_quality_receipt(receipts, repl.REPLACEMENT_PURPOSE) is None
    seam_quality = [
        r for r in rc.evidence_receipts(receipts, "GATE_EVIDENCE") if r.disposition in ("GREEN", "HONEST_FAIL")
    ]
    assert seam_quality == []


# ======================================================================
# State model (C)
# ======================================================================


def test_every_allowed_transition_passes_and_every_other_is_refused():
    states = list(t.ALLOWED_TRANSITIONS)
    targets = [s for s in states if s is not None]
    for previous in states:
        for nxt in targets:
            if nxt in t.ALLOWED_TRANSITIONS[previous]:
                t.assert_transition(previous, nxt)
            else:
                with pytest.raises(t.TerminalStateError):
                    t.assert_transition(previous, nxt)


def test_trusted_terminal_never_downgrades_and_absorbing_states_absorb():
    with pytest.raises(t.TerminalStateError):
        t.assert_transition("TERMINAL_EVIDENCE_WRITTEN", "INVALID_EVIDENCE_WRITTEN")
    for state in t.ABSORBING_STATES:
        assert t.ALLOWED_TRANSITIONS[state] == frozenset()
    with pytest.raises(t.TerminalStateError):
        t.assert_transition("BOGUS", "PREFLIGHTED")
    with pytest.raises(t.TerminalStateError):
        t.assert_transition(None, "BOGUS")


def test_writer_state_invariants():
    t.assert_transition("SCORED_PROVISIONAL", "TERMINAL_EVIDENCE_WRITTEN", writer="RUNNER")
    t.assert_transition("EXECUTING", "INVALID_EVIDENCE_WRITTEN", writer="FINALIZER")
    with pytest.raises(t.TerminalStateError):
        t.assert_transition("SCORED_PROVISIONAL", "TERMINAL_EVIDENCE_WRITTEN", writer="FINALIZER")
    with pytest.raises(t.TerminalStateError):
        t.assert_transition("TERMINAL_EVIDENCE_WRITTEN", "TERMINAL_EVIDENCE_PUBLISHED", writer="RUNNER")
    t.assert_transition("TERMINAL_EVIDENCE_WRITTEN", "TERMINAL_EVIDENCE_PUBLISHED")


# ======================================================================
# Candidate verification (E)
# ======================================================================


def _write_candidate(root: Path, data: bytes) -> None:
    (root / t.TERMINAL_FILENAME).write_bytes(data)


def test_candidate_absent(tmp_path):
    root, _, _ = _dirs(tmp_path)
    assert t.classify_candidate(root, _identity()).kind == "ABSENT"


@pytest.mark.parametrize(
    "kwargs,kind",
    [
        (_quality_kwargs("GREEN"), "TRUSTED_QUALITY"),
        (_quality_kwargs("HONEST_FAIL"), "TRUSTED_QUALITY"),
        (_infra_kwargs(), "TRUSTED_INFRASTRUCTURE_INVALID"),
        (_unclassified_kwargs(), "TRUSTED_UNCLASSIFIED"),
    ],
)
def test_candidate_trusted_kinds(tmp_path, kwargs, kind):
    root, _, _ = _dirs(tmp_path)
    data = GateEvidenceRecord(**kwargs).model_dump_json(indent=2).encode()
    _write_candidate(root, data)
    verdict = t.classify_candidate(root, _identity())
    assert verdict.kind == kind
    assert verdict.sha256 == t._sha256(data)


@pytest.mark.parametrize("data", [b"\xff\xfe", b"not json", b"[1, 2]", b"{\"truncated\": "])
def test_candidate_unparseable(tmp_path, data):
    root, _, _ = _dirs(tmp_path)
    _write_candidate(root, data)
    assert t.classify_candidate(root, _identity()).kind == "UNPARSEABLE"


def test_candidate_schema_invalid(tmp_path):
    root, _, _ = _dirs(tmp_path)
    _write_candidate(root, b"{\"schema_version\": 1}")
    assert t.classify_candidate(root, _identity()).kind == "SCHEMA_INVALID"


@pytest.mark.parametrize(
    "overrides",
    [
        {"workflow_identity": ".github/workflows/other.yml"}, {"run_id": "701"}, {"run_attempt": 2},
        {"event": "push"}, {"ref": "refs/heads/other"}, {"source_sha": SHA_B}, {"expected_source_sha": SHA_B},
    ],
)
def test_candidate_identity_invalid_per_field(tmp_path, overrides):
    root, _, _ = _dirs(tmp_path)
    _write_candidate(root, GateEvidenceRecord(**_quality_kwargs()).model_dump_json().encode())
    assert t.classify_candidate(root, _identity(**overrides)).kind == "IDENTITY_INVALID"


def test_candidate_provenance_invalid_for_replacement(tmp_path):
    root, _, _ = _dirs(tmp_path)
    _write_candidate(root, GateEvidenceRecord(**_quality_kwargs(terminal_writer=None)).model_dump_json().encode())
    assert t.classify_candidate(root, _identity()).kind == "PROVENANCE_INVALID"


def test_candidate_non_null_provenance_under_original_purpose_is_invalid(tmp_path):
    root, _, _ = _dirs(tmp_path)
    identity = _identity(purpose=repl.ORIGINAL_PURPOSE)
    _write_candidate(root, GateEvidenceRecord(**_quality_kwargs()).model_dump_json().encode())
    assert t.classify_candidate(root, identity).kind == "PROVENANCE_INVALID"
    plain = _quality_kwargs(
        replacement_of_run_id=None, owner_ruling_id=None, marker_purpose=None,
        envelope_id=None, envelope_version=None, terminal_writer=None,
    )
    _write_candidate(root, GateEvidenceRecord(**plain).model_dump_json().encode())
    assert t.classify_candidate(root, identity).kind == "TRUSTED_QUALITY"


def test_candidate_oversize(tmp_path, monkeypatch):
    root, _, _ = _dirs(tmp_path)
    monkeypatch.setattr(t, "MAX_TERMINAL_BYTES", 10)
    _write_candidate(root, b"x" * 11)
    assert t.classify_candidate(root, _identity()).kind == "OVERSIZE"
    assert t.verify_terminal_bytes(b"y" * 11, _identity()).kind == "OVERSIZE"


def test_candidate_directory_is_not_regular_and_not_replaceable(tmp_path):
    root, _, _ = _dirs(tmp_path)
    (root / t.TERMINAL_FILENAME).mkdir()
    verdict = t.classify_candidate(root, _identity())
    assert verdict.kind == "NOT_REGULAR_FILE" and verdict.replaceable is False


def test_candidate_symlink_is_not_regular_but_replaceable(tmp_path):
    root, _, _ = _dirs(tmp_path)
    real = tmp_path / "real.json"
    real.write_bytes(b"{}")
    try:
        os.symlink(real, root / t.TERMINAL_FILENAME)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    verdict = t.classify_candidate(root, _identity())
    assert verdict.kind == "NOT_REGULAR_FILE" and verdict.replaceable is True


def test_classify_refuses_missing_publication_root(tmp_path):
    with pytest.raises(t.TerminalEvidenceError):
        t.classify_candidate(tmp_path / "missing", _identity())


# ======================================================================
# Atomic writer (E)
# ======================================================================


def test_runner_write_is_atomic_and_returns_sha(tmp_path, monkeypatch):
    root, staging, _ = _dirs(tmp_path)
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    record = GateEvidenceRecord(**_quality_kwargs())
    digest = t.write_terminal_atomically(
        record, publication_root=root, staging_root=staging, prior=t.PRIOR_ABSENT, identity=_identity()
    )
    data = (root / t.TERMINAL_FILENAME).read_bytes()
    assert digest == t._sha256(data)
    assert data == record.model_dump_json(indent=2).encode()
    assert synced
    assert list(staging.iterdir()) == []
    assert t.classify_candidate(root, _identity()).kind == "TRUSTED_QUALITY"


def test_prior_absent_refuses_existing_target_and_cleans_staging(tmp_path):
    root, staging, _ = _dirs(tmp_path)
    _write_candidate(root, b"garbage")
    with pytest.raises(t.TerminalEvidenceError):
        t.write_terminal_atomically(
            GateEvidenceRecord(**_quality_kwargs()), publication_root=root, staging_root=staging,
            prior=t.PRIOR_ABSENT, identity=_identity(),
        )
    assert (root / t.TERMINAL_FILENAME).read_bytes() == b"garbage"
    assert list(staging.iterdir()) == []


def test_untrusted_prior_replaces_matching_untrusted_candidate(tmp_path):
    root, staging, _ = _dirs(tmp_path)
    _write_candidate(root, b"garbage")
    sha = t.classify_candidate(root, _identity()).sha256
    record = t.build_invalid_record(
        identity=_identity(), envelope=ENVELOPE, created_at_utc=NOW, model="claude-sonnet-5",
        profile_name="sonnet-official-gate", unclassified_basis="RUNNER_TERMINAL_EVIDENCE_UNTRUSTED",
    )
    t.write_terminal_atomically(
        record, publication_root=root, staging_root=staging, prior=t.UntrustedPrior(sha), identity=_identity()
    )
    assert t.classify_candidate(root, _identity()).kind == "TRUSTED_UNCLASSIFIED"


def test_untrusted_prior_refuses_changed_or_absent_or_trusted(tmp_path):
    root, staging, _ = _dirs(tmp_path)
    record = t.build_invalid_record(
        identity=_identity(), envelope=ENVELOPE, created_at_utc=NOW, model="m", profile_name="p",
        unclassified_basis="RUNNER_TERMINAL_EVIDENCE_UNTRUSTED",
    )
    with pytest.raises(t.TerminalEvidenceError):  # absent
        t.write_terminal_atomically(
            record, publication_root=root, staging_root=staging, prior=t.UntrustedPrior("0" * 64), identity=_identity()
        )
    _write_candidate(root, b"garbage")
    with pytest.raises(t.TerminalEvidenceError):  # changed
        t.write_terminal_atomically(
            record, publication_root=root, staging_root=staging, prior=t.UntrustedPrior("0" * 64), identity=_identity()
        )
    trusted = GateEvidenceRecord(**_quality_kwargs()).model_dump_json().encode()
    _write_candidate(root, trusted)
    with pytest.raises(t.TerminalEvidenceError, match="never replaced"):
        t.write_terminal_atomically(
            record, publication_root=root, staging_root=staging,
            prior=t.UntrustedPrior(t._sha256(trusted)), identity=_identity(),
        )
    assert (root / t.TERMINAL_FILENAME).read_bytes() == trusted
    with pytest.raises(t.TerminalEvidenceError):
        t.write_terminal_atomically(
            record, publication_root=root, staging_root=staging, prior="ABSENT", identity=_identity()
        )


def test_staging_nested_in_root_refused(tmp_path):
    root, _, _ = _dirs(tmp_path)
    nested = root / "staging"
    nested.mkdir()
    with pytest.raises(t.TerminalEvidenceError, match="sibling"):
        t.write_terminal_atomically(
            GateEvidenceRecord(**_quality_kwargs()), publication_root=root, staging_root=nested,
            prior=t.PRIOR_ABSENT, identity=_identity(),
        )


def test_root_nested_in_staging_refused(tmp_path):
    staging = tmp_path / "staging"
    root = staging / "artifacts"
    root.mkdir(parents=True)
    with pytest.raises(t.TerminalEvidenceError, match="sibling"):
        t.write_terminal_atomically(
            GateEvidenceRecord(**_quality_kwargs()), publication_root=root, staging_root=staging,
            prior=t.PRIOR_ABSENT, identity=_identity(),
        )


def test_symlinked_staging_refused(tmp_path):
    root, _, _ = _dirs(tmp_path)
    real = tmp_path / "real-staging"
    real.mkdir()
    link = tmp_path / "link-staging"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    with pytest.raises(t.TerminalEvidenceError, match="real directory"):
        t.write_terminal_atomically(
            GateEvidenceRecord(**_quality_kwargs()), publication_root=root, staging_root=link,
            prior=t.PRIOR_ABSENT, identity=_identity(),
        )


def test_different_device_refused(tmp_path, monkeypatch):
    root, staging, _ = _dirs(tmp_path)
    real_lstat = os.lstat

    class _Fake:
        def __init__(self, st):
            self.st_mode = st.st_mode
            self.st_dev = st.st_dev + 1
            self.st_size = st.st_size

    def fake_lstat(path, *a, **k):
        st = real_lstat(path, *a, **k)
        return _Fake(st) if Path(path) == staging else st

    monkeypatch.setattr(t.os, "lstat", fake_lstat)
    with pytest.raises(t.TerminalEvidenceError, match="same device"):
        t.write_terminal_atomically(
            GateEvidenceRecord(**_quality_kwargs()), publication_root=root, staging_root=staging,
            prior=t.PRIOR_ABSENT, identity=_identity(),
        )


def test_crash_before_replace_leaves_no_candidate(tmp_path, monkeypatch):
    root, staging, _ = _dirs(tmp_path)

    def boom(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(t, "_discard_staged", lambda tmp: None)  # simulate a hard stop: temp survives
    monkeypatch.setattr(t.os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        t.write_terminal_atomically(
            GateEvidenceRecord(**_quality_kwargs()), publication_root=root, staging_root=staging,
            prior=t.PRIOR_ABSENT, identity=_identity(),
        )
    assert t.classify_candidate(root, _identity()).kind == "ABSENT"
    leftovers = list(staging.iterdir())
    assert len(leftovers) == 1 and leftovers[0].name.startswith("tmp-") and leftovers[0].name.endswith(".part")
    assert t.inventory_publication_root(root).unexpected == ()


def test_ancillary_writer_refusals_and_success(tmp_path):
    root, staging, _ = _dirs(tmp_path)
    for name in (t.TERMINAL_FILENAME, t.JOURNAL_FILENAME, "other.json"):
        with pytest.raises(t.TerminalEvidenceError):
            t.write_ancillary_atomically(name, b"[]", publication_root=root, staging_root=staging)
    digest = t.write_ancillary_atomically(t.CHECKS_FILENAME, b"[]", publication_root=root, staging_root=staging)
    assert digest == t._sha256(b"[]")
    with pytest.raises(t.TerminalEvidenceError, match="already exists"):
        t.write_ancillary_atomically(t.CHECKS_FILENAME, b"[1]", publication_root=root, staging_root=staging)
    assert list(staging.iterdir()) == []


# ======================================================================
# Inventory and quarantine (E)
# ======================================================================


def test_inventory_classifies_unexpected_including_stray_temp(tmp_path):
    root, staging, quarantine = _dirs(tmp_path)
    (root / t.JOURNAL_FILENAME).write_bytes(b"")
    (root / t.CHECKS_FILENAME).write_bytes(b"[]")
    (root / "tmp-abc.part").write_bytes(b"partial")
    (root / ".hidden").write_bytes(b"x")
    inventory = t.inventory_publication_root(root)
    assert inventory.present_allowlisted == frozenset({t.JOURNAL_FILENAME, t.CHECKS_FILENAME})
    assert inventory.unexpected == (".hidden", "tmp-abc.part")
    digest = t.move_to_quarantine(
        root / "tmp-abc.part", quarantine, publication_root=root, path_class="UNEXPECTED"
    )
    assert digest == t._sha256(b"partial")
    assert not (root / "tmp-abc.part").exists()
    assert (quarantine / f"unexpected-{digest}.bin").read_bytes() == b"partial"


def test_copy_to_quarantine_keeps_candidate_in_place(tmp_path):
    root, _, quarantine = _dirs(tmp_path)
    _write_candidate(root, b"garbage")
    digest = t.copy_to_quarantine(root / t.TERMINAL_FILENAME, quarantine, publication_root=root)
    assert (root / t.TERMINAL_FILENAME).read_bytes() == b"garbage"
    assert (quarantine / f"untrusted-candidate-{digest}.bin").read_bytes() == b"garbage"
    assert t.copy_to_quarantine(root / t.TERMINAL_FILENAME, quarantine, publication_root=root) == digest


def test_quarantine_refusals(tmp_path):
    root, _, quarantine = _dirs(tmp_path)
    (root / "dir").mkdir()
    with pytest.raises(t.TerminalEvidenceError):
        t.move_to_quarantine(root / "dir", quarantine, publication_root=root, path_class="UNEXPECTED")
    (root / "f").write_bytes(b"x")
    with pytest.raises(t.TerminalEvidenceError):
        t.move_to_quarantine(root / "f", quarantine, publication_root=root, path_class="BOGUS")
    nested = root / "q"
    nested.mkdir()
    with pytest.raises(t.TerminalEvidenceError, match="sibling"):
        t.copy_to_quarantine(root / "f", nested, publication_root=root)


# ======================================================================
# Finalizer decision table (G) -- one test per frozen row
# ======================================================================

OK = t.JournalSummary(integrity="OK")


def _decide(candidate, journal=OK, outcome="success", consumption="CONSUMED", run_attempt=1, replaceable=True):
    return t.decide_finalization(
        run_attempt=run_attempt, consumption=consumption, candidate=candidate, journal=journal,
        execute_step_outcome=outcome, candidate_replaceable=replaceable,
    )


def test_row1_and_row2_runner_quality_preserved_identically():
    decision = _decide("TRUSTED_QUALITY")
    assert decision == t.FinalizerDecision(action="PRESERVE_RUNNER_EVIDENCE", quarantine_unexpected=True)
    # a later signal never overrides trusted quality evidence (A6)
    signalled = _decide("TRUSTED_QUALITY", t.JournalSummary(integrity="OK", signals=("SIGTERM",)), "cancelled")
    assert signalled == decision


def test_row3_runner_infrastructure_preserved_and_ancillary_quarantined():
    decision = _decide("TRUSTED_INFRASTRUCTURE_INVALID")
    assert decision.action == "PRESERVE_RUNNER_EVIDENCE" and decision.quarantine_ancillary is True


@pytest.mark.parametrize("integrity", ["OK", "TRAILING_FRAGMENT"])
@pytest.mark.parametrize("cause", ["PRE_PROVIDER_FAILURE", "RUNNER_EXCEPTION"])
def test_row3b_runner_exception_without_signal_writes_infrastructure(integrity, cause):
    decision = _decide("ABSENT", t.JournalSummary(integrity=integrity, runner_exception_cause=cause), "failure")
    assert decision.action == "WRITE_INFRASTRUCTURE_INVALID" and decision.infrastructure_cause == cause


def test_row3b_terminal_write_failed_alone_means_runner_exception():
    decision = _decide("ABSENT", t.JournalSummary(integrity="OK", terminal_write_failed=True), "failure")
    assert decision.infrastructure_cause == "RUNNER_EXCEPTION"


@pytest.mark.parametrize("signal", ["SIGINT", "SIGTERM"])
def test_rows4_5_single_signal_writes_unclassified(signal):
    summary = t.JournalSummary(integrity="OK", signals=(signal,), runner_exception_cause="RUNNER_EXCEPTION")
    decision = _decide("ABSENT", summary, "failure")
    assert decision.action == "WRITE_UNCLASSIFIED"
    assert decision.unclassified_basis == "OBSERVED_SIGNAL" and decision.observed_signals == (signal,)


def test_row6_cancelled_step_without_journaled_signal_is_unknown_external():
    decision = _decide("ABSENT", OK, "cancelled")
    assert decision.unclassified_basis == "OBSERVED_SIGNAL"
    assert decision.observed_signals == ("UNKNOWN_EXTERNAL_TERMINATION",)


@pytest.mark.parametrize(
    "summary,outcome",
    [
        (t.JournalSummary(integrity="ABSENT"), "skipped"),
        (t.JournalSummary(integrity="OK"), "failure"),
        (t.JournalSummary(integrity="CORRUPT", runner_exception_cause="RUNNER_EXCEPTION"), "failure"),
        (t.JournalSummary(integrity="OK"), "success"),
    ],
)
def test_row6b_killed_without_trace_writes_unclassified_absent(summary, outcome):
    decision = _decide("ABSENT", summary, outcome)
    assert decision.action == "WRITE_UNCLASSIFIED"
    assert decision.unclassified_basis == "RUNNER_TERMINAL_EVIDENCE_ABSENT" and decision.observed_signals == ()


@pytest.mark.parametrize(
    "kind", ["UNPARSEABLE", "SCHEMA_INVALID", "IDENTITY_INVALID", "PROVENANCE_INVALID", "OVERSIZE"]
)
def test_row7_corrupt_candidate_quarantined_and_unclassified(kind):
    decision = _decide(kind, t.JournalSummary(integrity="OK", signals=("SIGINT",)), "failure")
    assert decision.action == "WRITE_UNCLASSIFIED"
    assert decision.unclassified_basis == "RUNNER_TERMINAL_EVIDENCE_UNTRUSTED"
    assert decision.quarantine_candidate and decision.quarantine_ancillary
    assert decision.observed_signals == ("SIGINT",)


def test_row7b_non_regular_candidate():
    assert _decide("NOT_REGULAR_FILE", replaceable=True).action == "WRITE_UNCLASSIFIED"
    assert _decide("NOT_REGULAR_FILE", replaceable=True).quarantine_candidate is False
    assert _decide("NOT_REGULAR_FILE", replaceable=False).action == "INTERNAL_ERROR"


def test_row8_row9_missing_or_temp_only_follow_absent_rows():
    assert _decide("ABSENT").unclassified_basis == "RUNNER_TERMINAL_EVIDENCE_ABSENT"


def test_row11_not_consumed_or_attempt_gt_1():
    assert _decide("TRUSTED_QUALITY", run_attempt=2) == t.FinalizerDecision(
        action="NO_TERMINAL_REQUIRED", reason="RUN_ATTEMPT_GT_1"
    )
    assert _decide("ABSENT", consumption="NOT_CONSUMED_BY_THIS_ATTEMPT").action == "NO_TERMINAL_REQUIRED"
    assert _decide("ABSENT", consumption="ASSUMED_CONSUMED_REST_UNAVAILABLE").action == "WRITE_UNCLASSIFIED"


def test_unknown_candidate_verdict_is_internal_error():
    assert _decide("SOMETHING_ELSE").action == "INTERNAL_ERROR"


@pytest.mark.parametrize(
    "outcome,rest,expected",
    [
        ("skipped", True, "NOT_CONSUMED_BY_THIS_ATTEMPT"),
        ("success", False, "CONSUMED"),
        ("failure", True, "CONSUMED"),
        ("cancelled", False, "NOT_CONSUMED_BY_THIS_ATTEMPT"),
        ("failure", None, "ASSUMED_CONSUMED_REST_UNAVAILABLE"),
        ("", None, "ASSUMED_CONSUMED_REST_UNAVAILABLE"),
    ],
)
def test_consumption_mapping(outcome, rest, expected):
    assert t.consumption_from(outcome, rest) == expected


# ======================================================================
# Unarmed (J)
# ======================================================================


def test_official_gate_purpose_unchanged_and_new_modules_unwired():
    text = (REPO_ROOT / "scripts" / "run_phase5_official_gate.py").read_text(encoding="utf-8")
    assert 'PURPOSE = "P5D_OFFICIAL_SONNET_GATE"' in text
    for path in (REPO_ROOT / "scripts").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for name in names:
                assert "sentinel.phase5.terminal" not in name, path
                assert "sentinel.phase5.journal" not in name, path

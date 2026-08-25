"""Tests for scripts/run_phase5_wif_probe.py (P5-B Part 3/3).
P5-C plumbing only; Part 3 never executes it.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE_RUNNER_PATH = REPO_ROOT / "scripts" / "run_phase5_wif_probe.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_phase5_wif_probe", PROBE_RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_purpose_string_and_totals_exact():
    module = _load_module()
    assert module.PURPOSE == "P5C_WIF_PROBE"
    assert module.PROBE_TOTAL_EUR_MICROS == 150_000
    assert module.PROBE_RESERVE_EUR_MICROS == 150_000


def test_fx_and_wif_and_oneshot_checks_precede_marker_write_in_preflight():
    """Seam 7: every retryable preflight (expected-source, one-shot
    discovery, WIF config, FX resolution + coordinator construction)
    must run BEFORE write_marker_json in cmd_preflight's source order."""
    text = PROBE_RUNNER_PATH.read_text(encoding="utf-8")
    start = text.index("def cmd_preflight")
    end = text.index("def cmd_execute")
    body = text[start:end]
    source_idx = body.index("assert_expected_source_on_disk")
    oneshot_idx = body.index("assert_purpose_not_yet_consumed")
    wif_idx = body.index("auth.assert_wif_config_ready")
    fx_idx = body.index("resolve_ecb_usd_per_eur")
    marker_idx = body.index("write_marker_json")
    assert source_idx < marker_idx
    assert oneshot_idx < marker_idx
    assert wif_idx < marker_idx
    assert fx_idx < marker_idx


def test_marker_visibility_confirmed_before_oidc_in_execute():
    text = PROBE_RUNNER_PATH.read_text(encoding="utf-8")
    start = text.index("def cmd_execute")
    body = text[start:]
    marker_confirm_idx = body.index("assert_marker_visible_for_this_run")
    oidc_idx = body.index("oidc.acquire_oidc")
    assert marker_confirm_idx < oidc_idx


def test_attempt_gt_1_is_ineligible_for_marker_creation():
    from sentinel.phase5.oneshot import is_eligible_marker_creation
    from sentinel.phase5.models import OneShotMarker
    from datetime import datetime, timezone

    marker = OneShotMarker(
        schema_version=1, purpose="P5C_WIF_PROBE", created_at_utc=datetime.now(timezone.utc),
        workflow_identity=".github/workflows/sentinel-wif-probe.yml", github_run_id="1",
        run_attempt=2, event="workflow_dispatch", source_sha="a" * 40,
    )
    assert is_eligible_marker_creation(marker) is False


def test_existing_marker_refuses_before_any_new_marker_creation():
    from sentinel.phase5.oneshot import OneShotAlreadyConsumed, assert_purpose_not_yet_consumed
    from sentinel.phase5.models import OneShotMarker
    from datetime import datetime, timezone
    import pytest

    existing = OneShotMarker(
        schema_version=1, purpose="P5C_WIF_PROBE", created_at_utc=datetime.now(timezone.utc),
        workflow_identity=".github/workflows/sentinel-wif-probe.yml", github_run_id="1",
        run_attempt=1, event="workflow_dispatch", source_sha="a" * 40,
    )
    with pytest.raises(OneShotAlreadyConsumed):
        assert_purpose_not_yet_consumed("P5C_WIF_PROBE", [existing])


def test_no_static_credential_fallback_path():
    text = PROBE_RUNNER_PATH.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in text
    assert "LOCAL_OAUTH" not in text
    assert "auth.WIF" in text


def test_probe_uses_haiku_model_not_sonnet():
    text = PROBE_RUNNER_PATH.read_text(encoding="utf-8")
    assert "HAIKU_ORDINARY.model" in text
    assert "SONNET_OFFICIAL_GATE" not in text


def test_probe_preflight_prepares_work_root_before_discovery():
    """Dispatch q77-p5d-premarker-workroot-init-repair-a: the shared
    work-root trusted-anchor gap (discover_oneshot_markers ->
    download_artifact -> create_fresh_root, requiring work_root to
    already exist) applies equally to this probe runner's own
    cmd_preflight -- it calls the exact same shared
    discover_oneshot_markers helper against the exact same
    per-workflow WORK_ROOT shape. Fixed consistently rather than
    leaving this completed P5-C runner with the same latent
    non-empty-state defect (its one-shot is already consumed, but the
    Python code path itself is shared and must not silently diverge)."""
    text = PROBE_RUNNER_PATH.read_text(encoding="utf-8")
    prepare_idx = text.index("prepare_fresh_work_root(args.work_root)")
    discover_idx = text.index("discover_oneshot_markers(client, args.work_root)")
    assert prepare_idx < discover_idx


def test_disposition_vocabulary_is_closed_pass_or_fail():
    text = PROBE_RUNNER_PATH.read_text(encoding="utf-8")
    assert '"CAPABILITY_PASS"' in text
    assert '"CAPABILITY_FAIL"' in text


def test_probe_evidence_record_disposition_pass_requires_cost_rows():
    """Schema-level guarantee (evidence_records.py), re-asserted here
    against a probe-shaped payload so the runner's own disposition
    logic cannot silently drift from the schema it writes to."""
    import pytest
    from datetime import datetime, timezone
    from pydantic import ValidationError

    from sentinel.phase5.evidence_records import ProbeEvidenceRecord

    with pytest.raises(ValidationError):
        ProbeEvidenceRecord(
            schema_version=1, workflow_identity=".github/workflows/sentinel-wif-probe.yml",
            github_run_id="1", run_attempt=1, event="workflow_dispatch", ref="refs/heads/main",
            source_sha="a" * 40, created_at_utc=datetime.now(timezone.utc), steps=(),
            expected_source_sha="a" * 40, disposition="CAPABILITY_PASS",
            cost_rows=(), accounted_total_eur_micros=0,
        )


# =====================================================================
# C0 (dispatch q77-p5c-execute-a): probe core repair coverage --
# parent RunRecord/terminal-close, failure-path CostRow preservation,
# persisted-row-derived auth_mode, and the mechanized PASS ceiling.
# =====================================================================

import argparse
import json
import sqlite3
from dataclasses import dataclass, field as _dc_field
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from agents.checker import auth
from agents.checker.harness import CagedCheckerStub, CheckerAgentError
from checks.judgment.stubs import JudgmentRequest
from contracts.schemas import CostRow, RunRecord
from sentinel import ledger
from sentinel.phase5.evidence_records import ProbeEvidenceRecord

_T0 = datetime(2026, 8, 24, 6, 0, 0, tzinfo=timezone.utc)


@dataclass
class _FakeResult:
    """Minimal stand-in for the SDK's terminal ResultMessage -- exactly
    the attributes agents/checker/harness.py's _terminalize/_estimate_
    eur_micros read."""

    total_cost_usd: float = 0.01
    usage: dict = _dc_field(default_factory=lambda: {"input_tokens": 100, "output_tokens": 50})
    num_turns: int = 1
    is_error: bool = False
    subtype: str = "success"


class _FakeSession:
    def __init__(self):
        self.shutdown_calls = 0

    def assert_healthy(self):
        return None

    def install_and_start(self, env):
        return None

    def shutdown(self, env):
        self.shutdown_calls += 1


class _FakeEvidenceClient:
    pass


def _clear_wif_env(monkeypatch):
    for var in auth.WIF_REQUIRED_ENV_VARS | auth.WIF_SHADOW_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _set_valid_wif_env(monkeypatch, tmp_path):
    _clear_wif_env(monkeypatch)
    token_file = tmp_path / "identity-token"
    token_file.write_text("not-a-real-token", encoding="ascii")
    monkeypatch.setenv("ANTHROPIC_FEDERATION_RULE_ID", "fdrl_test")
    monkeypatch.setenv("ANTHROPIC_ORGANIZATION_ID", "org_test")
    monkeypatch.setenv("ANTHROPIC_SERVICE_ACCOUNT_ID", "svac_test")
    monkeypatch.setenv("ANTHROPIC_IDENTITY_TOKEN_FILE", str(token_file))


def _fixed_ctx():
    from sentinel.phase5.github_context import GithubActionsContext

    return GithubActionsContext(
        repository="kobescak-kristian/ai-portfolio-sentinel",
        repository_owner="kobescak-kristian",
        run_id="999", run_attempt=1, event="workflow_dispatch",
        ref="refs/heads/main", sha="a" * 40,
        workflow_path=".github/workflows/sentinel-wif-probe.yml",
        api_url="https://api.github.com", server_url="https://github.com",
    )


def _make_args(tmp_path):
    fx_path = tmp_path / "fx-state.json"
    fx_path.write_text(json.dumps({
        "source": "test-fx", "rate_date": "2026-08-24",
        "retrieved_at_utc": "2026-08-24T00:00:00+00:00", "usd_per_eur": "1.10",
    }), encoding="utf-8")
    return argparse.Namespace(
        expected_source_sha="a" * 40,
        work_root=tmp_path,
        fx_state_path=fx_path,
        evidence_out=tmp_path / "probe-evidence.json",
    )


def _patch_common_seams(module, monkeypatch, session=None):
    monkeypatch.setattr(module, "build_evidence_client", lambda env: _FakeEvidenceClient())
    monkeypatch.setattr(module, "assert_marker_visible_for_this_run", lambda client, run_id, name: None)
    monkeypatch.setattr(module, "assert_expected_source_live", lambda client, sha: None)
    monkeypatch.setattr(module, "derive_github_context", lambda env: _fixed_ctx())
    monkeypatch.setattr("agents.checker.oidc.acquire_oidc", lambda env: session or _FakeSession())


def _patch_stub_factory(monkeypatch, query_fn):
    """Make cmd_execute's un-mockable default-query_fn construction
    controllable in tests: a REAL CagedCheckerStub (so ledger/FK/
    accounting behavior is genuine), with query_fn overridden BEFORE
    cmd_execute wraps it with health_gated."""

    def factory(*, run_id, conn, coordinator, model, auth_profile):
        return CagedCheckerStub(
            run_id=run_id, conn=conn, coordinator=coordinator,
            model=model, auth_profile=auth_profile, query_fn=query_fn,
        )

    monkeypatch.setattr("agents.checker.harness.CagedCheckerStub", factory)


def _read_evidence(args) -> ProbeEvidenceRecord:
    return ProbeEvidenceRecord.model_validate_json(args.evidence_out.read_text(encoding="utf-8"))


def _run_id_for(args) -> str:
    return f"r-p5c-{_fixed_ctx().run_id}"


# ---------------------------------------------------------------------
# FK enforcement -- guards against ever loosening the schema.
# ---------------------------------------------------------------------


def test_agent_calls_fk_enforced_without_parent_run_row(tmp_path):
    conn = ledger.open_ledger(tmp_path / "fk.sqlite3")
    with pytest.raises(sqlite3.IntegrityError):
        with ledger.unit_of_work(conn):
            ledger.insert_agent_call_reserved(
                conn, run_id="r-does-not-exist", task_key="s::c", surface="s",
                check_class="missing-synthetic-label", model="m",
                auth_mode="github-actions-wif-federation", started_at_utc=_T0,
                reserved_eur_micros=0, fx_source="src", fx_rate_date="2026-08-24",
                fx_retrieved_at_utc=_T0, fx_rate_decimal="1.10",
            )
    conn.close()


# ---------------------------------------------------------------------
# No second model call after exhausted/negative remaining capacity,
# observed at the actual query_fn invocation seam.
# ---------------------------------------------------------------------


def test_no_second_model_call_after_budget_exhausted(tmp_path, monkeypatch):
    from agents.checker.budget import RunBudgetCoordinator
    from agents.checker.fx import FxRate

    # LOCAL_OAUTH's check only cares that no override-capable variable
    # is present -- clear them regardless of the ambient dev/CI shell
    # (this machine's own environment sets ANTHROPIC_BASE_URL).
    for var in auth.AUTH_OVERRIDE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    conn = ledger.open_ledger(tmp_path / "budget.sqlite3")
    run_id = "r-budget-test"
    with ledger.unit_of_work(conn):
        ledger.insert_run(
            conn,
            RunRecord(
                schema_version=1, run_id=run_id, run_kind="live", status="RUNNING",
                started_at_utc=_T0, finished_at_utc=None, tasks_created=0, tasks_terminal=0,
                findings_new=0, findings_still_open=0, findings_resolved=0,
            ),
        )
    fx_rate = FxRate(source="test", rate_date="2026-08-24", retrieved_at_utc=_T0, usd_per_eur=Decimal("1.10"))
    coordinator = RunBudgetCoordinator(
        fx_rate=fx_rate, total_eur_micros=150_000, max_per_call_reserve_eur_micros=150_000,
    )
    calls: list = []

    def fake_query_fn(check_class, reservation, state, user_prompt, model=None):
        calls.append(1)
        return _FakeResult(total_cost_usd=1.0)  # overshoots the entire 150000 budget in one call

    stub = CagedCheckerStub(
        run_id=run_id, conn=conn, coordinator=coordinator, clock=lambda: _T0,
        model="test-model", auth_profile=auth.LOCAL_OAUTH, query_fn=fake_query_fn,
    )
    request = JudgmentRequest(surface="s", check_class="missing-synthetic-label", path="p", text="line")

    stub.judge(request)  # completes, but its overshoot drives remaining capacity <= 0
    assert len(calls) == 1
    assert coordinator.remaining_eur_micros() <= 0

    with pytest.raises(CheckerAgentError):
        stub.judge(request)
    assert len(calls) == 1  # the second judgment never reached query_fn
    conn.close()


# ---------------------------------------------------------------------
# cmd_execute integration scenarios.
# ---------------------------------------------------------------------


def test_clean_judgment_at_or_below_ceiling_yields_pass(tmp_path, monkeypatch):
    module = _load_module()
    _patch_common_seams(module, monkeypatch)
    _set_valid_wif_env(monkeypatch, tmp_path)
    _patch_stub_factory(monkeypatch, lambda *a, **kw: _FakeResult())

    args = _make_args(tmp_path)
    rc = module.cmd_execute(args)
    evidence = _read_evidence(args)

    assert rc == 0
    assert evidence.disposition == "CAPABILITY_PASS"
    assert len(evidence.cost_rows) == 1
    assert evidence.auth_mode == "github-actions-wif-federation"
    assert evidence.accounted_total_eur_micros <= module.PROBE_TOTAL_EUR_MICROS

    conn = ledger.open_ledger(tmp_path / "probe.sqlite3")
    run = ledger.get_run(conn, _run_id_for(args))
    assert run.status == "COMPLETED"
    conn.close()


def test_sdk_failure_after_reservation_yields_fail_with_truthful_costrow(tmp_path, monkeypatch):
    module = _load_module()
    _patch_common_seams(module, monkeypatch)
    _set_valid_wif_env(monkeypatch, tmp_path)
    failing_result = _FakeResult(is_error=True, subtype="some_other_sdk_error")
    _patch_stub_factory(monkeypatch, lambda *a, **kw: failing_result)

    args = _make_args(tmp_path)
    rc = module.cmd_execute(args)
    evidence = _read_evidence(args)

    assert rc == 1
    assert evidence.disposition == "CAPABILITY_FAIL"
    assert len(evidence.cost_rows) == 1
    assert evidence.cost_rows[0].cost_eur_micros > 0  # truthful charge, never lost
    assert evidence.auth_mode == "github-actions-wif-federation"

    conn = ledger.open_ledger(tmp_path / "probe.sqlite3")
    run = ledger.get_run(conn, _run_id_for(args))
    assert run.status == "FAILED"
    conn.close()


def test_reserved_row_left_unresolved_charges_full_reservation(tmp_path, monkeypatch):
    module = _load_module()
    _patch_common_seams(module, monkeypatch)
    _set_valid_wif_env(monkeypatch, tmp_path)
    _patch_stub_factory(monkeypatch, lambda *a, **kw: _FakeResult())

    def _raise_after_insert(self, *a, **kw):
        raise RuntimeError("terminalization crash")

    monkeypatch.setattr(CagedCheckerStub, "_terminalize", _raise_after_insert)

    args = _make_args(tmp_path)
    rc = module.cmd_execute(args)
    evidence = _read_evidence(args)

    assert rc == 1
    assert evidence.disposition == "CAPABILITY_FAIL"
    assert len(evidence.cost_rows) == 1
    assert evidence.cost_rows[0].cost_eur_micros == module.PROBE_RESERVE_EUR_MICROS  # full reservation, not zero


def test_pre_provider_auth_refusal_makes_zero_query_fn_calls(tmp_path, monkeypatch):
    module = _load_module()
    _patch_common_seams(module, monkeypatch)
    _clear_wif_env(monkeypatch)  # required vars absent -> WifConfigurationError

    calls: list = []

    def must_not_be_called(*a, **kw):
        calls.append(1)
        raise AssertionError("model path must not be reached on a failed WIF precheck")

    _patch_stub_factory(monkeypatch, must_not_be_called)

    args = _make_args(tmp_path)
    rc = module.cmd_execute(args)
    evidence = _read_evidence(args)

    assert calls == []
    assert rc == 1
    assert evidence.disposition == "CAPABILITY_FAIL"
    assert len(evidence.cost_rows) == 1  # a REJECTED row is still an agent_calls row
    assert evidence.cost_rows[0].cost_eur_micros == 0
    assert evidence.auth_mode == "github-actions-wif-federation"

    conn = ledger.open_ledger(tmp_path / "probe.sqlite3")
    run = ledger.get_run(conn, _run_id_for(args))
    assert run.status == "FAILED"
    conn.close()


def test_no_agent_calls_row_at_all_yields_auth_mode_none(tmp_path, monkeypatch):
    module = _load_module()

    def _boom(client, run_id, name):
        raise RuntimeError("marker not visible")

    monkeypatch.setattr(module, "build_evidence_client", lambda env: _FakeEvidenceClient())
    monkeypatch.setattr(module, "assert_marker_visible_for_this_run", _boom)
    monkeypatch.setattr(module, "derive_github_context", lambda env: _fixed_ctx())

    args = _make_args(tmp_path)
    rc = module.cmd_execute(args)
    evidence = _read_evidence(args)

    assert rc == 1
    assert evidence.disposition == "CAPABILITY_FAIL"
    assert evidence.cost_rows == ()
    assert evidence.auth_mode is None


def test_clean_judgment_over_ceiling_yields_fail_with_full_unclamped_total(tmp_path, monkeypatch):
    module = _load_module()
    _patch_common_seams(module, monkeypatch)
    _set_valid_wif_env(monkeypatch, tmp_path)
    # 1.0 USD / 1.10 usd-per-eur is far above the 150000 ceiling, and
    # above the per-call reservation too -- ADR-0008: a completed call's
    # charge is never clamped to its reservation.
    _patch_stub_factory(monkeypatch, lambda *a, **kw: _FakeResult(total_cost_usd=1.0))

    args = _make_args(tmp_path)
    rc = module.cmd_execute(args)
    evidence = _read_evidence(args)

    assert rc == 1
    assert evidence.disposition == "CAPABILITY_FAIL"
    assert evidence.accounted_total_eur_micros > module.PROBE_TOTAL_EUR_MICROS
    assert evidence.cost_rows[0].cost_eur_micros == evidence.accounted_total_eur_micros


def test_close_run_failure_yields_fail_without_erasing_evidence_or_raw_exception_prose(
    tmp_path, monkeypatch, capsys
):
    module = _load_module()
    session = _FakeSession()
    _patch_common_seams(module, monkeypatch, session=session)
    _set_valid_wif_env(monkeypatch, tmp_path)
    _patch_stub_factory(monkeypatch, lambda *a, **kw: _FakeResult())
    monkeypatch.setattr(
        "sentinel.ledger.close_run",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db is locked, very sensitive detail")),
    )

    args = _make_args(tmp_path)
    rc = module.cmd_execute(args)
    evidence = _read_evidence(args)

    assert rc == 1
    assert evidence.disposition == "CAPABILITY_FAIL"
    # accounting already derived from the clean judgment survives untouched
    assert len(evidence.cost_rows) == 1
    assert evidence.accounted_total_eur_micros > 0
    assert evidence.auth_mode == "github-actions-wif-federation"
    # cleanup still ran
    assert session.shutdown_calls == 1
    # no raw exception prose ever printed, only the bounded type name
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert "db is locked" not in captured.err
    assert "very sensitive detail" not in captured.err


# ---------------------------------------------------------------------
# Evidence-schema tests (auth_mode).
# ---------------------------------------------------------------------


def _cost_row() -> CostRow:
    return CostRow(
        schema_version=1, run_id="r-1", recorded_at_utc=_T0, run_kind="live",
        model="claude-haiku-4-5-20251001", input_tokens=10, output_tokens=5, cost_eur_micros=100,
    )


def test_probe_evidence_record_pass_with_no_auth_mode_is_invalid():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProbeEvidenceRecord(
            schema_version=1, workflow_identity=".github/workflows/sentinel-wif-probe.yml",
            github_run_id="1", run_attempt=1, event="workflow_dispatch", ref="refs/heads/main",
            source_sha="a" * 40, created_at_utc=_T0, steps=(),
            expected_source_sha="a" * 40, disposition="CAPABILITY_PASS",
            cost_rows=(_cost_row(),), accounted_total_eur_micros=100, auth_mode=None,
        )


def test_probe_evidence_record_pass_with_conflicting_auth_mode_is_invalid():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProbeEvidenceRecord(
            schema_version=1, workflow_identity=".github/workflows/sentinel-wif-probe.yml",
            github_run_id="1", run_attempt=1, event="workflow_dispatch", ref="refs/heads/main",
            source_sha="a" * 40, created_at_utc=_T0, steps=(),
            expected_source_sha="a" * 40, disposition="CAPABILITY_PASS",
            cost_rows=(_cost_row(),), accounted_total_eur_micros=100, auth_mode="conflicting-auth-mode",
        )


def test_probe_evidence_record_pass_with_valid_wif_auth_mode_is_accepted():
    record = ProbeEvidenceRecord(
        schema_version=1, workflow_identity=".github/workflows/sentinel-wif-probe.yml",
        github_run_id="1", run_attempt=1, event="workflow_dispatch", ref="refs/heads/main",
        source_sha="a" * 40, created_at_utc=_T0, steps=(),
        expected_source_sha="a" * 40, disposition="CAPABILITY_PASS",
        cost_rows=(_cost_row(),), accounted_total_eur_micros=100,
        auth_mode="github-actions-wif-federation",
    )
    assert record.auth_mode == "github-actions-wif-federation"


# =====================================================================
# scripts/record_phase5_cost_evidence.py (C0-D) coverage.
# =====================================================================

RECORD_SCRIPT_PATH = REPO_ROOT / "scripts" / "record_phase5_cost_evidence.py"


def _load_record_module():
    spec = importlib.util.spec_from_file_location("record_phase5_cost_evidence", RECORD_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cost_row_json(run_id: str, cost_eur_micros: int = 100) -> dict:
    return {
        "schema_version": 1, "run_id": run_id,
        "recorded_at_utc": "2026-08-24T00:00:00+00:00", "run_kind": "live",
        "model": "claude-haiku-4-5-20251001", "input_tokens": 10, "output_tokens": 5,
        "cost_eur_micros": cost_eur_micros,
    }


def _evidence_json(run_ids: list) -> str:
    return json.dumps({
        "schema_version": 1, "workflow_identity": ".github/workflows/sentinel-wif-probe.yml",
        "github_run_id": "1", "run_attempt": 1, "event": "workflow_dispatch",
        "ref": "refs/heads/main", "source_sha": "a" * 40,
        "created_at_utc": "2026-08-24T00:00:00+00:00", "steps": [],
        "expected_source_sha": "a" * 40,
        "disposition": "CAPABILITY_PASS" if run_ids else "CAPABILITY_FAIL",
        "cost_rows": [_cost_row_json(rid) for rid in run_ids],
        "accounted_total_eur_micros": 100 * len(run_ids),
        "auth_mode": "github-actions-wif-federation" if run_ids else None,
    })


def test_record_script_refuses_on_malformed_existing_ledger(tmp_path):
    module = _load_record_module()
    ledger_path = tmp_path / "cost_ledger.jsonl"
    ledger_path.write_text('{"not": "valid CostRow JSON at all"\n', encoding="utf-8")
    before = ledger_path.read_bytes()
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(_evidence_json(["r-new-1"]), encoding="utf-8")

    rc = module.main(["--evidence-path", str(evidence_path), "--cost-ledger", str(ledger_path)])

    assert rc == 1
    assert ledger_path.read_bytes() == before


def test_record_script_refuses_duplicate_against_existing_ledger(tmp_path):
    module = _load_record_module()
    from telemetry.cost_ledger import serialize_cost_row

    ledger_path = tmp_path / "cost_ledger.jsonl"
    existing_row = CostRow.model_validate(_cost_row_json("r-existing"))
    ledger_path.write_text(serialize_cost_row(existing_row) + "\n", encoding="utf-8")
    before = ledger_path.read_bytes()
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(_evidence_json(["r-existing"]), encoding="utf-8")

    rc = module.main(["--evidence-path", str(evidence_path), "--cost-ledger", str(ledger_path)])

    assert rc == 1
    assert ledger_path.read_bytes() == before


def test_record_script_refuses_duplicate_within_incoming_batch(tmp_path):
    module = _load_record_module()
    ledger_path = tmp_path / "cost_ledger.jsonl"
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(_evidence_json(["r-dup", "r-dup"]), encoding="utf-8")

    rc = module.main(["--evidence-path", str(evidence_path), "--cost-ledger", str(ledger_path)])

    assert rc == 1
    assert not ledger_path.exists()


def test_record_script_appends_exactly_once_and_reparses_clean(tmp_path):
    module = _load_record_module()
    from telemetry.cost_ledger import read_cost_rows

    ledger_path = tmp_path / "cost_ledger.jsonl"
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(_evidence_json(["r-fresh"]), encoding="utf-8")

    rc = module.main(["--evidence-path", str(evidence_path), "--cost-ledger", str(ledger_path)])

    assert rc == 0
    rows = read_cost_rows(ledger_path)
    assert [r.run_id for r in rows] == ["r-fresh"]


def test_record_script_second_invocation_refuses_duplicate(tmp_path):
    module = _load_record_module()
    ledger_path = tmp_path / "cost_ledger.jsonl"
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(_evidence_json(["r-once"]), encoding="utf-8")

    rc1 = module.main(["--evidence-path", str(evidence_path), "--cost-ledger", str(ledger_path)])
    assert rc1 == 0
    after_first = ledger_path.read_bytes()

    rc2 = module.main(["--evidence-path", str(evidence_path), "--cost-ledger", str(ledger_path)])
    assert rc2 == 1
    assert ledger_path.read_bytes() == after_first


def test_record_script_post_append_verification_is_not_a_removable_assert(tmp_path, monkeypatch):
    """Monkeypatches read_cost_rows so the post-append reparse reports
    the just-appended run_id as absent (simulating a concurrent-writer
    anomaly). Proves the tool reports nonzero with a bounded message --
    an `assert` stripped under `python -O` would instead silently return
    0 here, which this test would catch."""
    module = _load_record_module()
    ledger_path = tmp_path / "cost_ledger.jsonl"
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(_evidence_json(["r-verify"]), encoding="utf-8")

    def _empty_read(path):
        return []

    monkeypatch.setattr(module, "read_cost_rows", _empty_read)

    rc = module.main(["--evidence-path", str(evidence_path), "--cost-ledger", str(ledger_path)])
    assert rc == 1

"""Tests for sentinel/phase5/preflight.py (P5-B Part 3/3)."""

from __future__ import annotations

import pytest

from sentinel.phase5.preflight import (
    PROVIDER_GATE_STEP,
    REHEARSAL_STEP_ORDER,
    SCHEDULED_STEP_ORDER,
    PreflightLedger,
    ProviderNotPermitted,
    StepOrderViolation,
)


def test_golden_scheduled_step_order():
    assert SCHEDULED_STEP_ORDER == (
        "S01_DERIVE_GITHUB_CONTEXT",
        "S02_DISCOVER_ACTIVE_WINDOW",
        "S03_RESOLVE_OWNED_SLOT",
        "S04_VALIDATE_PROVENANCE",
        "S05_RESTORE_PREDECESSOR_BUNDLE",
        "S06_EVALUATE_SPEND_AND_CADENCE",
        "S07_ASSERT_WIF_CONFIG_READY",
        "S08_PRE_PROVIDER_EVIDENCE",
        "S09_REQUEST_OIDC_TOKEN",
        "S10_WRITE_TOKEN_FILE",
        "S11_PERMIT_PROVIDER_PATH",
        "S12_EXECUTE_LIVE_SENTINEL_RUN",
        "S13_ASSERT_TERMINAL_LEDGER_ROW",
        "S14_ASSERT_EXACTLY_ONE_COSTROW",
        "S15_BUILD_AND_VALIDATE_SUCCESSOR",
        "S16_CLASSIFY_QUALIFICATION",
        "S17_STAGE_SUCCESSOR_ARTIFACT",
        "S18_CLEANUP_TOKEN_FILE",
    )


def test_golden_rehearsal_step_order():
    assert REHEARSAL_STEP_ORDER == (
        "S01_DERIVE_GITHUB_CONTEXT",
        "S02_DISCOVER_ACTIVE_WINDOW",
        "S03_RESOLVE_OWNED_SLOT",
        "S04_VALIDATE_PROVENANCE",
        "S05_RESTORE_PREDECESSOR_BUNDLE",
        "S06_EVALUATE_SPEND_AND_CADENCE",
        "S08_PRE_PROVIDER_EVIDENCE",
        "S15_BUILD_AND_VALIDATE_SUCCESSOR",
        "S17_STAGE_SUCCESSOR_ARTIFACT",
    )
    for absent in ("S07_ASSERT_WIF_CONFIG_READY", "S09_REQUEST_OIDC_TOKEN", "S10_WRITE_TOKEN_FILE",
                   "S11_PERMIT_PROVIDER_PATH", "S12_EXECUTE_LIVE_SENTINEL_RUN",
                   "S13_ASSERT_TERMINAL_LEDGER_ROW", "S14_ASSERT_EXACTLY_ONE_COSTROW",
                   "S16_CLASSIFY_QUALIFICATION"):
        assert absent not in REHEARSAL_STEP_ORDER


def test_out_of_order_record_raises():
    ledger = PreflightLedger(order=SCHEDULED_STEP_ORDER)
    with pytest.raises(StepOrderViolation):
        ledger.record("S02_DISCOVER_ACTIVE_WINDOW", "OK")


def test_skipping_ahead_raises():
    ledger = PreflightLedger(order=SCHEDULED_STEP_ORDER)
    ledger.record("S01_DERIVE_GITHUB_CONTEXT", "OK")
    with pytest.raises(StepOrderViolation):
        ledger.record("S03_RESOLVE_OWNED_SLOT", "OK")


def test_early_exit_blocks_all_later_records():
    ledger = PreflightLedger(order=SCHEDULED_STEP_ORDER)
    ledger.record("S01_DERIVE_GITHUB_CONTEXT", "OK")
    ledger.mark_early_exit("S02_DISCOVER_ACTIVE_WINDOW", "no active window")
    with pytest.raises(StepOrderViolation):
        ledger.record("S03_RESOLVE_OWNED_SLOT", "OK")


def test_provider_permitted_false_until_s11_ok():
    ledger = PreflightLedger(order=SCHEDULED_STEP_ORDER)
    for step in SCHEDULED_STEP_ORDER[:10]:
        assert ledger.provider_permitted is False
        ledger.record(step, "OK")
    assert ledger.provider_permitted is False  # S11 not yet recorded
    ledger.record("S11_PERMIT_PROVIDER_PATH", "OK")
    assert ledger.provider_permitted is True


def test_guard_provider_raises_before_s11():
    ledger = PreflightLedger(order=SCHEDULED_STEP_ORDER)
    with pytest.raises(ProviderNotPermitted):
        ledger.guard_provider(lambda: "should not run")


def test_guard_provider_permits_after_s11():
    ledger = PreflightLedger(order=SCHEDULED_STEP_ORDER)
    for step in SCHEDULED_STEP_ORDER[:11]:
        ledger.record(step, "OK")
    assert ledger.guard_provider(lambda: 42) == 42


def test_a_refused_step_also_terminates_the_ledger():
    ledger = PreflightLedger(order=SCHEDULED_STEP_ORDER)
    ledger.record("S01_DERIVE_GITHUB_CONTEXT", "REFUSED", "bad context")
    with pytest.raises(StepOrderViolation):
        ledger.record("S02_DISCOVER_ACTIVE_WINDOW", "OK")


def test_to_records_reflects_recorded_order_and_detail():
    ledger = PreflightLedger(order=SCHEDULED_STEP_ORDER)
    ledger.record("S01_DERIVE_GITHUB_CONTEXT", "OK", "ctx-123")
    records = ledger.to_records()
    assert len(records) == 1
    assert records[0].step_id == "S01_DERIVE_GITHUB_CONTEXT"
    assert records[0].status == "OK"
    assert records[0].detail == "ctx-123"


def test_rehearsal_ledger_cannot_record_provider_steps():
    ledger = PreflightLedger(order=REHEARSAL_STEP_ORDER)
    for step in REHEARSAL_STEP_ORDER[:6]:
        ledger.record(step, "OK")
    with pytest.raises(StepOrderViolation):
        ledger.record("S07_ASSERT_WIF_CONFIG_READY", "OK")

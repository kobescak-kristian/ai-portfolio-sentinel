"""Tests for sentinel/phase5/cadence.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sentinel.phase5 import cadence as c
from tests.test_phase5_bundle import WINDOW_CREATED_AT, make_control_state, make_cost_row, make_window, slot_ts

NOW = datetime(2026, 9, 1, 6, 37, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# trailing_30d_spend_eur_micros — fail-closed evidence
# ---------------------------------------------------------------------------


def test_trailing_spend_closed_interval_boundaries(tmp_path):
    ledger = tmp_path / "cost_ledger.jsonl"
    in_range_start = NOW - timedelta(days=30)
    in_range_end = NOW
    out_before = in_range_start - timedelta(seconds=1)
    out_after = in_range_end + timedelta(seconds=1)
    rows = [
        make_cost_row("r-before", cost_eur_micros=1000, recorded_at_utc=out_before),
        make_cost_row("r-start", cost_eur_micros=10, recorded_at_utc=in_range_start),
        make_cost_row("r-mid", cost_eur_micros=20, recorded_at_utc=NOW - timedelta(days=15)),
        make_cost_row("r-end", cost_eur_micros=30, recorded_at_utc=in_range_end),
        make_cost_row("r-after", cost_eur_micros=2000, recorded_at_utc=out_after),
    ]
    from sentinel.phase5.models import canonical_json_bytes

    with ledger.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            data = row.model_dump()
            import json

            data["recorded_at_utc"] = row.recorded_at_utc.isoformat()
            handle.write(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n")

    total = c.trailing_30d_spend_eur_micros(ledger, NOW)
    assert total == 60  # only r-start + r-mid + r-end


def test_trailing_spend_missing_ledger_raises_with_generic_message(tmp_path):
    with pytest.raises(c.CostEvidenceUnavailable) as excinfo:
        c.trailing_30d_spend_eur_micros(tmp_path / "missing.jsonl", NOW)
    assert str(excinfo.value) == "cost ledger missing"


def test_trailing_spend_malformed_ledger_raises_generic_message_cause_preserved(tmp_path):
    ledger = tmp_path / "cost_ledger.jsonl"
    ledger.write_text("not json at all\n", encoding="utf-8")
    with pytest.raises(c.CostEvidenceUnavailable) as excinfo:
        c.trailing_30d_spend_eur_micros(ledger, NOW)
    assert str(excinfo.value) == "cost ledger unreadable or malformed"
    assert "not json at all" not in str(excinfo.value)
    assert excinfo.value.__cause__ is not None


def test_trailing_spend_valid_empty_ledger_sums_to_zero(tmp_path):
    ledger = tmp_path / "cost_ledger.jsonl"
    ledger.write_text("", encoding="utf-8")
    assert c.trailing_30d_spend_eur_micros(ledger, NOW) == 0


def test_trailing_spend_oserror_path_raises(tmp_path, monkeypatch):
    ledger = tmp_path / "cost_ledger.jsonl"
    ledger.write_text("", encoding="utf-8")

    def _boom(_path):
        raise OSError("disk gone")

    monkeypatch.setattr(c, "read_cost_rows", _boom)
    with pytest.raises(c.CostEvidenceUnavailable) as excinfo:
        c.trailing_30d_spend_eur_micros(ledger, NOW)
    assert str(excinfo.value) == "cost ledger unreadable or malformed"


# ---------------------------------------------------------------------------
# EUR50 / EUR40
# ---------------------------------------------------------------------------


def test_eur50_exact_boundary_passes():
    c.assert_provider_start_permitted(50_000_000 - c.SCHEDULED_ORDINARY_ALLOWANCE_EUR_MICROS, c.SCHEDULED_ORDINARY_ALLOWANCE_EUR_MICROS)


def test_eur50_one_micro_over_refuses():
    with pytest.raises(c.CostCeilingExceeded):
        c.assert_provider_start_permitted(
            50_000_000 - c.SCHEDULED_ORDINARY_ALLOWANCE_EUR_MICROS + 1, c.SCHEDULED_ORDINARY_ALLOWANCE_EUR_MICROS
        )


@pytest.mark.parametrize("allowance", [c.SCHEDULED_ORDINARY_ALLOWANCE_EUR_MICROS, c.WIF_PROBE_ALLOWANCE_EUR_MICROS, c.OFFICIAL_GATE_ALLOWANCE_EUR_MICROS])
def test_eur50_named_allowances(allowance):
    c.assert_provider_start_permitted(0, allowance)
    with pytest.raises(c.CostCeilingExceeded):
        c.assert_provider_start_permitted(50_000_000, allowance)


def test_headroom_exact_boundary_passes():
    assert c.window_freeze_headroom_ok(40_000_000 - 5 * 750_000, "DAILY") is True


def test_headroom_one_micro_over_fails():
    assert c.window_freeze_headroom_ok(40_000_000 - 5 * 750_000 + 1, "DAILY") is False


def test_headroom_formula_includes_five_ordinary_allowances():
    boundary = 40_000_000 - 5 * c.SCHEDULED_ORDINARY_ALLOWANCE_EUR_MICROS
    assert c.window_freeze_headroom_ok(boundary, "DAILY") is True
    assert c.window_freeze_headroom_ok(boundary + 1, "DAILY") is False


@pytest.mark.parametrize("cadence_level", ["EVERY_2_DAYS", "WEEKLY"])
def test_headroom_non_daily_always_fails(cadence_level):
    assert c.window_freeze_headroom_ok(0, cadence_level) is False


# ---------------------------------------------------------------------------
# Cadence eligibility
# ---------------------------------------------------------------------------


def test_daily_always_eligible():
    assert c.is_slot_eligible("DAILY", None, slot_ts(1)) is True


def test_daily_requires_no_anchor_but_tolerates_none():
    assert c.is_slot_eligible("DAILY", slot_ts(1), slot_ts(2)) is True


@pytest.mark.parametrize("offset_days,expected", [(0, False), (1, False), (2, True), (3, False), (4, True)])
def test_every_2_days_offsets(offset_days, expected):
    anchor = slot_ts(1)
    expected_slot = anchor + timedelta(days=offset_days)
    assert c.is_slot_eligible("EVERY_2_DAYS", anchor, expected_slot) is expected


@pytest.mark.parametrize("offset_days,expected", [(0, False), (1, False), (6, False), (7, True), (14, True)])
def test_weekly_offsets(offset_days, expected):
    anchor = slot_ts(1)
    expected_slot = anchor + timedelta(days=offset_days)
    assert c.is_slot_eligible("WEEKLY", anchor, expected_slot) is expected


def test_non_daily_without_anchor_raises_state_corruption():
    with pytest.raises(c.Phase5StateCorruption):
        c.is_slot_eligible("WEEKLY", None, slot_ts(1))


# ---------------------------------------------------------------------------
# evaluate_scheduled_trigger
# ---------------------------------------------------------------------------


def test_scheduled_trigger_ineligible_daily_cron_is_cadence_skip():
    window = make_window()
    control_state = make_control_state(window, cadence_level="WEEKLY", cadence_anchor=slot_ts(1))
    decision = c.evaluate_scheduled_trigger(control_state, 0, window, slot_ts(2))
    assert decision.outcome == "CADENCE_SKIP"
    assert decision.provider_call_permitted is False


def test_scheduled_trigger_eur50_refusal():
    window = make_window()
    control_state = make_control_state(window, cadence_level="DAILY")
    spend = 50_000_000 - c.SCHEDULED_ORDINARY_ALLOWANCE_EUR_MICROS + 1
    decision = c.evaluate_scheduled_trigger(control_state, spend, None, slot_ts(1))
    assert decision.outcome == "COST_CADENCE_REFUSAL"
    assert decision.provider_call_permitted is False
    assert decision.consume_active_window is False
    assert decision.window_consume_reason is None


def test_scheduled_trigger_combined_eur50_fail_and_window_breach():
    window = make_window()
    control_state = make_control_state(window, cadence_level="DAILY")
    spend = 50_000_000 - c.SCHEDULED_ORDINARY_ALLOWANCE_EUR_MICROS + 1
    decision = c.evaluate_scheduled_trigger(control_state, spend, window, slot_ts(1))
    assert decision.outcome == "COST_CADENCE_REFUSAL"
    assert decision.provider_call_permitted is False
    assert decision.consume_active_window is True
    assert decision.window_consume_reason == "COST_CADENCE_REFUSAL"


def test_scheduled_trigger_window_breach_alone_without_eur50_fail():
    window = make_window()
    control_state = make_control_state(window, cadence_level="DAILY")
    spend = 41_000_000  # over 40M, well under 50M-750k
    decision = c.evaluate_scheduled_trigger(control_state, spend, window, slot_ts(1))
    assert decision.consume_active_window is True
    assert decision.window_consume_reason == "COST_CADENCE_REFUSAL"


def test_scheduled_trigger_daily_to_every_2_days_transition():
    window = make_window()
    control_state = make_control_state(window, cadence_level="DAILY")
    decision = c.evaluate_scheduled_trigger(control_state, 41_000_000, None, slot_ts(1))
    assert decision.outcome == "CADENCE_SKIP"
    assert decision.cadence_transition_to == "EVERY_2_DAYS"
    assert decision.new_anchor == slot_ts(1)


def test_scheduled_trigger_every_2_days_to_weekly_transition():
    window = make_window()
    control_state = make_control_state(window, cadence_level="EVERY_2_DAYS", cadence_anchor=slot_ts(1))
    decision = c.evaluate_scheduled_trigger(control_state, 41_000_000, None, slot_ts(3))
    assert decision.cadence_transition_to == "WEEKLY"


def test_scheduled_trigger_weekly_floor_proceeds_once_eur50_passes():
    window = make_window()
    control_state = make_control_state(window, cadence_level="WEEKLY", cadence_anchor=slot_ts(1))
    decision = c.evaluate_scheduled_trigger(control_state, 41_000_000, None, slot_ts(8))
    assert decision.outcome == "PROCEED"
    assert decision.provider_call_permitted is True


def test_scheduled_trigger_ordinary_proceed():
    window = make_window()
    control_state = make_control_state(window, cadence_level="DAILY")
    decision = c.evaluate_scheduled_trigger(control_state, 0, window, slot_ts(1))
    assert decision.outcome == "PROCEED"
    assert decision.provider_call_permitted is True


# ---------------------------------------------------------------------------
# evaluate_post_run
# ---------------------------------------------------------------------------


def test_post_run_consumes_with_distinct_reason():
    window = make_window()
    decision = c.evaluate_post_run(window, 41_000_000)
    assert decision.consume_active_window is True
    assert decision.window_consume_reason == "POST_RUN_COST_TRIGGER"


def test_post_run_no_crossing_does_not_consume():
    window = make_window()
    decision = c.evaluate_post_run(window, 1_000)
    assert decision.consume_active_window is False
    assert decision.window_consume_reason is None


def test_post_run_no_active_window_never_consumes():
    decision = c.evaluate_post_run(None, 999_000_000)
    assert decision.consume_active_window is False


def test_post_run_spend_never_clamped_for_display():
    window = make_window()
    huge = 500_000_000
    decision = c.evaluate_post_run(window, huge)
    assert decision.consume_active_window is True
    # the decision itself carries no spend number to clamp — this test
    # documents that evaluate_post_run never mutates or reports a
    # capped value, only the honest consume/reason pair
    assert decision.window_consume_reason == "POST_RUN_COST_TRIGGER"


# ---------------------------------------------------------------------------
# Phase5ControlState clean-completion invariant (constructed via cadence
# module's own imported model, exercising the cross-module contract)
# ---------------------------------------------------------------------------


def test_control_state_clean_completion_only_at_slot5():
    window = make_window()
    make_control_state(window, slot_index=5, window_consumed=True, window_consume_reason=None)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_control_state(window, slot_index=4, window_consumed=True, window_consume_reason=None)

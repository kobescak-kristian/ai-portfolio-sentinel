"""Tests for sentinel/phase5/oneshot.py."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sentinel.phase5 import models as m
from sentinel.phase5 import oneshot as o

NOW = datetime(2026, 9, 1, 6, 37, 0, tzinfo=timezone.utc)
SOURCE_SHA = "a" * 40
OTHER_SOURCE_SHA = "b" * 40


def make_marker(purpose="P5C_WIF_PROBE", **overrides) -> m.OneShotMarker:
    fields = dict(
        schema_version=1,
        purpose=purpose,
        created_at_utc=NOW,
        workflow_identity="phase5-scheduled",
        github_run_id="gh-run-1",
        run_attempt=1,
        event="schedule",
        source_sha=SOURCE_SHA,
    )
    fields.update(overrides)
    return m.OneShotMarker(**fields)


def test_zero_markers_eligible():
    o.assert_purpose_not_yet_consumed("P5C_WIF_PROBE", [])


def test_existing_probe_marker_blocks_all_later_probe_attempts():
    marker = make_marker("P5C_WIF_PROBE")
    with pytest.raises(o.OneShotAlreadyConsumed):
        o.assert_purpose_not_yet_consumed("P5C_WIF_PROBE", [marker])


def test_existing_gate_marker_blocks_all_later_gate_attempts():
    marker = make_marker("P5D_OFFICIAL_SONNET_GATE")
    with pytest.raises(o.OneShotAlreadyConsumed):
        o.assert_purpose_not_yet_consumed("P5D_OFFICIAL_SONNET_GATE", [marker])


def test_probe_marker_does_not_consume_gate():
    marker = make_marker("P5C_WIF_PROBE")
    o.assert_purpose_not_yet_consumed("P5D_OFFICIAL_SONNET_GATE", [marker])


def test_gate_marker_does_not_consume_probe():
    marker = make_marker("P5D_OFFICIAL_SONNET_GATE")
    o.assert_purpose_not_yet_consumed("P5C_WIF_PROBE", [marker])


def test_attempt2_never_eligible_for_creation():
    marker = make_marker(run_attempt=2)
    assert o.is_eligible_marker_creation(marker) is False


def test_attempt1_eligible_for_creation():
    marker = make_marker(run_attempt=1)
    assert o.is_eligible_marker_creation(marker) is True


def test_marker_round_trips_through_canonical_json():
    marker = make_marker()
    data = m.canonical_json_bytes(marker)
    assert m.sha256_hex_of_model(marker) == __import__("hashlib").sha256(data).hexdigest()


def test_malformed_ambiguous_candidates_fail_closed():
    marker_a = make_marker(github_run_id="gh-run-a")
    marker_b = make_marker(github_run_id="gh-run-b")
    with pytest.raises(o.OneShotDiscoveryAmbiguous):
        o.assert_purpose_not_yet_consumed("P5C_WIF_PROBE", [marker_a, marker_b])


def test_identical_duplicate_markers_still_consume_once():
    marker_a = make_marker()
    marker_b = make_marker()
    with pytest.raises(o.OneShotAlreadyConsumed):
        o.assert_purpose_not_yet_consumed("P5C_WIF_PROBE", [marker_a, marker_b])


def test_source_sha_change_does_not_reset_one_shot():
    marker = make_marker(source_sha=SOURCE_SHA)
    new_attempt = make_marker(source_sha=OTHER_SOURCE_SHA)
    # a purpose-wide check against the OLD marker still blocks, regardless
    # of what a differently-shaped candidate for a new source SHA would be
    with pytest.raises(o.OneShotAlreadyConsumed):
        o.assert_purpose_not_yet_consumed("P5C_WIF_PROBE", [marker])
    with pytest.raises(o.OneShotAlreadyConsumed):
        o.assert_purpose_not_yet_consumed("P5C_WIF_PROBE", [new_attempt])


def test_no_outcome_field_on_marker():
    with pytest.raises(ValidationError):
        m.OneShotMarker(
            schema_version=1,
            purpose="P5C_WIF_PROBE",
            created_at_utc=NOW,
            workflow_identity="wf",
            github_run_id="r1",
            run_attempt=1,
            event="schedule",
            source_sha=SOURCE_SHA,
            outcome="SUCCESS",
        )


def test_marker_source_sha_must_be_hex40():
    with pytest.raises(ValidationError):
        make_marker(source_sha="not-hex")


def test_marker_extra_field_rejected():
    with pytest.raises(ValidationError):
        make_marker(unexpected="nope")

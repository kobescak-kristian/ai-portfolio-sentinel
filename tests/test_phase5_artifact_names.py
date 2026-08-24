"""Tests for sentinel/phase5/artifact_names.py (P5-B Part 3/3)."""

from __future__ import annotations

import pytest

from sentinel.phase5 import artifact_names as an


def test_p5w_window_id_accepted():
    an.assert_artifact_safe_window_id("p5w-32646306244")
    an.assert_artifact_safe_window_id("p5w-control-run-1")  # Part-2 test fixture form


@pytest.mark.parametrize(
    "bad",
    ["bad-window", "p5w-", "p5w-" + "a" * 41, "P5W-123", "notp5w-123", "p5w-Has-Upper"],
)
def test_unsafe_window_id_rejected(bad):
    with pytest.raises(an.ArtifactNameError):
        an.assert_artifact_safe_window_id(bad)


def test_genesis_name_roundtrip():
    name = an.genesis_name("p5w-111", "999")
    assert name == "sentinel-p5-genesis-p5w-111-r999"
    parsed = an.parse_artifact_name(name)
    assert parsed.kind == "GENESIS"
    assert parsed.window_id == "p5w-111"
    assert parsed.run_id == "999"


def test_slot_name_roundtrip_and_bounds():
    for slot_index in range(1, 6):
        name = an.slot_name("p5w-111", slot_index, "999")
        parsed = an.parse_artifact_name(name)
        assert parsed.kind == "SLOT_SUCCESSOR"
        assert parsed.slot_index == slot_index
        assert parsed.window_id == "p5w-111"
    with pytest.raises(an.ArtifactNameError):
        an.slot_name("p5w-111", 0, "999")
    with pytest.raises(an.ArtifactNameError):
        an.slot_name("p5w-111", 6, "999")


def test_refusal_name_roundtrip():
    name = an.refusal_name("p5w-111", "999")
    parsed = an.parse_artifact_name(name)
    assert parsed.kind == "CONTROL_REFUSAL"
    assert parsed.window_id == "p5w-111"


@pytest.mark.parametrize(
    "purpose,slug", [("P5C_WIF_PROBE", "p5c-wif-probe"), ("P5D_OFFICIAL_SONNET_GATE", "p5d-official-sonnet-gate")]
)
def test_oneshot_marker_name_roundtrip(purpose, slug):
    name = an.oneshot_marker_name(purpose, "42")
    assert name == f"sentinel-p5-oneshot-{slug}-r42"
    parsed = an.parse_artifact_name(name)
    assert parsed.kind == "ONESHOT_MARKER"
    assert parsed.purpose == purpose
    assert parsed.run_id == "42"


def test_unknown_oneshot_purpose_rejected():
    with pytest.raises(an.ArtifactNameError):
        an.oneshot_marker_name("NOT_A_REAL_PURPOSE", "1")


@pytest.mark.parametrize(
    "builder",
    [
        an.attempt_evidence_name,
        an.prewindow_evidence_name,
        an.rehearsal_evidence_name,
        an.probe_evidence_name,
        an.gate_evidence_name,
        an.freeze_refusal_evidence_name,
    ],
)
def test_evidence_names_carry_run_id_and_attempt(builder):
    name = builder("777", 3)
    parsed = an.parse_artifact_name(name)
    assert parsed.run_id == "777"
    assert parsed.attempt == 3


def test_evidence_names_unique_per_attempt():
    a = an.attempt_evidence_name("1", 1)
    b = an.attempt_evidence_name("1", 2)
    assert a != b


def test_prefix_helpers_match_generated_names():
    assert an.genesis_name("p5w-1", "2").startswith(an.GENESIS_PREFIX)
    assert an.oneshot_marker_name("P5C_WIF_PROBE", "1").startswith(an.ONESHOT_PREFIX)
    assert an.slot_name("p5w-1", 1, "2").startswith(an.slot_prefix("p5w-1"))
    assert an.refusal_name("p5w-1", "2").startswith(an.refusal_prefix("p5w-1"))
    # different window ids do not share a slot/refusal prefix
    assert not an.slot_name("p5w-2", 1, "2").startswith(an.slot_prefix("p5w-1"))


def test_parse_rejects_unrelated_strings():
    assert an.parse_artifact_name("not-one-of-ours") is None
    assert an.parse_artifact_name("sentinel-p5-genesis-badwindow-r1") is None


def test_supersession_example_uses_p5w_form():
    old_id = "p5w-100"
    new_id = "p5w-200"
    assert an.genesis_name(old_id, "100") != an.genesis_name(new_id, "200")
    an.assert_artifact_safe_window_id(old_id)
    an.assert_artifact_safe_window_id(new_id)

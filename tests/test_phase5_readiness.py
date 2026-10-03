"""Tests for the P5-D replacement readiness matrix (ADR-0012 section 22,
Amendments A9 and B; Stage 2C-B6-3a, plan revision 4 and owner rulings
D1 to D5, D7, R6 to R10 of 2026-10-02).

Model-free and network-free: every fact is injected; the collector tests
use a ``Sources`` fake that reads real repository files but scripts every
git and GitHub answer.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from sentinel.phase5 import readiness as rd

REPO_ROOT = Path(__file__).resolve().parent.parent
ADR_PATH = REPO_ROOT / "adr" / "0012-p5d-replacement-execution-envelope.md"
UTC = timezone.utc
T = datetime(2026, 10, 3, 10, 0, 0, tzinfo=UTC)
BASE = "a" * 40


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "run_phase5_readiness", REPO_ROOT / "scripts" / "run_phase5_readiness.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ======================================================================
# Builders
# ======================================================================


def _comp(name, status="PASS", state="PREARM_BASELINE", at=T, evidence=None, reason=None):
    evidence = {"ok": True} if evidence is None else evidence
    if status == "DEFERRED":
        evidence = {"frozen_definition": "plan section 4", **evidence}
        reason = reason or "BLOCKED ON POST-ARMING PROVIDER PREPARATION"
    if status == "FAIL":
        reason = reason or "failed for a test"
    return rd.Component(component=name, status=status, evidence_state=state, collected_at_utc=at,
                        evidence=evidence, reason=reason)


def _rows(overrides=None, state="PREARM_BASELINE"):
    overrides = overrides or {}
    rows = []
    for row in rd.ROW_DEFS:
        comps = []
        for name in row.components:
            status = "DEFERRED" if name in rd.DEFERRED_COMPONENTS else "PASS"
            comps.append(overrides.get(name) or _comp(name, status, state))
        rows.append(rd.RowEntry(row_id=row.row_id, status=rd.aggregate(c.status for c in comps),
                                components=tuple(comps)))
    return tuple(rows)


def _ci(sha=BASE):
    return rd.CiEvidence(sha=sha, run_id="123", head_sha=sha, conclusion="success")


def _record(overrides=None, **kwargs):
    fields = dict(
        schema_version=1, matrix_version=1, stage="B6-3b", recorded_at_utc=T, base_source_sha=BASE,
        closure=rd.CLOSURE_PENDING, ci=(_ci(),), rows=_rows(overrides), adjudications=(),
    )
    fields.update(kwargs)
    return rd.ReadinessRecord(**fields)


# ======================================================================
# The matrix equals the ADR
# ======================================================================


def _numbered_items(text: str) -> dict[int, str]:
    items: dict[int, str] = {}
    current = None
    for line in text.splitlines():
        match = re.match(r"^(\d+)\. (.+)$", line)
        if match:
            current = int(match.group(1))
            items[current] = match.group(2).strip()
        elif current is not None and line.startswith("    ") and line.strip():
            items[current] += " " + line.strip()
        else:
            current = None
    return items


def _between(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end, text.index(start))]


def test_matrix_titles_equal_the_adr_text_verbatim():
    adr = ADR_PATH.read_text(encoding="utf-8")
    items = {}
    items.update(_numbered_items(_between(adr, "### 22. Fresh replacement readiness matrix", "### 23.")))
    items.update(_numbered_items(_between(adr, "### A9. Readiness matrix additions", "### A10.")))
    items.update(_numbered_items(_between(adr, "### B1. Matrix additions", "### B2.")))
    assert sorted(items) == list(range(1, 26))
    for row in rd.ROW_DEFS:
        assert " ".join(row.title.split()) == " ".join(items[row.row_id].split()), row.row_id


def test_every_row_carries_the_required_definition_fields():
    assert rd.ROW_IDS == tuple(range(1, 26))
    for row in rd.ROW_DEFS:
        assert row.title and row.source and row.tiers
        assert row.locus in {"LOCAL", "GITHUB", "RUNNER", "PROVIDER", "MIXED"}
        assert row.mode in {"MODEL_FREE", "REAL_PROVIDER"}
        assert row.components and len(set(row.components)) == len(row.components)
    assert rd.ROW_BY_ID[4].components == ("4.1", "4.2")
    assert rd.ROW_BY_ID[16].components == tuple(f"16{c}" for c in "abcdefghijkl")
    assert rd.DEFERRED_COMPONENTS == {"4.2", "16h", "16i"} and rd.DEFERRED_ROWS == {4, 16}


def test_aggregate_is_fail_then_deferred_then_pass():
    assert rd.aggregate(["PASS", "PASS"]) == "PASS"
    assert rd.aggregate(["PASS", "DEFERRED"]) == "DEFERRED"
    assert rd.aggregate(["DEFERRED", "FAIL", "PASS"]) == "FAIL"
    with pytest.raises(rd.ReadinessError):
        rd.aggregate([])


# ======================================================================
# Record schema
# ======================================================================


def test_a_valid_record_round_trips_in_canonical_bytes():
    record = _record()
    data = rd.record_bytes(record)
    assert data.endswith(b"\n") and b"\r" not in data
    assert rd.ReadinessRecord.model_validate_json(data) == record
    assert rd.record_bytes(rd.ReadinessRecord.model_validate_json(data)) == data
    assert len(rd.record_sha256(record)) == 64


def test_record_rejects_extra_fields_including_a_b63b_ci_result():
    for extra in ({"b63b_ci": {"conclusion": "success"}}, {"result": "PASS"}, {"closed": True}):
        with pytest.raises(ValidationError):
            _record(**extra)


@pytest.mark.parametrize("closure", ["PASS", "CLOSED", "SUCCESS", ""])
def test_record_cannot_claim_closure_or_pass(closure):
    with pytest.raises(ValidationError):
        _record(closure=closure)


def test_record_requires_exactly_rows_1_to_25_once_in_order():
    rows = list(_rows())
    for bad in (tuple(rows[:-1]), tuple(rows + [rows[0]]), tuple(reversed(rows)), tuple(rows[:3] + rows[4:])):
        with pytest.raises(ValidationError):
            _record(rows=bad)


def test_row_entry_rejects_unknown_rows_missing_components_and_wrong_order():
    with pytest.raises(ValidationError):
        rd.RowEntry(row_id=99, status="PASS", components=(_comp("1"),))
    with pytest.raises(ValidationError):
        rd.RowEntry(row_id=16, status="PASS", components=(_comp("16a"),))
    with pytest.raises(ValidationError):
        rd.RowEntry(row_id=4, status="DEFERRED", components=(_comp("4.2", "DEFERRED"), _comp("4.1")))


def test_an_aggregate_can_never_hide_a_fail():
    comps = tuple(_comp(name, "FAIL" if name == "16a" else ("DEFERRED" if name in rd.DEFERRED_COMPONENTS else "PASS"))
                  for name in rd.ROW_BY_ID[16].components)
    with pytest.raises(ValidationError):
        rd.RowEntry(row_id=16, status="DEFERRED", components=comps)
    with pytest.raises(ValidationError):
        rd.RowEntry(row_id=16, status="PASS", components=comps)
    assert rd.RowEntry(row_id=16, status="FAIL", components=comps).status == "FAIL"


@pytest.mark.parametrize("bad", [
    datetime(2026, 10, 3, 10, 0, 0), datetime(2026, 10, 3, 10, 0, 0, 5, tzinfo=UTC),
    datetime(2026, 10, 3, 11, 0, 0, tzinfo=timezone(timedelta(hours=1))),
])
def test_timestamps_must_be_whole_second_utc_server_time(bad):
    with pytest.raises(ValidationError):
        _comp("1", at=bad)
    with pytest.raises(ValidationError):
        _record(recorded_at_utc=bad)


@pytest.mark.parametrize("evidence", [
    {"x": 1.5}, {"x": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}}, {"x": "y" * 5000},
    {"x": list(range(5000))}, {str(i): i for i in range(300)}, {"": 1}, {"x": object()}, {1: 2},
])
def test_evidence_must_be_bounded_json_safe_data(evidence):
    with pytest.raises(ValidationError):
        _comp("1", evidence=evidence)


def test_status_specific_component_rules():
    with pytest.raises(ValidationError):
        rd.Component(component="1", status="FAIL", evidence_state="T0", collected_at_utc=T, evidence={})
    with pytest.raises(ValidationError):
        _comp("1", "DEFERRED")  # DEFERRED is legal only for 4.2, 16h, 16i
    with pytest.raises(ValidationError):
        rd.Component(component="4.2", status="DEFERRED", evidence_state="PREARM_BASELINE", collected_at_utc=T,
                     evidence={}, reason="blocked")
    with pytest.raises(ValidationError):
        rd.Component(component="4.2", status="DEFERRED", evidence_state="PREARM_BASELINE", collected_at_utc=T,
                     evidence={"frozen_definition": "x"})
    with pytest.raises(ValidationError):
        _comp("zz")


def test_final_t2_evidence_cannot_exist_in_a_b63_record():
    with pytest.raises(ValidationError):
        _comp("16k", state="FINAL_T2")


def test_ci_evidence_names_only_the_b63a_sha_and_only_success():
    with pytest.raises(ValidationError):
        _record(ci=(_ci("b" * 40),))
    with pytest.raises(ValidationError):
        _record(ci=())
    with pytest.raises(ValidationError):
        rd.CiEvidence(sha=BASE, run_id="1", head_sha="b" * 40, conclusion="success")
    with pytest.raises(ValidationError):
        rd.CiEvidence(sha=BASE, run_id="1", head_sha=BASE, conclusion="failure")
    with pytest.raises(ValidationError):
        rd.CiEvidence(sha=BASE, run_id="x", head_sha=BASE, conclusion="success")


def test_a_component_cannot_be_collected_after_the_record():
    late = T + timedelta(seconds=1)
    with pytest.raises(ValidationError):
        _record(overrides={"1": _comp("1", at=late)})


# ======================================================================
# Eligibility: the exact deferred shape, never a closed state
# ======================================================================


def test_the_exact_deferred_shape_is_arming_eligible():
    verdict = rd.evaluate_arming_eligibility(_record())
    assert verdict.eligible is True and verdict.reasons == ()
    assert not hasattr(verdict, "closure") and not hasattr(verdict, "closed")


def test_any_fail_makes_the_record_not_eligible_and_names_it():
    verdict = rd.evaluate_arming_eligibility(_record({"9": _comp("9", "FAIL")}))
    assert not verdict.eligible and any("9" in r for r in verdict.reasons)


def test_a_failing_row_16_subcheck_is_a_row_16_fail_not_eligible():
    record = _record({"16a": _comp("16a", "FAIL")})
    assert {r.row_id: r.status for r in record.rows}[16] == "FAIL"
    assert not rd.evaluate_arming_eligibility(record).eligible


@pytest.mark.parametrize("name", ["4.2", "16h", "16i"])
def test_a_deferred_component_marked_pass_is_not_eligible(name):
    verdict = rd.evaluate_arming_eligibility(_record({name: _comp(name, "PASS")}))
    assert not verdict.eligible
    assert any("deferred components must be exactly" in r for r in verdict.reasons)


def test_row_4_and_row_16_must_be_deferred_never_pass():
    record = _record()
    statuses = {r.row_id: r.status for r in record.rows}
    assert statuses[4] == "DEFERRED" and statuses[16] == "DEFERRED"
    assert all(s == "PASS" for rid, s in statuses.items() if rid not in (4, 16))
    with pytest.raises(ValidationError):  # a row 4 stated PASS over a DEFERRED component is invalid
        rd.RowEntry(row_id=4, status="PASS", components=(_comp("4.1"), _comp("4.2", "DEFERRED")))


def test_transitive_differences_need_a_matching_owner_adjudication():
    drift = {"transitive_differences": [{"package": "httpx", "baseline_version": "0.27.0", "observed_version": "0.28.0"}]}
    overrides = {"16g": _comp("16g", evidence=drift)}
    assert not rd.evaluate_arming_eligibility(_record(overrides)).eligible
    ok = rd.Adjudication(package="httpx", baseline_version="0.27.0", observed_version="0.28.0",
                         decision="ACCEPTED", ruling_ref="owner-ruling-2026-10-03")
    assert rd.evaluate_arming_eligibility(_record(overrides, adjudications=(ok,))).eligible
    stale = rd.Adjudication(package="idna", baseline_version="3.0", observed_version="3.1",
                            decision="ACCEPTED", ruling_ref="owner-ruling-2026-10-03")
    assert not rd.evaluate_arming_eligibility(_record(adjudications=(stale,))).eligible
    wrong_version = ok.model_copy(update={"observed_version": "0.29.0"})
    assert not rd.evaluate_arming_eligibility(_record(overrides, adjudications=(wrong_version,))).eligible


def test_adjudication_schema_is_strict():
    good = dict(package="httpx", baseline_version="1", observed_version="2", decision="ACCEPTED", ruling_ref="ok-ref")
    assert rd.Adjudication(**good).package == "httpx"
    for change in ({"package": "HTTPX!"}, {"observed_version": "1"}, {"ruling_ref": "no spaces allowed"},
                   {"decision": "REJECTED"}, {"extra": 1}):
        with pytest.raises(ValidationError):
            rd.Adjudication(**{**good, **change})
    twice = (rd.Adjudication(**good), rd.Adjudication(**{**good, "observed_version": "3", "ruling_ref": "r2"}))
    with pytest.raises(ValidationError):
        _record(adjudications=twice)


# ======================================================================
# Freshness (D5) and evidence states (R8)
# ======================================================================

R_TIME = datetime(2026, 10, 10, 9, 0, 0, tzinfo=UTC)
A_TIME = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)


def test_prearm_baseline_predating_r_is_valid_in_a_b63_record():
    old = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
    record = _record({"16k": _comp("16k", state="PREARM_BASELINE", at=old)})
    assert rd.evaluate_arming_eligibility(record).eligible


@pytest.mark.parametrize("state,at,ok", [
    ("FINAL_T2", A_TIME - timedelta(hours=2), True),
    ("FINAL_T2", A_TIME - timedelta(hours=2, seconds=1), False),
    ("FINAL_T2", A_TIME, True),
    ("FINAL_T2", A_TIME + timedelta(seconds=1), False),
    ("FINAL_T2", R_TIME - timedelta(seconds=1), False),
    ("PREARM_BASELINE", A_TIME - timedelta(minutes=5), False),
    ("T0", A_TIME - timedelta(minutes=5), False),
])
def test_a_final_t2_requirement_accepts_only_fresh_final_evidence(state, at, ok):
    satisfied, _reason = rd.final_t2_satisfied(
        None, evidence_state=state, collected_at_utc=at, r_commit_time=R_TIME, commit_a_time=A_TIME)
    assert satisfied is ok


def test_final_t2_evidence_predating_r_fails_even_when_inside_two_hours():
    r_time = datetime(2026, 10, 10, 11, 0, 0, tzinfo=UTC)
    satisfied, reason = rd.final_t2_satisfied(
        None, evidence_state="FINAL_T2", collected_at_utc=datetime(2026, 10, 10, 10, 59, 59, tzinfo=UTC),
        r_commit_time=r_time, commit_a_time=A_TIME)
    assert not satisfied and "predates" in reason


def test_pre_dispatch_drift_set_and_the_extended_set_beyond_two_hours():
    base = rd.pre_dispatch_required_facts(A_TIME, A_TIME + timedelta(hours=2))
    assert base == rd.PRE_DISPATCH_FACTS and len(base) == 5
    extended = rd.pre_dispatch_required_facts(A_TIME, A_TIME + timedelta(hours=2, seconds=1))
    assert extended == rd.PRE_DISPATCH_FACTS + rd.PRE_DISPATCH_EXTENDED_FACTS
    with pytest.raises(rd.ReadinessError):
        rd.pre_dispatch_required_facts(A_TIME, A_TIME - timedelta(seconds=1))


def test_pre_dispatch_drift_requires_every_fact_present_and_equal():
    before = {k: "h" for k in rd.PRE_DISPATCH_FACTS}
    assert rd.evaluate_pre_dispatch_drift(before, dict(before), rd.PRE_DISPATCH_FACTS) == (True, ())
    changed = dict(before, latch_state="other")
    assert rd.evaluate_pre_dispatch_drift(before, changed, rd.PRE_DISPATCH_FACTS) == (False, ("latch_state",))
    missing = {k: v for k, v in before.items() if k != "scheduler_state"}
    ok, problems = rd.evaluate_pre_dispatch_drift(before, missing, rd.PRE_DISPATCH_FACTS)
    assert not ok and problems == ("scheduler_state",)


# ======================================================================
# A8: lifecycle, environment, resolved model keys (R6)
# ======================================================================


def _lifecycle(**changes):
    snapshot = {
        "read_at_utc": T.isoformat(), "source_url": "https://platform.claude.com/docs/en/about-claude/model-deprecations",
        "models": [
            {"model": "claude-sonnet-5", "state": "Active", "deprecated": "N/A", "deprecation_notice": False,
             "tentative_retirement_not_sooner_than": "2027-06-30"},
            {"model": "claude-haiku-4-5-20251001", "state": "Active", "deprecated": "N/A", "deprecation_notice": False,
             "tentative_retirement_not_sooner_than": "2026-10-15"},
        ],
    }
    snapshot.update(changes)
    return snapshot


def test_model_lifecycle_passes_when_both_models_are_active_without_notice():
    assert rd.evaluate_model_lifecycle(_lifecycle(), now=T).status == "PASS"


@pytest.mark.parametrize("change", [
    {"state": "Legacy"}, {"state": "Deprecated"}, {"state": "Retired"}, {"deprecation_notice": True},
    {"deprecated": "2026-09-30"}, {"deprecation_notice": None},
])
def test_model_lifecycle_change_fails_for_either_model(change):
    for index in (0, 1):
        snapshot = _lifecycle()
        snapshot["models"][index] = {**snapshot["models"][index], **change}
        assert rd.evaluate_model_lifecycle(snapshot, now=T).status == "FAIL"


@pytest.mark.parametrize("snapshot", [
    _lifecycle(models=[]), _lifecycle(models=_lifecycle()["models"][:1]),
    _lifecycle(models=_lifecycle()["models"] + [{"model": "claude-opus-5", "state": "Active", "deprecated": "N/A",
                                                 "deprecation_notice": False}]),
    _lifecycle(models=_lifecycle()["models"] + _lifecycle()["models"][:1]),
    _lifecycle(read_at_utc=(T + timedelta(seconds=1)).isoformat()), _lifecycle(read_at_utc="not a time"),
    _lifecycle(read_at_utc="2026-10-03T10:00:00"), _lifecycle(source_url="http://example.invalid"),
    {"models": []}, _lifecycle(models="x"), _lifecycle(models=["x"]),
], ids=["empty", "one-model", "unknown-extra", "duplicate", "future", "garbled", "naive", "not-https", "missing-keys",
        "models-not-list", "entry-not-object"])
def test_model_lifecycle_malformed_or_inconsistent_snapshots_fail_closed(snapshot):
    assert rd.evaluate_model_lifecycle(snapshot, now=T).status == "FAIL"


@pytest.mark.parametrize("name", list(rd.PROHIBITED_OVERRIDE_ENV) + list(rd.STATIC_CREDENTIAL_ENV)
                         + ["ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_BASE_URL"])
def test_any_override_credential_or_provider_variable_name_fails_the_model_free_lane(name):
    outcome = rd.evaluate_env_names(["PATH", "GITHUB_JOB", name])
    assert outcome.status == "FAIL" and name in outcome.evidence["present"]


def test_environment_without_provider_names_passes_and_text_scan_finds_override_names():
    assert rd.evaluate_env_names(["PATH", "GITHUB_JOB", "RUNNER_NAME"]).status == "PASS"
    assert rd.scan_text_for_override_names("env:\n  ANTHROPIC_MODEL: x\n") == ["ANTHROPIC_MODEL"]
    assert rd.scan_text_for_override_names("ANTHROPIC_MODELS_ARE_NOT_A_NAME") == []
    workflow = (REPO_ROOT / ".github" / "workflows" / "sentinel-official-gate.yml").read_text(encoding="utf-8")
    assert rd.scan_text_for_override_names(workflow) == []


SONNET, HAIKU = "claude-sonnet-5", "claude-haiku-4-5-20251001"


def test_resolved_model_keys_contract_passes_for_the_b5_observed_shapes():
    entries = [[SONNET]] * 2 + [[HAIKU, SONNET]] * 22
    assert rd.check_resolved_model_keys(entries, invocation_count=24).status == "PASS"
    assert rd.check_resolved_model_keys([(SONNET,)], invocation_count=1).status == "PASS"


@pytest.mark.parametrize("entries,count", [
    ([[HAIKU]], 1),  # required primary absent
    ([[SONNET, "claude-opus-5"]], 1),  # unauthorized key
    ([[SONNET], []], 2),  # empty set
    ([[SONNET], rd.RESOLVED_MODEL_UNAVAILABLE], 2),  # unavailable
    ([[SONNET]], 2),  # capture does not cover every invocation
    ([[SONNET], [SONNET]], 1),  # more captures than invocations
    ([[SONNET, 5]], 1),  # non-string key
    ([[SONNET], None], 2),  # missing entry
    ([[SONNET]], 0), ([[SONNET]], True), ([], 0),
])
def test_resolved_model_keys_contract_fails_closed(entries, count):
    assert rd.check_resolved_model_keys(entries, invocation_count=count).status == "FAIL"


# ======================================================================
# Runtime drift (D3, R7)
# ======================================================================


def _identity(**changes):
    doc = {
        "python_version": "3.12.14", "runner_image": {"image_version": "20260927.320.1"},
        "sdk": {"version": "0.2.110", "record_sha256": "r" * 64, "transport_module": {"sha256_actual": "t" * 64},
                "bundled_cli": {"sha256_actual": "c" * 64, "cli_version_declared": "2.1.191"}},
        "distributions": [["anyio", "4.14.0"], ["certifi", "2026.6.17"], ["claude-agent-sdk", "0.2.110"],
                          ["httpx", "0.27.0"], ["idna", "3.10"], ["pydantic", "2.13.4"], ["pyyaml", "6.0.3"]],
    }
    doc.update(changes)
    return doc


PINS = {"anyio": "4.14.0", "certifi": "2026.6.17", "claude-agent-sdk": "0.2.110", "pydantic": "2.13.4", "pyyaml": "6.0.3"}


def _dists(**replace):
    base = dict(_identity()["distributions"])
    for name, version in replace.items():
        key = name.replace("_", "-")
        if version is None:
            base.pop(key, None)
        else:
            base[key] = version
    return [[k, v] for k, v in sorted(base.items())]


def _sdk(**changes):
    sdk = copy.deepcopy(_identity()["sdk"])
    for key, value in changes.items():
        if key == "transport":
            sdk["transport_module"]["sha256_actual"] = value
        elif key == "cli_sha":
            sdk["bundled_cli"]["sha256_actual"] = value
        elif key == "cli_version":
            sdk["bundled_cli"]["cli_version_declared"] = value
        else:
            sdk[key] = value
    return sdk


def test_identical_runtime_has_no_drift():
    c = rd.classify_runtime_drift(_identity(), _identity(), direct_pins=PINS)
    assert (c.fatal, c.residuals, c.transitive) == ((), (), ())
    assert rd.evaluate_runtime_drift(c, ()).status == "PASS"


@pytest.mark.parametrize("current,label", [
    (_identity(sdk=_sdk(version="0.2.111")), "SDK_VERSION"),
    (_identity(sdk=_sdk(record_sha256="x" * 64)), "SDK_RECORD_DIGEST"),
    (_identity(sdk=_sdk(transport="x" * 64)), "TRANSPORT_DIGEST"),
    (_identity(sdk=_sdk(cli_sha="x" * 64)), "CLI_DIGEST"),
    (_identity(sdk=_sdk(cli_version="2.1.192")), "CLI_VERSION"),
    (_identity(python_version="3.13.0"), "PYTHON_MINOR"),
    (_identity(python_version="3.11.9"), "PYTHON_MINOR"),
    (_identity(distributions=_dists(pydantic="2.13.5")), "DIRECT_PIN pydantic"),
    (_identity(distributions=_dists(anyio=None)), "DIRECT_PIN anyio"),
    (None, "IDENTITY_CAPTURE_FAILED"),
    ({"sdk": {}}, "IDENTITY_CAPTURE_FAILED"),
])
def test_fatal_runtime_drift_is_a_stop_that_adjudication_cannot_waive(current, label):
    c = rd.classify_runtime_drift(_identity(), current, direct_pins=PINS)
    assert any(label in f for f in c.fatal), c.fatal
    adjudications = [rd.Adjudication(package="idna", baseline_version="3.10", observed_version="3.11",
                                     decision="ACCEPTED", ruling_ref="r1")]
    assert rd.evaluate_runtime_drift(c, adjudications).status == "FAIL"


def test_runner_image_and_python_patch_are_recorded_residuals_not_a_stop():
    current = _identity(python_version="3.12.15", runner_image={"image_version": "20261002.1.1"})
    c = rd.classify_runtime_drift(_identity(), current, direct_pins=PINS)
    assert c.fatal == () and len(c.residuals) == 2 and c.transitive == ()
    outcome = rd.evaluate_runtime_drift(c, ())
    assert outcome.status == "PASS" and len(outcome.evidence["residuals"]) == 2


@pytest.mark.parametrize("name", ["httpx", "idna", "pip", "setuptools", "wheel"])
def test_every_transitive_difference_requires_owner_adjudication_even_tooling_packages(name):
    changed = _identity(distributions=_dists(**{name: "9.9.9"}))
    c = rd.classify_runtime_drift(_identity(distributions=_dists(**{name: "1.0.0"})), changed, direct_pins=PINS)
    assert [d.package for d in c.transitive] == [name]
    assert rd.evaluate_runtime_drift(c, ()).status == "FAIL"
    ruling = rd.Adjudication(package=name, baseline_version="1.0.0", observed_version="9.9.9",
                             decision="ACCEPTED", ruling_ref="owner-ruling-1")
    assert rd.evaluate_runtime_drift(c, [ruling]).status == "PASS"


def test_a_transitive_addition_or_removal_is_a_difference_too():
    added = rd.classify_runtime_drift(_identity(), _identity(distributions=_dists(newpkg="1.0")), direct_pins=PINS)
    removed = rd.classify_runtime_drift(_identity(), _identity(distributions=_dists(idna=None)), direct_pins=PINS)
    assert [(d.package, d.baseline_version, d.observed_version) for d in added.transitive] == [("newpkg", None, "1.0")]
    assert [(d.package, d.baseline_version, d.observed_version) for d in removed.transitive] == [("idna", "3.10", None)]


def test_a_stale_or_partial_adjudication_does_not_cover_a_difference():
    c = rd.classify_runtime_drift(_identity(), _identity(distributions=_dists(httpx="0.28.0")), direct_pins=PINS)
    stale = rd.Adjudication(package="httpx", baseline_version="0.27.0", observed_version="0.27.5",
                            decision="ACCEPTED", ruling_ref="r1")
    assert rd.evaluate_runtime_drift(c, [stale]).status == "FAIL"


def test_malformed_baseline_is_a_hard_error_and_direct_pins_parse_strictly():
    with pytest.raises(rd.ReadinessError):
        rd.classify_runtime_drift({"sdk": {}}, _identity(), direct_pins=PINS)
    assert rd.parse_direct_pins("a==1.0\n# c\nPyYAML==6.0.3  # x\n-r other\n") == {"a": "1.0", "pyyaml": "6.0.3"}
    with pytest.raises(rd.ReadinessError):
        rd.parse_direct_pins("a>=1.0\n")
    assert rd.parse_direct_pins((REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")) == PINS


# ======================================================================
# Carry-forward (D4)
# ======================================================================


def _b2_facts(**changes):
    facts = {
        "artifacts": {aid: {"digest": digest, "expired": False} for _n, aid, digest in rd.B2_ARTIFACT_DIGESTS},
        "path_diff": [], "workflow_diff_lines": list(rd.ALLOWED_WORKFLOW_DIFF_LINES),
    }
    facts.update(changes)
    return facts


def test_carry_forward_passes_when_digests_paths_and_the_timeout_line_all_hold():
    outcome = rd.evaluate_carry_forward(rd.CARRY_SPECS["b2"], _b2_facts())
    assert outcome.status == "PASS"
    assert set(outcome.evidence["artifacts"].values()) == {"VERIFIED"}


def test_carry_forward_digest_mismatch_or_unchecked_artifact_invalidates():
    first_id = rd.B2_ARTIFACT_DIGESTS[0][1]
    bad = _b2_facts()
    bad["artifacts"] = {**bad["artifacts"], first_id: {"digest": "sha256:" + "0" * 64, "expired": False}}
    assert rd.evaluate_carry_forward(rd.CARRY_SPECS["b2"], bad).status == "FAIL"
    missing = _b2_facts()
    missing["artifacts"] = {k: v for k, v in missing["artifacts"].items() if k != first_id}
    assert rd.evaluate_carry_forward(rd.CARRY_SPECS["b2"], missing).status == "FAIL"


def test_an_expired_artifact_rests_on_the_durable_digest_and_is_recorded_as_such():
    facts = _b2_facts()
    facts["artifacts"] = {k: {"digest": None, "expired": True} for k in facts["artifacts"]}
    outcome = rd.evaluate_carry_forward(rd.CARRY_SPECS["b2"], facts)
    assert outcome.status == "PASS" and set(outcome.evidence["artifacts"].values()) == {"EXPIRED_DURABLE_DIGEST_ONLY"}


@pytest.mark.parametrize("key", ["wif", "b2", "b2files", "b5", "pc"])
def test_any_path_change_in_the_frozen_set_invalidates_the_row(key):
    spec = rd.CARRY_SPECS[key]
    facts = {"artifacts": {aid: {"digest": d, "expired": False} for _n, aid, d in spec.artifacts},
             "path_diff": [spec.path_set[0] + "/x.py" if "/" not in spec.path_set[0] else spec.path_set[0]],
             "workflow_diff_lines": list(rd.ALLOWED_WORKFLOW_DIFF_LINES)}
    assert rd.evaluate_carry_forward(spec, facts).status == "FAIL"


@pytest.mark.parametrize("lines", [
    [], ["-    timeout-minutes: 30"], list(rd.ALLOWED_WORKFLOW_DIFF_LINES) + ["+    name: gate2"],
    ["-    timeout-minutes: 30", "+    timeout-minutes: 107"], ["+    timeout-minutes: 106"], None,
])
def test_the_official_workflow_diff_must_be_exactly_the_allowed_timeout_change(lines):
    facts = _b2_facts(workflow_diff_lines=lines)
    assert rd.evaluate_carry_forward(rd.CARRY_SPECS["b2"], facts).status == "FAIL"


def test_rows_that_do_not_rely_on_the_workflow_ignore_its_diff_and_malformed_facts_fail():
    spec = rd.CARRY_SPECS["b5"]
    facts = {"artifacts": {aid: {"digest": d, "expired": False} for _n, aid, d in spec.artifacts}, "path_diff": []}
    assert rd.evaluate_carry_forward(spec, facts).status == "PASS"
    assert rd.evaluate_carry_forward(spec, {"artifacts": "x"}).status == "FAIL"
    assert rd.evaluate_carry_forward(spec, {**facts, "path_diff": None}).status == "FAIL"


# ======================================================================
# Quality surface, envelope, latch row
# ======================================================================


def _quality(**changes):
    facts = {
        "changed": {"agents/checker/envelope_guard.py": "A", "agents/checker/process_control.py": "A",
                    "agents/checker/oidc.py": "M", "agents/checker/harness.py": "M"},
        "diff_sha256": {p: v[1] for p, v in rd.QUALITY_ALLOWED_DIFFS.items() if v[1]},
        "frozen_manifest_identical": True, "runner_never_overrides_attempts": True, "model_literal_pinned": True,
    }
    facts.update(changes)
    return facts


def test_quality_surface_passes_only_for_the_allowlisted_reviewed_changes():
    assert rd.evaluate_quality_surface(_quality()).status == "PASS"
    assert rd.evaluate_quality_surface(_quality(changed={}, diff_sha256={})).status == "PASS"


@pytest.mark.parametrize("change", [
    {"changed": {**_quality()["changed"], "agents/checker/prompts.py": "M"}},
    {"changed": {**_quality()["changed"], "fixtures/x.json": "M"}},
    {"changed": {**_quality()["changed"], "agents/checker/process_control.py": "M"}},
    {"diff_sha256": {**_quality()["diff_sha256"], "agents/checker/harness.py": "0" * 64}},
    {"frozen_manifest_identical": False}, {"runner_never_overrides_attempts": False}, {"model_literal_pinned": None},
    {"changed": None}, {"diff_sha256": None},
])
def test_quality_surface_drift_or_unreviewed_hunks_fail(change):
    assert rd.evaluate_quality_surface(_quality(**change)).status == "FAIL"


def _envelope(**changes):
    env = {"envelope_id": rd.EXPECTED_ENVELOPE_ID, "envelope_version": "1", "max_observed_ms": 38021,
           "outer_seconds": 6327, "workflow_timeout_minutes": 106, "session_duration_s": 5847, "stall_budget_ms": 600000}
    env.update(changes)
    return env


def test_envelope_passes_for_the_frozen_values_and_the_bound_workflow_timeout():
    assert rd.evaluate_envelope(_envelope(), workflow_timeout_minutes=106).status == "PASS"


@pytest.mark.parametrize("change", [
    {"envelope_id": "0" * 64}, {"envelope_version": "2"}, {"stall_budget_ms": 380210}, {"outer_seconds": 6328},
    {"session_duration_s": 5848}, {"workflow_timeout_minutes": 105}, {"max_observed_ms": "x"}, {"max_observed_ms": None},
])
def test_envelope_drift_fails(change):
    assert rd.evaluate_envelope(_envelope(**change), workflow_timeout_minutes=106).status == "FAIL"


@pytest.mark.parametrize("workflow_minutes", [105, 30, None])
def test_the_official_workflow_timeout_must_equal_the_envelope(workflow_minutes):
    assert rd.evaluate_envelope(_envelope(), workflow_timeout_minutes=workflow_minutes).status == "FAIL"


def _latch(**changes):
    facts = {
        "kinds": ["GENESIS"], "file_sha256": rd.EXPECTED_LATCH_FILE_SHA256, "head_sha256": rd.EXPECTED_LATCH_HEAD_SHA256,
        "unarmed_refusal_reason": "LATCH_UNARMED", "eligibility_requires_latch": True,
        "preflight_consults_latch_in_order": True, "enforcement_tests_green": True, "history_permits_exactly_one": True,
        "official_run_numbers": [1, 2, 3, 4], "replacement_prefix_artifacts": 0, "gate_evidence_prefix_artifacts": 0,
        "replacement_receipts": 0, "purpose_is_original": True, "envelope_is_none": True,
    }
    facts.update(changes)
    return facts


def test_latch_row_passes_for_the_committed_unarmed_genesis():
    assert rd.evaluate_latch_row(_latch()).status == "PASS"


@pytest.mark.parametrize("change", [
    {"kinds": ["GENESIS", "ATTEMPT_AUTHORIZED"]}, {"kinds": []}, {"file_sha256": "0" * 64}, {"head_sha256": "0" * 64},
    {"unarmed_refusal_reason": None}, {"eligibility_requires_latch": False},
    {"preflight_consults_latch_in_order": False}, {"enforcement_tests_green": False},
    {"history_permits_exactly_one": False}, {"official_run_numbers": [1, 2, 3, 4, 5]}, {"official_run_numbers": [1, 2, 3]},
    {"replacement_prefix_artifacts": 1}, {"gate_evidence_prefix_artifacts": 1}, {"replacement_receipts": 1},
    {"purpose_is_original": False}, {"envelope_is_none": False},
])
def test_latch_row_drift_or_replacement_activity_fails(change):
    assert rd.evaluate_latch_row(_latch(**change)).status == "FAIL"


def test_latch_row_with_incomplete_facts_fails_closed():
    facts = _latch()
    del facts["kinds"]
    assert rd.evaluate_latch_row(facts).status == "FAIL"


# ======================================================================
# Deferred provider components: frozen predicates, evaluated later
# ======================================================================


def _rule_readback(**changes):
    readback = {
        "rule": {**copy.deepcopy(dict(rd.FROZEN_RULE_EXPECTATION)), "id": "fdrl_new"},
        "other_rules": [{"id": "fdrl_old", "status": "Archived", "claims": {"workflow_ref": rd.FROZEN_RULE_EXPECTATION["claims"]["workflow_ref"]}}],
        "original_rules_archived": {"P5C": "Archived", "P5D_ORIGINAL": "Archived", "TIMING": "Archived"},
        "auth_events_for_rule": 0, "variable_value": "fdrl_new",
        "oidc_customization": {"use_default": True, "use_immutable_subject": False},
    }
    readback.update(changes)
    return readback


def test_replacement_rule_passes_only_for_the_frozen_configuration():
    assert rd.evaluate_replacement_rule(_rule_readback()).status == "PASS"


@pytest.mark.parametrize("field,value", [
    ("status", "Archived"), ("issuer", "other"), ("check_jti", False), ("service_account", "x"),
    ("workspaces", ["sentinel", "default"]), ("applies_to_all_workspaces", True), ("scope", "org:admin"),
    ("token_lifetime_seconds", 3600), ("audience", "https://example.invalid"),
    ("subject_prefix", "repo:kobescak-kristian/ai-portfolio-sentinel:*"), ("cel_condition", "true"),
    ("claims", {"repository_owner": "x"}),
])
def test_replacement_rule_field_mismatches_fail(field, value):
    readback = _rule_readback()
    readback["rule"] = {**readback["rule"], field: value}
    assert rd.evaluate_replacement_rule(readback).status == "FAIL"


@pytest.mark.parametrize("change", [
    {"other_rules": [{"id": "x", "status": "Active", "claims": {"workflow_ref": rd.FROZEN_RULE_EXPECTATION["claims"]["workflow_ref"]}}]},
    {"other_rules": [{"id": "x", "status": "Active", "claims": {}}]},
    {"other_rules": "x"}, {"other_rules": ["x"]},
    {"original_rules_archived": {"P5C": "Archived", "P5D_ORIGINAL": "Active", "TIMING": "Archived"}},
    {"auth_events_for_rule": 1}, {"variable_value": "fdrl_old"},
    {"oidc_customization": {"use_default": False, "use_immutable_subject": False}}, {"rule": None},
])
def test_replacement_rule_context_violations_fail(change):
    assert rd.evaluate_replacement_rule(_rule_readback(**change)).status == "FAIL"


def test_a_second_active_rule_for_an_unrelated_workflow_is_allowed():
    other = {"id": "x", "status": "Active", "claims": {"workflow_ref": "owner/repo/.github/workflows/other.yml@refs/heads/main"}}
    assert rd.evaluate_replacement_rule(_rule_readback(other_rules=[other])).status == "PASS"


def _cap(**changes):
    readback = {
        "month_to_date_spend_usd": "0.00", "headroom_eur": "2.50", "granularity_usd": "1.00", "cap_usd": "9.00",
        "fx_usd_per_eur": "1.1355", "fx_retrieved_at_utc": (T - timedelta(minutes=30)).isoformat(),
        "currency": "USD", "period": "calendar-month-utc",
    }
    readback.update(changes)
    return readback


def test_cap_must_be_the_smallest_supported_value_covering_s_plus_e_plus_h():
    # (0 + 5 + 2.5) EUR x 1.1355 = 8.51625 USD, so the smallest $1.00-granular value is $9.00
    assert rd.evaluate_cap(_cap(), now=T).status == "PASS"
    assert rd.evaluate_cap(_cap(cap_usd="8.00"), now=T).status == "FAIL"  # below the requirement
    assert rd.evaluate_cap(_cap(cap_usd="12.00"), now=T).status == "FAIL"  # not the smallest
    assert rd.evaluate_cap(_cap(month_to_date_spend_usd="0.50", cap_usd="10.00"), now=T).status == "PASS"


@pytest.mark.parametrize("change", [
    {"cap_usd": "60.00", "month_to_date_spend_usd": "55.00"},  # exceeds the EUR 50 ceiling
    {"fx_retrieved_at_utc": (T - timedelta(hours=2, seconds=1)).isoformat()},
    {"fx_retrieved_at_utc": (T + timedelta(seconds=1)).isoformat()},
    {"currency": "EUR"}, {"period": "rolling-30d"}, {"granularity_usd": "0"}, {"fx_usd_per_eur": "-1"},
    {"cap_usd": "abc"}, {"headroom_eur": "-1"}, {"month_to_date_spend_usd": None},
])
def test_cap_ceiling_staleness_units_and_malformed_inputs_fail(change):
    assert rd.evaluate_cap(_cap(**change), now=T).status == "FAIL"


def test_cap_readback_missing_keys_fails_closed():
    broken = _cap()
    del broken["cap_usd"]
    assert rd.evaluate_cap(broken, now=T).status == "FAIL"


# ======================================================================
# Probe evidence schema and row 24
# ======================================================================

PROBE_SHA = "d" * 40


def _checks(**details):
    base = {
        "server_time": {"monotonic": True, "reads": 3},
        "current_run": {"run_number": 5, "status": "in_progress"},
        "official_listing": {"run_numbers": [1, 2, 3, 4], "entries": 4},
        "pagination": {"pages": 5, "total_count": 87, "entries": 87, "per_page": 20},
        "attempt_jobs": {"jobs": 1, "job_name": "gate", "has_started_at": True},
        "job_steps": {"own_steps": 8, "run_3_marker": "skipped", "run_3_execute": "skipped", "run_4_marker": "success"},
        "anchor": {"resolved": True, "api_job_name": "gate"},
        "commit": {"parents": 1, "files": 3, "with_patch": 3},
        "push_activity": {"entries": 94, "matching": 1},
        "artifact_discovery": {"gate_evidence_prefix_count": 0, "oneshot_prefix_count": 2, "replacement_marker_count": 0},
        "original_marker": {"found": 1, "markers_discovered": 2, "expired_covered_by_receipt": False},
        "layout": {"journal_established": True},
        "import_closure": {"imported": True, "third_party_modules": 20},
        "env_scan": {"flagged": [], "env_names": 80},
        "runtime_identity": {"runtime_identity_id": "i" * 64, "sdk_pin_matches": True, "distribution_count": 33},
    }
    base.update(details)
    return tuple(rd.ProbeCheck(name=name, ok=True, detail=detail) for name, detail in base.items())


def _probe(**changes):
    fields = dict(
        schema_version=1, lane="latch-read-probe", run_id="50005", run_attempt=1, event="workflow_dispatch",
        ref="refs/heads/main", sha=PROBE_SHA, workflow_identity=rd.PROBE_WORKFLOW, result="PASS", stop_reason=None,
        server_time_first_utc=T.isoformat(), server_time_last_utc=(T + timedelta(minutes=1)).isoformat(),
        checks=_checks(), runtime_identity=_identity(),
    )
    fields.update(changes)
    return rd.ProbeEvidence(**fields)


def test_probe_evidence_round_trips_and_row_24_passes_for_the_complete_probe():
    probe = _probe()
    assert rd.ProbeEvidence.model_validate_json(probe.model_dump_json()) == probe
    outcome = rd.evaluate_probe_for_row24(probe, expected_sha=PROBE_SHA)
    assert outcome.status == "PASS" and outcome.evidence["pages"] == 5


def _replace_check(name, ok=True, **detail):
    old = {c.name: c for c in _checks()}
    old[name] = rd.ProbeCheck(name=name, ok=ok, detail=detail or old[name].detail)
    return tuple(old.values())


@pytest.mark.parametrize("make,why", [
    (lambda: _probe(result="STOP", stop_reason="x"), "did not complete"),
    (lambda: _probe(sha="e" * 40), "identity"),
    (lambda: _probe(run_attempt=2), "identity"),
    (lambda: _probe(event="push"), "identity"),
    (lambda: _probe(ref="refs/heads/other"), "identity"),
    (lambda: _probe(workflow_identity=rd.OFFICIAL_WORKFLOW), "workflow"),
    (lambda: _probe(checks=_replace_check("commit", ok=False)), "commit"),
    (lambda: _probe(checks=_replace_check("anchor", ok=False, resolved=True)), "anchor"),
    (lambda: _probe(checks=_replace_check("pagination", pages=1, total_count=87, entries=87)), "pagination"),
    (lambda: _probe(checks=_replace_check("pagination", pages=5, total_count=87, entries=80)), "pagination"),
    (lambda: _probe(checks=_replace_check("official_listing", run_numbers=[1, 2, 3, 4, 5], entries=5)), "listing"),
    (lambda: _probe(checks=_replace_check("server_time", monotonic=False, reads=3)), "monotonic"),
    (lambda: _probe(checks=_replace_check("anchor", resolved=False)), "anchor"),
])
def test_row_24_fails_for_any_missing_or_unproven_surface(make, why):
    outcome = rd.evaluate_probe_for_row24(make(), expected_sha=PROBE_SHA)
    assert outcome.status == "FAIL" and why in (outcome.reason or "")


def test_probe_schema_is_strict():
    with pytest.raises(ValidationError):
        _probe(checks=_checks()[:-1])  # a PASS probe must carry every required check
    with pytest.raises(ValidationError):
        _probe(checks=_checks() + (rd.ProbeCheck(name="env_scan", ok=True, detail={}),))  # repeated
    with pytest.raises(ValidationError):
        rd.ProbeCheck(name="not-a-check", ok=True, detail={})
    with pytest.raises(ValidationError):
        rd.ProbeCheck(name="layout", ok=True, detail={"x": 1.5})
    with pytest.raises(ValidationError):
        _probe(result="STOP", stop_reason=None)
    with pytest.raises(ValidationError):
        _probe(result="PASS", stop_reason="x")
    with pytest.raises(ValidationError):
        _probe(sha="XYZ")
    with pytest.raises(ValidationError):
        _probe(extra_field=1)
    stop = _probe(result="STOP", stop_reason="failed checks: commit", checks=_checks()[:3])
    assert rd.evaluate_probe_for_row24(stop, expected_sha=PROBE_SHA).status == "FAIL"


def test_probe_single_check_evidence_for_other_rows():
    probe = _probe()
    assert rd.evaluate_probe_check(probe, "layout", expected_sha=PROBE_SHA).status == "PASS"
    assert rd.evaluate_probe_check(probe, "layout", expected_sha="e" * 40).status == "FAIL"
    failed = _probe(result="STOP", stop_reason="x", checks=_checks()[:2])
    assert rd.evaluate_probe_check(failed, "server_time", expected_sha=PROBE_SHA).status == "FAIL"
    missing = _probe(result="STOP", stop_reason="x", checks=_checks()[:1])
    assert rd.evaluate_probe_check(missing, "layout", expected_sha=PROBE_SHA).status == "FAIL"
    assert rd.PROBE_REQUIRED_CHECKS == tuple(dict.fromkeys(rd.PROBE_REQUIRED_CHECKS))
    assert set(rd.ROW24_CHECKS) <= set(rd.PROBE_REQUIRED_CHECKS)


# ======================================================================
# Write-set proofs (R10)
# ======================================================================

THREE = list(rd.B63B_PATHS)


def test_precommit_write_set_must_equal_exactly_the_three_paths():
    rd.assert_precommit_write_set(THREE, [], [])
    rd.assert_precommit_write_set([], THREE[:1], THREE[1:])
    rd.assert_precommit_write_set(THREE, [], THREE)
    for dirty, untracked, staged in ((THREE + ["x.py"], [], []), (THREE[:2], [], []), ([], ["x.py"], THREE),
                                     ([], [], THREE + ["tests/x.py"]), ([], [], [])):
        with pytest.raises(rd.ReadinessError):
            rd.assert_precommit_write_set(dirty, untracked, staged)


def test_postcommit_write_set_commit_count_and_parent_are_all_checked():
    rd.assert_postcommit_write_set(THREE, 1, BASE, BASE)
    for names, count, parent in ((THREE + ["x"], 1, BASE), (THREE[:2], 1, BASE), (THREE, 2, BASE), (THREE, 0, BASE),
                                 (THREE, 1, "b" * 40), (THREE + THREE[:1], 1, BASE)):
        with pytest.raises(rd.ReadinessError):
            rd.assert_postcommit_write_set(names, count, parent, BASE)


# ======================================================================
# Frozen constants equal the committed artifacts
# ======================================================================


def _committed_genesis_view(directory: Path) -> Path:
    """A-tolerance (Stage 2C-B6-6a, ADR-0012 Amendment C): the committed
    latch's GENESIS line alone; commit A appends one line and cannot change
    tests. GENESIS-only at R is enforced by row 25 of the FINAL_T2 record."""
    latch = REPO_ROOT / "artifacts" / "phase5_replacement_latch.jsonl"
    first = latch.read_bytes().replace(b"\r\n", b"\n").split(b"\n", 1)[0] + b"\n"
    path = directory / "committed_genesis_view.jsonl"
    path.write_bytes(first)
    return path


def test_frozen_constants_equal_the_committed_artifacts(tmp_path):
    import hashlib

    def sha(rel):
        return hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest()

    assert sha("artifacts/phase5_receipt_registry.jsonl") == rd.EXPECTED_REGISTRY_SHA256
    assert sha("artifacts/phase5_a8_model_binding.json") == rd.EXPECTED_A8_BINDING_SHA256
    assert sha("artifacts/phase5_execution_envelope.json") == rd.EXPECTED_ENVELOPE_ID
    view = _committed_genesis_view(tmp_path)
    assert hashlib.sha256(view.read_bytes()).hexdigest() == rd.EXPECTED_LATCH_FILE_SHA256
    from sentinel.phase5.latch import load_latch, record_sha256
    assert record_sha256(load_latch(REPO_ROOT / "artifacts/phase5_replacement_latch.jsonl")[0]) == rd.EXPECTED_LATCH_HEAD_SHA256
    a8 = json.loads((REPO_ROOT / "artifacts/phase5_a8_model_binding.json").read_text(encoding="utf-8"))
    assert set(a8["allowed_model_keys"]) == set(rd.ALLOWED_MODEL_KEYS)
    assert a8["required_primary_model"] == rd.REQUIRED_PRIMARY_MODEL
    assert set(a8["go_stop_rule"]["stop_if_env_override_set"]) == set(rd.PROHIBITED_OVERRIDE_ENV)
    assert a8["execution_envelope_id"] == rd.EXPECTED_ENVELOPE_ID
    from sentinel.phase5.execution_envelope import REHEARSAL_SDK_PIN
    assert REHEARSAL_SDK_PIN == f"claude-agent-sdk=={rd.SDK_VERSION}"


def _git_has(sha: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=REPO_ROOT, capture_output=True).returncode == 0


@pytest.mark.skipif(not _git_has(rd.ORIGINAL_RUN_SOURCE_SHA), reason="full history not available in this checkout")
def test_quality_allowlist_hunk_hashes_equal_the_reviewed_git_diffs():
    import hashlib

    for path, (status, expected) in rd.QUALITY_ALLOWED_DIFFS.items():
        out = subprocess.run(["git", "diff", "--no-color", rd.ORIGINAL_RUN_SOURCE_SHA, "HEAD", "--", path],
                             cwd=REPO_ROOT, capture_output=True, check=True).stdout
        if expected is not None:
            assert hashlib.sha256(out).hexdigest() == expected, path
        names = subprocess.run(["git", "diff", "--name-status", rd.ORIGINAL_RUN_SOURCE_SHA, "HEAD", "--", path],
                               cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
        assert names.split("\t")[0].strip() == status, path


# ======================================================================
# Collector (scripts/run_phase5_readiness.py)
# ======================================================================

script = _load_script()
RUNNER_TEXT = (REPO_ROOT / "scripts" / "run_phase5_official_gate.py").read_text(encoding="utf-8")
COMMON_TEXT = (REPO_ROOT / "scripts" / "_phase5_common.py").read_text(encoding="utf-8")


_ARMED_PURPOSE = 'PURPOSE = "P5D_REPLACEMENT_SONNET_GATE"'
_ORIGINAL_PURPOSE = 'PURPOSE = "P5D_OFFICIAL_SONNET_GATE"'
_ARMED_ENVELOPE = re.compile(r'ENVELOPE: "EnvelopeIdentity \| None" = EnvelopeIdentity\(.*?\n\)', re.S)
_UNARMED_ENVELOPE = 'ENVELOPE: "EnvelopeIdentity | None" = None'


def _unarmed_runner_twin(text: str) -> str:
    """The B6-3-era unarmed runner, derived from the committed armed runner
    (Stage 2C-B6-4 armed it). The B6-3 collector is the unarmed-baseline tool;
    its logic stays covered against this twin, and A5 defers the armed-state
    evaluators to the readiness-at-R stage."""
    text = text.replace(_ARMED_PURPOSE, _ORIGINAL_PURPOSE, 1)
    text, count = _ARMED_ENVELOPE.subn(_UNARMED_ENVELOPE, text, count=1)
    assert count == 1
    return text


def test_runner_facts_for_the_committed_armed_runner():
    """Stage 2C-B6-4: the committed runner is bound to the replacement purpose
    and the committed envelope identity. The collector reports these facts
    accurately (the B6-3 row 25 predicate was written for the unarmed state)."""
    facts = script.runner_facts(RUNNER_TEXT, COMMON_TEXT)
    assert facts == {
        "purpose_is_original": False, "envelope_is_none": False, "preflight_consults_latch_in_order": True,
        "eligibility_requires_latch": True, "runner_never_overrides_attempts": True,
    }


def test_the_unarmed_twin_reproduces_the_unarmed_runner_facts():
    facts = script.runner_facts(_unarmed_runner_twin(RUNNER_TEXT.replace("\r\n", "\n")), COMMON_TEXT)
    assert facts["purpose_is_original"] is True and facts["envelope_is_none"] is True


@pytest.mark.parametrize("old,new,key", [
    (_ARMED_PURPOSE, _ORIGINAL_PURPOSE, "purpose_is_original"),
])
def test_runner_facts_detect_a_reversion_to_the_original_purpose(old, new, key):
    text = RUNNER_TEXT.replace("\r\n", "\n")
    assert old in text, old
    assert script.runner_facts(text.replace(old, new, 1), COMMON_TEXT)[key] is True


def test_runner_facts_detect_a_reversion_of_the_envelope_binding():
    text = RUNNER_TEXT.replace("\r\n", "\n")
    reverted, count = _ARMED_ENVELOPE.subn(_UNARMED_ENVELOPE, text, count=1)
    assert count == 1
    assert script.runner_facts(reverted, COMMON_TEXT)["envelope_is_none"] is True


@pytest.mark.parametrize("old,new,key", [
    ("        latch_records = load_replacement_latch()\n", "", "preflight_consults_latch_in_order"),
    ("        assert_purpose_armable(PURPOSE, ENVELOPE)\n        try:\n", "        try:\n", "preflight_consults_latch_in_order"),
    ("            run_ordinal=run_ordinal, clock=clock", "            max_model_attempts_per_task=1, run_ordinal=run_ordinal, clock=clock", "runner_never_overrides_attempts"),
])
def test_runner_facts_detect_arming_reordering_and_attempt_overrides(old, new, key):
    text = RUNNER_TEXT.replace("\r\n", "\n")
    assert old in text, old
    assert script.runner_facts(text.replace(old, new, 1), COMMON_TEXT)[key] is False


def test_runner_facts_detect_a_defaulted_latch_argument_and_a_double_admission():
    common = COMMON_TEXT.replace("\r\n", "\n").replace("    latch: LatchVerdict,\n", "    latch: LatchVerdict = None,\n", 1)
    assert script.runner_facts(RUNNER_TEXT, common)["eligibility_requires_latch"] is False
    runner = RUNNER_TEXT.replace("\r\n", "\n")
    extra = "        replacement_latch_admission(client, ctx, latch_records, env, expected_api_job_name='gate')\n"
    twice = runner.replace("        assert_purpose_armable(PURPOSE, ENVELOPE)\n", extra + "        assert_purpose_armable(PURPOSE, ENVELOPE)\n", 1)
    assert script.runner_facts(twice, COMMON_TEXT)["preflight_consults_latch_in_order"] is False


def test_local_fact_gatherers_read_the_committed_unarmed_state():
    marker = script.marker_semantics_facts()
    assert marker["unconstructible_without_frozen_fields"] is True
    assert marker["canonical_name"] == "sentinel-p5-oneshot-p5d-replacement-sonnet-gate-r123"
    registry = script.registry_facts(REPO_ROOT / "artifacts" / "phase5_receipt_registry.jsonl")
    assert registry == {"receipts": 4, "replacement_receipts": 0, "history_permits_exactly_one": True,
                        "original_refused_durably": True}
    latch = script.latch_facts_local(REPO_ROOT / "artifacts" / "phase5_replacement_latch.jsonl")
    assert latch == {"kinds": ["GENESIS"], "file_sha256": rd.EXPECTED_LATCH_FILE_SHA256,
                     "head_sha256": rd.EXPECTED_LATCH_HEAD_SHA256, "unarmed_refusal_reason": "LATCH_UNARMED"}
    envelope = script.envelope_facts(REPO_ROOT / "artifacts" / "phase5_execution_envelope.json")
    assert envelope["envelope_id"] == rd.EXPECTED_ENVELOPE_ID and envelope["workflow_timeout_minutes"] == 106
    workflow = (REPO_ROOT / ".github" / "workflows" / "sentinel-official-gate.yml").read_text(encoding="utf-8")
    assert script.job_timeout_minutes(workflow) == 106 and script.job_timeout_minutes("jobs: {}") is None
    ledger = script.ledger_facts(REPO_ROOT / "telemetry" / "cost_ledger.jsonl", T)
    assert ledger["p5_run_ids"] == list(script.EXPECTED_LEDGER_RUN_IDS) and ledger["timing_row_micros"] == 1061616


def test_git_derived_facts_parse_name_status_names_and_workflow_diff_lines():
    class G(script.Sources):
        def git(self, args):
            if "--name-status" in args:
                return "A\tagents/checker/envelope_guard.py\nM\tagents/checker/harness.py\n"
            if "--name-only" in args:
                return "b.py\n\na.py\n"
            return "diff --git a/w b/w\n--- a/w\n+++ b/w\n@@ -29 +29 @@ jobs:\n-    timeout-minutes: 30\n+    timeout-minutes: 106\n"

        def git_bytes(self, args):
            return b"diff text"

    src = G()
    assert script.name_status(src, BASE, ["agents"]) == {"agents/checker/envelope_guard.py": "A", "agents/checker/harness.py": "M"}
    assert script.diff_names(src, BASE, ["x"]) == ["a.py", "b.py"]
    assert script.workflow_diff_lines(src, BASE, rd.OFFICIAL_WORKFLOW) == list(rd.ALLOWED_WORKFLOW_DIFF_LINES)
    assert script.diff_sha256(src, BASE, "x") == script.sha256_hex(b"diff text")


def test_artifact_download_verifies_the_github_digest_and_extracts_one_member():
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("phase5_latch_read_probe.json", b'{"x": 1}')
    data = buffer.getvalue()

    class G(script.Sources):
        def gh_bytes(self, args):
            return data

    digest = "sha256:" + script.sha256_hex(data)
    assert script.download_artifact_zip(G(), "1", digest) == data
    assert script.extract_single(data, "phase5_latch_read_probe.json") == b'{"x": 1}'
    with pytest.raises(script.CollectError):
        script.download_artifact_zip(G(), "1", "sha256:" + "0" * 64)
    with pytest.raises(script.CollectError):
        script.extract_single(data, "other.json")


class _FakeClient:
    def __init__(self, run_numbers=(1, 2, 3, 4), replacement=0, gate_evidence=0):
        self.run_numbers, self.replacement, self.gate_evidence = run_numbers, replacement, gate_evidence

    def list_workflow_runs_counted(self, path, *, created_after, created_before, **kw):
        from sentinel.phase5.github_evidence import RunRef
        return [RunRef(run_id=str(9000 + n), run_attempt=1, event="workflow_dispatch", ref="refs/heads/main", sha="c" * 40,
                       workflow_path=path, created_at=T, run_started_at=None, run_number=n, status="completed",
                       conclusion="failure", head_branch="main") for n in self.run_numbers]

    def list_artifacts(self, prefix):
        from sentinel.phase5.github_evidence import ArtifactRef
        if prefix == "sentinel-p5-gate-evidence-":
            return [ArtifactRef(id=i, name=f"sentinel-p5-gate-evidence-r{i}-a1", workflow_run_id="1") for i in range(self.gate_evidence)]
        names = ["sentinel-p5-oneshot-p5c-wif-probe-r1", "sentinel-p5-oneshot-p5d-official-sonnet-gate-r2"]
        names += [f"sentinel-p5-oneshot-p5d-replacement-sonnet-gate-r{i}" for i in range(self.replacement)]
        return [ArtifactRef(id=i, name=n, workflow_run_id="1") for i, n in enumerate(names)]


class _FakeSources(script.Sources):
    """Real repository files; scripted git and GitHub answers."""

    def __init__(self, *, ci=True, digests=None, head=PROBE_SHA, origin=PROBE_SHA, clean=True,
                 changed=None, path_diff="", workflow_diff=None, client=None, workflows=None):
        super().__init__(REPO_ROOT)
        self.ci, self.head, self.origin, self.clean = ci, head, origin, clean
        self.path_diff, self.fake_client = path_diff, client or _FakeClient()
        self.changed = changed if changed is not None else (
            "A\tagents/checker/envelope_guard.py\nA\tagents/checker/process_control.py\n"
            "M\tagents/checker/oidc.py\nM\tagents/checker/harness.py\n")
        self.workflow_diff = workflow_diff if workflow_diff is not None else (
            "--- a/w\n+++ b/w\n@@ -29 +29 @@\n-    timeout-minutes: 30\n+    timeout-minutes: 106\n")
        self.digests = digests or {aid: d for specs in (rd.B2_ARTIFACT_DIGESTS, rd.B5_ARTIFACT_DIGESTS) for _n, aid, d in specs}
        self.workflows = workflows or sorted(script.EXPECTED_WORKFLOWS)

    def git(self, args):
        if args[:2] == ["rev-parse", "HEAD"]:
            return self.head
        if args[:2] == ["rev-parse", "origin/main"]:
            return self.origin
        if args[:2] == ["status", "--porcelain"]:
            return "" if self.clean else " M x.py"
        if "--name-status" in args:
            return self.changed
        if "--name-only" in args:
            return self.path_diff
        if "--unified=0" in args:
            return self.workflow_diff
        raise AssertionError(args)

    def gh_json(self, args):
        if args[0] == "run":
            return [{"databaseId": 123, "conclusion": "success", "status": "completed", "headSha": PROBE_SHA}] if self.ci else []
        if args[0] == "variable":
            return [{"name": "ANTHROPIC_ORGANIZATION_ID", "value": "org"}, {"name": "SENTINEL_P5D_FEDERATION_RULE_ID", "value": "fdrl_old"}]
        if args[0] == "secret":
            return []
        if args[0] == "workflow":
            return [{"name": w, "state": "active", "path": f".github/workflows/{w}"} for w in self.workflows]
        path = args[1]
        if path.endswith("/actions/permissions/workflow"):
            return {"default_workflow_permissions": "read"}
        if path.endswith("/oidc/customization/sub"):
            return {"use_default": True, "use_immutable_subject": False}
        artifact_id = path.rsplit("/", 1)[1]
        return {"digest": self.digests.get(artifact_id), "expired": False}

    def read_text(self, relative):
        text = super().read_text(relative)
        if relative == "scripts/run_phase5_official_gate.py":
            return _unarmed_runner_twin(text)  # the B6-3 collector evaluates the unarmed baseline
        return text

    def client(self):
        return self.fake_client

    def phase1_frozen_ok(self):
        return True


def _build(src=None, *, probe=None, lifecycle=None, adjudications=(), scheduler=None, identity=None):
    src = src or _FakeSources()
    pinned = {p: v[1] for p, v in rd.QUALITY_ALLOWED_DIFFS.items()}
    script.diff_sha256 = lambda s, base, path: pinned.get(path)
    return script.build_record(
        src, base_sha=PROBE_SHA, probe=probe or _probe(), b5_identity=identity or _identity(),
        lifecycle=lifecycle or _lifecycle(), adjudications=adjudications,
        scheduler=scheduler or {"state": "Disabled", "enabled": False}, now=T + timedelta(minutes=1),
    )


@pytest.fixture(autouse=True)
def _restore_diff_hash():
    original = script.diff_sha256
    yield
    script.diff_sha256 = original


@pytest.fixture(autouse=True)
def _genesis_only_committed_latch(monkeypatch, tmp_path_factory):
    """A-tolerance: the B6-3 collector evaluates the unarmed GENESIS-only
    baseline, so it reads the committed GENESIS line alone (see
    ``_committed_genesis_view``)."""
    view = _committed_genesis_view(tmp_path_factory.mktemp("latch"))
    committed = (REPO_ROOT / "artifacts" / "phase5_replacement_latch.jsonl").resolve()
    original = script.latch_facts_local
    monkeypatch.setattr(script, "latch_facts_local",
                        lambda path: original(view if Path(path).resolve() == committed else path))


def test_a_fully_consistent_world_builds_an_arming_eligible_record_with_the_exact_deferred_shape():
    record = _build()
    assert rd.evaluate_arming_eligibility(record).eligible, rd.evaluate_arming_eligibility(record).reasons
    statuses = {r.row_id: r.status for r in record.rows}
    assert statuses[4] == statuses[16] == "DEFERRED"
    assert {n for n, c in rd.component_map(record).items() if c.status == "DEFERRED"} == {"4.2", "16h", "16i"}
    assert record.closure == "PENDING_POST_PUSH_CI" and [c.sha for c in record.ci] == [PROBE_SHA]
    assert rd.ReadinessRecord.model_validate_json(rd.record_bytes(record)) == record
    assert all(c.evidence_state in ("T0", "PREARM_BASELINE") for c in rd.component_map(record).values())


def test_collection_fails_closed_without_exact_sha_ci_for_the_b63a_sha():
    with pytest.raises(script.CollectError):
        _build(_FakeSources(ci=False))


@pytest.mark.parametrize("kwargs,component", [
    ({"digests": {"10615366134": "sha256:" + "0" * 64}}, "13"),
    ({"path_diff": "agents/checker/oidc.py\n"}, "14"),
    ({"workflow_diff": "-    timeout-minutes: 30\n+    timeout-minutes: 106\n+    name: other\n"}, "12"),
    ({"head": "e" * 40}, "16a"),
    ({"origin": "e" * 40}, "16a"),
    ({"clean": False}, "16a"),
    ({"changed": "M\tagents/checker/prompts.py\n"}, "15"),
    ({"workflows": ["ci.yml", "sentinel-official-gate.yml"]}, "16b"),
    ({"client": _FakeClient(run_numbers=(1, 2, 3, 4, 5))}, "25"),
    ({"client": _FakeClient(replacement=1)}, "3"),
    ({"client": _FakeClient(gate_evidence=1)}, "25"),
])
def test_each_drift_in_the_world_fails_exactly_its_component_and_blocks_eligibility(kwargs, component):
    record = _build(_FakeSources(**kwargs))
    assert rd.component_map(record)[component].status == "FAIL"
    verdict = rd.evaluate_arming_eligibility(record)
    assert not verdict.eligible and any(component in r for r in verdict.reasons)


def test_probe_failures_scheduler_lifecycle_and_overrides_fail_their_components():
    stopped = _probe(result="STOP", stop_reason="failed checks: commit", checks=_checks()[:3])
    record = _build(probe=stopped)
    assert rd.component_map(record)["24"].status == "FAIL" and not rd.evaluate_arming_eligibility(record).eligible
    enabled = _build(scheduler={"state": "Ready", "enabled": True})
    assert rd.component_map(enabled)["16l"].status == "FAIL"
    deprecated = _lifecycle()
    deprecated["models"][1] = {**deprecated["models"][1], "state": "Deprecated"}
    assert rd.component_map(_build(lifecycle=deprecated))["16k"].status == "FAIL"
    flagged = _probe(checks=_replace_check("env_scan", ok=False, flagged=["ANTHROPIC_MODEL"], env_names=3))
    assert rd.component_map(_build(probe=flagged))["16k"].status == "FAIL"


def test_runtime_drift_flows_through_adjudication_into_rows_2_and_16g():
    drifted = _identity(distributions=_dists(httpx="0.28.0"))
    record = _build(probe=_probe(runtime_identity=drifted))
    assert rd.component_map(record)["2"].status == rd.component_map(record)["16g"].status == "FAIL"
    ruling = rd.Adjudication(package="httpx", baseline_version="0.27.0", observed_version="0.28.0",
                             decision="ACCEPTED", ruling_ref="owner-ruling-1")
    ok = _build(probe=_probe(runtime_identity=drifted), adjudications=(ruling,))
    assert rd.evaluate_arming_eligibility(ok).eligible
    fatal = _build(probe=_probe(runtime_identity=_identity(sdk=_sdk(cli_sha="x" * 64))), adjudications=(ruling,))
    assert rd.component_map(fatal)["2"].status == "FAIL"


def test_verify_command_reports_the_rows_verdict_only_and_rejects_non_canonical_files(tmp_path, capsys):
    import argparse

    path = tmp_path / "record.json"
    path.write_bytes(rd.record_bytes(_record()))
    assert script.cmd_verify(argparse.Namespace(record=str(path))) == 0
    out = capsys.readouterr().out
    assert "ARMING-ELIGIBLE" in out and "PENDING_POST_PUSH_CI" in out and "CLOSED" not in out
    path.write_bytes(rd.record_bytes(_record({"9": _comp("9", "FAIL")})))
    assert script.cmd_verify(argparse.Namespace(record=str(path))) == 1
    path.write_bytes(rd.record_bytes(_record()).replace(b"\n", b" \n"))
    assert script.cmd_verify(argparse.Namespace(record=str(path))) == 2


def test_write_set_command_runs_the_pre_and_post_commit_proofs(capsys):
    import argparse

    class G(script.Sources):
        def __init__(self, dirty, untracked, staged, names, count, parent):
            super().__init__(REPO_ROOT)
            self.v = (dirty, untracked, staged, names, count, parent)

        def git(self, args):
            dirty, untracked, staged, names, count, parent = self.v
            if args[:1] == ["diff"] and "--cached" in args:
                return "\n".join(staged)
            if args[:1] == ["diff"] and f"{BASE}..HEAD" in args:
                return "\n".join(names)
            if args[:1] == ["diff"]:
                return "\n".join(dirty)
            if args[:1] == ["ls-files"]:
                return "\n".join(untracked)
            if args[:1] == ["rev-list"]:
                return str(count)
            if args == ["rev-parse", "HEAD~1"]:
                return parent
            raise AssertionError(args)

    pre = argparse.Namespace(phase="pre", base_source_sha=None)
    assert script.cmd_write_set(pre, G(THREE, [], [], [], 0, BASE)) == 0
    assert script.cmd_write_set(pre, G(THREE + ["x.py"], [], [], [], 0, BASE)) == 1
    post = argparse.Namespace(phase="post", base_source_sha=BASE)
    assert script.cmd_write_set(post, G([], [], [], THREE, 1, BASE)) == 0
    assert script.cmd_write_set(post, G([], [], [], THREE, 2, BASE)) == 1
    assert script.cmd_write_set(post, G([], [], [], THREE + ["x"], 1, BASE)) == 1
    assert "WRITE SET FAIL" in capsys.readouterr().err

"""Tests for sentinel/phase5/execution_envelope.py (ADR-0012 sections 7
and 8, Amendment A4; dispatch q77-p5d-repair-stage2c1-implement-a,
Stage 2C-1). Model-free: no provider, no network, no thread."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import typing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from sentinel.phase5 import execution_envelope as ee
from sentinel.phase5 import terminal as t
from sentinel.phase5.github_evidence import JobDetail
from sentinel.phase5.models import canonical_json_bytes

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "sentinel" / "phase5" / "execution_envelope.py"
NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
SHA_A = "a" * 40
WORKFLOW = ".github/workflows/sentinel-rehearsal.yml"


def _rehearsal(max_ms: int = 100_000, **overrides) -> ee.TimingRehearsalProvenance:
    fields = dict(
        rehearsal_workflow_identity=WORKFLOW,
        rehearsal_run_id="4242",
        rehearsal_run_attempt=1,
        rehearsal_source_sha=SHA_A,
        preregistration_sha256="c" * 64,
        corpus_sha256="d" * 64,
        observation_count=24,
        observations_ms=tuple([min(1_000, max_ms)] * 23 + [max_ms]),
        model="claude-sonnet-5",
        sdk_pin="claude-agent-sdk==0.2.110",
    )
    fields.update(overrides)
    return ee.TimingRehearsalProvenance(**fields)


def _envelope(max_ms: int = 100_000) -> ee.ExecutionEnvelope:
    return ee.build_execution_envelope(_rehearsal(max_ms))


# ======================================================================
# Formulas (exact integer arithmetic, ceiling never floor)
# ======================================================================


@pytest.mark.parametrize(
    "max_ms,outer,minutes,session,stall",
    [
        (1, 1_081, 19, 601, 600_000),
        (1_001, 1_219, 21, 739, 600_000),
        (100_000, 14_880, 248, 14_400, 1_000_000),
        (148_000, 21_504, 359, 21_024, 1_480_000),
    ],
)
def test_exact_formulas(max_ms, outer, minutes, session, stall):
    assert ee.outer_seconds(max_ms) == outer
    assert ee.workflow_timeout_minutes(outer) == minutes
    assert ee.session_duration_s(outer) == session
    assert ee.stall_budget_ms(max_ms) == stall
    envelope = _envelope(max_ms)
    assert (envelope.outer_seconds, envelope.workflow_timeout_minutes) == (outer, minutes)
    assert (envelope.session_duration_s, envelope.stall_budget_ms) == (session, stall)


def test_ceil_not_floor():
    # 138 * 1001 / 1000 = 138.138 -> 139, never 138
    assert ee.outer_seconds(1_001) == 139 + 1_080
    # 14881 / 60 = 248.02 -> 249, never 248
    assert ee.workflow_timeout_minutes(14_881) == 249
    assert ee.workflow_timeout_minutes(14_880) == 248


def test_invocation_budget_is_min_of_remaining_and_stall():
    assert ee.invocation_budget_ms(5_000, 600_000) == 5_000
    assert ee.invocation_budget_ms(900_000, 600_000) == 600_000
    assert ee.invocation_budget_ms(0, 600_000) == 0
    assert ee.invocation_budget_ms(-1, 600_000) == -1  # an expired session never yields a positive budget
    with pytest.raises(ee.EnvelopeError):
        ee.invocation_budget_ms(True, 600_000)


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "1"])
def test_formula_inputs_must_be_positive_integers(bad):
    with pytest.raises(ee.EnvelopeError):
        ee.outer_seconds(bad)
    with pytest.raises(ee.EnvelopeError):
        ee.stall_budget_ms(bad)


def test_feasibility_boundary_148000_pass_148001_and_149000_infeasible():
    assert ee.is_feasible(148_000) is True
    assert ee.is_feasible(148_001) is False
    assert ee.is_feasible(149_000) is False
    assert _envelope(148_000).workflow_timeout_minutes == 359
    for infeasible in (148_001, 149_000, 10_000_000):
        with pytest.raises(ValidationError):
            _envelope(infeasible)
        with pytest.raises(ValidationError):
            ee.ExecutionEnvelope.model_validate(
                {**json.loads(canonical_json_bytes(_envelope(148_000))), "max_observed_ms": infeasible}
            )


def test_frozen_contract_constants():
    assert ee.INVOCATIONS_SIZED == 92 and ee.MARGIN_MULTIPLIER == "1.5"
    assert ee.FIXED_OVERHEAD_S == 600 and ee.FINALIZATION_RESERVE_S == 480
    assert ee.STALL_FLOOR_S == 600 and ee.STALL_MULTIPLIER == 10
    assert ee.PLATFORM_JOB_CEILING_MIN == 360 and ee.MAX_OBSERVED_CEILING_MS == 148_000
    assert ee.ENVELOPE_VERSION == "1" and ee.SCHEMA_VERSION == 1
    envelope = _envelope()
    assert (envelope.schema_version, envelope.envelope_version) == (1, "1")
    assert (envelope.invocations_sized, envelope.margin_multiplier) == (92, "1.5")
    assert (envelope.fixed_overhead_s, envelope.finalization_reserve_s) == (600, 480)
    assert (envelope.stall_floor_s, envelope.stall_multiplier) == (600, 10)
    assert (envelope.platform_job_ceiling_min, envelope.max_observed_ceiling_ms) == (360, 148_000)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2), ("envelope_version", "2"), ("invocations_sized", 93),
        ("margin_multiplier", "1.4"), ("fixed_overhead_s", 599), ("finalization_reserve_s", 479),
        ("stall_floor_s", 599), ("stall_multiplier", 9), ("platform_job_ceiling_min", 361),
        ("max_observed_ceiling_ms", 148_001), ("outer_seconds", 14_881), ("workflow_timeout_minutes", 247),
        ("session_duration_s", 14_401), ("stall_budget_ms", 999_999), ("max_observed_ms", 99_999),
    ],
)
def test_frozen_literals_and_derived_fields_reject_other_values(field, value):
    data = json.loads(canonical_json_bytes(_envelope()))
    data[field] = value
    with pytest.raises(ValidationError):
        ee.ExecutionEnvelope.model_validate(data)


def test_envelope_is_frozen_and_extra_forbidden():
    envelope = _envelope()
    with pytest.raises(ValidationError):
        envelope.max_observed_ms = 5  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ee.ExecutionEnvelope.model_validate({**json.loads(canonical_json_bytes(envelope)), "anchor": {}})
    with pytest.raises(ValidationError):
        _rehearsal(source_sha=SHA_A)  # extra forbidden -- and no generic source_sha exists


# ======================================================================
# Identity
# ======================================================================


def test_envelope_id_is_sha256_of_canonical_bytes_and_deterministic():
    a, b = _envelope(), _envelope()
    assert a.envelope_id == b.envelope_id == hashlib.sha256(canonical_json_bytes(a)).hexdigest()
    assert len(a.envelope_id) == 64
    identity = a.identity()
    assert isinstance(identity, t.EnvelopeIdentity)
    assert identity == t.EnvelopeIdentity(envelope_id=a.envelope_id, envelope_version="1")
    assert _envelope(100_001).envelope_id != a.envelope_id
    assert ee.build_execution_envelope(_rehearsal(rehearsal_run_id="4243")).envelope_id != a.envelope_id


def test_anchor_clock_and_control_config_are_not_envelope_fields():
    fields = set(ee.ExecutionEnvelope.model_fields)
    assert fields == {
        "schema_version", "envelope_version", "invocations_sized", "margin_multiplier",
        "fixed_overhead_s", "finalization_reserve_s", "stall_floor_s", "stall_multiplier",
        "platform_job_ceiling_min", "max_observed_ceiling_ms", "max_observed_ms",
        "outer_seconds", "workflow_timeout_minutes", "session_duration_s", "stall_budget_ms",
        "rehearsal",
    }
    for name in fields | set(ee.TimingRehearsalProvenance.model_fields):
        assert not any(token in name for token in ("anchor", "clock", "control", "monotonic", "resolved"))


# ======================================================================
# Rehearsal provenance
# ======================================================================


def test_rehearsal_provenance_uses_rehearsal_source_sha_and_no_generic_source_sha():
    fields = set(ee.TimingRehearsalProvenance.model_fields)
    assert fields == {
        "rehearsal_workflow_identity", "rehearsal_run_id", "rehearsal_run_attempt",
        "rehearsal_source_sha", "preregistration_sha256", "corpus_sha256",
        "observation_count", "observations_ms", "model", "sdk_pin",
    }
    assert "source_sha" not in fields and "source_sha" not in ee.ExecutionEnvelope.model_fields
    rehearsal = _rehearsal()
    assert rehearsal.rehearsal_source_sha == SHA_A and rehearsal.max_observed_ms == 100_000


@pytest.mark.parametrize(
    "overrides",
    [
        dict(observations_ms=tuple([1_000] * 23)),
        dict(observations_ms=tuple([1_000] * 25)),
        dict(observation_count=23),
        dict(observations_ms=tuple([1_000] * 23 + [0])),
        dict(observations_ms=tuple([1_000] * 23 + [True])),
        dict(model="claude-opus-5"),
        dict(sdk_pin="claude-agent-sdk==0.2.111"),
        dict(rehearsal_source_sha="A" * 40),
        dict(rehearsal_source_sha="a" * 39),
        dict(preregistration_sha256="c" * 63),
        dict(corpus_sha256="D" * 64),
        dict(rehearsal_run_id=""),
        dict(rehearsal_run_id=" 4242"),
        dict(rehearsal_workflow_identity=""),
        dict(rehearsal_run_attempt=0),
    ],
)
def test_rehearsal_provenance_refusals(overrides):
    with pytest.raises(ValidationError):
        _rehearsal(**overrides)


def test_envelope_max_observed_must_equal_rehearsal_maximum():
    data = json.loads(canonical_json_bytes(_envelope(100_000)))
    data["rehearsal"]["observations_ms"][-1] = 90_000
    with pytest.raises(ValidationError):
        ee.ExecutionEnvelope.model_validate(data)


# ======================================================================
# Superseded formula absent; no wall clock; no committed envelope
# ======================================================================


def _numeric_constants(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    }


def test_superseded_per_invocation_formula_is_absent():
    constants = _numeric_constants(MODULE_PATH)
    assert 180 not in constants and 180_000 not in constants and 3 not in constants
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "max":
            # the only max() is the stall floor: max(STALL_FLOOR_S * 1000, STALL_MULTIPLIER * max_observed_ms)
            assert not any(isinstance(arg, ast.Constant) for arg in node.args)


def test_module_never_consults_the_wall_clock():
    text = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            assert name not in ("now", "utcnow", "today", "time", "gmtime", "localtime"), ast.dump(node)


def test_no_committed_envelope_artifact_exists():
    for path in REPO_ROOT.rglob("*envelope*.json"):
        assert ".git" in path.parts, path
    for path in (REPO_ROOT / "artifacts").glob("*"):
        assert "envelope" not in path.name.lower(), path
    runner = (REPO_ROOT / "scripts" / "run_phase5_official_gate.py").read_text(encoding="utf-8")
    assert 'ENVELOPE: "EnvelopeIdentity | None" = None' in runner


# ======================================================================
# Committed envelope loader (strict)
# ======================================================================


def test_load_committed_envelope_round_trips_canonical_bytes(tmp_path):
    envelope = _envelope()
    path = tmp_path / "envelope.json"
    path.write_bytes(ee.committed_envelope_bytes(envelope))
    loaded = ee.load_committed_envelope(path)
    assert loaded == envelope and loaded.envelope_id == envelope.envelope_id


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{",
        b"[]",
        b"null",
        b"\xff\xfe",
        json.dumps(json.loads(canonical_json_bytes(_envelope())), indent=2).encode(),  # non-canonical
        canonical_json_bytes(_envelope()) + b"\n",  # non-canonical trailing newline
        json.dumps({**json.loads(canonical_json_bytes(_envelope())), "extra": 1}, sort_keys=True, separators=(",", ":")).encode(),
    ],
)
def test_load_committed_envelope_refuses_malformed_or_noncanonical(tmp_path, data):
    path = tmp_path / "envelope.json"
    path.write_bytes(data)
    with pytest.raises(ee.CommittedEnvelopeError):
        ee.load_committed_envelope(path)


def test_load_committed_envelope_refuses_infeasible_even_when_internally_consistent(tmp_path):
    data = json.loads(canonical_json_bytes(_envelope(148_000)))
    max_ms = 149_000
    outer = ee.outer_seconds(max_ms)
    data.update(
        max_observed_ms=max_ms, outer_seconds=outer, workflow_timeout_minutes=ee.workflow_timeout_minutes(outer),
        session_duration_s=ee.session_duration_s(outer), stall_budget_ms=ee.stall_budget_ms(max_ms),
    )
    data["rehearsal"]["observations_ms"][-1] = max_ms
    path = tmp_path / "envelope.json"
    path.write_bytes(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())
    with pytest.raises(ee.CommittedEnvelopeError):
        ee.load_committed_envelope(path)


def test_load_committed_envelope_refuses_absent_directory_oversize_and_symlink(tmp_path, monkeypatch):
    with pytest.raises(ee.CommittedEnvelopeError):
        ee.load_committed_envelope(tmp_path / "missing.json")
    directory = tmp_path / "dir.json"
    directory.mkdir()
    with pytest.raises(ee.CommittedEnvelopeError):
        ee.load_committed_envelope(directory)
    big = tmp_path / "big.json"
    big.write_bytes(ee.committed_envelope_bytes(_envelope()))
    monkeypatch.setattr(ee, "MAX_ENVELOPE_BYTES", 10)
    with pytest.raises(ee.CommittedEnvelopeError):
        ee.load_committed_envelope(big)
    monkeypatch.undo()
    link = tmp_path / "link.json"
    try:
        os.symlink(big, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ee.CommittedEnvelopeError):
        ee.load_committed_envelope(link)


def test_loader_errors_never_carry_the_path(tmp_path):
    secret_dir = tmp_path / "very-private-location"
    secret_dir.mkdir()
    (secret_dir / "envelope.json").write_bytes(b"{")
    with pytest.raises(ee.CommittedEnvelopeError) as info:
        ee.load_committed_envelope(secret_dir / "envelope.json")
    assert "very-private-location" not in str(info.value)


# ======================================================================
# Job-start anchor
# ======================================================================

STARTED = datetime(2026, 9, 16, 11, 55, 0, tzinfo=timezone.utc)


def _job(**overrides) -> JobDetail:
    fields = dict(id=77, run_id="9", name="Sonnet official gate", status="in_progress", started_at=STARTED, runner_name="GitHub Actions 3")
    fields.update(overrides)
    return JobDetail(**fields)


def _resolve(jobs, **overrides) -> ee.JobStartAnchor:
    kwargs = dict(
        run_id="9", run_attempt=2, expected_workflow_job_id="gate", expected_api_job_name="Sonnet official gate",
        expected_runner_name="GitHub Actions 3", resolved_at_utc=NOW, monotonic_at_resolve=1000.0,
    )
    kwargs.update(overrides)
    return ee.resolve_job_start_anchor(jobs, **kwargs)


def test_anchor_resolves_with_attempt_from_request_identity():
    anchor = _resolve([_job(), _job(id=78, name="finalize", status="queued", started_at=None, runner_name=None)])
    assert anchor == ee.JobStartAnchor(
        run_id="9", run_attempt=2, workflow_job_id="gate", api_job_id=77, api_job_name="Sonnet official gate",
        runner_name="GitHub Actions 3", job_started_at_utc=STARTED, resolved_at_utc=NOW, monotonic_at_resolve=1000.0,
    )


def test_job_detail_has_no_body_run_attempt_field():
    assert "run_attempt" not in JobDetail.__dataclass_fields__
    assert set(JobDetail.__dataclass_fields__) == {"id", "run_id", "name", "status", "started_at", "runner_name"}


def test_anchor_matches_explicit_api_name_not_workflow_job_id():
    jobs = [_job()]
    # GITHUB_JOB ("gate") is not the REST display name; passing it as the API name refuses.
    with pytest.raises(ee.JobStartAnchorError, match="no job"):
        _resolve(jobs, expected_api_job_name="gate")
    # and the workflow job id is provenance only: any value resolves against the same explicit API name
    assert _resolve(jobs, expected_workflow_job_id="something-else").workflow_job_id == "something-else"


@pytest.mark.parametrize(
    "jobs,message",
    [
        ([_job(run_id="10")], "different run"),
        ([_job(), _job(id=78, run_id="10", name="other")], "different run"),
        ([], "no job"),
        ([_job(name="other")], "no job"),
        ([_job(), _job(id=78)], "more than one"),
        ([_job(status="queued")], "not in_progress"),
        ([_job(status="completed")], "not in_progress"),
        ([_job(runner_name="GitHub Actions 4")], "runner name"),
        ([_job(runner_name=None)], "runner name"),
        ([_job(started_at=None)], "no started_at"),
        ([_job(started_at=STARTED.replace(tzinfo=None))], "aware UTC"),
        ([_job(started_at=STARTED.astimezone(timezone(timedelta(hours=2))))], "aware UTC"),
        ([_job(started_at=NOW + timedelta(seconds=1))], "later than"),
    ],
)
def test_anchor_refusals(jobs, message):
    with pytest.raises(ee.JobStartAnchorError, match=message):
        _resolve(jobs)


@pytest.mark.parametrize(
    "overrides",
    [
        dict(run_id=""), dict(run_attempt=0), dict(run_attempt=True), dict(expected_api_job_name=""),
        dict(expected_runner_name=""), dict(expected_workflow_job_id=" "),
        dict(resolved_at_utc=NOW.replace(tzinfo=None)), dict(monotonic_at_resolve="1000"),
    ],
)
def test_anchor_argument_refusals(overrides):
    with pytest.raises(ee.JobStartAnchorError):
        _resolve([_job()], **overrides)


# ======================================================================
# Session clock
# ======================================================================


class _Mono:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _clock(*, started=STARTED, resolved=NOW, mono=None, at=1000.0, duration=14_400) -> tuple[ee.SessionClock, _Mono]:
    mono = mono or _Mono(at)
    clock = ee.SessionClock(
        job_started_at_utc=started, resolved_at_utc=resolved, monotonic_at_resolve=at,
        session_duration_s=duration, monotonic=mono,
    )
    return clock, mono


def test_setup_time_already_consumed_is_subtracted():
    # job started 11:55, resolved 12:00 -> five minutes already consumed
    clock, _ = _clock(started=STARTED, resolved=NOW)
    assert clock.session_deadline_utc == STARTED + timedelta(seconds=14_400)
    assert clock.remaining_at_resolution == timedelta(seconds=14_400 - 300)
    assert clock.monotonic_session_deadline == 1000.0 + 14_100
    assert clock.remaining_ms() == 14_100_000
    fresh, _ = _clock(started=STARTED, resolved=STARTED)
    assert fresh.remaining_ms() - clock.remaining_ms() == 300_000


def test_resolution_five_minutes_after_start_is_exactly_five_minutes_less():
    at_start, _ = _clock(started=STARTED, resolved=STARTED)
    later, _ = _clock(started=STARTED, resolved=STARTED + timedelta(minutes=5))
    assert at_start.remaining_ms() - later.remaining_ms() == 5 * 60 * 1000


def test_monotonic_progression_and_expiry():
    clock, mono = _clock(started=STARTED, resolved=STARTED, duration=600)
    assert clock.remaining_ms() == 600_000 and not clock.expired()
    mono.value += 599.999
    assert clock.remaining_ms() == 0 and clock.expired()
    mono.value = 1000.0 + 300.25
    assert clock.remaining_ms() == 299_750 and not clock.expired()
    mono.value = 1000.0 + 700
    assert clock.remaining_ms() == -100_000 and clock.expired()


def test_post_resolution_wall_clock_change_has_no_effect(monkeypatch):
    clock, mono = _clock(started=STARTED, resolved=STARTED, duration=600)
    before = clock.remaining_ms()

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # pragma: no cover - must never be called
            raise AssertionError("wall clock consulted")

    monkeypatch.setattr(ee, "datetime", _Frozen)
    assert clock.remaining_ms() == before
    mono.value += 1
    assert clock.remaining_ms() == before - 1_000


def test_already_expired_anchor_is_expired():
    clock, _ = _clock(started=STARTED, resolved=STARTED + timedelta(seconds=14_400), duration=14_400)
    assert clock.already_expired_at_resolution is True
    assert clock.remaining_at_resolution == timedelta(0)
    assert clock.expired() is True and clock.remaining_ms() == 0
    late, _ = _clock(started=STARTED, resolved=STARTED + timedelta(seconds=14_401), duration=14_400)
    assert late.already_expired_at_resolution and late.remaining_ms() == -1_000
    live, _ = _clock(started=STARTED, resolved=STARTED + timedelta(seconds=14_399), duration=14_400)
    assert live.already_expired_at_resolution is False


def test_no_preflight_start_or_execute_start_deadline_parameter_exists():
    init = set(inspect.signature(ee.SessionClock.__init__).parameters) - {"self"}
    assert init == {"job_started_at_utc", "resolved_at_utc", "monotonic_at_resolve", "session_duration_s", "monotonic"}
    for params in (init, set(inspect.signature(ee.SessionClock.from_anchor).parameters)):
        for name in params:
            assert "preflight" not in name and "execute" not in name and name != "now"
    assert "now" not in typing.get_type_hints(ee.SessionClock.remaining_ms)
    assert set(inspect.signature(ee.SessionClock.remaining_ms).parameters) == {"self"}


def test_from_anchor_uses_envelope_session_duration():
    anchor = _resolve([_job()])
    mono = _Mono(1000.0)
    clock = ee.SessionClock.from_anchor(anchor, _envelope(100_000), monotonic=mono)
    assert clock.session_deadline_utc == STARTED + timedelta(seconds=14_400)
    assert clock.remaining_ms() == (14_400 - 300) * 1000
    assert clock.monotonic_now() == 1000.0


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(started=STARTED.replace(tzinfo=None)),
        dict(resolved=NOW.astimezone(timezone(timedelta(hours=1)))),
        dict(duration=0),
        dict(duration=True),
        dict(at="1000"),
    ],
)
def test_session_clock_input_refusals(kwargs):
    with pytest.raises((ValueError, ee.EnvelopeError)):
        _clock(**kwargs)

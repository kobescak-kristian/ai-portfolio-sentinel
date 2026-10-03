"""Tests for sentinel/phase5/latch.py and the latch plumbing in
scripts/_phase5_common.py (ADR-0012 sections 3 and 21; the STATE.md HARD
PRE-ARMING GATE and ARMING CONTRACT; Stage 2C-B6-2, owner rulings R1-R5 of
2026-10-01). Model-free: no provider, no network, no workflow dispatch."""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from sentinel.phase5 import latch as lt
from sentinel.phase5 import replacement as repl
from sentinel.phase5.github_evidence import (
    CommitDetail,
    CommitFile,
    GithubEvidenceError,
    JobDetail,
    JobEvidence,
    JobStep,
    PushActivity,
    RunRef,
)

common = importlib.import_module("scripts._phase5_common")

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "sentinel" / "phase5" / "latch.py"
COMMON_PATH = REPO_ROOT / "scripts" / "_phase5_common.py"
COMMITTED_LATCH = REPO_ROOT / "artifacts" / "phase5_replacement_latch.jsonl"
GATE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sentinel-official-gate.yml"
PRE_PUSH_PATH = REPO_ROOT / ".githooks" / "pre-push"

UTC = timezone.utc
WF = ".github/workflows/sentinel-official-gate.yml"
R = "b" * 40
A = "c" * 40
CTX_RUN_ID = "50005"
F = 4
T_GEN = datetime(2026, 10, 2, 9, 0, 0, tzinfo=UTC)
T_REC = datetime(2026, 10, 3, 10, 0, 0, tzinfo=UTC)  # server time captured before the push of A
T_A = datetime(2026, 10, 3, 10, 1, 0, tzinfo=UTC)  # GitHub's push record of A
CLOSE = T_REC + timedelta(hours=24)
JOB_START = datetime(2026, 10, 3, 10, 5, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 3, 10, 7, 0, tzinfo=UTC)

# The committed GENESIS (Stage 2C-B6-2), stamped with GitHub server time.
COMMITTED_GENESIS_RECORDED_AT = datetime(2026, 10, 1, 21, 7, 16, tzinfo=UTC)
COMMITTED_HEAD_SHA256 = "af5446752c3abed4f5b87efcc55027f22eb69d56c7b70b85a371bf9f9f69019b"
COMMITTED_LF_BYTES_SHA256 = "37799c774db5d7b7281d5c9290b76b531d849a6feaa30ea21a75d51996e6ee5a"


# ======================================================================
# Builders
# ======================================================================


def _genesis(**overrides) -> lt.LatchGenesis:
    fields = dict(
        schema_version=1, record_kind="GENESIS", purpose=repl.REPLACEMENT_PURPOSE,
        replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID, owner_ruling_id=repl.OWNER_RULING_ID,
        workflow_identity=WF, governance_ref=lt.GENESIS_REF_ID,
        prev_record_sha256=lt.ZERO_SHA256, recorded_at_utc=T_GEN,
    )
    fields.update(overrides)
    return lt.LatchGenesis(**fields)


def _auth(genesis: lt.LatchGenesis, **overrides) -> lt.LatchAttemptAuthorized:
    fields = dict(
        schema_version=1, record_kind="ATTEMPT_AUTHORIZED", purpose=repl.REPLACEMENT_PURPOSE,
        replacement_of_run_id=repl.REPLACEMENT_OF_RUN_ID, owner_ruling_id=repl.OWNER_RULING_ID,
        workflow_identity=WF, readiness_source_sha=R, owner_go_ref="owner-go-test",
        window_closes_at_utc=CLOSE, prior_official_gate_run_number=F,
        prev_record_sha256=lt.record_sha256(genesis), recorded_at_utc=T_REC,
    )
    fields.update(overrides)
    return lt.LatchAttemptAuthorized(**fields)


def _records(**auth_overrides):
    genesis = _genesis()
    return (genesis, _auth(genesis, **auth_overrides))


def _line(record) -> str:
    return lt.record_line_bytes(record)[:-1].decode("utf-8")


def _write(path: Path, *lines: str) -> Path:
    path.write_bytes("".join(line + "\n" for line in lines).encode("utf-8"))
    return path


def _canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _run(number, *, run_id=None, status="completed", conclusion="failure", event="workflow_dispatch",
         branch="main", path=WF, sha=A, attempt=1):
    return RunRef(
        run_id=run_id or str(9000 + number), run_attempt=attempt, event=event, ref=f"refs/heads/{branch}",
        sha=sha, workflow_path=path, created_at=T_A + timedelta(minutes=number), run_started_at=None,
        run_number=number, status=status, conclusion=conclusion, head_branch=branch,
    )


def _step(name, conclusion, status="completed", number=1):
    return JobStep(name=name, status=status, conclusion=conclusion, number=number)


def _pre_marker_job(run_id: str, *, marker="skipped", execute="skipped", status="completed",
                    conclusion="failure", name="gate", extra_steps=()):
    """The exact step shape the real official-gate run #3 (32869033063)
    reported: preflight failure, marker skipped, execute skipped."""
    steps = (
        _step("Set up job", "success", number=1),
        _step("Run actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "success", number=2),
        _step("preflight", "failure", number=6),
        _step(lt.MARKER_STEP_NAME, marker, number=7),
        _step(lt.EXECUTE_STEP_NAME, execute, number=8),
        _step("finalize", "success", number=9),
        _step("Complete job", "success", number=19),
    ) + tuple(extra_steps)
    return JobEvidence(id=1, run_id=run_id, name=name, status=status, conclusion=conclusion, steps=steps)


def _commit(records, **overrides) -> CommitDetail:
    patch = f"@@ -1 +1,2 @@\n {_line(records[0])}\n+{_line(records[-1])}"
    file_fields = dict(
        filename="artifacts/phase5_replacement_latch.jsonl", status="modified", additions=1, deletions=0, patch=patch,
    )
    file_fields.update(overrides.pop("file", {}))
    fields = dict(sha=A, parents=(R,), files=(CommitFile(**file_fields),))
    fields.update(overrides)
    return CommitDetail(**fields)


def _facts(records, *, current_number=F + 1, prior=(), prior_jobs=None, floor_runs=None, **overrides):
    current = _run(current_number, run_id=CTX_RUN_ID, status="in_progress", conclusion=None)
    # Historical runs at or below the floor: never evidence for this decision
    # (run 4 even carries a marker success, like the real original run).
    if floor_runs is None:
        floor_runs = tuple(_run(n, conclusion="cancelled" if n == F else "failure") for n in range(1, F + 1))
    runs = tuple(floor_runs) + tuple(prior) + (current,)
    if prior_jobs is None:
        prior_jobs = {
            (run.run_id, attempt): (_pre_marker_job(run.run_id),)
            for run in prior for attempt in range(1, run.run_attempt + 1)
        }
    fields = dict(
        ctx_run_id=CTX_RUN_ID, ctx_run_attempt=1, ctx_sha=A, ctx_workflow_identity=WF, current_run=current,
        commit=_commit(records),
        push_activities=(PushActivity(before=R, after=A, ref="refs/heads/main", activity_type="push", timestamp=T_A),),
        job_started_at_utc=JOB_START, workflow_runs=runs, prior_attempt_jobs=prior_jobs, server_now_utc=NOW,
    )
    fields.update(overrides)
    return lt.LatchFacts(**fields)


def _verdict(records=None, **facts_overrides):
    records = records or _records()
    return lt.latch_verdict(records, _facts(records, **facts_overrides))


def _refused(verdict, reason):
    assert verdict.admitted is False, verdict
    assert verdict.reason == reason, verdict


# ======================================================================
# Committed latch
# ======================================================================


def _committed_genesis_view(tmp_path: Path) -> Path:
    """A-tolerance (Stage 2C-B6-6a, ADR-0012 Amendment C): the committed
    latch's GENESIS line alone. On commit A the committed latch carries one
    more line, and commit A cannot change any test, so tests that prove UNARMED
    behaviour read this view. GENESIS-only at R itself is enforced
    mechanically by row 25 of the FINAL_T2 record."""
    first = COMMITTED_LATCH.read_bytes().replace(b"\r\n", b"\n").split(b"\n", 1)[0] + b"\n"
    path = tmp_path / "committed_genesis_view.jsonl"
    path.write_bytes(first)
    return path


def test_committed_latch_is_genesis_only_or_genesis_plus_one_final_go_authorization():
    """Exactly two committed states are legal: GENESIS only (every commit up
    to and including R), or GENESIS plus one strictly valid ATTEMPT_AUTHORIZED
    whose owner_go_ref has the exact final-GO form (commit A)."""
    from sentinel.phase5.final_readiness import OWNER_GO_REF_PATTERN

    records = lt.load_latch(COMMITTED_LATCH)
    assert lt.record_sha256(records[0]) == COMMITTED_HEAD_SHA256
    assert len(records) in (1, 2)
    if len(records) == 2:
        auth = records[1]
        assert isinstance(auth, lt.LatchAttemptAuthorized)
        assert OWNER_GO_REF_PATTERN.fullmatch(auth.owner_go_ref)
        assert auth.prev_record_sha256 == COMMITTED_HEAD_SHA256


def test_the_committed_state_check_rejects_any_other_shape(tmp_path):
    from sentinel.phase5.final_readiness import OWNER_GO_REF_PATTERN

    view = _committed_genesis_view(tmp_path)
    lt.append_attempt_authorized(view, server_now_utc=T_REC, readiness_source_sha=R, owner_go_ref="owner-go-test",
                                 window_closes_at_utc=CLOSE, prior_official_gate_run_number=F)
    assert not OWNER_GO_REF_PATTERN.fullmatch(lt.load_latch(view)[1].owner_go_ref)
    lines = view.read_bytes().split(b"\n")
    view.write_bytes(view.read_bytes() + lines[1] + b"\n")
    with pytest.raises(lt.LatchError):
        lt.load_latch(view)


def test_committed_latch_is_exactly_one_server_time_genesis_and_unarmed(tmp_path):
    view = _committed_genesis_view(tmp_path)
    records = lt.load_latch(view)
    assert len(records) == 1 and isinstance(records[0], lt.LatchGenesis)
    genesis = records[0]
    assert genesis.purpose == repl.REPLACEMENT_PURPOSE
    assert genesis.replacement_of_run_id == repl.REPLACEMENT_OF_RUN_ID
    assert genesis.owner_ruling_id == repl.OWNER_RULING_ID
    assert genesis.workflow_identity == WF
    assert genesis.governance_ref == "q77-p5d-repair-stage2cb6-2-latch-genesis"
    assert genesis.recorded_at_utc == COMMITTED_GENESIS_RECORDED_AT
    assert lt.record_sha256(genesis) == COMMITTED_HEAD_SHA256
    assert hashlib.sha256(view.read_bytes()).hexdigest() == COMMITTED_LF_BYTES_SHA256
    for moment in (COMMITTED_GENESIS_RECORDED_AT, CLOSE, datetime(2030, 1, 1, tzinfo=UTC)):
        assert lt.admission_state(records, moment) == "UNARMED"
    _refused(lt.latch_verdict(records, None), "LATCH_UNARMED")


def test_committed_latch_identity_equals_the_replacement_constants():
    assert lt.OFFICIAL_GATE_WORKFLOW == WF
    assert lt.LATCH_PATH.as_posix() == "artifacts/phase5_replacement_latch.jsonl"
    assert lt.MAX_AUTHORIZATION_WINDOW == timedelta(hours=24)


# ======================================================================
# Loader (strict, fail-closed)
# ======================================================================


def test_load_round_trips_genesis_and_authorization(tmp_path):
    records = _records()
    path = _write(tmp_path / "latch.jsonl", _line(records[0]), _line(records[1]))
    assert lt.load_latch(path) == records


def test_load_accepts_consistent_crlf_only(tmp_path):
    genesis = _genesis()
    path = tmp_path / "latch.jsonl"
    path.write_bytes((_line(genesis) + "\r\n").encode())
    assert lt.load_latch(path) == (genesis,)
    path.write_bytes((_line(genesis) + "\r\r\n").encode())
    with pytest.raises(lt.LatchError):
        lt.load_latch(path)


def test_load_refuses_missing_empty_directory_and_invalid_utf8(tmp_path):
    with pytest.raises(lt.LatchError, match="missing"):
        lt.load_latch(tmp_path / "absent.jsonl")
    (tmp_path / "dir.jsonl").mkdir()
    with pytest.raises(lt.LatchError):
        lt.load_latch(tmp_path / "dir.jsonl")
    (tmp_path / "empty.jsonl").write_bytes(b"")
    with pytest.raises(lt.LatchError, match="empty"):
        lt.load_latch(tmp_path / "empty.jsonl")
    (tmp_path / "bad.jsonl").write_bytes(b"\xff\xfe\n")
    with pytest.raises(lt.LatchError):
        lt.load_latch(tmp_path / "bad.jsonl")


def test_load_refuses_a_symlink(tmp_path):
    target = _write(tmp_path / "real.jsonl", _line(_genesis()))
    link = tmp_path / "link.jsonl"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(lt.LatchError):
        lt.load_latch(link)


def _chained_second_authorization(genesis, first):
    """A second ATTEMPT_AUTHORIZED that is valid in every respect but its
    count: correct predecessor hash, later timestamps, in-bounds window.
    Only the two-record cap can refuse it."""
    return _auth(
        genesis, prev_record_sha256=lt.record_sha256(first),
        recorded_at_utc=first.recorded_at_utc + timedelta(seconds=1),
        window_closes_at_utc=first.window_closes_at_utc + timedelta(seconds=1),
    )


@pytest.mark.parametrize("body", [
    lambda g, a: (_line(g)).encode(),  # trailing fragment: no final newline
    lambda g, a: (_line(g) + "\n\n").encode(),  # blank line
    lambda g, a: b"{not json\n",
    lambda g, a: (json.dumps(json.loads(_line(g)), indent=1).replace("\n", " ") + "\n").encode(),  # non-canonical
    lambda g, a: (_canonical({**json.loads(_line(g)), "extra": 1}) + "\n").encode(),
    lambda g, a: (_canonical({**json.loads(_line(g)), "record_kind": "CONSUMED"}) + "\n").encode(),
    lambda g, a: (_line(a) + "\n").encode(),  # ATTEMPT_AUTHORIZED before GENESIS
    lambda g, a: (_line(g) + "\n" + _line(g) + "\n").encode(),  # second GENESIS
    lambda g, a: (_line(g) + "\n" + _line(a) + "\n" + _line(a) + "\n").encode(),  # a repeated, unchained line
    lambda g, a: (  # a second ATTEMPT_AUTHORIZED that chains correctly: refused only by the record cap
        _line(g) + "\n" + _line(a) + "\n" + _line(_chained_second_authorization(g, a)) + "\n"
    ).encode(),
], ids=["fragment", "blank", "json", "non-canonical", "extra-field", "unknown-kind", "auth-first",
        "second-genesis", "third-record-unchained", "second-authorization-validly-chained"])
def test_load_refuses_malformed_shapes(tmp_path, body):
    genesis = _genesis()
    path = tmp_path / "latch.jsonl"
    path.write_bytes(body(genesis, _auth(genesis)))
    with pytest.raises(lt.LatchError):
        lt.load_latch(path)


def test_a_validly_chained_second_authorization_is_refused_by_the_record_cap(tmp_path):
    genesis = _genesis()
    first = _auth(genesis)
    second = _chained_second_authorization(genesis, first)
    assert second.prev_record_sha256 == lt.record_sha256(first)  # valid but for its count
    with pytest.raises(lt.LatchError, match="more records"):
        lt.load_latch(_write(tmp_path / "latch.jsonl", _line(genesis), _line(first), _line(second)))


def test_load_refuses_a_broken_chain_and_a_nonzero_genesis_predecessor(tmp_path):
    genesis = _genesis()
    broken = _auth(genesis, prev_record_sha256="d" * 64)
    with pytest.raises(lt.LatchError, match="chain"):
        lt.load_latch(_write(tmp_path / "a.jsonl", _line(genesis), _line(broken)))
    raw = {**json.loads(_line(genesis)), "prev_record_sha256": "d" * 64}
    with pytest.raises(lt.LatchError):
        lt.load_latch(_write(tmp_path / "b.jsonl", _canonical(raw)))


@pytest.mark.parametrize("field,value", [
    ("purpose", "P5D_OFFICIAL_SONNET_GATE"),
    ("replacement_of_run_id", "1"),
    ("owner_ruling_id", "some-other-ruling"),
    ("workflow_identity", ".github/workflows/sentinel-timing-rehearsal.yml"),
    ("governance_ref", "other-ref"),
    ("recorded_at_utc", "2026-10-02T09:00:00"),  # naive
    ("recorded_at_utc", "2026-10-02T10:00:00+01:00"),  # non-zero offset
])
def test_load_refuses_identity_and_time_violations(tmp_path, field, value):
    raw = {**json.loads(_line(_genesis())), field: value}
    with pytest.raises(lt.LatchError):
        lt.load_latch(_write(tmp_path / "latch.jsonl", _canonical(raw)))


def test_load_refuses_an_authorization_earlier_than_genesis(tmp_path):
    genesis = _genesis(recorded_at_utc=T_REC + timedelta(hours=1))
    earlier = _auth(genesis)  # recorded at T_REC, before genesis
    with pytest.raises(lt.LatchError, match="earlier"):
        lt.load_latch(_write(tmp_path / "latch.jsonl", _line(genesis), _line(earlier)))


@pytest.mark.parametrize("close", [T_REC, T_REC - timedelta(seconds=1), T_REC + timedelta(hours=24, seconds=1)])
def test_authorization_window_must_be_after_record_and_at_most_24_hours(close):
    with pytest.raises(ValueError):
        _auth(_genesis(), window_closes_at_utc=close)
    _auth(_genesis(), window_closes_at_utc=T_REC + timedelta(hours=24))  # the inclusive upper bound


@pytest.mark.parametrize("bad", [-1, True, "4", None])
def test_prior_run_number_must_be_a_non_negative_strict_int(bad):
    with pytest.raises(ValueError):
        _auth(_genesis(), prior_official_gate_run_number=bad)


def test_authorization_microseconds_are_refused_as_not_server_time():
    with pytest.raises(ValueError):
        _auth(_genesis(), recorded_at_utc=T_REC.replace(microsecond=5))


# ======================================================================
# Append (the only write operations)
# ======================================================================


def test_append_genesis_once_then_authorization_once(tmp_path):
    path = tmp_path / "latch.jsonl"
    genesis = lt.append_genesis(path, server_now_utc=T_GEN)
    assert lt.load_latch(path) == (genesis,)
    with pytest.raises(lt.LatchError, match="exactly once"):
        lt.append_genesis(path, server_now_utc=T_GEN)
    auth = lt.append_attempt_authorized(
        path, server_now_utc=T_REC, readiness_source_sha=R, owner_go_ref="owner-go-test",
        window_closes_at_utc=CLOSE, prior_official_gate_run_number=F,
    )
    assert lt.load_latch(path) == (genesis, auth)
    assert auth.prev_record_sha256 == lt.record_sha256(genesis)
    with pytest.raises(lt.LatchError, match="UNARMED"):
        lt.append_attempt_authorized(
            path, server_now_utc=T_REC, readiness_source_sha=R, owner_go_ref="owner-go-test",
            window_closes_at_utc=CLOSE, prior_official_gate_run_number=F,
        )
    assert len(lt.load_latch(path)) == 2


def test_append_authorization_refuses_a_missing_latch_and_an_earlier_than_genesis_time(tmp_path):
    with pytest.raises(lt.LatchError):
        lt.append_attempt_authorized(
            tmp_path / "absent.jsonl", server_now_utc=T_REC, readiness_source_sha=R, owner_go_ref="g",
            window_closes_at_utc=CLOSE, prior_official_gate_run_number=F,
        )
    path = tmp_path / "latch.jsonl"
    lt.append_genesis(path, server_now_utc=T_REC)
    with pytest.raises(lt.LatchError, match="earlier"):
        lt.append_attempt_authorized(
            path, server_now_utc=T_REC - timedelta(minutes=1), readiness_source_sha=R, owner_go_ref="g",
            window_closes_at_utc=T_REC + timedelta(hours=1), prior_official_gate_run_number=F,
        )
    assert len(lt.load_latch(path)) == 1


def test_append_api_has_no_default_clock_or_run_number():
    for function in (lt.append_genesis, lt.append_attempt_authorized):
        parameters = inspect.signature(function).parameters
        for name, parameter in parameters.items():
            if name == "path":
                continue
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, name
            assert parameter.default is inspect.Parameter.empty, name


def test_a_partial_write_makes_every_later_load_fail_closed(tmp_path):
    path = tmp_path / "latch.jsonl"
    lt.append_genesis(path, server_now_utc=T_GEN)
    with open(path, "ab") as handle:
        handle.write(_line(_auth(_genesis()))[:40].encode())
    with pytest.raises(lt.LatchError):
        lt.load_latch(path)
    with pytest.raises(lt.LatchError):
        lt.append_attempt_authorized(
            path, server_now_utc=T_REC, readiness_source_sha=R, owner_go_ref="g",
            window_closes_at_utc=CLOSE, prior_official_gate_run_number=F,
        )


def test_module_has_no_update_delete_reset_or_reopen_api_and_never_reads_a_local_clock():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    forbidden = ("update", "delete", "truncate", "reset", "reopen", "repair", "remove", "extend", "amend", "consume")
    assert not [n for n in names if any(word in n.lower() for word in forbidden)]
    _assert_no_local_clock(tree)
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "receipts" not in imported and ".receipts" not in imported  # durability independent of receipts


def _assert_no_local_clock(tree) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("now", "utcnow", "today", "time", "time_ns"), ast.dump(node)


def test_latch_admission_path_in_common_never_reads_a_local_clock():
    tree = ast.parse(COMMON_PATH.read_text(encoding="utf-8"))
    functions = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    for name in ("gather_latch_facts", "replacement_latch_admission", "load_replacement_latch",
                 "assert_no_replacement_gate_evidence_visible", "assert_replacement_history_permits"):
        _assert_no_local_clock(functions[name])


# ======================================================================
# Admission: the happy path and R2/R4 (admission boundary)
# ======================================================================


def test_first_dispatch_with_an_empty_interval_is_admitted():
    verdict = _verdict()
    assert verdict == lt.LatchVerdict(admitted=True, state="ADMISSION_OPEN", reason=None)


def test_unarmed_and_missing_facts_never_admit():
    _refused(lt.latch_verdict((_genesis(),), None), "LATCH_UNARMED")
    _refused(lt.latch_verdict(_records(), None), "FACTS_MISSING")


def test_job_started_before_close_but_server_now_at_or_after_close_is_not_admitted():
    for now in (CLOSE, CLOSE + timedelta(seconds=1), CLOSE + timedelta(days=30)):
        verdict = _verdict(server_now_utc=now, job_started_at_utc=CLOSE - timedelta(minutes=5))
        assert verdict.state == "ADMISSION_CLOSED"
        _refused(verdict, "ADMISSION_WINDOW_CLOSED")


def test_one_second_before_close_is_admitted():
    verdict = _verdict(server_now_utc=CLOSE - timedelta(seconds=1), job_started_at_utc=CLOSE - timedelta(minutes=5))
    assert verdict.admitted is True


def test_job_started_at_close_is_not_admitted():
    _refused(
        _verdict(server_now_utc=CLOSE - timedelta(seconds=1), job_started_at_utc=CLOSE),
        "JOB_STARTED_AT_OR_AFTER_CLOSE",
    )


def test_server_now_earlier_than_job_start_refuses():
    _refused(_verdict(server_now_utc=JOB_START - timedelta(seconds=1)), "TIMESTAMP_INCONSISTENT")


def test_admission_state_is_one_way_in_server_time():
    records = _records()
    moments = [T_REC, T_A, NOW, CLOSE - timedelta(seconds=1), CLOSE, CLOSE + timedelta(hours=1), datetime(2031, 1, 1, tzinfo=UTC)]
    states = [lt.admission_state(records, moment) for moment in moments]
    assert states == ["ADMISSION_OPEN"] * 4 + ["ADMISSION_CLOSED"] * 3


def test_rerun_and_wrong_workflow_are_not_admitted():
    _refused(_verdict(ctx_run_attempt=2), "RERUN_ATTEMPT")
    _refused(_verdict(ctx_workflow_identity=".github/workflows/sentinel-timing-rehearsal.yml"), "WORKFLOW_MISMATCH")


@pytest.mark.parametrize("change", [
    dict(sha="d" * 40), dict(workflow_path=".github/workflows/ci.yml"), dict(event="push"),
    dict(head_branch="other"), dict(run_attempt=2), dict(run_number=None), dict(run_id="1"),
])
def test_current_run_identity_mismatch_refuses(change):
    records = _records()
    facts = _facts(records)
    _refused(lt.latch_verdict(records, replace(facts, current_run=replace(facts.current_run, **change))),
             "CURRENT_RUN_IDENTITY_MISMATCH")


# ======================================================================
# R1: commit A and its server-side push anchor
# ======================================================================


def test_commit_a_must_be_the_dispatched_sha():
    records = _records()
    _refused(_verdict(records, commit=_commit(records, sha="d" * 40)), "COMMIT_A_NOT_DISPATCHED_SHA")


@pytest.mark.parametrize("parents", [("d" * 40,), (R, "d" * 40), ()])
def test_commit_a_must_have_exactly_parent_r(parents):
    records = _records()
    _refused(_verdict(records, commit=_commit(records, parents=parents)), "COMMIT_A_PARENT_MISMATCH")


@pytest.mark.parametrize("file_change", [
    dict(filename="STATE.md"), dict(status="added"), dict(additions=2), dict(deletions=1), dict(patch=None),
])
def test_commit_a_file_shape_mismatch_refuses(file_change):
    records = _records()
    _refused(_verdict(records, commit=_commit(records, file=file_change)), "COMMIT_A_DIFF_MISMATCH")


def test_commit_a_patch_must_add_exactly_this_authorization_line():
    records = _records()
    genesis_line, auth_line = _line(records[0]), _line(records[1])
    other = _line(_auth(records[0], owner_go_ref="a-different-go"))
    for patch in (
        f"@@ -1 +1,2 @@\n {genesis_line}\n+{other}",
        f"@@ -1 +1,3 @@\n {genesis_line}\n+{auth_line}\n+{auth_line}",
        f"@@ -1 +1 @@\n-{genesis_line}\n+{auth_line}",
    ):
        _refused(_verdict(records, commit=_commit(records, file=dict(patch=patch))), "COMMIT_A_DIFF_MISMATCH")


def test_commit_a_with_an_extra_file_refuses():
    records = _records()
    commit = _commit(records)
    extra = CommitFile(filename="STATE.md", status="modified", additions=1, deletions=0, patch="+x")
    _refused(_verdict(records, commit=replace(commit, files=commit.files + (extra,))), "COMMIT_A_DIFF_MISMATCH")


@pytest.mark.parametrize("pushes", [
    (),
    (PushActivity(before=R, after=A, ref="refs/heads/main", activity_type="push", timestamp=T_A),) * 2,
    (PushActivity(before="d" * 40, after=A, ref="refs/heads/main", activity_type="push", timestamp=T_A),),
    (PushActivity(before=R, after=A, ref="refs/heads/other", activity_type="push", timestamp=T_A),),
    (PushActivity(before=R, after=A, ref="refs/heads/main", activity_type="force_push", timestamp=T_A),),
], ids=["none", "duplicate", "before", "ref", "force-push"])
def test_push_anchor_must_be_exactly_one_fast_forward_push_from_r(pushes):
    _refused(_verdict(push_activities=pushes), "PUSH_ANCHOR_MISSING_OR_AMBIGUOUS")


def test_the_anchor_is_the_push_record_not_the_authorization_record_time():
    records = _records()
    early_push = PushActivity(before=R, after=A, ref="refs/heads/main", activity_type="push",
                              timestamp=T_REC - timedelta(seconds=1))
    _refused(_verdict(records, push_activities=(early_push,)), "TIMESTAMP_INCONSISTENT")
    # a job that started before A was pushed cannot be a run of A
    _refused(_verdict(records, job_started_at_utc=T_A - timedelta(seconds=1),
                      server_now_utc=T_A), "TIMESTAMP_INCONSISTENT")


def test_a_push_at_or_after_close_is_never_admitted():
    late = PushActivity(before=R, after=A, ref="refs/heads/main", activity_type="push", timestamp=CLOSE)
    assert _verdict(push_activities=(late,)).admitted is False


# ======================================================================
# R5: run-number continuity and the floor
# ======================================================================


def test_contiguous_prior_numbers_all_proven_pre_marker_are_admitted():
    prior = (_run(5), _run(6), _run(7))
    assert _verdict(current_number=8, prior=prior).admitted is True


def test_run_5_proven_pre_marker_admits_run_6():
    assert _verdict(current_number=6, prior=(_run(5),)).admitted is True


def test_run_5_with_marker_success_refuses_run_6():
    prior = (_run(5),)
    jobs = {(prior[0].run_id, 1): (_pre_marker_job(prior[0].run_id, marker="success", execute="success",
                                                    conclusion="cancelled"),)}
    assert _verdict(current_number=6, prior=prior, prior_jobs=jobs).admitted is False


def test_one_missing_number_refuses():
    _refused(_verdict(current_number=7, prior=(_run(6),)), "RUN_NUMBER_MISSING")


def test_a_duplicate_number_refuses():
    prior = (_run(5), _run(5, run_id="9999"))
    _refused(_verdict(current_number=6, prior=prior), "RUN_NUMBER_DUPLICATE")


def test_the_current_run_must_be_visible_exactly_once():
    records = _records()
    facts = _facts(records)
    without_current = tuple(r for r in facts.workflow_runs if r.run_id != CTX_RUN_ID)
    _refused(lt.latch_verdict(records, replace(facts, workflow_runs=without_current)), "RUN_LISTING_INCOMPLETE")
    doubled = facts.workflow_runs + (facts.current_run,)
    _refused(lt.latch_verdict(records, replace(facts, workflow_runs=doubled)), "RUN_LISTING_INCOMPLETE")
    unnumbered = facts.workflow_runs + (replace(_run(99), run_number=None),)
    _refused(lt.latch_verdict(records, replace(facts, workflow_runs=unnumbered)), "RUN_LISTING_INCOMPLETE")


@pytest.mark.parametrize("current_number", [F, F - 1])
def test_the_floor_is_never_an_authorized_run(current_number):
    _refused(_verdict(current_number=current_number, floor_runs=()), "RUN_NUMBER_NOT_ABOVE_FLOOR")


def test_a_run_at_the_floor_is_outside_the_interval_even_with_marker_success():
    floor_run = _run(F, conclusion="cancelled")
    # no job evidence is supplied for it at all: it is never inspected
    assert _verdict(current_number=F + 1, floor_runs=(floor_run,)).admitted is True


@pytest.mark.parametrize("change", [dict(event="push"), dict(workflow_path=".github/workflows/ci.yml"),
                                    dict(head_branch="other")])
def test_an_accounted_run_with_unexpected_identity_refuses(change):
    prior = (replace(_run(5), **change),)
    _refused(_verdict(current_number=6, prior=prior), "PRIOR_RUN_IDENTITY_MISMATCH")


# ======================================================================
# R3: step-level proof that every accounted earlier run stopped before the marker
# ======================================================================


def _one_prior(job=None, *, run=None):
    run = run or _run(5)
    jobs = {(run.run_id, attempt): (job or _pre_marker_job(run.run_id),) for attempt in range(1, run.run_attempt + 1)}
    return _verdict(current_number=6, prior=(run,), prior_jobs=jobs)


@pytest.mark.parametrize("marker", ["success", "failure", "cancelled", None])
def test_marker_step_not_proven_skipped_refuses(marker):
    _refused(_one_prior(_pre_marker_job("9005", marker=marker)), "PRIOR_MARKER_STEP_NOT_PROVEN_SKIPPED")


def test_missing_or_duplicate_marker_step_refuses():
    job = _pre_marker_job("9005")
    without = replace(job, steps=tuple(s for s in job.steps if s.name != lt.MARKER_STEP_NAME))
    _refused(_one_prior(without), "PRIOR_MARKER_STEP_NOT_PROVEN_SKIPPED")
    doubled = _pre_marker_job("9005", extra_steps=(_step(lt.MARKER_STEP_NAME, "skipped", number=30),))
    _refused(_one_prior(doubled), "PRIOR_MARKER_STEP_NOT_PROVEN_SKIPPED")


def test_execute_step_not_proven_skipped_refuses():
    _refused(_one_prior(_pre_marker_job("9005", execute="success")), "PRIOR_EXECUTE_STEP_NOT_PROVEN_SKIPPED")


@pytest.mark.parametrize("status,conclusion", [("in_progress", None), ("completed", "cancelled"),
                                               ("completed", "success")])
def test_job_not_a_completed_failure_refuses(status, conclusion):
    _refused(_one_prior(_pre_marker_job("9005", status=status, conclusion=conclusion)),
             "PRIOR_JOB_NOT_PRE_MARKER_FAILURE")


def test_missing_or_ambiguous_gate_job_refuses():
    run = _run(5)
    for jobs in ((), (_pre_marker_job(run.run_id, name="other"),),
                 (_pre_marker_job(run.run_id), _pre_marker_job(run.run_id))):
        _refused(_verdict(current_number=6, prior=(run,), prior_jobs={(run.run_id, 1): jobs}),
                 "PRIOR_JOB_MISSING_OR_AMBIGUOUS")


def test_an_unresolved_prior_run_refuses():
    _refused(_one_prior(run=_run(5, status="in_progress", conclusion=None)), "PRIOR_RUN_UNRESOLVED")


def test_missing_attempt_evidence_refuses():
    run = _run(5, attempt=2)
    jobs = {(run.run_id, 1): (_pre_marker_job(run.run_id),)}
    _refused(_verdict(current_number=6, prior=(run,), prior_jobs=jobs), "PRIOR_RUN_EVIDENCE_INCOMPLETE")


def test_a_later_attempt_with_marker_success_refuses():
    run = _run(5, attempt=2)
    jobs = {
        (run.run_id, 1): (_pre_marker_job(run.run_id),),
        (run.run_id, 2): (_pre_marker_job(run.run_id, marker="success"),),
    }
    _refused(_verdict(current_number=6, prior=(run,), prior_jobs=jobs), "PRIOR_MARKER_STEP_NOT_PROVEN_SKIPPED")


def test_the_real_run_3_step_shape_keeps_eligibility():
    assert _one_prior().admitted is True


# ======================================================================
# Workflow and hook pins
# ======================================================================


def test_step_and_job_names_equal_the_official_gate_workflow():
    data = yaml.safe_load(GATE_WORKFLOW.read_text(encoding="utf-8"))
    job = data["jobs"]["gate"]
    assert job["name"] == lt.GATE_JOB_NAME
    steps = {step.get("id"): step for step in job["steps"] if step.get("id")}
    marker = steps["marker"]
    assert marker["name"] == lt.MARKER_STEP_NAME
    assert marker["uses"].startswith("actions/upload-artifact@")
    assert "if" not in marker
    assert steps["execute"]["name"] == lt.EXECUTE_STEP_NAME
    assert list(data[True]) == [lt.DISPATCH_EVENT]
    assert data["concurrency"]["group"] == "sentinel-oneshot-p5d"
    assert data["concurrency"]["cancel-in-progress"] is False
    names = [step.get("name") for step in job["steps"]]
    assert names.count(lt.MARKER_STEP_NAME) == 1 and names.count(lt.EXECUTE_STEP_NAME) == 1


def test_pre_push_hook_carries_the_latch_append_only_guard():
    text = PRE_PUSH_PATH.read_text(encoding="utf-8")
    guard_start = text.index('LATCH="artifacts/phase5_replacement_latch.jsonl"')
    block = text[guard_start:]
    assert 'if git diff "$base" HEAD -- "$LATCH" | grep -E \'^-[^-]\' >/dev/null; then' in block
    assert "PRE-PUSH BLOCK: $LATCH has removed or rewritten established latch content (append-only)." in block
    assert text.index('REGISTRY="artifacts/phase5_receipt_registry.jsonl"') < guard_start
    assert guard_start < text.rindex('echo "pre-push: Tier 0 + leak-grep PASS"')
    latch_lines = [line for line in block.splitlines() if "$LATCH" in line and "git diff" in line]
    assert latch_lines and all("--diff-filter" not in line for line in latch_lines)


# ======================================================================
# _phase5_common: loading, gate-evidence guard, fact gathering
# ======================================================================


def test_load_replacement_latch_reads_the_committed_latch_and_fails_closed(tmp_path):
    assert common.load_replacement_latch() == lt.load_latch(COMMITTED_LATCH)
    with pytest.raises(common.Phase5ScriptError, match="replacement latch failed to load"):
        common.load_replacement_latch(tmp_path / "absent.jsonl")


class _ArtifactClient:
    def __init__(self, refs=(), error=None):
        self._refs, self._error, self.prefixes = list(refs), error, []

    def list_artifacts(self, prefix):
        self.prefixes.append(prefix)
        if self._error is not None:
            raise self._error
        return self._refs


def test_gate_evidence_guard_refuses_any_visible_evidence_or_any_discovery_error():
    client = _ArtifactClient()
    common.assert_no_replacement_gate_evidence_visible(client)
    assert client.prefixes == ["sentinel-p5-gate-evidence-"]
    with pytest.raises(common.Phase5ScriptError, match="already visible"):
        common.assert_no_replacement_gate_evidence_visible(_ArtifactClient(refs=[object()]))
    with pytest.raises(common.Phase5ScriptError, match="discovery failed"):
        common.assert_no_replacement_gate_evidence_visible(_ArtifactClient(error=GithubEvidenceError("x")))


class _Ctx:
    run_id = CTX_RUN_ID
    run_attempt = 1
    sha = A
    workflow_path = WF


_ENV = {"GITHUB_JOB": "gate", "RUNNER_NAME": "GitHub Actions 7"}


class _FactsClient:
    """Answers every read gather_latch_facts makes, recording the order.
    ``server_times`` is consumed one per ``server_time_utc`` call."""

    def __init__(self, records, *, current_number=F + 1, prior=(), server_times=(NOW,), fail=None):
        facts = _facts(records, current_number=current_number, prior=prior)
        self._facts, self._server_times, self._fail = facts, list(server_times), fail
        self.calls: list[str] = []

    def _call(self, name):
        self.calls.append(name)
        if self._fail == name:
            raise GithubEvidenceError(f"{name} failed")

    def get_run(self, run_id):
        self._call("get_run")
        return self._facts.current_run

    def get_commit(self, sha):
        self._call("get_commit")
        return self._facts.commit

    def list_push_activity(self, ref):
        self._call("list_push_activity")
        return list(self._facts.push_activities)

    def list_run_attempt_jobs(self, run_id, attempt):
        self._call("list_run_attempt_jobs")
        return [JobDetail(id=1, run_id=run_id, name="gate", status="in_progress", started_at=JOB_START,
                          runner_name=_ENV["RUNNER_NAME"])]

    def list_workflow_runs_counted(self, workflow_path, *, created_after, created_before):
        self._call("list_workflow_runs_counted")
        assert workflow_path == WF
        return list(self._facts.workflow_runs)

    def list_run_attempt_job_evidence(self, run_id, attempt):
        self._call("list_run_attempt_job_evidence")
        return list(self._facts.prior_attempt_jobs[(run_id, attempt)])

    def server_time_utc(self):
        self._call("server_time_utc")
        return self._server_times.pop(0)


def test_gather_reads_server_time_last_and_the_admission_is_computed_once():
    records = _records()
    client = _FactsClient(records, current_number=6, prior=(_run(5),))
    verdict = common.replacement_latch_admission(client, _Ctx(), records, _ENV, expected_api_job_name="gate")
    assert verdict.admitted is True
    assert client.calls[-1] == "server_time_utc"
    assert client.calls.count("server_time_utc") == 1
    assert client.calls.count("list_run_attempt_job_evidence") == 1


def test_an_unarmed_latch_refuses_without_any_github_read(tmp_path):
    class _NoReads:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected GitHub read {name}")

    verdict = common.replacement_latch_admission(
        _NoReads(), _Ctx(), lt.load_latch(_committed_genesis_view(tmp_path)), _ENV, expected_api_job_name="gate",
    )
    _refused(verdict, "LATCH_UNARMED")


@pytest.mark.parametrize("failing", ["get_run", "get_commit", "list_push_activity", "list_run_attempt_jobs",
                                     "list_workflow_runs_counted", "list_run_attempt_job_evidence",
                                     "server_time_utc"])
def test_any_failed_github_read_refuses(failing):
    records = _records()
    client = _FactsClient(records, current_number=6, prior=(_run(5),), fail=failing)
    with pytest.raises(common.Phase5ScriptError, match="facts unavailable"):
        common.replacement_latch_admission(client, _Ctx(), records, _ENV, expected_api_job_name="gate")


def test_a_job_anchor_failure_refuses():
    records = _records()
    client = _FactsClient(records)
    with pytest.raises(common.Phase5ScriptError, match="job anchor"):
        common.replacement_latch_admission(
            client, _Ctx(), records, {**_ENV, "RUNNER_NAME": "someone else"}, expected_api_job_name="gate",
        )


def test_a_server_clock_before_the_job_start_refuses_through_the_anchor():
    records = _records()
    client = _FactsClient(records, server_times=(JOB_START - timedelta(seconds=1),))
    with pytest.raises(common.Phase5ScriptError, match="job anchor"):
        common.replacement_latch_admission(client, _Ctx(), records, _ENV, expected_api_job_name="gate")

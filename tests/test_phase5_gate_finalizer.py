"""Tests for scripts/run_phase5_gate_finalizer.py (ADR-0012 sections 4,
10, 21 and Amendment A2/A5/A6; dispatch
q77-p5d-repair-stage2b2-implement-a, Stage 2B-2).

Model-free and network-blocked (tests/conftest.py ``block_network``):
every REST seam is an in-memory fake with an injected clock. Nothing here
creates or consumes a marker, dispatches a workflow, or arms the
replacement. Scripts are loaded by file path, never imported as the
``scripts`` package (tests/test_dependency_surface.py).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contracts.schemas import CostRow
from sentinel.phase5 import terminal as t
from sentinel.phase5.evidence_records import GateEvidenceRecord
from sentinel.phase5.github_evidence import ArtifactDetail, ArtifactUnsafe, GithubEvidenceError
from sentinel.phase5.journal import OperationalJournal, read_journal

REPO_ROOT = Path(__file__).resolve().parent.parent
FINALIZER_PATH = REPO_ROOT / "scripts" / "run_phase5_gate_finalizer.py"
RUNNER_PATH = REPO_ROOT / "scripts" / "run_phase5_official_gate.py"

SHA = "a" * 40
WIF = "github-actions-wif-federation"
WORKFLOW = ".github/workflows/sentinel-official-gate.yml"
RUN_ID = "4242"
ORIGINAL_PURPOSE = "P5D_OFFICIAL_SONNET_GATE"
ARTIFACT_NAME = f"sentinel-p5-gate-evidence-r{RUN_ID}-a1"
DIGEST = "ab" * 32
GH_ENV = {
    "GITHUB_REPOSITORY": "kobescak-kristian/ai-portfolio-sentinel",
    "GITHUB_REPOSITORY_OWNER": "kobescak-kristian",
    "GITHUB_RUN_ID": RUN_ID,
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": SHA,
    "GITHUB_WORKFLOW_REF": f"kobescak-kristian/ai-portfolio-sentinel/{WORKFLOW}@refs/heads/main",
    "GITHUB_API_URL": "https://api.github.com",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_TOKEN": "test-token",
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fin():
    return _load(FINALIZER_PATH, "run_phase5_gate_finalizer")


@pytest.fixture(scope="module")
def runner():
    return _load(RUNNER_PATH, "run_phase5_official_gate_for_finalizer_tests")


def _identity(**overrides) -> t.TerminalIdentity:
    fields = dict(
        workflow_identity=WORKFLOW, run_id=RUN_ID, run_attempt=1, event="workflow_dispatch",
        ref="refs/heads/main", source_sha=SHA, expected_source_sha=SHA, purpose=ORIGINAL_PURPOSE,
    )
    fields.update(overrides)
    return t.TerminalIdentity(**fields)


def _quality_record(disposition="GREEN", **overrides) -> GateEvidenceRecord:
    fields = dict(
        schema_version=1, workflow_identity=WORKFLOW, github_run_id=RUN_ID, run_attempt=1,
        event="workflow_dispatch", ref="refs/heads/main", source_sha=SHA,
        created_at_utc=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc), steps=(),
        expected_source_sha=SHA, model="claude-sonnet-5", profile_name="sonnet-official-gate",
        run_ids=("r-1", "r-2"), scoring={"emitted": 3}, thresholds={}, invariant_results={"ok": True},
        execution_validity={"valid": True}, miss_patterns=(),
        failed_checks=("pooled_recall: 1/3 -> FAIL",) if disposition == "HONEST_FAIL" else (),
        cost_rows=(CostRow(
            schema_version=1, run_id="r-1", recorded_at_utc=datetime(2026, 9, 15, tzinfo=timezone.utc),
            run_kind="dev", model="claude-sonnet-5", input_tokens=1, output_tokens=1, cost_eur_micros=2_000,
        ),),
        accounted_total_eur_micros=2_000, disposition=disposition, auth_mode=WIF,
    )
    fields.update(overrides)
    return GateEvidenceRecord(**fields)


def _infra_record(runner, writer="RUNNER") -> GateEvidenceRecord:
    raw = t.build_invalid_record(
        identity=_identity(), envelope=None, created_at_utc=datetime(2026, 9, 15, tzinfo=timezone.utc),
        model="claude-sonnet-5", profile_name="sonnet-official-gate",
        infrastructure_cause="RUNNER_EXCEPTION", writer=writer,
    )
    return runner.attribute_invalid_record(raw, purpose=ORIGINAL_PURPOSE)


def _bytes(record: GateEvidenceRecord) -> bytes:
    return record.model_dump_json(indent=2).encode("utf-8")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _FakeClient:
    def __init__(self, clock: _Clock, listings, *, costs=None, files=None, download_error=None):
        self._clock = clock
        self._listings = list(listings)
        self._costs = list(costs) if costs is not None else None
        self._files = files if files is not None else {}
        self._download_error = download_error
        self.listing_calls = 0
        self.download_calls = 0

    def list_run_artifacts_named(self, run_id, name):
        self.listing_calls += 1
        self._clock.now += self._costs.pop(0) if self._costs else 1.0
        item = self._listings.pop(0) if self._listings else []
        if isinstance(item, Exception):
            raise item
        return item

    def download_artifact(self, ref, trusted_root, dest):
        self.download_calls += 1
        self._clock.now += 1.0
        if self._download_error is not None:
            raise self._download_error
        dest = Path(dest)
        dest.mkdir()
        for name, data in self._files.items():
            (dest / name).write_bytes(data)
        return dest


def _detail(**overrides) -> ArtifactDetail:
    fields = dict(
        id=77, name=ARTIFACT_NAME, workflow_run_id=RUN_ID, expired=False,
        digest="sha256:" + DIGEST, size_in_bytes=100,
    )
    fields.update(overrides)
    return ArtifactDetail(**fields)


def _roots(tmp_path: Path):
    work = tmp_path / "p5-gate"
    work.mkdir()
    root = work / "artifacts"
    for d in (root, work / "terminal-staging", work / "terminal-quarantine"):
        d.mkdir()
    return work, root


def _confirm(fin, tmp_path, client, clock, *, upload_outcome="success", uploaded_id="77",
             uploaded_digest=DIGEST, local_bytes=None, identity=None):
    work, root = _roots(tmp_path)
    if local_bytes is not None:
        (root / t.TERMINAL_FILENAME).write_bytes(local_bytes)
    return fin.confirm_publication(
        client=client, identity=identity or _identity(), artifact_name=ARTIFACT_NAME,
        upload_outcome=upload_outcome, uploaded_artifact_id=uploaded_id, uploaded_digest=uploaded_digest,
        publication_root=root, work_root=work, sleep=clock.sleep, monotonic=clock.monotonic,
    )


def _published_files(record_bytes: bytes, *, checks=True, extra=None) -> dict:
    files = {t.TERMINAL_FILENAME: record_bytes, t.JOURNAL_FILENAME: b""}
    if checks:
        files[t.CHECKS_FILENAME] = b"[]"
    if extra:
        files.update(extra)
    return files


# ===========================================================================
# confirm: positive absence requires the full bounded observation window
# ===========================================================================


def test_confirm_upload_failure_first_zero_then_strict_valid_artifact_is_published(fin, tmp_path):
    clock = _Clock()
    data = _bytes(_quality_record("HONEST_FAIL"))
    client = _FakeClient(clock, [[], [_detail()]], files=_published_files(data))
    result = _confirm(fin, tmp_path, client, clock, upload_outcome="failure", uploaded_id="", uploaded_digest="",
                      local_bytes=data)
    assert result.state == "TERMINAL_EVIDENCE_PUBLISHED"
    assert result.disposition == "HONEST_FAIL"
    assert client.listing_calls == 2


def test_confirm_upload_failure_complete_zero_window_is_failed_positively_absent(fin, tmp_path):
    clock = _Clock()
    client = _FakeClient(clock, [[], [], []])
    result = _confirm(fin, tmp_path, client, clock, upload_outcome="failure")
    assert (result.state, result.reason) == ("PUBLICATION_FAILED", "POSITIVELY_ABSENT")
    assert client.listing_calls == fin.REQUIRED_ZERO_OBSERVATIONS == 3
    assert clock.sleeps == [fin.LISTING_SLEEP_S, fin.LISTING_SLEEP_S]
    assert client.download_calls == 0


@pytest.mark.parametrize("error", [GithubEvidenceError("HTTP 500"), TimeoutError("timed out")])
def test_confirm_upload_failure_zero_then_rest_error_is_unconfirmed(fin, tmp_path, error):
    clock = _Clock()
    client = _FakeClient(clock, [[], error, []])
    result = _confirm(fin, tmp_path, client, clock, upload_outcome="failure")
    assert (result.state, result.reason) == ("PUBLICATION_UNCONFIRMED", "REST_UNAVAILABLE")


def test_confirm_upload_failure_budget_exhausted_before_complete_zero_set_is_unconfirmed(fin, tmp_path):
    clock = _Clock()
    client = _FakeClient(clock, [[], [], []], costs=[12.0, 12.0, 12.0])
    result = _confirm(fin, tmp_path, client, clock, upload_outcome="failure")
    assert (result.state, result.reason) == ("PUBLICATION_UNCONFIRMED", "CONFIRM_BUDGET_EXHAUSTED")
    assert client.listing_calls == 2


def test_confirm_upload_success_complete_zero_window_is_unconfirmed_not_yet_visible(fin, tmp_path):
    clock = _Clock()
    client = _FakeClient(clock, [[], [], []])
    result = _confirm(fin, tmp_path, client, clock, upload_outcome="success")
    assert (result.state, result.reason) == ("PUBLICATION_UNCONFIRMED", "NOT_YET_VISIBLE")


@pytest.mark.parametrize("upload_outcome", ["failure", "success", "cancelled", ""])
def test_confirm_single_successful_zero_listing_never_yields_failed(fin, tmp_path, upload_outcome):
    clock = _Clock()
    client = _FakeClient(clock, [[], [], []], costs=[27.0, 1.0, 1.0])
    result = _confirm(fin, tmp_path, client, clock, upload_outcome=upload_outcome)
    assert client.listing_calls == 1
    assert result.state == "PUBLICATION_UNCONFIRMED"
    assert result.state != "PUBLICATION_FAILED"


def test_confirm_late_completing_zero_attempt_not_counted(fin, tmp_path):
    clock = _Clock()
    client = _FakeClient(clock, [[], [], []], costs=[1.0, 1.0, 25.0])
    result = _confirm(fin, tmp_path, client, clock, upload_outcome="failure")
    assert client.listing_calls == 3
    assert clock.now > fin.LISTING_DEADLINE_S
    assert (result.state, result.reason) == ("PUBLICATION_UNCONFIRMED", "CONFIRM_BUDGET_EXHAUSTED")


def test_confirm_rest_error_then_later_match_still_publishes(fin, tmp_path):
    clock = _Clock()
    data = _bytes(_quality_record("GREEN"))
    client = _FakeClient(clock, [GithubEvidenceError("HTTP 502"), [_detail()]], files=_published_files(data))
    result = _confirm(fin, tmp_path, client, clock, upload_outcome="failure", local_bytes=data)
    assert result.state == "TERMINAL_EVIDENCE_PUBLISHED"


# ===========================================================================
# confirm: strict-valid published bytes are authoritative
# ===========================================================================


def test_confirm_local_equals_downloaded_published_no_warning(fin, tmp_path):
    clock = _Clock()
    data = _bytes(_quality_record("GREEN"))
    client = _FakeClient(clock, [[_detail()]], files=_published_files(data))
    result = _confirm(fin, tmp_path, client, clock, local_bytes=data)
    assert result.state == "TERMINAL_EVIDENCE_PUBLISHED"
    assert result.warnings == ()
    assert result.artifact_id == 77


def test_confirm_local_candidate_changed_after_upload_stays_published_with_local_payload_sha_mismatch(fin, tmp_path):
    clock = _Clock()
    published = _bytes(_quality_record("GREEN"))
    local = _bytes(_quality_record("GREEN", created_at_utc=datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)))
    assert hashlib.sha256(published).hexdigest() != hashlib.sha256(local).hexdigest()
    client = _FakeClient(clock, [[_detail()]], files=_published_files(published))
    result = _confirm(fin, tmp_path, client, clock, local_bytes=local)
    assert result.state == "TERMINAL_EVIDENCE_PUBLISHED"
    assert result.warnings == ("LOCAL_PAYLOAD_SHA_MISMATCH",)


@pytest.mark.parametrize("published", [
    _bytes(_quality_record("GREEN", run_attempt=2)),
    b"{not json",
    json.dumps({"disposition": "GREEN"}).encode("utf-8"),
    _bytes(_quality_record("GREEN", terminal_writer="RUNNER")),
], ids=["wrong-attempt", "tampered", "schema-invalid", "writer-under-original-purpose"])
def test_confirm_downloaded_untrusted_never_confirmed(fin, tmp_path, published):
    clock = _Clock()
    client = _FakeClient(clock, [[_detail()]], files=_published_files(published))
    result = _confirm(fin, tmp_path, client, clock, local_bytes=_bytes(_quality_record("GREEN")))
    assert (result.state, result.reason) == ("PUBLICATION_FAILED", "PUBLISHED_EVIDENCE_UNTRUSTED")
    assert result.disposition is None


@pytest.mark.parametrize("disposition", ["GREEN", "HONEST_FAIL"])
@pytest.mark.parametrize("local_mutation", ["deleted", "corrupted", "different-trusted"])
def test_confirm_published_quality_never_downgraded_by_local_inconsistency(
    fin, tmp_path, capsys, monkeypatch, disposition, local_mutation,
):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    clock = _Clock()
    published = _bytes(_quality_record(disposition))
    local = {
        "deleted": None,
        "corrupted": b"\x00garbage",
        "different-trusted": _bytes(_quality_record(
            disposition, created_at_utc=datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc),
        )),
    }[local_mutation]
    client = _FakeClient(clock, [[_detail()]], files=_published_files(published))
    result = _confirm(fin, tmp_path, client, clock, local_bytes=local)
    assert result.state == "TERMINAL_EVIDENCE_PUBLISHED"
    expected_warning = "LOCAL_PAYLOAD_SHA_MISMATCH" if local_mutation == "different-trusted" else "LOCAL_CANDIDATE_UNAVAILABLE"
    assert result.warnings == (expected_warning,)
    assert fin.report_confirmation(result, ARTIFACT_NAME) == 0
    out = capsys.readouterr().out
    assert f"DISPOSITION: {disposition}\n" in out
    assert "state=TERMINAL_EVIDENCE_PUBLISHED" in out


# ===========================================================================
# confirm: identity, digest, tree and budget checks
# ===========================================================================


def test_confirm_multiple_matches_is_unconfirmed_ambiguous(fin, tmp_path):
    clock = _Clock()
    client = _FakeClient(clock, [[_detail(), _detail(id=78)]])
    result = _confirm(fin, tmp_path, client, clock)
    assert (result.state, result.reason) == ("PUBLICATION_UNCONFIRMED", "AMBIGUOUS")


@pytest.mark.parametrize("overrides", [{"workflow_run_id": "999"}, {"expired": True}])
def test_confirm_artifact_identity_invalid_is_failed(fin, tmp_path, overrides):
    clock = _Clock()
    client = _FakeClient(clock, [[_detail(**overrides)]])
    result = _confirm(fin, tmp_path, client, clock)
    assert (result.state, result.reason) == ("PUBLICATION_FAILED", "ARTIFACT_IDENTITY_INVALID")


def test_confirm_artifact_id_mismatch_is_failed(fin, tmp_path):
    clock = _Clock()
    client = _FakeClient(clock, [[_detail()]])
    result = _confirm(fin, tmp_path, client, clock, uploaded_id="76")
    assert (result.state, result.reason) == ("PUBLICATION_FAILED", "ARTIFACT_ID_MISMATCH")


def test_confirm_digest_mismatch_is_failed_and_absent_digest_is_skipped(fin, tmp_path):
    clock = _Clock()
    client = _FakeClient(clock, [[_detail()]])
    (tmp_path / "a").mkdir()
    result = _confirm(fin, tmp_path / "a", client, clock, uploaded_digest="cd" * 32)
    assert (result.state, result.reason) == ("PUBLICATION_FAILED", "DIGEST_MISMATCH")

    clock = _Clock()
    data = _bytes(_quality_record("GREEN"))
    client = _FakeClient(clock, [[_detail(digest=None)]], files=_published_files(data))
    (tmp_path / "b").mkdir()
    result = _confirm(fin, tmp_path / "b", client, clock, uploaded_digest="cd" * 32, local_bytes=data)
    assert result.state == "TERMINAL_EVIDENCE_PUBLISHED"


def test_confirm_extra_file_or_unsafe_zip_is_tree_invalid(fin, tmp_path):
    data = _bytes(_quality_record("GREEN"))
    clock = _Clock()
    client = _FakeClient(clock, [[_detail()]], files=_published_files(data, extra={"stray.txt": b"x"}))
    (tmp_path / "a").mkdir()
    result = _confirm(fin, tmp_path / "a", client, clock, local_bytes=data)
    assert (result.state, result.reason) == ("PUBLICATION_FAILED", "TREE_INVALID")

    clock = _Clock()
    client = _FakeClient(clock, [[_detail()]], download_error=ArtifactUnsafe("traversal"))
    (tmp_path / "b").mkdir()
    result = _confirm(fin, tmp_path / "b", client, clock, local_bytes=data)
    assert (result.state, result.reason) == ("PUBLICATION_FAILED", "TREE_INVALID")


def test_confirm_download_transport_error_is_unconfirmed(fin, tmp_path):
    clock = _Clock()
    client = _FakeClient(clock, [[_detail()]], download_error=GithubEvidenceError("transport"))
    result = _confirm(fin, tmp_path, client, clock)
    assert (result.state, result.reason) == ("PUBLICATION_UNCONFIRMED", "DOWNLOAD_UNAVAILABLE")


def test_confirm_invalid_published_exits_1_and_prints_invalid_disposition(fin, runner, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    clock = _Clock()
    data = _bytes(_infra_record(runner))
    client = _FakeClient(clock, [[_detail()]], files=_published_files(data, checks=False))
    result = _confirm(fin, tmp_path, client, clock, local_bytes=data)
    assert result.state == "INVALID_EVIDENCE_PUBLISHED"
    assert result.warnings == ()
    assert fin.report_confirmation(result, ARTIFACT_NAME) == 1
    assert "DISPOSITION: INFRASTRUCTURE_FAILURE" in capsys.readouterr().out


def test_confirm_quality_without_checks_warns_checks_absent(fin, tmp_path):
    clock = _Clock()
    data = _bytes(_quality_record("GREEN"))
    client = _FakeClient(clock, [[_detail()]], files=_published_files(data, checks=False))
    result = _confirm(fin, tmp_path, client, clock, local_bytes=data)
    assert result.state == "TERMINAL_EVIDENCE_PUBLISHED"
    assert result.warnings == ("CHECKS_ABSENT",)


def test_confirm_never_mutates_publication_root_or_journal(fin, tmp_path):
    clock = _Clock()
    data = _bytes(_quality_record("GREEN"))
    work, root = _roots(tmp_path)
    (root / t.TERMINAL_FILENAME).write_bytes(data)
    (root / t.JOURNAL_FILENAME).write_bytes(b"journal-bytes")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    client = _FakeClient(clock, [[_detail()]], files=_published_files(data))
    result = fin.confirm_publication(
        client=client, identity=_identity(), artifact_name=ARTIFACT_NAME, upload_outcome="success",
        uploaded_artifact_id="77", uploaded_digest=DIGEST, publication_root=root, work_root=work,
        sleep=clock.sleep, monotonic=clock.monotonic,
    )
    assert result.state == "TERMINAL_EVIDENCE_PUBLISHED"
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


def test_confirm_download_not_started_after_latest_start(fin, tmp_path):
    clock = _Clock()
    client = _FakeClient(clock, [[_detail()]], costs=[fin.LATEST_DOWNLOAD_START_S + 1.0])
    result = _confirm(fin, tmp_path, client, clock)
    assert (result.state, result.reason) == ("PUBLICATION_UNCONFIRMED", "CONFIRM_BUDGET_EXHAUSTED")
    assert client.download_calls == 0


def test_confirm_budget_constants_fit_a_one_minute_step(fin):
    assert fin.CONFIRM_BUDGET_S <= 45.0
    worst_listing = (
        fin.MAX_LISTING_ATTEMPTS * fin.CONFIRM_REQUEST_TIMEOUT_S
        + (fin.MAX_LISTING_ATTEMPTS - 1) * fin.LISTING_SLEEP_S
    )
    assert worst_listing <= fin.LISTING_DEADLINE_S
    assert fin.LATEST_ATTEMPT_START_S + fin.CONFIRM_REQUEST_TIMEOUT_S <= fin.LISTING_DEADLINE_S
    assert fin.LATEST_DOWNLOAD_START_S + fin.CONFIRM_DOWNLOAD_TIMEOUT_S <= fin.CONFIRM_BUDGET_S
    assert fin.MIN_ZERO_OBSERVATION_SPAN_S <= 2 * fin.LISTING_SLEEP_S


def _set_env(monkeypatch, tmp_path, **extra):
    for key, value in GH_ENV.items():
        monkeypatch.setenv(key, value)
    output = tmp_path / "github_output"
    summary = tmp_path / "step_summary"
    output.write_text("", encoding="utf-8")
    summary.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    return output, summary


def _outputs(path: Path) -> dict:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if line)


def test_cmd_confirm_client_timeouts_bounded_and_unconfirmed_prints_no_disposition(fin, tmp_path, monkeypatch, capsys):
    output, summary = _set_env(monkeypatch, tmp_path, UPLOAD_STEP_OUTCOME="success")
    work, root = _roots(tmp_path)
    seen = {}
    clock = _Clock()

    def _build(env, **kwargs):
        seen.update(kwargs)
        return _FakeClient(clock, [GithubEvidenceError("x")] * 3)

    monkeypatch.setattr(fin, "build_evidence_client", _build)
    args = argparse.Namespace(expected_source_sha=SHA, artifacts_dir=root, work_root=work)
    code = fin.cmd_confirm(args, sleep=clock.sleep, monotonic=clock.monotonic)
    assert seen == {"request_timeout_s": 8.0, "download_timeout_s": 12.0}
    assert code == 2
    out = capsys.readouterr().out
    assert "state=PUBLICATION_UNCONFIRMED" in out and "reason=REST_UNAVAILABLE" in out
    assert "DISPOSITION" not in out
    assert _outputs(output)["publication_state"] == "PUBLICATION_UNCONFIRMED"
    assert summary.read_text(encoding="utf-8") == ""


def test_cmd_confirm_published_writes_disposition_and_summary(fin, tmp_path, monkeypatch, capsys):
    output, summary = _set_env(monkeypatch, tmp_path, UPLOAD_STEP_OUTCOME="success",
                               UPLOADED_ARTIFACT_ID="77", UPLOADED_ARTIFACT_DIGEST=DIGEST)
    work, root = _roots(tmp_path)
    data = _bytes(_quality_record("HONEST_FAIL"))
    (root / t.TERMINAL_FILENAME).write_bytes(data)
    clock = _Clock()
    monkeypatch.setattr(fin, "build_evidence_client",
                        lambda env, **kw: _FakeClient(clock, [[_detail()]], files=_published_files(data)))
    args = argparse.Namespace(expected_source_sha=SHA, artifacts_dir=root, work_root=work)
    assert fin.cmd_confirm(args, sleep=clock.sleep, monotonic=clock.monotonic) == 0
    out = capsys.readouterr().out
    assert out.endswith("DISPOSITION: HONEST_FAIL\n")
    assert "HONEST_FAIL" in summary.read_text(encoding="utf-8")
    assert _outputs(output)["publication_state"] == "TERMINAL_EVIDENCE_PUBLISHED"


def test_cmd_confirm_exception_is_unconfirmed_exit_2(fin, tmp_path, monkeypatch, capsys):
    _set_env(monkeypatch, tmp_path)
    monkeypatch.delenv("GITHUB_SHA")
    args = argparse.Namespace(expected_source_sha=SHA, artifacts_dir=tmp_path / "a", work_root=tmp_path)
    assert fin.cmd_confirm(args) == 2
    out = capsys.readouterr().out
    assert "state=PUBLICATION_UNCONFIRMED" in out and "reason=CONFIRM_EXCEPTION" in out
    assert "exception_type=GithubContextError" in out


# ===========================================================================
# finalize
# ===========================================================================


def _finalize_world(runner, tmp_path, monkeypatch, *, marker="success", execute="success", **env):
    output, _summary = _set_env(monkeypatch, tmp_path, MARKER_STEP_OUTCOME=marker, EXECUTE_STEP_OUTCOME=execute, **env)
    work = tmp_path / "p5-gate"
    work.mkdir()
    artifacts = work / "artifacts"
    runner.establish_preflight_journal(artifacts)
    args = argparse.Namespace(expected_source_sha=SHA, artifacts_dir=artifacts)
    return args, artifacts, work, output


def _runner_journal(artifacts: Path, *events):
    journal = OperationalJournal(artifacts / t.JOURNAL_FILENAME, writer="RUNNER").open()
    for event, fields in events:
        journal.append(event, **fields)
    journal.close()
    assert not journal.broken


def _no_rest(monkeypatch, fin):
    def _forbidden(env, **kwargs):
        raise AssertionError("finalize must not query REST here")

    monkeypatch.setattr(fin, "build_evidence_client", _forbidden)


def _events(artifacts: Path):
    result = read_journal(artifacts / t.JOURNAL_FILENAME)
    assert result.integrity == "OK"
    return result.events


def test_finalize_attempt_gt_1_no_terminal_required_no_rest(fin, runner, tmp_path, monkeypatch, capsys):
    args, artifacts, work, output = _finalize_world(runner, tmp_path, monkeypatch, marker="failure", GITHUB_RUN_ATTEMPT="2")
    _no_rest(monkeypatch, fin)
    before = (artifacts / t.JOURNAL_FILENAME).read_bytes()
    assert fin.cmd_finalize(args) == 0
    assert _outputs(output) == {"terminal_required": "false", "decision": "NO_TERMINAL_REQUIRED"}
    assert (artifacts / t.JOURNAL_FILENAME).read_bytes() == before


def test_finalize_marker_skipped_no_terminal_required_root_untouched(fin, runner, tmp_path, monkeypatch):
    args, artifacts, work, output = _finalize_world(runner, tmp_path, monkeypatch, marker="skipped", execute="skipped")
    _no_rest(monkeypatch, fin)
    before = {p.name: p.read_bytes() for p in artifacts.iterdir()}
    assert fin.cmd_finalize(args) == 0
    assert _outputs(output)["terminal_required"] == "false"
    assert {p.name: p.read_bytes() for p in artifacts.iterdir()} == before


@pytest.mark.parametrize("disposition", ["GREEN", "HONEST_FAIL"])
def test_finalize_preserves_trusted_quality_byte_identical(fin, runner, tmp_path, monkeypatch, capsys, disposition):
    args, artifacts, work, output = _finalize_world(runner, tmp_path, monkeypatch)
    _no_rest(monkeypatch, fin)
    data = _bytes(_quality_record(disposition))
    (artifacts / t.TERMINAL_FILENAME).write_bytes(data)
    (artifacts / t.CHECKS_FILENAME).write_bytes(b"[]")
    (artifacts / "stray.txt").write_bytes(b"stray")
    assert fin.cmd_finalize(args) == 0
    assert (artifacts / t.TERMINAL_FILENAME).read_bytes() == data
    assert (artifacts / t.CHECKS_FILENAME).exists()
    assert not (artifacts / "stray.txt").exists()
    assert any(p.name.startswith("unexpected-") for p in (work / "terminal-quarantine").iterdir())
    assert _outputs(output) == {"terminal_required": "true", "decision": "PRESERVE_RUNNER_EVIDENCE"}
    out = capsys.readouterr().out
    assert out == "FINALIZE: action=PRESERVE_RUNNER_EVIDENCE consumption=CONSUMED candidate=TRUSTED_QUALITY\n"
    assert "GREEN" not in out and "HONEST_FAIL" not in out


def test_finalize_preserves_trusted_infra_and_quarantines_checks(fin, runner, tmp_path, monkeypatch):
    args, artifacts, work, output = _finalize_world(runner, tmp_path, monkeypatch, execute="failure")
    _no_rest(monkeypatch, fin)
    data = _bytes(_infra_record(runner))
    (artifacts / t.TERMINAL_FILENAME).write_bytes(data)
    (artifacts / t.CHECKS_FILENAME).write_bytes(b"[]")
    assert fin.cmd_finalize(args) == 0
    assert (artifacts / t.TERMINAL_FILENAME).read_bytes() == data
    assert not (artifacts / t.CHECKS_FILENAME).exists()
    quarantined = [e for e in _events(artifacts) if e.event == "FINALIZER_QUARANTINED"]
    assert [e.path_class for e in quarantined] == ["ANCILLARY_WITHOUT_TRUSTED_QUALITY"]


@pytest.mark.parametrize("last_state,expect_transition", [("PREFLIGHTED", False), ("EXECUTING", True)])
def test_finalize_absent_with_runner_exception_writes_attributed_infrastructure_invalid(
    fin, runner, tmp_path, monkeypatch, last_state, expect_transition,
):
    args, artifacts, work, output = _finalize_world(runner, tmp_path, monkeypatch, execute="failure")
    _no_rest(monkeypatch, fin)
    events = []
    if last_state == "EXECUTING":
        events += [
            ("STATE_TRANSITION", {"state_from": "PREFLIGHTED", "state_to": "REPLACEMENT_MARKED"}),
            ("STATE_TRANSITION", {"state_from": "REPLACEMENT_MARKED", "state_to": "EXECUTING"}),
        ]
    events.append(("RUNNER_EXCEPTION", {"cause": "RUNNER_EXCEPTION", "exception_type": "RuntimeError"}))
    _runner_journal(artifacts, *events)
    assert fin.cmd_finalize(args) == 0
    verdict = t.verify_terminal_bytes((artifacts / t.TERMINAL_FILENAME).read_bytes(), _identity())
    assert verdict.kind == "TRUSTED_INFRASTRUCTURE_INVALID"
    assert verdict.record.termination_source == "RUNNER_EXCEPTION"
    assert verdict.record.terminal_writer is None
    finalizer_events = [e for e in _events(artifacts) if e.writer == "FINALIZER"]
    kinds = [e.event for e in finalizer_events]
    assert kinds[:4] == ["JOURNAL_OPENED", "FINALIZER_CONSUMPTION", "FINALIZER_CANDIDATE", "FINALIZER_DECISION"]
    assert ("STATE_TRANSITION" in kinds) is expect_transition
    assert _outputs(output) == {"terminal_required": "true", "decision": "WRITE_INFRASTRUCTURE_INVALID"}


def test_finalize_absent_with_observed_signal_writes_unclassified(fin, runner, tmp_path, monkeypatch):
    args, artifacts, work, output = _finalize_world(runner, tmp_path, monkeypatch, execute="cancelled")
    _no_rest(monkeypatch, fin)
    _runner_journal(artifacts, ("SIGNAL_OBSERVED", {"signal": "SIGTERM"}))
    assert fin.cmd_finalize(args) == 0
    verdict = t.verify_terminal_bytes((artifacts / t.TERMINAL_FILENAME).read_bytes(), _identity())
    assert verdict.kind == "TRUSTED_UNCLASSIFIED"
    assert verdict.record.unclassified_basis == "OBSERVED_SIGNAL"
    assert verdict.record.observed_signals == ("SIGTERM",)
    assert verdict.record.termination_source is None


def test_finalize_execute_cancelled_without_signal_is_unknown_external_termination(fin, runner, tmp_path, monkeypatch):
    args, artifacts, work, output = _finalize_world(runner, tmp_path, monkeypatch, execute="cancelled")
    _no_rest(monkeypatch, fin)
    assert fin.cmd_finalize(args) == 0
    record = t.verify_terminal_bytes((artifacts / t.TERMINAL_FILENAME).read_bytes(), _identity()).record
    assert record.disposition == "UNCLASSIFIED_TERMINATION"
    assert record.observed_signals == ("UNKNOWN_EXTERNAL_TERMINATION",)


def test_finalize_untrusted_candidate_quarantined_and_replaced(fin, runner, tmp_path, monkeypatch):
    args, artifacts, work, output = _finalize_world(runner, tmp_path, monkeypatch, execute="failure")
    _no_rest(monkeypatch, fin)
    garbage = b"{partial"
    (artifacts / t.TERMINAL_FILENAME).write_bytes(garbage)
    assert fin.cmd_finalize(args) == 0
    record = t.verify_terminal_bytes((artifacts / t.TERMINAL_FILENAME).read_bytes(), _identity()).record
    assert record.unclassified_basis == "RUNNER_TERMINAL_EVIDENCE_UNTRUSTED"
    digest = hashlib.sha256(garbage).hexdigest()
    assert (work / "terminal-quarantine" / f"untrusted-candidate-{digest}.bin").read_bytes() == garbage


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_finalize_symlink_candidate_replaced(fin, runner, tmp_path, monkeypatch):
    args, artifacts, work, output = _finalize_world(runner, tmp_path, monkeypatch, execute="failure")
    _no_rest(monkeypatch, fin)
    target = tmp_path / "elsewhere.json"
    target.write_bytes(_bytes(_quality_record("GREEN")))
    (artifacts / t.TERMINAL_FILENAME).symlink_to(target)
    assert fin.cmd_finalize(args) == 0
    candidate = artifacts / t.TERMINAL_FILENAME
    assert not candidate.is_symlink()
    record = t.verify_terminal_bytes(candidate.read_bytes(), _identity()).record
    assert record.disposition == "UNCLASSIFIED_TERMINATION"


def test_finalize_non_replaceable_candidate_internal_error_exit_4(fin, runner, tmp_path, monkeypatch):
    args, artifacts, work, output = _finalize_world(runner, tmp_path, monkeypatch, execute="failure")
    _no_rest(monkeypatch, fin)
    (artifacts / t.TERMINAL_FILENAME).mkdir()
    assert fin.cmd_finalize(args) == 4
    assert _outputs(output) == {"terminal_required": "true", "decision": "INTERNAL_ERROR"}


def test_finalize_consumption_rest_visible_absent_and_unavailable(fin, runner, tmp_path, monkeypatch):
    marker_detail = ArtifactDetail(
        id=5, name=f"sentinel-p5-oneshot-p5d-official-sonnet-gate-r{RUN_ID}", workflow_run_id=RUN_ID,
        expired=False, digest=None, size_in_bytes=1,
    )
    cases = [
        ("visible", [[marker_detail]], "true"),
        ("absent", [[], [], []], "false"),
        ("unavailable", [GithubEvidenceError("x")], "true"),
    ]
    for label, listings, expected in cases:
        case = tmp_path / label
        case.mkdir()
        args, artifacts, work, output = _finalize_world(runner, case, monkeypatch, marker="failure", execute="skipped")
        clock = _Clock()
        monkeypatch.setattr(fin, "build_evidence_client", lambda env, _l=listings, **kw: _FakeClient(clock, _l))
        assert fin.cmd_finalize(args, sleep=clock.sleep, monotonic=clock.monotonic) == 0, label
        assert _outputs(output)["terminal_required"] == expected, label


def test_rest_marker_visible_rules(fin):
    name = "m"
    detail = ArtifactDetail(id=1, name=name, workflow_run_id=RUN_ID, expired=False, digest=None, size_in_bytes=1)
    clock = _Clock()
    assert fin.rest_marker_visible(_FakeClient(clock, [[detail]]), RUN_ID, name,
                                   sleep=clock.sleep, monotonic=clock.monotonic) is True
    clock = _Clock()
    assert fin.rest_marker_visible(_FakeClient(clock, [[], [], []]), RUN_ID, name,
                                   sleep=clock.sleep, monotonic=clock.monotonic) is False
    clock = _Clock()
    assert fin.rest_marker_visible(_FakeClient(clock, [[], GithubEvidenceError("x")]), RUN_ID, name,
                                   sleep=clock.sleep, monotonic=clock.monotonic) is None
    clock = _Clock()
    assert fin.rest_marker_visible(_FakeClient(clock, [[], []], costs=[20.0, 1.0]), RUN_ID, name,
                                   sleep=clock.sleep, monotonic=clock.monotonic) is None
    expired = ArtifactDetail(id=1, name=name, workflow_run_id=RUN_ID, expired=True, digest=None, size_in_bytes=1)
    clock = _Clock()
    assert fin.rest_marker_visible(_FakeClient(clock, [[expired], [], []]), RUN_ID, name,
                                   sleep=clock.sleep, monotonic=clock.monotonic) is False
    assert fin.FINALIZE_BUDGET_S <= 30.0


def test_finalize_exception_emits_no_terminal_required_exit_5(fin, runner, tmp_path, monkeypatch, capsys):
    args, artifacts, work, output = _finalize_world(runner, tmp_path, monkeypatch)
    monkeypatch.delenv("GITHUB_SHA")
    assert fin.cmd_finalize(args) == 5
    assert _outputs(output) == {}
    assert capsys.readouterr().out == "FINALIZE ERROR: exception_type=GithubContextError\n"


def test_finalizer_never_constructs_gate_evidence_record_directly():
    tree = ast.parse(FINALIZER_PATH.read_text(encoding="utf-8"))
    names = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert "GateEvidenceRecord" not in names
    assert "build_invalid_record" in names
    text = FINALIZER_PATH.read_text(encoding="utf-8")
    assert "SONNET_OFFICIAL_GATE" not in text
    assert "P5D_REPLACEMENT_SONNET_GATE" not in text


def test_finalizer_purpose_and_envelope_come_from_the_runner(fin):
    assert fin.PURPOSE == ORIGINAL_PURPOSE
    assert fin.ENVELOPE is None


def test_finalizer_journal_events_are_finalizer_scoped_and_content_free(fin, runner, tmp_path, monkeypatch):
    args, artifacts, work, output = _finalize_world(runner, tmp_path, monkeypatch)
    _no_rest(monkeypatch, fin)
    (artifacts / t.TERMINAL_FILENAME).write_bytes(_bytes(_quality_record("HONEST_FAIL")))
    assert fin.cmd_finalize(args) == 0
    finalizer_events = [e for e in _events(artifacts) if e.writer == "FINALIZER"]
    assert {e.event for e in finalizer_events} <= {
        "JOURNAL_OPENED", "FINALIZER_CONSUMPTION", "FINALIZER_CANDIDATE", "FINALIZER_QUARANTINED",
        "FINALIZER_DECISION", "STATE_TRANSITION", "SIGNAL_OBSERVED",
    }
    journal_bytes = (artifacts / t.JOURNAL_FILENAME).read_bytes()
    for forbidden in (b"HONEST_FAIL", b"GREEN", b"pooled", b"emitted"):
        assert forbidden not in journal_bytes


# ===========================================================================
# Stage 2C-B1 rehearsal lane (dispatch q77-p5d-repair-stage2cb1-implement-b;
# owner ruling q77-p5d-stage2cb1-finalizer-ruling-a).
#
# The model-free kill rehearsal has NO marker by ADR design. This lane
# creates none, consumes none, looks none up, fabricates no CONSUMED
# value and journals no consumption statement -- it passes its own
# marker-not-applicable gate and enters the SAME shared
# post-eligibility rows the official lane uses.
# ===========================================================================

KILL_WORKFLOW = ".github/workflows/sentinel-kill-rehearsal.yml"
REHEARSAL_ARTIFACT = f"sentinel-p5-rehearsal-r{RUN_ID}-a1"


def _rehearsal_world(runner, tmp_path, monkeypatch, *, execute="failure", **env):
    output, _summary = _set_env(
        monkeypatch, tmp_path, EXECUTE_STEP_OUTCOME=execute,
        GITHUB_WORKFLOW_REF=f"kobescak-kristian/ai-portfolio-sentinel/{KILL_WORKFLOW}@refs/heads/main",
        **env,
    )
    work = tmp_path / "p5-kill-rehearsal"
    work.mkdir()
    artifacts = work / "artifacts"
    runner.establish_preflight_journal(artifacts)
    args = argparse.Namespace(expected_source_sha=SHA, artifacts_dir=artifacts, lane="rehearsal")
    return args, artifacts, work, output


def _written_record(artifacts: Path) -> GateEvidenceRecord:
    return GateEvidenceRecord.model_validate_json(
        (artifacts / t.TERMINAL_FILENAME).read_text(encoding="utf-8")
    )


def _forbid_consumption(monkeypatch, fin):
    def _forbidden(*args, **kwargs):
        raise AssertionError("the rehearsal lane must never ask about marker consumption")

    monkeypatch.setattr(fin, "consumption_from", _forbidden)


def test_rehearsal_lane_makes_no_marker_rest_call_and_journals_no_consumption(
    fin, runner, tmp_path, monkeypatch
):
    args, artifacts, _work, output = _rehearsal_world(runner, tmp_path, monkeypatch)
    _no_rest(monkeypatch, fin)
    _forbid_consumption(monkeypatch, fin)
    assert fin.cmd_finalize(args) == 0
    events = _events(artifacts)
    assert "FINALIZER_CONSUMPTION" not in [e.event for e in events]
    assert "FINALIZER_CANDIDATE" in [e.event for e in events]
    assert "FINALIZER_DECISION" in [e.event for e in events]
    assert all(e.consumption is None for e in events)
    assert _outputs(output)["terminal_required"] == "true"


def test_rehearsal_lane_absent_candidate_reaches_shared_write_unclassified(
    fin, runner, tmp_path, monkeypatch, capsys
):
    args, artifacts, _work, output = _rehearsal_world(runner, tmp_path, monkeypatch, execute="cancelled")
    _no_rest(monkeypatch, fin)
    _forbid_consumption(monkeypatch, fin)
    assert fin.cmd_finalize(args) == 0
    assert _outputs(output) == {"terminal_required": "true", "decision": "WRITE_UNCLASSIFIED"}
    assert "consumption=NOT_APPLICABLE" in capsys.readouterr().out
    record = _written_record(artifacts)
    assert record.disposition == "UNCLASSIFIED_TERMINATION"
    assert record.unclassified_basis == "OBSERVED_SIGNAL"
    assert record.observed_signals == ("UNKNOWN_EXTERNAL_TERMINATION",)


def test_rehearsal_record_carries_truthful_non_quality_identity(fin, runner, tmp_path, monkeypatch):
    args, artifacts, _work, _output = _rehearsal_world(runner, tmp_path, monkeypatch)
    _no_rest(monkeypatch, fin)
    assert fin.cmd_finalize(args) == 0
    record = _written_record(artifacts)
    # No model was invoked on this lane, and no quality profile ran.
    assert record.model == "NO_MODEL_INVOKED" == fin.REHEARSAL_MODEL
    assert record.profile_name == "p5d-kill-rehearsal" == fin.REHEARSAL_PROFILE_NAME
    assert fin.REHEARSAL_PURPOSE == "P5D_KILL_REHEARSAL"
    # No authentication occurred: the lane has no token-issuing permission.
    assert record.auth_mode is None
    assert record.model != "claude-sonnet-5"
    # Structurally non-quality.
    assert record.run_ids == () and record.scoring == {} and record.cost_rows == ()
    assert record.accounted_total_eur_micros == 0
    assert record.terminal_writer is None


def test_rehearsal_record_has_no_replacement_provenance_and_is_rejected_as_replacement(
    fin, runner, tmp_path, monkeypatch
):
    args, artifacts, _work, _output = _rehearsal_world(runner, tmp_path, monkeypatch)
    _no_rest(monkeypatch, fin)
    assert fin.cmd_finalize(args) == 0
    record = _written_record(artifacts)
    for field in ("replacement_of_run_id", "owner_ruling_id", "marker_purpose",
                  "envelope_id", "envelope_version", "terminal_writer"):
        assert getattr(record, field) is None, field
    from sentinel.phase5.evidence_records import validate_replacement_provenance
    with pytest.raises(ValueError):
        validate_replacement_provenance(record, expected_source_sha=SHA)
    # Under the replacement purpose the bytes classify as PROVENANCE_INVALID.
    replacement_identity = _identity(
        workflow_identity=KILL_WORKFLOW, purpose="P5D_REPLACEMENT_SONNET_GATE"
    )
    assert t.verify_terminal_bytes(_bytes(record), replacement_identity).kind == "PROVENANCE_INVALID"


def test_rehearsal_lane_cannot_author_a_quality_result(fin, runner, tmp_path, monkeypatch):
    """The shared core writes only terminal.build_invalid_record output,
    so no rehearsal run can ever produce GREEN or HONEST_FAIL."""
    args, artifacts, _work, _output = _rehearsal_world(runner, tmp_path, monkeypatch)
    _no_rest(monkeypatch, fin)
    assert fin.cmd_finalize(args) == 0
    assert _written_record(artifacts).disposition == "UNCLASSIFIED_TERMINATION"
    tree = ast.parse(FINALIZER_PATH.read_text(encoding="utf-8"))
    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "GREEN" not in literals and "HONEST_FAIL" not in literals


def test_rehearsal_lane_preserves_trusted_runner_evidence(fin, runner, tmp_path, monkeypatch):
    """Trusted-candidate precedence is shared, unchanged behaviour."""
    args, artifacts, _work, output = _rehearsal_world(runner, tmp_path, monkeypatch)
    _no_rest(monkeypatch, fin)
    identity = _identity(workflow_identity=KILL_WORKFLOW, purpose=fin.REHEARSAL_PURPOSE)
    raw = t.build_invalid_record(
        identity=identity, envelope=None, created_at_utc=datetime(2026, 9, 19, tzinfo=timezone.utc),
        model=fin.REHEARSAL_MODEL, profile_name=fin.REHEARSAL_PROFILE_NAME,
        infrastructure_cause="RUNNER_EXCEPTION", writer="RUNNER",
    )
    data = _bytes(runner.attribute_invalid_record(raw, purpose=fin.REHEARSAL_PURPOSE))
    (artifacts / t.TERMINAL_FILENAME).write_bytes(data)
    assert fin.cmd_finalize(args) == 0
    assert _outputs(output)["decision"] == "PRESERVE_RUNNER_EVIDENCE"
    assert (artifacts / t.TERMINAL_FILENAME).read_bytes() == data


def test_rehearsal_confirm_targets_the_rehearsal_artifact_namespace(fin, tmp_path, monkeypatch, capsys):
    _set_env(monkeypatch, tmp_path, UPLOAD_STEP_OUTCOME="success",
             GITHUB_WORKFLOW_REF=f"kobescak-kristian/ai-portfolio-sentinel/{KILL_WORKFLOW}@refs/heads/main")
    work, root = _roots(tmp_path)
    asked: list = []
    clock = _Clock()

    class _NameSpy:
        def list_run_artifacts_named(self, run_id, name):
            asked.append(name)
            raise GithubEvidenceError("stop here; the requested name is what this test pins")

    monkeypatch.setattr(fin, "build_evidence_client", lambda env, **kw: _NameSpy())
    args = argparse.Namespace(
        expected_source_sha=SHA, artifacts_dir=root, work_root=work, lane="rehearsal"
    )
    fin.cmd_confirm(args, sleep=clock.sleep, monotonic=clock.monotonic)
    assert asked and set(asked) == {REHEARSAL_ARTIFACT}
    assert not any(name.startswith("sentinel-p5-gate-evidence-") for name in asked)
    assert REHEARSAL_ARTIFACT in capsys.readouterr().out


def test_official_lane_is_the_default_and_is_unchanged(fin, runner, tmp_path, monkeypatch, capsys):
    """No --lane means official: marker reasoning, the consumption event
    and the official Sonnet identity all still apply."""
    args, artifacts, _work, output = _finalize_world(runner, tmp_path, monkeypatch)
    assert not hasattr(args, "lane")
    _no_rest(monkeypatch, fin)
    assert fin.cmd_finalize(args) == 0
    events = _events(artifacts)
    consumption = [e for e in events if e.event == "FINALIZER_CONSUMPTION"]
    assert len(consumption) == 1 and consumption[0].consumption == "CONSUMED"
    assert "consumption=CONSUMED" in capsys.readouterr().out
    record = _written_record(artifacts)
    assert (record.model, record.profile_name) == runner.gate_profile_identity()
    assert record.model != fin.REHEARSAL_MODEL
    assert _outputs(output)["terminal_required"] == "true"


def test_lane_vocabulary_is_closed_and_unknown_lanes_are_rejected(fin):
    assert fin.LANES == ("official", "rehearsal")
    assert fin.OFFICIAL_LANE == "official" and fin.REHEARSAL_LANE == "rehearsal"
    with pytest.raises(SystemExit):
        fin.main(["finalize", "--expected-source-sha", SHA, "--artifacts-dir", "a", "--lane", "nope"])

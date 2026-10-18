"""Tests for scripts/establish_phase5_replacement_receipts.py (P5-E
prerequisite, owner approval 2026-10-17). Network-free: a fake GitHub
client serves synthetic run/artifact metadata and payloads; the registry
under test is a temporary copy of the committed historical registry."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contracts.schemas import CostRow
from sentinel.phase5 import artifact_names
from sentinel.phase5 import receipts as rc
from sentinel.phase5.evidence_records import GateEvidenceRecord
from sentinel.phase5.github_evidence import ArtifactRef
from sentinel.phase5.journal import JournalEvent, event_line_bytes
from sentinel.phase5.models import OneShotMarker
from sentinel.phase5.replacement import (
    OWNER_RULING_ID,
    REPLACEMENT_OF_RUN_ID,
    REPLACEMENT_PURPOSE,
    REPLACEMENT_RUN_ID,
    REPLACEMENT_SOURCE_SHA,
)
from telemetry.cost_ledger import append_cost_row, read_cost_rows

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "establish_phase5_replacement_receipts.py"
COMMITTED_REGISTRY = REPO_ROOT / "artifacts" / "phase5_receipt_registry.jsonl"
Q_SHA = "a" * 40  # a later qualification source; never equal to A


def _historical_registry_bytes() -> bytes:
    lines = COMMITTED_REGISTRY.read_bytes().splitlines()
    return b"".join(line + b"\n" for line in lines[:4])


def _load_script():
    spec = importlib.util.spec_from_file_location("establish_phase5_replacement_receipts", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ers = _load_script()


# ---------------------------------------------------------------------------
# Synthetic sources shaped like the recorded replacement history
# ---------------------------------------------------------------------------


def _cost_rows(costs=(597_367, 476_208)):
    return tuple(
        CostRow(
            schema_version=1, run_id=run_id, recorded_at_utc=datetime(2026, 10, 5, 23, 28, i, tzinfo=timezone.utc),
            run_kind="dev", model="claude-sonnet-5", input_tokens=1, output_tokens=1, cost_eur_micros=cost,
        )
        for i, (run_id, cost) in enumerate(zip(ers.EXPECTED_COST_RUN_IDS, costs))
    )


def _gate_record(**overrides):
    rows = overrides.pop("cost_rows", _cost_rows())
    fields = dict(
        schema_version=1, workflow_identity=".github/workflows/sentinel-official-gate.yml",
        github_run_id=REPLACEMENT_RUN_ID, run_attempt=1, event="workflow_dispatch",
        ref="refs/heads/main", source_sha=REPLACEMENT_SOURCE_SHA,
        created_at_utc=datetime(2026, 10, 5, 23, 23, 0, tzinfo=timezone.utc),
        steps=(), expected_source_sha=REPLACEMENT_SOURCE_SHA, model="claude-sonnet-5",
        profile_name="sonnet-official-gate", run_ids=ers.EXPECTED_COST_RUN_IDS, scoring={"emitted": 0},
        thresholds={}, invariant_results={"ok": True}, execution_validity={"valid": True},
        miss_patterns=(), failed_checks=(), cost_rows=rows,
        accounted_total_eur_micros=sum(r.cost_eur_micros for r in rows),
        disposition="GREEN", auth_mode="github-actions-wif-federation",
        replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID,
        marker_purpose=REPLACEMENT_PURPOSE, envelope_id="env-1", envelope_version="v1",
        terminal_writer="RUNNER",
    )
    fields.update(overrides)
    return GateEvidenceRecord(**fields)


def _marker(**overrides):
    fields = dict(
        schema_version=1, purpose=REPLACEMENT_PURPOSE,
        created_at_utc=datetime(2026, 10, 5, 23, 23, 30, tzinfo=timezone.utc),
        workflow_identity=".github/workflows/sentinel-official-gate.yml", github_run_id=REPLACEMENT_RUN_ID,
        run_attempt=1, event="workflow_dispatch", source_sha=REPLACEMENT_SOURCE_SHA,
        replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID,
    )
    fields.update(overrides)
    return OneShotMarker(**fields)


def _checks_bytes(lines=None):
    import json

    lines = lines if lines is not None else [f"check_{i}: PASS" for i in range(ers.EXPECTED_CHECK_LINES)]
    return json.dumps(lines, indent=2).encode("utf-8")


def _journal_bytes(*extra_events):
    when = datetime(2026, 10, 5, 23, 23, 40, tzinfo=timezone.utc)
    events = [dict(event="JOURNAL_OPENED"), *extra_events]
    return b"".join(
        event_line_bytes(JournalEvent(schema_version=1, seq=i, recorded_at_utc=when, elapsed_ms=0, writer="RUNNER", **e))
        for i, e in enumerate(events, start=1)
    )


def _gate_files(record=None, **overrides):
    """The real three-file gate-evidence artifact shape."""
    files = {
        "phase5_official_gate.json": (record or _gate_record()).model_dump_json().encode("utf-8"),
        "phase5_official_gate_checks.json": _checks_bytes(),
        "phase5_gate_journal.jsonl": _journal_bytes(),
    }
    files.update(overrides)
    return {name: data for name, data in files.items() if data is not None}


class _FakeClient:
    def __init__(self, *, marker=None, record=None, gate_files=None, run_overrides=None, digest_overrides=None):
        self.payloads = {
            ers.REPLACEMENT_MARKER.artifact_id: {
                "marker.json": (marker or _marker()).model_dump_json().encode("utf-8"),
            },
            ers.REPLACEMENT_GATE_EVIDENCE.artifact_id: gate_files if gate_files is not None else _gate_files(record),
        }
        self.specs = {s.artifact_id: s for s in (ers.REPLACEMENT_MARKER, ers.REPLACEMENT_GATE_EVIDENCE)}
        self.run = {
            "path": ".github/workflows/sentinel-official-gate.yml", "event": "workflow_dispatch", "run_attempt": 1,
            "head_sha": REPLACEMENT_SOURCE_SHA, "status": "completed", "conclusion": "success",
            **(run_overrides or {}),
        }
        self.digests = {**ers.RECORDED_DIGESTS, **(digest_overrides or {})}

    def _get_json(self, path):
        if path == f"/repos/{ers.REPOSITORY}/actions/runs/{REPLACEMENT_RUN_ID}":
            return dict(self.run)
        artifact_id = int(path.rsplit("/", 1)[1])
        return {
            "expired": False, "name": self.specs[artifact_id].artifact_name,
            "workflow_run": {"id": int(REPLACEMENT_RUN_ID), "head_sha": REPLACEMENT_SOURCE_SHA},
            "expires_at": "2027-01-03T23:23:02Z", "digest": self.digests[artifact_id],
        }

    def list_artifacts_for_run(self, run_id):
        return [ArtifactRef(id=s.artifact_id, name=s.artifact_name, workflow_run_id=run_id) for s in self.specs.values()]

    def download_artifact(self, ref, dest_trusted_root, dest_dir):
        from sentinel.phase5.bundle import create_fresh_root

        root = create_fresh_root(dest_trusted_root, dest_dir)
        root.mkdir(exist_ok=True)
        for name, data in self.payloads[ref.id].items():
            if name.endswith("/"):
                (root / name).mkdir()
            else:
                (root / name).write_bytes(data)
        return root


@pytest.fixture
def world(tmp_path, monkeypatch):
    registry = tmp_path / "registry.jsonl"
    # the pre-establishment state: the four historical receipts, i.e. the
    # first four lines of the committed registry
    registry.write_bytes(_historical_registry_bytes())
    ledger = tmp_path / "cost_ledger.jsonl"
    ledger.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-not-real")
    client = _FakeClient()
    monkeypatch.setattr(ers, "GithubEvidenceClient", lambda **_kwargs: client)
    return registry, ledger, client


def _run(registry, ledger, *extra):
    return ers.main(["--registry", str(registry), "--cost-ledger", str(ledger), *extra])


def _dry_run_hash(capsys, registry, ledger):
    assert _run(registry, ledger, "--dry-run") == 0
    out = capsys.readouterr().out
    return next(line.split()[-1] for line in out.splitlines() if line.startswith("FACTS_SHA256:"))


# ---------------------------------------------------------------------------
# Constants and import surface
# ---------------------------------------------------------------------------


def test_recorded_constants_match_canonical_names_and_the_replacement_identity():
    assert ers.REPLACEMENT_RUN.run_id == REPLACEMENT_RUN_ID == "37388164100"
    assert ers.REPLACEMENT_RUN.source_sha == REPLACEMENT_SOURCE_SHA == "612832e4d7bf0706cf994a83462e50cce9d9e1be"
    assert ers.REPLACEMENT_MARKER.artifact_name == artifact_names.oneshot_marker_name(REPLACEMENT_PURPOSE, REPLACEMENT_RUN_ID)
    assert ers.REPLACEMENT_GATE_EVIDENCE.artifact_name == artifact_names.gate_evidence_name(REPLACEMENT_RUN_ID, 1)
    assert sum((597_367, 476_208)) == ers.EXPECTED_COST_TOTAL_EUR_MICROS


def test_script_reaches_no_provider_oidc_or_common_seam():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    for module in modules:
        assert module.split(".")[0] not in {"agents", "claude_agent_sdk", "anthropic"}, module
    assert "scripts._phase5_common" not in modules
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "acquire_oidc" not in text and "print(token" not in text and 'f"{token' not in text
    # read-only use of the terminal/journal libraries: no writer is imported
    for writer in ("write_terminal_atomically", "write_ancillary_atomically", "OperationalJournal",
                   "move_to_quarantine", "copy_to_quarantine", "build_invalid_record"):
        assert writer not in text, writer


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _facts():
    marker_fact = ers._fact(
        receipt_class="ONESHOT_MARKER_CONSUMED", purpose=REPLACEMENT_PURPOSE, github_run_id=REPLACEMENT_RUN_ID,
        run_attempt=1, source_sha=REPLACEMENT_SOURCE_SHA, artifact_name="m", artifact_id=1,
        payload_filename="marker.json", payload_sha256="1" * 64, disposition="CONSUMED", owner_ruling_id=OWNER_RULING_ID,
    )
    return [marker_fact, {**marker_fact, "receipt_class": "GATE_EVIDENCE"}]


FILE_SHAS = {name: "f" * 64 for name in ers.GATE_ARTIFACT_FILES}


def test_fact_bundle_is_deterministic_and_binds_cost_rows_and_gate_files():
    rows = _cost_rows()
    base = ers.build_fact_bundle(_facts(), rows, FILE_SHAS)
    assert base == ers.build_fact_bundle(_facts(), rows, dict(FILE_SHAS))
    assert base != ers.build_fact_bundle(_facts(), _cost_rows((597_368, 476_207)), FILE_SHAS)
    assert base != ers.build_fact_bundle(_facts(), rows, {**FILE_SHAS, "phase5_gate_journal.jsonl": "e" * 64})


def test_fact_bundle_refuses_wrong_count_keys_or_file_set():
    with pytest.raises(ers.EstablishmentRefused):
        ers.build_fact_bundle(_facts()[:1], _cost_rows(), FILE_SHAS)
    bad = _facts()
    bad[0] = {**bad[0], "recorded_at_utc": "x"}
    with pytest.raises(ers.EstablishmentRefused):
        ers.build_fact_bundle(bad, _cost_rows(), FILE_SHAS)
    with pytest.raises(ers.EstablishmentRefused):
        ers.build_fact_bundle(_facts(), _cost_rows(), {"phase5_official_gate.json": "f" * 64})


def test_registry_precondition_accepts_exactly_the_committed_historical_four(tmp_path):
    ers.check_registry_precondition(rc.load_registry(COMMITTED_REGISTRY)[:4])
    with pytest.raises(ers.EstablishmentRefused):
        ers.check_registry_precondition(rc.load_registry(COMMITTED_REGISTRY)[:3])


def test_cost_rows_to_append_states():
    rows = _cost_rows()
    assert ers.cost_rows_to_append(rows, []) == rows
    assert ers.cost_rows_to_append(rows, list(rows)) == ()
    with pytest.raises(ers.EstablishmentRefused, match="only some"):
        ers.cost_rows_to_append(rows, [rows[0]])
    with pytest.raises(ers.EstablishmentRefused, match="more than once"):
        ers.cost_rows_to_append(rows, [rows[0], rows[0], rows[1]])


def test_gate_record_check_binds_source_a_not_q():
    ers.check_gate_record(_gate_record())
    at_q = _gate_record(source_sha=Q_SHA, expected_source_sha=Q_SHA)
    with pytest.raises(ValueError, match="source_sha does not match"):
        ers.check_gate_record(at_q)


@pytest.mark.parametrize(
    "overrides",
    [
        {"disposition": "HONEST_FAIL", "failed_checks": ("invariant[x]: FAIL",)},
        {"cost_rows": _cost_rows((597_367, 476_209))},
        {"cost_rows": _cost_rows()[:1]},
    ],
)
def test_gate_record_check_refuses_divergence_from_recorded_history(overrides):
    record = _gate_record(**overrides)  # a valid record; only the correlation may refuse
    with pytest.raises(ers.EstablishmentRefused):
        ers.check_gate_record(record)


# ---------------------------------------------------------------------------
# main(): dry run, real run, refusals
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(world, capsys):
    registry, ledger, _client = world
    before = (registry.read_bytes(), ledger.read_bytes())
    digest = _dry_run_hash(capsys, registry, ledger)
    assert len(digest) == 64
    assert (registry.read_bytes(), ledger.read_bytes()) == before


def test_real_run_requires_a_reviewed_hash(world):
    registry, ledger, _client = world
    assert _run(registry, ledger) == 2
    assert _run(registry, ledger, "--dry-run", "--expect-facts-sha256", "0" * 64) == 2


def test_real_run_with_wrong_hash_writes_nothing(world, capsys):
    registry, ledger, _client = world
    before = (registry.read_bytes(), ledger.read_bytes())
    assert _run(registry, ledger, "--expect-facts-sha256", "0" * 64) == 1
    assert (registry.read_bytes(), ledger.read_bytes()) == before


def test_real_run_establishes_receipts_that_satisfy_the_freeze_seam_at_q(world, capsys, tmp_path, monkeypatch):
    registry, ledger, _client = world
    digest = _dry_run_hash(capsys, registry, ledger)
    before = registry.read_bytes()
    assert _run(registry, ledger, "--expect-facts-sha256", digest) == 0
    after = registry.read_bytes()
    assert after.startswith(before) and after.count(b"\n") == 6  # pure append
    receipts = rc.load_registry(registry)
    assert [(r.receipt_class, r.purpose, r.source_sha) for r in receipts[4:]] == [
        ("ONESHOT_MARKER_CONSUMED", REPLACEMENT_PURPOSE, REPLACEMENT_SOURCE_SHA),
        ("GATE_EVIDENCE", REPLACEMENT_PURPOSE, REPLACEMENT_SOURCE_SHA),
    ]
    assert receipts[5].disposition == "GREEN"
    assert [r.run_id for r in read_cost_rows(ledger)] == list(ers.EXPECTED_COST_RUN_IDS)

    # End to end: the established receipts pass seam 3 of the window
    # freeze, which never sees the qualification source Q.
    spec = importlib.util.spec_from_file_location("wf_for_receipts", REPO_ROOT / "scripts" / "run_phase5_window_freeze.py")
    wf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wf)
    (tmp_path / "repo" / "telemetry").mkdir(parents=True)
    shutil.copy(ledger, tmp_path / "repo" / "telemetry" / "cost_ledger.jsonl")
    monkeypatch.setattr(wf, "REPO_ROOT", tmp_path / "repo")
    (tmp_path / "seam-work").mkdir()
    # The fake client still serves the retained gate evidence, so the seam
    # hash-verifies it against its receipt, validates it at source A and
    # recovers its two CostRows from the ledger, each present exactly once.
    recovered = wf._verify_provider_phase_prerequisites(_FakeClient(), tmp_path / "seam-work", receipts)
    assert tuple(r.run_id for r in recovered) == ers.EXPECTED_COST_RUN_IDS

    # one-time establishment: a second run refuses and writes nothing
    assert _run(registry, ledger, "--expect-facts-sha256", digest) == 1
    assert registry.read_bytes() == after


def test_already_recorded_cost_rows_are_not_duplicated(world, capsys):
    registry, ledger, _client = world
    for row in _cost_rows():
        append_cost_row(ledger, row)
    digest = _dry_run_hash(capsys, registry, ledger)
    assert _run(registry, ledger, "--expect-facts-sha256", digest) == 0
    assert len(read_cost_rows(ledger)) == 2


@pytest.mark.parametrize(
    "client_kwargs",
    [
        {"run_overrides": {"head_sha": Q_SHA}},
        {"run_overrides": {"conclusion": "failure"}},
        {"digest_overrides": {11380108778: "sha256:" + "0" * 64}},
        {"marker": _marker(source_sha=Q_SHA)},
        {"record": _gate_record(source_sha=Q_SHA, expected_source_sha=Q_SHA)},
    ],
)
def test_dry_run_refuses_any_source_divergence(world, monkeypatch, client_kwargs):
    registry, ledger, _client = world
    monkeypatch.setattr(ers, "GithubEvidenceClient", lambda **_kwargs: _FakeClient(**client_kwargs))
    assert _run(registry, ledger, "--dry-run") == 1


def test_payload_hash_in_the_fact_is_the_downloaded_bytes(world, capsys):
    registry, ledger, client = world
    _dry_run_hash(capsys, registry, ledger)  # consume output
    assert _run(registry, ledger, "--dry-run") == 0
    out = capsys.readouterr().out
    terminal = client.payloads[ers.REPLACEMENT_GATE_EVIDENCE.artifact_id]["phase5_official_gate.json"]
    expected = hashlib.sha256(terminal).hexdigest()
    assert f"FACT[2].payload_sha256: {expected}" in out


def test_dry_run_refuses_once_replacement_receipts_exist(world, capsys):
    registry, ledger, _client = world
    digest = _dry_run_hash(capsys, registry, ledger)
    assert _run(registry, ledger, "--expect-facts-sha256", digest) == 0
    assert _run(registry, ledger, "--dry-run") == 1
    assert "STOP: EstablishmentRefused: registry holds 6 receipts" in capsys.readouterr().err



# ---------------------------------------------------------------------------
# Gate-evidence artifact shape: exactly the three uploaded files
# ---------------------------------------------------------------------------


def test_gate_artifact_files_are_exactly_what_the_official_gate_workflow_uploads():
    import yaml

    data = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "sentinel-official-gate.yml").read_text(encoding="utf-8"))
    steps = next(iter(data["jobs"].values()))["steps"]
    upload = next(s for s in steps if s.get("name") == "upload gate evidence")
    uploaded = sorted(Path(line.strip()).name for line in upload["with"]["path"].splitlines() if line.strip())
    assert uploaded == sorted(ers.GATE_ARTIFACT_FILES)


def test_real_three_file_artifact_shape_passes_and_binds_every_file(world, capsys):
    registry, ledger, client = world
    assert _run(registry, ledger, "--dry-run") == 0
    out = capsys.readouterr().out
    for name, data in client.payloads[ers.REPLACEMENT_GATE_EVIDENCE.artifact_id].items():
        assert f"GATE_FILE_SHA256: {name} {hashlib.sha256(data).hexdigest()}" in out


def test_facts_hash_changes_when_an_ancillary_file_changes(world, capsys, monkeypatch):
    registry, ledger, _client = world
    first = _dry_run_hash(capsys, registry, ledger)
    other = _FakeClient(gate_files=_gate_files(**{"phase5_gate_journal.jsonl": _journal_bytes(
        dict(event="STATE_TRANSITION", state_to="PREFLIGHTED"))}))
    monkeypatch.setattr(ers, "GithubEvidenceClient", lambda **_kwargs: other)
    assert _dry_run_hash(capsys, registry, ledger) != first


@pytest.mark.parametrize(
    "gate_files",
    [
        pytest.param(_gate_files(**{"phase5_official_gate_checks.json": None, "phase5_gate_journal.jsonl": None}),
                     id="old-one-file-shape"),
        pytest.param(_gate_files(**{"phase5_official_gate_checks.json": None}), id="missing-checks"),
        pytest.param(_gate_files(**{"phase5_gate_journal.jsonl": None}), id="missing-journal"),
        pytest.param(_gate_files(**{"phase5_official_gate.json": None}), id="missing-terminal"),
        pytest.param(_gate_files(**{"extra.txt": b"x"}), id="extra-file"),
        pytest.param(_gate_files(**{"nested/": b""}), id="extra-directory"),
    ],
)
def test_gate_artifact_with_missing_or_extra_entries_refuses(world, monkeypatch, capsys, gate_files):
    registry, ledger, _client = world
    monkeypatch.setattr(ers, "GithubEvidenceClient", lambda **_kwargs: _FakeClient(gate_files=gate_files))
    assert _run(registry, ledger, "--dry-run") == 1
    assert "extracted entry set" in capsys.readouterr().err


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"phase5_official_gate_checks.json": _checks_bytes([f"c{i}: PASS" for i in range(26)])}, "26 check lines"),
        ({"phase5_official_gate_checks.json": _checks_bytes(
            [f"c{i}: PASS" for i in range(26)] + ["invariant[x]: FAIL"])}, "reports a failed check"),
        ({"phase5_official_gate_checks.json": b'{"not": "a list"}'}, "not a list"),
        ({"phase5_official_gate_checks.json": b"\xff"}, "not strict UTF-8 JSON"),
        ({"phase5_gate_journal.jsonl": b"garbage\n"}, "integrity is CORRUPT"),
        ({"phase5_gate_journal.jsonl": b""}, "OK with events required"),
        ({"phase5_gate_journal.jsonl": _journal_bytes(dict(event="SIGNAL_OBSERVED", signal="SIGTERM"))},
         "records a signal"),
    ],
)
def test_ancillary_files_are_validated_not_ignored(world, monkeypatch, capsys, overrides, message):
    registry, ledger, _client = world
    monkeypatch.setattr(ers, "GithubEvidenceClient", lambda **_kwargs: _FakeClient(gate_files=_gate_files(**overrides)))
    assert _run(registry, ledger, "--dry-run") == 1
    assert message in capsys.readouterr().err


def test_terminal_record_must_be_trusted_quality_under_the_replacement_identity(world, monkeypatch, capsys):
    registry, ledger, _client = world
    wrong = _gate_record(ref="refs/heads/other")
    monkeypatch.setattr(ers, "GithubEvidenceClient", lambda **_kwargs: _FakeClient(record=wrong))
    assert _run(registry, ledger, "--dry-run") == 1
    assert "IDENTITY_INVALID, not TRUSTED_QUALITY" in capsys.readouterr().err


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(_gate_record(disposition="HONEST_FAIL", failed_checks=("invariant[x]: FAIL",)), id="honest-fail-not-the-recorded-green"),
        pytest.param(_gate_record(cost_rows=_cost_rows((597_367, 476_209))), id="cost-total-diverges"),
    ],
)
def test_dry_run_refuses_a_trusted_record_that_diverges_from_recorded_history(world, monkeypatch, capsys, record):
    """verify_terminal_bytes trusts both shapes; the recorded-history
    correlation (check_gate_record) is what refuses them."""
    registry, ledger, _client = world
    monkeypatch.setattr(ers, "GithubEvidenceClient", lambda **_kwargs: _FakeClient(record=record))
    assert _run(registry, ledger, "--dry-run") == 1
    assert "STOP: EstablishmentRefused" in capsys.readouterr().err



def test_committed_registry_is_the_historical_four_plus_the_established_replacement_pair():
    """After the 2026-10-18 establishment the committed registry is exactly
    the historical prefix this tool ran against, plus one replacement
    marker and one GREEN gate-evidence receipt at source A."""
    receipts = rc.load_registry(COMMITTED_REGISTRY)
    assert len(receipts) == ers.HISTORICAL_RECEIPT_COUNT + 2
    ers.check_registry_precondition(receipts[: ers.HISTORICAL_RECEIPT_COUNT])
    assert [(r.receipt_class, r.purpose, r.github_run_id, r.source_sha, r.artifact_id, r.disposition)
            for r in receipts[4:]] == [
        ("ONESHOT_MARKER_CONSUMED", REPLACEMENT_PURPOSE, REPLACEMENT_RUN_ID, REPLACEMENT_SOURCE_SHA,
         ers.REPLACEMENT_MARKER.artifact_id, "CONSUMED"),
        ("GATE_EVIDENCE", REPLACEMENT_PURPOSE, REPLACEMENT_RUN_ID, REPLACEMENT_SOURCE_SHA,
         ers.REPLACEMENT_GATE_EVIDENCE.artifact_id, "GREEN"),
    ]
    with pytest.raises(ers.EstablishmentRefused):
        ers.check_registry_precondition(receipts)


def test_committed_ledger_holds_each_replacement_cost_row_exactly_once():
    rows = read_cost_rows(REPO_ROOT / "telemetry" / "cost_ledger.jsonl")
    matches = [r for r in rows if r.run_id in ers.EXPECTED_COST_RUN_IDS]
    assert sorted(r.run_id for r in matches) == sorted(ers.EXPECTED_COST_RUN_IDS)
    assert sum(r.cost_eur_micros for r in matches) == ers.EXPECTED_COST_TOTAL_EUR_MICROS

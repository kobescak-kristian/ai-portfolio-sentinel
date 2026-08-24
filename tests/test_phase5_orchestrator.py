"""Tests for sentinel/phase5/orchestrator.py (P5-B Part 3/3).

Fake ports throughout; network-blocked by conftest.py's autouse
``block_network``. Reuses the shared Phase-5 bundle builders from
``tests.test_phase5_bundle``.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sentinel.phase5 import artifact_names as an
from sentinel.phase5 import bundle as b
from sentinel.phase5 import models as m
from sentinel.phase5.bundle import create_fresh_root
from sentinel.phase5.github_evidence import ArtifactRef
from sentinel.phase5.orchestrator import (
    ScheduledPorts,
    run_scheduled,
)
from tests.test_phase5_bundle import (
    CONTROL_WORKFLOW,
    SCHEDULED_WORKFLOW,
    SOURCE_SHA,
    WINDOW_CREATED_AT,
    build_valid_bundle,
    make_control_state,
    make_genesis,
    make_window,
    slot_ts,
)

RUN_ID = "555"

ENV = {
    "GITHUB_REPOSITORY": "kobescak-kristian/ai-portfolio-sentinel",
    "GITHUB_REPOSITORY_OWNER": "kobescak-kristian",
    "GITHUB_RUN_ID": RUN_ID,
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_EVENT_NAME": "schedule",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": SOURCE_SHA,
    "GITHUB_WORKFLOW_REF": (
        "kobescak-kristian/ai-portfolio-sentinel/.github/workflows/sentinel-schedule.yml@refs/heads/main"
    ),
    "GITHUB_API_URL": "https://api.github.com",
    "GITHUB_SERVER_URL": "https://github.com",
}


@dataclass
class CallRecorder:
    calls: list[str] = field(default_factory=list)

    def record(self, name: str) -> None:
        self.calls.append(name)


class FakeEvidenceClient:
    """Duck-types GithubEvidenceClient's discovery/download surface.
    ``bundles`` maps artifact name -> a real, already-built bundle
    directory on disk; ``download_artifact`` copies it into the
    destination so validate_bundle reads real files."""

    def __init__(self, *, bundles: dict[str, Path], run_timing: tuple[datetime, datetime | None]):
        self._bundles = bundles
        self._run_timing = run_timing

    def get_run_timing(self, run_id: str):
        return self._run_timing

    def list_artifacts(self, prefix: str) -> list[ArtifactRef]:
        return [
            ArtifactRef(id=i, name=name, workflow_run_id=RUN_ID)
            for i, name in enumerate(sorted(self._bundles))
            if name.startswith(prefix)
        ]

    def download_artifact(self, ref: ArtifactRef, dest_trusted_root: Path, dest_dir: Path) -> Path:
        root = create_fresh_root(dest_trusted_root, dest_dir)
        shutil.copytree(self._bundles[ref.name], root, dirs_exist_ok=True)
        return root


def _fake_ports(*, recorder: CallRecorder, evidence_client, run_sentinel_outcome=None, env=None):
    def wif_ready():
        recorder.record("wif_ready")

    def acquire_oidc():
        recorder.record("acquire_oidc")
        return object()

    def install_token(session):
        recorder.record("install_token")

    def shutdown_oidc(session):
        recorder.record("shutdown_oidc")

    def run_sentinel(working_state, run_id):
        recorder.record("run_sentinel")
        return run_sentinel_outcome

    return ScheduledPorts(
        env=env or ENV,
        clock=lambda: WINDOW_CREATED_AT + timedelta(days=2),
        evidence_client=evidence_client,
        wif_ready=wif_ready,
        acquire_oidc=acquire_oidc,
        install_token=install_token,
        shutdown_oidc=shutdown_oidc,
        run_sentinel=run_sentinel,
    )


def make_scheduled_window(**overrides):
    overrides.setdefault("scheduled_workflow_identity", ".github/workflows/sentinel-schedule.yml")
    return make_window(**overrides)


def _build_genesis_bundle(tmp_path: Path, window: m.QualificationWindowRecord) -> Path:
    genesis = make_genesis(window)
    control_state = make_control_state(window, slot_index=0, evaluated_at=WINDOW_CREATED_AT)
    genesis_src = tmp_path / "genesis-src"
    genesis_src.mkdir(parents=True)
    built = build_valid_bundle(
        genesis_src,
        window=window,
        manifest_fields=genesis.model_dump(exclude={"carried_files"}),
        control_state=control_state,
    )
    return built.root


# ---------------------------------------------------------------------------
# Pre-window safety
# ---------------------------------------------------------------------------


def test_pre_window_makes_zero_oidc_wif_provider_calls_and_exits_zero(tmp_path):
    recorder = CallRecorder()
    client = FakeEvidenceClient(
        bundles={}, run_timing=(WINDOW_CREATED_AT + timedelta(days=2), None)
    )
    ports = _fake_ports(recorder=recorder, evidence_client=client)

    result = run_scheduled(ports, work_trusted_root=tmp_path / "work")

    assert result.exit_code == 0
    assert recorder.calls == ["shutdown_oidc"]  # only S18 cleanup runs, unconditionally
    step_ids = [r.step_id for r in result.ledger.to_records()]
    assert step_ids == ["S01_DERIVE_GITHUB_CONTEXT", "S02_DISCOVER_ACTIVE_WINDOW"]
    assert result.ledger.to_records()[-1].status == "EARLY_EXIT"
    assert len(result.staged_artifacts) == 1
    assert result.staged_artifacts[0].name.startswith("sentinel-p5-prewindow-")


def test_slot_not_open_makes_zero_provider_calls(tmp_path):
    window = make_scheduled_window()
    genesis_root = _build_genesis_bundle(tmp_path, window)
    recorder = CallRecorder()
    client = FakeEvidenceClient(
        bundles={an.genesis_name(window.window_id, window.control_run_id): genesis_root},
        # created_at BEFORE slot 1 opens
        run_timing=(window.created_at_utc + timedelta(hours=1), None),
    )
    ports = _fake_ports(recorder=recorder, evidence_client=client)

    result = run_scheduled(ports, work_trusted_root=tmp_path / "work")

    assert result.exit_code == 0
    assert recorder.calls == ["shutdown_oidc"]
    assert result.ledger.to_records()[-1].step_id == "S03_RESOLVE_OWNED_SLOT"
    assert result.ledger.to_records()[-1].status == "EARLY_EXIT"


# ---------------------------------------------------------------------------
# Provenance refusal
# ---------------------------------------------------------------------------


def test_wrong_event_refuses_before_any_provider_call(tmp_path):
    window = make_scheduled_window()
    genesis_root = _build_genesis_bundle(tmp_path, window)
    recorder = CallRecorder()
    client = FakeEvidenceClient(
        bundles={an.genesis_name(window.window_id, window.control_run_id): genesis_root},
        run_timing=(slot_ts(1), None),
    )
    env = dict(ENV, GITHUB_EVENT_NAME="workflow_dispatch")
    ports = _fake_ports(recorder=recorder, evidence_client=client, env=env)

    result = run_scheduled(ports, work_trusted_root=tmp_path / "work")

    assert result.exit_code == 1
    assert recorder.calls == ["shutdown_oidc"]
    assert result.ledger.to_records()[-1].step_id == "S04_VALIDATE_PROVENANCE"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_qualifying_run_reaches_s18_in_exact_order(tmp_path):
    window = make_scheduled_window()
    genesis_root = _build_genesis_bundle(tmp_path, window)
    recorder = CallRecorder()
    client = FakeEvidenceClient(
        bundles={an.genesis_name(window.window_id, window.control_run_id): genesis_root},
        run_timing=(slot_ts(1), None),
    )

    @dataclass
    class FakeOutcome:
        run_id: str
        status: str

    def run_sentinel(working_state, run_id):
        recorder.record("run_sentinel")
        # simulate a completed live run: append exactly one CostRow for run_id
        with open(working_state.cost_ledger_path, "a", encoding="utf-8") as fh:
            from telemetry.cost_ledger import serialize_cost_row
            from contracts.schemas import CostRow

            row = CostRow(
                schema_version=1, run_id=run_id, recorded_at_utc=slot_ts(1),
                run_kind="live", model="claude-haiku-4-5-20251001",
                input_tokens=10, output_tokens=5, cost_eur_micros=1000,
            )
            fh.write(serialize_cost_row(row) + "\n")
        return FakeOutcome(run_id=run_id, status="COMPLETED")

    ports = ScheduledPorts(
        env=ENV,
        clock=lambda: slot_ts(1),
        evidence_client=client,
        wif_ready=lambda: recorder.record("wif_ready"),
        acquire_oidc=lambda: (recorder.record("acquire_oidc"), object())[1],
        install_token=lambda session: recorder.record("install_token"),
        shutdown_oidc=lambda session: recorder.record("shutdown_oidc"),
        run_sentinel=run_sentinel,
    )

    result = run_scheduled(ports, work_trusted_root=tmp_path / "work")

    assert result.exit_code == 0, [r for r in result.ledger.to_records()]
    step_ids = [r.step_id for r in result.ledger.to_records()]
    assert step_ids[0] == "S01_DERIVE_GITHUB_CONTEXT"
    assert step_ids[-1] == "S18_CLEANUP_TOKEN_FILE"
    assert "S17_STAGE_SUCCESSOR_ARTIFACT" in step_ids
    assert recorder.calls == [
        "wif_ready", "acquire_oidc", "install_token", "run_sentinel", "shutdown_oidc",
    ]
    slot_artifacts = [a for a in result.staged_artifacts if "slot" in a.name]
    assert len(slot_artifacts) == 1
    validated = b.validate_bundle(slot_artifacts[0].path)
    assert validated.manifest.qualification_outcome == "QUALIFYING"


# ---------------------------------------------------------------------------
# Cadence refusal (S06)
# ---------------------------------------------------------------------------


def test_cost_cadence_refusal_builds_refusal_bundle_and_makes_zero_provider_calls(tmp_path):
    window = make_scheduled_window()
    genesis = make_genesis(window)
    # Spend already above the EUR50 hard-start ceiling for the ordinary
    # 750,000 allowance: 49,500,000 + 750,000 > 50,000,000.
    from contracts.schemas import CostRow
    from telemetry.cost_ledger import serialize_cost_row

    spend = 49_500_000
    control_state = make_control_state(window, slot_index=0, spend=spend, evaluated_at=WINDOW_CREATED_AT)
    genesis_src = tmp_path / "genesis-src"
    genesis_src.mkdir(parents=True)
    row = CostRow(
        schema_version=1, run_id="prior-run", recorded_at_utc=WINDOW_CREATED_AT,
        run_kind="live", model="claude-haiku-4-5-20251001", input_tokens=0, output_tokens=0,
        cost_eur_micros=spend,
    )
    built = build_valid_bundle(
        genesis_src, window=window, manifest_fields=genesis.model_dump(exclude={"carried_files"}),
        control_state=control_state, cost_rows_jsonl=serialize_cost_row(row) + "\n",
    )
    genesis_root = built.root

    recorder = CallRecorder()
    client = FakeEvidenceClient(
        bundles={an.genesis_name(window.window_id, window.control_run_id): genesis_root},
        run_timing=(slot_ts(1), None),
    )
    ports = _fake_ports(recorder=recorder, evidence_client=client)
    ports = ScheduledPorts(
        env=ENV, clock=lambda: slot_ts(1), evidence_client=client,
        wif_ready=ports.wif_ready, acquire_oidc=ports.acquire_oidc,
        install_token=ports.install_token, shutdown_oidc=ports.shutdown_oidc,
        run_sentinel=ports.run_sentinel,
    )

    result = run_scheduled(ports, work_trusted_root=tmp_path / "work")

    assert result.exit_code == 0
    assert recorder.calls == ["shutdown_oidc"]  # zero wif/oidc/provider calls
    assert result.ledger.to_records()[-1].step_id == "S06_EVALUATE_SPEND_AND_CADENCE"
    assert result.ledger.to_records()[-1].status == "EARLY_EXIT"
    refusal_artifacts = [a for a in result.staged_artifacts if "refusal" in a.name]
    assert len(refusal_artifacts) == 1
    validated = b.validate_bundle(refusal_artifacts[0].path)
    assert validated.manifest.bundle_kind == "CONTROL_REFUSAL"
    assert validated.manifest.qualification_outcome == "COST_CADENCE_REFUSAL"


# ---------------------------------------------------------------------------
# Non-qualifying post-execution outcome (S15 seam-6 construction)
# ---------------------------------------------------------------------------


def test_failed_sentinel_run_builds_durable_failed_nonterminal_successor(tmp_path):
    window = make_scheduled_window()
    genesis_root = _build_genesis_bundle(tmp_path, window)
    recorder = CallRecorder()
    client = FakeEvidenceClient(
        bundles={an.genesis_name(window.window_id, window.control_run_id): genesis_root},
        run_timing=(slot_ts(1), None),
    )

    @dataclass
    class FakeOutcome:
        run_id: str
        status: str

    def run_sentinel(working_state, run_id):
        recorder.record("run_sentinel")
        from telemetry.cost_ledger import serialize_cost_row
        from contracts.schemas import CostRow

        with open(working_state.cost_ledger_path, "a", encoding="utf-8") as fh:
            row = CostRow(
                schema_version=1, run_id=run_id, recorded_at_utc=slot_ts(1),
                run_kind="live", model="claude-haiku-4-5-20251001",
                input_tokens=1, output_tokens=1, cost_eur_micros=100,
            )
            fh.write(serialize_cost_row(row) + "\n")
        return FakeOutcome(run_id=run_id, status="FAILED")

    ports = ScheduledPorts(
        env=ENV, clock=lambda: slot_ts(1), evidence_client=client,
        wif_ready=lambda: recorder.record("wif_ready"),
        acquire_oidc=lambda: (recorder.record("acquire_oidc"), object())[1],
        install_token=lambda session: recorder.record("install_token"),
        shutdown_oidc=lambda session: recorder.record("shutdown_oidc"),
        run_sentinel=run_sentinel,
    )

    result = run_scheduled(ports, work_trusted_root=tmp_path / "work")

    assert result.exit_code == 0, [r for r in result.ledger.to_records()]
    slot_artifacts = [a for a in result.staged_artifacts if "slot" in a.name]
    assert len(slot_artifacts) == 1
    validated = b.validate_bundle(slot_artifacts[0].path)
    assert validated.manifest.qualification_outcome == "FAILED_NONTERMINAL"
    assert validated.control_state.window_consumed is True
    assert validated.control_state.window_consume_reason == "FAILED_NONTERMINAL"


# ---------------------------------------------------------------------------
# Rehearsal (model-free; no OIDC/provider ports exist at all)
# ---------------------------------------------------------------------------


def test_rehearsal_builds_synthetic_genesis_and_never_touches_lineage_names(tmp_path):
    from sentinel.phase5.orchestrator import run_rehearsal

    recorder = CallRecorder()
    client = FakeEvidenceClient(bundles={}, run_timing=(slot_ts(1), None))
    ports = ScheduledPorts(
        env=ENV, clock=lambda: slot_ts(1), evidence_client=client,
        wif_ready=lambda: recorder.record("wif_ready"),
        acquire_oidc=lambda: recorder.record("acquire_oidc"),
        install_token=lambda s: recorder.record("install_token"),
        shutdown_oidc=lambda s: recorder.record("shutdown_oidc"),
        run_sentinel=lambda *a: recorder.record("run_sentinel"),
    )

    result = run_rehearsal(ports, work_trusted_root=tmp_path / "work", expected_source_sha=SOURCE_SHA)

    assert result.exit_code == 0, [r for r in result.ledger.to_records()]
    assert recorder.calls == []  # rehearsal never invokes any provider-shaped port
    assert len(result.staged_artifacts) == 1
    assert result.staged_artifacts[0].name.startswith("sentinel-p5-rehearsal-")
    step_ids = [r.step_id for r in result.ledger.to_records()]
    for banned in ("S07_ASSERT_WIF_CONFIG_READY", "S09_REQUEST_OIDC_TOKEN", "S12_EXECUTE_LIVE_SENTINEL_RUN"):
        assert banned not in step_ids


def test_rehearsal_wrong_expected_source_sha_refuses(tmp_path):
    client = FakeEvidenceClient(bundles={}, run_timing=(slot_ts(1), None))
    ports = ScheduledPorts(
        env=ENV, clock=lambda: slot_ts(1), evidence_client=client,
        wif_ready=lambda: None, acquire_oidc=lambda: None,
        install_token=lambda s: None, shutdown_oidc=lambda s: None, run_sentinel=lambda *a: None,
    )
    from sentinel.phase5.orchestrator import run_rehearsal

    result = run_rehearsal(ports, work_trusted_root=tmp_path / "work", expected_source_sha="b" * 40)
    assert result.exit_code == 1
    assert result.staged_artifacts == ()

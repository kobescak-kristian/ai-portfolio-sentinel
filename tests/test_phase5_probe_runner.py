"""Tests for scripts/run_phase5_wif_probe.py (P5-B Part 3/3).
P5-C plumbing only; Part 3 never executes it.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE_RUNNER_PATH = REPO_ROOT / "scripts" / "run_phase5_wif_probe.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_phase5_wif_probe", PROBE_RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_purpose_string_and_totals_exact():
    module = _load_module()
    assert module.PURPOSE == "P5C_WIF_PROBE"
    assert module.PROBE_TOTAL_EUR_MICROS == 150_000
    assert module.PROBE_RESERVE_EUR_MICROS == 150_000


def test_fx_and_wif_and_oneshot_checks_precede_marker_write_in_preflight():
    """Seam 7: every retryable preflight (expected-source, one-shot
    discovery, WIF config, FX resolution + coordinator construction)
    must run BEFORE write_marker_json in cmd_preflight's source order."""
    text = PROBE_RUNNER_PATH.read_text(encoding="utf-8")
    start = text.index("def cmd_preflight")
    end = text.index("def cmd_execute")
    body = text[start:end]
    source_idx = body.index("assert_expected_source_on_disk")
    oneshot_idx = body.index("assert_purpose_not_yet_consumed")
    wif_idx = body.index("auth.assert_wif_config_ready")
    fx_idx = body.index("resolve_ecb_usd_per_eur")
    marker_idx = body.index("write_marker_json")
    assert source_idx < marker_idx
    assert oneshot_idx < marker_idx
    assert wif_idx < marker_idx
    assert fx_idx < marker_idx


def test_marker_visibility_confirmed_before_oidc_in_execute():
    text = PROBE_RUNNER_PATH.read_text(encoding="utf-8")
    start = text.index("def cmd_execute")
    body = text[start:]
    marker_confirm_idx = body.index("assert_marker_visible_for_this_run")
    oidc_idx = body.index("oidc.acquire_oidc")
    assert marker_confirm_idx < oidc_idx


def test_attempt_gt_1_is_ineligible_for_marker_creation():
    from sentinel.phase5.oneshot import is_eligible_marker_creation
    from sentinel.phase5.models import OneShotMarker
    from datetime import datetime, timezone

    marker = OneShotMarker(
        schema_version=1, purpose="P5C_WIF_PROBE", created_at_utc=datetime.now(timezone.utc),
        workflow_identity=".github/workflows/sentinel-wif-probe.yml", github_run_id="1",
        run_attempt=2, event="workflow_dispatch", source_sha="a" * 40,
    )
    assert is_eligible_marker_creation(marker) is False


def test_existing_marker_refuses_before_any_new_marker_creation():
    from sentinel.phase5.oneshot import OneShotAlreadyConsumed, assert_purpose_not_yet_consumed
    from sentinel.phase5.models import OneShotMarker
    from datetime import datetime, timezone
    import pytest

    existing = OneShotMarker(
        schema_version=1, purpose="P5C_WIF_PROBE", created_at_utc=datetime.now(timezone.utc),
        workflow_identity=".github/workflows/sentinel-wif-probe.yml", github_run_id="1",
        run_attempt=1, event="workflow_dispatch", source_sha="a" * 40,
    )
    with pytest.raises(OneShotAlreadyConsumed):
        assert_purpose_not_yet_consumed("P5C_WIF_PROBE", [existing])


def test_no_static_credential_fallback_path():
    text = PROBE_RUNNER_PATH.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in text
    assert "LOCAL_OAUTH" not in text
    assert "auth.WIF" in text


def test_probe_uses_haiku_model_not_sonnet():
    text = PROBE_RUNNER_PATH.read_text(encoding="utf-8")
    assert "HAIKU_ORDINARY.model" in text
    assert "SONNET_OFFICIAL_GATE" not in text


def test_disposition_vocabulary_is_closed_pass_or_fail():
    text = PROBE_RUNNER_PATH.read_text(encoding="utf-8")
    assert '"CAPABILITY_PASS"' in text
    assert '"CAPABILITY_FAIL"' in text


def test_probe_evidence_record_disposition_pass_requires_cost_rows():
    """Schema-level guarantee (evidence_records.py), re-asserted here
    against a probe-shaped payload so the runner's own disposition
    logic cannot silently drift from the schema it writes to."""
    import pytest
    from datetime import datetime, timezone
    from pydantic import ValidationError

    from sentinel.phase5.evidence_records import ProbeEvidenceRecord

    with pytest.raises(ValidationError):
        ProbeEvidenceRecord(
            schema_version=1, workflow_identity=".github/workflows/sentinel-wif-probe.yml",
            github_run_id="1", run_attempt=1, event="workflow_dispatch", ref="refs/heads/main",
            source_sha="a" * 40, created_at_utc=datetime.now(timezone.utc), steps=(),
            expected_source_sha="a" * 40, disposition="CAPABILITY_PASS",
            cost_rows=(), accounted_total_eur_micros=0,
        )

"""Tests for scripts/run_phase5_official_gate.py (P5-B Part 3/3).
ADR-0011 Section 7 pins this exact path; Part 3 never executes it.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_RUNNER_PATH = REPO_ROOT / "scripts" / "run_phase5_official_gate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_phase5_official_gate", GATE_RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adr_pinned_path_exists_verbatim():
    assert GATE_RUNNER_PATH.exists()


def test_gate_cost_literals_cross_pinned_against_sonnet_official_gate():
    """Anti-tautology precedent (run_phase3_dev_gate.py's own
    PER_RUN_COST_CAP_EUR_MICROS comment): the gate runner's local
    literals are NOT imported from agents/checker/config.py, so this
    test is the only place they are checked to still agree with it."""
    from agents.checker.config import SONNET_OFFICIAL_GATE

    module = _load_module()
    assert module.GATE_TOTAL_EUR_MICROS == 5_000_000 == SONNET_OFFICIAL_GATE.run_budget_eur_micros
    assert module.GATE_RESERVE_EUR_MICROS == 1_000_000 == SONNET_OFFICIAL_GATE.max_per_call_reserve_eur_micros


def test_purpose_string_is_exact():
    module = _load_module()
    assert module.PURPOSE == "P5D_OFFICIAL_SONNET_GATE"


def test_uses_one_shared_coordinator_not_two_independent_ones():
    """Unlike run_phase3_dev_gate.py's two independent breakers, the
    gate session must construct exactly ONE RunBudgetCoordinator and
    pass it to both designated runs."""
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text)
    coordinator_constructions = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RunBudgetCoordinator"
    ]
    # exactly two call SITES in source (preflight's proof-construction,
    # and execute's real one) but the real gate session itself
    # (_run_gate_session) receives the coordinator as a parameter and
    # constructs none of its own.
    assert "def _run_gate_session(*, gate_root: Path, coordinator, session" in text
    assert "coordinator=coordinator" in text


def test_wif_auth_profile_selected_not_local_oauth():
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    assert "auth.WIF" in text
    assert "auth.LOCAL_OAUTH" not in text


def test_expected_source_sha_flows_into_prospective_preflight_never_replaced_by_github_sha():
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    assert "args.expected_source_sha" in text
    assert "github.sha" not in text
    assert "os.environ[\"GITHUB_SHA\"]" not in text
    assert 'os.environ.get("GITHUB_SHA")' not in text


def test_marker_written_before_provider_construction_in_preflight():
    """All retryable preflights (source checks, fixture presence, WIF
    config, FX + coordinator construction proof) must precede
    write_marker_json in cmd_preflight's source order."""
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    preflight_start = text.index("def cmd_preflight")
    preflight_end = text.index("def _run_gate_session")
    body = text[preflight_start:preflight_end]
    fx_idx = body.index("resolve_ecb_usd_per_eur")
    wif_idx = body.index("auth.assert_wif_config_ready")
    marker_idx = body.index("write_marker_json")
    assert wif_idx < marker_idx
    assert fx_idx < marker_idx


def test_no_generic_model_selector_and_cli_untouched():
    """No --auth-mode / generic model-purpose flag anywhere in this
    script, and sentinel/cli.py remains the guard-tested,
    selector-free CLI (covered directly by
    tests/test_execution_profile.py::test_sentinel_cli_has_no_model_or_profile_selecting_flag)."""
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    assert "--auth-mode" not in text
    assert "--model-purpose" not in text


def test_official_gate_disposition_vocabulary_is_closed():
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    assert '"GREEN" if result["green"] else "HONEST_FAIL"' in text
    assert 'disposition = "INFRASTRUCTURE_FAILURE"' in text

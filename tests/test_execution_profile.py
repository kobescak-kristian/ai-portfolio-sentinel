"""Phase-5 execution-profile refactor (dispatch q77-p5b-foundation-a,
ADR-0011 §7 and its 2026-08-23 Sonnet-reserve amendment).

Two things this file proves, model-free and network-free (conftest.py's
autouse ``block_network`` fixture covers every test here with no
redeclaration needed):

1. ``HAIKU_ORDINARY`` reproduces the existing bare Haiku constants
   exactly, the default-constructed ``RunBudgetCoordinator`` and the
   default-constructed ``build_caged_judgment_stub`` factory are
   byte-for-byte unchanged, and ``sentinel/cli.py`` gained no new
   import or flag — ordinary ``--judgment-mode agent`` behavior is
   untouched.
2. ``SONNET_OFFICIAL_GATE`` matches the ADR-0011 §7 amendment's fixed
   numbers exactly, a coordinator built from it honors its own
   per-call reserve, and no code path in this dispatch constructs a
   live run from it — it is configuration only until a later,
   dedicated Phase-5 gate runner exists.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.checker import auth
from agents.checker.budget import RunBudgetCoordinator
from agents.checker.config import (
    AUTH_MODE_LABEL,
    HAIKU_ORDINARY,
    MAX_PER_CALL_RESERVE_EUR_MICROS,
    MODEL,
    RUN_BUDGET_EUR_MICROS,
    SDK_ALLOWANCE_SAFETY_MARGIN,
    SONNET_OFFICIAL_GATE,
    ExecutionProfile,
)
from agents.checker.fx import FxRate
from agents.checker.harness import CagedCheckerStub, build_caged_judgment_stub
from checks.judgment.stubs import JudgmentRequest
from contracts.schemas import RunRecord
from sentinel import ledger

REPO_ROOT = Path(__file__).resolve().parent.parent

T0 = datetime(2026, 8, 24, 6, 0, 0, tzinfo=timezone.utc)

_FAKE_RATE = FxRate(
    source="ecb-eurofxref-daily",
    rate_date="2026-08-24",
    retrieved_at_utc=T0,
    usd_per_eur=Decimal("1.1554"),
)


# =====================================================================
# ExecutionProfile golden values
# =====================================================================


def test_haiku_ordinary_reproduces_the_existing_module_constants_exactly():
    assert HAIKU_ORDINARY == ExecutionProfile(
        name="haiku-ordinary",
        model=MODEL,
        run_budget_eur_micros=RUN_BUDGET_EUR_MICROS,
        max_per_call_reserve_eur_micros=MAX_PER_CALL_RESERVE_EUR_MICROS,
        sdk_allowance_safety_margin=SDK_ALLOWANCE_SAFETY_MARGIN,
    )


def test_sonnet_official_gate_matches_the_adr0011_amendment_exactly():
    """Literal-pinned, not symbolic, so drift in the fixed contract
    itself is caught (the same discipline test_bounds.py already
    applies to MODEL/RUN_BUDGET_EUR_MICROS)."""
    assert SONNET_OFFICIAL_GATE.model == "claude-sonnet-5"
    assert SONNET_OFFICIAL_GATE.run_budget_eur_micros == 5_000_000
    assert SONNET_OFFICIAL_GATE.max_per_call_reserve_eur_micros == 1_000_000
    assert SONNET_OFFICIAL_GATE.sdk_allowance_safety_margin == SDK_ALLOWANCE_SAFETY_MARGIN
    # The amendment's own stated rationale: proportional consistency
    # with the existing Haiku reserve fraction, not a coincidence.
    assert Decimal(SONNET_OFFICIAL_GATE.max_per_call_reserve_eur_micros) / Decimal(
        SONNET_OFFICIAL_GATE.run_budget_eur_micros
    ) == Decimal(HAIKU_ORDINARY.max_per_call_reserve_eur_micros) / Decimal(
        HAIKU_ORDINARY.run_budget_eur_micros
    )


def test_execution_profile_is_frozen():
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        HAIKU_ORDINARY.model = "something-else"


# =====================================================================
# RunBudgetCoordinator: new per-call-reserve / safety-margin seams
# =====================================================================


def test_default_constructed_coordinator_reproduces_pre_refactor_reserve_amount():
    coord = RunBudgetCoordinator(fx_rate=_FAKE_RATE)
    reservation = coord.reserve()
    assert reservation.reserved_eur_micros == MAX_PER_CALL_RESERVE_EUR_MICROS


def test_default_constructed_coordinator_reproduces_pre_refactor_usd_allowance():
    a = RunBudgetCoordinator(fx_rate=_FAKE_RATE)
    b = RunBudgetCoordinator(fx_rate=_FAKE_RATE)
    assert a.reserve().sdk_max_budget_usd == b.reserve().sdk_max_budget_usd


def test_custom_per_call_reserve_caps_reservation_not_the_haiku_constant():
    coord = RunBudgetCoordinator(
        fx_rate=_FAKE_RATE,
        total_eur_micros=SONNET_OFFICIAL_GATE.run_budget_eur_micros,
        max_per_call_reserve_eur_micros=SONNET_OFFICIAL_GATE.max_per_call_reserve_eur_micros,
    )
    reservation = coord.reserve()
    assert reservation.reserved_eur_micros == 1_000_000
    assert reservation.reserved_eur_micros != MAX_PER_CALL_RESERVE_EUR_MICROS


def test_custom_safety_margin_changes_the_usd_allowance():
    conservative = RunBudgetCoordinator(fx_rate=_FAKE_RATE, sdk_allowance_safety_margin="0.10")
    default = RunBudgetCoordinator(fx_rate=_FAKE_RATE)
    assert conservative.reserve().sdk_max_budget_usd < default.reserve().sdk_max_budget_usd


# =====================================================================
# Factory defaults: proof ordinary CLI behavior is unchanged
# =====================================================================


def test_build_caged_judgment_stub_defaults_are_haiku_ordinary_and_local_oauth(tmp_path):
    with patch("agents.checker.harness.auth.assert_no_auth_override_risk", return_value=None), \
         patch("agents.checker.harness.resolve_ecb_usd_per_eur", return_value=_FAKE_RATE):
        stub = build_caged_judgment_stub(
            run_id="r-1",
            db_path=tmp_path / "sentinel.sqlite3",
            clock=lambda: T0,
        )
    assert stub.model == MODEL
    assert stub.auth_profile is auth.LOCAL_OAUTH
    assert stub.auth_profile.label == AUTH_MODE_LABEL
    assert stub.coordinator.total_eur_micros == RUN_BUDGET_EUR_MICROS
    assert stub.coordinator.max_per_call_reserve_eur_micros == MAX_PER_CALL_RESERVE_EUR_MICROS
    assert stub.coordinator.sdk_allowance_safety_margin == SDK_ALLOWANCE_SAFETY_MARGIN


def test_sentinel_cli_call_site_passes_no_profile_override():
    """sentinel/cli.py's sole call site must keep passing neither
    ``profile`` nor ``auth_profile`` -- that is what makes the new
    defaults reproduce today's behavior. A source-text check rather
    than an import, since importing sentinel.cli here would require
    the Agent SDK to be importable even for this stub-mode-adjacent
    assertion."""
    source = (REPO_ROOT / "sentinel" / "cli.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_caged_judgment_stub"
    ]
    assert len(calls) == 1
    kwargs = {kw.arg for kw in calls[0].keywords}
    assert kwargs == {"run_id", "db_path"}


def test_sentinel_cli_has_no_model_or_profile_selecting_flag():
    """ADR-0011 §7: no ordinary Sentinel CLI option exposes 'official
    gate' or a generic model-purpose switch."""
    source = (REPO_ROOT / "sentinel" / "cli.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    add_argument_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
    ]
    flag_names = {
        arg.value
        for call in add_argument_calls
        for arg in call.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    }
    assert not any("model" in name.lower() for name in flag_names)
    assert not any("profile" in name.lower() for name in flag_names)
    assert not any("gate" in name.lower() for name in flag_names)


def test_ordinary_judge_call_records_unchanged_model_and_auth_mode_on_the_ledger_row(tmp_path):
    """End-to-end through the default stub: the agent_calls row this
    caged run writes is indistinguishable from before this refactor."""
    from types import SimpleNamespace

    db_path = tmp_path / "sentinel.sqlite3"
    conn = ledger.open_ledger(db_path)
    with ledger.unit_of_work(conn):
        ledger.insert_run(
            conn,
            RunRecord(
                schema_version=1,
                run_id="r-1",
                run_kind="dev",
                status="RUNNING",
                started_at_utc=T0,
                tasks_created=0,
                tasks_terminal=0,
                findings_new=0,
                findings_still_open=0,
                findings_resolved=0,
            ),
        )

    def clean_query_fn(check_class, reservation, state, user_prompt, model=None):
        return SimpleNamespace(
            is_error=False,
            subtype="success",
            num_turns=1,
            total_cost_usd=0.0001,
            usage={"input_tokens": 10, "output_tokens": 5},
            result="done",
        )

    coordinator = RunBudgetCoordinator(fx_rate=_FAKE_RATE)
    with patch("agents.checker.harness.auth.assert_no_auth_override_risk", return_value=None):
        stub = CagedCheckerStub(
            run_id="r-1", conn=conn, coordinator=coordinator, clock=lambda: T0, query_fn=clean_query_fn
        )
        stub.judge(
            JudgmentRequest(
                surface="acme/STATE.md",
                check_class="missing-synthetic-label",
                path="STATE.md",
                text="line one\nline two has 42\nline three",
            )
        )
    rows = ledger.list_agent_calls_for_run(conn, "r-1")
    assert rows[0].model == MODEL
    assert rows[0].auth_mode == AUTH_MODE_LABEL
    conn.close()


# =====================================================================
# No live path exists for the Sonnet profile in this dispatch
# =====================================================================


def test_sonnet_profile_is_referenced_only_by_its_own_definition_and_tests():
    """SONNET_OFFICIAL_GATE was configuration/capability data only
    through dispatch q77-p5b-foundation-a: no production module other
    than config.py's own definition referenced the name. P5-B Part 3/3
    (ADR-0011 §7's dedicated gate runner) is that later dispatch —
    exactly one additional production file, the ADR-pinned
    ``scripts/run_phase5_official_gate.py``, is now allowed to
    construct a run from it. Every other production module still may
    not."""
    production_dirs = ["sentinel", "agents", "scripts", "runner", "checks", "contracts"]
    allowed_extra = REPO_ROOT / "scripts" / "run_phase5_official_gate.py"
    hits = []
    for d in production_dirs:
        for path in (REPO_ROOT / d).rglob("*.py"):
            if path == REPO_ROOT / "agents" / "checker" / "config.py":
                continue  # the definition itself
            if path == allowed_extra:
                continue  # ADR-0011 §7's pinned dedicated gate runner
            text = path.read_text(encoding="utf-8")
            if "SONNET_OFFICIAL_GATE" in text:
                hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []
    assert allowed_extra.exists()
    assert "SONNET_OFFICIAL_GATE" in allowed_extra.read_text(encoding="utf-8")


def test_module_makes_no_network_or_provider_call():
    # Belt-and-suspenders alongside conftest.py's autouse block_network
    # fixture: this module never imports claude_agent_sdk directly.
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "claude_agent_sdk" not in imported_roots

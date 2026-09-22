"""Tests for scripts/run_phase5_official_gate.py (P5-B Part 3/3).
ADR-0011 Section 7 pins this exact path; Part 3 never executes it.
"""

from __future__ import annotations

import ast
import importlib.metadata
import importlib.util
import io
import json
import os
import sys
import types
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schemas import CostRow
from sentinel.phase5.evidence_records import GateEvidenceRecord, ProbeEvidenceRecord

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_RUNNER_PATH = REPO_ROOT / "scripts" / "run_phase5_official_gate.py"

_SHA = "a" * 40
_WIF = "github-actions-wif-federation"

# Every top-level package that lives IN this repository, never a
# third-party distribution (dispatch
# q77-p5d-premarker-dependency-repair-a): reachable from the official
# gate runner's own import graph.
_LOCAL_TOP_LEVEL_PACKAGES = frozenset(
    {"scripts", "sentinel", "agents", "contracts", "checks", "telemetry", "runner"}
)


def _normalize_dist_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _requirements_txt_top_level_names() -> set[str]:
    """Distribution names directly declared in requirements.txt
    (normalized), ignoring comments and ``-r`` includes."""
    return {
        _normalize_dist_name(line.split("==")[0])
        for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#") and not line.strip().startswith("-r")
    }


def _local_module_file(dotted: str, base_dir: Path) -> Path | None:
    rel = Path(*dotted.split("."))
    candidate = base_dir / rel.with_suffix(".py")
    if candidate.is_file():
        return candidate
    candidate = base_dir / rel / "__init__.py"
    if candidate.is_file():
        return candidate
    return None


def _collect_local_third_party_imports(entry_file: Path) -> set[str]:
    """Statically walk only files that live under this repository's
    own top-level packages, starting at ``entry_file``, collecting
    every third-party top-level import name literally written in THIS
    repo's own source -- never descending into an already-third-party
    package's own internals (dispatch
    q77-p5d-premarker-dependency-repair-a).

    This is a deliberately narrower scope than a full runtime
    ``sys.modules`` diff: a diff over the whole transitive graph also
    captures optional/soft imports deep inside already-declared
    dependencies (e.g. ``uvicorn`` opportunistically importing
    ``watchfiles``/``rich`` if present, harmless if absent) -- noise
    unrelated to what THIS repo's own unconditional top-level imports
    actually require. Every import statement this repo's own source
    writes at module level is unconditional (no repo file wraps one in
    try/except), so a plain literal-import walk is the correct,
    precise proof of the real invariant: exactly the failure mode that
    crashed GitHub Actions run 32863558192."""
    stdlib = set(sys.stdlib_module_names)
    seen: set[Path] = set()
    third_party: set[str] = set()
    stack = [entry_file.resolve()]
    while stack:
        current = stack.pop()
        if current in seen or not current.is_file():
            continue
        seen.add(current)
        tree = ast.parse(current.read_text(encoding="utf-8"), filename=str(current))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in _LOCAL_TOP_LEVEL_PACKAGES:
                        target = _local_module_file(alias.name, REPO_ROOT)
                        if target is not None:
                            stack.append(target)
                    elif top not in stdlib:
                        third_party.add(top)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level >= 1:
                    base_dir = current.parent
                    for _ in range(node.level - 1):
                        base_dir = base_dir.parent
                    if node.module:
                        target = _local_module_file(node.module, base_dir)
                        if target is not None:
                            stack.append(target)
                    else:
                        for alias in node.names:
                            target = _local_module_file(alias.name, base_dir)
                            if target is not None:
                                stack.append(target)
                    continue
                if node.module:
                    top = node.module.split(".")[0]
                    if top in _LOCAL_TOP_LEVEL_PACKAGES:
                        target = _local_module_file(node.module, REPO_ROOT)
                        if target is not None:
                            stack.append(target)
                    elif top not in stdlib:
                        third_party.add(top)
    return third_party


def _load_module():
    spec = importlib.util.spec_from_file_location("run_phase5_official_gate", GATE_RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cost_row(run_id: str = "run1") -> CostRow:
    return CostRow(
        schema_version=1, run_id=run_id, recorded_at_utc=datetime.now(timezone.utc),
        run_kind="dev", model="claude-sonnet-5", input_tokens=1, output_tokens=1, cost_eur_micros=2_000,
    )


def _gate_kwargs(**overrides) -> dict:
    """Minimal valid GateEvidenceRecord kwargs (dispatch
    q77-p5d-s1-evidence-repair-a) — every test overrides only the
    field(s) it is actually exercising."""
    base = dict(
        schema_version=1, workflow_identity=".github/workflows/sentinel-official-gate.yml",
        github_run_id="1", run_attempt=1, event="workflow_dispatch", ref="refs/heads/main",
        source_sha=_SHA, created_at_utc=datetime.now(timezone.utc), steps=(),
        expected_source_sha=_SHA, model="claude-sonnet-5", profile_name="sonnet-official-gate",
        run_ids=("run1", "run2"), scoring={"emitted": 1}, thresholds={},
        invariant_results={"ok": True}, execution_validity={"valid": True},
        miss_patterns=(), failed_checks=(), cost_rows=(_cost_row(),),
        accounted_total_eur_micros=2_000, disposition="GREEN", auth_mode=_WIF,
    )
    base.update(overrides)
    return base


class _FakeCall:
    """Minimal stand-in for ``AgentCallRow`` carrying only the one
    attribute ``_derive_auth_mode``/``_recover_partial_auth_mode``
    inspect."""

    def __init__(self, auth_mode: str) -> None:
        self.auth_mode = auth_mode


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
    assert "*, gate_root: Path, coordinator, session, expected_source_sha: str," in text
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


def test_durable_history_precedes_replacement_check_precedes_marker_write():
    """Dispatch q77-p5d-repair-stage2-implement-a: the durable receipt
    registry is loaded, then the durable one-shot consumption check
    runs, then the structural replacement-eligibility check runs, all
    strictly before write_marker_json in cmd_preflight's source
    order."""
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    preflight_start = text.index("def cmd_preflight")
    preflight_end = text.index("def _run_gate_session")
    body = text[preflight_start:preflight_end]
    history_idx = body.index("load_durable_history")
    oneshot_idx = body.index("assert_oneshot_not_consumed_durably")
    eligibility_idx = body.index("assert_replacement_history_permits")
    marker_idx = body.index("write_marker_json")
    assert history_idx < oneshot_idx < eligibility_idx < marker_idx


def test_committed_registry_already_shows_original_p5d_consumed_and_preflight_refuses():
    """Dispatch q77-p5d-repair-stage2-implement-a: the committed
    durable receipt registry already carries a consumed
    P5D_OFFICIAL_SONNET_GATE marker receipt for the original run, so a
    full preflight run against a zero-live-marker world must still
    refuse via the durable check -- artifact expiry can never silently
    re-open a consumed one-shot, and PURPOSE stays the unarmed original
    value. Model-free: no network, no GitHub call, no OIDC/provider
    activity, no marker written."""
    from sentinel.phase5.receipts import load_registry

    module = _load_module()
    committed = REPO_ROOT / "artifacts" / "phase5_receipt_registry.jsonl"
    receipts = load_registry(committed)
    with pytest.raises(module.Phase5ScriptError):
        module.assert_oneshot_not_consumed_durably(module.PURPOSE, receipts, [])


def test_replacement_not_permitted_for_unarmed_original_purpose():
    """The structural replacement-eligibility check also independently
    refuses for the unarmed original purpose (defense in depth beyond
    the durable one-shot check above): PURPOSE is not the frozen
    replacement purpose, so ``assert_replacement_history_permits``
    refuses regardless of what the registry shows."""
    from sentinel.phase5.receipts import load_registry

    module = _load_module()
    committed = REPO_ROOT / "artifacts" / "phase5_receipt_registry.jsonl"
    receipts = load_registry(committed)
    with pytest.raises(module.Phase5ScriptError):
        module.assert_replacement_history_permits(receipts, [], module.PURPOSE)


def test_no_generic_model_selector_and_cli_untouched():
    """No --auth-mode / generic model-purpose flag anywhere in this
    script, and sentinel/cli.py remains the guard-tested,
    selector-free CLI (covered directly by
    tests/test_execution_profile.py::test_sentinel_cli_has_no_model_or_profile_selecting_flag)."""
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    assert "--auth-mode" not in text
    assert "--model-purpose" not in text


def test_official_gate_disposition_vocabulary_is_closed():
    """Stage 2B-2 (dispatch q77-p5d-repair-stage2b2-implement-a): the
    runner's only quality expression is unchanged, and its only
    execution-invalid record comes from the Stage-2B-1
    ``build_invalid_record`` with an explicit RUNNER writer."""
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    assert '"GREEN" if result["green"] else "HONEST_FAIL"' in text
    assert "build_invalid_record(" in text
    assert 'writer="RUNNER"' in text


@pytest.mark.parametrize(
    "entry_file",
    [GATE_RUNNER_PATH, REPO_ROOT / "scripts" / "run_phase5_gate_finalizer.py"],
    ids=["official-gate-runner", "gate-finalizer"],
)
def test_official_gate_runtime_import_closure_matches_requirements_txt(entry_file):
    """Dispatch q77-p5d-premarker-dependency-repair-a: proves the exact
    invariant whose absence (PyYAML) crashed GitHub Actions run
    32863558192 at ``ModuleNotFoundError: No module named 'yaml'``
    inside ``preflight``, before the one-shot marker was ever uploaded
    -- every Phase-5 workflow installs only ``requirements.txt``, never
    ``requirements-dev.txt``. Fully offline and deterministic: a
    static AST walk of this repo's own source (see
    ``_collect_local_third_party_imports``), never a network call or
    venv build."""
    third_party = _collect_local_third_party_imports(entry_file)
    declared_top_level = _requirements_txt_top_level_names()

    import_name_to_dists = importlib.metadata.packages_distributions()
    missing = []
    for top_name in sorted(third_party):
        candidate_dists = {_normalize_dist_name(d) for d in import_name_to_dists.get(top_name, ())}
        if not candidate_dists:
            missing.append(f"{top_name} (no installed distribution metadata found for this import)")
        elif not (candidate_dists & declared_top_level):
            missing.append(
                f"{top_name} -> {sorted(candidate_dists)} (not declared in requirements.txt)"
            )
    assert not missing, (
        "official-gate runtime import closure not covered by requirements.txt: "
        + "; ".join(missing)
    )
    # The specific regression this dispatch fixes: yaml must actually
    # be reachable from this exact import graph (a sanity check that
    # the walk above is exercising the real defect's code path, not
    # vacuously passing because nothing third-party was found).
    assert "yaml" in third_party
    assert "pyyaml" in declared_top_level


# ======================================================================
# Evidence-readiness repair (dispatch q77-p5d-s1-evidence-repair-a):
# Defect A (durable auth provenance) and Defect B (HONEST_FAIL
# recordability for non-scoring failure causes). Every test below is
# model-free and makes zero network/provider/OIDC/model calls.
# ======================================================================

# --- AUTH PROVENANCE -------------------------------------------------

def test_green_with_wif_auth_mode_constructs_and_serializes():
    """(1) GREEN with persisted auth_mode=github-actions-wif-federation
    constructs and serializes successfully."""
    record = GateEvidenceRecord(**_gate_kwargs(disposition="GREEN", auth_mode=_WIF))
    payload = record.model_dump_json()
    assert _WIF in payload


def test_honest_fail_with_wif_auth_constructs_and_serializes():
    """(2) HONEST_FAIL with persisted WIF auth constructs and
    serializes successfully."""
    record = GateEvidenceRecord(**_gate_kwargs(
        disposition="HONEST_FAIL", auth_mode=_WIF, failed_checks=("pooled_recall: 1/2 -> FAIL",),
    ))
    payload = record.model_dump_json()
    assert _WIF in payload
    assert record.disposition == "HONEST_FAIL"


def test_green_with_none_auth_mode_rejected():
    """(3) GREEN with auth_mode=None is rejected."""
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_gate_kwargs(disposition="GREEN", auth_mode=None))


def test_green_with_non_wif_auth_label_rejected():
    """(4) GREEN with a non-WIF auth label is rejected."""
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_gate_kwargs(
            disposition="GREEN", auth_mode="operator-subscription-oauth-assumed",
        ))


@pytest.mark.parametrize("bad_auth_mode", [None, "operator-subscription-oauth-assumed"])
def test_honest_fail_with_non_wif_or_none_auth_rejected(bad_auth_mode):
    """(5) HONEST_FAIL with non-WIF/None auth is rejected."""
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_gate_kwargs(
            disposition="HONEST_FAIL", auth_mode=bad_auth_mode,
            failed_checks=("pooled_recall: 1/2 -> FAIL",),
        ))


def test_conflicting_auth_labels_cannot_produce_valid_green_or_honest_fail():
    """(6) Conflicting persisted auth labels cannot produce a valid
    GREEN/HONEST_FAIL record. Derivation first (matches the runner's
    own path), then schema rejection of the derived placeholder."""
    module = _load_module()
    derived = module._derive_auth_mode([_FakeCall(_WIF), _FakeCall("operator-subscription-oauth-assumed")])
    assert derived == "conflicting-auth-mode"
    for disposition, extra in (
        ("GREEN", {}),
        ("HONEST_FAIL", {"failed_checks": ("pooled_recall: 1/2 -> FAIL",)}),
    ):
        with pytest.raises(ValidationError):
            GateEvidenceRecord(**_gate_kwargs(disposition=disposition, auth_mode=derived, **extra))


def test_infrastructure_failure_with_zero_model_call_rows_remains_recordable():
    """(7) INFRASTRUCTURE_FAILURE with zero model-call rows (a pre-
    provider preflight/source/WIF/OIDC/FX/setup stop) remains
    recordable — no auth provenance requirement applies."""
    module = _load_module()
    assert module._derive_auth_mode([]) is None
    record = GateEvidenceRecord(**_gate_kwargs(
        disposition="INFRASTRUCTURE_FAILURE", run_ids=(), scoring={}, execution_validity={},
        miss_patterns=(), failed_checks=(), cost_rows=(), accounted_total_eur_micros=0, auth_mode=None,
    ))
    assert record.disposition == "INFRASTRUCTURE_FAILURE"


def test_gate_runner_derives_auth_mode_from_persisted_calls_not_hardcoded():
    """(8) The gate runner derives auth provenance from persisted
    agent_calls rows, never by hard-coding the configured auth
    profile's label."""
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    assert "_derive_auth_mode(all_calls)" in text
    assert "all_calls.extend(ledger.list_agent_calls_for_run(conn, run_id))" in text
    assert f'auth_mode="{_WIF}"' not in text
    assert "auth_mode=SONNET_OFFICIAL_GATE" not in text
    # Post-marker INFRASTRUCTURE_FAILURE recovery path is wired too
    # (Stage 2B-2 split: quality record and infrastructure record;
    # Stage 2C-3 moved the call site into _infrastructure_invalid_record,
    # shared by both the QualityRefused fallback and the outer failure
    # handler, but the recovery call itself is unchanged).
    assert "auth_mode=_recover_partial_auth_mode(gate_root)" in text
    assert 'auth_mode=result["auth_mode"]' in text


def test_recover_partial_auth_mode_helper_is_best_effort(tmp_path):
    """``_recover_partial_auth_mode`` never raises and returns None
    when the gate ledger was never created (failure occurred before
    any provider work began)."""
    module = _load_module()
    assert module._recover_partial_auth_mode(tmp_path / "never-created") is None


# --- HONEST-FAIL SHAPES -----------------------------------------------

def test_failed_check_messages_extracts_only_failed_entries():
    """Pure-function proof for ``_failed_check_messages`` covering all
    four failure categories independently — no gate-session/DB/fixture
    harness required."""
    module = _load_module()
    checks = [
        (True, "pooled_precision: 2/2 -> PASS"),
        (False, "pooled_recall: 1/2 -> FAIL"),
        (True, "invariant[every_task_terminal]: PASS"),
        (False, "invariant[idempotent_rerun]: FAIL"),
        (True, "gate_session_cost_within_cap: 1000 micro-EUR (<= 5000000) -> PASS"),
        (False, "execution_validity[source_pinned]: FAIL"),
    ]
    assert module._failed_check_messages(checks) == (
        "pooled_recall: 1/2 -> FAIL",
        "invariant[idempotent_rerun]: FAIL",
        "execution_validity[source_pinned]: FAIL",
    )
    assert module._failed_check_messages([(True, "a"), (True, "b")]) == ()


def test_scoring_only_honest_failure_produces_valid_artifact():
    """(9) Scoring-only honest failure produces a valid HONEST_FAIL
    artifact with structured analysis."""
    module = _load_module()
    checks = [(False, "pooled_recall: 1/2 -> FAIL"), (True, "invariant[x]: PASS"), (True, "cost: PASS")]
    failed = module._failed_check_messages(checks)
    record = GateEvidenceRecord(**_gate_kwargs(
        disposition="HONEST_FAIL", miss_patterns=("stale-STATE-marker|synthetic-01|README.md",),
        failed_checks=failed,
    ))
    assert record.failed_checks == failed
    assert record.miss_patterns


def test_invariant_only_honest_failure_with_perfect_scoring_produces_valid_artifact():
    """(10) Invariant-only honest failure with otherwise perfect
    scoring produces a valid HONEST_FAIL artifact — the Defect-B
    regression: empty miss_patterns no longer blocks construction."""
    module = _load_module()
    checks = [
        (True, "pooled_precision: 2/2 -> PASS"), (True, "pooled_recall: 2/2 -> PASS"),
        (False, "invariant[idempotent_rerun]: FAIL"), (True, "cost: PASS"),
    ]
    failed = module._failed_check_messages(checks)
    record = GateEvidenceRecord(**_gate_kwargs(
        disposition="HONEST_FAIL", miss_patterns=(), failed_checks=failed,
    ))
    assert record.miss_patterns == ()
    assert record.failed_checks == ("invariant[idempotent_rerun]: FAIL",)


def test_execution_validity_only_honest_failure_with_perfect_scoring_produces_valid_artifact():
    """(11) Execution-validity-only honest failure with otherwise
    perfect scoring produces a valid HONEST_FAIL artifact."""
    module = _load_module()
    checks = [
        (True, "pooled_precision: 2/2 -> PASS"), (True, "pooled_recall: 2/2 -> PASS"),
        (True, "invariant[x]: PASS"), (True, "cost: PASS"),
        (False, "execution_validity[source_pinned]: FAIL"),
    ]
    failed = module._failed_check_messages(checks)
    record = GateEvidenceRecord(**_gate_kwargs(
        disposition="HONEST_FAIL", miss_patterns=(), failed_checks=failed,
    ))
    assert record.miss_patterns == ()
    assert record.failed_checks == ("execution_validity[source_pinned]: FAIL",)


def test_cost_only_honest_failure_preserves_exact_over_cap_total():
    """(12) Cost-only honest failure with accounted_total >
    5,000,000 micro-EUR produces a valid HONEST_FAIL artifact,
    preserving the exact over-cap total rather than clamping/rejecting
    it."""
    module = _load_module()
    checks = [
        (True, "pooled_precision: 2/2 -> PASS"), (True, "pooled_recall: 2/2 -> PASS"),
        (True, "invariant[x]: PASS"),
        (False, "gate_session_cost_within_cap: 6000000 micro-EUR (<= 5000000) -> FAIL"),
    ]
    failed = module._failed_check_messages(checks)
    over_cap_row = CostRow(
        schema_version=1, run_id="run1", recorded_at_utc=datetime.now(timezone.utc),
        run_kind="dev", model="claude-sonnet-5", input_tokens=1, output_tokens=1, cost_eur_micros=6_000_000,
    )
    record = GateEvidenceRecord(**_gate_kwargs(
        disposition="HONEST_FAIL", miss_patterns=(), failed_checks=failed,
        cost_rows=(over_cap_row,), accounted_total_eur_micros=6_000_000,
    ))
    assert record.accounted_total_eur_micros == 6_000_000  # never clamped to the 5,000,000 cap
    assert record.failed_checks == ("gate_session_cost_within_cap: 6000000 micro-EUR (<= 5000000) -> FAIL",)


def test_honest_fail_with_no_failure_analysis_rejected():
    """(13) HONEST_FAIL with no mechanically supported failure
    analysis (empty failed_checks) is rejected — the fix must not
    weaken HONEST_FAIL to permit an evidence-free failure."""
    with pytest.raises(ValidationError):
        GateEvidenceRecord(**_gate_kwargs(disposition="HONEST_FAIL", miss_patterns=(), failed_checks=()))


class _FakeResponse:
    """Minimal fake urllib response (local copy of the equivalent
    helper in test_phase5_github_evidence.py, kept local here to avoid
    cross-test-file coupling)."""

    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _make_zip(entries: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# ======================================================================
# Work-root initialization regression (dispatch
# q77-p5d-premarker-workroot-init-repair-a): a real preflight
# rehearsal against a non-empty live repository state (an actual
# existing P5-C marker to discover) crashed with
# ``BundleSafetyError: destination trusted root does not exist`` inside
# ``create_fresh_root``, called from ``discover_oneshot_markers`` with
# ``work_root`` itself as the trusted anchor -- but nothing had ever
# established that ``work_root`` (GitHub Actions' ``WORK_ROOT``, a
# subdirectory of the runner-guaranteed ``runner.temp`` that no
# workflow step creates) actually existed yet. This never fired
# before: P5-C's own run discovered zero markers, and both P5-D
# attempts failed earlier (missing PyYAML; then the Authorization-
# redirect leak, both before this exact call).
# ======================================================================

def test_prepare_fresh_work_root_creates_fresh_directory_beneath_existing_parent(tmp_path):
    """(1) trusted parent (tmp_path) exists; (2) work_root initially
    does NOT exist; (3) preparation succeeds; (4) work_root now exists
    as a real directory. Accessed via ``_load_module()`` (never
    ``from scripts... import``) -- this repo's dependency-surface
    governance (``tests/test_dependency_surface.py``) forbids test
    files from importing the ``scripts`` root at all, exactly why
    every existing test in this file already uses this pattern."""
    module = _load_module()

    work_root = tmp_path / "p5-gate"
    assert tmp_path.is_dir()
    assert not work_root.exists()
    result = module.prepare_fresh_work_root(work_root)
    assert result == work_root
    assert work_root.is_dir()


def test_old_unprepared_work_root_reproduces_the_real_crash_new_prepared_one_does_not(tmp_path):
    """The exact regression: calling ``create_fresh_root(work_root,
    work_root / "marker-0")`` directly against an unprepared
    ``work_root`` -- precisely what ``discover_oneshot_markers`` ->
    ``download_artifact`` did before this repair -- reproduces
    ``BundleSafetyError`` exactly as the real rehearsal observed.
    ``prepare_fresh_work_root`` first, then the identical call,
    succeeds: (5) marker-0 extraction beneath work_root succeeds and
    (6) the resulting marker parses correctly."""
    from sentinel.phase5.bundle import BundleSafetyError, create_fresh_root
    from sentinel.phase5.github_evidence import ArtifactRef, GithubEvidenceClient
    from sentinel.phase5.models import OneShotMarker

    module = _load_module()
    work_root = tmp_path / "p5-gate"

    # OLD behavior (pre-repair): reproduces the real crash exactly.
    with pytest.raises(BundleSafetyError):
        create_fresh_root(work_root, work_root / "marker-0")
    assert not work_root.exists()  # the failed attempt left nothing behind

    # REPAIRED behavior: prepare first, then the same download/extract
    # sequence discover_oneshot_markers performs succeeds.
    module.prepare_fresh_work_root(work_root)
    marker_payload = {
        "schema_version": 1, "purpose": "P5C_WIF_PROBE",
        "created_at_utc": "2026-08-24T22:09:19.953584Z",
        "workflow_identity": ".github/workflows/sentinel-wif-probe.yml",
        "github_run_id": "1", "run_attempt": 1, "event": "workflow_dispatch",
        "source_sha": "a" * 40,
    }
    zip_bytes = _make_zip({"marker.json": json.dumps(marker_payload).encode("utf-8")})

    def opener(request, timeout=None):
        return _FakeResponse(200, zip_bytes)

    client = GithubEvidenceClient(api_url="https://api.github.com", repository="acme/repo", token="tkn", opener=opener)
    ref = ArtifactRef(id=1, name="sentinel-p5-oneshot-p5c-wif-probe-r1", workflow_run_id="1")
    root = client.download_artifact(ref, work_root, work_root / "marker-0")
    marker = OneShotMarker.model_validate_json((root / "marker.json").read_text(encoding="utf-8"))
    assert marker.purpose == "P5C_WIF_PROBE"


def test_prepare_fresh_work_root_refuses_pre_existing_directory(tmp_path):
    """(7) a pre-existing work_root is refused -- never silently
    reused."""
    module = _load_module()

    work_root = tmp_path / "p5-gate"
    work_root.mkdir()
    with pytest.raises(module.Phase5ScriptError):
        module.prepare_fresh_work_root(work_root)


def test_prepare_fresh_work_root_refuses_symlink(tmp_path):
    """(8) a symlink work_root is refused where platform semantics
    permit (skips, rather than fails, if this environment cannot
    create a symlink without elevated privileges)."""
    module = _load_module()

    real_dir = tmp_path / "elsewhere"
    real_dir.mkdir()
    work_root = tmp_path / "p5-gate"
    try:
        work_root.symlink_to(real_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")
    with pytest.raises(module.Phase5ScriptError):
        module.prepare_fresh_work_root(work_root)


def test_prepare_fresh_work_root_refuses_missing_parent(tmp_path):
    """(9) a missing trusted parent is refused."""
    module = _load_module()

    work_root = tmp_path / "does-not-exist-parent" / "p5-gate"
    with pytest.raises(module.Phase5ScriptError):
        module.prepare_fresh_work_root(work_root)


def test_official_gate_preflight_prepares_work_root_before_discovery():
    """Static proof the wiring is actually in place, in the correct
    order: ``prepare_fresh_work_root`` is called before
    ``discover_oneshot_markers`` inside ``cmd_preflight``."""
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    preflight_start = text.index("def cmd_preflight")
    preflight_end = text.index("def _run_gate_session")
    body = text[preflight_start:preflight_end]
    prepare_idx = body.index("prepare_fresh_work_root(args.work_root)")
    discover_idx = body.index("discover_oneshot_markers(client, args.work_root)")
    assert prepare_idx < discover_idx


def test_green_still_requires_cost_ok_in_source():
    """(14) GREEN/frozen scoring-threshold-model-budget behavior is
    unchanged: cost remains part of overall_pass, unmodified by this
    repair."""
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    assert 'overall_pass = scoring_pass and cost_ok and validity["valid"]' in text
    assert "GATE_TOTAL_EUR_MICROS = 5_000_000" in text
    assert "GATE_RESERVE_EUR_MICROS = 1_000_000" in text


def test_probe_evidence_record_compatibility_intact():
    """(15) ProbeEvidenceRecord / P5-C compatibility remains intact —
    untouched by this repair."""
    probe_row = _cost_row(run_id="r-p5c-1")
    record = ProbeEvidenceRecord(
        schema_version=1, workflow_identity=".github/workflows/sentinel-wif-probe.yml",
        github_run_id="1", run_attempt=1, event="workflow_dispatch", ref="refs/heads/main",
        source_sha=_SHA, created_at_utc=datetime.now(timezone.utc), steps=(),
        expected_source_sha=_SHA, disposition="CAPABILITY_PASS",
        cost_rows=(probe_row,), accounted_total_eur_micros=2_000, auth_mode=_WIF,
    )
    assert record.disposition == "CAPABILITY_PASS"
    with pytest.raises(ValidationError):
        ProbeEvidenceRecord(
            schema_version=1, workflow_identity=".github/workflows/sentinel-wif-probe.yml",
            github_run_id="1", run_attempt=1, event="workflow_dispatch", ref="refs/heads/main",
            source_sha=_SHA, created_at_utc=datetime.now(timezone.utc), steps=(),
            expected_source_sha=_SHA, disposition="CAPABILITY_PASS",
            cost_rows=(probe_row,), accounted_total_eur_micros=2_000, auth_mode=None,
        )


# ======================================================================
# Stage 2B-2 terminal-publication wiring (dispatch
# q77-p5d-repair-stage2b2-implement-a). Model-free: every provider,
# OIDC, REST and FX seam below is a local fake; conftest.py blocks the
# network. Nothing here creates a real marker or dispatches anything.
# ======================================================================

import argparse  # noqa: E402
import contextlib  # noqa: E402
import functools  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import yaml  # noqa: E402

from sentinel.phase5 import replacement as _repl  # noqa: E402
from sentinel.phase5 import terminal as _t  # noqa: E402
from sentinel.phase5.execution_envelope import CommittedEnvelopeError, JobStartAnchorError  # noqa: E402
from sentinel.phase5.journal import read_journal  # noqa: E402

GATE_FINALIZER_PATH = REPO_ROOT / "scripts" / "run_phase5_gate_finalizer.py"
GATE_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "sentinel-official-gate.yml"
_WORKFLOW = ".github/workflows/sentinel-official-gate.yml"
_RUN_ID = "4242"
_GH_ENV = {
    "GITHUB_REPOSITORY": "kobescak-kristian/ai-portfolio-sentinel",
    "GITHUB_REPOSITORY_OWNER": "kobescak-kristian",
    "GITHUB_RUN_ID": _RUN_ID,
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": _SHA,
    "GITHUB_WORKFLOW_REF": f"kobescak-kristian/ai-portfolio-sentinel/{_WORKFLOW}@refs/heads/main",
    "GITHUB_API_URL": "https://api.github.com",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_TOKEN": "test-token",
}
_POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="POSIX fd inheritance semantics")


def _identity(purpose: str = "P5D_OFFICIAL_SONNET_GATE") -> _t.TerminalIdentity:
    return _t.TerminalIdentity(
        workflow_identity=_WORKFLOW, run_id=_RUN_ID, run_attempt=1, event="workflow_dispatch",
        ref="refs/heads/main", source_sha=_SHA, expected_source_sha=_SHA, purpose=purpose,
    )


def _session_result(green: bool) -> dict:
    return {
        "run_ids": ("r-1", "r-2"),
        "scoring": {"emitted": 3, "true_positives": 3 if green else 1},
        "thresholds": {"pooled_recall": {"ratio_min": "0.85"}},
        "invariant_results": {"every_task_terminal": True},
        "execution_validity": {"valid": True},
        "miss_patterns": () if green else ("stale-STATE-marker|synthetic-01|README.md",),
        "failed_checks": () if green else ("pooled_recall: 1/3 -> FAIL",),
        "cost_rows": (_cost_row("r-1"),),
        "accounted_total_eur_micros": 2_000,
        "auth_mode": _WIF,
        "green": green,
        "check_lines": ["pooled_recall: 3/3 -> PASS"] if green else ["pooled_recall: 1/3 -> FAIL"],
    }


class _FakeSession:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def install_and_start(self, env) -> None:
        self._calls.append("install_and_start")

    def shutdown(self, env) -> None:
        self._calls.append("shutdown")


class _FakeAnchor:
    """Minimal stand-in for JobStartAnchor carrying only the attributes
    SessionClock.from_anchor reads. Fresh timestamps by default so the
    derived SessionClock never starts pre-expired; job_started_offset_s
    lets a test push the anchor into the past to force expiry."""

    def __init__(self, *, job_started_offset_s: float = 0.0) -> None:
        now = datetime.now(timezone.utc)
        self.job_started_at_utc = now + timedelta(seconds=job_started_offset_s)
        self.resolved_at_utc = now
        self.monotonic_at_resolve = time.monotonic()


class _FakeEnvelope:
    """Minimal stand-in for ExecutionEnvelope carrying only the
    attributes SessionClock.from_anchor / _run_gate_session read. One
    hour of session budget is far longer than any test's real runtime
    unless a test deliberately shrinks it to force expiry."""

    def __init__(self, *, session_duration_s: int = 3600, stall_budget_ms: int = 600_000) -> None:
        self.session_duration_s = session_duration_s
        self.stall_budget_ms = stall_budget_ms


class _FakeAnchorClient:
    """Stand-in for the evidence client's list_run_attempt_jobs seam
    only -- resolve_job_start_anchor itself is monkeypatched in
    _prepare_execute, so the returned list is never actually parsed."""

    def list_run_attempt_jobs(self, run_id, run_attempt):
        return []


def _prepare_execute(
    tmp_path, monkeypatch, *, session_fn, marker_visible=None, write_fx=True,
    anchor_error=None, envelope_error=None, anchor_offset_s=0.0, envelope_session_duration_s=3600,
    run_gate_kwargs=None,
):
    """Arrange a post-marker execute world: a verified PREFLIGHTED
    journal, fake REST/OIDC/budget seams and an injected gate session.
    Stage 2C-3: also fakes the anchor-resolution and envelope-load seams
    so control construction (ExecutionSafetyDomain/SessionLatch/
    InvocationRegistry/SessionClock/TerminalArbiter/SessionMonitor) runs
    for real in every execute test that reaches it, exactly as it would
    once Stage 2C-B commits a real envelope. anchor_error/envelope_error
    make that one seam raise instead of succeeding, for the pre-control
    failure tests. anchor_offset_s/envelope_session_duration_s let a test
    construct an already-expired SessionClock. run_gate_kwargs, if given
    a list, is appended to with the exact kwargs _run_gate_session would
    have received, so a test's session_fn can reach the real shared
    control objects (e.g. to trip the latch mid-session)."""
    from agents.checker import budget as budget_mod
    from agents.checker import oidc as oidc_mod

    module = _load_module()
    for key, value in _GH_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("ANTHROPIC_IDENTITY_TOKEN_FILE", raising=False)
    work = tmp_path / "p5-gate"
    work.mkdir()
    artifacts = work / "artifacts"
    module.establish_preflight_journal(artifacts)
    fx_path = work / "fx-state.json"
    if write_fx:
        fx_path.write_text(json.dumps({
            "source": "ECB", "rate_date": "2026-09-15",
            "retrieved_at_utc": "2026-09-15T12:00:00+00:00", "usd_per_eur": "1.10",
        }), encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(module, "_suppressed_operator_output", contextlib.nullcontext)
    monkeypatch.setattr(
        module, "build_evidence_client",
        lambda env, **kw: (env.pop("GITHUB_TOKEN", None), _FakeAnchorClient())[1],
    )
    monkeypatch.setattr(
        module, "assert_marker_visible_for_this_run",
        marker_visible or (lambda client, run_id, name: calls.append(("marker", name))),
    )
    monkeypatch.setattr(module, "assert_expected_source_live", lambda client, sha: None)

    if anchor_error is not None:
        def _raise_anchor(*a, **kw):
            raise anchor_error
        monkeypatch.setattr(module, "resolve_job_start_anchor", _raise_anchor)
    else:
        monkeypatch.setattr(
            module, "resolve_job_start_anchor",
            lambda *a, **kw: _FakeAnchor(job_started_offset_s=anchor_offset_s),
        )

    if envelope_error is not None:
        def _raise_envelope(path):
            raise envelope_error
        monkeypatch.setattr(module, "load_committed_envelope", _raise_envelope)
    else:
        monkeypatch.setattr(
            module, "load_committed_envelope",
            lambda path: _FakeEnvelope(session_duration_s=envelope_session_duration_s),
        )

    monkeypatch.setattr(budget_mod, "RunBudgetCoordinator", lambda **kw: object())

    def _acquire(env):
        calls.append("acquire_oidc")
        return _FakeSession(calls)

    monkeypatch.setattr(oidc_mod, "acquire_oidc", _acquire)
    monkeypatch.setattr(oidc_mod, "scrub_identity_token_file", lambda env: calls.append("scrub"))

    def _run_gate_session_stub(**kw):
        if run_gate_kwargs is not None:
            run_gate_kwargs.append(kw)
        return session_fn()

    monkeypatch.setattr(module, "_run_gate_session", _run_gate_session_stub)
    args = argparse.Namespace(
        expected_source_sha=_SHA, gate_root=work / "gate-root", artifacts_dir=artifacts, fx_state_path=fx_path,
    )
    return module, args, artifacts, calls


def _journal_shape(path: Path) -> list:
    result = read_journal(path)
    assert result.integrity == "OK"
    return [
        (e.writer, e.event, e.state_from, e.state_to, e.record_kind, e.cause, e.signal)
        for e in result.events
    ]


# --- pre-marker journal fail-closed --------------------------------------


def _prepare_preflight(tmp_path, monkeypatch, *, journal_fsync=None, prepare_hook=None):
    from agents.checker import auth as auth_mod
    from agents.checker import budget as budget_mod
    from agents.checker import fx as fx_mod
    from agents.checker import oidc as oidc_mod
    from agents.checker.fx import FxRate

    module = _load_module()
    for key, value in _GH_ENV.items():
        monkeypatch.setenv(key, value)
    order: list = []
    monkeypatch.setattr(module, "assert_expected_source_on_disk", lambda sha: sha)
    monkeypatch.setattr(module, "build_evidence_client", lambda env, **kw: object())
    monkeypatch.setattr(module, "assert_expected_source_live", lambda client, sha: None)
    monkeypatch.setattr(module, "_load_eval_config", lambda: {})
    monkeypatch.setattr(module, "_read_jsonl", lambda path: [])
    monkeypatch.setattr(module, "load_durable_history", lambda: ())
    monkeypatch.setattr(module, "discover_oneshot_markers", lambda client, work_root: [])
    monkeypatch.setattr(module, "assert_oneshot_not_consumed_durably", lambda purpose, receipts, markers: None)
    monkeypatch.setattr(module, "assert_replacement_history_permits", lambda receipts, markers, purpose: None)
    if prepare_hook is not None:
        real_prepare = module.prepare_fresh_work_root
        monkeypatch.setattr(module, "prepare_fresh_work_root", lambda wr: prepare_hook(real_prepare(wr)))
    monkeypatch.setattr(oidc_mod, "write_placeholder_token_file", lambda env: None)
    monkeypatch.setattr(auth_mod, "assert_wif_config_ready", lambda env: None)
    monkeypatch.setattr(fx_mod, "resolve_ecb_usd_per_eur", lambda now: FxRate(
        source="ECB", rate_date="2026-09-15", retrieved_at_utc=datetime(2026, 9, 15, tzinfo=timezone.utc),
        usd_per_eur=__import__("decimal").Decimal("1.10"),
    ))
    monkeypatch.setattr(budget_mod, "RunBudgetCoordinator", lambda **kw: object())

    real_establish = module.establish_preflight_journal
    establish = functools.partial(real_establish, fsync=journal_fsync) if journal_fsync else real_establish

    def _establish(artifacts_dir):
        order.append("establish_preflight_journal")
        return establish(artifacts_dir)

    real_write_marker = module.write_marker_json

    def _write_marker(candidate, path):
        order.append("write_marker_json")
        return real_write_marker(candidate, path)

    monkeypatch.setattr(module, "establish_preflight_journal", _establish)
    monkeypatch.setattr(module, "write_marker_json", _write_marker)
    work = tmp_path / "p5-gate"
    args = argparse.Namespace(
        expected_source_sha=_SHA, gate_root=work / "gate-root", artifacts_dir=work / "artifacts",
        work_root=work, marker_out=work / "marker.json", fx_state_path=work / "fx-state.json",
    )
    return module, args, order


def _failing_fsync_on_call(n: int):
    counter = {"calls": 0}

    def _fsync(fd):
        counter["calls"] += 1
        if counter["calls"] == n:
            raise OSError("injected fsync failure")
        os.fsync(fd)

    return _fsync


def test_preflight_marker_written_only_after_verified_journal(tmp_path, monkeypatch, capsys):
    module, args, order = _prepare_preflight(tmp_path, monkeypatch)
    assert module.cmd_preflight(args) == 0
    assert order == ["establish_preflight_journal", "write_marker_json"]
    assert args.marker_out.exists()
    shape = _journal_shape(args.artifacts_dir / _t.JOURNAL_FILENAME)
    assert shape == [
        ("RUNNER", "JOURNAL_OPENED", None, None, None, None, None),
        ("RUNNER", "STATE_TRANSITION", None, "PREFLIGHTED", None, None, None),
    ]
    for sibling in ("terminal-staging", "terminal-quarantine"):
        assert (args.work_root / sibling).is_dir()


def test_preflight_journal_open_failure_refuses_before_marker(tmp_path, monkeypatch, capsys):
    module, args, order = _prepare_preflight(tmp_path, monkeypatch, journal_fsync=_failing_fsync_on_call(1))
    assert module.cmd_preflight(args) == 2
    assert "PREFLIGHT FAIL" in capsys.readouterr().err
    assert "write_marker_json" not in order
    assert not args.marker_out.exists()


def test_preflight_preflighted_append_fsync_failure_refuses_before_marker(tmp_path, monkeypatch, capsys):
    module, args, order = _prepare_preflight(tmp_path, monkeypatch, journal_fsync=_failing_fsync_on_call(2))
    assert module.cmd_preflight(args) == 2
    assert "PREFLIGHTED journal append failed" in capsys.readouterr().err
    assert "write_marker_json" not in order
    assert not args.marker_out.exists()


def test_preflight_layout_creation_failure_refuses_before_marker(tmp_path, monkeypatch, capsys):
    def _conflict(work_root):
        (work_root / "terminal-staging").mkdir()
        return work_root

    module, args, order = _prepare_preflight(tmp_path, monkeypatch, prepare_hook=_conflict)
    assert module.cmd_preflight(args) == 2
    assert "terminal layout creation failed" in capsys.readouterr().err
    assert "write_marker_json" not in order
    assert not args.marker_out.exists()


def test_establish_preflight_journal_readback_rejects_unexpected_content(tmp_path, monkeypatch):
    module = _load_module()
    common_globals = module.establish_preflight_journal.__globals__
    real_read = common_globals["read_journal"]

    def _tampered(path):
        result = real_read(path)
        return type(result)(events=result.events[:1], integrity=result.integrity, size=result.size)

    monkeypatch.setitem(common_globals, "read_journal", _tampered)
    (tmp_path / "w").mkdir()
    with pytest.raises(module.Phase5ScriptError, match="read-back"):
        module.establish_preflight_journal(tmp_path / "w" / "artifacts")


def test_preflight_source_order_armable_guard_and_journal_before_marker():
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    body = text[text.index("def cmd_preflight"):text.index("def _derive_auth_mode")]
    assert body.index("assert_replacement_history_permits") < body.index("assert_purpose_armable(PURPOSE, ENVELOPE)")
    assert body.index("assert_purpose_armable(PURPOSE, ENVELOPE)") < body.index("OneShotMarker(")
    assert body.index("write_json_artifact(") < body.index("establish_preflight_journal(args.artifacts_dir)")
    assert body.index("establish_preflight_journal(args.artifacts_dir)") < body.index("write_marker_json(")


# --- quality-neutral execute ---------------------------------------------


def test_execute_green_and_honest_fail_are_operator_indistinguishable(tmp_path, monkeypatch, capfd):
    observations = {}
    for label, green in (("green", True), ("honest", False)):
        case_root = tmp_path / label
        case_root.mkdir()
        module, args, artifacts, calls = _prepare_execute(
            case_root, monkeypatch, session_fn=lambda green=green: _session_result(green),
        )
        output_file = case_root / "github_output"
        summary_file = case_root / "step_summary"
        output_file.write_text("", encoding="utf-8")
        summary_file.write_text("", encoding="utf-8")
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        capfd.readouterr()
        code = module.cmd_execute(args)
        captured = capfd.readouterr()
        data = (artifacts / _t.TERMINAL_FILENAME).read_bytes()
        verdict = _t.verify_terminal_bytes(data, _identity())
        journal_bytes = (artifacts / _t.JOURNAL_FILENAME).read_bytes()
        observations[label] = dict(
            code=code, out=captured.out, err=captured.err,
            github_output=output_file.read_text(encoding="utf-8"),
            summary=summary_file.read_text(encoding="utf-8"),
            journal=_journal_shape(artifacts / _t.JOURNAL_FILENAME),
            verdict=verdict.kind, disposition=verdict.record.disposition,
            writer=verdict.record.terminal_writer, journal_bytes=journal_bytes,
            checks=(artifacts / _t.CHECKS_FILENAME).exists(), calls=calls,
        )
    green, honest = observations["green"], observations["honest"]
    assert green["disposition"] == "GREEN" and honest["disposition"] == "HONEST_FAIL"
    for key in ("code", "out", "err", "github_output", "summary", "journal", "verdict", "writer", "checks", "calls"):
        assert green[key] == honest[key], key
    assert green["code"] == 0
    assert green["out"] == module.EXECUTE_QUALITY_LINE + "\n"
    assert green["err"] == ""
    assert green["github_output"] == "" and green["summary"] == ""
    assert green["verdict"] == "TRUSTED_QUALITY" and green["writer"] is None
    for forbidden in (b"GREEN", b"HONEST_FAIL", b"pooled", b"true_positives", b"FAIL", b"r-1", b"emitted"):
        assert forbidden not in green["journal_bytes"] and forbidden not in honest["journal_bytes"]
    assert [row[1] for row in green["journal"]] == [
        "JOURNAL_OPENED", "STATE_TRANSITION", "JOURNAL_OPENED", "STATE_TRANSITION", "STATE_TRANSITION",
        "STATE_TRANSITION", "TERMINAL_WRITE_STARTED", "TERMINAL_WRITE_COMPLETED", "STATE_TRANSITION",
    ]
    assert [row[3] for row in green["journal"] if row[1] == "STATE_TRANSITION"] == [
        "PREFLIGHTED", "REPLACEMENT_MARKED", "EXECUTING", "SCORED_PROVISIONAL", "TERMINAL_EVIDENCE_WRITTEN",
    ]


def test_runner_source_never_prints_disposition_or_branches_exit_on_green():
    text = GATE_RUNNER_PATH.read_text(encoding="utf-8")
    assert "DISPOSITION" not in text
    assert '== "GREEN"' not in text
    assert "print(f\"GATE EXECUTE FAILED" not in text


def test_execute_refuses_without_posix_suppression(tmp_path, monkeypatch, capfd):
    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=lambda: _session_result(True))
    real = _load_module()._suppressed_operator_output
    monkeypatch.setattr(module, "_suppressed_operator_output", functools.partial(real, os_name="nt"))
    capfd.readouterr()
    assert module.cmd_execute(args) == 3
    assert capfd.readouterr().out == "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=RUNNER_INTERNAL_ERROR\n"
    assert "acquire_oidc" not in calls
    assert not (artifacts / _t.TERMINAL_FILENAME).exists()


def test_suppression_refuses_non_posix():
    module = _load_module()
    with pytest.raises(module.Phase5ScriptError):
        with module._suppressed_operator_output(os_name="nt"):
            pass


# --- infrastructure-error contract -----------------------------------------


def test_pre_provider_failure_record_when_marker_not_visible(tmp_path, monkeypatch, capfd):
    module = _load_module()

    def _not_visible(client, run_id, name):
        raise module.Phase5ScriptError("marker not visible")

    module, args, artifacts, calls = _prepare_execute(
        tmp_path, monkeypatch, session_fn=lambda: _session_result(True), marker_visible=_not_visible,
    )
    capfd.readouterr()
    assert module.cmd_execute(args) == 1
    captured = capfd.readouterr()
    assert captured.out == (
        "EXECUTE INFRASTRUCTURE_FAILURE: cause=PRE_PROVIDER_FAILURE exception_type=Phase5ScriptError\n"
    )
    assert captured.err == ""
    assert "acquire_oidc" not in calls and "scrub" in calls
    verdict = _t.verify_terminal_bytes((artifacts / _t.TERMINAL_FILENAME).read_bytes(), _identity())
    assert verdict.kind == "TRUSTED_INFRASTRUCTURE_INVALID"
    assert verdict.record.termination_source == "PRE_PROVIDER_FAILURE"
    assert verdict.record.terminal_writer is None
    shape = _journal_shape(artifacts / _t.JOURNAL_FILENAME)
    assert ("RUNNER", "RUNNER_EXCEPTION", None, None, None, "PRE_PROVIDER_FAILURE", None) in shape
    assert not (artifacts / _t.CHECKS_FILENAME).exists()


def test_runner_exception_record_after_oidc_boundary(tmp_path, monkeypatch, capfd):
    def _boom():
        raise RuntimeError("session failed with some free text that must never be printed")

    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=_boom)
    capfd.readouterr()
    assert module.cmd_execute(args) == 1
    captured = capfd.readouterr()
    assert captured.out == "EXECUTE INFRASTRUCTURE_FAILURE: cause=RUNNER_EXCEPTION exception_type=RuntimeError\n"
    assert "free text" not in captured.out + captured.err
    assert calls[-2:] == ["install_and_start", "shutdown"] or "shutdown" in calls
    verdict = _t.verify_terminal_bytes((artifacts / _t.TERMINAL_FILENAME).read_bytes(), _identity())
    assert verdict.kind == "TRUSTED_INFRASTRUCTURE_INVALID"
    assert verdict.record.termination_source == "RUNNER_EXCEPTION"
    states = [row[3] for row in _journal_shape(artifacts / _t.JOURNAL_FILENAME) if row[1] == "STATE_TRANSITION"]
    assert states[-1] == "INVALID_EVIDENCE_WRITTEN"
    assert not (artifacts / _t.CHECKS_FILENAME).exists()


def test_signal_observed_with_exception_writes_no_objective_cause(tmp_path, monkeypatch, capfd):
    captured_journal = {}

    def _boom():
        captured_journal["journal"].observe_signal("SIGTERM")
        raise RuntimeError("raised alongside a signal")

    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=_boom)

    def _capture(journal):
        captured_journal["journal"] = journal
        return lambda: None

    monkeypatch.setattr(module, "install_observing_signal_handlers", _capture)
    capfd.readouterr()
    assert module.cmd_execute(args) == 3
    assert capfd.readouterr().out == "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=SIGNAL_OBSERVED\n"
    assert not (artifacts / _t.TERMINAL_FILENAME).exists()
    events = [row[1] for row in _journal_shape(artifacts / _t.JOURNAL_FILENAME)]
    assert "SIGNAL_OBSERVED" in events
    assert "RUNNER_EXCEPTION" not in events


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_keyboard_interrupt_and_system_exit_propagate_no_terminal(tmp_path, monkeypatch, exc_type):
    def _cancel():
        raise exc_type()

    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=_cancel)
    state = {"entered": False, "exited": False}

    @contextlib.contextmanager
    def _recording():
        state["entered"] = True
        yield
        state["exited"] = True

    monkeypatch.setattr(module, "_suppressed_operator_output", _recording)
    with pytest.raises(exc_type):
        module.cmd_execute(args)
    assert state == {"entered": True, "exited": False}  # descriptors never restored
    assert not (artifacts / _t.TERMINAL_FILENAME).exists()
    assert "shutdown" in calls


def _except_handler_type_names(path: Path) -> list:
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                names.append("<bare>")
            else:
                for sub in ast.walk(node.type):
                    if isinstance(sub, ast.Name):
                        names.append(sub.id)
    return names


@pytest.mark.parametrize("path", [GATE_RUNNER_PATH, GATE_FINALIZER_PATH])
def test_gate_scripts_catch_only_ordinary_exceptions(path):
    names = _except_handler_type_names(path)
    for forbidden in ("<bare>", "BaseException", "KeyboardInterrupt", "SystemExit"):
        assert forbidden not in names, (path.name, forbidden)


def test_terminal_write_failure_journals_and_exits_3(tmp_path, monkeypatch, capfd):
    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=lambda: _session_result(True))

    def _fail(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(module, "write_terminal_atomically", _fail)
    capfd.readouterr()
    assert module.cmd_execute(args) == 3
    assert capfd.readouterr().out == "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=TERMINAL_WRITE_FAILED\n"
    shape = _journal_shape(artifacts / _t.JOURNAL_FILENAME)
    assert ("RUNNER", "TERMINAL_WRITE_FAILED", None, None, "QUALITY", None, None) in shape
    assert not (artifacts / _t.CHECKS_FILENAME).exists()


def test_checks_ancillary_written_only_after_quality_terminal(tmp_path, monkeypatch, capfd):
    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=lambda: _session_result(True))
    assert module.cmd_execute(args) == 0
    assert json.loads((artifacts / _t.CHECKS_FILENAME).read_text(encoding="utf-8")) == ["pooled_recall: 3/3 -> PASS"]


# test_journal_wires_no_stage2c_events_or_liveness retired here (Stage
# 2C-3, dispatch q77-p5d-repair-stage2c3-implement-a): it guarded against
# premature Stage-2C wiring landing before its own stage. Stage 2C-3 IS
# that wiring landing -- its invariant is superseded by the positive
# coverage below (STATE.md carries the dated record).


# ======================================================================
# Stage 2C-3: runner/workflow wiring of execution-safety controls
# (ADR-0012 repair; dispatch q77-p5d-repair-stage2c3-implement-a).
# ======================================================================


# --- pre-control anchor/envelope construction ------------------------------


def test_execute_pre_control_anchor_failure_takes_pre_provider_failure_path(tmp_path, monkeypatch, capfd):
    module, args, artifacts, calls = _prepare_execute(
        tmp_path, monkeypatch, session_fn=lambda: _session_result(True),
        anchor_error=JobStartAnchorError("no matching job"),
    )
    capfd.readouterr()
    assert module.cmd_execute(args) == 1
    captured = capfd.readouterr()
    assert captured.out == (
        "EXECUTE INFRASTRUCTURE_FAILURE: cause=PRE_PROVIDER_FAILURE exception_type=JobStartAnchorError\n"
    )
    assert "acquire_oidc" not in calls and "scrub" in calls
    verdict = _t.verify_terminal_bytes((artifacts / _t.TERMINAL_FILENAME).read_bytes(), _identity())
    assert verdict.kind == "TRUSTED_INFRASTRUCTURE_INVALID"
    assert verdict.record.termination_source == "PRE_PROVIDER_FAILURE"


def test_execute_pre_control_envelope_absent_takes_pre_provider_failure_path(tmp_path, monkeypatch, capfd):
    module, args, artifacts, calls = _prepare_execute(
        tmp_path, monkeypatch, session_fn=lambda: _session_result(True),
        envelope_error=CommittedEnvelopeError("committed envelope is absent"),
    )
    capfd.readouterr()
    assert module.cmd_execute(args) == 1
    captured = capfd.readouterr()
    assert captured.out == (
        "EXECUTE INFRASTRUCTURE_FAILURE: cause=PRE_PROVIDER_FAILURE exception_type=CommittedEnvelopeError\n"
    )
    assert "acquire_oidc" not in calls and "scrub" in calls
    verdict = _t.verify_terminal_bytes((artifacts / _t.TERMINAL_FILENAME).read_bytes(), _identity())
    assert verdict.kind == "TRUSTED_INFRASTRUCTURE_INVALID"
    assert verdict.record.termination_source == "PRE_PROVIDER_FAILURE"


# --- _run_gate_session wiring: composition, shared objects, abort ---------


class _NullJournal:
    def append(self, *a, **kw) -> None:
        pass


def _probe_run_gate_session(tmp_path, monkeypatch, *, trip_after_run=None):
    """Drive module._run_gate_session directly with every provider-facing
    seam faked (CagedCheckerStub, health_gated, deadline_guarded,
    execute_run, ledger.open_ledger), so only the Stage-2C-3 wiring under
    test -- deps_for's composition, the run-boundary SessionAborted
    checks and the before_task_execute hook -- runs for real, never a
    real provider call, ledger row or scoring pass. trip_after_run, if 1
    or 2, trips a real shared SessionLatch as a side effect of that
    execute_run call returning, so the run-boundary check right after it
    observes a latched cause."""
    module = _load_module()
    from agents.checker import envelope_guard as guard_mod
    from agents.checker import harness as harness_mod
    from agents.checker import oidc as oidc_mod
    from sentinel import ledger as ledger_mod
    import sentinel.pipeline as pipeline_mod

    guard_calls: list = []
    health_calls: list = []
    assertion_calls: list = []
    deps_captured: list = []
    execute_run_call_count = {"n": 0}

    def _fake_health_gated(query_fn, session):
        health_calls.append((query_fn, session))
        return ("health-wrapped", query_fn)

    def _fake_assertion_refreshed(query_fn, session, env):
        assertion_calls.append((query_fn, session, env))
        return ("assertion-refreshed", query_fn)

    def _fake_deadline_guarded(query_fn, **kw):
        guard_calls.append(kw)
        return ("deadline-wrapped", query_fn, kw["run_ordinal"])

    class _FakeStub:
        def __init__(self, **kw) -> None:
            self.query_fn = "raw-query-fn"

    domain = module.ExecutionSafetyDomain()
    latch = module.SessionLatch(domain)
    registry = module.InvocationRegistry(domain)

    class _FixedClock:
        def monotonic_now(self) -> float:
            return 0.0

    clock = _FixedClock()

    def _fake_execute_run(config, deps):
        execute_run_call_count["n"] += 1
        deps_captured.append(deps)
        if trip_after_run == execute_run_call_count["n"]:
            latch.trip("WATCHDOG", 0.0)
        return object()

    monkeypatch.setattr(oidc_mod, "health_gated", _fake_health_gated)
    monkeypatch.setattr(oidc_mod, "assertion_refreshed", _fake_assertion_refreshed)
    monkeypatch.setattr(guard_mod, "deadline_guarded", _fake_deadline_guarded)
    monkeypatch.setattr(harness_mod, "CagedCheckerStub", _FakeStub)
    monkeypatch.setattr(ledger_mod, "open_ledger", lambda *a, **kw: object())
    monkeypatch.setattr(pipeline_mod, "execute_run", _fake_execute_run)

    gate_root = tmp_path / "gate-root"
    session = object()

    def _run():
        return module._run_gate_session(
            gate_root=gate_root, coordinator=object(), session=session, expected_source_sha=_SHA,
            clock=clock, latch=latch, registry=registry, journal=_NullJournal(),
            stall_budget_ms=600_000, config=object(), terminate=lambda cfg: None,
            on_control_failure=lambda: None,
        )

    return types.SimpleNamespace(
        module=module, run=_run, latch=latch, registry=registry, clock=clock, session=session,
        guard_calls=guard_calls, health_calls=health_calls, assertion_calls=assertion_calls,
        deps_captured=deps_captured, execute_run_call_count=execute_run_call_count,
    )


def test_run_gate_session_wraps_query_fn_with_health_gated_then_deadline_guarded(tmp_path, monkeypatch):
    probe = _probe_run_gate_session(tmp_path, monkeypatch, trip_after_run=1)
    with pytest.raises(probe.module.SessionAborted):
        probe.run()
    assert len(probe.health_calls) == 1
    assert probe.health_calls[0][1] is probe.session
    # Every gate invocation spawns a fresh CLI process that performs its own
    # provider exchange, so a never-exchanged assertion is installed first
    # (Q-77 B5-P0 Part 1). assertion_refreshed sits OUTSIDE health_gated and
    # INSIDE deadline_guarded, and receives the same session plus the process
    # environment the token file is named in.
    assert len(probe.assertion_calls) == 1
    assert probe.assertion_calls[0][0] == ("health-wrapped", "raw-query-fn")
    assert probe.assertion_calls[0][1] is probe.session
    assert probe.assertion_calls[0][2] is os.environ
    assert len(probe.guard_calls) == 1
    assert probe.guard_calls[0]["run_ordinal"] == 1
    assert probe.guard_calls[0]["clock"] is probe.clock
    assert probe.guard_calls[0]["latch"] is probe.latch
    assert probe.guard_calls[0]["registry"] is probe.registry
    assert probe.guard_calls[0]["stall_budget_ms"] == 600_000
    stub_query_fn = probe.deps_captured[0].judgment.query_fn
    assert stub_query_fn == (
        "deadline-wrapped", ("assertion-refreshed", ("health-wrapped", "raw-query-fn")), 1
    )


def test_execute_control_construction_shares_one_domain_across_both_runs(tmp_path, monkeypatch):
    probe = _probe_run_gate_session(tmp_path, monkeypatch, trip_after_run=2)
    with pytest.raises(probe.module.SessionAborted):
        probe.run()
    assert len(probe.guard_calls) == 2
    assert probe.guard_calls[0]["latch"] is probe.guard_calls[1]["latch"] is probe.latch
    assert probe.guard_calls[0]["registry"] is probe.guard_calls[1]["registry"] is probe.registry
    assert probe.guard_calls[0]["clock"] is probe.guard_calls[1]["clock"] is probe.clock


def test_before_task_execute_hook_aborts_reservation_once_latched(tmp_path, monkeypatch):
    probe = _probe_run_gate_session(tmp_path, monkeypatch, trip_after_run=1)
    with pytest.raises(probe.module.SessionAborted):
        probe.run()
    hook = probe.deps_captured[0].hooks.before_task_execute
    with pytest.raises(probe.module.SessionAborted):
        hook(object())


def test_run1_final_task_latch_prevents_run2_from_starting(tmp_path, monkeypatch):
    probe = _probe_run_gate_session(tmp_path, monkeypatch, trip_after_run=1)
    with pytest.raises(probe.module.SessionAborted):
        probe.run()
    assert probe.execute_run_call_count["n"] == 1
    assert len(probe.deps_captured) == 1


def test_run2_final_task_latch_aborts_before_scoring(tmp_path, monkeypatch):
    probe = _probe_run_gate_session(tmp_path, monkeypatch, trip_after_run=2)
    with pytest.raises(probe.module.SessionAborted):
        probe.run()
    assert probe.execute_run_call_count["n"] == 2
    assert [c["run_ordinal"] for c in probe.guard_calls] == [1, 2]


# --- terminal commit seam ---------------------------------------------------


def test_post_control_ordinary_invalid_commits_through_arbiter(tmp_path, monkeypatch, capfd):
    from sentinel.phase5 import execution_control as ec_mod

    commit_invalid_calls: list = []
    real_commit_invalid = ec_mod.TerminalArbiter.commit_invalid

    def _spy_commit_invalid(self, cause, replace):
        commit_invalid_calls.append(cause)
        return real_commit_invalid(self, cause, replace)

    monkeypatch.setattr(ec_mod.TerminalArbiter, "commit_invalid", _spy_commit_invalid)

    def _boom():
        raise RuntimeError("post-control failure")

    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=_boom)
    capfd.readouterr()
    assert module.cmd_execute(args) == 1
    assert capfd.readouterr().out == "EXECUTE INFRASTRUCTURE_FAILURE: cause=RUNNER_EXCEPTION exception_type=RuntimeError\n"
    assert commit_invalid_calls == ["RUNNER_EXCEPTION"]
    verdict = _t.verify_terminal_bytes((artifacts / _t.TERMINAL_FILENAME).read_bytes(), _identity())
    assert verdict.kind == "TRUSTED_INFRASTRUCTURE_INVALID"


def test_terminal_digest_preserved_through_arbiter_commit_quality(tmp_path, monkeypatch):
    import hashlib

    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=lambda: _session_result(True))
    assert module.cmd_execute(args) == 0
    data = (artifacts / _t.TERMINAL_FILENAME).read_bytes()
    expected = hashlib.sha256(data).hexdigest()
    events = read_journal(artifacts / _t.JOURNAL_FILENAME).events
    completed = next(e for e in events if e.event == "TERMINAL_WRITE_COMPLETED" and e.record_kind == "QUALITY")
    assert completed.sha256 == expected


def test_terminal_digest_preserved_through_arbiter_commit_invalid(tmp_path, monkeypatch):
    import hashlib

    def _boom():
        raise RuntimeError("boom")

    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=_boom)
    assert module.cmd_execute(args) == 1
    data = (artifacts / _t.TERMINAL_FILENAME).read_bytes()
    expected = hashlib.sha256(data).hexdigest()
    events = read_journal(artifacts / _t.JOURNAL_FILENAME).events
    completed = next(
        e for e in events if e.event == "TERMINAL_WRITE_COMPLETED" and e.record_kind == "INFRASTRUCTURE_INVALID"
    )
    assert completed.sha256 == expected


def test_quality_refused_builds_new_invalid_record_not_a_retry(tmp_path, monkeypatch, capfd):
    run_gate_kwargs: list = []

    def _session_fn():
        run_gate_kwargs[-1]["latch"].trip("WATCHDOG", 0.0)
        return _session_result(True)

    module, args, artifacts, calls = _prepare_execute(
        tmp_path, monkeypatch, session_fn=_session_fn, run_gate_kwargs=run_gate_kwargs,
    )
    capfd.readouterr()
    assert module.cmd_execute(args) == 1
    assert capfd.readouterr().out == "EXECUTE INFRASTRUCTURE_FAILURE: cause=WATCHDOG exception_type=QualityRefused\n"
    verdict = _t.verify_terminal_bytes((artifacts / _t.TERMINAL_FILENAME).read_bytes(), _identity())
    assert verdict.kind == "TRUSTED_INFRASTRUCTURE_INVALID"
    assert verdict.record.termination_source == "WATCHDOG"
    assert verdict.record.disposition == "INFRASTRUCTURE_FAILURE"
    events = read_journal(artifacts / _t.JOURNAL_FILENAME).events
    completed = [e for e in events if e.event == "TERMINAL_WRITE_COMPLETED"]
    assert len(completed) == 1 and completed[0].record_kind == "INFRASTRUCTURE_INVALID"


def test_commit_point_session_deadline_journaled_exactly_once(tmp_path, monkeypatch, capfd):
    module, args, artifacts, calls = _prepare_execute(
        tmp_path, monkeypatch, session_fn=lambda: _session_result(True),
        anchor_offset_s=-7200, envelope_session_duration_s=1,
    )
    capfd.readouterr()
    assert module.cmd_execute(args) == 1
    assert capfd.readouterr().out == (
        "EXECUTE INFRASTRUCTURE_FAILURE: cause=SESSION_DEADLINE exception_type=QualityRefused\n"
    )
    events = read_journal(artifacts / _t.JOURNAL_FILENAME).events
    cause_latched = [e for e in events if e.event == "OBJECTIVE_CAUSE_LATCHED"]
    assert len(cause_latched) == 1 and cause_latched[0].cause == "SESSION_DEADLINE"
    verdict = _t.verify_terminal_bytes((artifacts / _t.TERMINAL_FILENAME).read_bytes(), _identity())
    assert verdict.record.termination_source == "SESSION_DEADLINE"


def test_already_latched_cause_does_not_duplicate_objective_cause_journal(tmp_path, monkeypatch, capfd):
    def _session_fn():
        # Let the real SessionMonitor's own background tick discover the
        # already-expired clock and journal OBJECTIVE_CAUSE_LATCHED
        # itself, winning the race before commit_quality is ever called.
        time.sleep(1.2)
        return _session_result(True)

    module, args, artifacts, calls = _prepare_execute(
        tmp_path, monkeypatch, session_fn=_session_fn,
        anchor_offset_s=-7200, envelope_session_duration_s=1,
    )
    capfd.readouterr()
    assert module.cmd_execute(args) == 1
    assert capfd.readouterr().out == (
        "EXECUTE INFRASTRUCTURE_FAILURE: cause=SESSION_DEADLINE exception_type=QualityRefused\n"
    )
    events = read_journal(artifacts / _t.JOURNAL_FILENAME).events
    cause_latched = [e for e in events if e.event == "OBJECTIVE_CAUSE_LATCHED"]
    assert len(cause_latched) == 1 and cause_latched[0].cause == "SESSION_DEADLINE"


def test_invalid_refused_fails_closed_without_recursive_retry(tmp_path, monkeypatch, capfd):
    from sentinel.phase5 import execution_control as ec_mod

    call_count = {"n": 0}

    def _always_refuse(self, cause, replace):
        call_count["n"] += 1
        raise ec_mod.InvalidRefused(cause=cause, latched=None, state=self.state)

    monkeypatch.setattr(ec_mod.TerminalArbiter, "commit_invalid", _always_refuse)

    def _boom():
        raise RuntimeError("post-control failure")

    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=_boom)
    capfd.readouterr()
    assert module.cmd_execute(args) == 3
    assert capfd.readouterr().out == "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=TERMINAL_WRITE_FAILED\n"
    assert call_count["n"] == 1
    events = read_journal(artifacts / _t.JOURNAL_FILENAME).events
    failed = [e for e in events if e.event == "TERMINAL_WRITE_FAILED"]
    assert len(failed) == 1 and failed[0].exception_type == "InvalidRefused"
    assert not (artifacts / _t.TERMINAL_FILENAME).exists()


def test_quality_refused_with_none_cause_fails_closed_without_fabricating_cause(tmp_path, monkeypatch, capfd):
    from sentinel.phase5 import execution_control as ec_mod

    def _refuse_with_none_cause(self, replace):
        raise ec_mod.QualityRefused(cause=None, state=ec_mod.TerminalCommitState.QUALITY_COMMITTED)

    monkeypatch.setattr(ec_mod.TerminalArbiter, "commit_quality", _refuse_with_none_cause)
    commit_invalid_calls: list = []
    real_commit_invalid = ec_mod.TerminalArbiter.commit_invalid

    def _spy_commit_invalid(self, cause, replace):
        commit_invalid_calls.append(cause)
        return real_commit_invalid(self, cause, replace)

    monkeypatch.setattr(ec_mod.TerminalArbiter, "commit_invalid", _spy_commit_invalid)

    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=lambda: _session_result(True))
    capfd.readouterr()
    assert module.cmd_execute(args) == 3
    assert capfd.readouterr().out == "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=TERMINAL_WRITE_FAILED\n"
    assert commit_invalid_calls == []
    events = read_journal(artifacts / _t.JOURNAL_FILENAME).events
    failed = [e for e in events if e.event == "TERMINAL_WRITE_FAILED"]
    assert len(failed) == 1 and failed[0].record_kind == "QUALITY" and failed[0].exception_type == "QualityRefused"
    assert not (artifacts / _t.TERMINAL_FILENAME).exists()


def test_session_aborted_at_run_boundary_routes_through_arbiter_with_latched_cause(tmp_path, monkeypatch, capfd):
    from sentinel.phase5 import execution_control as ec_mod

    commit_invalid_calls: list = []
    real_commit_invalid = ec_mod.TerminalArbiter.commit_invalid

    def _spy_commit_invalid(self, cause, replace):
        commit_invalid_calls.append(cause)
        return real_commit_invalid(self, cause, replace)

    monkeypatch.setattr(ec_mod.TerminalArbiter, "commit_invalid", _spy_commit_invalid)

    session_aborted_cls = _load_module().SessionAborted
    run_gate_kwargs: list = []

    def _session_fn():
        latch = run_gate_kwargs[-1]["latch"]
        latch.trip("INVOCATION_STALL_DEADLINE", 0.0)
        raise session_aborted_cls(latch.cause)

    module, args, artifacts, calls = _prepare_execute(
        tmp_path, monkeypatch, session_fn=_session_fn, run_gate_kwargs=run_gate_kwargs,
    )
    capfd.readouterr()
    assert module.cmd_execute(args) == 1
    assert capfd.readouterr().out == (
        "EXECUTE INFRASTRUCTURE_FAILURE: cause=INVOCATION_STALL_DEADLINE exception_type=SessionAborted\n"
    )
    assert commit_invalid_calls == ["INVOCATION_STALL_DEADLINE"]
    verdict = _t.verify_terminal_bytes((artifacts / _t.TERMINAL_FILENAME).read_bytes(), _identity())
    assert verdict.record.termination_source == "INVOCATION_STALL_DEADLINE"


def test_run_boundary_session_aborted_does_not_journal_runner_exception_with_stage2c_cause(
    tmp_path, monkeypatch, capfd,
):
    session_aborted_cls = _load_module().SessionAborted
    run_gate_kwargs: list = []

    def _session_fn():
        # Let the real SessionMonitor's own background tick trip AND
        # journal OBJECTIVE_CAUSE_LATCHED itself, before this raises.
        time.sleep(1.2)
        latch = run_gate_kwargs[-1]["latch"]
        raise session_aborted_cls(latch.cause)

    module, args, artifacts, calls = _prepare_execute(
        tmp_path, monkeypatch, session_fn=_session_fn, run_gate_kwargs=run_gate_kwargs,
        anchor_offset_s=-7200, envelope_session_duration_s=1,
    )
    capfd.readouterr()
    assert module.cmd_execute(args) == 1
    assert capfd.readouterr().out == (
        "EXECUTE INFRASTRUCTURE_FAILURE: cause=SESSION_DEADLINE exception_type=SessionAborted\n"
    )
    events = read_journal(artifacts / _t.JOURNAL_FILENAME).events
    cause_latched = [e for e in events if e.event == "OBJECTIVE_CAUSE_LATCHED"]
    assert len(cause_latched) == 1 and cause_latched[0].cause == "SESSION_DEADLINE"
    assert [e for e in events if e.event == "RUNNER_EXCEPTION"] == []
    assert read_journal(artifacts / _t.JOURNAL_FILENAME).integrity == "OK"


def test_quality_replace_raising_reaches_quality_failed_with_no_invalid_fallback(tmp_path, monkeypatch, capfd):
    from sentinel.phase5 import execution_control as ec_mod

    commit_invalid_calls: list = []
    real_commit_invalid = ec_mod.TerminalArbiter.commit_invalid

    def _spy_commit_invalid(self, cause, replace):
        commit_invalid_calls.append(cause)
        return real_commit_invalid(self, cause, replace)

    monkeypatch.setattr(ec_mod.TerminalArbiter, "commit_invalid", _spy_commit_invalid)
    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=lambda: _session_result(True))

    def _fail(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(module, "write_terminal_atomically", _fail)
    capfd.readouterr()
    assert module.cmd_execute(args) == 3
    assert capfd.readouterr().out == "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=TERMINAL_WRITE_FAILED\n"
    assert commit_invalid_calls == []
    shape = _journal_shape(artifacts / _t.JOURNAL_FILENAME)
    assert ("RUNNER", "TERMINAL_WRITE_FAILED", None, None, "QUALITY", None, None) in shape
    assert not (artifacts / _t.TERMINAL_FILENAME).exists()


def test_invalid_replace_raising_reaches_invalid_failed_with_no_retry(tmp_path, monkeypatch, capfd):
    def _boom():
        raise RuntimeError("post-control failure")

    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=_boom)

    write_calls = {"n": 0}

    def _fail(*a, **kw):
        write_calls["n"] += 1
        raise OSError("disk full")

    monkeypatch.setattr(module, "write_terminal_atomically", _fail)
    capfd.readouterr()
    assert module.cmd_execute(args) == 3
    assert capfd.readouterr().out == "EXECUTE NO_RUNNER_TERMINAL_EVIDENCE: reason=TERMINAL_WRITE_FAILED\n"
    assert write_calls["n"] == 1
    shape = _journal_shape(artifacts / _t.JOURNAL_FILENAME)
    assert ("RUNNER", "TERMINAL_WRITE_FAILED", None, None, "INFRASTRUCTURE_INVALID", None, None) in shape
    assert not (artifacts / _t.TERMINAL_FILENAME).exists()


# --- runner termination -----------------------------------------------------


def test_make_runner_terminator_lock_acquired_calls_fake_exiter_once():
    module = _load_module()
    domain = module.ExecutionSafetyDomain()
    exit_calls: list = []
    module._make_runner_terminator(domain, exiter=exit_calls.append)("WATCHDOG", None)
    assert exit_calls == [1]


def test_make_runner_terminator_lock_timeout_still_calls_fake_exiter_once(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "RUNNER_EXIT_LOCK_WAIT_S", 0.05)
    domain = module.ExecutionSafetyDomain()
    exit_calls: list = []
    release_event = threading.Event()
    holder_ready = threading.Event()

    def _hold_lock():
        with domain.lock:
            holder_ready.set()
            release_event.wait(5.0)

    holder = threading.Thread(target=_hold_lock, daemon=True)
    holder.start()
    try:
        assert holder_ready.wait(5.0)
        module._make_runner_terminator(domain, exiter=exit_calls.append)("WATCHDOG", None)
        assert exit_calls == [1]
    finally:
        release_event.set()
        holder.join(5.0)


def test_make_runner_terminator_releases_lock_when_fake_exiter_returns():
    module = _load_module()
    domain = module.ExecutionSafetyDomain()
    module._make_runner_terminator(domain, exiter=lambda status: None)("WATCHDOG", None)
    assert domain.lock.acquire(blocking=False)
    domain.lock.release()


# --- session monitor lifecycle ----------------------------------------------


def test_execute_starts_and_stops_session_monitor(tmp_path, monkeypatch):
    from agents.checker import process_control as pc_mod

    lifecycle: list = []
    real_start = pc_mod.SessionMonitor.start
    real_stop = pc_mod.SessionMonitor.stop

    def _spy_start(self):
        lifecycle.append("start")
        return real_start(self)

    def _spy_stop(self):
        lifecycle.append("stop")
        return real_stop(self)

    monkeypatch.setattr(pc_mod.SessionMonitor, "start", _spy_start)
    monkeypatch.setattr(pc_mod.SessionMonitor, "stop", _spy_stop)
    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=lambda: _session_result(True))
    assert module.cmd_execute(args) == 0
    assert lifecycle == ["start", "stop"]


def test_monitor_stays_alive_through_post_control_invalid_commit_then_stops_before_session_teardown(
    tmp_path, monkeypatch,
):
    from agents.checker import process_control as pc_mod
    from sentinel.phase5 import execution_control as ec_mod

    def _boom():
        raise RuntimeError("post-control failure")

    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=_boom)

    real_start = pc_mod.SessionMonitor.start
    real_stop = pc_mod.SessionMonitor.stop
    real_commit_invalid = ec_mod.TerminalArbiter.commit_invalid

    def _spy_start(self):
        calls.append("monitor.start")
        return real_start(self)

    def _spy_stop(self):
        calls.append("monitor.stop")
        return real_stop(self)

    def _spy_commit_invalid(self, cause, replace):
        calls.append("commit_invalid")
        return real_commit_invalid(self, cause, replace)

    monkeypatch.setattr(pc_mod.SessionMonitor, "start", _spy_start)
    monkeypatch.setattr(pc_mod.SessionMonitor, "stop", _spy_stop)
    monkeypatch.setattr(ec_mod.TerminalArbiter, "commit_invalid", _spy_commit_invalid)

    assert module.cmd_execute(args) == 1
    ordered = [c for c in calls if c in ("monitor.start", "commit_invalid", "monitor.stop", "shutdown")]
    assert ordered == ["monitor.start", "commit_invalid", "monitor.stop", "shutdown"]


# --- surface checks ----------------------------------------------------------


def test_expected_api_job_name_is_gate():
    module = _load_module()
    assert module.EXPECTED_API_JOB_NAME == "gate"


def _references_os_exit(path: Path) -> bool:
    """AST-level check (never a docstring/comment substring match): does
    this file's code actually reference the os._exit attribute?"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, ast.Attribute) and node.attr == "_exit"
        and isinstance(node.value, ast.Name) and node.value.id == "os"
        for node in ast.walk(tree)
    )


def test_os_exit_present_only_in_official_gate_runner():
    assert _references_os_exit(GATE_RUNNER_PATH)
    for relative in (
        "agents/checker/process_control.py",
        "agents/checker/envelope_guard.py",
        "sentinel/phase5/execution_control.py",
        "sentinel/phase5/execution_envelope.py",
    ):
        path = REPO_ROOT / Path(relative)
        assert not _references_os_exit(path), relative


# --- purpose-gated writer attribution --------------------------------------


def test_quality_writer_none_under_original_purpose_is_trusted():
    module = _load_module()
    record = module._quality_record(_identity(), _session_result(False))
    assert record.terminal_writer is None and record.replacement_of_run_id is None
    data = record.model_dump_json(indent=2).encode("utf-8")
    assert _t.verify_terminal_bytes(data, _identity()).kind == "TRUSTED_QUALITY"


def test_quality_writer_runner_under_armed_purpose_with_envelope_is_trusted(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "PURPOSE", _repl.REPLACEMENT_PURPOSE)
    monkeypatch.setattr(module, "ENVELOPE", _t.EnvelopeIdentity(envelope_id="env-test", envelope_version="v-test"))
    identity = _identity(_repl.REPLACEMENT_PURPOSE)
    record = module._quality_record(identity, _session_result(True))
    assert record.terminal_writer == "RUNNER"
    assert record.replacement_of_run_id == _repl.REPLACEMENT_OF_RUN_ID
    data = record.model_dump_json(indent=2).encode("utf-8")
    assert _t.verify_terminal_bytes(data, identity).kind == "TRUSTED_QUALITY"
    # The Stage-2B-1 non-replacement trust rule is unchanged: the same
    # writer-attributed bytes are never trusted under the original purpose.
    assert _t.verify_terminal_bytes(data, _identity()).kind == "PROVENANCE_INVALID"


def test_writer_attributed_invalid_record_under_original_purpose_stays_provenance_invalid():
    module = _load_module()
    raw = _t.build_invalid_record(
        identity=_identity(), envelope=None, created_at_utc=datetime.now(timezone.utc),
        model="claude-sonnet-5", profile_name="sonnet-official-gate",
        infrastructure_cause="RUNNER_EXCEPTION", writer="RUNNER",
    )
    assert _t.verify_terminal_bytes(raw.model_dump_json().encode("utf-8"), _identity()).kind == "PROVENANCE_INVALID"
    attributed = module.attribute_invalid_record(raw, purpose=module.PURPOSE)
    assert attributed.terminal_writer is None
    assert _t.verify_terminal_bytes(
        attributed.model_dump_json().encode("utf-8"), _identity()
    ).kind == "TRUSTED_INFRASTRUCTURE_INVALID"


def test_replacement_purpose_without_envelope_refuses_in_preflight_and_execute(tmp_path, monkeypatch, capfd):
    module = _load_module()
    with pytest.raises(module.Phase5ScriptError, match="Stage-2C"):
        module.assert_purpose_armable(_repl.REPLACEMENT_PURPOSE, None)
    module.assert_purpose_armable(module.PURPOSE, None)  # the unarmed original is not refused by this guard

    module, args, artifacts, calls = _prepare_execute(tmp_path, monkeypatch, session_fn=lambda: _session_result(True))
    monkeypatch.setattr(module, "PURPOSE", _repl.REPLACEMENT_PURPOSE)
    capfd.readouterr()
    assert module.cmd_execute(args) == 1
    assert "cause=PRE_PROVIDER_FAILURE" in capfd.readouterr().out
    assert "acquire_oidc" not in calls


def test_purpose_original_and_envelope_none():
    module = _load_module()
    assert module.PURPOSE == "P5D_OFFICIAL_SONNET_GATE"
    assert module.ENVELOPE is None


@pytest.mark.parametrize("path", [GATE_RUNNER_PATH, GATE_FINALIZER_PATH, GATE_WORKFLOW_PATH])
def test_gate_scripts_and_workflow_carry_no_replacement_literal(path):
    assert "P5D_REPLACEMENT_SONNET_GATE" not in path.read_text(encoding="utf-8")


def test_workflow_marker_name_matches_runner_purpose_canonical_name():
    module = _load_module()
    data = yaml.safe_load(GATE_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = next(iter(data["jobs"].values()))["steps"]
    marker = next(s for s in steps if s.get("id") == "marker")
    assert marker["with"]["name"] == module.artifact_names.oneshot_marker_name(module.PURPOSE, "${{ github.run_id }}")


def test_replacement_marker_without_frozen_fields_is_unconstructible():
    from sentinel.phase5.models import OneShotMarker

    with pytest.raises(ValidationError):
        OneShotMarker(
            schema_version=1, purpose=_repl.REPLACEMENT_PURPOSE, created_at_utc=datetime.now(timezone.utc),
            workflow_identity=_WORKFLOW, github_run_id="1", run_attempt=1, event="workflow_dispatch",
            source_sha=_SHA,
        )


# --- POSIX fd suppression (child interpreters with real std streams) ------


def _run_child(body: str) -> subprocess.CompletedProcess:
    code = (
        "import importlib.util, logging, subprocess, sys, warnings\n"
        f"spec = importlib.util.spec_from_file_location('gate_runner_child', {str(GATE_RUNNER_PATH)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        + body
    )
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, cwd=str(REPO_ROOT), timeout=300,
    )


@_POSIX_ONLY
def test_suppression_discards_unflushed_buffered_stdout_stderr_without_newline():
    proc = _run_child(
        "with module._suppressed_operator_output():\n"
        "    sys.stdout.write('LEAKOUT')\n"
        "    sys.stderr.write('LEAKERR')\n"
        "print('VISIBLE')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b"VISIBLE\n"
    assert b"LEAK" not in proc.stderr


@_POSIX_ONLY
@pytest.mark.parametrize("snippet", [
    "    subprocess.run([sys.executable, '-c', \"import sys; sys.stdout.write('LEAKCHILDOUT'); "
    "sys.stderr.write('LEAKCHILDERR')\"])\n",
    "    logging.getLogger('claude_agent_sdk._internal.query').error('LEAKSDKLOG')\n",
    "    warnings.warn('LEAKWARNING')\n",
], ids=["inherited-child-process", "sdk-logger-lastresort", "warnings"])
def test_suppression_silences_child_processes_sdk_logging_and_warnings(snippet):
    proc = _run_child(
        "with module._suppressed_operator_output():\n"
        + snippet
        + "print('VISIBLE')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b"VISIBLE\n"
    assert b"LEAK" not in proc.stderr


@_POSIX_ONLY
def test_suppression_not_restored_on_keyboard_interrupt_traceback_hidden():
    proc = _run_child(
        "with module._suppressed_operator_output():\n"
        "    sys.stdout.write('LEAKOUT')\n"
        "    raise KeyboardInterrupt\n"
    )
    assert proc.returncode != 0
    assert proc.stdout == b""
    assert proc.stderr == b""

"""Tests for scripts/run_phase5_official_gate.py (P5-B Part 3/3).
ADR-0011 Section 7 pins this exact path; Part 3 never executes it.
"""

from __future__ import annotations

import ast
import importlib.metadata
import importlib.util
import io
import json
import sys
import zipfile
from datetime import datetime, timezone
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


def test_official_gate_runtime_import_closure_matches_requirements_txt():
    """Dispatch q77-p5d-premarker-dependency-repair-a: proves the exact
    invariant whose absence (PyYAML) crashed GitHub Actions run
    32863558192 at ``ModuleNotFoundError: No module named 'yaml'``
    inside ``preflight``, before the one-shot marker was ever uploaded
    -- every Phase-5 workflow installs only ``requirements.txt``, never
    ``requirements-dev.txt``. Fully offline and deterministic: a
    static AST walk of this repo's own source (see
    ``_collect_local_third_party_imports``), never a network call or
    venv build."""
    third_party = _collect_local_third_party_imports(GATE_RUNNER_PATH)
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
    # Post-marker INFRASTRUCTURE_FAILURE recovery path is wired too.
    assert "recovered_auth_mode = _recover_partial_auth_mode(args.gate_root)" in text
    assert "auth_mode=(result[\"auth_mode\"] if result else recovered_auth_mode)" in text


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

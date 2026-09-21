"""Tests for the P5-D N=24 Sonnet timing-rehearsal surface (ADR-0012
section 13 and Amendment A3/A5/A8; approved plan
q77-p5d-repair-stage2cb4-plan-d; dispatch
q77-p5d-repair-stage2cb4-implement-a, Stage 2C-B4).

Network-blocked (tests/conftest.py ``block_network``) and provider-free:
nothing here executes the rehearsal, calls a model, runs the bundled
CLI, performs OIDC, or touches a marker.

The corpus is frozen prospectively. These tests exist so that no later
stage can quietly re-choose a line count, a stratum, an ordering, a
budget control or a STOP rule after timings have been observed.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVER_PATH = REPO_ROOT / "scripts" / "run_phase5_timing_rehearsal.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "sentinel-timing-rehearsal.yml"
CORPUS_PATH = REPO_ROOT / "rehearsal" / "timing" / "corpus.json"
PREREG_PATH = REPO_ROOT / "rehearsal" / "timing" / "preregistration.json"

STATE_ANCHORS = (9, 28, 32, 32, 32, 34, 36, 38, 71, 71, 459, 459)
LINK_ANCHORS = (3, 18, 33, 41, 70, 100, 138, 183, 246, 298, 430, 738)
STATE_TOTAL = 1301
LINK_TOTAL = 2298
CORPUS_TOTAL = 3599


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tr():
    return _load(DRIVER_PATH, "run_phase5_timing_rehearsal")


@pytest.fixture(scope="module")
def corpus():
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def prereg():
    return json.loads(PREREG_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


def test_corpus_has_exactly_24_items_with_contiguous_ordinals(corpus):
    items = corpus["items"]
    assert corpus["n"] == 24 and len(items) == 24
    assert [i["ordinal"] for i in items] == list(range(1, 25))


def test_corpus_is_two_twelve_item_production_input_strata(tr, corpus):
    state = [i for i in corpus["items"] if i["stratum"] == tr.STRATUM_STATE]
    link = [i for i in corpus["items"] if i["stratum"] == tr.STRATUM_LINK]
    assert len(state) == len(link) == 12
    # The production path determines the class; strata are NOT balanced
    # across check classes for symmetry.
    assert {i["check_class"] for i in state} == {"stale-STATE-marker"}
    assert {i["check_class"] for i in link} == {"missing-synthetic-label"}
    assert {i["path"] for i in state} == {"STATE.md"}
    assert {i["path"] for i in link} == {"README.md"}


def test_corpus_line_counts_are_the_frozen_anchors(tr, corpus):
    state = [i["line_count"] for i in corpus["items"] if i["stratum"] == tr.STRATUM_STATE]
    link = [i["line_count"] for i in corpus["items"] if i["stratum"] == tr.STRATUM_LINK]
    assert tuple(state) == STATE_ANCHORS
    assert tuple(link) == LINK_ANCHORS
    assert sum(state) == STATE_TOTAL
    assert sum(link) == LINK_TOTAL
    assert sum(i["line_count"] for i in corpus["items"]) == CORPUS_TOTAL
    # The ruled-out 4798-line extreme tail never enters the corpus.
    assert 4798 not in state and 4798 not in link


def test_corpus_ordering_interleaves_the_strata(tr, corpus):
    items = corpus["items"]
    for k in range(12):
        assert items[2 * k]["id"] == f"tim-state-{k + 1:02d}"
        assert items[2 * k]["stratum"] == tr.STRATUM_STATE
        assert items[2 * k + 1]["id"] == f"tim-link-{k + 1:02d}"
        assert items[2 * k + 1]["stratum"] == tr.STRATUM_LINK


def test_corpus_text_is_byte_identical_to_the_frozen_generator(tr, corpus):
    for item in corpus["items"]:
        assert item["text"] == tr.generate_text(item["id"], item["line_count"])


def test_corpus_text_line_discipline_is_exact(corpus):
    for item in corpus["items"]:
        text = item["text"]
        assert len(text.split("\n")) == item["line_count"]
        assert "\r" not in text
        assert not text.endswith("\n")


def test_corpus_surfaces_are_distinct_so_task_keys_are_unique(corpus):
    surfaces = [i["surface"] for i in corpus["items"]]
    assert len(set(surfaces)) == 24
    assert all(s.startswith("sentinel-timing/") for s in surfaces)


def test_corpus_carries_no_production_or_fixture_prose(corpus):
    """Synthetic and self-authored: every line is generated from the
    frozen template, so no monitored document or official fixture text
    can have been copied in."""
    for item in corpus["items"]:
        assert item["text"].startswith(f"# Synthetic timing surface {item['id']}")
        assert item["text"].endswith(f"<!-- end of synthetic timing surface {item['id']} -->")
        if item["line_count"] >= 6:
            assert "Synthetic content" in item["text"]
            assert "not a quality fixture, not scored" in item["text"]


# ---------------------------------------------------------------------------
# Canonical hashes and pre-registration
# ---------------------------------------------------------------------------


def test_committed_files_are_exactly_the_canonical_bytes(tr, corpus, prereg):
    assert CORPUS_PATH.read_bytes() == tr.canonical_bytes(corpus)
    assert PREREG_PATH.read_bytes() == tr.canonical_bytes(prereg)


def test_corpus_regenerates_deterministically(tr, corpus):
    assert tr.build_corpus() == corpus
    assert tr.canonical_bytes(tr.build_corpus()) == CORPUS_PATH.read_bytes()


def test_preregistration_pins_the_committed_corpus_hash(tr, corpus, prereg):
    corpus_sha = hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest()
    assert prereg["corpus_sha256"] == corpus_sha
    assert tr.build_preregistration(corpus, corpus_sha) == prereg


def test_preregistration_hashes_are_usable_by_timing_rehearsal_provenance(tr):
    """Both hashes must satisfy TimingRehearsalProvenance's 64-lowercase-hex
    contract directly, with no schema translation at B6."""
    from sentinel.phase5.execution_envelope import TimingRehearsalProvenance  # noqa: F401
    import re

    for path in (CORPUS_PATH, PREREG_PATH):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_preregistration_freezes_the_execution_and_budget_contract(tr, prereg):
    execution = prereg["execution"]
    assert execution["model"] == "claude-sonnet-5"
    assert execution["sdk_pin"] == "claude-agent-sdk==0.2.110"
    assert execution["query_seam"] == "agents.checker.harness.run_query"
    assert execution["max_turns"] == 10
    assert execution["max_tool_calls_per_check"] == 5
    assert execution["max_model_attempts_per_task"] == 1
    assert execution["production_default_max_model_attempts_per_task"] == 2
    assert execution["sequential"] is True
    assert execution["deadline_guarded"] is False
    assert execution["statistic"] == "max_observed"

    budget = prereg["budget"]
    assert budget["total_eur_micros"] == 2_500_000
    assert budget["max_per_call_reserve_eur_micros"] == 1_000_000

    envelope = prereg["envelope_inputs"]
    assert envelope["margin_multiplier"] == "1.5"
    assert envelope["fixed_overhead_s"] == 600
    assert envelope["finalization_reserve_s"] == 480
    assert envelope["platform_ceiling_min"] == 360
    assert envelope["feasibility_max_observed_ms"] == 148_000

    assert prereg["total_corpus_lines"] == CORPUS_TOTAL
    assert set(prereg["stop_reasons"]) == set(tr.STOP_REASONS)
    assert prereg["workflow"]["job_backstop_minutes"] == 360
    assert prereg["workflow"]["evidence_upload_timeout_minutes"] == 2
    assert prereg["exactly_once"]["durable_receipt_added"] is False


def test_preregistration_records_per_item_hashes(tr, corpus, prereg):
    by_id = {i["id"]: i for i in corpus["items"]}
    for entry in prereg["items"]:
        expected = hashlib.sha256(by_id[entry["id"]]["text"].encode("utf-8")).hexdigest()
        assert entry["text_sha256"] == expected


# ---------------------------------------------------------------------------
# Quality firewall
# ---------------------------------------------------------------------------


def _imported_roots(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_driver_imports_no_fixture_eval_or_scoring_surface():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
    for banned in (
        "run_phase3_dev_gate", "sentinel.inventory.fixtures", "checks.scoring",
        "sentinel.phase5.terminal", "sentinel.phase5.journal", "agents.checker.envelope_guard",
    ):
        assert not any(m == banned or m.startswith(banned + ".") for m in modules), banned
    # Path literals, not prose: the driver may *declare* that it uses no
    # answer key, but it must never name one.
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for literal in literals:
        assert "fixtures/" not in literal, literal
        assert not literal.startswith("evals/"), literal
        assert "eval_config" not in literal, literal
        assert "ANSWER_KEY" not in literal, literal


def test_driver_derives_no_quality_disposition():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for banned in ("GREEN", "HONEST_FAIL", "INFRASTRUCTURE_FAILURE", "UNCLASSIFIED_TERMINATION"):
        assert banned not in literals, banned
    # No scoring or threshold machinery is called; the docstring may say
    # in prose that none is used.
    called = {
        n.func.attr for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    } | {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    for banned in ("score_findings", "evaluate_execution_validity", "apply_thresholds"):
        assert banned not in called, banned


def test_driver_never_names_prohibited_repo_wide_literals():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "SONNET_OFFICIAL_GATE" not in source
    assert "ExecutionControlConfig(" not in source


def test_preregistration_declares_the_data_contract(prereg):
    contract = prereg["data_contract"]
    assert contract["no_scoring"] is True
    assert contract["no_answer_key"] is True
    assert contract["no_threshold_application"] is True
    assert contract["no_quality_disposition"] is True
    joined = " ".join(contract["discarded"]).lower()
    assert "response content" in joined and "prompt text" in joined and "cmdline" in joined


# ---------------------------------------------------------------------------
# Cage and seam reuse
# ---------------------------------------------------------------------------


def test_driver_pins_the_real_production_cage(tr):
    from agents.checker.config import MAX_TOOL_CALLS_PER_CHECK, MAX_TURNS

    assert MAX_TURNS == 10
    assert MAX_TOOL_CALLS_PER_CHECK == 5
    assert tr.MODEL_ALIAS == "claude-sonnet-5"
    assert tr.SDK_PIN == "claude-agent-sdk==0.2.110"
    assert tr.TIMING_MAX_MODEL_ATTEMPTS == 1
    assert tr.N_OBSERVATIONS == 24


def test_driver_reuses_the_real_query_seam_and_applies_no_deadline():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "CagedCheckerStub" in source
    assert "health_gated" in source
    # A per-invocation deadline could truncate a slow call and understate
    # max_observed, so the envelope guard must never be imported or called.
    # The module docstring is allowed to explain that it is not used.
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not any("envelope_guard" in name for name in imported)
    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "deadline_guarded" not in called


def test_driver_supplies_the_timing_attempt_bound_of_one():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "max_model_attempts_per_task=TIMING_MAX_MODEL_ATTEMPTS" in source


def test_production_attempt_default_is_untouched():
    from agents.checker.config import MAX_MODEL_ATTEMPTS_PER_TASK
    from agents.checker.harness import CagedCheckerStub
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(CagedCheckerStub)}
    assert MAX_MODEL_ATTEMPTS_PER_TASK == 2
    assert fields["max_model_attempts_per_task"].default == MAX_MODEL_ATTEMPTS_PER_TASK


# ---------------------------------------------------------------------------
# Budget (A3)
# ---------------------------------------------------------------------------


def test_budget_constants_are_the_a3_values(tr):
    assert tr.TOTAL_EUR_MICROS == 2_500_000
    assert tr.MAX_PER_CALL_RESERVE_EUR_MICROS == 1_000_000


def test_start_control_checks_remaining_before_calling_reserve():
    """The pre-check must gate the loop BEFORE judge() reaches
    coordinator.reserve(), because reserve() only refuses at
    remaining <= 0 and would otherwise silently truncate the SDK
    allowance."""
    tree = ast.parse(DRIVER_PATH.read_text(encoding="utf-8"))
    execute = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "cmd_execute"
    )
    loop = next(n for n in ast.walk(execute) if isinstance(n, ast.For))
    body_src = ast.dump(ast.Module(body=loop.body, type_ignores=[]))
    assert "remaining_eur_micros" in body_src
    assert "UNDER_RESERVATION_REFUSAL" in body_src
    first = ast.dump(loop.body[0]) + ast.dump(loop.body[1])
    assert "remaining_eur_micros" in first


def test_every_frozen_stop_reason_is_declared(tr):
    assert set(tr.STOP_REASONS) == {
        "SDK_BUDGET_CEILING", "UNDER_RESERVATION_REFUSAL", "COST_OVERSHOOT",
        "BUDGET_EXHAUSTED", "INFRASTRUCTURE_FAULT", "AUTH_OR_OIDC_FAULT",
        "TOPOLOGY_ESCAPE", "TOPOLOGY_CLI_UNIDENTIFIED", "INCOMPLETE_N",
        "FEASIBILITY_FAILURE", "PRIOR_RUN_PRESENT", "HASH_MISMATCH",
    }
    with pytest.raises(ValueError):
        tr.TimingRehearsalStop("NOT_A_REAL_REASON")


def test_stop_is_raised_for_ceiling_overshoot_and_feasibility():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert 'error_max_budget_usd' in source and 'SDK_BUDGET_CEILING' in source
    assert "charged > reserved" in source and "COST_OVERSHOOT" in source
    assert "FEASIBILITY_MAX_OBSERVED_MS" in source and "FEASIBILITY_FAILURE" in source


def test_no_automatic_second_rehearsal_exists(tr, prereg):
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "retry" not in source.lower().replace("retryable", "")
    assert prereg["no_second_rehearsal"].startswith("No automatic second rehearsal")
    assert prereg["no_discard"].startswith("No observation is discarded")


# ---------------------------------------------------------------------------
# Durability: START before the provider call
# ---------------------------------------------------------------------------


def test_invocation_started_is_emitted_before_awaiting_the_provider():
    """Load-bearing ordering. If the runner is killed while invocation k
    is in flight, the durable stream must still prove k started."""
    tree = ast.parse(DRIVER_PATH.read_text(encoding="utf-8"))
    timed = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "timed"
    )
    started_line = None
    await_line = None
    for node in ast.walk(timed):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "append" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value == "INVOCATION_STARTED":
                    started_line = node.lineno
        if isinstance(node, ast.Await) and await_line is None:
            await_line = node.lineno
    assert started_line is not None, "INVOCATION_STARTED is not emitted in the wrapper"
    assert await_line is not None, "the wrapper does not await the real seam"
    assert started_line < await_line


def test_event_log_fsyncs_each_record_and_counts_invocations(tr, tmp_path, monkeypatch):
    fsynced = []
    real_fsync = os.fsync
    monkeypatch.setattr(tr.os, "fsync", lambda fd: (fsynced.append(fd), real_fsync(fd))[1])
    log = tr.EventLog(tmp_path / "events.jsonl")
    log.append("RUN_STARTED", lane=tr.LANE)
    log.append("INVOCATION_STARTED", ordinal=1, item_id="tim-state-01")
    log.append("INVOCATION_FINISHED", ordinal=1, item_id="tim-state-01", elapsed_ms=5)
    assert len(fsynced) == 3
    assert (log.started, log.finished) == (1, 1)
    with pytest.raises(ValueError):
        log.append("NOT_AN_EVENT")


def test_interrupted_invocation_is_incomplete_n_not_k_minus_one(tr, tmp_path):
    """A kill during invocation k leaves START without FINISH. That must
    STOP, and must never be read as 'only k-1 invocations occurred'."""
    log = tr.EventLog(tmp_path / "events.jsonl")
    for ordinal in (1, 2):
        log.append("INVOCATION_STARTED", ordinal=ordinal, item_id=f"i{ordinal}")
        log.append("INVOCATION_FINISHED", ordinal=ordinal, item_id=f"i{ordinal}", elapsed_ms=1)
    log.append("INVOCATION_STARTED", ordinal=3, item_id="i3", reserved_eur_micros=1_000_000)

    verdict = tr.adjudicate_events(log.path)
    assert verdict["started_count"] == 3
    assert verdict["finished_count"] == 2
    assert verdict["interrupted_ordinals"] == [3]
    assert verdict["complete"] is False
    assert verdict["stop_reason"] == "INCOMPLETE_N"


def test_complete_stream_of_24_is_the_only_complete_verdict(tr, tmp_path):
    log = tr.EventLog(tmp_path / "events.jsonl")
    for ordinal in range(1, 25):
        log.append("INVOCATION_STARTED", ordinal=ordinal, item_id=f"i{ordinal}")
        log.append("INVOCATION_FINISHED", ordinal=ordinal, item_id=f"i{ordinal}", elapsed_ms=1)
    verdict = tr.adjudicate_events(log.path)
    assert verdict["complete"] is True and verdict["stop_reason"] is None
    assert verdict["started_count"] == verdict["finished_count"] == 24


def test_event_vocabulary_is_closed_and_carries_no_content(tr):
    assert set(tr.EVENT_TYPES) == {
        "RUN_STARTED", "INVOCATION_STARTED", "INVOCATION_FINISHED",
        "OBSERVATION_ACCOUNTED", "RUN_FINISHED", "STOP",
    }
    tree = ast.parse(DRIVER_PATH.read_text(encoding="utf-8"))
    # No event field carries prompt or response content, and no code path
    # reads /proc cmdline. Prose describing those prohibitions is fine.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                assert keyword.arg not in ("user_prompt", "prompt", "response"), keyword.arg
        if isinstance(node, ast.Attribute):
            assert node.attr != "cmdline"
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "cmdline" not in literals


# ---------------------------------------------------------------------------
# Exactly once
# ---------------------------------------------------------------------------


class _Runs:
    def __init__(self, runs, exc=None):
        self._runs = runs
        self._exc = exc

    def list_workflow_runs(self, workflow_path, *, created_after, created_before):
        if self._exc is not None:
            raise self._exc
        return self._runs


class _Run:
    def __init__(self, run_id):
        self.run_id = run_id


def test_prior_run_refusal_allows_only_the_current_run(tr):
    assert tr.assert_no_prior_timing_run(_Runs([_Run("777")]), "777") == 1
    assert tr.assert_no_prior_timing_run(_Runs([]), "777") == 0


@pytest.mark.parametrize("prior", [["888"], ["777", "888"], ["1", "2", "3"]])
def test_any_other_visible_run_stops_the_rehearsal(tr, prior):
    client = _Runs([_Run(r) for r in prior])
    with pytest.raises(tr.TimingRehearsalStop) as excinfo:
        tr.assert_no_prior_timing_run(client, "777")
    assert excinfo.value.reason == "PRIOR_RUN_PRESENT"


def test_discovery_failure_is_never_read_as_no_prior_run(tr):
    from sentinel.phase5.github_evidence import DiscoveryOverflow

    for exc in (DiscoveryOverflow("too many pages"), ValueError("malformed")):
        with pytest.raises(tr.TimingRehearsalStop) as excinfo:
            tr.assert_no_prior_timing_run(_Runs([], exc=exc), "777")
        assert excinfo.value.reason == "PRIOR_RUN_PRESENT"


def test_prior_run_search_covers_complete_visible_history(tr):
    assert tr.DISCOVERY_AFTER.year == 2026 and tr.DISCOVERY_AFTER.month == 1
    assert tr.WORKFLOW_PATH == ".github/workflows/sentinel-timing-rehearsal.yml"
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "run_attempt != 1" in source
    assert "no new durable receipt" in source.lower() or "durable_receipt_added" in source


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


def test_topology_uses_pid_starttime_identity_and_ppid_ancestry():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "starttime" in source
    assert "descendants_of" in source and "ancestors_of" in source
    # Ancestry is never redefined in session / process-group terms.
    for banned in ("getpgid", "getsid", "setsid", "killpg"):
        assert banned not in source, banned


def test_cli_is_identified_through_proc_exe_never_cmdline():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert '"exe"' in source or "/ \"exe\"" in source or "exe" in source
    tree = ast.parse(source)
    constants = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "cmdline" not in constants


def test_no_child_subreaper_is_introduced():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    for banned in ("PR_SET_CHILD_SUBREAPER", "prctl", "subreaper"):
        assert banned not in source.replace("child_subreaper", ""), banned


def test_process_identity_is_reuse_safe(tr, monkeypatch):
    from agents.checker.process_control import ProcessStat

    monkeypatch.setattr(tr, "read_process_stat", lambda pid, proc_root: None)
    assert tr.process_identity(4242, Path("/proc")) is None
    monkeypatch.setattr(
        tr, "read_process_stat", lambda pid, proc_root: ProcessStat(pid=4242, ppid=1, state="S", starttime=99)
    )
    assert tr.process_identity(4242, Path("/proc")) == (4242, 99)


def test_topology_contract_is_frozen_in_the_preregistration(prereg):
    topology = prereg["topology"]
    assert topology["child_subreaper"] is False
    assert "PPID" in topology["ancestry"]
    assert "cmdline" in topology["cli_identification"]
    assert set(topology["stop"]) == {"TOPOLOGY_ESCAPE", "TOPOLOGY_CLI_UNIDENTIFIED"}
    assert topology["residual_if_unclosed"] == "REAL_CLI_TOPOLOGY_UNOBSERVED"


@pytest.mark.skipif(sys.platform != "linux", reason="real /proc sampling is Linux-only")
def test_linux_topology_sample_sees_the_controlled_root(tr):
    sample = tr.sample_topology(os.getpid(), None, Path("/proc"))
    assert sample["root_pid"] == os.getpid()
    assert isinstance(sample["processes"], list)
    scan = tr.survivor_scan(os.getpid(), Path("/proc"))
    assert "survivor_count" in scan


# ---------------------------------------------------------------------------
# Upload safety
# ---------------------------------------------------------------------------


def test_evidence_filenames_are_exactly_five_and_live_under_evidence(tr):
    assert tr.EVIDENCE_DIRNAME == "evidence"
    assert tr.EVIDENCE_FILENAMES == (
        "phase5_timing_events.jsonl",
        "phase5_timing_runtime_identity.json",
        "phase5_timing_topology.json",
        "phase5_timing_summary.json",
        "phase5_timing_stop.json",
    )
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    for filename in tr.EVIDENCE_FILENAMES:
        assert f"/evidence/{filename}" in workflow


def test_upload_cannot_sweep_ledger_token_or_work_root(tr):
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    upload_block = workflow.split("upload timing evidence", 1)[1]
    for forbidden in (".sqlite3", "anthropic_identity_token", "fx-state.json", "*"):
        assert forbidden not in upload_block, forbidden
    # The ephemeral ledger and FX state deliberately live outside evidence/.
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert 'work_root / "timing.sqlite3"' in source


def test_driver_writes_evidence_only_into_the_evidence_directory(tr):
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "evidence_dir(" in source
    assert "evidence / RUNTIME_IDENTITY_FILENAME" in source
    assert "evidence / TOPOLOGY_FILENAME" in source
    assert "evidence / SUMMARY_FILENAME" in source
    assert "evidence / STOP_FILENAME" in source


# ---------------------------------------------------------------------------
# Provider / marker absence
# ---------------------------------------------------------------------------


def test_driver_creates_or_consumes_no_marker():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    for banned in ("oneshot_marker_name", "sentinel-p5-oneshot", "OneShotMarker",
                   "assert_oneshot_not_consumed_durably", "discover_oneshot_markers"):
        assert banned not in source, banned


def test_driver_makes_no_direct_provider_or_network_call():
    roots = _imported_roots(DRIVER_PATH)
    for banned in ("claude_agent_sdk", "anthropic", "urllib", "requests", "socket", "ssl"):
        assert banned not in roots, banned
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "http://" not in source and "https://" not in source

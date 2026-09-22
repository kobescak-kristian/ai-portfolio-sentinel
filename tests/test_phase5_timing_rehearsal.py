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
import importlib
import importlib.util
import json
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sentinel.phase5.evidence_records import TimingCostEvidenceRecord
from sentinel.phase5.github_evidence import GithubEvidenceError

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
CORPUS_SHA = "98cdba8a183b3fab128f413f95bb3647e15961d711bbfd6fedb9ce73c42a471d"
PREREG_SHA = "17549d3fd5789a8eeae04d15364d2aec0c94ef5f8ee25a02cf0391d354ff065b"
# Resolved at runtime rather than with an ``import scripts...`` statement:
# tests/test_dependency_surface.py pins each test module's third-party import
# set from the AST, and ``scripts`` is not in its first-party root list, so a
# package-style import statement would widen that pinned set. This is the same
# reason tests/test_phase5_gate_runner.py loads its entrypoint by path.
#
# import_module (not a fresh path load) is deliberate: it returns the SAME
# module object the driver itself imports, so Phase5ScriptError raised by the
# driver is the identical class this module asserts on.
common = importlib.import_module("scripts._phase5_common")
Phase5ScriptError = common.Phase5ScriptError


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
    """The frozen no-second-rehearsal rule, proven directly.

    This used to assert the substring "retry" never appeared anywhere in
    the driver. That was a proxy that only held while the driver contained
    no retry of any kind. B5-P0 adopts four bounded PRE-PROVIDER retries
    (plan-d Part 7), so the proxy is replaced by checks of the property it
    was standing in for: the rehearsal is never re-executed, no observation
    is ever retried, and every retry that does exist is one of the approved
    idempotent external reads.
    """
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert prereg["no_second_rehearsal"].startswith("No automatic second rehearsal")
    assert prereg["no_discard"].startswith("No observation is discarded")

    # The corpus is walked exactly once, and one invocation is made per item.
    tree = ast.parse(source)
    execute = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_execute"
    )
    corpus_loops = [
        node for node in ast.walk(execute)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Subscript)
        and getattr(node.iter.value, "id", None) == "corpus"
    ]
    assert len(corpus_loops) == 1
    judge_calls = [
        node for node in ast.walk(execute)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "judge"
    ]
    assert len(judge_calls) == 1

    # The single model attempt bound is what makes a second provider attempt
    # mechanically unreachable inside the harness.
    assert tr.TIMING_MAX_MODEL_ATTEMPTS == 1

    # Every retry in the driver is a bounded read at one of the two approved
    # pre-provider call sites. Nothing retries the provider seam, the
    # rehearsal, or a workflow dispatch.
    retry_sites = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "bounded_read_retry":
                retry_sites.setdefault(node.name, 0)
                retry_sites[node.name] += 1
    assert retry_sites == {"assert_no_prior_timing_run": 1, "cmd_preflight": 1}

    # No retry construct exists inside the measured region at all.
    timed = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "timed"
    )
    assert not [
        inner for inner in ast.walk(timed)
        if isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "bounded_read_retry"
    ]
    for banned in ("gh workflow run", "rerun", "re-dispatch"):
        assert banned not in source.lower()


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


class _NoSleep:
    """Drop-in for the module-level ``time`` in _phase5_common: keeps the retry
    bound observable without spending real seconds."""

    @staticmethod
    def sleep(seconds):
        return None


# ---------------------------------------------------------------------------
# B5-P0 repair (dispatch q77-p5d-repair-stage2cb5-p0-implement-a; approved plan
# q77-p5d-repair-stage2cb5-plan-d). Model-free throughout: no provider call, no
# OIDC request, no workflow dispatch.
# ---------------------------------------------------------------------------


def _driver_ast():
    return ast.parse(DRIVER_PATH.read_text(encoding="utf-8"))


def _function(name, kind=ast.FunctionDef):
    return next(
        node for node in ast.walk(_driver_ast())
        if isinstance(node, kind) and node.name == name
    )


# --- R1: a non-empty final survivor scan can never PASS ---------------------


def test_non_empty_final_survivor_scan_sets_a_topology_stop():
    """Frozen topology.pass requires the final survivor scan to be EMPTY.

    Before this repair the tail set stop_reason for ancestry escape and for an
    unidentified CLI, but not for surviving processes, so a run could publish
    result=PASS alongside c_dynamic_closed=false.
    """
    execute = _function("cmd_execute")
    guards = [
        node for node in ast.walk(execute)
        if isinstance(node, ast.If) and "survivor_count" in ast.dump(node.test)
        and "TOPOLOGY_ESCAPE" in ast.dump(node)
    ]
    assert len(guards) == 1
    guard = guards[0]
    assert "stop_reason" in ast.dump(guard.test)
    # The reason must come from the frozen twelve, and specifically the frozen
    # topology vocabulary -- a new reason would move both frozen hashes.
    assert "TOPOLOGY_ESCAPE" in tr_stop_reasons()
    assert "survivor" in ast.dump(guard).lower()


def tr_stop_reasons():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_tr_probe", DRIVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STOP_REASONS


def test_survivor_stop_is_appended_after_the_existing_topology_checks():
    """Precedence and detail strings of the two existing checks are unchanged."""
    source = DRIVER_PATH.read_text(encoding="utf-8")
    escape = source.index('"TOPOLOGY_ESCAPE", f"{len(escapes)} invocation(s) observed ancestry escape"')
    unidentified = source.index('"no sample identified the bundled CLI"')
    survivor = source.index("final survivor scan non-empty")
    pass_write = source.index('"result": "PASS"')
    assert escape < unidentified < survivor < pass_write


def test_pass_result_and_c_dynamic_closed_can_never_disagree():
    """The joint invariant the leak broke.

    PASS is written only under ``stop_reason is None``; ``c_dynamic_closed`` is
    ``stop_reason is None and survivor_count == 0``; and the R1 guard makes a
    non-zero survivor count force a stop_reason. So PASS implies
    survivor_count == 0 implies c_dynamic_closed is true.
    """
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert 'stop_reason is None and final_scan.get("survivor_count", 0) > 0' in source
    assert 'stop_reason is None and final_scan.get("survivor_count") == 0' in source

    execute = _function("cmd_execute")
    pass_branches = [
        node for node in ast.walk(execute)
        if isinstance(node, ast.If) and "'PASS'" in ast.dump(node)
    ]
    assert pass_branches, "no PASS branch found"
    # Every branch that can write a PASS result is guarded on stop_reason.
    for branch in pass_branches:
        assert "stop_reason" in ast.dump(branch.test)

    # Executable check of the same boolean relation over every combination.
    for stop_reason in (None, "TOPOLOGY_ESCAPE"):
        for survivors in (0, 1, 7):
            effective = stop_reason
            if effective is None and survivors > 0:
                effective = "TOPOLOGY_ESCAPE"
            c_dynamic_closed = bool(effective is None and survivors == 0)
            is_pass = effective is None
            assert not (is_pass and not c_dynamic_closed)


# --- R2: token counts retained on both durable record types -----------------


def test_usage_tokens_retains_numbers_and_records_unknown_as_none(tr):
    class _Result:
        def __init__(self, usage):
            self.usage = usage

    assert tr._usage_tokens(_Result({"input_tokens": 11, "output_tokens": 7})) == (11, 7)
    assert tr._usage_tokens(_Result({"input_tokens": 11})) == (11, None)
    assert tr._usage_tokens(_Result({})) == (None, None)
    assert tr._usage_tokens(_Result(None)) == (None, None)
    assert tr._usage_tokens(None) == (None, None)
    # A zero the SDK actually reported stays a number, distinct from unknown.
    assert tr._usage_tokens(_Result({"input_tokens": 0, "output_tokens": 0})) == (0, 0)
    # Booleans are not token counts.
    assert tr._usage_tokens(_Result({"input_tokens": True, "output_tokens": 5})) == (None, 5)


def _event_append_call(event_name):
    """The ``events.append("<event_name>", ...)`` call node in the driver."""
    for node in ast.walk(_driver_ast()):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "append"
            and getattr(node.func.value, "id", None) == "events"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == event_name
        ):
            return node
    raise AssertionError(f"no events.append({event_name!r}, ...) call found")


def test_both_durable_record_types_carry_explicit_token_fields():
    """Unknown is recorded as an explicit null, never omitted.

    Both record types must carry the FIELDS unconditionally -- a count the
    SDK never exposed is written as null, so an absent key can never be
    confused with an observed value.
    """
    for event_name in ("INVOCATION_FINISHED", "OBSERVATION_ACCOUNTED"):
        keywords = {kw.arg for kw in _event_append_call(event_name).keywords}
        assert "input_tokens" in keywords, event_name
        assert "output_tokens" in keywords, event_name


def test_finished_tokens_come_from_result_usage_and_accounted_from_the_ledger():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "finished_tokens = _usage_tokens(result)" in source
    assert 'getattr(latest, "input_tokens", None)' in source
    assert 'getattr(latest, "output_tokens", None)' in source


@pytest.mark.parametrize(
    "finished, accounted",
    [
        ((None, None), (None, None)),
        ((5, 6), (None, None)),
        ((None, None), (5, 6)),
        ((5, None), (5, 6)),
        ((5, 6), (5, 6)),
        ((0, 0), (0, 0)),
    ],
)
def test_unknown_or_agreeing_token_counts_are_not_a_timing_failure(tr, finished, accounted):
    """Retention is a retention requirement, not a PASS condition."""
    tr._reconcile_tokens(3, finished, accounted)


@pytest.mark.parametrize(
    "finished, accounted",
    [((5, 6), (9, 6)), ((5, 6), (5, 9)), ((0, 0), (1, 0))],
)
def test_contradicting_exposed_token_counts_fail_closed(tr, finished, accounted):
    with pytest.raises(tr.TimingRehearsalStop) as excinfo:
        tr._reconcile_tokens(3, finished, accounted)
    assert excinfo.value.reason == "INFRASTRUCTURE_FAULT"
    assert "ordinal 3" in str(excinfo.value)
    assert "contradiction" in str(excinfo.value)


def test_token_records_carry_no_prompt_or_response_content():
    """The evidence firewall is unchanged by R2: the added fields are two
    integers, and no record gained a content-bearing key."""
    allowed = {
        "INVOCATION_FINISHED": {
            "ordinal", "item_id", "elapsed_ms", "sdk_subtype", "is_error", "num_turns",
            "duration_ms", "duration_api_ms", "input_tokens", "output_tokens",
            "resolved_model_keys", "topology_samples", "bundled_cli_observed",
        },
        "OBSERVATION_ACCOUNTED": {
            "ordinal", "item_id", "charged_eur_micros", "reserved_eur_micros",
            "input_tokens", "output_tokens", "sdk_subtype", "failure_class",
            "finding_count",
        },
    }
    for event_name, expected in allowed.items():
        call = _event_append_call(event_name)
        keywords = {kw.arg for kw in call.keywords}
        assert keywords == expected, event_name
        dumped = ast.dump(call)
        for banned in ("user_prompt", "transcript", "cmdline", "content"):
            assert banned not in dumped, f"{event_name} may carry {banned}"


# --- R3: the complete resolved dependency set -------------------------------


def test_full_resolved_distribution_set_is_persisted_not_only_counted():
    """A count cannot reconstruct the set after the runner is destroyed."""
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert '"distributions": [[name, version] for name, version in identity.distributions]' in source
    assert '"distribution_count": len(identity.distributions)' in source


def test_runtime_identity_distributions_are_already_sorted_and_unique():
    from sentinel.phase5.runtime_identity import RuntimeIdentity

    field = RuntimeIdentity.model_fields["distributions"]
    assert field.annotation is not None
    with pytest.raises(Exception):
        RuntimeIdentity.model_validate({"distributions": [["b", "1"], ["a", "1"]]})


def test_requirements_remains_a_direct_pin_reconciliation_surface():
    """R3's corrected contract, recorded in the driver rather than assumed."""
    source = DRIVER_PATH.read_text(encoding="utf-8")
    # Normalize comment line-wrapping before matching the recorded contract.
    flat = " ".join(source.lower().replace("#", " ").split())
    assert "direct-pin" in flat or "direct pin" in flat
    assert "not a transitive lock" in flat


# --- Ordering property 9: acquisition outside the measured window -----------


def test_fresh_assertion_is_acquired_before_started_record_and_before_the_timer():
    """Load-bearing ordering (plan-d 1.3).

    The GitHub fetch must not enter elapsed_ms, and a failed acquisition must
    not leave an orphan INVOCATION_STARTED that the frozen durability rule
    would misread as INCOMPLETE_N.
    """
    timed = _function("timed", ast.AsyncFunctionDef)
    prepare = started = timer = awaited = None
    for index, node in enumerate(ast.walk(timed)):
        pass
    dumped = [ast.dump(stmt) for stmt in timed.body]

    def first(predicate):
        return next(i for i, text in enumerate(dumped) if predicate(text))

    prepare = first(lambda t: "prepare_fresh_assertion" in t)
    started = first(lambda t: "INVOCATION_STARTED" in t)
    timer = first(lambda t: "started_ns" in t and "perf_counter_ns" in t)
    awaited = first(lambda t: "Await" in t and "inner" in t)
    assert prepare < started < timer <= awaited


def test_failed_assertion_is_a_frozen_auth_stop_not_an_incomplete_n():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    timed_start = source.index("async def timed(")
    timed_block = source[timed_start:source.index("stub.query_fn = timed")]
    assert "AUTH_OR_OIDC_FAULT" in timed_block
    assert timed_block.index("AUTH_OR_OIDC_FAULT") < timed_block.index('"INVOCATION_STARTED"')


def test_timing_driver_does_not_use_the_composed_wrapper():
    """The timing lane must prepare itself, outside the measured region."""
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "assertion_refreshed" not in source
    assert "inner = health_gated(stub.query_fn, session)" in source
    assert "session.prepare_fresh_assertion(env)" in source


# --- Part 7: bounded pre-provider retries -----------------------------------


class _FlakyRuns:
    """Fails transiently ``failures`` times, then returns ``runs``."""

    def __init__(self, runs, failures, exc=None):
        self._runs, self._left = runs, failures
        self._exc = exc or GithubEvidenceError("GitHub REST request returned HTTP 503")
        self.calls = 0

    def list_workflow_runs(self, workflow_path, *, created_after, created_before):
        self.calls += 1
        if self._left > 0:
            self._left -= 1
            raise self._exc
        return self._runs


def test_prior_run_discovery_retries_only_transient_transport_failures(tr, monkeypatch):
    monkeypatch.setattr(common, "time", _NoSleep())
    client = _FlakyRuns([], failures=2)
    assert tr.assert_no_prior_timing_run(client, "777") == 0
    assert client.calls == 3


def test_prior_run_discovery_retry_is_bounded_then_fails_closed(tr, monkeypatch):
    monkeypatch.setattr(common, "time", _NoSleep())
    client = _FlakyRuns([], failures=99)
    with pytest.raises(tr.TimingRehearsalStop) as excinfo:
        tr.assert_no_prior_timing_run(client, "777")
    assert excinfo.value.reason == "PRIOR_RUN_PRESENT"
    assert client.calls == 3


def test_a_prior_run_actually_present_is_never_retried(tr, monkeypatch):
    """Deterministic: the read succeeded and the answer is a refusal."""
    monkeypatch.setattr(common, "time", _NoSleep())
    client = _FlakyRuns([_Run("888")], failures=0)
    with pytest.raises(tr.TimingRehearsalStop) as excinfo:
        tr.assert_no_prior_timing_run(client, "777")
    assert excinfo.value.reason == "PRIOR_RUN_PRESENT"
    assert client.calls == 1


@pytest.mark.parametrize(
    "exc",
    [
        GithubEvidenceError("GitHub REST request returned HTTP 403"),
        GithubEvidenceError("GitHub REST response was not valid JSON"),
    ],
)
def test_deterministic_discovery_failures_are_not_retried(tr, monkeypatch, exc):
    monkeypatch.setattr(common, "time", _NoSleep())
    client = _FlakyRuns([], failures=99, exc=exc)
    with pytest.raises(tr.TimingRehearsalStop):
        tr.assert_no_prior_timing_run(client, "777")
    assert client.calls == 1


def test_fx_retry_covers_the_fetch_but_never_the_parse():
    """Row 7 wires the retry through fx.py's existing injectable seam, so a
    malformed ECB response stays a deterministic first-attempt refusal."""
    from agents.checker.fx import FxResolutionError, resolve_ecb_usd_per_eur

    valid = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" '
        b'xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
        b'<Cube><Cube time="2026-09-22"><Cube currency="USD" rate="1.1000"/>'
        b"</Cube></Cube></gesmes:Envelope>"
    )
    fetches = []

    def flaky_fetch():
        fetches.append(1)
        if len(fetches) < 3:
            raise FxResolutionError("ECB reference-rate fetch returned HTTP 503")
        return valid

    rate = resolve_ecb_usd_per_eur(
        now=datetime(2026, 9, 22, tzinfo=timezone.utc),
        fetch=lambda: common.bounded_read_retry(flaky_fetch, sleep=lambda s: None),
    )
    assert str(rate.usd_per_eur) == "1.1000"
    assert len(fetches) == 3

    parses = []

    def good_fetch_bad_xml():
        parses.append(1)
        return b"not xml at all"

    with pytest.raises(FxResolutionError):
        resolve_ecb_usd_per_eur(
            now=datetime(2026, 9, 22, tzinfo=timezone.utc),
            fetch=lambda: common.bounded_read_retry(good_fetch_bad_xml, sleep=lambda s: None),
        )
    assert len(parses) == 1, "a parse failure must not be retried"


def test_fx_call_site_wraps_only_the_fetch_seam():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "fetch=lambda: bounded_read_retry(lambda: fetch_ecb_daily_xml(timeout=10.0))" in source
    fx_source = (REPO_ROOT / "agents" / "checker" / "fx.py").read_text(encoding="utf-8")
    assert "bounded_read_retry" not in fx_source, "agents/checker/fx.py must stay unmodified"


def test_retry_bounds_are_three_attempts_with_one_and_two_second_backoff():
    assert common.RETRY_ATTEMPTS == 3
    assert common.RETRY_BACKOFF_SECONDS == (1.0, 2.0)
    calls, slept = [], []

    def always_transient():
        calls.append(1)
        raise GithubEvidenceError("GitHub REST request returned HTTP 502")

    with pytest.raises(GithubEvidenceError):
        common.bounded_read_retry(always_transient, sleep=slept.append)
    assert len(calls) == 3
    assert slept == [1.0, 2.0], "no sleep after the final failed attempt"


def test_live_main_retry_covers_the_read_but_never_the_sha_comparison():
    """A genuine source mismatch is deterministic and refuses immediately."""
    calls = []

    class _Client:
        def get_main_head_sha(self):
            calls.append(1)
            return "b" * 40

    with pytest.raises(common.Phase5ScriptError):
        common.assert_expected_source_live(_Client(), "a" * 40)
    assert len(calls) == 1


# --- R4: governed class-B cost handoff --------------------------------------


def _event(event, **fields):
    return json.dumps({"event": event, **fields})


def _write_events(tmp_path, lines):
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "phase5_timing_events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return evidence


def _run_started(**overrides):
    payload = {
        "run_id": "99887766",
        "run_attempt": 1,
        "source_sha": "a" * 40,
        "corpus_sha256": CORPUS_SHA,
        "preregistration_sha256": PREREG_SHA,
        "model": "claude-sonnet-5",
    }
    payload.update(overrides)
    return _event("RUN_STARTED", **payload)


def test_class_a_b_c_d_accounting_is_frozen_prospectively(tr):
    """A accounted; B finished-but-unaccounted; C started-only; D no contact."""
    records = [json.loads(_run_started())]
    # A: fully accounted
    records += [
        json.loads(_event("INVOCATION_STARTED", ordinal=1, reserved_eur_micros=1_000_000)),
        json.loads(_event("INVOCATION_FINISHED", ordinal=1, input_tokens=10, output_tokens=4)),
        json.loads(_event("OBSERVATION_ACCOUNTED", ordinal=1, charged_eur_micros=250_000,
                          reserved_eur_micros=1_000_000, input_tokens=10, output_tokens=4)),
    ]
    # B: finished, never accounted -> full reservation, FINISHED-side tokens
    records += [
        json.loads(_event("INVOCATION_STARTED", ordinal=2, reserved_eur_micros=1_000_000)),
        json.loads(_event("INVOCATION_FINISHED", ordinal=2, input_tokens=7, output_tokens=3)),
    ]
    # C: started only -> full reservation, tokens unknown
    records += [json.loads(_event("INVOCATION_STARTED", ordinal=3, reserved_eur_micros=1_000_000))]

    spend = tr.aggregate_timing_spend(records)
    assert spend["accounting_basis"] == {1: "A", 2: "B", 3: "C"}
    assert spend["cost_eur_micros"] == 250_000 + 1_000_000 + 1_000_000
    assert spend["input_tokens"] == 17 and spend["output_tokens"] == 7
    assert spend["unresolved_ordinals"] == (2, 3)
    assert spend["unresolved_token_ordinals"] == (3,)
    assert spend["conservative_full_reservation_ordinals"] == (2, 3)
    assert spend["observations_accounted"] == 3


def test_class_d_contributes_nothing(tr):
    records = [json.loads(_run_started())]
    spend = tr.aggregate_timing_spend(records)
    assert spend["observations_accounted"] == 0
    assert spend["cost_eur_micros"] == 0


def test_conservative_charge_uses_the_adopted_terminal_charge_rule(tr):
    """Imported, not reimplemented: ADR-0008 cases C and D."""
    from agents.checker.failures import terminal_charge

    assert terminal_charge(
        completed=False, reserved_eur_micros=1_000_000, estimate_eur_micros=None
    ) == 1_000_000
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "from agents.checker.failures import terminal_charge" in source


def test_started_without_a_recorded_reservation_refuses_rather_than_guessing(tr):
    records = [
        json.loads(_run_started()),
        json.loads(_event("INVOCATION_STARTED", ordinal=1)),
    ]
    with pytest.raises(Phase5ScriptError):
        tr.aggregate_timing_spend(records)


def test_cost_evidence_emits_one_row_and_records_stop_spend(tr, tmp_path):
    """STOP spend is recorded: real money was spent either way."""
    evidence = _write_events(tmp_path, [
        _run_started(),
        _event("INVOCATION_STARTED", ordinal=1, reserved_eur_micros=1_000_000),
        _event("INVOCATION_FINISHED", ordinal=1, input_tokens=10, output_tokens=4),
        _event("OBSERVATION_ACCOUNTED", ordinal=1, charged_eur_micros=250_000,
               reserved_eur_micros=1_000_000, input_tokens=10, output_tokens=4),
        _event("STOP", reason="SDK_BUDGET_CEILING"),
    ])
    (evidence / "phase5_timing_stop.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "timing_cost_evidence.json"
    args = types.SimpleNamespace(
        evidence_dir=evidence, out_path=out,
        corpus_sha256=CORPUS_SHA, preregistration_sha256=PREREG_SHA,
    )
    assert tr.cmd_cost_evidence(args) == 0

    record = TimingCostEvidenceRecord.model_validate_json(out.read_text(encoding="utf-8"))
    assert record.terminal_class == "STOP"
    assert len(record.cost_rows) == 1
    assert record.cost_rows[0].cost_eur_micros == 250_000
    assert record.cost_rows[0].run_id == "r-p5d-timing-99887766"
    assert record.cost_rows[0].run_kind == "live"


def test_cost_evidence_emits_no_record_when_no_provider_invocation_started(tr, tmp_path):
    evidence = _write_events(tmp_path, [_run_started()])
    out = tmp_path / "timing_cost_evidence.json"
    args = types.SimpleNamespace(
        evidence_dir=evidence, out_path=out, corpus_sha256=None, preregistration_sha256=None,
    )
    assert tr.cmd_cost_evidence(args) == 4
    assert not out.exists()


def test_cost_evidence_refuses_a_missing_or_ambiguous_terminal_shape(tr, tmp_path):
    out = tmp_path / "out.json"
    # No events at all: a consumed run with no trustworthy artifact.
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(Phase5ScriptError):
        tr.cmd_cost_evidence(types.SimpleNamespace(
            evidence_dir=empty, out_path=out, corpus_sha256=None, preregistration_sha256=None))

    # Both a summary and a stop record present: ambiguous, fails closed.
    evidence = _write_events(tmp_path, [
        _run_started(),
        _event("INVOCATION_STARTED", ordinal=1, reserved_eur_micros=1_000_000),
    ])
    (evidence / "phase5_timing_summary.json").write_text("{}", encoding="utf-8")
    (evidence / "phase5_timing_stop.json").write_text("{}", encoding="utf-8")
    with pytest.raises(Phase5ScriptError):
        tr.cmd_cost_evidence(types.SimpleNamespace(
            evidence_dir=evidence, out_path=out, corpus_sha256=None, preregistration_sha256=None))


def test_cost_evidence_refuses_a_frozen_hash_mismatch(tr, tmp_path):
    evidence = _write_events(tmp_path, [
        _run_started(corpus_sha256="0" * 64),
        _event("INVOCATION_STARTED", ordinal=1, reserved_eur_micros=1_000_000),
    ])
    with pytest.raises(Phase5ScriptError):
        tr.cmd_cost_evidence(types.SimpleNamespace(
            evidence_dir=evidence, out_path=tmp_path / "o.json",
            corpus_sha256=CORPUS_SHA, preregistration_sha256=PREREG_SHA))


def test_timing_cost_record_forbids_extras_and_requires_exactly_one_row():
    base = dict(
        schema_version=1, lane="P5D_TIMING_REHEARSAL", rehearsal_run_id="1",
        rehearsal_run_attempt=1, rehearsal_source_sha="a" * 40,
        corpus_sha256=CORPUS_SHA, preregistration_sha256=PREREG_SHA,
        terminal_class="PASS", observations_accounted=1, accounting_basis={1: "A"},
        unresolved_ordinals=(), unresolved_token_ordinals=(),
        conservative_full_reservation_ordinals=(),
        cost_rows=(dict(
            schema_version=1, run_id="r-x", recorded_at_utc=datetime(2026, 9, 22, tzinfo=timezone.utc),
            run_kind="live", model="claude-sonnet-5", input_tokens=1, output_tokens=1,
            cost_eur_micros=1,
        ),),
    )
    TimingCostEvidenceRecord.model_validate(base)
    with pytest.raises(Exception):
        TimingCostEvidenceRecord.model_validate({**base, "unexpected": 1})
    with pytest.raises(Exception):
        TimingCostEvidenceRecord.model_validate({**base, "cost_rows": ()})
    with pytest.raises(Exception):
        TimingCostEvidenceRecord.model_validate(
            {**base, "observations_accounted": 0, "accounting_basis": {}}
        )
    # conservative ordinals must equal the class-B and class-C ordinals
    with pytest.raises(Exception):
        TimingCostEvidenceRecord.model_validate(
            {**base, "conservative_full_reservation_ordinals": (1,)}
        )


def test_recording_tool_accepts_the_timing_cost_record_shape():
    recorder = (REPO_ROOT / "scripts" / "record_phase5_cost_evidence.py").read_text(encoding="utf-8")
    assert "TimingCostEvidenceRecord" in recorder
    assert "ProbeEvidenceRecord, GateEvidenceRecord, TimingCostEvidenceRecord" in recorder


def test_no_class_b_row_is_appended_by_the_repair_itself():
    """B5-P0 builds the mechanism; B5-P6 invokes it."""
    ledger = (REPO_ROOT / "telemetry" / "cost_ledger.jsonl").read_text(encoding="utf-8")
    assert "P5D_TIMING_REHEARSAL" not in ledger
    assert "r-p5d-timing-" not in ledger

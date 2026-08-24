"""Tests for sentinel/phase5/qualification.py.

Reuses the builder helpers from tests/test_phase5_bundle.py — the same
cross-file-import pattern already used elsewhere in this suite.
"""

from __future__ import annotations

from datetime import timedelta
from typing import get_args

import pytest

from sentinel.phase5 import bundle as b
from sentinel.phase5 import models as m
from sentinel.phase5 import qualification as q
from tests.test_phase5_bundle import (
    SLOT1,
    SOURCE_SHA,
    WINDOW_CREATED_AT,
    make_control_state,
    make_cost_row,
    make_genesis,
    make_slot_successor,
    make_window,
    slot_ts,
)


def make_github_run(window, slot_n, **overrides):
    fields = dict(
        workflow_identity=window.scheduled_workflow_identity,
        github_run_id=f"gh-run-{slot_n}",
        run_attempt=1,
        event="schedule",
        ref=window.ref,
        source_sha=window.source_sha,
        created_at=slot_ts(slot_n),
        run_started_at=slot_ts(slot_n),
    )
    fields.update(overrides)
    return q.GithubRunMetadata(**fields)


def make_evidence(github_run_id, run_id="sentinel-run", **overrides):
    fields = dict(
        schema_version=1, run_id=run_id, github_run_id=github_run_id, status="COMPLETED", source="live", judgment_mode="agent"
    )
    fields.update(overrides)
    return m.SentinelRunEvidence(**fields)


def make_matching_successor(window, slot_n, github_run, evidence, predecessor_identity, predecessor_manifest, **overrides):
    fields = dict(
        github_run_id=github_run.github_run_id,
        event=github_run.event,
        run_attempt=github_run.run_attempt,
        github_run_created_at_utc=github_run.created_at,
        github_run_started_at_utc=github_run.run_started_at,
        sentinel_run_id=evidence.run_id,
    )
    fields.update(overrides)
    return make_slot_successor(window, slot_n, predecessor_identity=predecessor_identity, predecessor_manifest=predecessor_manifest, **fields)


def _happy_path(slot_n=1, delay=timedelta(minutes=5), spend=100, evaluated_at=None, window_consumed_flag=False):
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0, spend=0, evaluated_at=WINDOW_CREATED_AT)
    github_run = make_github_run(window, slot_n, created_at=slot_ts(slot_n) + delay, run_started_at=slot_ts(slot_n) + delay)
    evidence = make_evidence(github_run.github_run_id)
    cost_row = make_cost_row(evidence.run_id, cost_eur_micros=spend, recorded_at_utc=slot_ts(slot_n))
    evaluated_at = evaluated_at or slot_ts(slot_n)

    prev_identity, prev_manifest = "genesis-1", genesis
    slot_candidates = []
    for n in range(1, slot_n):
        prior_run = make_github_run(window, n)
        prior_evidence = make_evidence(prior_run.github_run_id, run_id=f"sentinel-run-{n}")
        prior_row = make_cost_row(prior_evidence.run_id, cost_eur_micros=0, recorded_at_utc=slot_ts(n))
        prior_manifest = make_matching_successor(
            window, n, prior_run, prior_evidence, prev_identity, prev_manifest, qualification_outcome="QUALIFYING"
        )
        prior_identity = f"slot-{n}"
        prior_state = make_control_state(window, slot_index=n, spend=0, evaluated_at=slot_ts(n))
        slot_candidates.append(
            b.SlotSuccessorCandidate(
                artifact_identity=prior_identity, manifest=prior_manifest, control_state=prior_state, cost_rows=(prior_row,)
            )
        )
        prev_identity, prev_manifest = prior_identity, prior_manifest

    successor_manifest = make_matching_successor(
        window, slot_n, github_run, evidence, prev_identity, prev_manifest, qualification_outcome="QUALIFYING"
    )
    successor_state = make_control_state(
        window, slot_index=slot_n, window_consumed=window_consumed_flag, spend=spend, evaluated_at=evaluated_at
    )
    successor = b.SlotSuccessorCandidate(
        artifact_identity=f"slot-{slot_n}", manifest=successor_manifest, control_state=successor_state, cost_rows=(cost_row,)
    )
    chain = q.QualificationChainContext(
        genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state, slot_candidates=tuple(slot_candidates)
    )
    return dict(
        window=window, slot_n=slot_n, github_run=github_run, evidence=evidence, cost_row=cost_row,
        successor=successor, chain=chain,
    )


# ---------------------------------------------------------------------------
# classify_run — core outcome matrix
# ---------------------------------------------------------------------------


def test_classify_run_qualifies_at_zero_delay():
    ctx = _happy_path(delay=timedelta(minutes=0))
    result = q.classify_run(
        ctx["window"], 1, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], ctx["successor"], ctx["chain"],
        window_already_consumed=False, now=ctx["github_run"].created_at,
    )
    assert result.outcome == "QUALIFYING"
    assert result.delay_minutes == 0


def test_classify_run_qualifies_at_exactly_120_minutes():
    ctx = _happy_path(delay=timedelta(minutes=120))
    result = q.classify_run(
        ctx["window"], 1, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], ctx["successor"], ctx["chain"],
        window_already_consumed=False, now=ctx["github_run"].created_at,
    )
    assert result.outcome == "QUALIFYING"
    assert result.delay_minutes == 120


def test_classify_run_late_at_121_minutes():
    ctx = _happy_path(delay=timedelta(minutes=121))
    successor = ctx["successor"]
    late_manifest = successor.manifest.model_copy(update={"qualification_outcome": "LATE_NONQUALIFYING"})
    late_state = make_control_state(
        ctx["window"], slot_index=1, window_consumed=True, window_consume_reason="LATE_NONQUALIFYING", spend=100, evaluated_at=slot_ts(1)
    )
    successor = b.SlotSuccessorCandidate(
        artifact_identity=successor.artifact_identity, manifest=late_manifest, control_state=late_state, cost_rows=successor.cost_rows
    )
    result = q.classify_run(
        ctx["window"], 1, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], successor, ctx["chain"],
        window_already_consumed=False, now=ctx["github_run"].created_at,
    )
    assert result.outcome == "LATE_NONQUALIFYING"
    assert result.delay_minutes == 121


def test_classify_run_negative_delay_is_wrong_provenance():
    ctx = _happy_path(delay=timedelta(minutes=-5))
    successor = ctx["successor"]
    wp_manifest = successor.manifest.model_copy(update={"qualification_outcome": "WRONG_PROVENANCE_NONQUALIFYING"})
    wp_state = make_control_state(
        ctx["window"], slot_index=1, window_consumed=True, window_consume_reason="WRONG_PROVENANCE_NONQUALIFYING", spend=100, evaluated_at=slot_ts(1)
    )
    successor = b.SlotSuccessorCandidate(
        artifact_identity=successor.artifact_identity, manifest=wp_manifest, control_state=wp_state, cost_rows=successor.cost_rows
    )
    result = q.classify_run(
        ctx["window"], 1, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], successor, ctx["chain"],
        window_already_consumed=False, now=ctx["github_run"].created_at,
    )
    assert result.outcome == "WRONG_PROVENANCE_NONQUALIFYING"


@pytest.mark.parametrize(
    "override",
    [
        dict(event="workflow_dispatch"),
        dict(workflow_identity="other-workflow"),
        dict(ref="refs/heads/other"),
        dict(source_sha="c" * 40),
    ],
)
def test_classify_run_wrong_provenance_no_successor_required(override):
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1, **override)
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    result = q.classify_run(
        window, 1, github_run, [], [], None, chain, window_already_consumed=False, now=slot_ts(1)
    )
    assert result.outcome == "WRONG_PROVENANCE_NONQUALIFYING"


def test_classify_run_wrong_attempt_is_duplicate_no_successor_required():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1, run_attempt=2)
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    result = q.classify_run(window, 1, github_run, [], [], None, chain, window_already_consumed=False, now=slot_ts(1))
    assert result.outcome == "DUPLICATE_NONQUALIFYING"


def test_classify_run_zero_evidence_no_successor_required():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1)
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    result = q.classify_run(window, 1, github_run, [], [], None, chain, window_already_consumed=False, now=slot_ts(1))
    assert result.outcome == "FAILED_NONTERMINAL"


def test_classify_run_evidence_filtered_by_github_run_id():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1)
    unrelated = make_evidence("some-other-gh-run")
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    result = q.classify_run(
        window, 1, github_run, [unrelated], [], None, chain, window_already_consumed=False, now=slot_ts(1)
    )
    assert result.outcome == "FAILED_NONTERMINAL"


def test_classify_run_two_evidence_candidates_for_this_run():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1)
    e1 = make_evidence(github_run.github_run_id, run_id="run-a")
    e2 = make_evidence(github_run.github_run_id, run_id="run-b")
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    result = q.classify_run(window, 1, github_run, [e1, e2], [], None, chain, window_already_consumed=False, now=slot_ts(1))
    assert result.outcome == "FAILED_NONTERMINAL"


def test_classify_run_non_completed_evidence():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1)
    evidence = make_evidence(github_run.github_run_id, status="FAILED")
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    result = q.classify_run(window, 1, github_run, [evidence], [], None, chain, window_already_consumed=False, now=slot_ts(1))
    assert result.outcome == "FAILED_NONTERMINAL"


def test_classify_run_stub_mode_never_qualifies():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1)
    evidence = make_evidence(github_run.github_run_id, judgment_mode="stub")
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    result = q.classify_run(window, 1, github_run, [evidence], [], None, chain, window_already_consumed=False, now=slot_ts(1))
    assert result.outcome == "WRONG_PROVENANCE_NONQUALIFYING"


def test_classify_run_fixtures_source_never_qualifies():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1)
    evidence = make_evidence(github_run.github_run_id, source="fixtures")
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    result = q.classify_run(window, 1, github_run, [evidence], [], None, chain, window_already_consumed=False, now=slot_ts(1))
    assert result.outcome == "WRONG_PROVENANCE_NONQUALIFYING"


@pytest.mark.parametrize("n_matches", [0, 2])
def test_classify_run_bad_costrow_count(n_matches):
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1)
    evidence = make_evidence(github_run.github_run_id)
    cost_rows = [make_cost_row(evidence.run_id) for _ in range(n_matches)]
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    result = q.classify_run(
        window, 1, github_run, [evidence], cost_rows, None, chain, window_already_consumed=False, now=slot_ts(1)
    )
    assert result.outcome == "COSTROW_INVALID"


def test_classify_run_wrong_costrow_run_id():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1)
    evidence = make_evidence(github_run.github_run_id)
    wrong_row = make_cost_row("some-other-run")
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    result = q.classify_run(
        window, 1, github_run, [evidence], [wrong_row], None, chain, window_already_consumed=False, now=slot_ts(1)
    )
    assert result.outcome == "COSTROW_INVALID"


def test_classify_run_window_already_consumed():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1)
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    result = q.classify_run(window, 1, github_run, [], [], None, chain, window_already_consumed=True, now=slot_ts(1))
    assert result.outcome == "STATE_CHAIN_FAILURE"


def test_classify_run_qualification_ready_with_no_successor_is_state_chain_failure():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1)
    evidence = make_evidence(github_run.github_run_id)
    cost_row = make_cost_row(evidence.run_id, cost_eur_micros=1)
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    result = q.classify_run(
        window, 1, github_run, [evidence], [cost_row], None, chain, window_already_consumed=False, now=slot_ts(1)
    )
    assert result.outcome == "STATE_CHAIN_FAILURE"


def test_classify_run_never_returns_missing_lost():
    assert "MISSING_LOST" not in get_args(m.ExecutionTimeQualificationOutcome)


# ---------------------------------------------------------------------------
# validate_successor_context — full provenance matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("github_run_id", "wrong-run"),
        ("sentinel_run_id", "wrong-sentinel-run"),
        ("window_id", "p5w-wrong"),
        ("window_record_sha256", "0" * 64),
        ("slot_index", 2),
        ("expected_slot_utc", slot_ts(2)),
        ("source_sha", "c" * 40),
        ("workflow_identity", "wrong-workflow"),
        ("ref", "refs/heads/other"),
        ("event", "workflow_dispatch"),
        ("run_attempt", 2),
        ("github_run_created_at_utc", slot_ts(2)),
        ("github_run_started_at_utc", slot_ts(2)),
    ],
)
def test_validate_successor_context_rejects_each_mismatch(field, value):
    ctx = _happy_path()
    tampered = ctx["successor"].manifest.model_copy(update={field: value})
    with pytest.raises(b.BrokenPredecessor):
        q.validate_successor_context(
            tampered, window=ctx["window"], slot_n=1, github_run=ctx["github_run"], evidence=ctx["evidence"]
        )


def test_validate_successor_context_accepts_matching_manifest():
    ctx = _happy_path()
    q.validate_successor_context(
        ctx["successor"].manifest, window=ctx["window"], slot_n=1, github_run=ctx["github_run"], evidence=ctx["evidence"]
    )


# ---------------------------------------------------------------------------
# Successor state-edge / consumption-binding / structural consistency
# ---------------------------------------------------------------------------


def test_classify_run_illegal_slot_jump_in_successor_state_is_state_chain_failure():
    # the deterministic computation itself resolves STATE_CHAIN_FAILURE
    # via the broken transition; the successor's own control state still
    # (wrongly) claims unconsumed, so the outer consistency check also
    # fails closed rather than silently accepting the mismatch
    ctx = _happy_path()
    successor = ctx["successor"]
    jumped_state = successor.control_state.model_copy(update={"latest_authoritative_slot_index": 3})
    jumped = b.SlotSuccessorCandidate(
        artifact_identity=successor.artifact_identity, manifest=successor.manifest, control_state=jumped_state, cost_rows=successor.cost_rows
    )
    with pytest.raises(q.InconsistentEvidence):
        q.classify_run(
            ctx["window"], 1, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], jumped, ctx["chain"],
            window_already_consumed=False, now=ctx["github_run"].created_at,
        )


def test_classify_run_spend_mismatched_successor_is_state_chain_failure():
    ctx = _happy_path()
    successor = ctx["successor"]
    poisoned_state = successor.control_state.model_copy(update={"last_accounted_spend_eur_micros": 999_999})
    poisoned = b.SlotSuccessorCandidate(
        artifact_identity=successor.artifact_identity, manifest=successor.manifest, control_state=poisoned_state, cost_rows=successor.cost_rows
    )
    with pytest.raises(q.InconsistentEvidence):
        q.classify_run(
            ctx["window"], 1, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], poisoned, ctx["chain"],
            window_already_consumed=False, now=ctx["github_run"].created_at,
        )


def test_classify_run_carried_ledger_missing_the_run_is_costrow_invalid():
    ctx = _happy_path()
    successor = ctx["successor"]
    other_row = make_cost_row("some-other-run", cost_eur_micros=100, recorded_at_utc=slot_ts(1))
    stripped = b.SlotSuccessorCandidate(
        artifact_identity=successor.artifact_identity,
        manifest=successor.manifest.model_copy(update={"qualification_outcome": "COSTROW_INVALID"}),
        control_state=successor.control_state.model_copy(
            update={"window_consumed": True, "window_consume_reason": "COSTROW_INVALID"}
        ),
        cost_rows=(other_row,),
    )
    result = q.classify_run(
        ctx["window"], 1, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], stripped, ctx["chain"],
        window_already_consumed=False, now=ctx["github_run"].created_at,
    )
    assert result.outcome == "COSTROW_INVALID"


def test_classify_run_runtime_costrow_disagrees_with_carried_row():
    ctx = _happy_path()
    successor = ctx["successor"]
    disagreeing_row = make_cost_row(ctx["evidence"].run_id, cost_eur_micros=999, recorded_at_utc=slot_ts(1))
    mismatched = b.SlotSuccessorCandidate(
        artifact_identity=successor.artifact_identity,
        manifest=successor.manifest.model_copy(update={"qualification_outcome": "COSTROW_INVALID"}),
        control_state=successor.control_state.model_copy(
            update={
                "window_consumed": True,
                "window_consume_reason": "COSTROW_INVALID",
                "last_accounted_spend_eur_micros": 999,
            }
        ),
        cost_rows=(disagreeing_row,),
    )
    result = q.classify_run(
        ctx["window"], 1, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], mismatched, ctx["chain"],
        window_already_consumed=False, now=ctx["github_run"].created_at,
    )
    assert result.outcome == "COSTROW_INVALID"


def test_classify_run_manifest_claim_mismatch_raises_for_qualifying_outcome():
    ctx = _happy_path()
    successor = ctx["successor"]
    lying = b.SlotSuccessorCandidate(
        artifact_identity=successor.artifact_identity,
        manifest=successor.manifest.model_copy(update={"qualification_outcome": "LATE_NONQUALIFYING"}),
        control_state=successor.control_state,
        cost_rows=successor.cost_rows,
    )
    with pytest.raises(q.InconsistentEvidence):
        q.classify_run(
            ctx["window"], 1, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], lying, ctx["chain"],
            window_already_consumed=False, now=ctx["github_run"].created_at,
        )


def test_classify_run_manifest_claim_mismatch_raises_for_nonqualifying_branch():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1, run_attempt=2)
    successor_manifest = make_matching_successor(
        window, 1, make_github_run(window, 1), make_evidence("gh-run-1"), "genesis-1", genesis,
        qualification_outcome="QUALIFYING",
    )
    successor_state = make_control_state(window, slot_index=1, window_consumed=False)
    successor = b.SlotSuccessorCandidate(
        artifact_identity="s1", manifest=successor_manifest, control_state=successor_state, cost_rows=()
    )
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    with pytest.raises(q.InconsistentEvidence):
        q.classify_run(window, 1, github_run, [], [], successor, chain, window_already_consumed=False, now=slot_ts(1))


def test_classify_run_consuming_outcome_requires_matching_reason():
    window = make_window()
    genesis = make_genesis(window)
    genesis_state = make_control_state(window, slot_index=0)
    github_run = make_github_run(window, 1, run_attempt=2)
    successor_manifest = make_matching_successor(
        window, 1, make_github_run(window, 1), make_evidence("gh-run-1"), "genesis-1", genesis,
        qualification_outcome="DUPLICATE_NONQUALIFYING",
    )
    # control state falsely claims unconsumed for a run that should consume
    successor_state = make_control_state(window, slot_index=1, window_consumed=False)
    successor = b.SlotSuccessorCandidate(
        artifact_identity="s1", manifest=successor_manifest, control_state=successor_state, cost_rows=()
    )
    chain = q.QualificationChainContext(genesis_identity="genesis-1", genesis=genesis, genesis_state=genesis_state)
    with pytest.raises(q.InconsistentEvidence):
        q.classify_run(window, 1, github_run, [], [], successor, chain, window_already_consumed=False, now=slot_ts(1))


def test_classify_run_qualifying_mid_window_must_not_consume():
    ctx = _happy_path(slot_n=2)
    successor = ctx["successor"]
    falsely_consumed_state = successor.control_state.model_copy(
        update={"window_consumed": True, "window_consume_reason": "LATE_NONQUALIFYING"}
    )
    falsely_consumed = b.SlotSuccessorCandidate(
        artifact_identity=successor.artifact_identity, manifest=successor.manifest, control_state=falsely_consumed_state, cost_rows=successor.cost_rows
    )
    with pytest.raises(q.InconsistentEvidence):
        q.classify_run(
            ctx["window"], 2, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], falsely_consumed, ctx["chain"],
            window_already_consumed=False, now=ctx["github_run"].created_at,
        )


def test_classify_run_slot5_qualifying_requires_clean_shape():
    ctx = _happy_path(slot_n=5)
    successor = ctx["successor"]
    unclean_state = successor.control_state.model_copy(update={"window_consumed": False})
    unclean = b.SlotSuccessorCandidate(
        artifact_identity=successor.artifact_identity, manifest=successor.manifest, control_state=unclean_state, cost_rows=successor.cost_rows
    )
    with pytest.raises(q.InconsistentEvidence):
        q.classify_run(
            ctx["window"], 5, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], unclean, ctx["chain"],
            window_already_consumed=False, now=ctx["github_run"].created_at,
        )


def test_classify_run_slot5_qualifying_clean_shape_accepted():
    ctx = _happy_path(slot_n=5, window_consumed_flag=True)
    result = q.classify_run(
        ctx["window"], 5, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], ctx["successor"], ctx["chain"],
        window_already_consumed=False, now=ctx["github_run"].created_at,
    )
    assert result.outcome == "QUALIFYING"
    assert ctx["successor"].control_state.window_consumed is True
    assert ctx["successor"].control_state.window_consume_reason is None


def test_classify_run_post_run_cost_crossing_requires_post_run_trigger():
    # the happy-path successor is unconsumed even though spend > 40M —
    # a real bundle in this state must carry POST_RUN_COST_TRIGGER, so
    # this must be rejected as inconsistent evidence
    ctx = _happy_path(slot_n=1, spend=41_000_000)
    with pytest.raises(q.InconsistentEvidence):
        q.classify_run(
            ctx["window"], 1, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], ctx["successor"], ctx["chain"],
            window_already_consumed=False, now=ctx["github_run"].created_at,
        )


def test_classify_run_post_run_cost_crossing_with_trigger_accepted():
    ctx = _happy_path(slot_n=1, spend=41_000_000)
    successor = ctx["successor"]
    consumed_state = successor.control_state.model_copy(
        update={"window_consumed": True, "window_consume_reason": "POST_RUN_COST_TRIGGER"}
    )
    consumed = b.SlotSuccessorCandidate(
        artifact_identity=successor.artifact_identity, manifest=successor.manifest, control_state=consumed_state, cost_rows=successor.cost_rows
    )
    result = q.classify_run(
        ctx["window"], 1, ctx["github_run"], [ctx["evidence"]], [ctx["cost_row"]], consumed, ctx["chain"],
        window_already_consumed=False, now=ctx["github_run"].created_at,
    )
    assert result.outcome == "QUALIFYING"


# ---------------------------------------------------------------------------
# Ownership intervals + duplicates + MISSING_LOST finality
# ---------------------------------------------------------------------------


def test_ownership_intervals_tile_with_no_gaps_or_overlaps():
    window = make_window()
    boundaries = [q._slot_ownership_interval(window, i) for i in range(1, 6)]
    for (_, end), (next_start, _) in zip(boundaries, boundaries[1:]):
        assert end == next_start
    assert boundaries[-1][1] == slot_ts(5) + timedelta(hours=24)


def test_run_at_121_minutes_matches_and_classifies_late_not_missing():
    window = make_window()
    run = make_github_run(window, 1, created_at=slot_ts(1) + timedelta(minutes=121))
    associations = q.associate_runs_to_slots(window, [run])
    assert associations[1].kind == "MATCHED"
    reviewed = q.independent_review_slots(window, [run], now=slot_ts(1) + timedelta(minutes=121))
    assert reviewed == []  # a matched run is never independently reviewed as missing


def test_run_at_23_hours_matches_prior_slot():
    window = make_window()
    run = make_github_run(window, 1, created_at=slot_ts(1) + timedelta(hours=23))
    associations = q.associate_runs_to_slots(window, [run])
    assert associations[1].kind == "MATCHED"
    assert associations[2].kind == "NO_MATCH"


def test_run_at_exact_next_slot_boundary_belongs_to_next_slot():
    window = make_window()
    run = make_github_run(window, 1, created_at=slot_ts(2))
    associations = q.associate_runs_to_slots(window, [run])
    assert associations[1].kind == "NO_MATCH"
    assert associations[2].kind == "MATCHED"


def test_slot5_interval_ends_at_24h():
    window = make_window()
    run = make_github_run(window, 1, created_at=slot_ts(5) + timedelta(hours=23, minutes=59))
    associations = q.associate_runs_to_slots(window, [run])
    assert associations[5].kind == "MATCHED"


def test_duplicate_runs_in_one_interval():
    window = make_window()
    run_a = make_github_run(window, 1, github_run_id="gh-a", created_at=slot_ts(1) + timedelta(minutes=10))
    run_b = make_github_run(window, 1, github_run_id="gh-b", created_at=slot_ts(1) + timedelta(minutes=20))
    associations = q.associate_runs_to_slots(window, [run_a, run_b])
    assert associations[1].kind == "DUPLICATE"


def test_duplicate_via_rerun_attempt2_same_run_id():
    window = make_window()
    run_a = make_github_run(window, 1, run_attempt=1, created_at=slot_ts(1) + timedelta(minutes=10))
    run_b = make_github_run(window, 1, run_attempt=2, created_at=slot_ts(1) + timedelta(minutes=15))
    associations = q.associate_runs_to_slots(window, [run_a, run_b])
    assert associations[1].kind == "DUPLICATE"


def test_identical_duplicate_api_rows_collapse_to_one_execution():
    window = make_window()
    run = make_github_run(window, 1)
    same = make_github_run(window, 1)
    associations = q.associate_runs_to_slots(window, [run, same])
    assert associations[1].kind == "MATCHED"


def test_conflicting_metadata_same_identity_raises_evidence_corruption():
    window = make_window()
    run_a = make_github_run(window, 1, created_at=slot_ts(1))
    run_b = make_github_run(window, 1, created_at=slot_ts(1) + timedelta(minutes=1))
    with pytest.raises(q.EvidenceCorruption):
        q.associate_runs_to_slots(window, [run_a, run_b])


def test_duplicate_slot_never_missing_lost_and_final_immediately():
    window = make_window()
    run_a = make_github_run(window, 1, github_run_id="gh-a", created_at=slot_ts(1) + timedelta(minutes=10))
    run_b = make_github_run(window, 1, github_run_id="gh-b", created_at=slot_ts(1) + timedelta(minutes=20))
    reviewed = q.independent_review_slots(window, [run_a, run_b], now=slot_ts(1) + timedelta(minutes=30))
    assert len(reviewed) == 1
    assert reviewed[0].outcome == "DUPLICATE_NONQUALIFYING"
    assert reviewed[0].determined_by == "independent_review"


def test_missing_lost_not_finalized_at_121_minutes():
    window = make_window()
    reviewed = q.independent_review_slots(window, [], now=slot_ts(1) + timedelta(minutes=121))
    assert reviewed == []


def test_missing_lost_not_finalized_at_23h59m():
    window = make_window()
    reviewed = q.independent_review_slots(window, [], now=slot_ts(1) + timedelta(hours=23, minutes=59))
    assert reviewed == []


def test_missing_lost_finalized_at_ownership_close():
    window = make_window()
    reviewed = q.independent_review_slots(window, [], now=slot_ts(2))
    assert len(reviewed) == 1
    assert reviewed[0].outcome == "MISSING_LOST"
    assert reviewed[0].slot_index == 1


def test_late_run_before_close_suppresses_missing_lost():
    window = make_window()
    run = make_github_run(window, 1, created_at=slot_ts(1) + timedelta(hours=5))
    reviewed = q.independent_review_slots(window, [run], now=slot_ts(2))
    assert reviewed == []


def test_only_independent_review_emits_missing_lost():
    assert "MISSING_LOST" in get_args(m.IndependentReviewOutcome)
    assert "MISSING_LOST" not in get_args(m.ExecutionTimeQualificationOutcome)


def test_run_started_at_does_not_affect_classification():
    ctx = _happy_path(delay=timedelta(minutes=5))
    divergent_run = q.GithubRunMetadata(**{**ctx["github_run"].__dict__, "run_started_at": slot_ts(1) - timedelta(hours=10)})
    successor_manifest = ctx["successor"].manifest.model_copy(
        update={"github_run_started_at_utc": divergent_run.run_started_at}
    )
    successor = b.SlotSuccessorCandidate(
        artifact_identity=ctx["successor"].artifact_identity,
        manifest=successor_manifest,
        control_state=ctx["successor"].control_state,
        cost_rows=ctx["successor"].cost_rows,
    )
    result = q.classify_run(
        ctx["window"], 1, divergent_run, [ctx["evidence"]], [ctx["cost_row"]], successor, ctx["chain"],
        window_already_consumed=False, now=divergent_run.created_at,
    )
    assert result.outcome == "QUALIFYING"
    assert result.delay_minutes == 5

"""Model-free proof package for adr/0008-judgment-call-execution-reliability
(dispatch q77-p3-adr8-impl-a).

Every test here is model-free and network-free: conftest.py's autouse
``block_network`` fixture fails any test that reaches a real socket, and
the caged harness is driven through its injected ``query_fn`` seam,
never through the real ``claude_agent_sdk.query``. Section R25 (added by
dispatch q77-p3-adr8-impl-review-remed-a) executes the production
``run_query`` body itself, with ``claude_agent_sdk.query`` patched to a
local deterministic stream — still no socket, no subprocess and no
model.

Section R10 is the PRE-WRITE proof: it pins the production uniqueness
invariant that lets ADR-0008 reuse ``(run_id, task_key)`` as the logical
judgment-task identity, and it passed before any ADR-0008 runtime or
schema change existed. It exercises the ACTUAL production constructors
(fixture policy, live ``_derive_policy`` / ``build_repo_surfaces``, site
``build_site_surface``, carry-forward ``open_scopes``, and
``_dedupe_repos_by_owner`` via ``build_work_units``) with deterministic
fakes — never a hand-built, already-deduplicated RepoPolicy, which would
prove nothing about production behaviour.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agents.checker import auth, failures, harness
from agents.checker.budget import BudgetExhausted, RunBudgetCoordinator, usd_to_charged_eur_micros
from agents.checker.config import (
    MAX_MODEL_ATTEMPTS_PER_TASK,
    MAX_TOOL_CALLS_PER_CHECK,
    MAX_PER_CALL_RESERVE_EUR_MICROS,
    MAX_TURNS,
    MODEL,
    RUN_BUDGET_EUR_MICROS,
    SDK_ALLOWANCE_SAFETY_MARGIN,
)
from agents.checker.failures import QueryOutcome
from agents.checker.fx import FxRate
from agents.checker.harness import CagedCheckerStub, CheckerAgentError, TerminalAccountingError
from agents.checker.tools import (
    ACCEPTED,
    BREAKER_REFUSED,
    DOCUMENT_ABSENT,
    DUPLICATE,
    EVIDENCE_COUNT_MISMATCH,
    EVIDENCE_NOT_A_LIST,
    EXCERPT_EMPTY,
    EXCERPT_NOT_VERBATIM,
    LINE_OUT_OF_RANGE,
    MAX_PROPOSED_EXCERPT_CHARS,
    MAX_REASON_CODE_CHARS,
    REASON_CODE_NOT_ALLOWED,
    REJECTED,
    CheckerToolState,
    ToolAttemptRecord,
)
from checks.judgment.stubs import JudgmentRequest
from contracts.schemas import RunRecord
from sentinel import costs, ledger
from sentinel.config import FIXTURE_LINK_SCANNED_PATHS
from sentinel.inventory.base import RepoSurface, build_work_units
from sentinel.inventory.fixtures import FixtureSurfaceProvider
from sentinel.inventory.github_live import _derive_policy, build_repo_surfaces
from sentinel.inventory.site import SITE_OWNER, build_site_surface
from sentinel.net.client import FakeHttpClient, HttpResponse

# =====================================================================
# R10 - production logical-task grouping / deterministic attempt order
# =====================================================================
#
# task_key is derived in exactly one place, agents/checker/harness.py:
#     task_key = f"{request.surface}::{request.check_class}"
# and both judgment adapters build request.surface as
#     f"{ctx.owner}/{ctx.detail_path}"
# from the same work-unit scope. So (run_id, task_key) identifies one
# logical judgment task iff no single run can produce two judgment work
# units sharing (surface, check_class). That is what this section pins.

JUDGMENT_CLASSES = ("missing-synthetic-label", "stale-STATE-marker")

USER = "kobescak-kristian"


def _judgment_scopes(units):
    return [(u.surface, u.check_class) for u in units if u.check_class in JUDGMENT_CLASSES]


def _assert_unique_judgment_tasks(units, context: str):
    """The R10 assertion itself: judgment (surface, check_class) pairs
    are unique, and therefore so is the derived task_key."""
    scopes = _judgment_scopes(units)
    assert len(scopes) == len(set(scopes)), f"duplicate judgment scope in {context}: {scopes}"
    task_keys = [f"{surface}::{check_class}" for surface, check_class in scopes]
    assert len(task_keys) == len(set(task_keys)), f"duplicate task_key in {context}: {task_keys}"
    return task_keys


def _repos_page(repos, page=1):
    url = (
        f"https://api.github.com/users/{USER}/repos"
        f"?type=owner&sort=full_name&direction=asc&per_page=100&page={page}"
    )
    return url, HttpResponse(200, {}, json.dumps(repos).encode(), "")


def _empty_page(page):
    url = (
        f"https://api.github.com/users/{USER}/repos"
        f"?type=owner&sort=full_name&direction=asc&per_page=100&page={page}"
    )
    return url, HttpResponse(200, {}, b"[]", "")


def _tree_url(repo, branch="main"):
    return f"https://api.github.com/repos/{USER}/{repo}/git/trees/{branch}?recursive=1"


def _tree_response(paths):
    """A git-tree response that may legitimately repeat a blob path -
    the adversarial input the set-based constructor must absorb."""
    entries = [{"type": "blob", "path": p} for p in paths]
    return HttpResponse(200, {}, json.dumps({"tree": entries}).encode(), "")


# --- R10 A: fixture branch -------------------------------------------


def test_r10_a_fixture_link_scanned_paths_contain_no_duplicates():
    """The fixture/eval branch's path list is a hand-written literal in
    sentinel/config.py rather than a set-based constructor, so this
    direct pin is what keeps it duplicate-free under future edits."""
    assert len(FIXTURE_LINK_SCANNED_PATHS) == len(set(FIXTURE_LINK_SCANNED_PATHS))


def test_r10_a_real_fixture_provider_yields_unique_judgment_tasks(tmp_path):
    """Drives the real FixtureSurfaceProvider -> _fixture_policy ->
    build_work_units path, not a hand-built policy."""
    for owner in ("synthetic-01", "synthetic-02", "synthetic-03"):
        (tmp_path / owner).mkdir()
        (tmp_path / owner / "README.md").write_text("# r\n", encoding="utf-8")

    repos = FixtureSurfaceProvider(fixtures_root=tmp_path).repos()
    units = build_work_units(repos)

    task_keys = _assert_unique_judgment_tasks(units, "fixture provider")
    # Every fixture owner contributes exactly one stale-STATE-marker task.
    assert sum(k.endswith("::stale-STATE-marker") for k in task_keys) == 3


# --- R10 B: live production policy constructor ------------------------


def test_r10_b_live_derive_policy_dedupes_duplicate_markdown_paths():
    """Feeds _derive_policy (the real production constructor) a git
    tree that repeats blob paths. The set-based construction inside it
    is what must absorb them."""
    http = FakeHttpClient(
        responses={
            _tree_url("repo-a"): _tree_response(
                ["README.md", "README.md", "STATE.md", "STATE.md", "docs/guide.md"]
            )
        }
    )
    policy = _derive_policy(http, USER, "repo-a", "main", timeout=5.0, open_scopes=frozenset())
    paths = policy.link_scanned_paths
    assert len(paths) == len(set(paths)), paths
    assert "README.md" in paths and "STATE.md" in paths


def test_r10_b_live_derive_policy_dedupes_carry_forward_against_discovered():
    """Carry-forward (R10 D) through the real live constructor: an open
    scope naming a path the tree already discovered must not reappear
    as a second entry."""
    http = FakeHttpClient(
        responses={_tree_url("repo-a"): _tree_response(["README.md", "STATE.md"])}
    )
    open_scopes = frozenset(
        {
            ("repo-a/README.md", "missing-synthetic-label"),
            ("repo-a/README.md", "broken-link"),
            ("repo-a/STATE.md", "missing-synthetic-label"),
            ("repo-a/NOTES.md", "missing-synthetic-label"),
            ("other-repo/README.md", "missing-synthetic-label"),
        }
    )
    policy = _derive_policy(http, USER, "repo-a", "main", timeout=5.0, open_scopes=open_scopes)
    paths = policy.link_scanned_paths
    assert len(paths) == len(set(paths)), paths
    assert "NOTES.md" in paths  # carry-forward genuinely widens the scope
    # A scope belonging to a different repo never leaks into this policy.
    assert not any(p.startswith("other-repo") for p in paths)


def test_r10_b_live_build_repo_surfaces_yields_unique_judgment_tasks():
    """End-to-end through the public live entry point, including a repo
    name genuinely repeated across two pagination pages (R10 E).

    Pagination terminates on a short page, so page 1 must be full (100
    rows) for page 2 to be fetched at all - that is what makes the
    duplicate reach build_work_units unfiltered."""
    repo_row = {"name": "repo-a", "fork": False, "archived": False, "disabled": False}
    page_1 = [
        {"name": f"repo-{i:03d}", "fork": False, "archived": False, "disabled": False}
        for i in range(99)
    ] + [repo_row]
    responses = dict([_repos_page(page_1, 1), _repos_page([repo_row], 2)])
    responses[_tree_url("repo-a")] = _tree_response(["README.md", "README.md", "STATE.md"])
    http = FakeHttpClient(responses=responses)

    surfaces = build_repo_surfaces(http, USER, timeout=5.0)
    owners = [s.owner for s in surfaces]
    assert owners.count("repo-a") == 2, "the pagination duplicate must reach build_work_units"

    units = build_work_units(surfaces)
    task_keys = _assert_unique_judgment_tasks(units, "live build_repo_surfaces")
    # The doubled owner still yields exactly one stale-STATE-marker task.
    assert task_keys.count("repo-a/STATE.md::stale-STATE-marker") == 1


# --- R10 C: site production policy constructor ------------------------


def test_r10_c_site_surface_dedupes_duplicates_and_carry_forward():
    site_repo = f"{USER}.github.io"
    http = FakeHttpClient(
        responses={_tree_url(site_repo): _tree_response(["README.md", "README.md", "index.md"])}
    )
    open_scopes = frozenset(
        {
            (f"{SITE_OWNER}/index.md", "missing-synthetic-label"),
            (f"{SITE_OWNER}/about.md", "broken-link"),
            ("repo-a/README.md", "missing-synthetic-label"),
        }
    )
    surface = build_site_surface(http, USER, timeout=5.0, open_scopes=open_scopes)

    paths = surface.policy.link_scanned_paths
    assert len(paths) == len(set(paths)), paths
    assert "about.md" in paths
    assert surface.owner == SITE_OWNER

    units = build_work_units([surface])
    _assert_unique_judgment_tasks(units, "site surface")


# --- R10 E: duplicate owners collapse before task creation ------------


def test_r10_e_duplicate_owner_surfaces_collapse_before_task_creation():
    """_dedupe_repos_by_owner runs unconditionally inside
    build_work_units, before any WorkUnitSpec exists."""
    http = FakeHttpClient(
        responses={_tree_url("repo-a"): _tree_response(["README.md", "STATE.md"])}
    )
    policy = _derive_policy(http, USER, "repo-a", "main", timeout=5.0, open_scopes=frozenset())

    def _fetch(path):  # never called during work-unit construction
        raise AssertionError("fetch must not run during work-unit construction")

    duplicated = [
        RepoSurface(owner="repo-a", fetch=_fetch, policy=policy),
        RepoSurface(owner="repo-a", fetch=_fetch, policy=policy),
        RepoSurface(owner="repo-a", fetch=_fetch, policy=policy),
    ]
    units = build_work_units(duplicated)
    task_keys = _assert_unique_judgment_tasks(units, "tripled owner")
    assert sum(k.endswith("::stale-STATE-marker") for k in task_keys) == 1


# --- R10 F: combined uniqueness across every production branch --------


def test_r10_f_task_key_unique_across_every_production_branch(tmp_path):
    """The R10 conclusion: across fixture, live, site, carry-forward and
    doubled-owner inputs handled in ONE run, no two logical judgment
    tasks share (surface, check_class), so (run_id, task_key) identifies
    exactly one logical judgment task and agent_calls.id insertion order
    deterministically orders that task's attempts."""
    for owner in ("synthetic-01", "synthetic-02"):
        (tmp_path / owner).mkdir()
        (tmp_path / owner / "README.md").write_text("# r\n", encoding="utf-8")
    fixture_surfaces = list(FixtureSurfaceProvider(fixtures_root=tmp_path).repos())

    repo_row = {"name": "repo-a", "fork": False, "archived": False, "disabled": False}
    site_repo = f"{USER}.github.io"
    responses = dict([_repos_page([repo_row], 1), _repos_page([repo_row], 2), _empty_page(3)])
    responses[_tree_url("repo-a")] = _tree_response(["README.md", "README.md", "STATE.md"])
    responses[_tree_url(site_repo)] = _tree_response(["README.md", "index.md", "index.md"])
    http = FakeHttpClient(responses=responses)

    open_scopes = frozenset(
        {
            ("repo-a/README.md", "missing-synthetic-label"),
            ("repo-a/EXTRA.md", "missing-synthetic-label"),
            (f"{SITE_OWNER}/index.md", "missing-synthetic-label"),
        }
    )
    live_surfaces = build_repo_surfaces(http, USER, timeout=5.0, open_scopes=open_scopes)
    site_surface = build_site_surface(http, USER, timeout=5.0, open_scopes=open_scopes)

    units = build_work_units([*fixture_surfaces, *live_surfaces, site_surface])
    task_keys = _assert_unique_judgment_tasks(units, "combined production branches")

    # The union genuinely exercised every branch.
    assert any(k.startswith("synthetic-01/") for k in task_keys)
    assert any(k.startswith("repo-a/") for k in task_keys)
    assert any(k.startswith(f"{SITE_OWNER}/") for k in task_keys)
    assert "repo-a/EXTRA.md::missing-synthetic-label" in task_keys


# =====================================================================
# Section 9A - excerpt-retention sufficiency, decided by test
# =====================================================================
#
# ADR-0008 section 4 permits retaining a bounded proposed excerpt ONLY
# if implementation proves it necessary to distinguish "substantively
# correct proposal rejected by host contract" from "no usable
# proposal". These tests are that proof. They run the real
# CheckerToolState.accept() path, so the categories and coordinates are
# the ones production would actually persist.

_LABEL_CODE = "FIGURE_WITHOUT_ADJACENT_SYNTHETIC_LABEL"
_DOC = "# Results\n- Coverage: 85.5 percent\n- Runs: 12\n"

# The consumed re-gate's own frozen line is line 2 of _DOC.
_NEAR_MISS = "Coverage: 85.5 percnt"  # one character wrong: a substantive proposal
_FABRICATION = "the moon is made of green cheese"  # appears nowhere in the document


def _label_request(text=_DOC):
    return JudgmentRequest(
        surface="acme/EVAL_RESULTS.md",
        check_class="missing-synthetic-label",
        path="EVAL_RESULTS.md",
        text=text,
    )


def _reject_once(excerpt: str) -> ToolAttemptRecord:
    state = CheckerToolState(request=_label_request())
    result = state.accept(
        reason_code=_LABEL_CODE, raw_evidence=[{"line": 2, "excerpt": excerpt}]
    )
    assert result.get("is_error") is True
    assert state.findings == []
    assert len(state.attempts) == 1
    return state.attempts[0]


def _coordinate_only_projection(record: ToolAttemptRecord):
    """Exactly what a coordinates-and-category-only audit row would
    retain - the Case A candidate, with no proposed text at all."""
    return (
        record.proposed_reason_code,
        record.proposed_evidence_count,
        record.primary_line,
        record.secondary_line,
        record.outcome,
        record.rejection_category,
    )


def test_9a_coordinates_and_category_alone_lose_the_required_distinction():
    """Case A is INSUFFICIENT. A near-miss (a substantively correct
    proposal whose cited span is one character wrong) and an outright
    fabrication reject identically: same reason code, same coordinate,
    same closed category, same proposal count. With no retained text
    the two collapse into one indistinguishable audit row - precisely
    the distinction ADR-0008 section 4 requires to be reconstructible,
    and the one PHASE3_GATE_DIAGNOSIS.md recorded as not determinable
    from retained metadata."""
    near_miss = _reject_once(_NEAR_MISS)
    fabrication = _reject_once(_FABRICATION)

    assert near_miss.rejection_category == EXCERPT_NOT_VERBATIM
    assert fabrication.rejection_category == EXCERPT_NOT_VERBATIM
    assert _coordinate_only_projection(near_miss) == _coordinate_only_projection(fabrication)


def test_9a_bounded_snippet_restores_the_distinction():
    """Case B therefore applies, and the SMALLEST retention that
    restores the distinction is the one offending span, bounded."""
    near_miss = _reject_once(_NEAR_MISS)
    fabrication = _reject_once(_FABRICATION)

    assert near_miss.proposed_excerpt != fabrication.proposed_excerpt
    assert near_miss.proposed_excerpt == _NEAR_MISS
    assert fabrication.proposed_excerpt == _FABRICATION


def test_9a_snippet_is_retained_only_where_text_is_the_discriminator():
    """Minimum-necessary: no other outcome or category retains text."""
    state = CheckerToolState(request=_label_request())

    # Accepted proposal - the finding itself already carries the span.
    state.accept(reason_code=_LABEL_CODE, raw_evidence=[{"line": 2, "excerpt": "Coverage"}])
    # Wrong reason code - the category is the whole story.
    state.accept(reason_code="NOT_A_REAL_CODE", raw_evidence=[{"line": 2, "excerpt": "Coverage"}])
    # Out-of-range line - the coordinate is the whole story.
    state.accept(reason_code=_LABEL_CODE, raw_evidence=[{"line": 999, "excerpt": "Coverage"}])
    # Wrong evidence count - the count is the whole story.
    state.accept(reason_code=_LABEL_CODE, raw_evidence=[])

    by_outcome = {a.outcome: a for a in state.attempts}
    assert by_outcome[ACCEPTED].proposed_excerpt is None
    categories = {a.rejection_category for a in state.attempts if a.outcome == REJECTED}
    assert categories == {REASON_CODE_NOT_ALLOWED, LINE_OUT_OF_RANGE, EVIDENCE_COUNT_MISMATCH}
    for attempt in state.attempts:
        if attempt.rejection_category != EXCERPT_NOT_VERBATIM:
            assert attempt.proposed_excerpt is None


def test_9a_snippet_is_deterministically_bounded():
    long_excerpt = "z" * 500
    record = _reject_once(long_excerpt)
    assert len(record.proposed_excerpt) == MAX_PROPOSED_EXCERPT_CHARS
    assert record.proposed_excerpt == "z" * MAX_PROPOSED_EXCERPT_CHARS
    # Deterministic: the same proposal truncates to the same value.
    assert _reject_once(long_excerpt).proposed_excerpt == record.proposed_excerpt


def test_9a_control_characters_never_reach_the_audit_record():
    record = _reject_once("Cover\x00age\x07 85.5")
    assert "\x00" not in record.proposed_excerpt
    assert "\x07" not in record.proposed_excerpt


def test_9a_rejection_reason_no_longer_carries_unbounded_model_prose():
    """The old leak: EvidenceRejected prose embeds the proposed excerpt
    verbatim and unbounded, and it used to reach
    agent_calls.rejection_reason. The persisted value is now the closed
    category."""
    state = CheckerToolState(request=_label_request())
    state.accept(reason_code=_LABEL_CODE, raw_evidence=[{"line": 2, "excerpt": _FABRICATION}])
    assert state.last_rejection_reason == EXCERPT_NOT_VERBATIM
    assert _FABRICATION not in state.last_rejection_reason


def test_9a_reason_code_is_bounded_before_persistence():
    state = CheckerToolState(request=_label_request())
    state.accept(reason_code="A" * 300, raw_evidence=[{"line": 2, "excerpt": "Coverage"}])
    assert len(state.attempts[0].proposed_reason_code) == MAX_REASON_CODE_CHARS


def test_duplicate_and_breaker_outcomes_are_recorded():
    state = CheckerToolState(request=_label_request())
    for _ in range(2):
        state.accept(reason_code=_LABEL_CODE, raw_evidence=[{"line": 2, "excerpt": "Coverage"}])
    assert [a.outcome for a in state.attempts] == [ACCEPTED, DUPLICATE]
    assert len(state.findings) == 1

    # Drive past the tool-call ceiling to reach the breaker branch.
    while not state.breaker_tripped():
        state.accept(reason_code=_LABEL_CODE, raw_evidence=[{"line": 2, "excerpt": "Runs"}])
    assert state.attempts[-1].outcome == BREAKER_REFUSED
    # Ordinals are contiguous and 1-based within one invocation.
    assert [a.ordinal for a in state.attempts] == list(range(1, len(state.attempts) + 1))


# --- classifier consistency with the untouched evidence.py ------------


def test_rejection_classifier_matches_real_evidence_validation():
    """classify_rejection must not drift from evidence.py, which this
    dispatch does not modify. Every case is driven through the real
    accept() path."""
    cases = [
        ("WRONG_CODE", [{"line": 2, "excerpt": "Coverage"}], REASON_CODE_NOT_ALLOWED),
        (_LABEL_CODE, [], EVIDENCE_COUNT_MISMATCH),
        (
            _LABEL_CODE,
            [{"line": 2, "excerpt": "Coverage"}, {"line": 3, "excerpt": "Runs"}],
            EVIDENCE_COUNT_MISMATCH,
        ),
        (_LABEL_CODE, [{"line": 99, "excerpt": "Coverage"}], LINE_OUT_OF_RANGE),
        (_LABEL_CODE, [{"line": 0, "excerpt": "Coverage"}], LINE_OUT_OF_RANGE),
        (_LABEL_CODE, [{"line": 2, "excerpt": ""}], EXCERPT_EMPTY),
        (_LABEL_CODE, [{"line": 2, "excerpt": _FABRICATION}], EXCERPT_NOT_VERBATIM),
        (_LABEL_CODE, [{"line": 3, "excerpt": "Coverage"}], EXCERPT_NOT_VERBATIM),
    ]
    for reason_code, raw_evidence, expected in cases:
        state = CheckerToolState(request=_label_request())
        response = state.accept(reason_code=reason_code, raw_evidence=raw_evidence)
        assert response.get("is_error") is True, (reason_code, raw_evidence)
        assert state.attempts[0].rejection_category == expected, (reason_code, raw_evidence)
        assert state.findings == []


def test_rejection_classifier_handles_absent_document_and_bad_payload():
    state = CheckerToolState(request=_label_request(text=None))
    state.accept(reason_code=_LABEL_CODE, raw_evidence=[{"line": 1, "excerpt": "x"}])
    assert state.attempts[0].rejection_category == DOCUMENT_ABSENT

    state = CheckerToolState(request=_label_request())
    state.accept(reason_code=_LABEL_CODE, raw_evidence="not-a-list")
    assert state.attempts[0].rejection_category == EVIDENCE_NOT_A_LIST
    assert state.attempts[0].proposed_evidence_count == 0


# =====================================================================
# Harness proof scaffolding - model-free, network-free
# =====================================================================

T0 = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)

_FAKE_RATE = FxRate(
    source="ecb-eurofxref-daily",
    rate_date="2026-08-21",
    retrieved_at_utc=T0,
    usd_per_eur=Decimal("1.1554"),
)

_STALE_CODE = "DATED_ENTRY_CONTRADICTS_CURRENT_STATE"


class TooManyInvocations(BaseException):
    """Deliberately a BaseException, not an Exception: the harness
    catches Exception around every SDK invocation, so an Exception here
    would be absorbed into the taxonomy and silently pass. This must
    escape and fail the test."""


def _coordinator(total_eur_micros: int = RUN_BUDGET_EUR_MICROS) -> RunBudgetCoordinator:
    return RunBudgetCoordinator(fx_rate=_FAKE_RATE, total_eur_micros=total_eur_micros)


@pytest.fixture
def ledger_conn(tmp_path):
    conn = ledger.open_ledger(tmp_path / "sentinel.sqlite3")
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
    yield conn
    conn.close()


def _request(check_class="missing-synthetic-label", text=_DOC):
    return JudgmentRequest(
        surface="acme/EVAL_RESULTS.md",
        check_class=check_class,
        path="EVAL_RESULTS.md",
        text=text,
    )


def _result(**overrides):
    """A ResultMessage-shaped double. The harness reads it only through
    getattr, exactly as it reads the real SDK type."""
    defaults = dict(
        is_error=False,
        subtype="success",
        num_turns=2,
        total_cost_usd=0.001,
        usage={"input_tokens": 100, "output_tokens": 20},
        result="done",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _budget_ceiling_result(total_cost_usd=0.1226):
    """The exact terminal shape the pinned SDK emits when it halts a
    call at max_budget_usd: typed subtype, is_error, and the accumulated
    cost/usage that used to be lost with the stream exception."""
    return _result(
        is_error=True,
        subtype=failures.SDK_BUDGET_CEILING_SUBTYPE,
        num_turns=3,
        total_cost_usd=total_cost_usd,
        usage={"input_tokens": 900, "output_tokens": 40},
        result=None,
    )


#: The trailing exception the SDK raises after a budget-ceiling result:
#: a plain, untyped Exception whose prose merely quotes the CLI text.
_TRAILING_BUDGET_EXCEPTION = Exception(
    "Claude Code returned an error result: Reached maximum budget ($0.1226)"
)


def _emit_label(state, excerpt="Coverage: 85.5 percent", line=2):
    state.accept(reason_code=_LABEL_CODE, raw_evidence=[{"line": line, "excerpt": excerpt}])


class ScriptedQuery:
    """One scripted step per ACTUAL SDK invocation. Running out of
    steps raises TooManyInvocations, so an unexpected extra model call
    can never be absorbed by the failure taxonomy."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def __call__(self, check_class, reservation, state, user_prompt):
        self.calls.append(
            SimpleNamespace(
                check_class=check_class,
                reservation=reservation,
                state=state,
                user_prompt=user_prompt,
            )
        )
        if len(self.calls) > len(self.steps):
            raise TooManyInvocations(f"invocation {len(self.calls)} was not scripted")
        step = self.steps[len(self.calls) - 1]
        return step(state) if callable(step) else step

    @property
    def invocations(self) -> int:
        return len(self.calls)


def _step(outcome, emits=()):
    def _run(state):
        for excerpt in emits:
            _emit_label(state, excerpt)
        return outcome
    return _run


def _stub(ledger_conn, query, coordinator=None, run_id="r-1"):
    coordinator = coordinator or _coordinator()
    with patch("agents.checker.harness.auth.assert_no_auth_override_risk", return_value=None):
        return CagedCheckerStub(
            run_id=run_id,
            conn=ledger_conn,
            coordinator=coordinator,
            clock=lambda: T0,
            query_fn=query,
        )


def _judge(stub, request=None):
    with patch("agents.checker.harness.auth.assert_no_auth_override_risk", return_value=None):
        return stub.judge(request or _request())


def _rows(conn, run_id="r-1"):
    return ledger.list_agent_calls_for_run(conn, run_id)


# =====================================================================
# R1-R5, R7-R9 - bounded re-execution contract
# =====================================================================


def test_r1_budget_ceiling_retries_exactly_once_and_succeeds(ledger_conn):
    query = ScriptedQuery(
        _step(QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION)),
        _step(_result(), emits=["Coverage: 85.5 percent"]),
    )
    stub = _stub(ledger_conn, query)
    findings = _judge(stub)

    assert query.invocations == 2
    assert len(findings) == 1
    rows = _rows(ledger_conn)
    assert [r.state for r in rows] == ["FAILED", "COMPLETED"]
    assert rows[0].sdk_subtype == failures.SDK_BUDGET_CEILING_SUBTYPE


def test_r2_second_invocation_failure_yields_no_third(ledger_conn):
    query = ScriptedQuery(
        _step(QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION)),
        _step(QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION)),
    )
    stub = _stub(ledger_conn, query)
    with pytest.raises(CheckerAgentError):
        _judge(stub)

    assert query.invocations == 2
    assert [r.state for r in _rows(ledger_conn)] == ["FAILED", "FAILED"]


NON_RETRYABLE_CASES = [
    ("sdk_result_error_other", _step(_result(is_error=True, subtype="error_during_execution"))),
    ("untyped_transport_exception", _step(QueryOutcome(result=None, error=RuntimeError("boom")))),
    (
        "budget_prose_without_typed_result",
        _step(QueryOutcome(result=None, error=_TRAILING_BUDGET_EXCEPTION)),
    ),
    ("no_result_message", _step(None)),
]


@pytest.mark.parametrize("label,step", NON_RETRYABLE_CASES, ids=[c[0] for c in NON_RETRYABLE_CASES])
def test_r3_non_retryable_classes_never_reach_a_second_invocation(ledger_conn, label, step):
    query = ScriptedQuery(step)
    stub = _stub(ledger_conn, query)
    with pytest.raises(CheckerAgentError):
        _judge(stub)
    assert query.invocations == 1, label
    assert [r.state for r in _rows(ledger_conn)] == ["FAILED"]


def test_r3_tool_breaker_is_non_retryable(ledger_conn):
    def _spam(state):
        for _ in range(MAX_TOOL_CALLS_PER_CHECK + 1):
            _emit_label(state)
        return _result()

    query = ScriptedQuery(_spam)
    stub = _stub(ledger_conn, query)
    with pytest.raises(CheckerAgentError):
        _judge(stub)
    assert query.invocations == 1
    assert _rows(ledger_conn)[0].state == "FAILED"


def test_r3_auth_refusal_makes_zero_invocations(ledger_conn):
    query = ScriptedQuery()  # any invocation at all raises TooManyInvocations
    coordinator = _coordinator()
    stub = CagedCheckerStub(
        run_id="r-1",
        conn=ledger_conn,
        coordinator=coordinator,
        clock=lambda: T0,
        query_fn=query,
    )
    with patch("agents.checker.harness.auth.assert_no_auth_override_risk") as check:
        check.side_effect = auth.AuthOverrideRisk("ANTHROPIC_API_KEY is set")
        with pytest.raises(CheckerAgentError):
            stub.judge(_request())

    assert query.invocations == 0
    rows = _rows(ledger_conn)
    assert [r.state for r in rows] == ["REJECTED"]
    assert rows[0].reserved_eur_micros == 0
    # Nothing was ever reserved with the coordinator.
    assert coordinator.remaining_eur_micros() == RUN_BUDGET_EUR_MICROS


def test_r3_pre_call_budget_exhaustion_makes_zero_invocations(ledger_conn):
    query = ScriptedQuery()
    coordinator = _coordinator()
    # Drain the run budget before any judgment call.
    while True:
        try:
            reservation = coordinator.reserve()
        except BudgetExhausted:
            break
        coordinator.commit(reservation, charged_eur_micros=reservation.reserved_eur_micros)

    stub = _stub(ledger_conn, query, coordinator=coordinator)
    with pytest.raises(CheckerAgentError):
        _judge(stub)

    assert query.invocations == 0
    assert [r.state for r in _rows(ledger_conn)] == ["EXHAUSTED"]


def test_r3_host_evidence_rejection_is_not_an_execution_failure(ledger_conn):
    """HOST_EVIDENCE_REJECTION is a measured judgment-quality outcome:
    the call still COMPLETES, returns no finding, and never retries."""
    query = ScriptedQuery(_step(_result(), emits=[_FABRICATION]))
    stub = _stub(ledger_conn, query)
    findings = _judge(stub)

    assert query.invocations == 1
    assert findings == ()
    row = _rows(ledger_conn)[0]
    assert row.state == "COMPLETED"
    assert row.accepted is False
    assert row.rejection_reason == EXCERPT_NOT_VERBATIM


def test_r3_missing_final_cost_does_not_retry(ledger_conn):
    query = ScriptedQuery(_step(_result(total_cost_usd=None), emits=["Coverage: 85.5 percent"]))
    stub = _stub(ledger_conn, query)
    findings = _judge(stub)
    assert query.invocations == 1
    assert len(findings) == 1
    assert _rows(ledger_conn)[0].state == "COMPLETED"


def test_r3_reported_cost_overshoot_does_not_retry(ledger_conn):
    query = ScriptedQuery(_step(_result(total_cost_usd=0.30), emits=["Coverage: 85.5 percent"]))
    stub = _stub(ledger_conn, query)
    findings = _judge(stub)
    assert query.invocations == 1
    assert len(findings) == 1
    row = _rows(ledger_conn)[0]
    assert row.state == "COMPLETED"
    assert row.charged_eur_micros > row.reserved_eur_micros


def test_r3_retryable_set_has_cardinality_one():
    assert failures.RETRYABLE_FAILURE_CLASSES == {failures.SDK_BUDGET_CEILING}
    for name in failures.ALL_FAILURE_CLASSES - failures.RETRYABLE_FAILURE_CLASSES:
        assert not failures.is_retryable(name), name


def test_r4_insufficient_capacity_after_retryable_failure_fails_closed(ledger_conn):
    """One reservation of capacity only: the first call fails at the
    SDK budget ceiling, the retry cannot be funded, so no second SDK
    invocation happens and the judgment fails closed."""
    coordinator = _coordinator(total_eur_micros=MAX_PER_CALL_RESERVE_EUR_MICROS)
    query = ScriptedQuery(
        _step(QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION))
    )
    stub = _stub(ledger_conn, query, coordinator=coordinator)
    with pytest.raises(CheckerAgentError):
        _judge(stub)

    assert query.invocations == 1
    states = [r.state for r in _rows(ledger_conn)]
    assert states == ["FAILED", "EXHAUSTED"]
    # The EXHAUSTED row records a pre-call refusal, not an invocation.
    assert _rows(ledger_conn)[1].reserved_eur_micros == 0


def test_r5_first_failed_row_stays_auditable_after_a_successful_retry(ledger_conn):
    query = ScriptedQuery(
        _step(
            QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION),
            emits=["Coverage: 85.5 percent"],
        ),
        _step(_result(), emits=["Coverage: 85.5 percent"]),
    )
    stub = _stub(ledger_conn, query)
    _judge(stub)

    rows = _rows(ledger_conn)
    assert len(rows) == 2
    failed, completed = rows
    assert failed.state == "FAILED"
    assert failed.task_key == completed.task_key == "acme/EVAL_RESULTS.md::missing-synthetic-label"
    assert failed.finished_at_utc is not None
    # A FAILED call stays conservatively unaccepted at call level even
    # though one of its proposals passed host validation.
    assert failed.accepted is False
    # ...and that accepted proposal survives in the per-attempt audit.
    attempts = ledger.list_tool_attempts_for_call(ledger_conn, failed.id)
    assert [a.outcome for a in attempts] == [ACCEPTED]


def test_r6_all_four_proposal_outcomes_persist_with_their_evidence(ledger_conn):
    def _mixed(state):
        _emit_label(state)  # ACCEPTED
        _emit_label(state)  # DUPLICATE
        state.accept(reason_code=_LABEL_CODE, raw_evidence=[{"line": 2, "excerpt": _NEAR_MISS}])
        while not state.breaker_tripped():  # BREAKER_REFUSED
            _emit_label(state, excerpt="Runs", line=3)
        return _result()

    query = ScriptedQuery(_mixed)
    stub = _stub(ledger_conn, query)
    with pytest.raises(CheckerAgentError):  # the breaker trips the call
        _judge(stub)

    call_id = _rows(ledger_conn)[0].id
    attempts = ledger.list_tool_attempts_for_call(ledger_conn, call_id)
    outcomes = [a.outcome for a in attempts]
    assert outcomes[:3] == [ACCEPTED, DUPLICATE, REJECTED]
    assert BREAKER_REFUSED in outcomes
    assert [a.ordinal for a in attempts] == list(range(1, len(attempts) + 1))

    rejected = attempts[2]
    assert rejected.rejection_category == EXCERPT_NOT_VERBATIM
    assert rejected.proposed_reason_code == _LABEL_CODE
    assert rejected.primary_line == 2
    assert rejected.proposed_evidence_count == 1
    assert rejected.proposed_excerpt == _NEAR_MISS


def test_r7_findings_from_a_failed_invocation_never_become_live(ledger_conn):
    query = ScriptedQuery(
        _step(
            QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION),
            emits=["Coverage: 85.5 percent"],
        ),
        _step(QueryOutcome(result=_result(is_error=True, subtype="error_during_execution"))),
    )
    stub = _stub(ledger_conn, query)
    with pytest.raises(CheckerAgentError):
        _judge(stub)
    # judge() raised, so the pipeline records Inconclusive -> DEAD_LETTER
    # and lifecycle.apply_observed is never reached for this task.
    assert ledger.list_open_findings(ledger_conn) == []


def test_r8_actual_invocations_never_exceed_the_bound(ledger_conn):
    assert MAX_MODEL_ATTEMPTS_PER_TASK == 2
    query = ScriptedQuery(
        _step(QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION)),
        _step(QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION)),
    )
    stub = _stub(ledger_conn, query)
    with pytest.raises(CheckerAgentError):
        _judge(stub)
    assert query.invocations == MAX_MODEL_ATTEMPTS_PER_TASK


def test_r9_both_invocations_share_one_continuous_run_budget(ledger_conn):
    coordinator = _coordinator()
    query = ScriptedQuery(
        _step(QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION)),
        _step(_result(total_cost_usd=0.05), emits=["Coverage: 85.5 percent"]),
    )
    stub = _stub(ledger_conn, query, coordinator=coordinator)
    _judge(stub)

    # One pool: both reservations came from it, and neither invocation
    # was handed a second coordinator.
    assert query.calls[0].state is not query.calls[1].state
    assert stub.coordinator is coordinator
    rows = _rows(ledger_conn)
    total = sum(r.charged_eur_micros for r in rows)
    assert coordinator.total_charged_eur_micros() == total
    assert coordinator.remaining_eur_micros() == RUN_BUDGET_EUR_MICROS - total


# =====================================================================
# R11-R18 - the four cost cases, overshoot and fail-closed accounting
# =====================================================================

RESERVATION = MAX_PER_CALL_RESERVE_EUR_MICROS


def _converted(usd) -> int:
    return usd_to_charged_eur_micros(Decimal(str(usd)), _FAKE_RATE)


def _run_one(ledger_conn, *, completed: bool, total_cost_usd, coordinator=None):
    """Exactly one invocation, completing or failing non-retryably, with
    the given recoverable SDK cost estimate."""
    if completed:
        step = _step(_result(total_cost_usd=total_cost_usd), emits=["Coverage: 85.5 percent"])
    else:
        step = _step(
            _result(
                is_error=True, subtype="error_during_execution", total_cost_usd=total_cost_usd
            )
        )
    query = ScriptedQuery(step)
    stub = _stub(ledger_conn, query, coordinator=coordinator)
    if completed:
        _judge(stub)
    else:
        with pytest.raises(CheckerAgentError):
            _judge(stub)
    assert query.invocations == 1
    row = _rows(ledger_conn)[0]
    assert row.reserved_eur_micros == RESERVATION
    return row, stub.coordinator


def test_r11_completed_estimate_below_reservation_charges_the_estimate(ledger_conn):
    usd = 0.05
    assert _converted(usd) < RESERVATION
    row, coordinator = _run_one(ledger_conn, completed=True, total_cost_usd=usd)
    assert row.state == "COMPLETED"
    assert row.charged_eur_micros == _converted(usd)
    # The unused remainder of the reservation is released.
    assert coordinator.remaining_eur_micros() == RUN_BUDGET_EUR_MICROS - _converted(usd)


def test_r12_completed_estimate_equal_to_reservation_charges_that_amount(ledger_conn):
    usd = "0.17331"  # exactly 150000 micro-EUR at the fixed test rate
    assert _converted(usd) == RESERVATION
    row, _ = _run_one(ledger_conn, completed=True, total_cost_usd=usd)
    assert row.charged_eur_micros == RESERVATION


def test_r13_completed_estimate_above_reservation_is_never_clamped(ledger_conn):
    usd = 0.30
    expected = _converted(usd)
    assert expected > RESERVATION
    row, coordinator = _run_one(ledger_conn, completed=True, total_cost_usd=usd)
    assert row.state == "COMPLETED"
    # The defect ADR-0008 removes was min(estimate, reservation).
    assert row.charged_eur_micros == expected
    assert row.charged_eur_micros != RESERVATION
    assert coordinator.total_charged_eur_micros() == expected


def test_r14_failed_estimate_below_reservation_burns_the_full_reservation(ledger_conn):
    usd = 0.05
    assert _converted(usd) < RESERVATION
    row, _ = _run_one(ledger_conn, completed=False, total_cost_usd=usd)
    assert row.state == "FAILED"
    # A failed call never becomes cheaper than its reservation.
    assert row.charged_eur_micros == RESERVATION


def test_r15_failed_estimate_above_reservation_charges_the_full_estimate(ledger_conn):
    usd = 0.30
    expected = _converted(usd)
    assert expected > RESERVATION
    row, _ = _run_one(ledger_conn, completed=False, total_cost_usd=usd)
    assert row.state == "FAILED"
    assert row.charged_eur_micros == expected


def test_r16_failed_without_recoverable_cost_burns_the_full_reservation(ledger_conn):
    row, _ = _run_one(ledger_conn, completed=False, total_cost_usd=None)
    assert row.state == "FAILED"
    assert row.charged_eur_micros == RESERVATION
    assert row.usd_cost_estimate is None


def test_r17_completed_without_recoverable_cost_burns_the_full_reservation(ledger_conn):
    row, _ = _run_one(ledger_conn, completed=True, total_cost_usd=None)
    assert row.state == "COMPLETED"
    assert row.charged_eur_micros == RESERVATION


def test_r18_overshoot_drives_remaining_negative_and_reserve_fails_closed(ledger_conn, tmp_path):
    usd = 0.90  # more than the whole run budget once converted
    expected = _converted(usd)
    assert expected > RUN_BUDGET_EUR_MICROS
    coordinator = _coordinator()
    row, coordinator = _run_one(
        ledger_conn, completed=True, total_cost_usd=usd, coordinator=coordinator
    )

    assert row.charged_eur_micros == expected
    assert coordinator.remaining_eur_micros() < 0
    with pytest.raises(BudgetExhausted):
        coordinator.reserve()

    # The aggregate CostRow records the honest sum, overshoot included.
    cost_row = costs.build_agent_cost_row(
        ledger_conn, run_id="r-1", run_kind="dev", recorded_at_utc=T0
    )
    assert cost_row.cost_eur_micros == expected
    assert cost_row.cost_eur_micros > RUN_BUDGET_EUR_MICROS


def test_r18_cost_row_sums_both_rows_after_a_retry(ledger_conn):
    query = ScriptedQuery(
        _step(QueryOutcome(result=_budget_ceiling_result(0.1226), error=_TRAILING_BUDGET_EXCEPTION)),
        _step(_result(total_cost_usd=0.05), emits=["Coverage: 85.5 percent"]),
    )
    stub = _stub(ledger_conn, query)
    _judge(stub)

    rows = _rows(ledger_conn)
    # Failed attempt: max(reservation, estimate) = the reservation here.
    assert rows[0].charged_eur_micros == RESERVATION
    assert rows[1].charged_eur_micros == _converted(0.05)
    cost_row = costs.build_agent_cost_row(
        ledger_conn, run_id="r-1", run_kind="dev", recorded_at_utc=T0
    )
    assert cost_row.cost_eur_micros == RESERVATION + _converted(0.05)
    # Token counts from BOTH attempts are aggregated honestly.
    assert cost_row.input_tokens == 900 + 100


# =====================================================================
# R19 - two invocations never mint two live findings
# =====================================================================


def test_r19_retry_emitting_the_same_defect_yields_exactly_one_live_finding(ledger_conn):
    from sentinel import lifecycle

    query = ScriptedQuery(
        _step(
            QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION),
            emits=["Coverage: 85.5 percent"],
        ),
        # A different valid span of the SAME line: one semantic defect.
        _step(_result(), emits=["- Coverage: 85.5 percent"]),
    )
    stub = _stub(ledger_conn, query)
    findings = _judge(stub)

    assert query.invocations == 2
    assert len(findings) == 1
    # Only the successful invocation's finding is returned.
    assert findings[0].detail.endswith("'- Coverage: 85.5 percent'")

    with ledger.unit_of_work(ledger_conn):
        lifecycle.apply_observed(ledger_conn, "r-1", T0, findings)
    open_rows = ledger.list_open_findings(ledger_conn)
    assert len(open_rows) == 1

    # The failed attempt's accepted proposal exists only as audit.
    failed_id = _rows(ledger_conn)[0].id
    assert [a.outcome for a in ledger.list_tool_attempts_for_call(ledger_conn, failed_id)] == [
        ACCEPTED
    ]


# =====================================================================
# R23 - cross-layer terminal durability, failure-injected
# =====================================================================


class InjectedPersistenceFailure(Exception):
    pass


@pytest.mark.parametrize("target", ["insert_tool_attempts", "finalize_agent_call"])
def test_r23_terminal_persistence_failure_rolls_back_and_holds_the_reservation(
    ledger_conn, monkeypatch, target
):
    """Both legs of the terminal transaction, injected separately.

    A: the agent_calls row stays RESERVED (the transaction rolled back).
    B: zero partial tool-attempt rows survive.
    C: the coordinator has NOT released or finalized the reservation.
    D: remaining capacity is exactly the post-reservation figure.
    E: no retry or second SDK invocation follows.
    F: the logical judgment fails closed.
    """
    coordinator = _coordinator()
    query = ScriptedQuery(_step(_result(), emits=["Coverage: 85.5 percent"]))
    stub = _stub(ledger_conn, query, coordinator=coordinator)

    def _boom(*args, **kwargs):
        raise InjectedPersistenceFailure(target)

    monkeypatch.setattr(ledger, target, _boom)

    with pytest.raises(InjectedPersistenceFailure):  # F - fails closed
        _judge(stub)

    rows = _rows(ledger_conn)
    assert [r.state for r in rows] == ["RESERVED"]  # A
    assert rows[0].finished_at_utc is None
    assert ledger.list_tool_attempts_for_call(ledger_conn, rows[0].id) == []  # B
    assert coordinator.total_charged_eur_micros() == 0  # C
    assert coordinator.remaining_eur_micros() == RUN_BUDGET_EUR_MICROS - RESERVATION  # D
    assert query.invocations == 1  # E


def test_r23_happy_path_terminalizes_durably_before_budget_advances(ledger_conn):
    """The ordering contract: when the coordinator is asked to account
    a charge, the ledger transaction has already committed."""
    coordinator = _coordinator()
    observed = {}
    real_commit = coordinator.commit

    def _spy(reservation, *, charged_eur_micros):
        row = _rows(ledger_conn)[0]
        observed["state"] = row.state
        observed["charged"] = row.charged_eur_micros
        observed["attempts"] = len(ledger.list_tool_attempts_for_call(ledger_conn, row.id))
        observed["calls"] = observed.get("calls", 0) + 1
        return real_commit(reservation, charged_eur_micros=charged_eur_micros)

    coordinator.commit = _spy
    query = ScriptedQuery(_step(_result(total_cost_usd=0.05), emits=["Coverage: 85.5 percent"]))
    stub = _stub(ledger_conn, query, coordinator=coordinator)
    _judge(stub)

    assert observed["state"] == "COMPLETED"
    assert observed["charged"] == _converted(0.05)
    assert observed["attempts"] == 1
    assert observed["calls"] == 1  # accounted exactly once


# =====================================================================
# R24 - process-crash semantics are unchanged
# =====================================================================


def test_r24_crash_reserved_row_is_reconciled_at_its_reservation(ledger_conn):
    """A call whose host process died mid-invocation leaves a RESERVED
    row. ADR-0008 changes nothing here and claims no crash-proof
    per-tool telemetry: the buffered attempt audit is simply absent."""
    with ledger.unit_of_work(ledger_conn):
        call_id = ledger.insert_agent_call_reserved(
            ledger_conn,
            run_id="r-1",
            task_key="acme/EVAL_RESULTS.md::missing-synthetic-label",
            surface="acme/EVAL_RESULTS.md",
            check_class="missing-synthetic-label",
            model=MODEL,
            auth_mode="operator-subscription-oauth-assumed",
            started_at_utc=T0,
            reserved_eur_micros=RESERVATION,
            fx_source=_FAKE_RATE.source,
            fx_rate_date=_FAKE_RATE.rate_date,
            fx_retrieved_at_utc=_FAKE_RATE.retrieved_at_utc,
            fx_rate_decimal=str(_FAKE_RATE.usd_per_eur),
        )

    unresolved = ledger.unresolved_agent_calls(ledger_conn, "r-1")
    assert [r.id for r in unresolved] == [call_id]
    assert ledger.list_tool_attempts_for_call(ledger_conn, call_id) == []

    cost_row = costs.build_agent_cost_row(
        ledger_conn, run_id="r-1", run_kind="dev", recorded_at_utc=T0
    )
    assert cost_row.cost_eur_micros == RESERVATION  # charged, never zero


# =====================================================================
# Boundary tests A-J (implementation dispatch section 16)
# =====================================================================


def test_boundary_a_typed_budget_result_survives_the_trailing_exception():
    outcome = QueryOutcome(
        result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION
    )
    assert failures.classify_invocation(outcome, breaker_tripped=False) == (
        failures.SDK_BUDGET_CEILING
    )
    assert failures.is_retryable(failures.SDK_BUDGET_CEILING)


def test_boundary_b_same_prose_without_a_typed_result_is_non_retryable():
    outcome = QueryOutcome(result=None, error=_TRAILING_BUDGET_EXCEPTION)
    klass = failures.classify_invocation(outcome, breaker_tripped=False)
    assert klass == failures.TRANSPORT_PROCESS_SDK_EXCEPTION_WITHOUT_CAPTURED_TYPED_RESULT
    assert not failures.is_retryable(klass)
    # The decisive point: the prose alone says "Reached maximum budget".
    assert "Reached maximum budget" in str(_TRAILING_BUDGET_EXCEPTION)


def test_boundary_c_a_different_typed_subtype_is_non_retryable():
    outcome = QueryOutcome(result=_result(is_error=True, subtype="error_max_turns"))
    klass = failures.classify_invocation(outcome, breaker_tripped=False)
    assert klass == failures.SDK_RESULT_ERROR_OTHER
    assert not failures.is_retryable(klass)


def test_boundary_d_containment_wins_over_a_coexisting_budget_subtype(ledger_conn):
    """A tripped breaker must never be promoted into a retry by a
    budget subtype that happens to arrive with it."""
    outcome = QueryOutcome(
        result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION
    )
    assert failures.classify_invocation(outcome, breaker_tripped=True) == failures.TOOL_BREAKER

    def _spam_then_ceiling(state):
        for _ in range(MAX_TOOL_CALLS_PER_CHECK + 1):
            _emit_label(state)
        return outcome

    query = ScriptedQuery(_spam_then_ceiling)
    stub = _stub(ledger_conn, query)
    with pytest.raises(CheckerAgentError):
        _judge(stub)
    assert query.invocations == 1  # no retry


def test_boundary_e_both_invocations_receive_identical_inputs(ledger_conn):
    query = ScriptedQuery(
        _step(QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION)),
        _step(_result(), emits=["Coverage: 85.5 percent"]),
    )
    stub = _stub(ledger_conn, query)
    request = _request()
    _judge(stub, request)

    first, second = query.calls
    assert first.check_class == second.check_class
    assert first.user_prompt == second.user_prompt
    # Same cage values on both; only the SDK budget allowance is
    # derived from whatever capacity remained.
    from agents.checker.harness import build_options

    opts_1 = build_options(first.check_class, first.reservation)
    opts_2 = build_options(second.check_class, second.reservation)
    assert opts_1.model == opts_2.model == MODEL
    assert opts_1.system_prompt == opts_2.system_prompt
    assert opts_1.max_turns == opts_2.max_turns == MAX_TURNS
    assert opts_1.allowed_tools == opts_2.allowed_tools
    assert opts_1.setting_sources == opts_2.setting_sources == []
    # The retry is never handed a larger reservation.
    assert second.reservation.reserved_eur_micros <= first.reservation.reserved_eur_micros


def test_boundary_f_no_second_coordinator_is_created(ledger_conn):
    coordinator = _coordinator()
    query = ScriptedQuery(
        _step(QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION)),
        _step(_result(), emits=["Coverage: 85.5 percent"]),
    )
    stub = _stub(ledger_conn, query, coordinator=coordinator)
    _judge(stub)
    assert stub.coordinator is coordinator
    # Reservations were drawn from the one pool, in sequence.
    assert coordinator.total_charged_eur_micros() > 0


def test_boundary_g_a_successful_retry_never_hides_the_first_failed_call(ledger_conn):
    query = ScriptedQuery(
        _step(QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION)),
        _step(_result(), emits=["Coverage: 85.5 percent"]),
    )
    stub = _stub(ledger_conn, query)
    _judge(stub)

    rows = _rows(ledger_conn)
    assert len(rows) == 2
    assert rows[0].id < rows[1].id  # deterministic attempt order
    assert rows[0].state == "FAILED"
    # The audit row is append-only in practice: deleting it is refused.
    with pytest.raises(sqlite3.IntegrityError):
        ledger_conn.execute("DELETE FROM agent_calls WHERE id = ?", (rows[0].id,))


def test_boundary_i_tool_attempt_ordinals_restart_per_invocation(ledger_conn):
    query = ScriptedQuery(
        _step(
            QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION),
            emits=["Coverage: 85.5 percent", "Runs"],
        ),
        _step(_result(), emits=["Coverage: 85.5 percent"]),
    )
    stub = _stub(ledger_conn, query)
    _judge(stub)

    first, second = _rows(ledger_conn)
    first_ordinals = [a.ordinal for a in ledger.list_tool_attempts_for_call(ledger_conn, first.id)]
    second_ordinals = [
        a.ordinal for a in ledger.list_tool_attempts_for_call(ledger_conn, second.id)
    ]
    assert first_ordinals == [1, 2]
    assert second_ordinals == [1]  # restarts, scoped by its own agent_call

    # Run-level ordering stays deterministic across both calls.
    run_level = ledger.list_tool_attempts_for_run(ledger_conn, "r-1")
    assert [(a.agent_call_id, a.ordinal) for a in run_level] == [
        (first.id, 1),
        (first.id, 2),
        (second.id, 1),
    ]


def test_boundary_j_persisted_audit_free_text_obeys_its_bounds(ledger_conn):
    def _oversized(state):
        # An oversized but VALID reason code with a non-verbatim
        # oversized excerpt: the excerpt is the discriminator here, so
        # both bounded fields are exercised on one row.
        state.accept(reason_code=_LABEL_CODE, raw_evidence=[{"line": 2, "excerpt": "y" * 400}])
        # An oversized invalid reason code rejects earlier, so it
        # deliberately retains no excerpt at all.
        state.accept(reason_code="B" * 200, raw_evidence=[{"line": 2, "excerpt": "z" * 400}])
        return _result()

    query = ScriptedQuery(_oversized)
    stub = _stub(ledger_conn, query)
    _judge(stub)

    call_id = _rows(ledger_conn)[0].id
    text_rejected, code_rejected = ledger.list_tool_attempts_for_call(ledger_conn, call_id)

    assert text_rejected.rejection_category == EXCERPT_NOT_VERBATIM
    assert len(text_rejected.proposed_excerpt) == MAX_PROPOSED_EXCERPT_CHARS

    assert code_rejected.rejection_category == REASON_CODE_NOT_ALLOWED
    assert len(code_rejected.proposed_reason_code) == MAX_REASON_CODE_CHARS
    assert code_rejected.proposed_excerpt is None


# =====================================================================
# Append-only enforcement on the tool-attempt audit table
# =====================================================================


def _one_attempt_row(conn):
    query = ScriptedQuery(_step(_result(), emits=["Coverage: 85.5 percent"]))
    stub = _stub(conn, query)
    _judge(stub)
    call_id = _rows(conn)[0].id
    rows = ledger.list_tool_attempts_for_call(conn, call_id)
    assert len(rows) == 1
    return call_id, rows[0]


def test_tool_attempt_rows_reject_delete(ledger_conn):
    _, row = _one_attempt_row(ledger_conn)
    with pytest.raises(sqlite3.IntegrityError):
        ledger_conn.execute("DELETE FROM agent_tool_attempts WHERE id = ?", (row.id,))


def test_tool_attempt_rows_reject_update(ledger_conn):
    """Unlike agent_calls, an individual attempt row has no update
    lifecycle at all."""
    _, row = _one_attempt_row(ledger_conn)
    with pytest.raises(sqlite3.IntegrityError):
        ledger_conn.execute(
            "UPDATE agent_tool_attempts SET outcome = 'REJECTED' WHERE id = ?", (row.id,)
        )


def test_tool_attempt_insert_is_permitted_and_ordinal_is_unique(ledger_conn):
    call_id, row = _one_attempt_row(ledger_conn)
    with ledger.unit_of_work(ledger_conn):
        ledger_conn.execute(
            """
            INSERT INTO agent_tool_attempts (
                agent_call_id, ordinal, proposed_reason_code,
                proposed_evidence_count, primary_line, secondary_line, outcome
            ) VALUES (?, 2, 'X', 1, 3, NULL, 'ACCEPTED')
            """,
            (call_id,),
        )
    assert len(ledger.list_tool_attempts_for_call(ledger_conn, call_id)) == 2

    with pytest.raises(sqlite3.IntegrityError):
        ledger_conn.execute(
            """
            INSERT INTO agent_tool_attempts (
                agent_call_id, ordinal, proposed_reason_code,
                proposed_evidence_count, primary_line, secondary_line, outcome
            ) VALUES (?, 2, 'X', 1, 3, NULL, 'ACCEPTED')
            """,
            (call_id,),
        )


def test_tool_attempt_constraints_reject_invalid_rows(ledger_conn):
    call_id, _ = _one_attempt_row(ledger_conn)

    def _insert(**over):
        values = dict(
            ordinal=9,
            proposed_reason_code="X",
            proposed_evidence_count=1,
            outcome="ACCEPTED",
            rejection_category=None,
            proposed_excerpt=None,
        )
        values.update(over)
        ledger_conn.execute(
            """
            INSERT INTO agent_tool_attempts (
                agent_call_id, ordinal, proposed_reason_code,
                proposed_evidence_count, outcome, rejection_category, proposed_excerpt
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                values["ordinal"],
                values["proposed_reason_code"],
                values["proposed_evidence_count"],
                values["outcome"],
                values["rejection_category"],
                values["proposed_excerpt"],
            ),
        )

    with pytest.raises(sqlite3.IntegrityError):  # ordinal must be >= 1
        _insert(ordinal=0)
    with pytest.raises(sqlite3.IntegrityError):  # negative proposal count
        _insert(proposed_evidence_count=-1)
    with pytest.raises(sqlite3.IntegrityError):  # outcome outside the closed set
        _insert(outcome="MAYBE")
    with pytest.raises(sqlite3.IntegrityError):  # category only on a rejection
        _insert(outcome="ACCEPTED", rejection_category="EXCERPT_NOT_VERBATIM")
    with pytest.raises(sqlite3.IntegrityError):  # rejection needs a category
        _insert(outcome="REJECTED", rejection_category=None)
    with pytest.raises(sqlite3.IntegrityError):  # snippet only where text discriminates
        _insert(outcome="REJECTED", rejection_category="LINE_OUT_OF_RANGE", proposed_excerpt="x")
    with pytest.raises(sqlite3.IntegrityError):  # snippet is length-bounded
        _insert(
            outcome="REJECTED",
            rejection_category="EXCERPT_NOT_VERBATIM",
            proposed_excerpt="z" * 200,
        )


# =====================================================================
# R22 - the proof suite makes zero network or model calls
# =====================================================================


def test_r22_proof_suite_never_reaches_the_real_sdk(ledger_conn):
    """Every path here runs through the injected query_fn seam. If any
    of it reached claude_agent_sdk.query the patch below would fire;
    conftest.py's autouse block_network fixture is the second, blanket
    guard against a real socket."""
    query = ScriptedQuery(
        _step(QueryOutcome(result=_budget_ceiling_result(), error=_TRAILING_BUDGET_EXCEPTION)),
        _step(_result(), emits=["Coverage: 85.5 percent"]),
    )
    with patch(
        "agents.checker.harness.query",
        side_effect=AssertionError("the real SDK query() must never be called"),
    ):
        stub = _stub(ledger_conn, query)
        findings = _judge(stub)
    assert len(findings) == 1
    assert query.invocations == 2


def test_r22_block_network_guard_is_active():
    import socket

    with pytest.raises(AssertionError):
        socket.socket().connect(("127.0.0.1", 9))


# =====================================================================
# R25 - the REAL run_query capture body, driven model-free
# =====================================================================
#
# Everything above drives the harness through its injected query_fn
# seam and manufactures a QueryOutcome AFTER the capture boundary. That
# proves the classifier and the accounting, but it never executes the
# production function whose job IS the capture:
# agents.checker.harness.run_query. The historical defect lived exactly
# there - a terminal typed ResultMessage observed inside the stream,
# then a trailing ordinary exception, and the typed signal lost before
# return - so it needs a direct test of that body.
#
# Only claude_agent_sdk.query is faked. create_sdk_mcp_server,
# build_emit_finding_tool, build_options and ClaudeAgentOptions all run
# for real; the SDK's in-process MCP server construction touches no
# socket and no subprocess. conftest.py's autouse block_network fixture
# stays fully armed.


class _FakeStream:
    """A deterministic stand-in for the SDK's message stream.

    Hand-written rather than an async generator so it has no event-loop
    or asyncgen-hook dependency at all: every __anext__ returns without
    awaiting anything, which is what lets _drive() below run the real
    coroutine with no event loop.
    """

    def __init__(self, *messages, raises: BaseException | None = None):
        self.messages = list(messages)
        self.raises = raises
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index < len(self.messages):
            self.index += 1
            return self.messages[self.index - 1]
        if self.raises is not None:
            raise self.raises
        raise StopAsyncIteration


def _drive(coro):
    """Run a coroutine that never awaits real I/O, WITHOUT an event loop.

    run_query is async, but with a local fake stream it suspends
    nowhere, so a single send() runs it to completion. Going through
    anyio.run()/asyncio.run() instead would build an event loop whose
    Windows self-pipe socketpair trips conftest.py's blanket
    block_network guard - the same interaction CagedCheckerStub._invoke
    documents. Driving the coroutine directly keeps that guard fully
    armed and keeps production's no-network guarantee untouched: if the
    body ever did await real I/O, this fails loudly instead of skipping.
    """
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    coro.close()
    raise AssertionError("run_query suspended: the fake stream must not await real I/O")


def _sdk_result_message(**overrides):
    """A REAL claude_agent_sdk.ResultMessage, so run_query's own
    isinstance(message, ResultMessage) branch is what fires.

    The type is reached through ``harness`` rather than imported here:
    tests/test_dependency_surface.py keeps the Agent SDK importable
    only from agents/, and this is the exact class object the
    production body compares against."""
    defaults = dict(
        subtype="success",
        duration_ms=1234,
        duration_api_ms=1000,
        is_error=False,
        num_turns=2,
        session_id="s-1",
        total_cost_usd=0.001,
        usage={"input_tokens": 100, "output_tokens": 20},
        result="done",
    )
    defaults.update(overrides)
    return harness.ResultMessage(**defaults)


def _sdk_budget_ceiling_message():
    """The exact terminal shape the pinned SDK emits when it halts a
    call at max_budget_usd, as a real typed ResultMessage."""
    return _sdk_result_message(
        subtype=failures.SDK_BUDGET_CEILING_SUBTYPE,
        is_error=True,
        num_turns=3,
        total_cost_usd=0.1226,
        usage={"input_tokens": 900, "output_tokens": 40},
        result=None,
    )


def _run_real_run_query(stream):
    """Execute the ACTUAL harness.run_query against a fake SDK stream."""
    reservation = _coordinator().reserve()
    state = CheckerToolState(request=_request())
    with patch("agents.checker.harness.query", return_value=stream) as fake_query:
        outcome = _drive(
            harness.run_query("missing-synthetic-label", reservation, state, "prompt text")
        )
    assert fake_query.call_count == 1
    return outcome


def test_r25_typed_terminal_result_survives_a_trailing_stream_exception():
    """The historical defect, reproduced against the production body:
    a typed terminal ResultMessage arrives inside the stream and the
    stream THEN raises a plain untyped Exception whose prose quotes the
    CLI's maximum-budget text.

    run_query must RETURN both halves rather than raise, and the typed
    half - subtype, cost, usage, turns - must survive intact."""
    terminal = _sdk_budget_ceiling_message()
    stream = _FakeStream(
        SimpleNamespace(text="a non-terminal message the capture must ignore"),
        terminal,
        raises=_TRAILING_BUDGET_EXCEPTION,
    )

    outcome = _run_real_run_query(stream)

    # Returned, not raised, and both halves are the exact objects.
    assert isinstance(outcome, QueryOutcome)
    assert outcome.result is terminal
    assert outcome.error is _TRAILING_BUDGET_EXCEPTION
    # Everything the old code path destroyed is still recoverable.
    assert outcome.subtype == failures.SDK_BUDGET_CEILING_SUBTYPE
    assert outcome.is_error is True
    assert outcome.result.total_cost_usd == 0.1226
    assert outcome.result.usage == {"input_tokens": 900, "output_tokens": 40}
    assert outcome.result.num_turns == 3
    # And the captured typed subtype is what authorizes the one retry.
    klass = failures.classify_invocation(outcome, breaker_tripped=False)
    assert klass == failures.SDK_BUDGET_CEILING
    assert failures.is_retryable(klass)


def test_r25_same_prose_without_a_typed_result_stays_non_retryable():
    """The complementary production adapter case, same real body: the
    identical plain exception, raised BEFORE any typed ResultMessage.

    Nothing typed was captured, so this is insufficient evidence for a
    retry - whatever the prose says. No exception text is parsed."""
    stream = _FakeStream(raises=_TRAILING_BUDGET_EXCEPTION)

    outcome = _run_real_run_query(stream)

    assert outcome.result is None
    assert outcome.error is _TRAILING_BUDGET_EXCEPTION
    klass = failures.classify_invocation(outcome, breaker_tripped=False)
    assert klass == failures.TRANSPORT_PROCESS_SDK_EXCEPTION_WITHOUT_CAPTURED_TYPED_RESULT
    assert not failures.is_retryable(klass)
    # The decisive point: the two cases differ ONLY in the captured
    # typed result. The exception object is literally the same one.
    assert "Reached maximum budget" in str(_TRAILING_BUDGET_EXCEPTION)


def test_r25_clean_stream_returns_the_terminal_result_with_no_error():
    """The ordinary path through the same body: the stream ends
    normally after its terminal message."""
    terminal = _sdk_result_message()
    stream = _FakeStream(SimpleNamespace(text="assistant chatter"), terminal)

    outcome = _run_real_run_query(stream)

    assert outcome.result is terminal
    assert outcome.error is None
    assert failures.classify_invocation(outcome, breaker_tripped=False) is None


def test_r25_run_query_is_the_production_default_query_fn(ledger_conn):
    """The body proven above is the one production actually uses: no
    test-only indirection stands between CagedCheckerStub and it."""
    stub = CagedCheckerStub(
        run_id="r-1", conn=ledger_conn, coordinator=_coordinator(), clock=lambda: T0
    )
    assert stub.query_fn is harness.run_query


# =====================================================================
# R26 - a coordinator accounting fault poisons the rest of the run
# =====================================================================
#
# Distinct from R23. R23 injects INSIDE the ledger transaction, so
# nothing was made durable and the row stays RESERVED. Here the durable
# terminal transaction COMMITS and the in-memory accounting then fails,
# which leaves the coordinator's figures known-wrong for the rest of
# the run. The invariant: no further model invocation starts.


class InjectedAccountingFailure(Exception):
    pass


def test_r26_post_durable_accounting_fault_blocks_every_later_model_call(ledger_conn):
    """One stub = one run.

    First judgment: one real query_fn invocation, terminal SQLite
    evidence and tool audit commit, then coordinator.commit fails.
    Second judgment on the SAME stub must fail closed before reserve()
    and before query_fn, making ZERO further model invocations, and
    must not touch the first call's durable evidence."""
    coordinator = _coordinator()
    real_commit = coordinator.commit
    durable_at_commit = {}
    reserve_calls = []
    real_reserve = coordinator.reserve

    def _failing_commit(reservation, *, charged_eur_micros):
        # Prove the terminal evidence really is durable at this point,
        # so the fault is genuinely POST-durable and not an R23 rollback.
        row = _rows(ledger_conn)[0]
        durable_at_commit["state"] = row.state
        durable_at_commit["charged"] = row.charged_eur_micros
        durable_at_commit["attempts"] = len(
            ledger.list_tool_attempts_for_call(ledger_conn, row.id)
        )
        raise InjectedAccountingFailure("coordinator accounting failed after durable commit")

    def _counting_reserve():
        reservation = real_reserve()
        reserve_calls.append(reservation)
        return reservation

    coordinator.commit = _failing_commit
    coordinator.reserve = _counting_reserve

    # Exactly ONE scripted step: a second invocation raises
    # TooManyInvocations, a BaseException the harness cannot absorb.
    query = ScriptedQuery(_step(_result(total_cost_usd=0.05), emits=["Coverage: 85.5 percent"]))
    stub = _stub(ledger_conn, query, coordinator=coordinator)

    with pytest.raises(TerminalAccountingError):
        _judge(stub)

    assert durable_at_commit["state"] == "COMPLETED"
    assert durable_at_commit["charged"] == _converted(0.05)
    assert durable_at_commit["attempts"] == 1
    assert query.invocations == 1
    assert len(reserve_calls) == 1

    first_rows = _rows(ledger_conn)
    assert len(first_rows) == 1
    first = first_rows[0]
    first_attempts = ledger.list_tool_attempts_for_call(ledger_conn, first.id)

    # A SECOND, different, non-absent judgment through the same stub.
    coordinator.commit = real_commit  # accounting itself is healthy again
    with pytest.raises(TerminalAccountingError):
        _judge(stub, _request(check_class="stale-STATE-marker"))

    # Zero further model invocations, and reserve() was never reached.
    assert query.invocations == 1
    assert len(reserve_calls) == 1

    # The first call's durable evidence is untouched: no new row, no
    # rewrite, no deletion, no extra audit row.
    after = _rows(ledger_conn)
    assert len(after) == 1
    assert (after[0].id, after[0].state, after[0].charged_eur_micros) == (
        first.id,
        first.state,
        first.charged_eur_micros,
    )
    assert after[0].finished_at_utc == first.finished_at_utc
    assert ledger.list_tool_attempts_for_call(ledger_conn, first.id) == first_attempts


def test_r26_latch_leaves_the_deterministic_absent_short_circuit_intact(ledger_conn):
    """The invariant is NO FURTHER MODEL INVOCATION, not a hard stop:
    a confirmed-absent request never enters the model path at all, so
    it keeps its deterministic empty return."""
    coordinator = _coordinator()

    def _failing_commit(reservation, *, charged_eur_micros):
        raise InjectedAccountingFailure("coordinator accounting failed after durable commit")

    coordinator.commit = _failing_commit
    query = ScriptedQuery(_step(_result(), emits=["Coverage: 85.5 percent"]))
    stub = _stub(ledger_conn, query, coordinator=coordinator)

    with pytest.raises(TerminalAccountingError):
        _judge(stub)

    assert _judge(stub, _request(text=None)) == ()
    assert query.invocations == 1
    assert len(_rows(ledger_conn)) == 1


def test_r26_an_unfaulted_stub_keeps_serving_later_judgments(ledger_conn):
    """The latch is set by an accounting fault, never by an ordinary
    judgment: two clean judgments on one stub still both run."""
    query = ScriptedQuery(
        _step(_result(), emits=["Coverage: 85.5 percent"]),
        _step(_result(), emits=["Coverage: 85.5 percent"]),
    )
    stub = _stub(ledger_conn, query)
    assert len(_judge(stub)) == 1
    # A second, different logical task on the same stub still reaches
    # the model path (its proposal is rejected on class mismatch, which
    # is an ordinary COMPLETED no-finding outcome, not a latched stop).
    assert _judge(stub, _request(check_class="stale-STATE-marker")) == ()
    assert query.invocations == 2
    assert [r.state for r in _rows(ledger_conn)] == ["COMPLETED", "COMPLETED"]


# =====================================================================
# The frozen bounds this dispatch must not move (dispatch section 13)
# =====================================================================


def test_adopted_bounds_are_unchanged_by_adr_0008():
    assert RUN_BUDGET_EUR_MICROS == 750_000
    assert MAX_PER_CALL_RESERVE_EUR_MICROS == 150_000
    assert SDK_ALLOWANCE_SAFETY_MARGIN == "0.70"
    assert MAX_TURNS == 10
    assert MAX_TOOL_CALLS_PER_CHECK == 5
    assert MODEL == "claude-haiku-4-5-20251001"

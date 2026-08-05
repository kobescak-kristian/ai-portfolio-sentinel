"""Live inventory: pagination, normalization, dedup, C8-round-2
required-path/readme-structure applicability, and inventory-failure
containment. All network access is via FakeHttpClient — CI never
touches the live GitHub API."""

from __future__ import annotations

import json

import pytest

from sentinel.inventory.base import Content, ConfirmedAbsent, build_work_units
from sentinel.inventory.github_live import (
    InventoryUnavailable,
    build_repo_surfaces,
    list_public_repos,
)
from sentinel.net.client import FakeHttpClient, HttpError, HttpResponse

USER = "kobescak-kristian"


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


PRE_PUSH_ACTIVE = b"#!/bin/sh\npython .githooks/validate_artifacts.py .\n"
PRE_PUSH_COMMENTED = b"#!/bin/sh\n# python .githooks/validate_artifacts.py .\n"
GATE_WITH_STATE = (
    b'ROOT = None\n'
    b'REQUIRED_README_SECTIONS = ["## Problem", "## Solution"]\n'
    b'if not (ROOT / "STATE.md").exists():\n'
    b'    errors.append(1)\n'
)
GATE_WITHOUT_STATE = b'REQUIRED_README_SECTIONS = ["## Problem"]\n'
GATE_UNPARSEABLE = b"def broken(:\n"


def _tree(paths):
    entries = [{"type": "blob", "path": p} for p in paths]
    return HttpResponse(200, {}, json.dumps({"tree": entries}).encode(), "")


def test_pagination_across_multiple_pages():
    repos_p1 = [{"name": f"repo-{i}", "fork": False, "archived": False, "disabled": False} for i in range(100)]
    repos_p2 = [{"name": "repo-100", "fork": False, "archived": False, "disabled": False}]
    responses = dict([_repos_page(repos_p1, 1), _repos_page(repos_p2, 2)])
    http = FakeHttpClient(responses=responses)
    result = list_public_repos(http, USER, timeout=5.0)
    assert len(result) == 101


def test_pagination_terminates_on_short_page():
    repos = [{"name": "solo", "fork": False, "archived": False, "disabled": False}]
    http = FakeHttpClient(responses=dict([_repos_page(repos, 1)]))
    result = list_public_repos(http, USER, timeout=5.0)
    assert len(result) == 1
    # only one page fetched
    assert len(http.calls) == 1


def test_unbounded_pagination_raises_inventory_unavailable():
    full_page = [{"name": f"r{i}", "fork": False, "archived": False, "disabled": False} for i in range(100)]
    responses = {}
    for page in range(1, 25):
        url, resp = _repos_page(full_page, page)
        responses[url] = resp
    http = FakeHttpClient(responses=responses)
    with pytest.raises(InventoryUnavailable):
        list_public_repos(http, USER, timeout=5.0)


def test_http_failure_raises_inventory_unavailable():
    url, _ = _repos_page([], 1)
    http = FakeHttpClient(responses={url: HttpError("timeout", "boom")})
    with pytest.raises(InventoryUnavailable):
        list_public_repos(http, USER, timeout=5.0)


def test_forks_archived_disabled_excluded():
    repos = [
        {"name": "keep", "fork": False, "archived": False, "disabled": False},
        {"name": "a-fork", "fork": True, "archived": False, "disabled": False},
        {"name": "archived", "fork": False, "archived": True, "disabled": False},
        {"name": "disabled", "fork": False, "archived": False, "disabled": True},
    ]
    http = FakeHttpClient(responses=dict([_repos_page(repos, 1), _empty_page(2)]))
    result = list_public_repos(http, USER, timeout=5.0)
    assert [r["name"] for r in result] == ["keep"]


def test_normalized_surfaces_pass_the_frozen_grammar():
    from contracts.schemas import CheckTask
    from datetime import datetime, timezone

    repos = [{"name": "acme", "fork": False, "archived": False, "disabled": False, "default_branch": "main"}]
    responses = dict(
        [
            _repos_page(repos, 1),
            _empty_page(2),
            (f"https://raw.githubusercontent.com/{USER}/acme/main/.githooks/pre-push", HttpResponse(404, {}, b"", "")),
            (f"https://api.github.com/repos/{USER}/acme/git/trees/main?recursive=1", _tree(["README.md"])),
        ]
    )
    http = FakeHttpClient(responses=responses)
    surfaces = build_repo_surfaces(http, USER, timeout=5.0)
    units = build_work_units(surfaces)
    for unit in units:
        CheckTask(
            schema_version=1,
            task_id="t",
            run_id="r",
            surface=unit.surface,
            check_class=unit.check_class,
            created_at_utc=datetime.now(timezone.utc),
            status="PENDING",
        )  # raises if the surface grammar is violated


def test_profile_and_pages_repos_excluded():
    repos = [
        {"name": USER, "fork": False, "archived": False, "disabled": False},
        {"name": f"{USER}.github.io", "fork": False, "archived": False, "disabled": False},
        {"name": "real-project", "fork": False, "archived": False, "disabled": False, "default_branch": "main"},
    ]
    responses = dict(
        [
            _repos_page(repos, 1),
            _empty_page(2),
            (f"https://raw.githubusercontent.com/{USER}/real-project/main/.githooks/pre-push", HttpResponse(404, {}, b"", "")),
            (f"https://api.github.com/repos/{USER}/real-project/git/trees/main?recursive=1", _tree([])),
        ]
    )
    http = FakeHttpClient(responses=responses)
    surfaces = build_repo_surfaces(http, USER, timeout=5.0)
    assert [s.owner for s in surfaces] == ["real-project"]


def test_gate_file_required_only_when_actively_invoked():
    repos = [
        {"name": "a", "fork": False, "archived": False, "disabled": False, "default_branch": "main"},
        {"name": "b", "fork": False, "archived": False, "disabled": False, "default_branch": "main"},
    ]
    responses = dict(
        [
            _repos_page(repos, 1),
            _empty_page(2),
            (f"https://raw.githubusercontent.com/{USER}/a/main/.githooks/pre-push", HttpResponse(200, {}, PRE_PUSH_ACTIVE, "")),
            (f"https://raw.githubusercontent.com/{USER}/a/main/.githooks/validate_artifacts.py", HttpResponse(200, {}, GATE_WITHOUT_STATE, "")),
            (f"https://api.github.com/repos/{USER}/a/git/trees/main?recursive=1", _tree(["README.md"])),
            (f"https://raw.githubusercontent.com/{USER}/b/main/.githooks/pre-push", HttpResponse(200, {}, PRE_PUSH_COMMENTED, "")),
            (f"https://api.github.com/repos/{USER}/b/git/trees/main?recursive=1", _tree(["README.md"])),
        ]
    )
    http = FakeHttpClient(responses=responses)
    surfaces = {s.owner: s for s in build_repo_surfaces(http, USER, timeout=5.0)}
    assert ".githooks/validate_artifacts.py" in surfaces["a"].policy.required_paths
    assert surfaces["a"].policy.readme_structure_applicable is True
    assert ".githooks/validate_artifacts.py" not in surfaces["b"].policy.required_paths
    assert surfaces["b"].policy.readme_structure_applicable is False


def test_state_md_required_only_when_gate_file_declares_it():
    repos = [{"name": "a", "fork": False, "archived": False, "disabled": False, "default_branch": "main"}]
    responses = dict(
        [
            _repos_page(repos, 1),
            _empty_page(2),
            (f"https://raw.githubusercontent.com/{USER}/a/main/.githooks/pre-push", HttpResponse(200, {}, PRE_PUSH_ACTIVE, "")),
            (f"https://raw.githubusercontent.com/{USER}/a/main/.githooks/validate_artifacts.py", HttpResponse(200, {}, GATE_WITH_STATE, "")),
            (f"https://api.github.com/repos/{USER}/a/git/trees/main?recursive=1", _tree(["README.md"])),
        ]
    )
    http = FakeHttpClient(responses=responses)
    surfaces = build_repo_surfaces(http, USER, timeout=5.0)
    assert "STATE.md" in surfaces[0].policy.required_paths


def test_no_authorization_header_ever_sent():
    repos = [{"name": "a", "fork": False, "archived": False, "disabled": False, "default_branch": "main"}]
    responses = dict(
        [
            _repos_page(repos, 1),
            _empty_page(2),
            (f"https://raw.githubusercontent.com/{USER}/a/main/.githooks/pre-push", HttpResponse(404, {}, b"", "")),
            (f"https://api.github.com/repos/{USER}/a/git/trees/main?recursive=1", _tree([])),
        ]
    )
    http = FakeHttpClient(responses=responses)
    build_repo_surfaces(http, USER, timeout=5.0)
    for _method, _url, headers, _timeout in http.calls:
        assert "authorization" not in {k.lower() for k in headers}


def test_inventory_failure_aborts_run_without_auto_resolving(
    tmp_path, make_config, make_deps, fixed_clock, seeded_ids
):
    """A GitHub outage must abort the whole run (FAILED, no tasks
    created) — it must never look like a clean, fully-scanned
    portfolio. Every pre-existing OPEN finding stays OPEN, untouched."""
    from sentinel import ledger
    from sentinel.pipeline import execute_run
    from tests.conftest import ListSurfaceProvider, T0, T1, make_repo_surface

    config = make_config(tmp_path)
    repo = make_repo_surface("acme", {"README.md": "## Solution\n"})
    execute_run(
        config,
        make_deps(clock=fixed_clock(T0), ids=seeded_ids(["run-1"]), surface_provider=ListSurfaceProvider([repo])),
    )

    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        before = [dict(r) for r in conn.execute("SELECT * FROM findings WHERE status='OPEN'").fetchall()]
    finally:
        conn.close()
    assert before  # sanity: a finding really exists

    class ExplodingProvider:
        def repos(self):
            raise InventoryUnavailable("simulated GitHub outage")

    outcome = execute_run(
        config, make_deps(clock=fixed_clock(T1), ids=seeded_ids(["run-2"]), surface_provider=ExplodingProvider())
    )
    assert outcome.status == "FAILED"
    assert outcome.tasks_created == 0

    conn = ledger.open_ledger(config.db_path, create=False)
    try:
        after = [dict(r) for r in conn.execute("SELECT * FROM findings WHERE status='OPEN'").fetchall()]
    finally:
        conn.close()
    assert after == before


def test_policy_probe_unparseable_gate_dead_letters_its_own_task(tmp_path, make_config, make_deps, fixed_clock):
    """A gate file that exists but isn't valid Python must not let the
    missing-required-file check for that exact path silently pass —
    it dead-letters instead of guessing."""
    from checks.base import CheckContext
    from checks.deterministic.files import check_missing_required_file
    from sentinel.inventory.base import Content

    ctx = CheckContext(
        owner="acme",
        detail_path=".githooks/validate_artifacts.py",
        fetch=lambda path: Content(text=GATE_UNPARSEABLE.decode()),
        link_resolver=None,
        judgment=None,
        policy_parse_required=True,
    )
    from checks.base import Inconclusive

    outcome = check_missing_required_file(ctx)
    assert isinstance(outcome, Inconclusive)

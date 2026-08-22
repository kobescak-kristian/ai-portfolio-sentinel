"""Phase-2 boundary tests: zero-model-call invariant (in *stub* mode)
and no-write-access-by-construction.

**Scoping note**: this is a narrower, differently-scoped file than
the full cage suite (tests/test_bounds.py), which landed at Phase 3
with the caged checker agent per BLUEPRINT §4/§6 P3, per
tests/test_failures.py's module docstring, and per dispatch q77-p3-a.
This file proves what the *deterministic* control plane supports on
its own: no model SDK is ever imported anywhere under sentinel/ or
checks/ (the caged agent's own SDK import lives only under agents/ —
tests/test_dependency_surface.py is the complementary check that it's
*only* allowed there), a stub-mode run makes no model call (every
CostRow is zero-token/zero-cost), no write-scoped credential is ever
used, and no byte is written outside the four explicit CLI paths. The
zero-model-call test below selects stub mode explicitly, rather than
relying on it being RunConfig's default — this invariant must hold
because stub mode was asked for, not because nothing else exists.

**Phase 4 addition** (adr/0010-phase4-loop-safety-controls; dispatch
q77-p4-runner-a): ``runner`` joins the protected source roots, so the
bounded-loop supervisor is covered by every static scan here, and four
further tests pin its internal import boundary — the generic supervisor
and its breakers stay domain-free, ``runner/sentinel_adapter.py`` is the
sole integration boundary, and no runner module reaches a provider
execution surface.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

BANNED_IMPORT_ROOTS = {
    "anthropic", "openai", "cohere", "mistralai", "ollama", "litellm",
    "transformers", "claude_agent_sdk", "google",  # google.generativeai / google.genai
    "langchain", "langchain_openai", "langchain_anthropic",
}

# Phase 4 (dispatch q77-p4-runner-a): ``runner`` joins the protected
# roots, so the bounded-loop supervisor is covered by every scan below —
# no model SDK import, no destructive SQL, no answer-key coupling. New
# code must not sit outside the boundary it is supposed to respect.
SOURCE_ROOTS = ["sentinel", "checks", "runner"]

# The one runner module allowed to bind the generic supervisor to
# Sentinel (ADR-0010 / dispatch section 4). Everything else under
# runner/ must stay domain-free or wire only other runner modules.
RUNNER_INTEGRATION_ADAPTER = Path("runner/sentinel_adapter.py")

# Domain roots the generic supervisor and its predicates must never
# reach for. ``telemetry`` is included: durable cost reconstruction is
# the adapter's job, not the supervisor's.
DOMAIN_IMPORT_ROOTS = {"sentinel", "checks", "agents", "telemetry", "contracts"}

# Provider-execution surfaces that no runner module may import. The
# adapter is allowed ``agents.checker.budget`` (the owner-approved
# allowance seam) and nothing else under agents/.
BANNED_PROVIDER_MODULES = {
    "agents.checker.harness",
    "agents.checker.auth",
    "agents.checker.tools",
    "claude_agent_sdk",
}


def _iter_source_files():
    for root in SOURCE_ROOTS:
        for path in Path(root).rglob("*.py"):
            yield path


def _imported_modules(path: Path) -> set[str]:
    """Every module name imported by a file, absolute imports only."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_static_scan_no_model_sdk_import_anywhere():
    for path in _iter_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in BANNED_IMPORT_ROOTS, f"{path}: imports {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in BANNED_IMPORT_ROOTS, f"{path}: imports from {node.module}"


def test_static_scan_no_delete_statement_in_control_plane():
    pattern = re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE)
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), f"{path}: contains a DELETE FROM statement"


def test_static_scan_no_answer_key_reference():
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        assert "answer_key" not in text, f"{path}: references the frozen answer key"


# --- Phase 4 runner import boundary (ADR-0010; dispatch q77-p4-runner-a) ----


def test_generic_loop_and_breakers_stay_domain_free():
    """``runner/loop.py`` and ``runner/breakers.py`` are the safety
    logic. They must be reasonable about WITHOUT the domain, so they may
    import the standard library and each other — nothing else. A loop
    that quietly learns what a Sentinel run is stops being a reusable
    bounded-loop pattern (BLUEPRINT §6 P4: "build once, cite twice")."""
    for name in ("runner/loop.py", "runner/breakers.py"):
        path = Path(name)
        for module in _imported_modules(path):
            root = module.split(".")[0]
            assert root not in DOMAIN_IMPORT_ROOTS, f"{name}: imports domain module {module}"


def test_sentinel_adapter_is_the_sole_integration_boundary():
    """Exactly one runner module may bind the loop to Sentinel run
    execution, durable cost accounting and budget construction. The
    single documented exception is ``runner/state.py``, which reuses the
    existing ledger connection/transaction primitives for persistence
    rather than opening a second database — persistence, not
    integration. If any other runner module reaches into the domain, the
    boundary has moved and this test is where that shows up."""
    persistence_only = {
        Path("runner/state.py"): {"sentinel.ledger", "contracts.schemas"},
    }
    offenders = []
    for path in Path("runner").rglob("*.py"):
        if path == RUNNER_INTEGRATION_ADAPTER:
            continue
        allowed = persistence_only.get(path, set())
        for module in _imported_modules(path):
            if module.split(".")[0] in DOMAIN_IMPORT_ROOTS and module not in allowed:
                offenders.append(f"{path}: {module}")
    assert offenders == [], (
        f"only {RUNNER_INTEGRATION_ADAPTER} may import domain modules "
        f"(runner/state.py: persistence primitives only): {offenders}"
    )


def test_integration_adapter_has_no_direct_model_sdk_import():
    modules = _imported_modules(RUNNER_INTEGRATION_ADAPTER)
    for module in modules:
        assert module.split(".")[0] not in BANNED_IMPORT_ROOTS, (
            f"{RUNNER_INTEGRATION_ADAPTER}: imports model SDK {module}"
        )


def test_no_runner_module_imports_a_provider_execution_surface():
    """The adapter may construct a reduced ``RunBudgetCoordinator``
    (``agents.checker.budget``) — that is the owner-approved allowance
    seam and it makes no model call. It may NOT import the harness, the
    auth path, the tool cage or the SDK: no provider-capable bounded
    loop is authorised in this implementation."""
    for path in Path("runner").rglob("*.py"):
        for module in _imported_modules(path):
            assert module not in BANNED_PROVIDER_MODULES, (
                f"{path}: imports provider-execution surface {module}"
            )
            if module.split(".")[0] == "agents":
                assert module == "agents.checker.budget" or module == "agents.checker.config", (
                    f"{path}: only the budget/config allowance seam is permitted, got {module}"
                )


def test_static_scan_no_environment_variable_reads_in_inventory():
    """github_live.py must read no environment variable — this is
    what makes "no credential of any kind" a structural, not a
    policy, property."""
    path = Path("sentinel/inventory/github_live.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
            pytest.fail(f"github_live.py reads an environment variable: {ast.dump(node)}")
        if isinstance(node, ast.Subscript):
            value = node.value
            if isinstance(value, ast.Attribute) and value.attr == "environ":
                pytest.fail("github_live.py reads os.environ[...]")


def test_dynamic_full_run_makes_zero_model_calls_and_zero_cost(tmp_path, make_config, make_deps, fixed_clock):
    from sentinel.pipeline import execute_run
    from telemetry.cost_ledger import read_cost_rows
    from tests.conftest import ListSurfaceProvider, T0, make_repo_surface

    # judgment_mode is explicit stub here, not relied upon as a
    # default -- this is the invariant a stub-mode run must uphold.
    config = make_config(tmp_path, judgment_mode="stub")
    repo = make_repo_surface("acme", {"README.md": "## Solution\n"})
    outcome = execute_run(
        config, make_deps(clock=fixed_clock(T0), surface_provider=ListSurfaceProvider([repo]))
    )
    assert outcome.status == "COMPLETED"
    rows = read_cost_rows(config.cost_ledger_path)
    assert rows
    for row in rows:
        assert row.input_tokens == 0
        assert row.output_tokens == 0
        assert row.cost_eur_micros == 0


def test_no_credential_env_var_ever_reaches_a_request_or_output(
    tmp_path, make_config, make_deps, fixed_clock, monkeypatch
):
    """A canary token in common credential env vars must appear in no
    outbound request header, no log line, no FINDINGS.md byte, and no
    ledger cell — the strongest available proof "it holds no
    credentials for them"."""
    import json as _json

    from sentinel.net.client import FakeHttpClient, HttpResponse
    from sentinel.pipeline import execute_run
    from tests.conftest import T0

    canary = "CANARY-SECRET-VALUE-DO-NOT-LEAK"
    for var in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
        monkeypatch.setenv(var, canary)

    user = "kobescak-kristian"
    repos = [{"name": "acme", "fork": False, "archived": False, "disabled": False, "default_branch": "main"}]
    responses = {
        f"https://api.github.com/users/{user}/repos?type=owner&sort=full_name&direction=asc&per_page=100&page=1": HttpResponse(
            200, {}, _json.dumps(repos).encode(), ""
        ),
        f"https://api.github.com/users/{user}/repos?type=owner&sort=full_name&direction=asc&per_page=100&page=2": HttpResponse(
            200, {}, b"[]", ""
        ),
        f"https://raw.githubusercontent.com/{user}/acme/main/.githooks/pre-push": HttpResponse(404, {}, b"", ""),
        f"https://api.github.com/repos/{user}/acme/git/trees/main?recursive=1": HttpResponse(
            200, {}, b'{"tree":[{"type":"blob","path":"README.md"}]}', ""
        ),
        f"https://raw.githubusercontent.com/{user}/acme/main/README.md": HttpResponse(200, {}, b"## Solution\n", ""),
        f"https://raw.githubusercontent.com/{user}/acme.github.io/main/README.md": HttpResponse(404, {}, b"", ""),
        f"https://api.github.com/repos/{user}/acme.github.io/git/trees/main?recursive=1": HttpResponse(200, {}, b'{"tree":[]}', ""),
    }
    fake_http = FakeHttpClient(responses=responses)

    config = make_config(tmp_path, run_kind="live", source="live", github_user=user)
    deps = make_deps(clock=fixed_clock(T0), http=fake_http)

    execute_run(config, deps)

    for _method, _url, headers, _timeout in fake_http.calls:
        assert canary not in str(headers.values())
        assert "authorization" not in {k.lower() for k in headers}

    log_text = config.log_path.read_text(encoding="utf-8")
    assert canary not in log_text
    if config.findings_path.exists():
        assert canary not in config.findings_path.read_text(encoding="utf-8")

    conn_text = config.db_path.read_bytes()
    assert canary.encode() not in conn_text


def test_containment_full_run_touches_nothing_outside_explicit_paths(tmp_path, make_config, make_deps, fixed_clock):
    from sentinel.pipeline import execute_run
    from tests.conftest import ListSurfaceProvider, T0, make_repo_surface

    out_dir = tmp_path / "out"
    config = make_config(
        tmp_path,
        db_path=out_dir / "s.sqlite3",
        findings_path=out_dir / "F.md",
        log_path=out_dir / "r.jsonl",
        cost_ledger_path=out_dir / "c.jsonl",
    )
    repo = make_repo_surface("acme", {"README.md": "## Solution\n"})
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    execute_run(config, make_deps(clock=fixed_clock(T0), surface_provider=ListSurfaceProvider([repo])))
    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    for path in after - before:
        assert str(path).startswith(str(out_dir))

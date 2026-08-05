"""Phase-2 boundary tests: zero-model-call invariant and no-write-
access-by-construction.

**Scoping note**: this is a narrower, differently-scoped file than
the full cage suite (tests/test_bounds.py), which lands at Phase 3
with the caged checker agent per BLUEPRINT §4/§6 P3 and per
tests/test_failures.py's module docstring. This file only proves what
Phase 2's actual surface supports: no model SDK is ever imported, no
model call is ever made (every CostRow is zero-token/zero-cost), no
write-scoped credential is ever used, and no byte is written outside
the four explicit CLI paths.
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

SOURCE_ROOTS = ["sentinel", "checks"]


def _iter_source_files():
    for root in SOURCE_ROOTS:
        for path in Path(root).rglob("*.py"):
            yield path


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

    config = make_config(tmp_path)
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

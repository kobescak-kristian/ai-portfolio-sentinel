"""Dependency-surface parity: every first-party module's third-party
import set must be exactly what's pinned in requirements.txt. An
accidental ``import requests`` in sentinel/ would run green locally
(it happens to be installed on the authoring machine) and fail CI with
ModuleNotFoundError at collection time; this test catches that before
it ever reaches a push.

Phase 3 addition (dispatch q77-p3-a, section G): ``agents`` is now a
first-party root, and it alone is allowed the Agent SDK's direct
dependencies (``claude_agent_sdk``, ``anyio``, ``certifi``) — every
other root stays exactly what it was (``pydantic`` only), so this
test is also the complementary check to
tests/test_read_only_boundary.py's AST ban: the SDK import is not just
absent from sentinel/checks (that test), it is *only* allowed under
agents/ anywhere in the runtime source tree (this test).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Per-root allowed third-party imports. Every root not listed here
# (or listed with an empty/small set) may import only what's shown.
PER_ROOT_ALLOWED_THIRD_PARTY: dict[str, set[str]] = {
    "sentinel": {"pydantic"},
    "checks": {"pydantic"},
    "contracts": {"pydantic"},
    "telemetry": {"pydantic"},
    "agents": {"pydantic", "claude_agent_sdk", "anyio", "certifi"},
    # Phase 4 (dispatch q77-p4-runner-a, owner-authorised scope
    # amendment 2026-08-22). The bounded-loop runner imports NO
    # third-party package at all: the supervisor and its breakers are
    # stdlib-only by design, and the adapter reaches only first-party
    # roots (sentinel, telemetry, agents). Listing it here is what makes
    # `runner` a first-party root for this module's scan AND subjects
    # runner/ to the same parity check as every other runtime root.
    "runner": set(),
}
ALLOWED_TEST_THIRD_PARTY = {"pydantic", "yaml", "pytest"}

SOURCE_ROOTS = list(PER_ROOT_ALLOWED_THIRD_PARTY)
FIRST_PARTY_ROOTS = {*SOURCE_ROOTS, "tests"}


def _iter_py_files(roots):
    for root in roots:
        for path in Path(root).rglob("*.py"):
            yield path


def _third_party_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    stdlib = set(sys.stdlib_module_names)
    return {r for r in roots if r not in FIRST_PARTY_ROOTS and r not in stdlib}


def test_runtime_modules_import_only_their_roots_allowed_third_party():
    for root, allowed in PER_ROOT_ALLOWED_THIRD_PARTY.items():
        for path in _iter_py_files([root]):
            third_party = _third_party_imports(path)
            assert third_party <= allowed, (
                f"{path} imports third-party not allowed for {root}/: "
                f"{third_party - allowed}"
            )


def test_test_modules_import_only_the_pinned_dev_set():
    for path in Path("tests").rglob("*.py"):
        third_party = _third_party_imports(path)
        assert third_party <= ALLOWED_TEST_THIRD_PARTY, f"{path} imports unpinned third-party: {third_party}"

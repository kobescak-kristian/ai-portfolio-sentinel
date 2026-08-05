"""Dependency-surface parity: every first-party module's third-party
import set must be exactly what's pinned in requirements.txt —
``pydantic`` only. An accidental ``import requests`` in sentinel/
would run green locally (it happens to be installed on the authoring
machine) and fail CI with ModuleNotFoundError at collection time;
this test catches that before it ever reaches a push.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ALLOWED_RUNTIME_THIRD_PARTY = {"pydantic"}
ALLOWED_TEST_THIRD_PARTY = {"pydantic", "yaml", "pytest"}

SOURCE_ROOTS = ["sentinel", "checks", "contracts", "telemetry"]


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
    first_party = {"sentinel", "checks", "contracts", "telemetry", "tests"}
    stdlib = set(sys.stdlib_module_names)
    return {r for r in roots if r not in first_party and r not in stdlib}


def test_runtime_modules_import_only_pydantic_as_third_party():
    for path in _iter_py_files(SOURCE_ROOTS):
        third_party = _third_party_imports(path)
        assert third_party <= ALLOWED_RUNTIME_THIRD_PARTY, f"{path} imports unpinned third-party: {third_party}"


def test_test_modules_import_only_the_pinned_dev_set():
    for path in Path("tests").rglob("*.py"):
        third_party = _third_party_imports(path)
        assert third_party <= ALLOWED_TEST_THIRD_PARTY, f"{path} imports unpinned third-party: {third_party}"

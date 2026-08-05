#!/usr/bin/env python3
"""Phase-1 freeze guard (BLUEPRINT §6 P1 freeze; Q-77 Phase 2 dispatch).

Verifies the frozen Phase 1 eval bed (fixture corpus, answer key,
scoring, review evidence) is byte-identical to the freeze commit.
Read-only — touches nothing. Exit 0 = PASS, 1 = FAIL.

Compares against committed constants and a tracked manifest, not
against the freeze commit object directly: CI's ``actions/checkout``
defaults to a shallow clone (fetch-depth 1), so ``git rev-parse
<freeze-sha>:fixtures`` would fail there. Hardcoded expectations work
at any clone depth and make the expectation itself reviewable in a
diff.
"""

from __future__ import annotations

import subprocess
import sys
from difflib import unified_diff
from pathlib import Path

FREEZE_COMMIT = "4d46c1d4fc3c4f485a83f44fa54afa6b04b1f541"
GATE_POST_COMMIT = "4e8e48b868297334a421215479a5e1f01aec51e1"

EXPECTED_TREES = {
    "fixtures": "32b9f9e623f51804ff90f4537c4090d7845e4e35",
    "evals": "6e2dab559e6b0f14659b472a4a595a105f4456e1",
}
EXPECTED_BLOBS = {
    "posts/2026-08-04-phase-1-gate.md": "9cd774f57c1800737434ee590db366865b677750",
}
EXPECTED_FILE_COUNT = 41  # under fixtures/ + evals/

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "scripts" / "phase1_freeze_manifest.txt"

# Reported, never blocking on their own: files that may legitimately
# change post-freeze (e.g. a frozen-schema-consuming module), but
# whose drift since the freeze is worth a human's attention.
AMBER_PATHS = ["contracts/schemas.py", "contracts/ledger_schema.sql", "SPEC.md"]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


def _commit_exists(sha: str) -> bool:
    return _run(["cat-file", "-e", f"{sha}^{{commit}}"]).returncode == 0


def main() -> int:
    failures: list[str] = []
    lines: list[str] = ["PHASE-1 FREEZE GUARD"]
    lines.append(f"  freeze commit ......... {FREEZE_COMMIT}")
    lines.append(f"  gate-post commit ...... {GATE_POST_COMMIT}")

    frozen_paths = list(EXPECTED_TREES) + list(EXPECTED_BLOBS)
    status = _run(["status", "--porcelain", "--"] + frozen_paths)
    if status.stdout.strip():
        failures.append(f"worktree/index dirty for frozen paths:\n{status.stdout}")
        lines.append("  worktree/index ........ DIRTY")
    else:
        lines.append("  worktree/index ........ CLEAN for " + ", ".join(frozen_paths))

    for subpath, expected in EXPECTED_TREES.items():
        result = _run(["rev-parse", f"HEAD:{subpath}"])
        actual = result.stdout.strip()
        if actual != expected:
            failures.append(f"tree {subpath}/ expected {expected}, got {actual!r}")
            lines.append(f"  tree {subpath}/ ........ {actual or '(missing)'}  MISMATCH")
        else:
            lines.append(f"  tree {subpath}/ ........ {actual}  MATCH")

    for subpath, expected in EXPECTED_BLOBS.items():
        result = _run(["rev-parse", f"HEAD:{subpath}"])
        actual = result.stdout.strip()
        if actual != expected:
            failures.append(f"blob {subpath} expected {expected}, got {actual!r}")
            lines.append(f"  blob {subpath} ..... {actual or '(missing)'}  MISMATCH")
        else:
            lines.append(f"  blob {subpath} ..... {actual}  MATCH")

    if MANIFEST_PATH.exists():
        expected_manifest = MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
        current = _run(["ls-tree", "-r", "HEAD", "--", "fixtures", "evals"])
        actual_manifest = sorted(current.stdout.splitlines())
        expected_sorted = sorted(expected_manifest)
        if actual_manifest != expected_sorted:
            diff = "\n".join(
                unified_diff(expected_sorted, actual_manifest, "expected", "actual", lineterm="")
            )
            failures.append(f"per-file manifest mismatch:\n{diff}")
            lines.append(f"  manifest .............. MISMATCH ({len(actual_manifest)} vs {len(expected_sorted)} blobs)")
        elif len(actual_manifest) != EXPECTED_FILE_COUNT:
            failures.append(
                f"manifest file count {len(actual_manifest)} != expected {EXPECTED_FILE_COUNT}"
            )
            lines.append(f"  manifest .............. WRONG COUNT ({len(actual_manifest)})")
        else:
            lines.append(f"  manifest .............. {len(actual_manifest)}/{EXPECTED_FILE_COUNT} blobs identical")
    else:
        failures.append(f"manifest file missing: {MANIFEST_PATH}")
        lines.append("  manifest .............. MISSING")

    if _commit_exists(FREEZE_COMMIT):
        ancestor = _run(["merge-base", "--is-ancestor", FREEZE_COMMIT, "HEAD"])
        if ancestor.returncode != 0:
            failures.append("freeze commit is not an ancestor of HEAD")
            lines.append("  ancestry .............. FAIL — freeze commit not an ancestor of HEAD")
        else:
            lines.append("  ancestry .............. freeze commit is an ancestor of HEAD")
    else:
        lines.append("  ancestry .............. SKIPPED (shallow clone — freeze commit object absent)")

    if _commit_exists(FREEZE_COMMIT):
        amber = _run(["diff", "--stat", f"{FREEZE_COMMIT}..HEAD", "--"] + AMBER_PATHS)
        if amber.stdout.strip():
            lines.append(f"  amber ................. {amber.stdout.strip().splitlines()[-1]} — justify in the session record")
        else:
            lines.append("  amber ................. " + ", ".join(AMBER_PATHS) + " — no change since freeze")

    print("\n".join(lines))
    if failures:
        print("\nFAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

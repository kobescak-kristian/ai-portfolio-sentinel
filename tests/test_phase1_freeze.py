"""Wraps scripts/check_phase1_frozen.py — proves the frozen Phase 1
eval bed is unchanged. Shells the script (it's a repo-level integrity
check, not a unit under test); this is the one place a subprocess
call is appropriate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_phase1_freeze_guard_passes():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_phase1_frozen.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout

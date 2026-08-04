"""Check-class parity across machine-readable surfaces (ADR 0004).

Three surfaces carry the frozen v1 class set: the CheckClass contract
in contracts/schemas.py, the delimited machine block in SPEC.md §2,
and — from the Phase 1 freeze commit — the top-level classes list in
evals/eval_config.yaml. This test compares them as exact sets; it
parses only the delimited SPEC block, never arbitrary prose.

Push 2 form: two-way (SPEC block == CheckClass). The freeze commit
extends this file to the three-way comparison.
"""

from __future__ import annotations

from pathlib import Path

from contracts.schemas import CHECK_CLASSES

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "SPEC.md"
BLOCK_BEGIN = "<!-- check-classes:begin -->"
BLOCK_END = "<!-- check-classes:end -->"


def spec_block_classes() -> list[str]:
    text = SPEC_PATH.read_text(encoding="utf-8")
    assert text.count(BLOCK_BEGIN) == 1, "SPEC must carry exactly one class block"
    assert text.count(BLOCK_END) == 1, "SPEC must carry exactly one class block"
    block = text.split(BLOCK_BEGIN, 1)[1].split(BLOCK_END, 1)[0]
    return [line.strip() for line in block.splitlines() if line.strip()]


def test_spec_block_matches_check_class_contract():
    spec_classes = spec_block_classes()
    assert len(spec_classes) == len(set(spec_classes)), "SPEC block has duplicates"
    assert set(spec_classes) == set(CHECK_CLASSES)

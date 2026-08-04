"""Check-class parity across machine-readable surfaces (ADR 0004).

Three surfaces carry the frozen v1 class set: the CheckClass contract
in contracts/schemas.py, the delimited machine block in SPEC.md §2,
and the top-level classes list in evals/eval_config.yaml (this file's
three-way form lands with the Phase 1 freeze commit). The surfaces
compare as exact sets; only the delimited SPEC block is parsed, never
arbitrary prose.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from contracts.schemas import CHECK_CLASSES

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "SPEC.md"
# Explicit root path — never glob: fixture snapshots contain
# same-named placeholder gate files under fixtures/repos/*/evals/.
CONFIG_PATH = REPO_ROOT / "evals" / "eval_config.yaml"
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


def test_three_way_parity_with_eval_config():
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    config_classes = config["classes"]
    assert len(config_classes) == len(set(config_classes))
    assert set(config_classes) == set(CHECK_CLASSES) == set(spec_block_classes())

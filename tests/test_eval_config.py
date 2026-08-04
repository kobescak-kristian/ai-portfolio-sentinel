"""Eval-config freeze tests (BLUEPRINT §5 quantization mandate).

Re-derives every quantized integer in evals/eval_config.yaml from the
locked Decimal ratios at the committed fixture counts, and pins the
config to the committed JSONL corpus data. All threshold arithmetic is
Decimal — binary floats never touch a ratio.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path

import yaml

from contracts.schemas import CHECK_CLASSES

REPO_ROOT = Path(__file__).resolve().parents[1]
# Explicit root path — never glob: fixture snapshots contain
# same-named placeholder gate files under fixtures/repos/*/evals/.
CONFIG_PATH = REPO_ROOT / "evals" / "eval_config.yaml"
ANSWER_KEY = REPO_ROOT / "evals" / "answer_key.jsonl"
CLEAN_SURFACES = REPO_ROOT / "evals" / "clean_surfaces.jsonl"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_root_config_is_the_real_config_not_a_fixture_placeholder():
    config = load_config()
    assert config.get("placeholder") is None
    assert config["schema_version"] == 1
    assert "classes" in config and "thresholds" in config


def test_class_parity_with_contract():
    config = load_config()
    assert config["classes"] == list(CHECK_CLASSES)


def test_fixture_counts_match_committed_corpus():
    config = load_config()
    counts = config["fixture_counts"]
    key_rows = load_jsonl(ANSWER_KEY)
    clean_rows = load_jsonl(CLEAN_SURFACES)

    assert counts["injected_total"] == len(key_rows) == 60
    per_class = {cls: 0 for cls in CHECK_CLASSES}
    for row in key_rows:
        per_class[row["check_class"]] += 1
    assert counts["injected_per_class"] == per_class
    assert counts["clean_units_total"] == len(clean_rows)
    snapshots = {row["snapshot"] for row in key_rows} | {
        row["snapshot"] for row in clean_rows
    }
    assert counts["snapshots"] == len(snapshots)


def test_quantized_integers_equal_decimal_formulas():
    config = load_config()
    thresholds = config["thresholds"]
    counts = config["fixture_counts"]
    n_pooled = counts["injected_total"]
    n_clean = counts["clean_units_total"]

    pooled_ratio = Decimal(thresholds["pooled_recall"]["ratio_min"])
    assert thresholds["pooled_recall"]["max_misses"] == n_pooled - math.ceil(
        pooled_ratio * n_pooled
    )

    per_class_ratio = Decimal(thresholds["per_class_recall"]["ratio_min"])
    for cls, n_cls in counts["injected_per_class"].items():
        expected = n_cls - math.ceil(per_class_ratio * n_cls)
        assert thresholds["per_class_recall"]["max_misses_per_class"] == expected, cls

    flag_ratio = Decimal(thresholds["clean_false_flag"]["ratio_max"])
    assert thresholds["clean_false_flag"]["max_flagged_clean_units"] == math.floor(
        flag_ratio * n_clean
    )

    # precision is emitted-count-dependent by design: the ratio is
    # locked, the integer restates at gate time — assert it is NOT
    # hardcoded and the ratio parses as an exact Decimal.
    assert Decimal(thresholds["pooled_precision"]["ratio_min"]) == Decimal("0.90")
    assert "max_false_positives" not in thresholds["pooled_precision"]


def test_locked_ratio_values():
    thresholds = load_config()["thresholds"]
    assert Decimal(thresholds["pooled_recall"]["ratio_min"]) == Decimal("0.85")
    assert Decimal(thresholds["per_class_recall"]["ratio_min"]) == Decimal("0.80")
    assert Decimal(thresholds["clean_false_flag"]["ratio_max"]) == Decimal("0.10")


def test_invariants_all_true_and_gate_fields():
    config = load_config()
    assert config["invariants"] == {
        "every_task_terminal": True,
        "zero_lost_tasks": True,
        "idempotent_rerun": True,
        "dedup_correct_on_doubled_fixture_run": True,
    }
    assert config["gate"] == {
        "official_model": "sonnet",
        "dev_model": "haiku",
        "max_regates": 1,
    }

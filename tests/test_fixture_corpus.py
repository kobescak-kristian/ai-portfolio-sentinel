"""Fixture-corpus integrity tests (BLUEPRINT §5; ADR 0004; SPEC §4).

Mechanical checks that the committed corpus, answer key and clean
inventory agree with each other, with the fixture bytes, and with the
scoring contract in evals/SCORING.md.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from contracts.schemas import CHECK_CLASSES

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REPOS = REPO_ROOT / "fixtures" / "repos"
ANSWER_KEY = REPO_ROOT / "evals" / "answer_key.jsonl"
CLEAN_SURFACES = REPO_ROOT / "evals" / "clean_surfaces.jsonl"
LINK_TRUTH = REPO_ROOT / "fixtures" / "link_truth.jsonl"
CONFIG_PATH = REPO_ROOT / "evals" / "eval_config.yaml"

REQUIRED_FILES = ["STATE.md", ".githooks/pre-push", "evals/eval_config.yaml"]
REQUIRED_HEADERS = [
    "## Problem",
    "## Solution",
    "## System",
    "## Outcome",
    "## Version Log",
]

KEY_FIELDS = {
    "injection_id": str,
    "snapshot": str,
    "surface": str,
    "check_class": str,
    "location": str,
    "expected_finding": str,
}
CLEAN_FIELDS = {
    "clean_id": str,
    "snapshot": str,
    "surface": str,
    "check_class": str,
    "location": str,
    "expected": str,
    "provenance": str,
}
CLEAN_PROVENANCES = {"generation", "d6-item1-reconciliation-2026-08-04"}


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_blind_sample_module():
    spec = importlib.util.spec_from_file_location(
        "blind_sample", REPO_ROOT / "evals" / "blind_sample.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KEY_ROWS = load_jsonl(ANSWER_KEY)
CLEAN_ROWS = load_jsonl(CLEAN_SURFACES)
LINK_ROWS = load_jsonl(LINK_TRUTH)


def fixture_lines(snapshot: str, rel: str) -> list[str]:
    return (FIXTURE_REPOS / snapshot / rel).read_text(encoding="utf-8").split("\n")


def split_location(location: str) -> tuple[str, int | None]:
    if ":" in location:
        path, _, line = location.rpartition(":")
        return path, int(line)
    return location, None


# --- JSONL schemas ----------------------------------------------------------


def test_answer_key_schema_and_sorting():
    assert len(KEY_ROWS) == 60
    for row in KEY_ROWS:
        assert set(row) == set(KEY_FIELDS)
        for field, kind in KEY_FIELDS.items():
            assert isinstance(row[field], kind), (row["injection_id"], field)
        assert row["surface"] == f"{row['snapshot']}/{split_location(row['location'])[0]}"
    ids = [row["injection_id"] for row in KEY_ROWS]
    assert ids == sorted(ids)
    assert len(set(ids)) == 60


def test_clean_inventory_schema():
    for row in CLEAN_ROWS:
        assert set(row) == set(CLEAN_FIELDS)
        for field, kind in CLEAN_FIELDS.items():
            assert isinstance(row[field], kind), (row["clean_id"], field)
        assert row["expected"] == "NONE"
        assert row["provenance"] in CLEAN_PROVENANCES
    ids = [row["clean_id"] for row in CLEAN_ROWS]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_reconciliation_units_are_the_eight_ruled_ones():
    added = [r for r in CLEAN_ROWS if r["provenance"] != "generation"]
    assert len(added) == 8
    assert all(r["check_class"] == "missing-synthetic-label" for r in added)
    assert sorted(r["snapshot"] for r in added) == [
        f"synthetic-{i:02d}" for i in range(1, 9)
    ]
    for row in added:
        path, line = split_location(row["location"])
        assert path == "README.md" and line is not None
        text = fixture_lines(row["snapshot"], path)[line - 1]
        assert text.startswith("- 2026-07-") and "demo numbers refreshed" in text


def test_class_tokens_and_per_class_totals():
    per_class = {cls: 0 for cls in CHECK_CLASSES}
    for row in KEY_ROWS:
        assert row["check_class"] in CHECK_CLASSES
        per_class[row["check_class"]] += 1
    assert all(count == 10 for count in per_class.values()), per_class
    for row in CLEAN_ROWS:
        assert row["check_class"] in CHECK_CLASSES


def test_unique_tuples_and_no_overlap():
    positives = [(r["check_class"], r["surface"], r["location"]) for r in KEY_ROWS]
    assert len(set(positives)) == len(positives)
    cleans = [(r["check_class"], r["surface"], r["location"]) for r in CLEAN_ROWS]
    assert len(set(cleans)) == len(cleans)
    assert not (set(positives) & set(cleans))


# --- positives match final fixture content ----------------------------------


@pytest.mark.parametrize(
    "row", KEY_ROWS, ids=[r["injection_id"] for r in KEY_ROWS]
)
def test_positive_location_matches_fixture_content(row):
    cls = row["check_class"]
    snapshot = row["snapshot"]
    path, line = split_location(row["location"])

    if cls == "missing-required-file":
        assert line is None
        assert path in REQUIRED_FILES
        assert not (FIXTURE_REPOS / snapshot / path).exists()
        return

    lines = fixture_lines(snapshot, path)
    assert line is not None and 1 <= line <= len(lines), row["location"]
    text = lines[line - 1]

    if cls == "broken-link":
        assert ".example.invalid" in text
    elif cls == "number-mismatch":
        assert path == "README.md"
        metric = text.lstrip("- ").split(":")[0]
        eval_lines = fixture_lines(snapshot, "EVAL_RESULTS.md")
        counterpart = [
            l for l in eval_lines if l.startswith(f"- {metric}:")
        ]
        assert len(counterpart) == 1
        assert counterpart[0] != text, "figure must diverge from EVAL_RESULTS"
    elif cls == "stale-STATE-marker":
        assert path == "STATE.md"
        assert text.startswith("- 2026-")
        log_start = lines.index("## Change log") + 1
        assert line > log_start
    elif cls == "missing-synthetic-label":
        assert text.startswith("- ")
        following = lines[line] if line < len(lines) else ""
        assert following != "  (synthetic figure)"
    elif cls == "readme-structure":
        assert path == "README.md"
        assert text in REQUIRED_HEADERS
        in_file = [l for l in lines if l in REQUIRED_HEADERS]
        assert in_file != REQUIRED_HEADERS, "structure positive on a valid README"


def test_non_missing_file_positives_reference_existing_paths():
    for row in KEY_ROWS:
        if row["check_class"] == "missing-required-file":
            continue
        path, _ = split_location(row["location"])
        assert (FIXTURE_REPOS / row["snapshot"] / path).exists(), row["injection_id"]


def test_dead_urls_unique_and_invalid_tld():
    dead = [r for r in LINK_ROWS if r["status"] == "dead"]
    assert len(dead) == 10
    urls = [r["url"] for r in dead]
    assert len(set(urls)) == 10
    assert all(".example.invalid" in url for url in urls)
    bl_rows = [r for r in KEY_ROWS if r["check_class"] == "broken-link"]
    dead_locations = {(r["snapshot"], f"{r['path']}:{r['line']}") for r in dead}
    key_locations = {(r["snapshot"], r["location"]) for r in bl_rows}
    assert key_locations == dead_locations


def test_link_truth_covers_every_fixture_url():
    seen = set()
    for snap_dir in sorted(FIXTURE_REPOS.iterdir()):
        for f in sorted(snap_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(snap_dir).as_posix()
            for n, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
                if "https://" in line:
                    seen.add((snap_dir.name, rel, n))
    recorded = {(r["snapshot"], r["path"], r["line"]) for r in LINK_ROWS}
    assert recorded == seen


# --- clean-unit structure ----------------------------------------------------


def test_valid_readmes_and_structure_clean_units():
    valid = []
    for snap_dir in sorted(FIXTURE_REPOS.iterdir()):
        lines = fixture_lines(snap_dir.name, "README.md")
        if [l for l in lines if l in REQUIRED_HEADERS] == REQUIRED_HEADERS:
            valid.append(snap_dir.name)
    structure_cleans = [
        r for r in CLEAN_ROWS if r["check_class"] == "readme-structure"
    ]
    assert sorted(r["snapshot"] for r in structure_cleans) == valid
    for row in structure_cleans:
        assert row["location"] == "README.md", "structure clean units are file-level"


def test_line_level_clean_units_reference_real_lines():
    for row in CLEAN_ROWS:
        path, line = split_location(row["location"])
        target = FIXTURE_REPOS / row["snapshot"] / path
        assert target.exists(), row["clean_id"]
        if row["check_class"] == "missing-required-file":
            assert line is None and path in REQUIRED_FILES
        elif row["check_class"] == "readme-structure":
            assert line is None
        else:
            lines = fixture_lines(row["snapshot"], path)
            assert line is not None and 1 <= line <= len(lines), row["clean_id"]


def test_control_eligibility_and_disjoint_controls():
    blind = load_blind_sample_module()
    eligible = {
        cls: [
            c["clean_id"]
            for c in CLEAN_ROWS
            if c["check_class"] == cls and blind.control_eligible(c, KEY_ROWS)
        ]
        for cls in CHECK_CLASSES
    }
    for cls, ids in eligible.items():
        assert len(ids) >= 2, (cls, ids)
    picks = blind.selections()
    assert len(picks["sample_positives"]) == 24
    sample_controls = set(picks["sample_controls"])
    full_controls = set(picks["full_controls"])
    assert len(sample_controls) == 6 and len(full_controls) == 6
    assert not (sample_controls & full_controls)


def test_clean_total_matches_eval_config():
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert config["fixture_counts"]["clean_units_total"] == len(CLEAN_ROWS)

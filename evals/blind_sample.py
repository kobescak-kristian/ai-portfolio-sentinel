"""Deterministic blind-review packet generator (SPEC §4, ADR 0004).

Builds the review packet and its ID map for the answer-key review:

    python evals/blind_sample.py sample
    python evals/blind_sample.py full

Sample mode: 4 positives per class (24 = 40% of 60) + 1 clean control
per class. Full mode: all 60 positives + 1 clean control per class,
disjoint from the sample controls. The reviewer sees excerpts with
actual fixture line numbers but never the expected answers; packet
shape may reveal the candidate class (the review certifies defect
presence or absence and location — not blind class discovery).

Determinism: everything derives from the committed SEED — selection,
opaque review IDs, item order. No timestamps, no random UUIDs, no
absolute paths, no environment reads; compact sorted-key JSON with LF
newlines on every platform. Re-running a mode reproduces its packet
and ID map byte-identically.

Control eligibility: a clean unit is control-eligible only if its
snapshot carries ZERO injections of the same class (so a control's
excerpt can never contain a same-class defect), and — for
missing-synthetic-label — only if the unit is an unlabeled-by-design
numeric line (the hard control demanded by the protocol). Units added
by the D6 item-1 inventory reconciliation (provenance
"d6-item1-reconciliation-2026-08-04") are fully scorable clean units
but are NOT control-eligible: the review controls were drawn from the
review-time inventory, and pinning eligibility to generation
provenance keeps the committed packets byte-reproducible.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

SEED = 20260804
REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REPOS = REPO_ROOT / "fixtures" / "repos"
EVALS = REPO_ROOT / "evals"

CLASS_ORDER = [
    "broken-link",
    "number-mismatch",
    "stale-STATE-marker",
    "missing-required-file",
    "missing-synthetic-label",
    "readme-structure",
]
POSITIVES_PER_CLASS_SAMPLE = 4
CONTEXT_LINES = 2
REQUIRED_FILES = ["STATE.md", ".githooks/pre-push", "evals/eval_config.yaml"]
REQUIRED_HEADERS = [
    "## Problem",
    "## Solution",
    "## System",
    "## Outcome",
    "## Version Log",
]

RESPONSE_CONVENTION = (
    "For each item, report EVERY defect of the item's class visible in "
    "the shown material, each as '<class> <path>:<line>' (file-level "
    "classes: '<class> <path>'), or exactly 'NONE' if the shown material "
    "is clean. Location conventions: a missing required header maps to "
    "the line of the first following required header still present (a "
    "missing final '## Version Log' maps to the '## Outcome' line); "
    "reordered sections map to the line of the first required header "
    "that violates the sequence Problem, Solution, System, Outcome, "
    "Version Log; a missing required file maps to its path with no line "
    "suffix."
)


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def fixture_lines(snapshot: str, rel: str) -> list[str]:
    return (FIXTURE_REPOS / snapshot / rel).read_text(encoding="utf-8").split("\n")


def numbered(lines: list[str], start: int, end: int) -> list[str]:
    """Inclusive 1-based [start, end] slice with actual line numbers."""
    start = max(1, start)
    end = min(len(lines), end)
    return [f"{n:4d}| {lines[n - 1]}" for n in range(start, end + 1)]


def split_location(location: str) -> tuple[str, int | None]:
    if ":" in location:
        path, _, line = location.rpartition(":")
        return path, int(line)
    return location, None


def review_id(mode: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{SEED}:{mode}:{source_id}".encode("utf-8")).hexdigest()
    return f"rev-{digest[:10]}"


def section_span(lines: list[str], header: str) -> tuple[int, int]:
    start = lines.index(header) + 1  # 1-based
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j] in REQUIRED_HEADERS:
            end = j
            break
    return start, end


def figure_block_span(lines: list[str]) -> tuple[int, int]:
    hits = [
        n
        for n, line in enumerate(lines, 1)
        if line.startswith("- ") and (" percent" in line or " ms" in line or line.split(": ")[-1].isdigit())
    ]
    return min(hits), max(hits) + 1


def build_item(row: dict, kind: str) -> tuple[dict, dict]:
    """Return (packet_item_content, id_map_excerpt_metadata)."""
    cls = row["check_class"]
    snapshot = row["snapshot"]
    path, line = split_location(row["location"])

    if cls == "missing-required-file":
        listing = sorted(
            f.relative_to(FIXTURE_REPOS / snapshot).as_posix()
            for f in (FIXTURE_REPOS / snapshot).rglob("*")
            if f.is_file()
        )
        content = {"file_listing": listing, "required_files": sorted(REQUIRED_FILES)}
        meta = {"excerpt": "file-listing"}
    elif cls == "readme-structure":
        lines = fixture_lines(snapshot, "README.md")
        headers = [
            {"line": n, "header": text}
            for n, text in enumerate(lines, 1)
            if text in REQUIRED_HEADERS
        ]
        content = {
            "path": "README.md",
            "required_sequence": REQUIRED_HEADERS,
            "headers_in_file_order": headers,
        }
        meta = {"excerpt": "header-sequence"}
    elif cls == "number-mismatch":
        readme = fixture_lines(snapshot, "README.md")
        evalr = fixture_lines(snapshot, "EVAL_RESULTS.md")
        r_start, r_end = figure_block_span(readme)
        e_start, e_end = figure_block_span(evalr)
        content = {
            "readme_excerpt": {"path": "README.md", "lines": numbered(readme, r_start, r_end)},
            "eval_results_excerpt": {
                "path": "EVAL_RESULTS.md",
                "lines": numbered(evalr, e_start, e_end),
            },
        }
        meta = {"excerpt": f"README.md:{r_start}-{r_end}+EVAL_RESULTS.md:{e_start}-{e_end}"}
    elif cls == "stale-STATE-marker":
        lines = fixture_lines(snapshot, "STATE.md")
        cur_start, cur_end = section_span(lines, "## Current state")
        log_start = lines.index("## Change log") + 1
        content = {
            "path": "STATE.md",
            "current_state_excerpt": numbered(lines, cur_start, cur_end),
            "change_log_excerpt": numbered(lines, log_start, len(lines)),
        }
        meta = {"excerpt": f"STATE.md:{cur_start}-{cur_end}+{log_start}-{len(lines)}"}
    else:  # broken-link, missing-synthetic-label: line + surrounding context
        lines = fixture_lines(snapshot, path)
        start, end = line - CONTEXT_LINES, line + CONTEXT_LINES
        content = {"path": path, "lines": numbered(lines, start, end)}
        meta = {"excerpt": f"{path}:{max(1, start)}-{min(len(lines), end)}"}

    item = {"packet_form": cls, "material": content}
    return item, meta


def control_eligible(clean_row: dict, positives: list[dict]) -> bool:
    cls = clean_row["check_class"]
    snapshot = clean_row["snapshot"]
    if clean_row.get("provenance", "generation") != "generation":
        return False
    if any(
        p["check_class"] == cls and p["snapshot"] == snapshot for p in positives
    ):
        return False
    if cls == "missing-synthetic-label":
        path, line = split_location(clean_row["location"])
        lines = fixture_lines(snapshot, path)
        if line is None or line >= len(lines):
            return False
        # unlabeled-by-design only: the next line must NOT be a label
        if lines[line] == "  (synthetic figure)":
            return False
    return True


def selections() -> dict:
    positives = load_jsonl(EVALS / "answer_key.jsonl")
    cleans = load_jsonl(EVALS / "clean_surfaces.jsonl")
    by_class_pos = {
        cls: sorted(p["injection_id"] for p in positives if p["check_class"] == cls)
        for cls in CLASS_ORDER
    }
    by_class_clean = {
        cls: sorted(
            c["clean_id"]
            for c in cleans
            if c["check_class"] == cls and control_eligible(c, positives)
        )
        for cls in CLASS_ORDER
    }
    rng = random.Random(SEED)
    picks = {"sample_positives": [], "sample_controls": [], "full_controls": []}
    for cls in CLASS_ORDER:
        picks["sample_positives"].extend(
            rng.sample(by_class_pos[cls], POSITIVES_PER_CLASS_SAMPLE)
        )
        controls = rng.sample(by_class_clean[cls], 2)
        picks["sample_controls"].append(controls[0])
        picks["full_controls"].append(controls[1])
    picks["full_positives"] = sorted(p["injection_id"] for p in positives)
    return picks


def build_packet(mode: str) -> None:
    positives = {p["injection_id"]: p for p in load_jsonl(EVALS / "answer_key.jsonl")}
    cleans = {c["clean_id"]: c for c in load_jsonl(EVALS / "clean_surfaces.jsonl")}
    picks = selections()
    if mode == "sample":
        chosen = [(pid, "positive") for pid in picks["sample_positives"]]
        chosen += [(cid, "control") for cid in picks["sample_controls"]]
    else:
        chosen = [(pid, "positive") for pid in picks["full_positives"]]
        chosen += [(cid, "control") for cid in picks["full_controls"]]

    items = []
    id_map = []
    for source_id, kind in chosen:
        row = positives[source_id] if kind == "positive" else cleans[source_id]
        rid = review_id(mode, source_id)
        item_content, meta = build_item(row, kind)
        items.append(
            {
                "review_id": rid,
                "instructions": RESPONSE_CONVENTION,
                **item_content,
            }
        )
        id_map.append(
            {
                "review_id": rid,
                "source_id": source_id,
                "source_kind": kind,
                "mode": mode,
                "snapshot": row["snapshot"],
                "surface": row["surface"],
                "expected_location": row["location"] if kind == "positive" else "NONE",
                **meta,
            }
        )

    order = random.Random(f"{SEED}:{mode}:order")
    index = list(range(len(items)))
    order.shuffle(index)
    items = [items[i] for i in index]
    id_map_by_rid = {entry["review_id"]: entry for entry in id_map}
    id_map = [id_map_by_rid[item["review_id"]] for item in items]

    packet_path = EVALS / f"blind_packet_{mode}.jsonl"
    map_path = EVALS / f"blind_id_map_{mode}.jsonl"
    with open(packet_path, "w", encoding="utf-8", newline="\n") as fh:
        for item in items:
            fh.write(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n")
    with open(map_path, "w", encoding="utf-8", newline="\n") as fh:
        for entry in id_map:
            fh.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"{packet_path.name}: {len(items)} items")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("sample", "full"):
        print("usage: python evals/blind_sample.py {sample|full}")
        return 2
    build_packet(sys.argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

<!-- Scoring contract for the frozen Phase 1 eval gate. Freezes with
the corpus (BLUEPRINT §5; ADR 0004). Changeable only by ADR before a
run, never after. -->

# SCORING — ai-portfolio-sentinel eval gate

This contract defines how emitted findings score against
`evals/answer_key.jsonl` (60 positives) and
`evals/clean_surfaces.jsonl` (166 clean units; 158 at generation plus
8 restored by the D6 item-1 reconciliation of 2026-08-04, marked by
their `provenance` field — fully scorable, excluded only from review
control-eligibility). The future eval harness implements this
contract; it does not reinterpret it.

## 1. Positive matching

A true positive requires one-to-one matching on all three of
`check_class`, `surface`, `location`.

- Location is exact for all line-level classes.
- `missing-required-file` matches at path level (no line suffix).
- One emitted finding may satisfy at most one answer-key row.
- Duplicates: the first emitted finding matching an answer-key row is
  the true positive; every additional duplicate is a false positive.
- `expected_finding` text is documentation only and is never matched.

### readme-structure locations (frozen semantics)

- Missing header: the line of the first following required header
  still present. A missing final `## Version Log` maps to the line of
  `## Outcome`.
- Reordered sections: the line of the first required header that
  violates the frozen sequence `## Problem`, `## Solution`,
  `## System`, `## Outcome`, `## Version Log`.

## 2. Clean-unit matching

### readme-structure

A structurally valid README is ONE file-level clean unit
(`location` = `README.md`, no line suffix). Any emitted
readme-structure finding on a valid README maps to that single unit
regardless of the emitted line. Multiple false structure findings on
one valid README count individually as false positives for pooled
precision but once in the clean-unit false-flag numerator.

### All other classes

False findings map to clean units by exact
`(check_class, surface, location)`. One emitted false finding maps to
at most one clean unit. A false finding matching no clean unit still
counts for pooled precision; it never enters the false-flag numerator.

## 3. Metrics and quantization (Decimal arithmetic only)

- Pooled precision = true positives / all emitted findings; binding
  threshold ≥ 0.90 over the actual emitted count:
  `allowed_fp = emitted - ceil(Decimal("0.90") * emitted)`.
  Reference at 60 emitted flags: 6 false positives.
- Pooled recall ≥ 0.85: `max_misses = 60 - ceil(Decimal("0.85") * 60)`
  = 9 (51/60 passes exactly).
- Per-class recall ≥ 0.80 at 10 positives per class: 2 misses
  permitted; 3 misses (0.70) FAIL.
- Clean false-flag rate = distinct clean units receiving ≥ 1 false
  finding / total clean units; ≤ 0.10. Binding integer
  `floor(Decimal("0.10") * 166)` = 16.
- Invariants at 100%, tolerance zero: every task terminal, zero lost
  tasks, idempotent rerun, dedup correctness on a doubled fixture run.

Binary floating-point arithmetic is prohibited in threshold math;
ratios parse through `Decimal` from their quoted YAML strings.

## 4. Link truth

Fixture link liveness is the committed corpus property in
`fixtures/link_truth.jsonl`. The eval harness resolves fixture links
through that map (or a stub of it) and never makes live network
requests for fixture link truth — transient third-party HTTP behavior
cannot affect the official gate.

## 5. Review protocol linkage

The answer-key review protocol (SPEC §4; packet forms, opaque IDs,
disagreement arithmetic) is implemented by `evals/blind_sample.py`;
its evidence lives in `evals/reviewer_raw.md` and
`evals/adjudication.md`. The reviewer is blind to expected answers and
locations; packet shape may reveal the candidate class.

<!-- Adjudication record for the sample-pass answer-key review
(SPEC §4; ADR 0004). Mechanical matching by the committed ID map;
generated from reviewer_raw.md verbatim labels. -->

# Adjudication — sample pass (2026-08-04)

Disagreement = mismatch on class or location for a sampled positive.
Denominator: the 24 sampled positives only; clean controls are
adjudicated individually and excluded from the rate.

## Result

- Sampled positives: 24; target-injection class/location
  disagreements: **0** — rate 0/24 = 0.0%. This rate counts only
  each packet's target injection; the reviewer returned 43 findings
  across the 24 positive packets in total, and every one is
  dispositioned in the additional-findings section below.
- Escalation threshold: 3 of 24 (12.5% > 10%). **Escalation NOT triggered; no full pass required.**
- Clean controls: 6 of 6 returned NONE; zero over-flags.
- Corpus-integrity events: none; no repair record required.
- Owner adjudication: zero disagreements arose, so no per-item
  ruling was required; the owner ratifies this record at the D6
  checkpoint. No disagreement remains unresolved at freeze.

## Per-item table

| review_id | source | expected | reviewer response | ruling |
|---|---|---|---|---|
| rev-64c397d80f | inj-021 | readme-structure README.md:18 | readme-structure README.md:18 | readme-structure README.md:36 | confirmed — reviewer identified the expected class and location |
| rev-87297b2c9b | inj-033 | readme-structure README.md:12 | readme-structure README.md:12 | readme-structure README.md:24 | confirmed — reviewer identified the expected class and location |
| rev-5225f56ff9 | inj-058 | readme-structure README.md:25 | readme-structure README.md:25 | confirmed — reviewer identified the expected class and location |
| rev-60a6f4bb03 | inj-060 | stale-STATE-marker STATE.md:16 | stale-STATE-marker STATE.md:15 | stale-STATE-marker STATE.md:16 | confirmed — reviewer identified the expected class and location |
| rev-b631325270 | inj-013 | broken-link README.md:15 | broken-link README.md:15 | confirmed — reviewer identified the expected class and location |
| rev-03796b178d | inj-011 | stale-STATE-marker STATE.md:15 | stale-STATE-marker STATE.md:15 | stale-STATE-marker STATE.md:16 | confirmed — reviewer identified the expected class and location |
| rev-cc8accb34d | inj-045 | readme-structure README.md:24 | readme-structure README.md:18 | readme-structure README.md:24 | confirmed — reviewer identified the expected class and location |
| rev-a3876406a1 | inj-017 | missing-synthetic-label EVAL_RESULTS.md:14 | missing-synthetic-label EVAL_RESULTS.md:14 | confirmed — reviewer identified the expected class and location |
| rev-3c14ac7b26 | inj-008 | number-mismatch README.md:29 | number-mismatch README.md:25 | number-mismatch README.md:27 | number-mismatch README.md:29 | confirmed — reviewer identified the expected class and location |
| rev-1c1a29c136 | inj-001 | broken-link README.md:15 | broken-link README.md:15 | confirmed — reviewer identified the expected class and location |
| rev-12d2e7a363 | clean-139 | NONE (clean control) | NONE | confirmed clean — control returned NONE |
| rev-65823d6e49 | clean-129 | NONE (clean control) | NONE | confirmed clean — control returned NONE |
| rev-6d5b98a66e | inj-057 | missing-synthetic-label EVAL_RESULTS.md:13 | missing-synthetic-label EVAL_RESULTS.md:12 | missing-synthetic-label EVAL_RESULTS.md:13 | confirmed — reviewer identified the expected class and location |
| rev-cd9ef4c5ad | clean-124 | NONE (clean control) | NONE | confirmed clean — control returned NONE |
| rev-5974b87148 | inj-003 | missing-required-file .githooks/pre-push | missing-required-file .githooks/pre-push | confirmed — reviewer identified the expected class and location |
| rev-c72e20139a | inj-039 | missing-required-file evals/eval_config.yaml | missing-required-file STATE.md | missing-required-file evals/eval_config.yaml | confirmed — reviewer identified the expected class and location |
| rev-ad01bae500 | inj-020 | number-mismatch README.md:29 | number-mismatch README.md:25 | number-mismatch README.md:27 | number-mismatch README.md:29 | confirmed — reviewer identified the expected class and location |
| rev-1c1f9a76c2 | inj-030 | missing-synthetic-label EVAL_RESULTS.md:13 | missing-synthetic-label EVAL_RESULTS.md:12 | missing-synthetic-label EVAL_RESULTS.md:13 | confirmed — reviewer identified the expected class and location |
| rev-75b3d8303f | inj-006 | number-mismatch README.md:25 | number-mismatch README.md:25 | number-mismatch README.md:27 | number-mismatch README.md:29 | confirmed — reviewer identified the expected class and location |
| rev-758c3d33c1 | inj-027 | missing-required-file .githooks/pre-push | missing-required-file .githooks/pre-push | missing-required-file evals/eval_config.yaml | confirmed — reviewer identified the expected class and location |
| rev-7ffb571618 | inj-026 | broken-link README.md:21 | broken-link README.md:21 | confirmed — reviewer identified the expected class and location |
| rev-d499f08e3a | clean-130 | NONE (clean control) | NONE | confirmed clean — control returned NONE |
| rev-f6dc71ef4e | inj-024 | stale-STATE-marker STATE.md:16 | stale-STATE-marker STATE.md:15 | stale-STATE-marker STATE.md:16 | confirmed — reviewer identified the expected class and location |
| rev-39139d84c0 | clean-108 | NONE (clean control) | NONE | confirmed clean — control returned NONE |
| rev-5cf915c1d2 | inj-049 | missing-synthetic-label EVAL_RESULTS.md:14 | missing-synthetic-label EVAL_RESULTS.md:14 | confirmed — reviewer identified the expected class and location |
| rev-cd6d6ab93e | clean-082 | NONE (clean control) | NONE | confirmed clean — control returned NONE |
| rev-88fa7c4281 | inj-014 | broken-link README.md:21 | broken-link README.md:21 | confirmed — reviewer identified the expected class and location |
| rev-b2bf44e21a | inj-048 | missing-required-file evals/eval_config.yaml | missing-required-file evals/eval_config.yaml | confirmed — reviewer identified the expected class and location |
| rev-c71fc8a971 | inj-007 | number-mismatch README.md:27 | number-mismatch README.md:25 | number-mismatch README.md:27 | number-mismatch README.md:29 | confirmed — reviewer identified the expected class and location |
| rev-055d2481ac | inj-051 | stale-STATE-marker STATE.md:15 | stale-STATE-marker STATE.md:15 | stale-STATE-marker STATE.md:16 | confirmed — reviewer identified the expected class and location |

## Additional-findings disposition (D6 owner ruling item 2, 2026-08-04)

The reviewer returned 43 findings across the 24 positive packets:
24 target findings plus 19 additional findings (mechanical count from
the verbatim labels; the ruling's provisional figure of 41/17 is
superseded by this count). Every additional finding was mechanically
adjudicated by its final (class, surface, location):

- Genuine co-occurring answer-key matches: **19 of 19** — packets for
  multi-injection snapshots legitimately show sibling defects of the
  same class, and the reviewer reported them.
- After deduplication: **16 unique answer-key rows** (inj-006,
  inj-007 and inj-008 were each observed twice from sibling packets).
- Reviewer over-flags: **0**.
- Genuine defects absent from the answer key: **0** — no
  corpus-integrity event; no repair, no full pass.
- Unique answer-key positives incidentally reviewed beyond the 24
  sampled targets: **13** (inj-012, inj-018, inj-019, inj-022,
  inj-023, inj-028, inj-029, inj-034, inj-038, inj-044, inj-052,
  inj-056, inj-059) — 37 of 60 key rows therefore carry review
  evidence.

## Clean-inventory reconciliation (D6 owner ruling item 1, 2026-08-04)

The demanded derivation of the missing-synthetic-label clean count
exposed a counting defect. Derivation at the review-time inventory:

- Correctly labelled figure units surviving injections: 64 baseline
  (8 per snapshot) − 10 label removals = **54**. No labelled unit was
  lost to file deletion (labels live in README/EVAL_RESULTS only).
- Designated unlabeled-by-design units: 3 per snapshot (two dated
  README Version Log entries + the STATE "Last demo refresh" line) =
  24 − 1 lost with synthetic-04's deleted STATE.md = **23 surviving**.
- Enumerated at generation: only **15** — the eight README
  "demo numbers refreshed" entries were excluded by an
  enumeration-rule scope accident (the rule matched only "scaffold
  committed"), an exclusion with no principled basis. The
  System-section prose version reference remains undesignated by
  design (a prose sentence, not a dated numeric unit line).

Ruled disposition: counting defect. The eight units were restored
append-only as clean-159..clean-166 with provenance
"d6-item1-reconciliation-2026-08-04" — fully scorable clean units,
excluded from review control-eligibility so the committed sample
packet and ID map remain byte-reproducible from the committed seed.

Corrected counts: missing-synthetic-label clean units 69 → **77**;
total clean units 158 → **166**; false-flag tolerance
floor(Decimal("0.10") × 166) = **16** (16/166 = 0.0964 PASS; 17/166 =
0.1024 FAIL). MANIFEST, eval_config.yaml, SCORING.md, the gate post
and the corpus tests were updated to the corrected counts.

# 0006 — Cross-run judgment finding identity

Status: ADOPTED

Date: 2026-08-20

## Context

The one permitted re-gate under `adr/0005-phase3-gate-remediation.md`
ran 2026-08-19 at source commit
`c12beee577b929f58cd6f91ff36d048fe955d73f` and recorded an honest
**OVERALL FAIL**. Every scoring threshold PASSED there — pooled
precision 60/60, pooled recall 60/60, per-class recall 10/10 on all six
classes, clean false flags 0/166 — as did `every_task_terminal` and
`zero_lost_tasks`. The failure is isolated to the two cross-run
invariants, `idempotent_rerun` and
`dedup_correct_on_doubled_fixture_run`, both traced to one cross-run
finding-identity defect. The full record is in `EVAL_RESULTS.md` (ONE
PERMITTED RE-GATE section) and `STATE.md`.

ADR 0005 does two things that make a new ADR mandatory before any
correction:

1. It names `agents/checker/evidence.py` among its frozen surfaces
   ("Non-goals / frozen surfaces").
2. Its "Failure outcome" section states that if the re-gate FAILs, the
   ADR "authorizes no further adjustment of any kind" and "any
   subsequent path requires a new owner-approved ADR".

The re-gate failed and has been recorded. This ADR is that new
owner-approved decision. It records the identity correction and its
required pre-validation evidence. It implements nothing.

Phase 3 remains OPEN. Q-77 remains OPEN. The one permitted re-gate is
CONSUMED, and no third gate run is authorized under the current
BLUEPRINT or ADR 0005.

## 1. Root cause

Free-form, model-selected excerpt spans currently participate in
persistent finding identity for the two judgment classes built through
`agents/checker/evidence.py`.

The consumed re-gate demonstrated this concretely on exactly one
semantic defect:

| Field | Value |
|---|---|
| Surface | `synthetic-05/EVAL_RESULTS.md` |
| Check class | `missing-synthetic-label` |
| Location | `EVAL_RESULTS.md:14` |
| Reason code | `FIGURE_WITHOUT_ADJACENT_SYNTHETIC_LABEL` |
| Frozen source line | `- Coverage: 85.5 percent` |
| Run 1 excerpt | `Coverage: 85.5 percent` |
| Run 2 excerpt | `- Coverage: 85.5 percent` |

Both excerpts passed host evidence validation: `evidence.py` requires
only that the excerpt appear verbatim *within* the cited source line
(`if item.excerpt not in source_line: raise EvidenceRejected`), so a
substring and the full line are equally admissible. The excerpt then
enters `normalized_content`, which `sentinel/lifecycle.py` feeds
through `compute_content_hash(location, normalized_content)` and then
`compute_fingerprint(surface, check_class, content_hash)`. The two
valid excerpt spans therefore produced different `content_hash` values
and different fingerprints while surface, check class, location and
reason code were identical — one semantic defect became one new finding
plus one auto-resolution. The lifecycle behaved correctly for the
fingerprints it was given; the identity handed to dedup was unstable.

This structural vulnerability predates the ADR-0005 remediation commit;
`evidence.py`, `contracts/schemas.py` and `sentinel/lifecycle.py` were
not among the files that commit changed.

## 2. Required identity invariant

For a judgment finding, arbitrary model-selected prose or span
variation must not change persistent identity.

The same:

- surface
- check class
- primary defect location
- closed reason code

must produce the same fingerprint irrespective of:

- full-line versus substring evidence
- list-marker inclusion
- whitespace or span selection
- secondary evidence excerpt
- secondary current-state anchor choice

Evidence validation remains fail-closed. Nothing in this invariant
relaxes what the host accepts as evidence; it changes only what
participates in identity.

## 3. Decision — adopt Option C

Separate persistent finding identity from descriptive, model-selected
evidence.

For the two judgment classes built through
`agents/checker/evidence.py`:

- Current: `normalized_content` includes the reason code plus one or
  more model-selected excerpt strings
  (`f"{reason_code}|{primary.excerpt}"`, or with the secondary excerpt
  appended for the two-evidence class).
- Approved target: `normalized_content = f"reason={reason_code}"`.

Persistent identity therefore remains derived through the existing,
unchanged formulas:

```
location      = host request path + model-selected,
                host-validated PRIMARY evidence line
content_hash  = hash(location + normalized_content)
fingerprint   = hash(surface + check_class + content_hash)
```

Judgment finding identity becomes, effectively:

```
(surface, check_class, primary location, closed validated reason_code)
```

Model-selected excerpt text, and stale-STATE secondary evidence
selection, remain validated and retained in `detail` as audit evidence,
but do **not** participate in persistent identity.

No change to `compute_content_hash` or `compute_fingerprint` is
approved. The `reason=<value>` shape also matches the existing house
convention already used by the deterministic checkers
(`url={url}`, `required_path={...}`, `label=...`); `evidence.py` is
today the sole outlier.

## 4. Why Option C

Option C:

- removes free-form model text from identity by construction;
- fixes both the observed excerpt-span instability and the
  secondary-evidence identity path;
- is fixture-independent;
- does not rely on prompt compliance;
- preserves the existing lifecycle;
- preserves the existing fingerprint formulas;
- requires no schema migration;
- does not touch deterministic checker identity;
- keeps model evidence auditable in detail;
- is the smallest implementation surface that satisfies §2.

## 5. Rejected alternatives

**A. Prompt-only full-line instruction.** Rejected: identity would
still depend on model compliance. A model that cites a substring
despite the instruction reproduces the exact failure.

**B. Host full-line canonicalization.** Rejected: document-content
changes would change identity, and secondary-anchor choice would still
affect identity.

**C. Separate identity from descriptive evidence.** Approved (§3).

**D. Tool-contract redesign.** Rejected now: a materially larger
surface for no additional identity guarantee needed for this failure.

**E. Text normalization of excerpts.** Rejected: heuristic, lossy and
fixture-shaped; it cannot generally canonicalize arbitrary valid
substrings of a line.

**F. Full-primary-line canonicalization plus secondary-evidence
removal.** Precision required here: **F *does* remove the
secondary-anchor fragmentation path.** It is not rejected for failing
to do so. F is rejected because primary source *content* itself would
remain part of identity, causing RESOLVED + NEW churn whenever the
source value or text at that location changes while the same semantic
defect continues.

## 6. Honest residuals and limits

- **The primary line remains part of identity.** This is deliberate and
  aligns with the frozen scorer's exact location matching
  (`evals/SCORING.md` §1).
- **Same-line collision.** Two genuinely distinct judgment defects of
  the same class and reason code, on the same surface and the exact
  same primary line, would collapse to one persistent identity. The
  frozen answer key currently contains no such collision and is
  one-to-one on `(check_class, surface, location)` — verified across
  all 60 rows and all 20 judgment rows — but this remains a design
  limitation, not a proof of general safety.
- **Changing source text at a stable location.** A semantic defect
  whose source text changes at the same primary location is
  deliberately treated as the same continuing finding.
- **`detail` immutability.** `detail` remains immutable after first-seen
  under the current ledger lifecycle. The immutability mechanism
  predates this change, but Option C increases when its consequence can
  be visible: a continuing finding can retain its original first-seen
  excerpt even after the source text at that location changes. `detail`
  is therefore defined explicitly as **FIRST-SEEN AUDIT EVIDENCE, not
  latest-run evidence.** Later per-attempt evidence persistence remains
  outside this remediation (it remains deferred per ADR 0005 §6).
- **Documentation wording.** `MODEL_CARD.md` §4 and `THREAT_MODEL.md`
  §4 are corrected at this adoption to describe current behavior
  truthfully and to distinguish it from this ADR's adopted-but-unbuilt
  target. Their final wording, once Option C lands, is an
  implementation-session concern.

## 7. Scoring disclosure

`evals/SCORING.md` §1 states that where several emitted findings match
one answer-key row, the first is the true positive and every additional
duplicate is a false positive.

Under Option C, within the affected judgment path, emissions with the
same:

```
(check_class, surface, primary location, closed reason_code)
```

but differing only in model-selected evidence text will collapse to the
same identity before persisted ledger scoring. The frozen duplicate
penalty therefore cannot distinguish *those particular same-identity
judgment emissions*.

This ADR makes **no** broader claim that the duplicate-as-FP rule
becomes unreachable. It remains fully reachable elsewhere, including
for deterministic classes and for judgment emissions that differ in
location, class or reason code.

The scorer, answer key, matching rules and thresholds are **not**
changed by this ADR. This is recorded here as an explicit trade-off
rather than left as a hidden consequence.

## 8. Migration and compatibility

Approved determination:

- no schema migration;
- no `schema_version` bump;
- no rewrite of historical gate databases;
- no rewrite of historical artifacts;
- no existing live judgment rows require migration.

The operational ledger was verified during design to contain no
judgment-class findings produced by `evidence.py`, and that verification
was repeated at adoption: the live ledger holds only deterministic
`readme-structure` findings and zero `agent_calls` rows.

If an old-rule OPEN judgment finding did exist, the first execution
under the new identity would resolve the old fingerprint and insert the
new fingerprint, after which the identity is stable. Historical rows
remain append-only and are never deleted.

## 9. Required pre-validation regression suite

Implementation and model-free regression tests land together; the
correction must not land untested. At minimum, the approved proof plan:

- **T1.** Same defect and location, different valid excerpt spans →
  same `normalized_content` and same fingerprint.
- **T2.** Distinct primary locations, surfaces or check classes →
  distinct identities.
- **T3.** `stale-STATE-marker`: the same primary defect line with
  different valid secondary anchors or excerpts → same identity;
  different primary defect lines → different identity.
- **T4.** Fabricated or non-verbatim evidence remains rejected
  fail-closed.
- **T5.** Deterministic-checker fingerprints remain unchanged.
- **T6.** Lifecycle rerun proxy: differing valid excerpts for the same
  semantic defect → `findings_new = 0`, the finding advances,
  `findings_resolved = 0`, `findings_still_open = 1`.
- **T7.** Old-rule ledger compatibility: the old identity resolves
  once, the new identity inserts once, nothing is deleted, and
  subsequent operation is stable.
- **T8.** Within-call duplicate emissions differing only by valid
  excerpt span collapse under the new judgment identity.

Wording that matters: **these tests are the only PRE-VALIDATION
evidence currently authorized for the correction.** They are not the
only evidence the correction will ever have. A future real-model
validation cycle may or may not later be authorized through a separate
owner-governed decision; this ADR does not decide that.

## 10. Deliberately unchanged

This decision freezes and does not modify:

`fixtures/`; `evals/answer_key.jsonl`; `evals/clean_surfaces.jsonl`;
`evals/SCORING.md`; scoring thresholds; `max_regates`; model
`claude-haiku-4-5-20251001`; `agents/checker/prompts.py`; the tool
schema; budget configuration; the `compute_content_hash` formula; the
`compute_fingerprint` formula; the ledger schema; lifecycle semantics;
deterministic checkers; historical gate artifacts and databases;
`SentinelDailyRun` activation or stub status; any Phase 4 work.

## 11. Validation governance boundary

This ADR authorizes only:

- the defined identity correction;
- its model-free regression tests;
- the necessary implementation documentation and state recording.

It does **not** authorize any new real-model gate or validation run.
The consumed ADR-0005 re-gate remains consumed. Any future validation
design or authorization is a separate owner-governed decision, taken
after implementation evidence exists.

## Consequences and risks

Judgment finding identity becomes stable against arbitrary valid
evidence-span variation, which is the precondition for the two failed
cross-run invariants to be meaningful at all. Accepted in exchange: a
narrower identity that cannot distinguish two distinct same-class,
same-reason defects on one line; a duplicate-penalty blind spot for
same-identity judgment emissions (§7); and a `detail` field that is
first-seen evidence rather than latest-run evidence (§6). Nothing here
claims the correction restores a passing gate — no gate is authorized
by this ADR, and no real-model evidence for the correction exists.

## Reopening conditions

This ADR reopens if evidence surfaces that a decision above rests on a
since-corrected fact, following the diagnosis-correction precedent on
record for this phase. Its scope otherwise closes when the correction
and its regression suite have landed.

## Owner approval

Approved by owner — 2026-08-20.

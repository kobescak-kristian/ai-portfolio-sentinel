# 0007 — Prospective Phase-3 validation protocol

Status: ADOPTED

Date: 2026-08-20

## Context

The one permitted re-gate under `adr/0005-phase3-gate-remediation.md`
ran 2026-08-19 at source commit
`c12beee577b929f58cd6f91ff36d048fe955d73f` and recorded an honest
**OVERALL FAIL**: every scoring threshold PASSED, and the failure was
isolated to the two cross-run invariants, traced to one cross-run
finding-identity defect. That re-gate is **CONSUMED**.
`adr/0006-judgment-finding-identity.md` (ADOPTED 2026-08-20) adopted
the identity correction, and it is IMPLEMENTED with its model-free
T1–T8 regression suite as its only current evidence. ADR 0005's
failure-outcome clause states that a failed re-gate authorizes no
further adjustment of any kind and that any subsequent path requires a
new owner-approved ADR; ADR 0006 §11 states that any future validation
design or authorization is a separate owner-governed decision.

This ADR is that decision. The prospective-validation design it adopts
was independently red-teamed and owner-approved before this adoption.
ARTIFACT_STANDARD v2.6 removed the former five-record decision-record
cap, so this ADR is added normally: no consolidation, deletion,
renumbering, or citation repair of any existing record.

This ADR authorizes governance only. It implements nothing, runs
nothing, and validates nothing. Phase 3 remains OPEN. Q-77 remains
OPEN.

## Decision

Exactly **one** new prospective Phase-3 validation cycle is authorized
for the current Sentinel-v1 Phase-3 validation lineage, under the
protocol below. The operative BLUEPRINT authorization is the dated
amendment BLUEPRINT §11(i), adopted with this ADR; ADR-0005 history is
not rewritten.

## 1. Preserved history and frozen surfaces

- ADR 0006 remains closed and unchanged.
- The original gate (2026-08-05) and the consumed re-gate (2026-08-19)
  remain historical FAILs; nothing in this ADR relabels either result.
- `evals/eval_config.yaml` `max_regates` remains **1**. The
  prospective cycle is a separately authorized new validation cycle
  under this ADR — it is not a second re-gate under ADR 0005.
- No fixture, answer key, scorer, threshold, model, prompt, identity,
  lifecycle, fingerprint, or frozen scoring-contract change of any
  kind is authorized.

## 2. Execution-validity predicates (all required)

A prospective execution is valid only if all of the following hold:

- both designated runs have status COMPLETED and the gate exits with
  code 0;
- zero FAILED or DEAD_LETTER tasks;
- every relevant `agent_call` is COMPLETED; zero FAILED, REJECTED,
  EXHAUSTED, or RESERVED `agent_calls`;
- Run-2 real-agent coverage, mechanical (an execution-validity
  predicate, not a new scoring metric): run 2 must contain more than
  zero relevant COMPLETED `agent_calls`, AND run 2's relevant
  COMPLETED `agent_call` count must equal run 1's;
- clean exact-SHA source attestation (the gate artifact records the
  exact pinned source SHA it ran from);
- the frozen scoring metrics, invariants, and cost caps are unchanged;
- the runner itself self-validates these execution-validity
  predicates — a post-run protocol alone is insufficient (a Stage-2
  implementation obligation under §6.B);
- independent read-only evidence reconstruction is required for BOTH
  PASS and FAIL before either result is recorded as verified;
- the gate uses fresh, non-default, initially nonexistent
  gate-root and artifact/evidence paths.

## 3. Consumption boundary (four dispositions, no fifth)

Let `C` = the count of persisted `agent_calls` rows across the
prospective run IDs with `reserved_eur_micros > 0`.

- **`C == 0` → PRE-CALL ABORT, always.** Any artifact produced is
  diagnostic and non-authoritative only. The cycle is NOT consumed.
  The same authorized SHA may be attempted later after the pre-call
  condition is resolved.
- **`C > 0` + complete independently verified PASS → PASS.** Bound
  consequences: Phase 3 may close; progression to Phase 4 becomes
  permitted under the Blueprint; Q-77 itself remains OPEN for its
  remaining production-readiness phases.
- **`C > 0` + a complete gate result that fails a binding condition,
  or a claimed PASS contradicted by independent reconstruction →
  VALID COMPLETED FAIL.** This is terminal for the current Sentinel-v1
  Phase-3 validation lineage. Phase 3 stays OPEN; no Phase 4.
- **`C > 0` + no parseable complete gate result → CONSUMED-PARTIAL /
  NO RESULT.** The cycle is consumed; no retry under this lineage.
  Phase 3 stays OPEN; no Phase 4. This disposition is explicitly NOT
  evidence that Sentinel failed a completed gate.

There is no fifth disposition.

## 4. SHA pinning and freeze

- The execution source SHA is pinned externally (in the private
  operations OS's Q-77 annotation) only after the Stage-2
  implementation (§6.B) is pushed and exact-SHA CI and the Phase-1
  freeze guard are green on that SHA.
- Once the Stage-2 SHA is externally pinned, ANY repository change
  before execution invalidates that pin: the SHA changes, and
  execution stops for reassessment.

## 5. Prospective execution preflight

All required BEFORE the consumption boundary of §3 can be crossed:

- fetch origin and verify `origin/main == HEAD`;
- `git rev-parse HEAD` equals the externally pinned Stage-2 SHA;
- `git status` is clean;
- the runner receives the mandatory required/pinned source SHA as an
  explicit input;
- Phase-1 freeze guard PASS;
- explicit fresh, non-default, initially nonexistent gate-root and
  artifact/evidence paths.

## 6. Bound sequence

- **A.** Stage-1 governance adoption commit (this ADR, the BLUEPRINT
  §11(i) amendment, the SPEC sync, and the STATE record).
- **A2.** Push Stage 1 and require exact-SHA CI success. Stage 2 MUST
  NOT begin before that exact Stage-1 SHA is green. No repository
  implementation is added to Stage 1 by this step.
- **B.** Separate Stage-2 runner implementation + model-free
  regression tests + STATE implementation/test record.
- **C.** Push Stage 2 and require exact-SHA CI and freeze green.
- **D.** Pin the resulting Stage-2 implementation SHA externally
  (§4).
- **E.** Only then may the single prospective validation cycle
  execute, under §2, §3 and §5.
- **F.** Independently verify and record the result.

## 7. Honest disclosures (stated, not resolved)

- Consumed re-gate model-selected primary-line stability was **20/20,
  not 60/60**: only the 20 judgment-class findings carry
  model-selected primary lines; the other 40/60 findings were
  deterministic host-computed and do not evidence model-selected
  location stability.
- The actual highest re-gate run cost was **636,623 micro-EUR**
  against the 750,000 micro-EUR per-run cap, leaving 113,377
  micro-EUR = **15.1% headroom**.
- The same frozen corpus is remediation-informed acceptance evidence,
  not unseen-generalization evidence: the ADR-0006 correction was
  designed with full knowledge of this corpus's failure.
- Retrospective application of the ADR-0006 identity rule to the
  consumed re-gate's evidence removes the observed identity
  fragmentation, but does NOT relabel the historical re-gate result;
  the OVERALL FAIL stands.
- The resulting tailoring/gate-shopping objection — that a correction
  informed by a failed run is then validated on the same frozen
  corpus — is disclosed here as a standing objection. It is not
  claimed to be eliminated.

## 8. Non-authorizations at Stage 1

This ADR's adoption commit authorizes and contains: this ADR, the
BLUEPRINT §11(i) amendment, the SPEC sync, the STATE record, and the
narrow documentation truth repair in `MODEL_CARD.md` and
`THREAT_MODEL.md`. It does NOT authorize, at Stage 1: any
implementation, any runner or test change, any model call, any gate
run, any validation artifact, any `max_regates` change, or any
Phase-4 work. Stage 2 and execution proceed only per the bound
sequence in §6.

## Consequences and risks

One tightly bounded validation path now exists for the implemented
ADR-0006 correction, with mechanical validity predicates, a
no-discretion consumption boundary, and independent verification on
both outcomes. Accepted in exchange: a VALID COMPLETED FAIL or a
CONSUMED-PARTIAL outcome is terminal for this lineage's Phase-3
validation — there is no further cycle under this ADR; and the §7
disclosures (remediation-informed corpus, 20/20 stability evidence
base, gate-shopping objection) stand unresolved. Nothing here claims
the correction produces a passing gate.

## Reopening conditions

This ADR reopens before execution only if evidence surfaces that a
decision above rests on a since-corrected fact, following the
diagnosis-correction precedent on record for this phase. Its scope
otherwise closes when the one prospective cycle reaches a §3
disposition other than PRE-CALL ABORT, or when the owner supersedes it
by a later ADR.

## Owner approval

The prospective-validation design was independently red-teamed and
owner-approved before this adoption. Approved by owner — 2026-08-20.

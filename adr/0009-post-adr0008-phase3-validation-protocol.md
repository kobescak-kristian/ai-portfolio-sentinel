# 0009 — Post-ADR-0008 Phase-3 validation protocol

Status: ADOPTED

Date: 2026-08-21

## Context

This ADR exists for one reason, and it is deliberately narrow. It is
not a restatement of `adr/0005`, `adr/0006`, `adr/0007` or `adr/0008`,
and it re-decides nothing any of them settled.

1. The one prospective cycle authorized by
   `adr/0007-prospective-validation-protocol.md` ran 2026-08-20 and
   reached **VALID COMPLETED FAIL** after independent Step-F
   verification. That cycle is consumed and terminal for that lineage.
2. `adr/0008-judgment-call-execution-reliability.md` is now
   IMPLEMENTED (2026-08-21) with the complete model-free **R1–R24**
   proof package that ADR itself required. The subsequent independent
   review found two narrow implementation/proof gaps and one
   documentation truth defect; the remediation added and passed
   **R25–R26**, and that remediation was independently reread **PASS**.
   R25–R26 are later review-remediation proofs — ADR-0008's own
   requirement was R1–R24 and is not retroactively restated.
3. ADR-0008 §11 is an explicit validation firewall: it authorizes no
   real-model validation cycle and does not pre-write or pre-relax the
   predicates of any future validation protocol.
4. ADR-0007 §2's execution-validity rule — every relevant `agent_call`
   COMPLETED, zero FAILED `agent_calls` — cannot evaluate the runtime
   ADR-0008 already adopted. The adopted bounded-recovery sequence is
   `FAILED (SDK budget ceiling) → COMPLETED`, which that rule rejects
   by construction.

ADR-0007 must not be reinterpreted to accommodate it: its §2
predicates are historical fact, and ADR-0008 §11 forbids silently
reinterpreting predicates such as `zero_agent_calls_failed` to make an
audited retry pass. The only honest route is a new prospective
decision, taken before any further model call. This is that decision.

This ADR authorizes governance only. It implements nothing, runs
nothing, and validates nothing. Phase 3 remains OPEN.

## Decision

Exactly **one** new prospective Phase-3 validation cycle is authorized,
for the post-ADR-0008 implementation, under the protocol below. It is:

- **not** another re-gate under `adr/0005`;
- **not** a rerun under `adr/0007`;
- **not** a relabeling of any historical result.

All historical outcomes remain unchanged: the designated gate FAIL
(2026-08-05), the one permitted re-gate OVERALL FAIL (2026-08-19) and
the prospective VALID COMPLETED FAIL (2026-08-20) all stand.
`max_regates` remains **1**.

The frozen quality contract is unchanged in every part: fixtures;
answer key; clean manifest; scorer; thresholds; model; prompts; reason
codes; ADR-0006 finding identity; deterministic checks.

ADR-0009 changes only the prospective **execution-validity** rule
required to evaluate ADR-0008, plus the cross-run coverage rule that
rule depends on, and it declares this lineage's own cost acceptance
ceilings.

## 1. Preserved history and frozen surfaces

- `adr/0007` remains closed and unchanged; its §2 predicates and its
  consumed disposition remain historical facts.
- `adr/0006` identity and `adr/0008` runtime contracts remain in force
  and are not modified here.
- No fixture, answer-key, clean-manifest, scorer, threshold, model,
  prompt, reason-code, identity, lifecycle, fingerprint, cap or
  retry-taxonomy change of any kind is authorized.

## 2. Execution-validity rule (prospective, this ADR only)

Group judgment attempts by the already-proven `(run_id, task_key)`
identity, with `agent_calls.id` as the deterministic attempt order.

For every logical judgment task that enters the model path, exactly ONE
of the following histories is valid.

**NORMAL**

    [ COMPLETED ]

**BOUNDED RECOVERY**

    [ FAILED whose persisted mechanized failure classification is
      SDK_BUDGET_CEILING,
      COMPLETED ]

Nothing else is valid.

**The authoritative recovery condition is the mechanized failure
classification, never the SDK subtype alone and never exception
prose.** ADR-0008's `classify_invocation` order is load-bearing: a
local containment failure wins outright, so a `TOOL_BREAKER` call can
persist while still carrying `sdk_subtype = error_max_budget_usd`.
Subtype alone would promote a contained call into a valid recovery.
It must not.

A valid recovered history therefore requires ALL of:

1. first row state = FAILED;
2. persisted mechanized failure classification = `SDK_BUDGET_CEILING`;
3. `sdk_subtype` exactly `error_max_budget_usd` (corroboration);
4. `sdk_is_error` == true (corroboration);
5. positive reservation (`reserved_eur_micros > 0`);
6. second row has the same `run_id`;
7. second row has the same `task_key`;
8. second row orders later by `agent_calls.id`;
9. second row state = COMPLETED;
10. no third invocation row exists for that logical task;
11. first-attempt findings remain audit-only;
12. only the completed logical outcome contributes live findings.

Histories that MUST fail validation include:

- `[ FAILED ]`
- `[ FAILED, FAILED ]`
- `[ FAILED (other subtype), COMPLETED ]`
- `[ FAILED (SDK_BUDGET_CEILING), FAILED ]`
- `[ COMPLETED, COMPLETED ]`
- **`[ FAILED classified TOOL_BREAKER while carrying
  sdk_subtype == error_max_budget_usd, COMPLETED ]`**
- three or more invocation rows
- any REJECTED row
- any EXHAUSTED row
- any RESERVED row

More generally: any non-`SDK_BUDGET_CEILING` mechanized failure class
followed by COMPLETED is invalid, even where the row happens to retain
the budget subtype.

Reconstructing the mechanized class by parsing exception prose is NOT
authorized, here or in implementation.

ADR-0007's historical `zero_agent_calls_failed` predicate is NOT
reinterpreted. This rule is prospective and binds this ADR's cycle
only.

### 2A. Stage-2 boundary for the mechanized class

This ADR binds the semantic requirement now and binds no schema.
**No schema column is added at Stage 1.**

The current implementation already persists a bounded host-generated
failure reason derived from the mechanized class, alongside the typed
SDK subtype and `is_error` metadata. Stage 2 must determine whether
those existing durable fields reconstruct the mechanized class
unambiguously.

If Stage 2 finds the class cannot be reconstructed mechanically
without ambiguous free-text parsing: **STOP for owner adjudication.**
Do not silently add schema, and do not weaken this rule.

Stage 2 must include a deterministic negative test for:
`TOOL_BREAKER` + `sdk_subtype = error_max_budget_usd` → NOT a valid
recovery.

## 3. Cross-run coverage (logical, not raw call counts)

ADR-0007 compared raw COMPLETED `agent_call` counts. Under bounded
recovery that comparison is wrong: a valid recovered history contains
two call rows but is ONE logical judgment task. ADR-0009 compares
logical judgment-task coverage instead. Required:

- run 1 has more than zero distinct model-path `task_key`s;
- run 2 has more than zero distinct model-path `task_key`s;
- the distinct `task_key` count of run 1 equals that of run 2;
- every logical model-path task ends with exactly one authoritative
  COMPLETED call.

## 4. Cost acceptance ceilings for this lineage

Declared here because ADR-0008 §8 requires any future validation
protocol to state its own total session ceiling rather than inherit
the historical one:

- per-run accounted-consumption **acceptance ceiling**: 750,000
  micro-EUR;
- two-run accounted-consumption **acceptance ceiling**: 1,500,000
  micro-EUR.

These are acceptance ceilings, not guaranteed physical pre-spend
limits. ADR-0008 §7 establishes that the pinned SDK enforces its
per-call budget after API-call activity, so a call already in flight
can overshoot before being halted, and detected post-call overshoot is
accounted rather than clamped. Therefore:

- account all known overshoot honestly;
- run accounted cost above 750,000 micro-EUR → FAIL;
- combined accounted cost above 1,500,000 micro-EUR → FAIL;
- never clamp cost to obtain a PASS;
- overshoot never authorizes another invocation or another validation
  cycle.

No cap value changes.

## 5. Consumption boundary and finality

Let `C` = the count of persisted `agent_calls` rows across the two
designated run IDs with `reserved_eur_micros > 0`.

- **`C == 0` → PRE-CALL ABORT.** Any artifact produced is diagnostic
  and non-authoritative. The cycle is NOT consumed.
- **`C > 0` + complete independently verified PASS → PASS.** Phase 3
  may close; progression to Phase 4 becomes permitted.
- **`C > 0` + a complete result failing any binding condition →
  VALID COMPLETED FAIL.** The cycle is consumed. Phase 3 remains OPEN;
  Phase 4 not permitted.
- **`C > 0` + no parseable complete result → CONSUMED-PARTIAL / NO
  RESULT.** The cycle is consumed. Phase 3 remains OPEN; Phase 4 not
  permitted. This is explicitly NOT evidence that Sentinel failed a
  completed gate.

There is no fifth disposition. **ADR-0009 authorizes no retry after a
consumed non-PASS.**

Recorded explicitly: if this cycle is consumed and does not
independently verify PASS, do NOT automatically create another
remediation or validation cycle. Any further path requires a new owner
decision, and the default posture is **PARK** rather than another
automatic fix-and-retest loop.

## 6. Strongest case against

This is a further validation opportunity after three historical failed
Phase-3 outcomes. Gate-shopping is a legitimate concern and is not
dismissed.

Why one final cycle is nevertheless accepted:

- no scoring change;
- no corpus change;
- no model change;
- no prompt change;
- no cap increase;
- no retry expansion;
- no identity change;
- ADR-0008 exists on its own unattended-runtime-reliability merits and
  was adopted, implemented and reviewed before this ADR;
- the new validity rule is defined prospectively, before another model
  call, and it is *stricter* than subtype matching would be;
- one consumed non-PASS ends this authorization.

If Stage 2 unexpectedly requires changing any frozen quality surface,
the model or prompts, finding identity, the retry taxonomy, a cap, the
task lifecycle, or broad schema: **STOP.** Do not expand ADR-0009 to
authorize it.

## 7. Bound sequence

This ADR authorizes this sequence and no other:

- **A.** Stage-1 adoption (this ADR, the BLUEPRINT amendment, the STATE
  record).
- **A2.** Push Stage 1 and require exact-SHA CI PASS.
- **B.** Separate model-free runner implementation plus tests.
- **C.** Push Stage 2; require exact-SHA CI, Phase-1 freeze and the
  repository publication gate PASS.
- **D.** Pin the exact Stage-2 SHA externally.
- **E.** Execute exactly one prospective cycle.
- **F.** Independent read-only verification and public recording.

The Stage-1 session performs **A and A2 only.**

## 8. Non-authorizations at Stage 1

This adoption commit contains this ADR, the BLUEPRINT amendment and
the STATE record. It does NOT authorize, at Stage 1: any
implementation; any runner, test, schema or runtime change; any model
call; any gate, re-gate, eval or scorer execution; any validation
artifact; any `max_regates` change; any cap change; or any Phase-4
work.

## Consequences and risks

One tightly bounded validation path now exists for the implemented
ADR-0008 runtime, with a prospective logical-history validity rule
that is strictly harder to satisfy than ADR-0007's on the recovery
path, logical-task cross-run coverage, explicit accounted-cost
acceptance ceilings, a no-discretion consumption boundary and
independent verification. Accepted in exchange: a consumed non-PASS is
terminal for this authorization with PARK as the default posture; the
remediation-informed-corpus and gate-shopping objections disclosed in
ADR-0007 §7 stand unresolved and are not claimed to be eliminated; and
the mechanized-class reconstruction question is deferred to Stage 2
with a STOP rather than pre-answered here. Nothing in this ADR claims
the implemented runtime produces a passing gate.

## Reopening and supersession

This ADR reopens before execution only if evidence surfaces that a
decision above rests on a since-corrected fact, following the
diagnosis-correction precedent on record for this phase. During Stage
2, the STOP conditions in §2A and §6 return the work for owner
adjudication rather than expanding scope. Its scope otherwise closes
when the one prospective cycle reaches a §5 disposition other than
PRE-CALL ABORT, or when the owner supersedes it by a later ADR.

## Owner approval

The protocol was owner-specified and owner-approved, with two dated
corrections applied before adoption: recovery authorization bound to
the persisted mechanized failure classification rather than the SDK
subtype alone, and preservation of the R1–R24 / R25–R26 evidence
lineage. Approved by owner — 2026-08-21.

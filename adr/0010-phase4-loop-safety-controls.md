# 0010 — Phase-4 bounded-loop safety controls

Status: ADOPTED

Date: 2026-08-22

## Context

Phase 3 is CLOSED — 2026-08-22, after the independently verified
ADR-0009 PASS. That closure is settled history here and is neither
reopened nor reinterpreted by this record.

Phase 4 is the BLUEPRINT §6 P4 long-horizon reliability layer. The
machinery built in Phases 2 and 3 already proves, with committed
evidence, run-level persistence, finding lifecycle and dedup,
run-scoped budget accounting, structured logging, crash recovery and
agent-call reliability. All of that operates inside the boundary of a
single Sentinel run.

Phase 4 introduces a genuinely new supervisory unit: **one bounded-loop
execution spanning multiple complete Sentinel runs**. Nothing already
built supervises that unit, and no existing ADR describes its safety
semantics.

The BLUEPRINT freezes the shape — N ≤ 10 iterations under caps, cost
and consecutive-failure breakers proven by SEEDED faults, failure
alerting proven through those same faults, a published ITERATION_LOG,
and a frozen gate. It does not freeze the exact breaker semantics or
the loop-wide ceiling. Left as is, an implementer would have to choose
policy while writing the gate that judges that policy. This
repository's ADR lineage exists precisely to prevent that ordering.

Owner decisions on those semantics were taken 2026-08-22, before any
Phase-4 implementation. This ADR freezes them prospectively.

This ADR authorizes governance only. It implements nothing, runs
nothing and validates nothing. No Phase-3 ADR or Phase-3 result is
reopened, reinterpreted, relabeled or softened by it.

## Decision

The Phase-4 bounded-loop control contract is frozen as §1 through §9
below, before implementation. Phase 4 moves from PERMITTED-but-not-
started to IN PROGRESS on this adoption. Phase 4 remains OPEN.

## 1. Failure unit and consecutive-failure streak

**Failure unit.** One iteration counts as failed if and only if its
underlying `RunOutcome.status` is not `COMPLETED`. An equivalent
terminal process outcome may be a nonzero exit, but the durable source
of truth is `runs.status` / the reconstructed `RunOutcome` status,
never the exit code alone.

**Not loop failures.** The following do NOT individually count as loop
iteration failures:

- a dead-lettered task by itself;
- an individual failed `agent_call`;
- an ADR-0008 bounded-recovery first attempt;
- an HTTP retry;
- a tool breaker event.

Those remain sub-run mechanisms and are governed by their own ADRs.

**Reset.** Only an iteration whose final run status is `COMPLETED`
resets the streak to zero. Nothing else resets it — not time, not a
new scheduler fire, not an operator restart, not a partially
successful run.

**Threshold.** The breaker trips at exactly **3** consecutive failed
iterations.

**Scope.** The streak belongs to ONE bounded-loop execution,
identified by `loop_id`. It is durable and reconstructable across a
crash and resume of that same loop. It does NOT persist across a
separately launched loop, across a scheduler invocation, or into
future unrelated operator sessions.

**Refusal target.** Once the threshold is reached, the loop refuses
the NEXT iteration. It does not abort a run already in progress, and
it creates no permanent or global lock.

## 2. Loop cost ceiling

`LOOP_BUDGET_EUR_MICROS = 750_000` for Phase 4.

This is a **real pre-start loop ceiling**, not merely an
after-the-fact acceptance metric of the kind ADR-0009 §4 declared for
its validation lineage.

It does not replace or raise the existing EUR 0.75 per-run cap, which
is unchanged.

No Phase-4 CLI flag, configuration value or environment variable may
allow an operator to raise this loop ceiling. Any future operation
above 750,000 micro-EUR requires a separate dated owner-governed
decision. **ADR-0010 pre-authorizes no such raise.**

**Accounting source.** Accounted consumption is reconstructed from
durable `CostRow`s belonging to the loop's own iteration `run_id`s. A
volatile cumulative in-memory counter is NOT the source of truth.

**Pre-start arithmetic.** Before another iteration may start:

    remaining_loop_budget =
        750_000 - durable_accounted_loop_consumption

If another iteration is permitted and `remaining_loop_budget > 0`:

    effective_iteration_allowance =
        min(existing_per_run_cap, remaining_loop_budget)

The reduced allowance must be propagated downward into the existing
run/model budget mechanism. If that reduced allowance cannot be
enforced, the loop **refuses the iteration fail-closed**. The normal
EUR 0.75 allowance is never silently restored.

Known overshoot is accounted in full and never clamped.

## 3. Termination precedence

The following state evaluation is frozen, in this exact order, AFTER a
finalized iteration.

**A. Accounted overshoot**

    if cumulative_accounted_cost > loop_ceiling:
        stop_reason = COST_BREAKER_TRIPPED
        nonzero exit

Known overshoot is recorded in full. Reaching N cannot hide it.

**B. Consecutive-failure threshold**

    else if consecutive_failures >= 3:
        stop_reason = CONSECUTIVE_FAILURE_BREAKER_TRIPPED
        nonzero exit

This outranks normal iteration-cap completion.

**C. Normal iteration-cap completion**

    else if iterations_completed >= N:
        stop_reason = COMPLETED_ITERATION_CAP
        exit 0

Valid only when `cumulative_accounted_cost <= loop_ceiling` and the
failure streak is below 3.

**D. Pre-start cost refusal** — evaluated only if another iteration
would otherwise start:

    remaining_loop_budget =
        loop_ceiling - cumulative_accounted_cost

    if remaining_loop_budget <= 0:
        stop_reason = COST_BREAKER_TRIPPED
        refuse next iteration
        nonzero exit

**E. Continue** — otherwise create the next durable iteration intent
(§4) and use:

    effective_allowance =
        min(existing_per_run_cap, remaining_loop_budget)

### 3A. Frozen boundary consequences

- N reached + cost 749999 + streak < 3
  => `COMPLETED_ITERATION_CAP`
- N reached + cost exactly 750000 + streak < 3
  => `COMPLETED_ITERATION_CAP`
- N reached + cost > 750000
  => `COST_BREAKER_TRIPPED`
- N not reached + cost exactly 750000
  => next iteration refused / `COST_BREAKER_TRIPPED`
- N not reached + cost > 750000
  => `COST_BREAKER_TRIPPED`
- N reached + cost <= 750000 + failure streak 3
  => `CONSECUTIVE_FAILURE_BREAKER_TRIPPED`

Stated explicitly, because it is the part most likely to be tidied
later by someone who did not read this section: **post-iteration
overshoot uses strict `>`; pre-start refusal uses remaining `<= 0`.**
Exactly 750,000 accounted is therefore an acceptable terminal state
but not a state from which another iteration may start. This asymmetry
is intentional and MUST NOT later be normalized into one comparison
operator.

## 4. Durable iteration intent

This section freezes a crash-safety invariant, not a schema.

Before an underlying Sentinel run for iteration *k* may begin, the
pair `(loop_id, iteration_index)` must already have exactly one
durably persisted `planned_run_id`. That `planned_run_id` is generated
once and is reused for that iteration. The underlying `execute_run`
invocation must receive that exact value as `RunConfig.run_id`.

On recovery of an unfinished loop iteration:

- **A.** No `runs` row exists for `planned_run_id`: the run may start
  once, using that SAME `planned_run_id`.
- **B.** A terminal `runs` row exists: adopt the existing run. NEVER
  invoke `execute_run` again for that iteration.
- **C.** The `runs` row is RUNNING: use the existing Sentinel
  interrupted-run recovery; do not create a replacement run. The
  recovered terminal run is the iteration result.
- **D.** A terminal run exists but its derived outputs are incomplete:
  reconcile the existing terminal outputs first; do not rerun the
  iteration; do not invent another cost source.

**Invariant:** a terminal underlying run must never be repeated merely
because loop bookkeeping crashed after run finalization.

The exact SQLite columns and table representation remain
implementation detail and are NOT frozen by this ADR.

## 5. Failure alert contract

No new notification channel is introduced by Phase 4.

A proven Phase-4 breaker or failure alert requires ALL FOUR of:

1. a structured ERROR-severity event from the closed logging event
   vocabulary;
2. a durable `stop_reason` on loop state;
3. a nonzero process exit;
4. a labeled `ITERATION_LOG.md` evidence line or section.

No email. No Slack. No webhook. No push notification. No dashboard.

Loop operational failures must NOT be appended into monitored-surface
findings in order to manufacture an alert. FINDINGS lifecycle
semantics stay separate from loop supervision.

## 6. Closed stop-reason vocabulary

    COMPLETED_ITERATION_CAP
    COST_BREAKER_TRIPPED
    CONSECUTIVE_FAILURE_BREAKER_TRIPPED
    LOOP_ABORTED_ERROR

`COMPLETED_ITERATION_CAP` is normal completion and exits 0. The other
three are abnormal / fail-closed and exit nonzero.

Exactly one terminal `stop_reason` is authoritative for a loop.

## 7. Frozen Phase-4 technical gate contract

Frozen here, BEFORE implementation.

**The technical gate is MODEL-FREE.** No Haiku. No Sonnet. No provider
contact. No real model spend. It uses seeded, model-free faults and
proves behavior mechanically.

**Leg 1 — normal.** N = 10; exactly 10 completed and finalized loop
iterations; no gaps and no duplicate iteration identities; each
underlying run terminal; tasks terminal; one `CostRow` per run;
continuity demonstrated across iterations; stop reason
`COMPLETED_ITERATION_CAP`; exit 0.

**Leg 2 — cost breaker.** At the fixed ceiling 750000, prove at
minimum:

- **a.** cumulative 749999 mid-loop: cost alone does not trip.
- **b.** cumulative exactly 750000 mid-loop: the next iteration is
  refused.
- **c.** cumulative above 750000: the full overshoot is accounted with
  no clamp, and the next iteration is refused.
- **d.** the refusal proves that NO next underlying run starts.
- **e.** N reached at exactly 750000: `COMPLETED_ITERATION_CAP`,
  exit 0.
- **f.** N reached above 750000: `COST_BREAKER_TRIPPED`, nonzero exit.
- **g.** a reduced remaining allowance below the normal per-run cap is
  propagated downward and is not restored to 750000.

Real provider spend across this leg is ZERO.

**Leg 3 — consecutive failure.**

- Trip case: exactly three consecutive failed iterations; streak
  exactly 3; the next iteration never starts;
  `CONSECUTIVE_FAILURE_BREAKER_TRIPPED`; all four alert parts of §5
  present; nonzero exit.
- Reset case: fail, fail, success, fail, fail must NOT trip from stale
  streak state.
- Terminal-boundary case: N reached while the streak reaches 3 must
  yield `CONSECUTIVE_FAILURE_BREAKER_TRIPPED`, not normal completion.

**Leg 4 — crash / recovery.** Primary injected seam: the underlying
Sentinel run is terminal and durable, but loop iteration finalization
is not yet committed. After restart, prove: same `loop_id`; same
iteration index; same `planned_run_id`; the existing terminal run is
adopted; `execute_run` is not invoked again for that iteration; no
duplicate run; no skipped iteration; the loop completes
deterministically. The no-run-yet case (§4A) and the
terminal-output-reconciliation case (§4D) must also be exercised
sufficiently to prove the §4 invariant.

**Self-check.** The public derived `ITERATION_LOG` figures must be
checked back against durable machine state.

## 8. The technical gate is not Phase-4 closure

Passing the technical loop gate does NOT by itself close Phase 4.

`adr/0003-production-readiness-program.md` maps four further closure
artifacts to P4:

- `TEST_MATRIX.md`
- `INCIDENT_RESPONSE.md`
- `MONITORING.md` (draft)
- `RUNBOOK.md` (draft)

They must be authored from the implemented capability and its
evidence, never as placeholders.

Phase 4 may be declared CLOSED only after BOTH:

- the technical Phase-4 loop gate PASSes; AND
- all four mapped P4 artifacts exist and pass their applicable
  artifact and publication controls.

If the technical gate passes but any mapped artifact is missing or
fails, **Phase 4 remains OPEN.**

## 9. Non-scope and preservation

This ADR explicitly preserves, and changes nothing about:

- Phase 3 CLOSED;
- the ADR-0009 cycle: PASS, complete and consumed;
- no Phase-3 gate reopening of any kind;
- no fixture, scorer, answer-key, threshold, model or prompt change;
- no ADR-0008 retry-taxonomy change;
- the existing EUR 0.75 per-run cap, unchanged;
- cross-run dedup, already proven and not re-gated here;
- the separately tracked Postgres / storage-backend work item, which
  is not part of Phase 4;
- no SQLAlchemy, Alembic or storage-backend migration;
- the GitHub Actions scheduler migration, which remains Phase 5;
- the official Sonnet gate, which remains Phase 5;
- `SentinelDailyRun`, which remains stub-mode;
- no production or production-ready claim of any kind;
- the site-owner collision, which remains later-program-blocking and
  is not a Phase-4 loop implementation blocker.

## Residuals recorded honestly

**Allowance rounding.** For a very small remaining loop budget,
conversion into the SDK's four-decimal USD allowance can round down to
0.0000. That is fail-closed and correct. Do NOT invent a positive
floor to defeat it.

**Ceiling equals one per-run cap.** The loop ceiling of 750,000
micro-EUR is exactly the existing per-run cap, so
`min(per_run_cap, remaining_loop_budget)` binds from the first
iteration onward and a real model-calling loop is effectively bounded
to roughly one full-cost iteration. This is deliberate fail-closed
conservatism, and it is consistent with the Phase-4 technical gate
being frozen model-free in §7. It is disclosed here rather than
engineered around: raising the ceiling requires a separate dated
owner-governed decision, and this ADR grants none.

**Nothing here claims the loop works.** These are prospective
controls, not evidence. No Phase-4 capability, gate result or
reliability property is claimed by this record.

## Consequences

The next implementation session may now build: the loop supervisor;
loop-state persistence; both breakers; crash recovery; model-free
fault-injection seams; public derived `ITERATION_LOG` support; and the
required coverage, read-only and logging boundary wiring.

**This adoption commit itself contains none of that code. No runner
package is created here.** No model call is made, no gate is run, no
scheduler is touched, and no Phase-5 work is authorized.

## Reopening and supersession

This ADR reopens before implementation only if evidence surfaces that
a decision above rests on a since-corrected fact, following the
diagnosis-correction precedent on record for this repository. Its
scope otherwise closes when Phase 4 closes under §8, or when the owner
supersedes it by a later ADR.

## Owner approval

The control contract above was owner-specified and owner-approved on
2026-08-22, before any Phase-4 implementation.

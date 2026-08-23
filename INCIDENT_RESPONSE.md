<!-- Mapped Phase-4 closure artifact (BLUEPRINT §6 P4, §11(d);
adr/0003-production-readiness-program.md P4; adr/0010 §8). Grounded in
the landed Phase-4 controls and the designated gate evidence, never in
anticipated capability. Status: in development toward production-ready.
No production, service or availability claim is made in this
document. -->

# INCIDENT_RESPONSE — ai-portfolio-sentinel

## 1. Scope and status

Operator incident response for the Phase-4 bounded loop and the runs it
supervises. One operator, n=1, monitoring the operator's own public
repositories read-only. There is no service, no other data subject, no
on-call rotation and no third party with standing here.

Status: **in development toward production-ready.** The
production-readiness program (`adr/0003`) is open, so nothing in this
document is a production, availability or uptime commitment.

What exists today: a bounded-loop supervisor with two circuit breakers,
durable loop state, crash-safe iteration identity, structured logging
and derived public evidence. What does not exist: any external alert
channel, any provider-capable loop path, and any GitHub Actions
scheduling. Sections below never assume otherwise.

## 2. Authoritative evidence order

When two sources disagree, the higher one wins. Always.

1. **Durable SQLite state** — `loop_runs`, `loop_iterations`, `runs`,
   `tasks`, `findings`.
2. **Durable `CostRow`s** — the append-only cost ledger.
3. **Structured JSONL logs** — the run/loop event stream.
4. **Derived outputs** — `FINDINGS.md`, `ITERATION_LOG.md`.

**Derived outputs never overrule durable state.** `ITERATION_LOG.md`
says so in its own header: every figure in it is rendered from durable
state and checked back against it, and where the two disagree the
ledger is right and the file is wrong. Diagnose from the ledger, then
use the derived files as a convenient index into it — never the other
way round.

Ledger rows are never deleted. Delete-abort triggers cover every table
including the loop tables, and there is no `DELETE` statement in the
control plane. That is a property to rely on during an incident, not an
obstacle to work around.

## 3. Stop reasons at a glance

| `stop_reason` | Exit | Meaning |
|---|---|---|
| `COMPLETED_ITERATION_CAP` | 0 | Normal completion at N |
| `COST_BREAKER_TRIPPED` | nonzero | Accounted cost overshot, or the next iteration was refused pre-start |
| `CONSECUTIVE_FAILURE_BREAKER_TRIPPED` | nonzero | Three consecutive failed iterations |
| `LOOP_ABORTED_ERROR` | nonzero | Unexpected supervisor error, failed closed |

Exactly one terminal `stop_reason` is authoritative per loop, enforced
by a guarded update, so "which one was it" is never ambiguous.

## 4. A — `COST_BREAKER_TRIPPED`

**Symptoms.** A durable `stop_reason` of `COST_BREAKER_TRIPPED` on the
loop row; a nonzero process exit; an ERROR-severity
`breaker.cost_tripped` event in the JSONL; no next iteration started
(no further INTENT row, no further `runs` row).

**Response.**

1. Read the durable `CostRow`s for this loop's own iteration run ids.
   They are the accounting source. Do not reason from a log line, a
   summary, or a remembered figure.
2. Compare accounted consumption against the fixed ceiling of
   **750,000 micro-EUR**. Note which case fired: accounted cost above
   the ceiling after an iteration (strict `>`), or remaining budget at
   or below zero before one could start (`<= 0`). Those two comparisons
   are deliberately asymmetric and must not be collapsed into one.
3. Account any known overshoot **in full**. Never clamp it. A row above
   the per-run cap is a truthful record of spend that already happened,
   not a bug in the ledger.
4. Identify the cost cause — which iteration, which run, which calls —
   before any further operator-authorised run.

**Never.**

- Never raise the 750,000 micro-EUR Phase-4 loop ceiling. No flag,
  configuration value or environment variable may do it, and none
  exists; any operation above it requires a separate dated
  owner-governed decision, which `adr/0010` does not pre-authorise.
- Never raise the **EUR 0.75** per-run cap to make an execution fit.
- Never clamp, round down or "tidy" recorded overshoot.
- Never restore a reduced iteration allowance to the full per-run cap.
  If the reduced allowance cannot be enforced downward, the correct
  behaviour is to refuse the iteration fail-closed.

**Lane-wide cost rule** (applies to all lane spend, pooled): a hard
ceiling of **EUR 50 per month**; if trailing-30-day spend exceeds
**EUR 40**, run frequency drops one notch (daily → every-2-days →
weekly). Frequency drops; caps and the ceiling never rise to fit. The
Phase-4 bounded-loop runner does not currently own monthly scheduling —
it is invoked deliberately, and the standing scheduled task is a
separate, stub-mode surface.

## 5. B — `CONSECUTIVE_FAILURE_BREAKER_TRIPPED`

**Symptoms.** A consecutive-failure streak of exactly 3; a durable
`stop_reason` of `CONSECUTIVE_FAILURE_BREAKER_TRIPPED`; a nonzero exit;
an ERROR-severity `breaker.consecutive_failure_tripped` event; the
fourth iteration refused before it starts; a labelled
`PHASE4_FAILURE_ALERT` section in the iteration evidence.

**Response.**

1. Read the three durable run outcomes behind the streak. An iteration
   failed if and only if its underlying run's final status is not
   `COMPLETED` — the exit code alone is never the source of truth.
2. Decide whether the three failures share one cause or are
   independent. A dead-lettered task, an individual failed model call,
   a bounded-recovery first attempt, an HTTP retry and a tool breaker
   event are sub-run mechanisms; they do not individually count as loop
   failures and they are not what tripped this breaker.
3. Fix the cause. The operator decides whether and when a new bounded
   loop begins; the breaker creates no permanent or global lock, and it
   never aborts a run already in progress.

**Never.**

- Never delete, reset or edit ledger rows to clear the streak. The
  streak is reconstructed from durable rows precisely so it survives a
  crash and cannot be cleared by restarting.
- Never treat a new scheduler fire, an operator restart or elapsed time
  as a reset. Only a `COMPLETED` iteration resets the streak.
- Never resume the same loop under different bounds to get past it —
  that is refused, and it would be raising a ceiling through the back
  door.

## 6. C — `LOOP_ABORTED_ERROR`

This is the fail-closed catch-all: the supervisor hit an unexpected
error and still wrote durable terminal state, so the loop has exactly
one authoritative stop reason rather than none.

**Response.**

1. Inspect durable loop and run state first — `loop_runs`,
   `loop_iterations`, then `runs` — before reading any log narrative.
2. Distinguish a **supervisor** failure (loop bookkeeping) from a
   terminal **underlying run** failure. They have different fixes, and
   the iteration rows tell them apart: an iteration still in `INTENT`
   with a terminal underlying run is a bookkeeping crash, not a run
   failure.
3. Preserve the evidence as it stands. The ERROR `loop.failed` event
   carries a redacted error type and message; that redaction is
   deliberate and is not to be undone by re-running with logging
   widened on a public surface.

**Never** manufacture a replacement run id to "clean up" the state. The
committed `planned_run_id` for an unfinished iteration is the identity
that recovery uses; minting a second one breaks the invariant the whole
crash-safety design rests on.

## 7. D — Interrupted run or process loss

Recovery is driven by what durably exists for the iteration's committed
`planned_run_id`. Four cases, and only four.

| Durable state | Correct recovery |
|---|---|
| INTENT committed, no `runs` row | Restart the same loop. The run starts **once**, using the same `planned_run_id`. The hard cost ceiling still applies before it starts. |
| `runs` row is `RUNNING` | The existing interrupted-run recovery path drives it terminal. No replacement run id is created; the recovered terminal run **is** the iteration's result. |
| `runs` row is terminal | Adopt it. The iteration is finalized from the existing run; the run is **not** executed again. |
| `runs` row is terminal, derived outputs incomplete | Reconcile the outputs (`FINDINGS.md` section, `CostRow`). Do **not** rerun, and do **not** create a second cost source. |

The invariant behind all four: a terminal underlying run must never be
repeated merely because loop bookkeeping crashed after run
finalization. A missing derived output is a reconciliation job, never a
reason to re-execute work that already cost something.

Earlier unfinished iteration indexes are always reconciled before any
later new iteration starts. If more than one iteration looks unfinished,
work the lowest index first — the supervisor does the same.

## 8. E — Public-evidence hygiene incident

If any output about to be committed contains a machine-local path, a
credential, a raw environment value, a traceback carrying unsafe data,
or any other private material:

1. **Stop before commit and before push.** Do not push and repair
   afterwards; this repository publishes on every push and there is no
   later flip gate to catch it.
2. **Do not hand-edit machine evidence into a passing state.** Editing
   `ITERATION_LOG.md` or a gate artifact so the hygiene check goes green
   is falsifying evidence, not fixing a defect.
3. Diagnose and fix the **generating code**. Public hygiene here is a
   property of a closed input schema, not of a cleanup pass: an unsafe
   value should be refused at the boundary, so a leak means a validator
   or a call site is wrong.
4. Preserve safe local diagnostic evidence while you work.
5. Regenerate machine evidence **only** under the appropriate governed
   execution, and only if a new execution is actually authorised. A gate
   is not rerun to produce nicer output.

Do not add an allowlist exemption to make a blocking hit disappear. If
public prose triggers the publication gate, rewrite the prose
descriptively.

## 9. F — Hash or line-ending mismatch

The official `iteration_log_sha256` recorded in
`artifacts/phase4_loop_gate.json` binds the exact committed **LF**
bytes of `ITERATION_LOG.md`.

On a Windows checkout where line-ending conversion changes working-copy
bytes, a working-tree hash mismatch **does not by itself prove the Git
blob is corrupt**. It may prove only that the working copy is not a
byte-for-byte image of the committed object.

**Response.** Compare against the committed blob — the exact canonical
bytes — before concluding anything. Only a mismatch against the
committed object is evidence of a real evidence-integrity problem, and
that is an incident.

**Never edit `ITERATION_LOG.md` to make a local working-tree hash
match.** That converts a representation question into a falsified
record. This section documents evidence semantics only; it is not a
repository configuration change and none was made for it.

## 10. Escalation criteria

Escalate to an owner decision — rather than acting inside a session —
when any of the following is true:

- Continuing would require raising a cap or a ceiling, in any wording.
- Durable state and durable `CostRow`s disagree with each other.
- A committed machine-written artifact does not match the committed
  `ITERATION_LOG.md` bytes.
- A finding, run or loop row appears to have been mutated outside its
  permitted lifecycle transition.
- Recovery cannot proceed without inventing an identity (a run id, a
  loop id, an iteration index) that durable state does not already
  carry.
- Trailing-30-day lane spend approaches the EUR 40 frequency-drop
  trigger or the EUR 50 hard ceiling.
- A private value has already been pushed to the public remote.

## 11. Evidence-preservation rules

- Preserve durable state and durable `CostRow`s before any repair
  attempt. They are the record.
- Record hashes of the evidence you inspected, before and after, when a
  session touches anything near it.
- Never rewrite history to make a past result look better. Historical
  results stand unrelabelled, including failures.
- Never edit a machine-written artifact by hand — not to reformat, not
  to reorder, not to prettify.
- Never change a fixture, answer key, threshold, prompt, model or
  predicate after seeing a result.
- Never re-run a gate to obtain a different outcome.
- Record what was *not* determinable from retained evidence, explicitly,
  rather than filling the gap with a plausible reconstruction.

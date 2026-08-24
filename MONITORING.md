<!-- DRAFT — Phase-4 baseline. Mapped Phase-4 closure artifact
(BLUEPRINT §6 P4, §11(d); adr/0003-production-readiness-program.md P4;
adr/0010 §8). The FINAL MONITORING.md is mapped to Phase 5 by adr/0003
and does not exist yet. Grounded in landed capability and designated
gate evidence. Status: in development toward production-ready. No
production, service, availability or uptime claim is made in this
document. -->

# MONITORING (DRAFT — Phase 4) — ai-portfolio-sentinel

> **This is a Phase-4 draft, not the final monitoring document.**
> `adr/0003` maps the final `MONITORING.md` to Phase 5, alongside the
> GitHub Actions scheduler migration and the operating history that
> would give it real measured content. This draft records only what the
> operator can observe today and which thresholds are already
> enforceable.

## 1. Scope and status

System: n=1, operator-owned, read-only monitoring of the operator's own
public repositories. There is no service, no other consumer, and no
availability or uptime objective anywhere in this document — publishing
one would exceed the claims ladder and is out of scope by design.

Status: **in development toward production-ready.** The
production-readiness program is open.

## 2. Authoritative sources

In this order, highest first. A lower source never overrules a higher
one.

| Source | Kind | What it is |
|---|---|---|
| `runs`, `tasks` | Durable SQLite | Run-level truth: status, task counts, terminal state |
| `loop_runs`, `loop_iterations` | Durable SQLite | Loop-level truth: bounds, iteration identity, streak, stop reason |
| `findings` | Durable SQLite | Finding lifecycle, OPEN/RESOLVED, fingerprints |
| `CostRow`s | Durable append-only ledger | The accounting source for consumed cost |
| Structured JSONL logs | Durable, local | Event stream, closed event vocabulary, redacted free text |
| `FINDINGS.md` | Derived | Public proposal/report output, append-only |
| `ITERATION_LOG.md` | Derived | Public loop/gate evidence, append-only |

Both derived files are explicitly **not** authoritative loop state.
`ITERATION_LOG.md` carries that statement in its own header.

## 3. Monitored signals

### 3.1 Run health

| Signal | Source |
|---|---|
| Run status (`RUNNING` / `COMPLETED` / `FAILED`) | `runs.status` |
| `tasks_created` | `runs.tasks_created` |
| `tasks_terminal` | `runs.tasks_terminal` |
| Dead-lettered / failed tasks | `tasks.status` in `FAILED`, `DEAD_LETTER` |
| Run exit result | process exit code, corroborated by `runs.status` |

A run that is `COMPLETED` has `tasks_terminal == tasks_created` — the
schema enforces it, so a divergence is a data-integrity incident rather
than a monitoring signal.

### 3.2 Loop health

| Signal | Source |
|---|---|
| Loop status (`RUNNING` / `FINISHED`) | `loop_runs.status` |
| `stop_reason` (exactly one, terminal) | `loop_runs.stop_reason` |
| `iterations_started` | `loop_runs.iterations_started` |
| `iterations_completed` | `loop_runs.iterations_completed` |
| Iteration indexes, contiguity | `loop_iterations.iteration_index` |
| `consecutive_failures` | `loop_runs.consecutive_failures`, per-step in `loop_iterations.consecutive_failures_after` |
| Planned and bound run identity | `loop_iterations.planned_run_id`, `bound_run_id` |
| Iteration state (`INTENT` / `FINALIZED`) | `loop_iterations.iteration_state` |

An iteration left in `INTENT` after a process ends is the recovery
signal, not a failure count: it says work was intended and its identity
is already committed.

### 3.3 Cost

| Signal | Source |
|---|---|
| Per-run accounted cost | `CostRow.cost_eur_micros` for that run id |
| Loop cumulative accounted cost | Sum of `CostRow`s for the loop's own iteration run ids |
| Remaining loop allowance | `750000 - cumulative accounted` |
| Reduced next-iteration allowance | `min(per-run cap, remaining)`, recorded per iteration in the derived evidence |
| Monthly pooled spend | Cost ledger, trailing-30-day window |

Known overshoot is retained in full and never clamped, so cumulative
accounted cost may legitimately exceed the ceiling. That is a truthful
record of spend that already happened, not permission for more.

### 3.4 Finding continuity

| Signal | Source |
|---|---|
| `findings_new` | `runs.findings_new` |
| `findings_still_open` | `runs.findings_still_open` |
| `findings_resolved` | `runs.findings_resolved` |

Across iterations of one loop over the same surfaces, a healthy pattern
is new findings on the first iteration and continuity afterwards. A
sudden non-zero `findings_new` on a later iteration over unchanged
surfaces is worth reading as a possible identity-instability signal,
not as a discovery.

### 3.5 Evidence completeness

| Signal | How to check |
|---|---|
| One `CostRow` per expected run | Cost ledger versus the loop's bound run ids |
| Complete terminal output per run | `FINDINGS.md` section complete for the run id |
| Derived evidence matches durable state | The loop gate's durable-state self-check, where a gate execution applies |

The reconciliation path repairs a missing derived output for an
already-terminal run at the start of the next invocation, without
re-executing the run.

## 4. Thresholds and controls that are already real

| Control | Value | Where enforcement lives |
|---|---|---|
| Phase-4 loop ceiling | 750,000 micro-EUR | **Enforced in code** — pre-start refusal and post-iteration overshoot, no flag can raise it |
| Consecutive-failure threshold | 3 | **Enforced in code** — the next iteration is refused |
| Iteration bound | `1 <= N <= 10` | **Enforced in code** before any durable intent, and again as a database CHECK |
| Per-run cap | EUR 0.75 (750,000 micro-EUR) | **Enforced in code** — unchanged by Phase 4, never raised by the loop |
| Reduced iteration allowance | `min(per-run cap, remaining)` | **Enforced in code** — refuses fail-closed if it cannot be propagated downward |
| Lane monthly hard ceiling | EUR 50 | **Operator-observed** — a governance rule over pooled spend, not a runtime breaker |
| Frequency-drop trigger | trailing-30-day spend above EUR 40 | **Operator-observed** — the operator drops cadence one notch; caps never rise |

The split matters and is stated deliberately: the first five refuse
work by themselves; the last two are rules the operator applies after
reading telemetry. Nothing in the Phase-4 runner currently enforces a
monthly budget or a cadence.

## 5. Alert contract as it exists today

A Phase-4 breaker or failure alert is exactly these four things, all of
them:

1. a structured **ERROR**-severity event from the closed logging event
   vocabulary (`breaker.cost_tripped`,
   `breaker.consecutive_failure_tripped`, `loop.failed`);
2. a **durable `stop_reason`** on loop state;
3. a **nonzero process exit**;
4. a **labelled `PHASE4_FAILURE_ALERT` section** in `ITERATION_LOG.md`.

All four were proven together, model-free, by the seeded fault
injection and again by the designated gate predicate
`LEG3_FOUR_PART_ALERT`.

**There is no email, Slack, webhook, push-notification or dashboard
integration.** None is planned inside Phase 4. Loop operational
failures are never written into monitored-surface findings in order to
manufacture an alert — finding lifecycle semantics stay separate from
loop supervision.

Practical consequence, stated plainly: today an unattended failure is
discovered when the operator reads the exit code, the log or the
evidence file. That is the honest state of the alerting story at n=1,
and it is why Phase 5 exists.

## 6. What Phase 5 still has to do

This section names future requirements; it does not claim them.

- The GitHub Actions scheduling topology is implemented and
  model-free-tested (P5-B); the local task scheduler has not yet been
  cut over. One capped, one-shot WIF capability probe (P5-C) has
  authenticated and fired once, non-qualifying by design; the
  scheduled lane itself has never authenticated or fired.
- Establish an Actions-scheduled operating history — five consecutive
  scheduled live runs within caps, zero lost runs.
- Finalize this monitoring document from that measured history.
- Author `SLO.md` under its owner-fixed framing: internal operator
  objectives for an n=1 system, with no service, availability guarantee
  or uptime commitment offered to another party. Permitted objective
  classes are scheduled-run success rate, maximum consecutive failed or
  missed runs, finding-detection latency, cost per run and monthly cost
  ceiling, and telemetry completeness — never service availability or
  uptime percentages, and objectives become claims only when backed by
  measured operating history.
- Finalize the operational runbook from real operation.

## 7. What this document does not claim

- No availability, uptime or reliability objective is published here.
- No third-party monitoring or service claim of any kind.
- No autonomy claim: the bounded loop is operator-invoked, and the
  bounded-loop entry point refuses provider/agent mode fail-closed.
- No claim of measured operating history that does not exist. The
  Phase-4 evidence behind this document is model-free and seeded.
- No production or production-ready claim; the status language is
  unchanged.

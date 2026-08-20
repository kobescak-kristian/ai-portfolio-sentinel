# 0008 — Judgment-call execution reliability and cost containment

Status: ADOPTED

Date: 2026-08-20

## Context

The one prospective Phase-3 validation cycle authorized by
`adr/0007-prospective-validation-protocol.md` ran 2026-08-20 and
recorded, after independent Step-F verification, a **VALID COMPLETED
FAIL** — terminal for the current Sentinel-v1 Phase-3 validation
lineage. That cycle is consumed. Phase 3 remains OPEN; Phase 4 is not
permitted; no further real-model Phase-3 validation is currently
authorized. Nothing in this ADR relabels any historical result: the
original designated gate FAIL (2026-08-05), the one permitted re-gate
OVERALL FAIL (2026-08-19) and the prospective VALID COMPLETED FAIL
(2026-08-20) all stand unchanged.

The scoring evidence of the prospective cycle is stated narrowly and
must stay narrow: all frozen scoring thresholds passed on the
completed evidence, and Step F found no additional independently
observed model-quality miss; the two misses were execution-derived.
Run 1 was incomplete, so this is never summarized as "model quality
passed."

This ADR is the owner-governed decision that follows. It answers a
runtime-reliability and cost-containment question. It is not a gate
amendment, it changes no evaluation surface, and it authorizes no
validation run. It was designed in a read-only session
(q77-p3-adr8-design-a), red-teamed, and owner-adjudicated before this
adoption. This adoption session implements nothing.

## Evidence and evidence boundary

Directly evidenced, from the persisted prospective evidence
(hash-verified before use), the committed source, and the pinned
`claude-agent-sdk==0.2.110` source inspected during design:

1. **The immediate execution failure mechanism is evidenced.** One
   Run-1 judgment-model invocation (`agent_calls` id 1, scope
   `synthetic-01/EVAL_RESULTS.md`, class `missing-synthetic-label`)
   reached the SDK's per-call max-budget boundary. Persisted error,
   verbatim: `Exception: Claude Code returned an error result:
   Reached maximum budget ($0.1226)`. The row shows
   `reserved_eur_micros = 150000`, `charged_eur_micros = 150000`,
   `tool_attempts = 2`. The harness handled it fail-closed as
   designed: FAILED call → Inconclusive → DEAD_LETTER task → run 1
   FAILED.
2. **The retained evidence does NOT establish** why that invocation
   became unusually expensive, its complete internal progress, the
   final token/cost data that existed inside the SDK stream, or
   whether each of its two tool proposals was accepted or rejected.
   The same scope subsequently completed successfully and
   substantially more cheaply (26,583 micro-EUR in run 2). The root
   cause is not proven transient, stochastic, or random. The
   permitted characterization is: this is the only currently observed
   execution-failure class for which the incident was followed by
   successful same-scope re-execution.
3. **SDK semantics, verified from the pinned SDK source and its
   bundled CLI, not from comments.** `max_budget_usd` is enforced by
   the CLI *after* API-call activity: when accumulated estimated cost
   reaches the budget, the CLI emits a terminal ResultMessage with
   subtype `error_max_budget_usd`, `is_error: true`, and — critically
   — the actual accumulated `total_cost_usd` and token `usage`; it
   then exits non-zero, which the SDK converts into an **untyped**
   `Exception` whose text merely quotes the error result. The typed,
   mechanizable signal is the terminal ResultMessage's subtype; the
   exception prose is not a reliable classifier. The current harness
   loses that terminal ResultMessage entirely (the stream raises
   before `run_query` returns), which is exactly why item 2's gaps
   exist. No supported SDK mechanism imposes a hard pre-spend
   per-call ceiling; a call already in flight can overshoot its
   allowance before the SDK halts it.
4. **A pre-registered trigger has fired.** ADR-0005 §6 deferred
   per-attempt tool-emission persistence and pre-registered an early
   revisit "if another failed real call cannot be adequately
   diagnosed from retained evidence." Item 2 is that case. The
   observability decided here is evidence-triggered, not
   anticipatory.
5. **Latent accounting issue, found by design review.** On a
   successful call the harness converts the recovered SDK estimate
   and then silently bounds it with
   `min(converted_estimate, reservation)` before committing to the
   run-budget coordinator. A hypothetical successful call whose
   available SDK estimate exceeded its reservation would be
   under-accounted, and the coordinator's remaining-budget view would
   overstate available capacity. **Historical occurrence is not
   evidenced**: a read-only scan of every `agent_calls` row across
   all three retained Phase-3 evidence databases found the maximum
   converted-estimate/reservation ratio on completed calls to be
   0.6558. This is a latent accounting/enforcement vulnerability, not
   a claim that historical Phase-3 costs were wrong.
6. **The ADR-0006 identity correction behaved as designed** in the
   prospective cycle (60 persisted finding rows, 60 distinct
   fingerprints, zero fragmentation). ADR-0006 remains in force and
   is not modified here.

Frozen and staying frozen: `RUN_BUDGET_EUR_MICROS = 750000`,
`MAX_PER_CALL_RESERVE_EUR_MICROS = 150000`,
`SDK_ALLOWANCE_SAFETY_MARGIN = 0.70`, `MAX_TURNS = 10`,
`MAX_TOOL_CALLS_PER_CHECK = 5`. No increase is authorized. ADR-0005's
prohibition of "FAIL → raise again → rerun" remains binding.

## Decision question

What is the smallest runtime contract that makes one pathological
judgment-model call a contained, diagnosable event rather than either
(a) a whole-run failure with insufficient evidence, or
(b) an excuse to spend/retry until success?

## Considered options

**1. No change (keep fail-closed dead-letter, keep current
evidence).** Steel man: simplest semantics; nothing new to defend; no
recovery path can ever be abused. Rejected because it leaves the
system unable to diagnose its only observed real-call failure class
(the ADR-0005 §6 trigger fired precisely because of this), leaves the
known accounting clamp in place, and makes an unattended run's
outcome hostage to a single per-call cost-boundary event.

**2. Bounded re-execution + failed-call observability + honest
accounting (adopted).** The three pieces are one contract: the
observability makes the failure class mechanically classifiable; the
classification gates exactly one bounded same-run re-execution for
exactly one class; the accounting stops silently clamping known
overshoot. Details below.

**3. Salvage host-validated findings from a failed invocation
without re-execution.** Rejected: a call that did not complete its
scan-then-emit contract may have partially enumerated; promoting its
partial output to live findings converts a failed call into a silent
partial success — the "quiet failure indistinguishable from quiet
success" class this system exists to prevent. Findings emitted during
a failed invocation remain discarded as live findings; they are
retained only as bounded audit evidence.

**4. Generic retry framework (multi-class, exponential, transport
retries, elevated recovery reservations).** Rejected: no evidence
supports any retryable class beyond the one observed; a generic
framework is exactly the "spend until success" shape the decision
question forbids; elevated reservations would dismantle the per-call
ceiling's purpose.

**5. Cap increases.** Prohibited by the preserved ADR-0005 rulings
and not proposed. The observed failure is not evidence that any cap
is mis-sized; it is evidence that a failure at the existing cap is
currently under-observed and over-punished at run scope.

## Strongest case for

The adopted contract makes the one observed execution-failure class
diagnosable (typed subtype, tokens, cost, per-proposal outcomes
retained), bounds recovery to a single additional invocation inside
the untouched run budget, removes a known silent under-accounting
path, and changes no evaluation, identity, or scoring surface. Every
piece maps to a fired trigger or a confirmed code-level defect.

## Strongest case against

Phase 3 has already consumed the designated initial gate, the one
permitted re-gate, and the separately authorized prospective
validation cycle. ADR-0008 follows the latest FAIL. The strongest
objection is therefore: "runtime semantics are being repeatedly
changed after evaluation failure until the project eventually obtains
a passing result."

Why this ADR still survives that objection:

- ADR-0005 pre-registered the failed-call observability trigger, and
  that trigger actually fired;
- the observed failed class later had successful same-scope
  re-execution;
- the second-invocation count is fixed here, before any future
  validation exists;
- caps do not rise;
- failure charging remains conservative (a failed invocation never
  becomes cheaper than before);
- no new validation is authorized;
- no current gate predicate is modified;
- the behavior has independent unattended-monitor reliability value
  (see the gate-shopping test).

Evidence that would change the recommendation toward PARK or
termination of this remediation direction: implementation turning out
to require model, prompt, fixture, scorer or threshold changes; the
re-execution not remaining inside the existing run budget state; more
than one second invocation being required; the retry taxonomy needing
expansion without observed evidence; logical attempt identity
requiring broad lifecycle redesign; model-free failure injection
being unable to demonstrate a hard terminal stop; or the remediation
expanding materially beyond the evidenced failure surfaces. If any of
those occurs: STOP — no further remediation layer is created
automatically.

## RED TEAM

- *"The retryable class is exactly the class that failed the last
  cycle — this is tailoring."* The class is defined by the SDK's own
  typed terminal subtype, not by what any gate needs; the same
  contract would be wanted with no gate left (below); and this ADR
  authorizes no validation cycle in which the tailoring could pay
  off.
- *"One re-execution can double the waste on a pathological scope."*
  Accepted and bounded: worst case is one additional ordinary
  reservation, both attempts inside the unchanged 750,000 micro-EUR
  breaker; the second invocation gets no larger reservation, so a
  document-driven expensive behavior fails again and dead-letters.
- *"Complexity creep into the control plane."* The re-execution loop
  is confined to the judgment-call harness seam. The task state
  machine, pipeline, finding lifecycle, identity rule and prompts are
  untouched; a task still sees a single judge() boundary that either
  returns findings or raises.
- *"What if the typed terminal result is not captured?"*
  Classification falls back to
  insufficient-evidence-for-retry → fail-closed non-retryable →
  exactly today's behavior. Degradation is fail-closed.

## Gate-shopping test

"Would we still want this behavior in an unattended production-style
monitor if there were no Phase-3 gate left to pass?" **YES.**
Failed-call observability is needed for incident diagnosis; one
bounded same-run re-execution prevents one evidenced execution
boundary event from automatically destroying an otherwise healthy
unattended run; no cap increases; no repeated success-until-green
loop; failure accounting remains conservative; successful overshoot
accounting becomes more honest; every other execution failure remains
fail-closed. Explicitly: **relaxing a gate predicate to tolerate
retries is NOT part of ADR-0008.**

## Decision

Adopt the following runtime contract:

1. retain enough bounded failed-call evidence to diagnose caught
   in-process judgment-call failures;
2. mechanically classify execution failures;
3. allow exactly ONE same-run re-execution for exactly ONE currently
   authorized failure class: captured terminal SDK subtype
   `error_max_budget_usd`;
4. keep that second invocation inside the existing run-scoped budget
   state;
5. account known cost overshoot honestly instead of silently clamping
   it;
6. preserve fail-closed behavior for every other failure class;
7. authorize NO real-model validation cycle.

## 1. Failure taxonomy

Mechanized classification, bound at adoption. Classification is by
failure semantics; nothing in it consults whether a retry would help
any evaluation pass.

| Class | Disposition |
|---|---|
| AUTH_OVERRIDE | NON-RETRYABLE |
| RUN_BUDGET_EXHAUSTED | NON-RETRYABLE |
| SDK_BUDGET_CEILING (captured terminal subtype == `error_max_budget_usd`) | RETRYABLE exactly once |
| SDK_RESULT_ERROR_OTHER | NON-RETRYABLE |
| TRANSPORT_PROCESS_SDK_EXCEPTION_WITHOUT_CAPTURED_TYPED_RESULT | insufficient evidence for retry; fail-closed NON-RETRYABLE |
| NO_RESULT_MESSAGE | insufficient evidence; fail-closed NON-RETRYABLE |
| TOOL_BREAKER | NON-RETRYABLE |
| HOST_EVIDENCE_REJECTION | not itself an execution failure; a measured judgment-quality outcome |
| MISSING_FINAL_COST | does not itself authorize retry; conservative unresolved-cost accounting applies |
| REPORTED_COST_OVERSHOOT | does not itself authorize retry; execution success/failure follows the underlying SDK execution result; all known overshoot is accounted honestly |

Any future expansion of the RETRYABLE set requires a NEW
owner-governed decision backed by evidence. No implementation-only
expansion is permitted.

## 2. Bounded re-execution contract

The RETRYABLE set at adoption has cardinality exactly one:
SDK_BUDGET_CEILING. The mechanized trigger is a captured terminal
ResultMessage subtype exactly equal to `error_max_budget_usd`.
String matching against exception prose MUST NOT authorize retry.

Maximum actual SDK/model invocations per logical judgment task:
**2 total** — the initial invocation and at most one second
invocation. Implementation must expose this as an explicit bounded
constant or equivalent invariant (a suitable semantic name is
`MAX_MODEL_ATTEMPTS_PER_TASK = 2`; exact source-level naming is not
governance-critical). A pre-call EXHAUSTED ledger record where no SDK
call occurred does not count as an invocation.

The second invocation:

- occurs in the SAME run;
- consumes the SAME run-scoped budget state/pool;
- receives an ordinary reservation from remaining run capacity;
- gets no separate recovery budget, no larger reservation, no
  safety-margin relaxation, no increased turn allowance, and no
  increased tool allowance.

If remaining run capacity cannot fund another reservation: ordinary
pre-call exhaustion evidence is recorded, NO second SDK invocation
occurs, the logical judgment fails closed, the task dead-letters, and
the run fails under existing semantics.

If the second invocation fails for ANY reason: no third invocation;
the task dead-letters; the run fails.

If the second invocation succeeds: the logical task may complete
normally, and the first failed attempt remains permanently auditable.

Findings emitted during a failed invocation remain DISCARDED as live
findings. ADR-0008 does not create partial-success semantics.

## 3. Attempt grouping / ordering invariant

Binding semantic invariant: **every model-call attempt belonging to
one logical judgment task must be unambiguously groupable and
deterministically ordered.**

No database representation is bound here. The current implementation
derives `task_key` from surface and check class and stores `run_id`
separately; if implementation proves that `(run_id, task_key)`
identifies exactly one logical judgment task, it may reuse that
identity and derive deterministic attempt ordering from it. If that
cannot be proven, implementation STOPS and returns for owner
adjudication before inventing an attempt ordinal, a retry-of
reference, a logical call id, another persistent identity field, or
another task identity mechanism. This adoption does not pre-authorize
those schema choices.

## 4. Failed-call observability contract

Objective, bound at adoption: persist the MINIMUM bounded evidence
required to reconstruct what occurred during a caught in-process
judgment-call failure.

Implementation must make it possible to determine, where available:

- which agent call a tool proposal belonged to;
- tool-proposal order;
- the proposed closed reason_code;
- the proposed evidence coordinates;
- the tool outcome: ACCEPTED, REJECTED, DUPLICATE, or
  BREAKER_REFUSED;
- a bounded rejection reason where applicable;
- whether host-valid evidence had been accepted before a later SDK
  execution failure;
- the captured SDK terminal subtype, is_error status, turns, token
  counts, and SDK cost estimate.

No specific new table/column schema is pre-bound beyond what those
requirements actually require. Bounded excerpt retention is not
pre-banned: if implementation proves that bounded/redacted proposed
excerpt text is necessary to distinguish "substantively correct
proposal rejected by host contract" from "no usable proposal", such
retention is permissible ONLY when it is the minimum necessary,
length-bounded, local-ledger only, subject to the existing redaction
discipline, never chain-of-thought, never full model prose or a
transcript, never a raw prompt, never an arbitrary unbounded payload,
and never emitted into public FINDINGS output. Reason codes and
coordinates are preferred over copied text wherever they provide
equivalent diagnostic value.

## 5. Observability durability boundary

Explicit non-goal: ADR-0008 does NOT require every tool proposal to
be synchronously written to SQLite while the SDK invocation is still
running. The observed incident was a caught in-process SDK failure
where currently available in-memory evidence was discarded before
durable finalization. Implementation MAY therefore collect bounded
attempt evidence in memory during one invocation and persist that
buffered evidence on every caught terminal path, before or atomically
with call finalization.

Host-process crash during an in-flight invocation remains governed by
the existing RESERVED-row / reconciliation behavior. ADR-0008 does
NOT claim crash-proof per-tool persistence or zero-loss tool
telemetry under process death. If future evidence demonstrates that
synchronous per-tool persistence is necessary, that requires a
separate evidence-based decision.

## 6. Cost-accounting contract

The contract distinguishes: reservation; SDK-reported cost estimate;
charged/accounted budget consumption; unresolved conservative charge;
detected overshoot; remaining run capacity. The SDK-reported cost
remains an estimate / model-equivalent consumption signal, never
authoritative provider billing.

**Case A — COMPLETED call, final SDK estimate recoverable.** Convert
the SDK-reported USD estimate using the existing conservative upward
EUR-micro rounding, and charge/account the FULL converted estimate.
If `converted_estimate <= reservation`, normal commit semantics may
release the unused reservation. If
`converted_estimate > reservation`, an overshoot-aware accounting
path records and accounts the FULL known estimate. Never silently
use `min(converted_estimate, reservation)` to hide known overshoot.

**Case B — FAILED call, final SDK estimate recoverable.**
Charge/account `max(reservation, converted_sdk_estimate)`. Rationale:
ADR-0005 deliberately made a failed real invocation burn its
reservation conservatively; adding re-execution must NOT make a
failed invocation economically cheaper; the SDK figure is an estimate
rather than authoritative billing; and any known estimate ABOVE the
reservation must never be understated.

**Case C — FAILED call, final cost not recoverable.** Charge the full
reservation, preserving the existing conservative unresolved-cost
semantics.

**Case D — COMPLETED call, final cost not recoverable.** Preserve
today's conservative full-reservation treatment unless later
implementation analysis proves a direct contradiction and returns for
owner adjudication. It is not silently redesigned.

## 7. Run-budget honesty

The pinned SDK enforces its call budget after API-call activity; a
call already in flight can overshoot before the SDK halts it. Any
conceptual claim that the current SDK can guarantee "actual
provider/model-equivalent consumption can never exceed EUR 0.75" is
corrected by this ADR. The correct operational invariant is:

- the coordinator governs whether another model invocation may START;
- reservations and already-accounted consumption constrain subsequent
  starts;
- all recoverable post-call estimates are accounted conservatively;
- when accounted run consumption reaches or exceeds nominal run
  capacity, no additional reservation may be issued.

A known post-call overshoot may therefore result in accounted
consumption above the nominal EUR 0.75 run budget after that call
completes or fails. That is DETECTED OVERSHOOT / BREACH — not
permission for further spend, and not an accounting failure.

Later implementation must correct any code comment or docstring that
currently claims aggregate charged consumption can never exceed
`RUN_BUDGET_EUR_MICROS`. That documentation correction is
pre-authorized for the future implementation dispatch; it is not made
in this adoption-only change.

## 8. Hard bounds — pre-spend vs post-spend

Mechanically bounded BEFORE another model invocation starts:

- maximum actual model invocations per logical judgment task = 2;
- `MAX_TOOL_CALLS_PER_CHECK` = 5;
- `MAX_TURNS` = 10;
- per-call reservation ≤ 150,000 micro-EUR;
- no second run-budget pool;
- a new reservation is refused when accounted/reserved run capacity
  has no remaining room.

Only known / detectable AFTER SDK execution reports usage:

- the SDK-estimated actual call cost;
- overshoot beyond the reservation;
- the SDK-estimated aggregate run consumption.

The monthly lane ceiling remains EUR 50/month and the trailing-30-day
frequency-drop rule remains unchanged. ADR-0008 does NOT establish a
new generic "session budget." The historical EUR 1.50 two-run session
bound belonged to the earlier gate/re-gate design and is NOT
automatically carried into any future validation lineage; any future
validation protocol must explicitly define its own total session
ceiling.

## 9. Required model-free implementation proof

All of the following must exist and pass, model-free and
network-free, BEFORE any future real-model validation could even be
considered:

- **R1.** SDK_BUDGET_CEILING → one second invocation → success.
- **R2.** SDK_BUDGET_CEILING → second invocation fails → no third
  invocation.
- **R3.** Every NON-RETRYABLE execution class → zero second
  invocation.
- **R4.** Insufficient remaining run capacity → no second SDK
  invocation.
- **R5.** The first FAILED invocation remains durably auditable after
  a successful second invocation.
- **R6.** Accepted / rejected / duplicate / breaker-refused tool
  proposals retain the minimum required diagnostic evidence.
- **R7.** Host-valid findings from a failed invocation do NOT become
  live findings.
- **R8.** Maximum actual SDK/model invocations per logical judgment
  task = 2.
- **R9.** Both invocations consume one continuous run-scoped budget
  state/pool. Same-object identity may serve as a test proof today,
  but Python object identity is not the governance contract.
- **R10.** Attempt grouping and ordering is unambiguous. If existing
  `(run_id, task_key)` behavior cannot prove this: STOP for owner
  adjudication.
- **R11.** COMPLETED known cost below reservation → correct converted
  charge.
- **R12.** COMPLETED known cost equal to reservation → correct
  charge.
- **R13.** COMPLETED known cost above reservation → full converted
  estimate accounted; no clamp.
- **R14.** FAILED known estimate below reservation → full reservation
  charged.
- **R15.** FAILED known estimate above reservation → full known
  estimate charged.
- **R16.** FAILED unresolved cost → full reservation charged.
- **R17.** The COMPLETED unresolved-cost path preserves the adopted
  conservative behavior.
- **R18.** Known overshoot updates run accounting such that
  subsequent reservation behavior remains fail-closed.
- **R19.** No duplicate live finding appears because two invocations
  occurred.
- **R20.** ADR-0006 identity tests remain green.
- **R21.** Deterministic checkers remain unchanged.
- **R22.** No network/model access occurs from the model-free suite.
- **R23.** A caught in-process failure persists buffered tool-attempt
  evidence before or atomically with terminal call finalization.
- **R24.** Process-crash behavior remains under the existing
  RESERVED-row reconciliation semantics; ADR-0008 does not claim
  crash-proof per-tool persistence.

## 10. Non-goals

Explicitly excluded: a generic model retry framework; exponential
model retry; generic transport retry; retrying auth/config failures;
more than two model invocations per logical task; a retry budget
pool; an elevated recovery reservation; any EUR 0.75 run-cap
increase; any EUR 0.15 reservation increase; any 0.70 safety-margin
increase; any `MAX_TURNS` increase; any `MAX_TOOL_CALLS_PER_CHECK`
increase; model change; prompt change; fixture change; answer-key
change; scorer change; threshold change; task-state-machine redesign;
finding-lifecycle redesign; ADR-0006 identity redesign; synchronous
SQLite writes for every in-process tool callback;
host-process-crash-proof per-tool persistence; public raw attempt
evidence; Phase 4; `SentinelDailyRun` agent-mode activation; another
validation authorization.

## 11. Validation firewall

**ADR-0008 does not authorize a real-model Phase-3 validation
cycle.** Bound with that:

- ADR-0007's one cycle remains consumed;
- `max_regates` remains consumed;
- the old validation predicates remain historical facts;
- no existing gate runner is amended;
- no current gate predicate is relaxed, and predicates such as
  `zero_agent_calls_failed` are not silently reinterpreted to make an
  audited retry pass;
- no fixture, answer-key, clean-manifest, scorer, threshold, model,
  prompt, or ADR-0006 identity change;
- no Phase-4 progression.

A future real-model validation lineage, if ever justified, requires a
SEPARATE owner-governed decision only AFTER: (1) ADR-0008
implementation exists; (2) the complete model-free proof package is
green; (3) the implementation is on an exact public SHA; (4) exact-SHA
CI is green; (5) independent review is complete. ADR-0008 does not
pre-write or pre-relax the validity predicates of any future
validation protocol.

## Consequences and risks

One pathological judgment call becomes a contained, classified,
audited event: its typed failure class, cost/token evidence and
per-proposal outcomes survive; exactly one bounded second invocation
may recover the scope inside the unchanged run budget; and known
overshoot is accounted instead of clamped. Accepted in exchange: a
failed-then-recovered task can consume up to two reservations; the
retryable set is deliberately minimal and may under-serve failure
classes not yet observed; buffered attempt evidence is not
crash-proof; and the accounting correction permits recorded run
consumption to exceed the nominal cap when a real overshoot is
detected — a truthful record replacing a silent clamp. Nothing here
claims improved judgment quality, and no evaluation evidence exists
for this contract until its model-free proof package lands.

## Reopening and supersession

This ADR reopens before implementation if evidence surfaces that a
decision above rests on a since-corrected fact, following the
diagnosis-correction precedent on record for this phase. During
implementation, the STOP conditions in "Strongest case against" and
§3/R10 return the work for owner adjudication rather than expanding
scope. Its scope otherwise closes when the implementation and its
complete model-free proof package have landed, or when the owner
supersedes it by a later ADR.

## Owner approval

The substantive decision was owner-adjudicated on the red-teamed
design session's decision packet before this adoption. Approved by
owner — 2026-08-20.

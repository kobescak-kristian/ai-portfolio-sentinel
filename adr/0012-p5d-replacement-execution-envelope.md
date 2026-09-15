# 0012 - P5-D replacement execution envelope and evidence durability

Status: ADOPTED

Date: 2026-09-15

## Context

The one official P5-D Sonnet gate authorized by
`adr/0011-phase5-unattended-operation-contract.md` was dispatched once:
Actions run `32880880053`, workflow `sentinel-official-gate`, event
`workflow_dispatch`, attempt 1, source SHA
`eef88a289cf465ad352ee223221d5497465469b3`. Preflight succeeded, the
`P5D_OFFICIAL_SONNET_GATE` one-shot marker was uploaded, and real
Sonnet execution began. GitHub then terminated the job at its
configured 30-minute job ceiling. The gate session never returned, no
evidence file was written, and the final evidence upload failed.

The recorded disposition is `EXECUTION_INVALID / NO_QUALITY_RESULT`. It
is not GREEN, not HONEST_FAIL, and not a usable quality observation.
The original one-shot is permanently consumed. No Sonnet prompt or
response content from that run was inspected or recovered, and none may
be.

Read-only forensics, recorded in `STATE.md`, established two readiness
defects:

- No wall-clock envelope existed anywhere beneath the GitHub job
  ceiling. The pinned SDK read path carries no elapsed-time deadline,
  and Sentinel's bounds (turn count, tool-call count, EUR budget) are
  not time bounds. A committed historical data point of about 37.5
  seconds per real Haiku call, extrapolated to 46 sequential calls,
  already approached 30 minutes before dispatch and was not checked
  against the job timeout.
- Evidence did not survive external termination. The gate work root
  accumulated incremental state, but the workflow uploaded only the
  final artifacts directory, so that state was lost with the runner.

A source correction matters for how this record relates to ADR-0011.
ADR-0011 did not set the official gate's 30-minute timeout. Its only
workflow timeout is the 20-minute value for the scheduled workflow
(section 2). The 30-minute value lives in
`.github/workflows/sentinel-official-gate.yml` and is pinned by
`tests/test_phase5_workflow_contracts.py`. The defect is therefore not a
wrong ADR-0011 clause. ADR-0011 left the execution-envelope and
evidence-survival dimension to implementation, and implementation was
declared ready without demonstrating cumulative-runtime feasibility or
evidence survival under external termination.

An independent red-team ranked four options: 3A (one exceptional
replacement single-run gate) above 1 (permanently block P5-D), above 3B
(replace the methodology with a multi-run campaign), far above 4
(retroactively close P5-D from the timed-out run). The owner then froze
ruling `q77-p5d-replacement-owner-ruling-a`: exactly one replacement
official measurement may eventually be conducted, only after the
execution and evidence defects are repaired and fresh readiness is
independently re-established.

The replacement is not a retry after an unfavorable result. The
original run produced no quality observation at all. Its invalidity is
objective (external job termination before any evidence existed) and
would support replacement equally had partial evidence looked
favorable or unfavorable.

This ADR freezes the governance and architecture of that repair. It
implements nothing, dispatches nothing, calls no model, creates no
marker, mutates no provider or platform configuration, and authorizes
no replacement execution.

## Decision

### 1. Scope and relationship to ADR-0011

ADR-0011 remains authoritative in full, except that this record fills
the execution-envelope and evidence-durability dimension for the P5-D
replacement, and adds the owner-authorized exception to ADR-0011
section 7 ("no confirmation rerun") and section 10 ("the one official
Sonnet gate") for exactly one replacement of an execution-invalid
measurement.

Nothing here reopens the ADR-0011 scheduler, authentication, state
continuity, qualification window, cadence or release contracts.

Multi-run or variance-aware stochastic gate governance (option 3B) is
out of scope and explicitly deferred.

### 2. Frozen quality surface

The repair must leave unchanged:

- the model contract `claude-sonnet-5`;
- the frozen official fixtures;
- the answer key and expected outcomes;
- the official quality prompts and instructions;
- checker and scorer semantics;
- the thresholds in `evals/eval_config.yaml`;
- the GREEN / HONEST_FAIL decision rule;
- the two-run quality methodology;
- 23 judgment tasks per run, 46 judgment tasks total;
- the existing bounded second model attempt per task;
- the existing official quality-budget semantics (5,000,000 micro-EUR
  gate total, 1,000,000 micro-EUR per-call reservation);
- any other parameter materially affecting GREEN-vs-HONEST_FAIL
  probability.

If any repair requires changing this surface, work stops and returns
to owner governance. No change to it is authorized here.

### 3. Replacement identity and one-shot semantics

The original `P5D_OFFICIAL_SONNET_GATE` marker stays permanently
visible and permanently consumed. It is never reset, deleted,
reinterpreted or reused.

The replacement uses a new, distinct one-shot purpose whose identity is
explicitly tied to original run `32880880053` and owner ruling
`q77-p5d-replacement-owner-ruling-a`.

Exactly one replacement execution may be authorized, and only after
fresh readiness and a final owner GO. No workflow rerun, re-dispatch,
`run_attempt > 1` or second marker can create a second eligible
replacement.

A valid replacement GREEN or HONEST_FAIL is terminal. There is no
confirmation run, and no retry because a result is close, surprising,
or looks like an unlucky stochastic realization. No tuning followed by
another attempt.

If the replacement is execution-invalid after its marker is consumed,
available evidence is preserved, P5-D execution stops, no automatic
retry occurs, and a new owner ruling is required.

### 4. Authoritative quality terminality

A locally computed score is provisional. A quality result becomes
authoritative only when a strict-valid terminal evidence record crosses
the external publication boundary, meaning it has been successfully
published as a workflow artifact under the official evidence contract.

Before authoritative publication, timeout, process loss or
unrecoverable execution failure produces execution-invalid status. The
replacement stays consumed, nothing retries automatically, and the
matter returns to owner governance.

After authoritative publication of a valid GREEN or HONEST_FAIL, that
verdict is irreversible. A later timeout, cancellation, cleanup
failure, finalizer failure, ancillary evidence fault or rule archival
failure must not erase, replace or relabel it, and cannot recreate
replacement eligibility. Ancillary incompleteness may block downstream
progression where required.

Where practical, the implementation avoids surfacing the provisional
quality disposition to the operator before authoritative publication.

### 5. Timeout classification

Elapsed-time controls govern execution validity, not quality scoring.
A per-invocation or session timeout that occurs before authoritative
terminal publication maps to:

    EXECUTION_INVALID / INFRASTRUCTURE_FAILURE

never to HONEST_FAIL. Adding a wall-clock deadline must not change the
GREEN-vs-HONEST_FAIL semantics of any run that completes.

### 6. Runtime basis: judgment tasks and invocations

The gate has 46 judgment tasks (23 per run, two runs). Current source
permits up to two SDK invocations per judgment task:

- `agents/checker/config.py`: `MAX_MODEL_ATTEMPTS_PER_TASK = 2`;
- `agents/checker/harness.py`: the attempt loop
  `for attempt_index in range(MAX_MODEL_ATTEMPTS_PER_TASK)`.

The runtime envelope is therefore sized for up to 92 `query_fn`
invocations. These are invocations, not judgment tasks. The second
attempt is part of the frozen methodology and may not be removed or
capped to shorten runtime, because denying a task its second attempt
can change scoring.

Attempt 2 has the same runtime-relevant shape as attempt 1. Each
iteration uses the same request, builds a fresh
`CheckerToolState(request=request)`, calls `build_user_prompt(request)`,
and invokes the same `query_fn` seam with the same model, `MAX_TURNS`
and `MAX_TOOL_CALLS_PER_CHECK`. Only the budget reservation is fresh. A
separate attempt-2 rehearsal stratum is therefore not required merely
because it is attempt 2.

The only retryable class is `SDK_BUDGET_CEILING`
(`agents/checker/failures.py`,
`RETRYABLE_FAILURE_CLASSES = frozenset({SDK_BUDGET_CEILING})`). A second
attempt follows a first attempt that ran until its SDK budget ceiling,
so a retry pair may be two slow invocations rather than one slow and
one fast. Committed evidence shows the path is real: the ADR-0009
validation run recorded 24 invocation rows for 23 logical tasks in run
1, one of them a bounded recovery (`EVAL_RESULTS.md`). This reinforces
sizing for 92.

### 7. Execution envelope

The envelope is frozen prospectively as an engineering safety margin.
`max_observed x 1.5` is a margin, not a statistical tail guarantee.

Outer job timeout:

    outer >= 92 x max_observed x 1.5 + 10 min + 8 min
          =  138 x max_observed + 18 min

The workflow value is the ceiling of `outer` in whole minutes, never
the floor.

Per-invocation deadline:

    max(180 s, 3 x max_observed)

Session deadline, an absolute instant anchored to job start:

    job_start + (outer - 8 min)

The session deadline is never measured as `outer - 8 min` from session
start. Checkout, dependency installation, preflight and marker upload
precede the session, so a session-relative deadline would consume the
finalization margin and run past the job timeout. The anchor must be no
later than the instant the platform job clock started; readiness shows
this.

Per-invocation deadlines do not bound the session (92 invocations at 3x
exceed the session budget by design). The session deadline is the
binding control. The per-invocation deadline only catches a single
stalled invocation.

The 10-minute fixed overhead is a prospective allowance. The 8-minute
finalization margin must cover finalizer and artifact publication, and
readiness must show it does.

Platform feasibility. GitHub-hosted jobs are limited to 360 minutes
(current GitHub Actions limits documentation, verified 2026-09-03).
The envelope is feasible only if:

    138 x max_observed + 18 min <= 360 min

which gives the frozen integer boundary:

    max_observed <= 148 s

(148 s gives 358.4 minutes, ceiling 359; 149 s gives 360.7 minutes.)
The allowed value is never rounded upward. If the timing rehearsal
yields `max_observed > 148 s`, or the ceiling of `outer` exceeds the
supported job limit, work stops and returns to owner governance. The
multiplier, overhead and finalization margin are never reduced to make
the envelope fit.

### 8. Inner execution controls

Authorized architecture:

- a per-logical-invocation wall-clock deadline at the existing
  `query_fn` wrapper seam (where `health_gated` already wraps
  `stub.query_fn` in `scripts/run_phase5_official_gate.py`);
- the whole-session deadline from section 7;
- a session-scoped infrastructure-failure latch set by any timeout;
- once the latch is set, no further provider work starts;
- explicit handling of SDK cancellation and termination;
- a watchdog or backstop that terminates the runner process if normal
  cancellation stalls.

`MAX_TURNS`, tool-call bounds and EUR budgets are not wall-clock
controls. No additional per-task or per-run timeout layer is added
unless implementation evidence shows it is mechanically required and
consistent with this record.

### 9. Operational journal and terminal evidence

An operational-only append journal is authorized under the gate's
artifact work surface. It may contain non-content metadata only:

- gate and replacement identity;
- workflow run ID and run attempt;
- source and configuration identity;
- task and run progression;
- invocation start and terminal state;
- timeout and cancellation events;
- elapsed timing;
- budget and reservation metadata;
- infrastructure error metadata;
- heartbeats and last checkpoint.

The journal must never contain model response content, finding text,
answer content, or reconstructed quality outcomes outside the proper
terminal evidence contract.

Journal writes are serialized under a lock, thread-safe, flushed and
fsynced. Terminal evidence is written atomically: temporary file, flush
and fsync, then atomic replace.

### 10. Workflow finalizer

A workflow-level finalizer runs after the evaluation step under
unconditional (`always()`-class) execution semantics.

Runner evidence takes precedence only if it strict-parses, passes
schema validation, passes identity validation, and passes replacement
provenance validation (section 18). Missing, partial, malformed or
identity-invalid runner evidence is never treated as a quality result.

Where no trusted runner evidence exists, the finalizer may create
distinct execution-invalid evidence from operational metadata. The
finalizer must never overwrite or replace a valid authoritative GREEN
or HONEST_FAIL record.

This record makes no assumption about platform behavior of
unconditional steps after a job-level timeout. That behavior is
established only by the rehearsal in section 12.

### 11. Durability claim and residual

The architecture is designed to survive Python crash, SDK hang, inner
timeout, process kill, and step or job cancellation, in each case only
where the runner stays available long enough for finalization and
publication to execute.

It does not claim that runner-local journal data survives total runner
or VM destruction before external publication. If that happens before
authoritative publication: no quality result is inferred, the
replacement stays consumed, durable platform metadata may support an
execution-invalid classification, no automatic replacement occurs, and
the matter returns to owner governance.

### 12. Model-free GitHub job-level kill rehearsal

Fresh readiness requires a model-free GitHub Actions rehearsal of the
actual job-level timeout or cancellation class that caused the
incident. It uses no model, no OIDC or WIF exchange, no federation
rule, no official or replacement marker, no one-shot consumption, and
no artifact name that collides with an official discovery prefix.

It must exercise, in order: fake execution, a real job-level timeout
or cancellation, the real finalizer, the real artifact publication
path, post-run download, and strict parsing of the resulting
execution-invalid evidence. A step-level timeout test may supplement
it but never substitutes for job-level proof.

The readiness claim states exactly which evidence classes the
rehearsal proved survive. It never claims journal survival when only
finalizer-created invalid evidence survived.

### 13. Real Sonnet timing rehearsal

Model-free testing cannot establish production Sonnet wall-clock
feasibility, so one real-provider timing rehearsal is authorized. It is
not a quality evaluation.

Before its first provider call, a committed pre-registration freezes
and hashes:

- N = 24 invocations;
- the exact synthetic corpus and its hashes;
- two strata of 12, with a runtime-relevant structural rationale;
- the pinned model `claude-sonnet-5`;
- the pinned SDK, harness and cage configuration;
- a production-representative prompt structure, where safe;
- the measurement boundary: one complete logical invocation at the
  `query_fn` seam, including all internal agent and tool turns;
- sequential execution;
- the statistic `max_observed` (not an empirical p95);
- the 1.5 multiplier, 10-minute overhead and 8-minute finalization
  margin;
- the 360-minute platform ceiling and the section 7 PASS boundary;
- the EUR 1.00 rehearsal budget and its start-control arithmetic;
- the PASS / STOP rule.

Forbidden: official fixtures; the official answer key; official quality
scoring; changing the corpus after seeing timings; discarding any
observation; any automatic second rehearsal or new calibration set.
Outputs are discarded; only timing and cost metadata are kept. Every
observation counts.

Budget and representativeness constraint. EUR 1.00 is a hard maximum
on accounted rehearsal consumption, enforced prospectively. The
production per-call reservation (1,000,000 micro-EUR) cannot be reused
unchanged, because it would permit only one invocation. Any smaller
per-invocation budget ceiling could end a slow invocation earlier than
production would, which would understate `max_observed`. Therefore any
rehearsal invocation that terminates at its budget ceiling is a
non-representative observation. It cannot be discarded, so it
invalidates the rehearsal: STOP.

The rehearsal also stops, and returns to governance, if it faults,
exhausts its budget, is incomplete, or fails the feasibility rule.

### 14. Temporary rehearsal federation rule

The timing rehearsal may use a temporary provider federation rule. This
record authorizes the architecture only. Any provider console mutation
is a later, explicit, human-controlled stage.

The rule must be technically bound to the rehearsal workflow only,
incapable of authorizing the official replacement workflow or any
generic repository workload, and of the minimum effective supported
scope. Its effective identity, scope and claims are read back before
use. No static credential is used, and no quality-run authorization is
implied.

After the rehearsal, the rule is verified to be genuinely
non-authorizing. If archiving is only administrative and does not
revoke effective authorization, the supported disable, delete or
revocation mechanism is used instead.

### 15. Provider cap

The provider monthly cap may be raised only after a fresh, dated
readback and arithmetic, with headroom defined prospectively. The cap
is the smallest supported value satisfying:

    month-to-date real spend
    + approved rehearsal exposure
    + existing replacement-gate exposure
    + explicit headroom

and it stays within the existing EUR 50 per month program ceiling. On
this lane that ceiling is operator-observed;
`sentinel/phase5/cadence.py` enforces it for the scheduled lane and
freeze headroom, not the gate runner.

Provider capacity and execution authorization are separate variables.
More capacity authorizes no extra rehearsal, replacement or quality
evaluation.

### 16. Cost classes

Three cost classes are kept distinct:

- A: the original execution-invalid official-attempt spend;
- B: readiness and timing-rehearsal spend;
- C: the replacement official quality-gate spend.

All real spend stays visible in program and provider accounting. Only
class C enters the replacement gate's frozen 5,000,000 micro-EUR
accounting. Classes A and B are not omitted, not double-counted, and
not inserted into class C. Implementation inspects downstream
cost-ledger handoff and headroom logic accordingly, including the
seam-3 CostRow byte-identity check.

### 17. Retries

No workflow-level automatic rerun may authorize another replacement.
No new application-level quality retry is introduced. The existing
bounded second attempt (section 6) remains, as frozen methodology.

Current Sentinel code has no transport retry beyond that bounded
attempt. Whether the pinned SDK or CLI retries transport internally
within one logical invocation is not established by this record.
Implementation documents it where technically observable and includes
its timing and cost effects in metadata where possible. Such behavior
never becomes a hidden additional official quality observation.

### 18. Replacement provenance

Base evidence-schema fields may stay optional for backward
compatibility. On the replacement path, construction and validation
both require:

- `replacement_of_run_id = 32880880053`;
- `owner_ruling_id = q77-p5d-replacement-owner-ruling-a`;
- the replacement marker purpose and identity;
- the exact source SHA;
- the workflow run ID;
- the run attempt;
- the execution-envelope identity and version;
- the termination source, for execution-invalid evidence.

Evidence missing any of these is not eligible as P5-D replacement
evidence.

### 19. P5-E seam compatibility

Current seam-3 code in `scripts/run_phase5_window_freeze.py`
(`_verify_provider_phase_prerequisites`) requires exactly one
`P5D_OFFICIAL_SONNET_GATE` marker and exactly one correlated gate
evidence record whose disposition is GREEN or HONEST_FAIL. After the
incident that condition can never be met: the original marker exists,
but no correlated evidence exists. A replacement under a distinct
purpose would not satisfy it either. The seam therefore requires
repair.

The repair changes only which execution is authoritative:

- the original invalid marker stays permanently visible and is
  explicitly non-qualifying;
- exactly one owner-authorized replacement marker is required;
- exactly one correlated replacement evidence record is required;
- section 18 provenance is mandatory;
- duplicates or ambiguity fail closed.

GREEN and HONEST_FAIL keep exactly their pre-incident downstream
consequences. Current source accepts both identically at that seam.
Implementation re-reads the seam before coding and preserves that
consequence unchanged. This is provenance plumbing, not methodology
change.

### 20. Readiness and execution binding

The replacement executes only the exact materially relevant snapshot
that passed fresh readiness. Binding is mechanical where controllable:

- source SHA;
- workflow identity and content;
- frozen quality-file hashes;
- execution-envelope configuration;
- replacement-marker semantics;
- dependency lock and resolution;
- runtime and environment identity;
- federation rule identity and configuration;
- time-sensitive provider-cap readback;
- exact-SHA CI status.

Any material change invalidates readiness and requires requalification.
No arbitrary calendar expiry applies when nothing changed.
Time-sensitive live facts are freshly re-read at the final GO.

### 21. Execution and publication state model

The implementation distinguishes at least:

    PREFLIGHTED
    REPLACEMENT_MARKED
    EXECUTING
    SCORED_PROVISIONAL
    TERMINAL_EVIDENCE_WRITTEN
    TERMINAL_EVIDENCE_PUBLISHED
    INVALID_EVIDENCE_WRITTEN
    INVALID_EVIDENCE_PUBLISHED
    PUBLICATION_FAILED

Evidence is never called published when publication failed. A
publication failure after marker consumption means: the replacement is
consumed, the execution and evidence state is recorded honestly from
surviving metadata, nothing reruns automatically, and the matter
returns to governance, unless an authoritative quality result was
already published.

### 22. Fresh replacement readiness matrix

Fresh readiness covers at least these rows. Each row, when later
defined, names its required evidence, PASS rule, STOP rule, whether it
is model-free or real-provider, and whether it runs locally or on
GitHub.

1. clean runtime and dependency closure
2. deployment parity
3. artifact access
4. OIDC/WIF production path
5. work-root behavior
6. replacement-marker semantics
7. ordinary infrastructure exception
8. per-invocation timeout
9. session timeout, including the job-start anchor
10. local external-process kill
11. journal and evidence behavior after process loss
12. workflow finalizer contract
13. real GitHub job-level kill rehearsal
14. real Sonnet timing rehearsal
15. frozen quality-surface equivalence
16. exact source and configuration readiness binding
17. original marker never reset
18. no automatic replacement retry
19. P5-E replacement provenance compatibility
20. cost-class and accounting correctness

### 23. Implementation boundary

Expected surfaces, from current source, include: the official gate
workflow and runner; `sentinel/phase5/evidence_records.py`; the
`OneShotMarker` purpose literal in `sentinel/phase5/models.py`;
`sentinel/phase5/oneshot.py`; `sentinel/phase5/artifact_names.py`;
`scripts/_phase5_common.py`; `scripts/run_phase5_window_freeze.py`; the
journal, finalizer and watchdog; the timing-rehearsal and
kill-rehearsal workflows and drivers; tests; and `STATE.md`.

This list is not permission to change every file. Implementation stays
minimal and evidence-driven, and adds no third-party dependency without
separate justification and review.

## Rejected alternatives

- Permanently blocking P5-D (option 1): discards a repairable
  measurement whose invalidity is objective.
- Replacing the methodology with a multi-run campaign (option 3B):
  changes the frozen quality surface more than necessary; deferred.
- Closing P5-D from the timed-out run (option 4): evidentially invalid;
  no quality observation exists.
- Capping or removing the second model attempt to shorten runtime:
  changes the frozen quality surface.
- Sizing the envelope on 46 invocations: understates the reachable
  worst case of 92.
- A session deadline measured from session start: overruns the job
  timeout by the pre-session overhead.
- Raising the job timeout beyond the supported platform limit, or
  shrinking margins to fit it.

## Reopening / stop conditions

Work returns to owner governance if:

- the repair requires a frozen quality-surface change;
- the timing rehearsal cannot complete prospectively;
- the derived envelope exceeds supported or approved infrastructure;
- the real GitHub job-level kill rehearsal fails;
- external publication or durability semantics cannot be demonstrated;
- replacement uniqueness cannot be enforced;
- P5-E compatibility cannot be achieved without a quality-semantic
  change;
- exact readiness and execution binding cannot be established;
- original Sonnet quality content would need to be inspected;
- an automatic second replacement becomes possible;
- current governing authority conflicts with this record.

## Consequences

The execution-envelope and evidence-durability repair is now governed.
Implementation has not begun. Neither rehearsal has run. Fresh
readiness is not established. The replacement is not ready and is not
authorized for dispatch.

P5-D remains in progress and unresolved. The original run remains
`EXECUTION_INVALID / NO_QUALITY_RESULT`, and its marker remains
consumed. P5-E is not started, and no production or production-ready
claim is permitted.

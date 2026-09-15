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

## Amendment A - 2026-09-15 independent-review corrections

Status: ADOPTED

Date: 2026-09-15

This amendment is part of ADR-0012. It records corrections required by
an independent conformance review of the adopted text (verdict: PASS
WITH REQUIRED CORRECTIONS), all of which the owner accepted before any
implementation or rehearsal provider call.

The original decision text above is preserved unchanged as historical
decision evidence. Where it conflicts with this amendment, this
amendment controls.

This amendment does not change the frozen quality surface (section 2),
does not change the Option 3A owner ruling, keeps exactly one
replacement as the maximum authorization, and authorizes no
implementation, rehearsal, marker action, provider or platform
mutation.

Sections affected:

- sections 3, 19 and 21: artifact-only consumption and history wording
  is superseded to the extent A1 requires;
- section 4, last paragraph: "where practical" is superseded by A2;
- section 7, per-invocation deadline: superseded by A4;
- section 10: tightened by A6;
- section 11 and section 12: supplemented by A5;
- section 13, EUR 1.00 budget and the "Budget and representativeness
  constraint" paragraph: superseded by A3;
- section 16: clarified by A7;
- section 20: extended by A8;
- section 22: extended by A9.

### A1. Durable consumption and result memory

Problem. Every Phase-5 workflow uploads artifacts with
`retention-days: 90`, and GitHub limits artifact retention in public
repositories to at most 90 days, after which artifacts are deleted. An
artifact-only mechanism therefore cannot truthfully enforce permanent
consumption, irreversibility of a valid result, or later
reconstruction of the authoritative P5-D history.

Observed 2026-09-15 by read-only API query, the historical artifacts
that replacement and P5-E logic still depend on expire as follows:

    artifact 9540505807
      sentinel-p5-oneshot-p5c-wif-probe-r32783229864
      expires 2026-11-22T22:09:06Z
    artifact 9540511349
      sentinel-p5-probe-evidence-r32783229864-a1
      expires 2026-11-22T22:09:06Z
    artifact 9575720463
      sentinel-p5-oneshot-p5d-official-sonnet-gate-r32880880053
      expires 2026-11-23T17:56:54Z

No gate evidence artifact exists for run `32880880053`; its upload
failed.

Rules:

1. Successful external workflow-artifact publication remains the
   moment a strict-valid GREEN or HONEST_FAIL becomes authoritative
   (section 4). The quality-publication boundary does not move into
   Git.

2. A machine-readable, committed durable receipt registry is added. Its
   purpose is long-term memory of already-established external
   evidence. Each applicable receipt carries enough immutable
   provenance to reconstruct the authoritative decision without an
   unexpired artifact, including where applicable:

   - purpose or evidence class;
   - GitHub workflow run ID;
   - run attempt;
   - source SHA;
   - artifact name;
   - numeric artifact ID;
   - SHA-256 of the verified marker or evidence bytes;
   - disposition;
   - `replacement_of_run_id`;
   - `owner_ruling_id`;
   - recorded-at timestamp;
   - receipt schema version.

3. Artifact bytes remain the primary evidence while retained. The
   receipt is the permanent machine-readable memory that the artifact
   existed, was strict-validated, and produced the stated disposition.
   A receipt never invents or reconstructs missing quality content.

4. Before replacement readiness can pass, implementation creates and
   verifies durable receipts for all historical Phase-5 evidence that
   replacement or P5-E logic still needs, including:

   - the P5-C capability probe marker and evidence;
   - the original P5-D consumed marker;
   - the original P5-D disposition
     `EXECUTION_INVALID / NO_QUALITY_RESULT`.

   The original P5-D receipt must not claim gate evidence existed. It
   records that the marker existed and was consumed, and that the
   separately governed incident disposition is execution-invalid with
   no quality result.

5. Historical receipts may be created only while their source
   artifacts can still be independently downloaded and verified. The
   earliest observed expiry is `2026-11-22T22:09:06Z`. If required
   verification is no longer possible, work stops and returns to owner
   governance.

6. After the replacement terminal artifact is successfully published
   and independently validated, its durable receipt is committed before
   P5-E or any final progression. Failure to commit that receipt does
   not erase an already-authoritative GREEN or HONEST_FAIL and does not
   recreate replacement eligibility. It only blocks downstream
   progression until the durable record is resolved.

7. One-shot discovery, replacement eligibility and the repaired P5-E
   seam consult the durable committed history. Absence caused by
   artifact expiry is never treated as "never happened". Artifact
   expiry never resets consumed status.

8. Artifact-only wording in sections 3, 19 and 21 is superseded to the
   extent necessary to implement this rule.

### A2. No pre-publication quality exposure; cancellation

Problem. Current source (`scripts/run_phase5_official_gate.py`) prints
`DISPOSITION: <disposition>` and returns exit status 0 for GREEN and 1
otherwise in the execute step, before the evidence upload step runs. An
operator could therefore learn that a provisional result is
unfavorable and cancel before publication.

Rules:

1. Before successful external publication, a valid quality disposition
   is not exposed through execute-step stdout or stderr, an execute-step
   exit-code distinction, the job summary, log-visible journal output,
   or any other operator-visible workflow surface that distinguishes
   GREEN from HONEST_FAIL.

2. The execute step has identical quality-neutral success behavior for
   a successfully computed GREEN and a successfully computed
   HONEST_FAIL. Infrastructure failure may still be signaled
   separately.

3. The disposition may exist inside the private candidate evidence file
   needed for publication. Its contents are not echoed to logs before
   publication succeeds.

4. The operational journal may retain bounded internal execution
   metadata, but quality-predictive journal state is not streamed or
   echoed into workflow logs before publication.

5. Only after successful authoritative artifact publication may the
   workflow, or a later governed recording step, surface the valid
   GREEN or HONEST_FAIL disposition.

6. Cancellation after replacement-marker consumption is never an
   automatic basis for another replacement. A termination is classified
   as objective infrastructure invalidity only when its termination
   source is positively established by surviving evidence. A generic,
   manual, forced or unknown cancellation is not, by itself, evidence of
   objective infrastructure invalidity. It leaves the replacement
   consumed, with no automatic retry and no second replacement under
   the current ruling, and work stops and returns to owner governance.

7. This rule binds equally whether the hidden provisional result would
   have been GREEN or HONEST_FAIL.

### A3. Timing rehearsal budget

Source correction. Section 13 states that the production per-call
reservation "would permit only one invocation" under a
1,000,000 micro-EUR total. That is incorrect. Current
`RunBudgetCoordinator.reserve()` (`agents/checker/budget.py`) reserves
`min(remaining, max_per_call_reserve)`, and `commit()` releases any
unused reservation after a completed call.

Kept unchanged: N = 24; sequential execution; two strata of 12;
production-representative invocation structure; the `max_observed`
statistic; every observation counts; no second calibration rehearsal.

Owner decision, made prospectively before any rehearsal provider call:
the rehearsal's hard class B budget changes from EUR 1.00 to EUR 2.50.
This does not alter the replacement gate's EUR 5.00 quality budget,
the EUR 50 per month program ceiling, the number of replacement
executions authorized, or any quality threshold or scoring rule.

Rules:

1. Each rehearsal invocation uses the same maximum per-call
   reservation and SDK allowance basis as the official gate:

       1,000,000 micro-EUR maximum reservation per invocation

2. Total rehearsal class B budget:

       2,500,000 micro-EUR

3. Before every rehearsal invocation starts, remaining rehearsal
   capacity must be enough to grant the full production-equivalent
   reservation. If the coordinator would have to lower that
   invocation's SDK allowance below the production-equivalent allowance,
   the rehearsal stops before starting it. No truncated or
   non-representative invocation is run. Equivalently, cumulative
   accounted consumption before any invocation must not exceed
   1,500,000 micro-EUR.

4. Any invocation that reaches its SDK budget ceiling makes the
   rehearsal non-representative: STOP.

5. Any overshoot, exhaustion, incomplete N = 24 corpus,
   infrastructure fault or feasibility failure: STOP.

6. No observation is discarded.

7. No automatic second rehearsal is authorized.

8. Before any provider mutation or use, implementation shows fresh
   provider-cap arithmetic covering the approved EUR 2.50 rehearsal
   exposure. Provider capacity grants no additional execution
   authorization.

### A4. Per-invocation stall deadline

The section 7 per-invocation deadline `max(180 s, 3 x max_observed)` is
too close to normal timing evidence for a control whose purpose is to
catch a pathological stall, not to redefine legitimate slow calls. It
is superseded by:

    min(remaining_to_session_deadline, max(600 s, 10 x max_observed))

Rules:

- the whole-session deadline remains the binding cumulative bound;
- the per-invocation deadline catches a single pathological stall;
- it never extends beyond the session deadline;
- reaching either deadline before authoritative publication remains
  `EXECUTION_INVALID / INFRASTRUCTURE_FAILURE`;
- no timeout becomes HONEST_FAIL;
- no margin changes after seeing replacement quality.

Unchanged unless a later owner ruling prospectively changes them:

    outer >= 138 x max_observed + 18 min
    max_observed <= 148 s

### A5. Cancellation finalization bound

GitHub's workflow cancellation reference (verified 2026-09-15) states
that on cancellation the runner sends SIGINT / Ctrl-C to the step's
entry process; if it has not exited within 7500 ms, sends SIGTERM /
Ctrl-Break and waits a further 2500 ms; then kills the process tree.
After a 5-minute cancellation timeout, the server forcibly terminates
all jobs and steps still marked for cancellation.

The 8-minute finalization margin in section 7 remains the reserve for
the normal session-deadline path. For cancellation and job-kill
readiness:

- any finalizer or publication behavior relied upon after cancellation
  completes within the platform's 5-minute cancellation window;
- cancellation handling tolerates the documented signal sequence;
- readiness demonstrates the actual available timing, and does not
  assume the 8-minute reserve applies after cancellation;
- whether a job-level timeout follows the same sequence is established
  only by the real GitHub rehearsal (section 12), which remains
  required;
- if that rehearsal cannot demonstrate this, work stops.

### A6. Finalizer duplicate-terminal rule

Section 10 is tightened. If strict-valid trusted runner terminal
evidence already exists, the finalizer does not create a competing
execution-invalid record. It either preserves and uses the trusted
terminal evidence, or does nothing beyond non-conflicting operational
metadata.

A valid GREEN or HONEST_FAIL and an execution-invalid record for the
same replacement execution never coexist as competing terminal
dispositions. Temporary or atomic-write staging files are never
included as candidate terminal evidence.

### A7. Class A cost accounting

The original execution-invalid run has no valid official gate CostRow;
the committed ledger contains none for run `32880880053`, and none is
fabricated.

- Class A stays visible as a separately labelled provider-accounting
  estimate with its provenance and uncertainty, as recorded in
  `STATE.md`.
- Class B stays readiness and rehearsal spend.
- Class C stays replacement official quality-gate spend.
- Only class C enters the replacement gate's EUR 5 quality budget.
- All three stay visible in broader program and provider accounting.

### A8. Readiness binding additions

Section 20 is extended. Fresh readiness also captures or verifies,
where technically available:

- the exact resolved provider model identifier returned by the actual
  execution path, not only the mutable alias;
- the GitHub runner image identity and version;
- the fully resolved dependency set used by the replacement;
- the pinned `claude-agent-sdk` package identity (currently
  `claude-agent-sdk==0.2.110`);
- the identity of the CLI executable bundled with that package, which
  the SDK uses when no CLI path is given;
- verification that no bundled CLI or runtime auto-update mechanism can
  silently alter execution after readiness;
- all source, workflow, quality-hash, federation-rule and provider-cap
  bindings section 20 already requires.

No new third-party dependency is added merely to implement this
binding. If exact binding is technically impossible for a material
input, work stops and the residual is reported before replacement
authorization.

### A9. Readiness matrix additions

Section 22 is extended with rows that carry the same required fields
(evidence, PASS, STOP, model-free or real-provider, local or GitHub):

21. durable receipts for historical Phase-5 evidence created and
    verified before the earliest artifact expiry (A1)
22. no pre-publication quality exposure on any operator-visible
    surface, for both GREEN and HONEST_FAIL (A2)
23. cancellation-path finalization and publication within the
    platform cancellation window (A5)

### A10. Unchanged by this amendment

- the quality model contract `claude-sonnet-5`;
- official fixtures, answer key, prompts and instructions;
- checker and scorer semantics and thresholds;
- the GREEN / HONEST_FAIL rule;
- the two-run methodology, 23 judgment tasks per run, 46 total;
- the existing bounded second attempt;
- the replacement gate's EUR 5 total budget and EUR 1 maximum per-call
  reservation;
- the replacement count and the Option 3A owner ruling;
- the outer job timeout formula and the `max_observed <= 148 s`
  boundary.

The EUR 2.50 change applies only to the non-quality timing rehearsal.

Implementation has not begun. Neither rehearsal has run. Fresh
readiness is not established. The replacement is not ready and is not
authorized for dispatch.

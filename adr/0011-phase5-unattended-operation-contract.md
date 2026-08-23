# 0011 - Phase 5 unattended-operation contract

Status: ADOPTED

Date: 2026-08-23

## Context

Phase 4 is CLOSED as of 2026-08-23. That closure and all earlier gate
results remain settled historical evidence and are not reopened by this
record.

BLUEPRINT §6 defines Phase 5 as the scheduler migration to GitHub
Actions, the official Sonnet gate on the frozen fixture contract, and
then five consecutive Actions-scheduled live runs within caps with zero
lost runs. It also requires a versioned release and an evidence-backed
operations runbook derived from real operation.

The current standing Windows `SentinelDailyRun` remains stub-mode. It
has historical value, but it does not prove the Phase-5 target:
unattended GitHub-hosted scheduled operation with the caged judgment
path active.

Phase 5 introduces several choices that must be frozen before
implementation: scheduler identity and trust, authentication, cross-run
state continuity on ephemeral runners, the evidence rule for a missed
or late scheduler fire, the migration boundary from Windows,
cost-driven cadence behavior, the official Sonnet gate's independent
budget, and the exact conditions under which five scheduled runs count.

Those choices were made prospectively on 2026-08-23, before any Phase-5
implementation. This ADR records them.

Current official documentation used for the platform assumptions:
- Anthropic, "Use WIF with GitHub Actions":
  https://platform.claude.com/docs/en/manage-claude/wif-providers/github-actions
- GitHub, "OpenID Connect reference":
  https://docs.github.com/en/actions/reference/security/oidc
- GitHub, "Events that trigger workflows", schedule:
  https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule

The current docs establish the intended GitHub OIDC and Anthropic WIF
mechanism and also document that scheduled Actions events may be
delayed and, under sufficiently high load, queued jobs may be dropped.
That scheduler behavior is treated as an evidence problem to measure,
not as an assumption to ignore.

This ADR is governance and architecture adoption only. It creates no
workflow, performs no authentication, calls no model, migrates no data,
changes no scheduler and executes no gate.

Adoption of this ADR is the first Phase-5 write. Phase 5 therefore
moves from PERMITTED / NOT STARTED to IN PROGRESS on 2026-08-23. Phase
5 remains OPEN.

## Decision

### 1. Phase-5 objective and non-goals

Phase 5 proves reliable unattended operation after an explicitly
recorded Windows-to-Actions migration boundary.

It does NOT prove seamless migration of one physical SQLite database
from the Windows era into GitHub-hosted runners. No Phase-5 requirement
demands that continuity, and one-off migration machinery would add a
different engineering problem to the learning objective.

The existing SQLite backend remains the backend throughout Phase 5.
Phase 5 adds no new database engine, ORM, database-provider abstraction
or migration framework.

The legacy Windows operational database is retained read-only. At the
migration boundary, its final SHA-256 and relevant table/row counts are
recorded. It is not rewritten to manufacture continuity with the new
Actions lineage.

The Actions era begins with a fresh SQLite operational lineage. Any
findings rediscovered after that boundary may legitimately receive a
new lineage-local `first_seen`. Public evidence must call this a
migration-lineage re-baseline, never a regression and never continuous
database identity since Phase 2.

### 2. GitHub Actions scheduler contract

Target schedule:

    37 6 * * *

The schedule is UTC and intentionally not at the top of the hour.

Target job permissions are exactly the minimum currently required by
the design:

    contents: read
    actions: read
    id-token: write

The scheduled workflow must not use `pull_request_target`, must not
place the model-calling path on a pull-request-reachable trigger, and
must not gain repository write permission.

Concurrency contract:

    group: sentinel-schedule
    cancel-in-progress: false

Workflow timeout:

    20 minutes

A scheduled invocation is never cancelled merely because the next
invocation exists. Overlap is prevented by concurrency; evidence is
preserved rather than discarded.

### 3. Authentication target and WIF capability probe

Scheduled Actions operation targets GitHub OIDC Workload Identity
Federation to the Anthropic API. The purpose is to avoid a stored
long-lived Anthropic API secret in GitHub.

The production federation trust rule is least-privilege and must be
bound to:
- the repository owner;
- this exact repository;
- `refs/heads/main`;
- the intended scheduled event, `event_name = schedule`;
- the Anthropic API audience required by current provider
  documentation.

No secret identifier, token value or credential value is committed to
this repository.

The repository currently pins `claude-agent-sdk==0.2.110`. Repository
evidence does not yet prove that this exact pinned execution path
successfully consumes the approved WIF configuration in GitHub Actions.
Therefore Phase 5 authorizes exactly ONE empirical capability probe.

The probe:
- occurs in P5-C;
- is Haiku;
- has maximum accounted allowance 150,000 micro-EUR;
- is explicitly NON-QUALIFYING;
- cannot count toward the five scheduled live runs;
- cannot count as the official Sonnet gate;
- cannot widen the final production trust rule.

If a manual `workflow_dispatch` path is needed only to perform that
probe, its WIF trust is temporary/separate or equivalently narrowed.
The final scheduled trust rule remains schedule-bound.

A failed WIF compatibility probe does not authorize a static credential
fallback. It stops the WIF path for a separate owner decision.

### 4. Actions-era state continuity

GitHub-hosted runners are ephemeral. Phase-5 continuity therefore uses
an immutable Actions artifact chain as transport for the existing
SQLite-backed state, not GitHub cache as authoritative storage.

Every post-run state bundle must carry all mutable state required for a
truthful next run and a manifest sufficient to reconstruct provenance.

At minimum, the manifest records:
- bundle schema/version;
- GitHub workflow run identity and run attempt;
- event name;
- source commit SHA;
- Sentinel run ID, or an explicit no-run/refusal outcome;
- predecessor manifest identity;
- SHA-256 of the predecessor manifest;
- SHA-256 for every carried state/evidence file.

The exact file list is implemented in P5-B from current runtime
requirements. The invariant is that restoring the predecessor bundle
must produce the same continuing operational lineage that the next run
expects. A cache hit or miss may improve speed but never establishes
state truth.

A broken predecessor reference, missing authoritative state file or
hash mismatch fails closed. The run does not silently bootstrap a new
lineage inside an active qualification window.

The Actions artifact chain is retained under the repository's governed
retention policy. Phase 5 does NOT commit the final SQLite database
snapshot to the public Git repository.

Public closure evidence may commit manifests and the repository's
normal public evidence surfaces. Raw operational SQLite remains outside
Git.

### 5. Prospective five-slot qualification window

The five-run requirement is prospective. Phase 5 may not search history
after the fact and select five successful scheduled runs.

Before slot 1, a machine-readable qualification record freezes:
- the five expected daily schedule slots;
- workflow identity;
- cron and timezone;
- branch/ref;
- exact source commit SHA;
- qualifying run mode;
- the 120-minute qualification tolerance.

No push to `main` is allowed while that five-slot qualification window
is active.

For one slot to qualify, there must be a one-to-one chain:

expected frozen slot
to GitHub Actions workflow run
to Sentinel run ID
to terminal Sentinel ledger row
to exactly one matching CostRow
to intact successor state bundle

The GitHub workflow run must have:
- `event == schedule`;
- `run_attempt == 1`;
- the frozen workflow identity;
- the frozen default-branch ref;
- the frozen source SHA.

The Sentinel execution must be:
- live;
- judgment mode agent;
- terminal;
- within its applicable run budget;
- represented by exactly one qualifying run ID and CostRow.

The following never count:
- local/manual CLI runs;
- `workflow_dispatch`;
- reruns;
- `run_attempt > 1`;
- duplicates;
- stub-mode runs;
- wrong-event runs;
- wrong-ref runs;
- wrong-SHA runs;
- rehearsal runs;
- the WIF capability probe;
- historical Windows scheduler runs.

Qualification timing is frozen at 120 minutes from the expected slot.

A matching scheduled execution inside 120 minutes may qualify and its
observed delay is recorded.

A matching scheduled execution observed after 120 minutes is
`LATE_NONQUALIFYING`.

At independent evidence review, an expected slot with no matching
scheduled execution in GitHub's run history is `MISSING/LOST`.

Both `LATE_NONQUALIFYING` and `MISSING/LOST` break the consecutive
five-run streak. So do a failed or nonterminal Sentinel run, duplicate
execution, missing CostRow, wrong provenance, or broken predecessor
state chain.

A failed qualification window is not repaired by counting a manual run
or a rerun. A new five-slot window must be frozen prospectively.

Any change to workflow identity, cron, timezone, cadence or source SHA
invalidates the active window and requires a newly frozen window.

### 6. Cost and cadence contract

Existing live Haiku run cap remains:

    750,000 micro-EUR

Existing lane hard ceiling remains:

    EUR 50 per trailing 30 days

Existing frequency rule remains:
- daily;
- if trailing-30-day spend exceeds EUR 40, reduce one notch to every
  two days;
- if the rule fires again at the reduced cadence, reduce to weekly;
- caps and ceiling never rise to fit the desired frequency.

Before freezing a five-slot qualification window:

    actual trailing-30-day accounted spend
    + remaining nominal qualifying-run caps
    <= EUR 40

Actual official-gate consumption already present in telemetry is not
added a second time as a hypothetical EUR 5 allowance.

This is risk headroom, not a guarantee. Existing accounting rules allow
known post-call overshoot to be recorded honestly.

If trailing-30-day spend crosses EUR 40 during an active qualification
window, that window fails and is consumed. The cadence rule takes
precedence over preserving a streak.

A `COST_CADENCE_REFUSAL` or `CADENCE_SKIP` path:
- makes zero model calls;
- records its reason durably;
- never counts as a qualifying successful live run.

Under WIF, the scheduled path is API-billed operation. CostRows remain
accounted-consumption telemetry and are not described as a provider
invoice.

### 7. Model routing and the official Sonnet gate

Ordinary scheduled live operation resolves to Haiku.

No ordinary Sentinel CLI option exposes "official gate" as a generic
model-purpose switch. The official Sonnet route is available only
through the dedicated Phase-5 gate runner:

    scripts/run_phase5_official_gate.py

The Phase-5 official gate uses the already-frozen fixture corpus,
answer key, scorer, scoring thresholds and invariants. Historical
Phase-3 gate, re-gate and validation evidence is immutable and is not
rewritten or consumed again.

The official gate gets its own total accounted-consumption coordinator:

    5,000,000 micro-EUR

The Haiku per-call reservation ceiling of 150,000 micro-EUR is NOT
inherited by the Sonnet gate.

Before the designated gate execution, P5-B must mechanically derive and
commit:
- the exact current Sonnet model ID;
- the official-gate per-call reservation;
- the derivation evidence from current canonical Anthropic pricing, the
  frozen gate workload/token envelope and the repository's existing
  FX/accounting policy.

If that reservation cannot be derived without a new discretionary
assumption, execution stops for a new owner decision. It is never
guessed and never copied from Haiku for convenience.

The gate preflight completes before the one designated invocation.
After model execution begins, this ADR authorizes no confirmation
rerun.

The Phase-5 gate item closes on either:
- GREEN; or
- honest FAIL with the miss-pattern analysis committed.

An honest FAIL does not authorize moving the fixtures, answer key,
scorer, thresholds, model route or budget after seeing the result.

#### Amendment (2026-08-23): owner-fixed official Sonnet gate reserve

**A. Original pre-write stop.** The first P5-B implementation attempt
reached this gate and stopped before any repository write, with reason
`SONNET_RESERVE_DERIVATION_NOT_MECHANICALLY_CLOSED`. The frozen
workload/prompt material for the gate exists, but no prospectively
frozen exact Sonnet input/output token envelope and no committed exact
local tokenizer/counting mechanism exist for the fixed Sonnet route.
Historical Haiku consumption is observational, not a future bound, and
available tokenizer behavior is approximate, not an exact bound. ECB
FX is intentionally resolved fresh per run rather than frozen, so it
supplies no permanent constant either. This was a governed
pre-implementation stop, not a model or gate-quality failure.

**B. Replacement owner ruling.** The official Sonnet gate model
remains:

    claude-sonnet-5

The gate total accounted-consumption coordinator remains:

    5,000,000 micro-EUR

The official-gate per-call reservation is prospectively owner-fixed
at:

    1,000,000 micro-EUR

This number is an owner-fixed risk/start limit. It is NOT workload- or
token-derived, NOT an expected call cost, NOT a statistical fit, NOT
guaranteed sufficient, and NOT a guaranteed physical or provider
maximum.

Rationale: 1,000,000 / 5,000,000 = 20 percent, the same
reservation-to-total proportion as the existing Haiku control,
150,000 / 750,000 = 20 percent. This proportional consistency is the
rationale. Current canonical Sonnet pricing may be recorded as context
but does not mathematically derive this constant.

**C. Token/FX consequence.** P5-B introduces no token maxima,
tokenizer dependency or provider token-count call solely to
manufacture this derivation. No permanent USD/EUR value is pinned or
invented. At gate execution, fresh authoritative ECB USD/EUR is
resolved under existing policy, and the existing SDK allowance safety
margin converts the EUR reservation into the SDK-facing USD allowance.
FX-resolution failure remains fail-closed before model execution.

**D. Overshoot/one-shot consequence.** Existing honest overshoot
accounting is unchanged: the reservation is a start-control; known
post-call consumption above the reservation is accounted in full and
never clamped to manufacture compliance; exhausted or negative
remaining capacity starts no new call; exceeding 5,000,000 micro-EUR
total is an honest gate FAIL. The owner accepts that 1,000,000
micro-EUR may prove restrictive. If the one official gate fails under
this prospective limit, the honest FAIL and required miss-pattern
analysis are recorded; the reserve is not relaxed retrospectively to
manufacture a pass. Any later change requires a new prospective
owner-governed ruling.

**E. Haiku unchanged.** The ordinary live Haiku run cap remains
750,000 micro-EUR with its existing 150,000 micro-EUR default per-call
reserve. The P5-C WIF capability probe total remains 150,000
micro-EUR.

After this amendment, P5-B's fixed official-gate implementation
contract is: model `claude-sonnet-5`; total 5,000,000 micro-EUR;
per-call reserve 1,000,000 micro-EUR; fresh FX resolution plus the
existing safety-margin conversion; honest overshoot semantics. No
generic model selector is introduced.

### 8. Windows-to-Actions transition

P5-C rehearsal and the WIF capability probe must pass before the
standing Windows schedule is touched.

After P5-C passes and before the qualifying window:
- disable `SentinelDailyRun`;
- do not delete it;
- perform the disable at least one day before qualifying slot 1;
- verify no dual-scheduler overlap.

The Windows schedule and all historical Windows evidence remain
preserved as history.

Disabling the old scheduler does not itself create a qualifying Actions
run.

### 9. Evidence-backed operations artifacts and release

The following Phase-5 program artifacts are finalized only after the
measured Actions operating evidence they describe exists:
- `RUNBOOK.md`;
- `MONITORING.md`;
- `MODEL_CARD.md` final;
- `SLO.md`.

`SLO.md` keeps the exact governing header:

> Internal operator objectives for an n=1 system. No service,
> availability guarantee, or uptime commitment is offered to another
> party.

Its permitted objective classes remain:
- scheduled-run success rate;
- maximum consecutive failed or missed runs;
- finding-detection latency;
- cost per run and monthly cost ceiling;
- telemetry completeness.

Measured objectives are not written as achieved claims before the
five-slot evidence exists.

Phase 5 also produces its required 300-600 word public phase post from
the recorded evidence, not from memory.

The target Phase-5 release is an annotated tag:

    v0.7

The tag is created only after the final Phase-5 evidence/artifact
commit has exact-SHA CI success. The later Phase-5 closure record
verifies and cites the tag.

### 10. Ordered Phase-5 execution

The approved sequence is:

1. P5-A: adopt this contract. This starts Phase 5.
2. P5-B: implement the Actions scheduler, state-chain, qualification,
   cost/cadence and gate-runner machinery with model-free tests.
3. P5-C: exact-SHA CI plus NON-QUALIFYING Actions rehearsal and the one
   capped WIF capability probe.
4. P5-D: run the one official Sonnet gate and record GREEN or honest
   FAIL plus analysis.
5. P5-E: disable the Windows schedule, verify the migration boundary,
   and freeze the prospective five-slot qualification window.
6. P5-F: allow the five expected Actions schedule slots to occur without
   pushes to main.
7. P5-G: independently reconstruct and verify every slot and every
   state-chain link.
8. P5-H: finalize the Phase-5 operations documents, SLO, public post and
   public evidence from the measured operation; run publication controls
   and exact-SHA CI.
9. After P5-H exact-SHA CI success, create and verify annotated tag
   `v0.7`.
10. P5-I: record formal Phase-5 closure and its verified evidence.

Planning or adoption alone never counts as implementation evidence.

### 11. Phase-5 closure and program boundary

Phase 5 closes only when all BLUEPRINT P5 conditions and the mapped
Phase-5 artifact conditions are satisfied, including:
- official gate disposition recorded;
- five consecutive qualifying Actions-scheduled live runs;
- zero lost slots within that qualifying streak;
- each qualifying run represented in the ledger and CostRow telemetry;
- evidence-backed deploy / rollback / diagnose runbook;
- final monitoring, model-card and SLO truth;
- required Phase-5 public post;
- versioned release tag verified.

Phase-5 closure still does NOT authorize the production-ready claim.

The overall production-readiness program remains open through Phase 6
and its remaining closure artifacts.

## Rejected alternatives

**Physically migrate the Windows SQLite database into Actions.**
Rejected for Phase 5. It adds one-off migration machinery not required
to prove unattended Actions-era operation.

**Use GitHub cache as authoritative state.**
Rejected. Cache semantics are not an audit-grade continuity contract.

**Store a long-lived Anthropic secret in GitHub as the target design.**
Rejected by this ADR. WIF is the adopted target; a WIF failure returns
to owner decision rather than silently authorizing static credentials.

**Count manual runs, workflow_dispatch runs or reruns toward 5/5.**
Rejected. They cannot prove the scheduler fired.

**Find five historical successes retrospectively.**
Rejected. The qualification window is prospective.

**Expose Sonnet gate routing through an ordinary CLI flag.**
Rejected. The gate route is structurally limited to its dedicated
runner.

**Commit the final Actions SQLite database to the public repository.**
Rejected. Public evidence uses manifests and normal derived evidence
surfaces; raw operational SQLite stays outside Git.

## Reopening / stop conditions

Stop and obtain a new owner decision if:
- the WIF capability probe shows the pinned runtime cannot use the
  adopted auth path;
- current GitHub or Anthropic platform semantics invalidate a binding
  assumption in this ADR;
- Actions artifact retention is insufficient for the evidence window and
  reconstruction requirement;
- the owner-fixed Sonnet model/reserve contract cannot be implemented
  or executed without weakening the existing fail-closed FX, budget or
  honest-overshoot accounting rules;
- a source/workflow change is needed during an active five-slot window;
- the cost rule requires a cadence change during an active window.

No stop condition authorizes silent weakening of the evidence rule.

## Consequences

Phase 5 begins on adoption of this ADR but no implementation is proven
by adoption.

The approved implementation is intentionally narrow: GitHub Actions
scheduling and WIF, durable transport of the existing SQLite state,
prospective schedule evidence, cost/cadence enforcement, one official
Sonnet gate, and evidence-backed operations documentation.

The monitoring boundary remains read-only against Kristian's own public
repositories. No third-party monitoring, auto-remediation, multi-tenant
operation or production-ready claim is introduced.

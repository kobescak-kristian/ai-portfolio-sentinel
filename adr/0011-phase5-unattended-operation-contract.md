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
- the 120-minute qualification tolerance. *(Superseded by the
  daily-slot amendment at the end of this section: the record freezes
  timing rule `DAILY_SLOT` and five pre-window observations instead. The
  five pre-window observations are in turn withdrawn by the 2026-10-26
  amendment for every window frozen after it: those are schema-version-3
  records with no pre-window observations. Version-2 records, such as
  `p5w-40148573833`, keep them.)*

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

*The next three paragraphs (the 120-minute timing rule) are superseded
by the daily-slot amendment at the end of this section; they stay as
written for the record.*

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
state chain. *(The `LATE_NONQUALIFYING` part of this paragraph is
superseded by the daily-slot amendment below; every other breaker
stands.)*

A failed qualification window is not repaired by counting a manual run
or a rerun. A new five-slot window must be frozen prospectively.

Any change to workflow identity, cron, timezone, cadence or source SHA
invalidates the active window and requires a newly frozen window.

#### Amendment (2026-10-17): daily-slot qualification timing

**Decision.** Qualification timing is the observable daily interval:
for each closed interval exactly one valid invocation of the frozen
scheduled workflow must exist, and five closed pre-window intervals
must show this at the exact SHA to be frozen before any window is
frozen; the 120-minute tolerance is withdrawn.
*(The five-interval pre-freeze requirement in this decision is
superseded, for every window frozen after it, by the 2026-10-26
amendment below, which governs new schema-version-3 windows. It stays as
written for the record and still describes the version-2 window
`p5w-40148573833`. The daily-interval timing rule itself is unchanged.)*

**A. Trigger and evidence.** Stop condition "current GitHub or Anthropic
platform semantics invalidate a binding assumption in this ADR" (see
Reopening / stop conditions). The binding assumption was that a
scheduled run is normally created within 120 minutes of its cron
instant. REAL scheduler observations on this repository (model-free;
no synthetic fixtures involved). Source: the raw REST run listing for
`sentinel-schedule.yml`, read 2026-10-17: 55 runs, of which 1 `push`
run (2026-08-24) and 54 `schedule` runs in 54 consecutive daily
intervals 2026-08-25 to 2026-10-17 (the 2026-10-17 interval was still
open at reading), exactly one per interval, every `run_attempt` 1,
conclusion `success`, path `.github/workflows/sentinel-schedule.yml`,
branch `main`. Delay = `created_at` minus the interval's 06:37 UTC
start, whole minutes rounded down: since 2026-08-27, 52 of 52 above 120
(256 to 742); only 2026-08-25 (51) and 2026-08-26 (52) were inside 120.
The non-qualifying Stage-1 probe (removed, STATE entry 2026-10-16):
18 of 18 above 120 (155 to 562), no other UTC hour better. GitHub's
documentation states that `schedule` events can be delayed and, under
high load, queued jobs dropped, and gives no service level. Under the
original rule no window could pass: it measured GitHub queue latency,
not the unattended operation Phase 5 is meant to prove.

**B. Owner rulings (2026-10-16; T2 overnight gate passed 2026-10-17).**
- D1: the five pre-freeze observations run at exactly the SHA that
  will be frozen. Any push to `main` resets the count.
- D2: each pre-freeze invocation must reach terminal conclusion
  `success`. They prove scheduler cadence only. They are NOT P5-E
  qualifying Sentinel/model runs and need no live or model judgment
  (the existing `NO_ACTIVE_WINDOW` early exit is what they execute).
- D3: `QualificationWindowRecord` moves to `schema_version` 2 and
  carries `timing_rule = DAILY_SLOT` instead of
  `tolerance_minutes = 120`.
- D4: only invocations of the frozen scheduled workflow
  (`.github/workflows/sentinel-schedule.yml`) are relevant. A
  wrong-provenance invocation of it breaks its daily interval and is
  never silently filtered out. Runs of other workflows never touch an
  interval.

**C. The rule.** Cron stays `37 6 * * *`; event stays `schedule`. Daily
interval N is `[N, N+1)`: N = 06:37 UTC, N+1 = the next day's 06:37
UTC; the fifth window interval closes 24 hours after its own start.
The invocation observed in interval N is the one interval N owns, and
for each closed interval exactly one valid invocation must exist. Its
delay is recorded and never judged for punctuality.
`LATE_NONQUALIFYING` now means only "created at or after the next
interval start"; runtime ownership already bounds `created_at` by the
interval, so it is defensive and unreachable at runtime. An interval
breaks on: no invocation (`MISSING_LOST` at interval close), more than
one invocation of the workflow (`DUPLICATE_NONQUALIFYING`, including a
wrong-provenance one next to a valid one), `run_attempt` other than 1,
wrong event, workflow, ref or source SHA, a failed or nonterminal
run, and every other §5 breaker. Wording such as "the run for nominal
slot N" means "the invocation observed in interval N".

**D. Pre-freeze requirement.** The window-control freeze refuses
(`PREWINDOW_DAILY_SLOT_STREAK_UNMET`) unless the five most recent
closed daily intervals, judged by the earlier of GitHub server time
and the record's creation instant, each hold exactly one invocation of
the scheduled workflow with event `schedule`, `run_attempt` 1, the
frozen path, branch `main`, `head_sha` equal to the SHA being frozen,
status `completed` and conclusion `success`. All invocations in an
interval are counted. The five observations (interval start, run id,
`head_sha`, `created_at`, conclusion) are frozen inside the hashed
window record, so the proof is durable without a later commit.
Earliest freeze = the last push to `main` plus five closed intervals.
*(Item D is superseded, for every window frozen after it, by the
2026-10-26 amendment below, which governs new schema-version-3 windows;
it stays as written for the record.)*

**E. Claim consequence.** Phase 5 may claim one unattended scheduled
execution per daily interval (daily cadence). It never claims punctual
cron execution and never claims which hidden GitHub cron firing caused
a delayed run. Later MONITORING and SLO text must say the same.

**F. Unchanged.** Every other §5 predicate: exact workflow, ref and
source SHA; `run_attempt` 1; live, agent-judgment, terminal, within
budget, exactly one CostRow; the state chain and its one-to-one links;
§6 cost and cadence controls; no push to `main` while a frozen
qualification window is active; prospective freezing; reruns,
`workflow_dispatch` and history never count. ADR-0012 is untouched.

**G. Disclosed residual.** A static cron string records no nominal
date. An invocation created at or after N+1 cannot be told apart from
interval N+1's own invocation: interval N then shows MISSING and N+1
DUPLICATE, or a sustained shift of every run by 24 hours or more
could look clean. Even then each interval holds exactly one real
unattended scheduled execution at the frozen SHA, which is the only
property this rule claims. Observed maximum delay: 742 minutes.

**H. P5-G open gate.** The independent review and reconstruction
functions (`associate_runs_to_slots`, `independent_review_slots`) are
implemented and tested but not wired to any production entrypoint. A
P5-G reconstruction entrypoint that reads the REST run listing of the
scheduled workflow and the artifact chain is an explicit
implementation gate before Phase-5 closure.

**I. Alternatives rejected.** Keep 120 minutes: 52 of 52 recent runs
fail it, so it can never pass. Widen the tolerance to a larger number
of minutes: still a punctuality claim GitHub does not support, and any
number would be fitted to the observed history. Move the cron to
another hour: the Stage-1 probe found no better hour. Accept
pre-freeze runs on the first-parent history of the frozen SHA (pushes
allowed during the five days): rejected by D1, since a pre-freeze
observation must prove the exact source that will be frozen.

**J. Not a silent weakening.** This is a deliberate, owner-ruled,
prospective change recorded before any qualification window was ever
frozen; no window exists whose result it could rescue. The punctuality
evidence the old rule demanded is withdrawn openly, and the claim in
E shrinks with it.

**K. Reopening.** Reopen if GitHub documents a service level for
`schedule` events; if any closed interval of the scheduled workflow
shows no invocation or more than one in two of any ten consecutive
intervals; or if any observed delay reaches 1440 minutes.

#### Amendment (2026-10-26): replacement-window pre-freeze evidence

**Status.** Accepted by the owner 2026-10-28 after the T2 gates: two
independent second-model reviews from raw materials (2026-10-26, the
second on this zero-interval text: ACCEPT WITH CHANGES, all changes
applied) and the overnight gate. Prospective: it governs the
replacement window and every window frozen after it.

**Decision.** For any qualification window frozen after this amendment,
the five closed pre-window daily intervals of the 2026-10-17 amendment
item D are withdrawn and not replaced by any shorter interval requirement.
Instead, a real-GitHub, model-free artifact-publication rehearsal (T4) at
exactly the SHA to be frozen must pass and be independently verified
before the owner's freeze GO. The five prospective live qualification
slots and every acceptance predicate are unchanged.

**A. Trigger and evidence.** Stop condition "a source/workflow change is
needed during an active five-slot window" (Reopening / stop conditions).
REAL operational evidence, read 2026-10-25/26 from the GitHub REST API and
the job log; no synthetic fixture involved. Window `p5w-40148573833`
(window record `created_at_utc` 2026-10-25T00:55:38Z; control run
`40148573833`, created 2026-10-25T00:55:20Z; source
`91827b8e25de6aa3b4f33df7b9d9f425a17b8bf5`). Slot-1 invocation
`40191045830` (event `schedule`, attempt 1, created
2026-10-25T11:57:46Z): step `orchestrate` success; step `upload attempt
evidence` failure, "Input required and not supplied: path"; step `upload
successor or refusal bundle` skipped; zero artifacts. Cause (code): the
S06 refusal and S17 success paths stage only a bundle, so the evidence
output was empty, the unconditional evidence upload failed, and the bundle
upload (implicit `success()`) was skipped. The Sentinel terminal outcome,
qualification classification and cost of that invocation are NOT
established by any surviving evidence. The `orchestrate` step ran 8 min
16 s (log 11:57:58Z to 12:06:14Z) and printed nothing. The GENESIS state
(DAILY, EUR 2.14 accounted) rules out the S06 refusal. The duration is
therefore consistent with a paid live run (inference, not evidence), and
that run's CostRow was lost with the runner. The missing CostRow does not
make that spend zero or irrelevant: accounted trailing-30-day spend may
understate real spend by about one Haiku run (start limit 750,000
micro-EUR; honest overshoot possible). The readiness review for the
replacement window checks Console usage for 2026-10-25, discloses the
result, and evaluates §6 headroom conservatively, counting that run at
the higher of its 750,000 micro-EUR start limit and the Console figure.
Owner ruling 2026-10-26: the window
FAILED at slot 1 and cannot qualify; `sentinel-schedule.yml` was disabled
as containment (REST `actions/workflows/{id}` state `disabled_manually`,
`updated_at` 2026-10-26T01:21:47Z).

The five pre-window intervals before that window (2026-10-19 to
2026-10-23, all clean) exercised only the no-window early exit
(`NO_ACTIVE_WINDOW`), which stages evidence and no bundle. They could not
reach the failing path and did not detect it; they proved scheduler
cadence at the frozen SHA, their only stated purpose.

**B. The rule.**
1. *No pre-window interval requirement.* The freeze no longer reads the
   scheduled workflow's run listing. New window records are
   `QualificationWindowRecord` `schema_version` 3 with no
   `prewindow_observations`. Version-2 records (including
   `p5w-40148573833`) remain valid and are read unchanged.
2. *Publication rehearsal (T4).* `.github/workflows/sentinel-publication-rehearsal.yml`
   is dispatched once per case on `main` at exactly the SHA to be frozen,
   with `expected_source_sha` equal to it, `run_attempt` 1. A case PASSES
   only when the run's `head_sha` is that SHA and its step conclusions,
   run conclusion and artifacts are exactly:

   | Case | orchestrate | evidence upload | bundle upload | Run | Artifacts |
   |---|---|---|---|---|---|
   | `bundle_only` | success | skipped | success | success | 1 bundle |
   | `evidence_only` | success | success | skipped | success | 1 evidence |
   | `failure_with_evidence` | failure | success | skipped | failure | 1 evidence |
   | `nothing_staged` | success | failure | skipped | failure | 0 |
   | `bundle_missing` | success | skipped | failure | failure | 0 |

   A negative case passes only by failing at the step shown; any other
   failure (for example `SOURCE_SHA_MISMATCH` in `orchestrate`) is a FAIL.
   `nothing_staged` and `bundle_missing` are told apart only by step
   conclusions, so verification reads the jobs API. The rehearsal's two
   upload steps are identical to the scheduled workflow's (repository
   contract test); its artifact names match no Phase-5 discovery prefix;
   it is model-free and uses no credential. It is never a qualifying,
   pre-window or interval-relevant invocation (2026-10-17 ruling D4).
3. *Binding (reviewed, not hashed).* T4 evidence is NOT frozen in the
   window record. Before the owner's freeze GO, an independent second
   reader verifies every case in item 2 from the REST API (run, jobs and
   artifacts endpoints) and records each run ID, `head_sha`,
   `head_branch` (must be `main`), `event` (must be `workflow_dispatch`),
   `run_attempt` (must be 1), step conclusions, run conclusion and
   artifact names. The GitHub run records
   are the primary evidence. Because any commit to `main` would change the
   SHA to be frozen, that record lands in `STATE.md` in the first allowed
   commit window after the window ends (the same deferral already used for
   the provider-side owner actions and the freeze record). A push to
   `main` after T4 and before the freeze voids T4; it must be rerun at the
   new SHA.
4. *Re-enable order.* `sentinel-schedule.yml` stays disabled through the
   merge of the repair, the T4 rehearsal and its verification, and the
   readiness review. It is re-enabled only on an explicit owner
   authorization, and never while `main` is
   `91827b8e25de6aa3b4f33df7b9d9f425a17b8bf5`: at that SHA a slot-2..5
   invocation restores GENESIS (the missing slot 1 leaves the
   qualification frontier at GENESIS) and reaches the paid model path
   before the predecessor check fails. The replacement freeze is then
   completed and its GENESIS verified before slot 1 opens. If too little
   time remains before the next 06:37 UTC, slot 1 is the following day's
   06:37 UTC. If the freeze fails after the re-enable, the workflow is
   disabled again and the failure is reported to the owner before any
   further step. No scheduled invocation is required or awaited before
   the freeze (item 1).
5. *Replacement lineage.* The replacement window names
   `p5w-40148573833` in `supersedes_window_id` with its record hash.

Earliest freeze = the repair and this amendment on `main`, T4 passed and
verified at that SHA, and the remaining readiness review complete.

**C. Assurance lost (disclosed).** Relative to the 2026-10-17 rule:
- ANY pre-freeze evidence that the scheduler fires at the exact frozen
  SHA. The first scheduled invocation at that SHA may be slot 1 itself.
  If GitHub does not schedule the re-enabled workflow at that SHA, slot 1
  is `MISSING_LOST` and the window fails; this is discovered only after
  the freeze.
- Evidence that the re-enable of `sentinel-schedule.yml` (disabled
  2026-10-26) took effect before slot 1. The readiness review can read the
  workflow `state` (`active`), but not that a scheduled run will be
  created.
- Five daily intervals of evidence against an intermittent miss or
  duplicate before slot 1; the 2026-10-17 ruling D1 guarantee that the
  exact frozen source was seen scheduled; and five days for a
  time-dependent fault (runner image, platform change) to surface before
  slot 1.
- Pre-freeze evidence that the no-window early-exit path works at the new
  SHA in a real `schedule` event (T4 is `workflow_dispatch`).
Partly offsetting history (REAL, model-free): 54 consecutive daily
intervals 2026-08-25 to 2026-10-17 each held exactly one invocation, and
no interval was missed across the source changes to `72bbc8f`, `91df005`,
`de0e6f7` and `91827b8` (one invocation per interval 2026-10-13 to
2026-10-25 in the REST listing). That history includes no disable and
re-enable of the workflow. It is context, not a bound; a missed slot after
this amendment is still `MISSING_LOST` and fails the window.

**D. What neither the old nor the new rule proves.** The WIF exchange, the
live caged run and successor publication in a real `schedule` event at the
frozen SHA. The schedule federation rule accepts only `event_name =
schedule`, and §3 authorized exactly one capability probe (consumed in
P5-C). Slot 1 is the first proof; this amendment authorizes no probe.

**E. Unchanged.** Five prospective live scheduled qualification slots;
prospective freezing; exact source SHA, workflow identity, ref, cron and
timezone; `DAILY_SLOT` timing (2026-10-17 items C, E, G); `event ==
schedule`, `run_attempt == 1`; live, agent judgment, terminal, within
budget, exactly one CostRow per qualifying run; the one-to-one chain to an
intact successor bundle; §6 cost, cadence and headroom; no push to `main`
while a window is active; reruns, `workflow_dispatch`, manual and
historical runs never count; P5-G reconstruction before closure
(2026-10-17 item H). ADR-0012 untouched.

**F. Not a silent weakening.** Prospective; recorded after the failed
window was ruled failed and before any replacement freeze; no window
exists whose result it could rescue. It removes cadence-only evidence that
did not exercise the failing path and adds evidence that does; the
cadence assurance it removes is disclosed in C. The claim in 2026-10-17
item E is unchanged.

**G. Reopening.** Reopen if the first replacement window loses any slot to
`MISSING_LOST` or `DUPLICATE_NONQUALIFYING`. A T4 case whose outcome
differs from the table in B.2 is a failed readiness test, not a reopening
trigger: it blocks the freeze and is investigated under the existing test
procedure. It reopens this ADR only if the investigation shows that a
binding assumption of this contract is invalid.

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

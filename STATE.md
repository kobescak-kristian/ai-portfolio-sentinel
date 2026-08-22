# STATE — ai-portfolio-sentinel

Scheduled monitor over Kristian's own public portfolio repos — link
rot, number consistency, drift markers, required files, label presence
— findings proposed, never applied. LEARNING LANE: the deliverable is
the skill set (long-horizon agent ops); the monitor is the vehicle.
Eval gate runs on labeled synthetic fixtures; live runs on own repos;
no production claims.

**Classification:** EXPERIMENT · T0 (BLUEPRINT.md header; reclassification
pre-registered, BLUEPRINT §0).
**Visibility:** PUBLIC BY DESIGN from day one, permanent — ruling
2026-07-15, canonical record in the private operations OS (same
date). Public-while-dormant is intended; do not flag.
**Phase status:** Phase 3 is **CLOSED — 2026-08-22**, after
**four** recorded results, none of which replaces another and none of
which is relabeled or softened by a later one. (1) The designated Haiku
dev gate ran 2026-08-05 at source commit
`cf713649bc1aaf31f1494112921d7741493533b0` and recorded an honest
**FAIL** (BLUEPRINT §6 P3: "Dev gate leg green on fixtures" was not
met): pooled precision 47/56 = 0.8393 (< 0.90), pooled recall
47/60 = 0.7833 (< 0.85); per-class recall FAIL on `stale-STATE-marker`
(2/10) and `missing-synthetic-label` (5/10); the four deterministic
classes and the clean-false-flag rate all PASS; run IDs
`r-8f646359aef946178f2863acd75887c4`,
`r-06dc9ec88f6c4cdc9057dacec88a1a0a`. (2) The one permitted re-gate
under `adr/0005-phase3-gate-remediation.md` ran 2026-08-19 at source
commit `c12beee577b929f58cd6f91ff36d048fe955d73f` (run IDs
`r-80b91c34a10a4925a62d573a473cfb4d`,
`r-659b534850f945c2bb614f0065eaa6e7`) and recorded an honest **OVERALL
FAIL**. Every scoring threshold PASSED there — pooled precision 60/60,
pooled recall 60/60, all six classes 10/10, clean false flags 0/166 —
and `every_task_terminal` and `zero_lost_tasks` PASSED; the failure is
isolated to the two cross-run invariants, `idempotent_rerun` and
`dedup_correct_on_doubled_fixture_run`, both traced to one
cross-run finding-identity defect. (3) The one prospective validation
cycle under `adr/0007-prospective-validation-protocol.md` ran
2026-08-20 at source commit
`8c235af1ba254e9a238a797be558129bc2a82f99` (run IDs
`r-e8e27a1133754705ac76fd0f0842c101`,
`r-a2ed87b770014722a5f7bd583b9637db`) and recorded, after independent
ADR-0007 Step-F verification, a **VALID COMPLETED FAIL** — terminal
for the current Sentinel-v1 Phase-3 validation lineage. Every scoring
threshold PASSED there (pooled precision 58/58 = 1.0000, pooled
recall 58/60 = 0.9667, all six classes at or above the per-class
threshold, clean false flags 0/166) and the ADR-0006 identity defect
did NOT recur (60 persisted finding rows, 60 distinct fingerprints;
`dedup_correct_on_doubled_fixture_run` PASS); the FAIL is an
execution-validity failure — one run-1 Haiku call failed at the SDK
per-call budget ceiling, dead-lettering its scope fail-closed, so run
1 FAILED and `idempotent_rerun` failed on the resulting execution gap
(run 2 `findings_new = 2`, both in that exact scope), not on identity
instability. (4) The one prospective validation cycle under
`adr/0009-post-adr0008-phase3-validation-protocol.md` ran 2026-08-21/22
at validated source commit
`54f5ce3d0e066417104b47fecbc49d05b5303859` (run IDs
`r-cce0280d1a824ca6a12ac8faf42a30e1`,
`r-e68b8878b62b453eaf6cf5fe2544a6bb`, both COMPLETED with 80/80 tasks
terminal and all DONE) and, after independent Step-F verification,
recorded **PASS** — `C = 47 > 0` together with a complete,
independently verified PASS, which is the ADR-0009 §5 PASS
disposition. Every scoring threshold PASSED there (60 emitted, 60 true
positives, 0 false positives, 0 misses; pooled precision and recall
1.0000; all six classes 10/10; clean false flags 0/166); all four
frozen invariants PASSED, including both cross-run invariants; every
ADR-0009 execution-validity predicate PASSED, with cross-run logical
judgment-task coverage 23 == 23 and zero invalid logical histories;
persistent identity held (60 finding rows, 60 distinct fingerprints;
run 2 `findings_new = 0`, `findings_still_open = 60`,
`findings_resolved = 0`); and exactly one valid `BOUNDED_RECOVERY`
logical history occurred — one FAILED invocation whose mechanized
class reconstructed as `SDK_BUDGET_CEILING` followed by one COMPLETED
invocation for the same logical task, with zero `BREAKER_REFUSED`
outcomes persisted. Accounted consumption was 645,883 and 575,877
micro-EUR per run and 1,221,760 micro-EUR combined, inside the
declared 750,000 / 1,500,000 accounted-consumption acceptance
ceilings. Full figures, invariant predicates, cost evidence, evidence
hashes and the root-cause records for **all four** results:
`EVAL_RESULTS.md`.
Note that `artifacts/phase3_dev_gate.json` continues to carry the
2026-08-19 re-gate artifact — each prospective cycle wrote its
artifact to a fresh external evidence parent and the committed file
is untouched; both prospective cycles' raw evidence remains
external/local and is identified in `EVAL_RESULTS.md` by SHA-256. The original gate's
artifact is preserved
at commit `f9b7ea4e0762161a2519158ec817288308128584`, blob
`2b34e31e13ab8c6dd4e59fd9110e40159b48bcb4`. No fixture, label,
answer-key, scoring, threshold, model, prompt, lifecycle, fingerprint
or evidence-validation change was made after seeing any result. The
one permitted re-gate is **consumed** and no third gate run is
authorized under ADR 0005; the one prospective validation cycle
separately authorized by `adr/0007-prospective-validation-protocol.md`
via BLUEPRINT §11(i) (ADOPTED 2026-08-20) is now also **consumed**
(executed 2026-08-20; VALID COMPLETED FAIL — terminal for this
lineage; all three historical FAILs stand unrelabeled; no further
validation cycle is authorized by ADR-0007). The identity
remediation design decision has since been taken as a separate
owner-governed ADR: `adr/0006-judgment-finding-identity.md`, ADOPTED
2026-08-20, adopting Option C — persistent judgment finding identity is
separated from descriptive model-selected evidence, with
`normalized_content = "reason=<reason_code>"` for the two judgment
classes built through `agents/checker/evidence.py`. That correction is
now **IMPLEMENTED** (2026-08-20), landed together with its required
model-free T1–T8 regression suite; see the 2026-08-20 (ADR-0006
implementation) change-log entry below for the exact rule, evidence and
non-authorizations. **The implementation session made no Sentinel
checker-agent model call and ran no gate, re-gate, eval or scorer**;
its only evidence is model-free regression evidence. The one permitted
re-gate remains **consumed**, and passing model-free tests was never
Phase-3 closure — closure came only from the independently verified
ADR-0009 validated PASS recorded as result (4) above. The subsequent validation
path decided by `adr/0007-prospective-validation-protocol.md`
(ADOPTED 2026-08-20) has now run to completion: its one prospective
cycle executed 2026-08-20 and reached **VALID COMPLETED FAIL**
(terminal for that lineage; see result (3) above and
`EVAL_RESULTS.md`). The further validation path decided by
`adr/0009-post-adr0008-phase3-validation-protocol.md` has since run to
completion as well: its full A–F bound sequence is complete, its one
authorized cycle executed 2026-08-21/22, and independent Step-F
verification recorded **PASS** (result (4) above). That cycle is
**consumed and complete**. **Phase 3 is therefore CLOSED (2026-08-22).
Phase 4 is IN PROGRESS — 2026-08-22**: `adr/0010-phase4-loop-safety-controls.md`
is **ADOPTED**, freezing the bounded-loop control contract (failure
unit and a 3-consecutive-failure streak breaker, a 750,000 micro-EUR
Phase-4 loop ceiling that never raises the unchanged EUR 0.75 per-run
cap, termination precedence, the crash-safe `planned_run_id`
invariant, a four-part no-new-channel failure-alert contract and a
model-free technical gate). **Phase-4 implementation has NOT yet
landed, the technical Phase-4 gate has NOT run, and the four ADR-0003
P4 closure artifacts (`TEST_MATRIX.md`, `INCIDENT_RESPONSE.md`,
`MONITORING.md` draft, `RUNBOOK.md` draft) have NOT yet landed. Phase
4 remains OPEN** — passing the technical gate alone does not close it
(ADR-0010 §8). The governing task item remains **OPEN** and
is tracked in the private operations OS. `SentinelDailyRun` remains
stub-mode, unedited. The overall production-readiness program remains
**OPEN**: Phases 4–6 and the remaining program gates are open, no
production or production-ready claim is permitted yet, and the status
language is unchanged — in development toward production-ready.
Implementation itself (the caged checker
agent, run-scoped EUR budget, main-ledger audit, `--judgment-mode
stub|agent` activation, the cage-suite tests, `THREAT_MODEL.md`,
`MODEL_CARD.md`) landed complete and CI-green at commit
`cf71364` before this gate ran; see the 2026-08-05 (Phase 3
implementation) change-log entry for that evidence and the 2026-08-05
(Phase 3 gate FAIL) entry below for the gate result itself. Phase 2 CLOSED
2026-08-05 — deterministic control plane end to end, scheduler-gate
proven; see that change-log entry for full evidence. Phase 1 CLOSED
2026-08-04 — eval gate frozen by this commit (fixture corpus, answer
key, clean inventory, scoring contract, quantized thresholds and
review evidence committed; see the 2026-08-04 change-log entry and
evals/). Phase 0 CLOSED 2026-08-03 — evidence: foundation and canary
commits public on main; repository publish gate OVERALL PASS from the
closing HEAD; CI green on push (Actions run 30852395018, conclusion
success; 36/36 tests, ubuntu-latest, Python 3.12). Next action in this
repo: the separate `q77-p4-runner-a` Phase-4 implementation session;
see the Plan field below.
**Status:** in development toward production-ready (program opened by
owner ruling 2026-08-03); claim levels per the CLAUDE.md ladder as
amended 2026-08-03.
**License:** RESOLVED and LANDED — Apache-2.0 (portfolio default,
owner ruling 2026-08-03); LICENSE file committed 2026-08-03 at
2283b4f via the repo-exclusive rollout step. No remaining
license-related program-closure dependency.
**Plan:** Phases 0–6 per BLUEPRINT §6. Remediation ADR ADOPTED
2026-08-19 (`adr/0005-phase3-gate-remediation.md`) and its
implementation LANDED 2026-08-19 in one dedicated remediation commit
containing remediation only (dispatch q77-p3-remediation-implement-a;
see that change-log entry). That commit's own SHA is not self-cited
here — it is recorded, with its exact CI run, in the private
operations OS's Q-77 annotation. That commit's exact-SHA CI run was
green, and the one separately authorized re-gate has since been run at
that source: OVERALL FAIL, recorded in full in `EVAL_RESULTS.md` and in
the 2026-08-20 change-log entry below. **Exactly zero re-gates remain**
— the single permitted re-gate is consumed, and no third gate run is
authorized under the current BLUEPRINT or ADR 0005. Identity
remediation ADR ADOPTED 2026-08-20
(`adr/0006-judgment-finding-identity.md`, Option C) and **IMPLEMENTED
2026-08-20** in one dedicated commit containing the identity
correction, its model-free T1–T8 regression suite and the documentation
truth repair those changes required. Prospective-validation governance
ADOPTED 2026-08-20 (`adr/0007-prospective-validation-protocol.md`,
BLUEPRINT §11(i)): exactly one prospective validation cycle was
authorized under that ADR's protocol, and its full A–F sequence is
now complete. Stage 1 (step A) landed 2026-08-20 with exact-SHA CI
green (step A2); Stage 2 (step B) landed 2026-08-20 — the runner
self-validates the §2 execution-validity predicates and the §5
preflight — with exact-SHA CI and freeze green on that commit (step
C); the external pin (step D) was recorded in the private operations
OS's Q-77 annotation; the one cycle executed 2026-08-20 under the §5
preflight (step E); and independent verification (step F) recorded
the disposition: **VALID COMPLETED FAIL**, terminal for the current
Sentinel-v1 Phase-3 validation lineage — the cycle is consumed, no
further validation cycle is authorized by ADR-0007, Phase 4 is not
permitted under this lineage, and Phase 3 remains OPEN (full record:
`EVAL_RESULTS.md`, prospective section, and the 2026-08-20 Step-F
recording change-log entry below). The committed fixed-path
`artifacts/phase3_dev_gate.json` continues to carry the 2026-08-19
re-gate artifact; the prospective raw evidence remains
external/local, identified in `EVAL_RESULTS.md` by SHA-256. Next
action: any subsequent validation path required a new owner-governed
decision; that decision has since been taken as
`adr/0009-post-adr0008-phase3-validation-protocol.md` (below).
Runtime-reliability
ADR ADOPTED 2026-08-20
(`adr/0008-judgment-call-execution-reliability.md`): bounded
failed-call observability, a mechanized failure taxonomy, exactly one
same-run re-execution for the single captured SDK budget-ceiling
class, and honest overshoot accounting. **IMPLEMENTED 2026-08-21**,
landed together with the complete model-free R1–R24 proof package the
ADR requires (see the 2026-08-21 implementation change-log entry
below). It authorizes no validation cycle, and none has been run: its
only evidence is model-free regression evidence; the subsequent
independent-review remediation added and passed R25–R26 and was
independently reread PASS (2026-08-21), which are later
review-remediation proofs and not a restatement of ADR-0008's own
R1–R24 requirement. Post-ADR-0008 validation governance ADOPTED
2026-08-21 (`adr/0009-post-adr0008-phase3-validation-protocol.md`,
BLUEPRINT §11(j)): exactly ONE new prospective Phase-3 validation
cycle is authorized for the post-ADR-0008 implementation, under a
prospective logical-history execution-validity rule whose recovery
authorization is the persisted mechanized failure classification
`SDK_BUDGET_CEILING` — never the SDK subtype alone and never exception
prose — with logical judgment-task cross-run coverage, declared
accounted-consumption acceptance ceilings of 750,000 micro-EUR per run
and 1,500,000 micro-EUR across two runs, four dispositions with no
fifth, no retry after a consumed non-PASS, and PARK as the default
posture thereafter. **ADOPTED, IMPLEMENTED and now VALIDATED.** Its
independent read returned PASS, and its full A–F bound sequence has
run: steps A and A2 (adoption plus exact-SHA CI); steps B and C (the
Stage-2 runner, which self-validates the ADR-0009 logical-history
execution-validity contract, proven model-free — see the 2026-08-21
Stage-2 change-log entry below — with exact-SHA CI and freeze green);
step D (the external Stage-2 validation SHA pinned before execution,
outside this repository, in the private operations OS annotation for
this work item); step E (the one authorized cycle executed
2026-08-21/22 under the §5 preflight at validated source commit
`54f5ce3d0e066417104b47fecbc49d05b5303859`); and step F (independent
verification of the runner artifact against the raw persisted
evidence). The independent Step-F disposition is **PASS**, and the one
ADR-0009 cycle is **consumed and complete**. Nothing in the frozen
quality contract moved to reach it: the scoring corpus, answer key,
thresholds, model, prompts, caps, retry taxonomy, identity rule and
`max_regates: 1` are all unchanged, and all three historical FAILs
stand unrelabeled. **Phase 3 is CLOSED (2026-08-22).** Phase-4
loop-safety governance ADOPTED 2026-08-22
(`adr/0010-phase4-loop-safety-controls.md`): the bounded-loop control
contract is frozen prospectively, before implementation — failure unit
defined at the run level with a 3-consecutive-failure streak breaker
scoped to one `loop_id`; a real pre-start Phase-4 loop ceiling of
750,000 micro-EUR that neither replaces nor raises the unchanged EUR
0.75 per-run cap and that no flag, config value or environment
variable may raise; a frozen termination precedence (accounted
overshoot, then the failure breaker, then normal iteration-cap
completion, then pre-start cost refusal) with the deliberate strict-`>`
versus remaining-`<= 0` asymmetry; a crash-safe durable
`planned_run_id` iteration-intent invariant; a four-part alert
contract that introduces no new notification channel; a closed
stop-reason vocabulary; and a MODEL-FREE technical gate frozen before
implementation. **Phase 4 is IN PROGRESS — 2026-08-22.
Implementation has NOT yet landed, the technical Phase-4 gate has NOT
run, and the four ADR-0003 P4 closure artifacts have NOT yet landed;
Phase 4 remains OPEN**, and per ADR-0010 §8 a technical-gate PASS
alone does not close it. Next action: the separate `q77-p4-runner-a`
implementation session.
Activating the
standing scheduled task in agent mode remains a separate, later
decision either way — SentinelDailyRun stays stub-mode, unedited.
**Open decisions:** rename window CLOSED 2026-08-03 (expired by date;
name kept). Internal path reference removed from the Visibility line
2026-08-03 (this repo's own public-live rule; content unchanged
otherwise). Fixture final counts → quantization integers: RESOLVED
2026-08-04 at the Phase 1 freeze (integers stated in
evals/eval_config.yaml and CI-enforced). Canonical validator recognizes both
`adr/` and `decisions/` as valid decision-record directories. This
repository now uses only `adr/` as a representation/reader-clarity
normalization; no validator change is required by this cleanup.

Decision 0001 — Separate track (adopted 2026-07-13). Status: ADOPTED
and IN FORCE. Ruling provenance: main governance chat 2026-07-12;
marketing-repo precedent applies. Substance, unchanged:
ai-portfolio-sentinel runs as its own track; its task queue is this
STATE.md; the private operations OS's task queue is READ-ONLY from this
repo's sessions — it may be consulted for cross-dependencies but is
never written from them; the private operations OS carries only the
pointer/annotation required by its own governance workflow.
Reopening condition (pre-registered): if this lane blocks or delays an
operations-OS task queue item TWICE, track status is re-decided.
Occurrences are counted here, dated, append-only. **Occurrence count:
none.**
Artifact note: this ruling previously lived as a standalone decision
record at the former path `decisions/0001-separate-track.md`
(pre-consolidation blob `f8a6696a678e30baca940e605d77dc2cb82aecc7`,
recoverable through Git history). That file was consolidated into
STATE.md on 2026-08-20 solely to comply with this repository's
then-governing ARTIFACT_STANDARD decision-record cap of five
non-template records; after the v2.6 cap removal it was restored
byte-exact at its original path the same day, and on 2026-08-20 it was
moved to `adr/0001-separate-track.md`, where the standalone record now
lives. Representation changes only: the decision is neither revoked
nor superseded.

Scope decision 2026-07-14 (Kristian): sentinel is built to
production engineering standard as an explicit learning objective —
CI on every push, unit+integration+FI test depth with coverage
stated, structured logging + failure alerting, pinned deps +
versioned releases, ops runbook (deploy/rollback/diagnose). Claim
vocabulary unchanged per CLAUDE.md ladder: "production engineering
standard, operated at n=1" — never "production-ready" unqualified.
Phase gates absorb these as exit criteria, not a separate phase.
Resolution 2026-08-04: absorbed into BLUEPRINT v1.2 and the
Phase 0–1 implementation record. CI-on-push, test-depth,
structured-logging, dependency-pinning and production-readiness
exit criteria now live in the amended phase gates. The earlier
"next blueprint touch" and "blueprint is one amendment behind"
wording is superseded.

Scope clarification 2026-07-14 (Kristian): the portfolio website
(the public site repo) is an explicit monitored surface, two
checks: (1) its URLs participate in link-rot checking wherever
other surfaces point at them; (2) claims parity — numbers and
gate statements on site cards must match the committed files of
the repos they cite (the manual cross-check the site publish gate
performs, automated weekly). Boundary: sentinel checks
repo-content parity only; served-site verification (Pages build
bound to commit, cache) remains the publish gate's job. No
availability or uptime monitoring — that would exceed the claims
ladder. Resolution 2026-08-04: BLUEPRINT v1.2 §11(h) records site
gate-statement parity as in scope but deferred and ungated. It is
not implemented and no live run reports site-parity findings until
an ADR adds class 7, paired fixtures, answer-key rows, scoring
semantics and restated pooled integers before or with the check
code. The P6 backstop remains binding. The earlier "same next-touch
amendment" wording is superseded.

Roadmap decision 2026-07-14 (Kristian): a REMEDIATION agent is
pre-registered as a separate future automation — consumes sentinel
findings, drafts mechanical parity fixes ONLY (claim/number drift,
link targets) as pull requests; a human merges every change,
always; no push access to main; judgment-class findings stay
flag-only. Sentinel itself remains read-only permanently — the
fixer is a second bounded system, never a sentinel capability.
Trigger to build: a real findings history exists (several live
runs) AND the fix workload proves annoying by experience, not by
anticipation. Claims line when built: "proposes fixes; a human
merges every change."
**Change log:**
- 2026-08-05 — Phase 3 designated Haiku dev gate: honest FAIL (dispatch
  q77-p3-a). Source commit `cf713649bc1aaf31f1494112921d7741493533b0`.
  Model `claude-haiku-4-5-20251001`, auth mode
  `operator-subscription-oauth-assumed` (subscription OAuth, not
  API-key billing). Run 1 (primary/scoring) `r-8f646359aef946178f2863acd75887c4`,
  run 2 (doubled-fixture) `r-06dc9ec88f6c4cdc9057dacec88a1a0a`. Results:
  56 emitted, 47 true positives, 9 false positives, 13 misses against
  60 frozen positives; pooled precision 0.8393 (< 0.90 FAIL); pooled
  recall 0.7833 (< 0.85 FAIL); per-class recall PASS on broken-link,
  missing-required-file, number-mismatch, readme-structure (10/10
  each), FAIL on missing-synthetic-label (5/10) and stale-STATE-marker
  (2/10); clean false-flag 1/166 (PASS, ≤16 allowed). Invariants:
  every_task_terminal, zero_lost_tasks PASS; idempotent_rerun and
  dedup_correct_on_doubled_fixture_run PASS but **qualified** — run 1
  consumed the entire shared 500,000-micro-EUR run budget, so run 2
  made zero real model calls (`cost_row2_micros: 0`) and its judgment
  tasks correctly dead-lettered on exhaustion rather than silently
  passing; this proves budget-exhaustion safety, not real-agent-rerun
  idempotency — full qualification in `EVAL_RESULTS.md`. Total charged
  500,000 micro-EUR (= the 500,000 cap, PASS). Full record:
  `EVAL_RESULTS.md` (ORIGINAL DESIGNATED GATE section) and this gate's
  own artifact, `artifacts/phase3_dev_gate.json` as committed at
  `f9b7ea4e0762161a2519158ec817288308128584`, blob
  `2b34e31e13ab8c6dd4e59fd9110e40159b48bcb4` — that working-tree path
  now carries the 2026-08-19 re-gate artifact. Per the binding
  gate discipline: no fixture, label, answer-key, scoring, threshold,
  model, or prompt change was made after seeing this result; no rerun
  in this session. **Phase 3 remains OPEN.** Q-77 remains open.
  `SentinelDailyRun` unchanged, stub-mode. Subsequent remediation
  requires a separately approved ADR — not designed in this record.
- 2026-08-05 — Phase 3 gate diagnosis (dispatch q77-p3-diagnose-a):
  post-gate diagnosis completed, reconstructing the honest FAIL above
  from persisted evidence only (`var/phase3_gate/gate.sqlite3`,
  `gate.jsonl`, `cost_ledger.jsonl`, `evals/answer_key.jsonl`,
  `evals/clean_surfaces.jsonl`). Database SHA-256
  `6a66b70b2131343b3e5f65a035ff1ea0607fa278a32175a47bd9b1b6a07ff25f`
  (identical before and after querying). Diagnosis commit pending until
  committed — not embedded here, a commit cannot truthfully cite its own
  hash. Run 1 judgment-class calls: 15 COMPLETED, 2 FAILED, 7 EXHAUSTED
  (of 24 expected). Run 2: all 24 judgment tasks EXHAUSTED, zero real
  model calls occurred. Of the 13 total misses: 2 attributable with
  certainty to run-level shared-budget exhaustion (inj-059, inj-060, no
  model call made); 2 more from a separate, distinct per-call
  maximum-budget guard that failed an in-progress call (inj-004,
  inj-005) — both budget mechanisms proven by exact persisted rejection
  text, not inferred, and not the same pool; 9 misses occurred after a
  completed call (not budget-related). False positives: 9 (3
  missing-synthetic-label, 6 stale-STATE-marker); only 1 of the 9
  matches a registered frozen clean unit (clean-162, the sole
  `clean_flagged: 1/166` — the other 8 are off-manifest and their
  substantive correctness is not determined by this record). Whether
  host-side validation ever rejected a substantively correct model
  answer: NOT DETERMINABLE FROM RETAINED METADATA (no schema persists
  raw model text). No model call occurred, no gate rerun occurred, no
  implementation or gate contract changed in this diagnostic session.
  Full record: `PHASE3_GATE_DIAGNOSIS.md`,
  `artifacts/phase3_gate_diagnosis.json`. **Phase 3 remains OPEN. Q-77
  remains OPEN.** Remediation ADR remains the next decision point and
  was not designed in this record.
- 2026-08-05 — Phase 3 gate diagnosis CORRECTION (dispatch
  q77-p3-diagnose-fix-a): the diagnosis above incorrectly described two
  separate budget pools. Corrected architecture, confirmed against
  `agents/checker/budget.py` and `agents/checker/harness.py`: there is
  ONE shared run-budget architecture (`RunBudgetCoordinator` owns the
  entire run's EUR budget; every call reserves a bounded slice of that
  same shared pool, and the SDK-facing `max_budget_usd` ceiling is
  derived from that call's own reservation), expressed through two
  distinct failure modes, not two pools. The four budget-related misses
  are unchanged: inj-004/inj-005 — call failed at the reservation-derived
  SDK ceiling; inj-059/inj-060 — no model call after the shared budget
  reached zero. The earlier claim of "2 rejected tool emissions" was
  unsupported and is corrected: two failed calls (agent_calls id=1,
  id=17) each had one tool attempt and no persisted finding, but
  `accepted=False` on an SDK-exception path is a call-level finalization
  value, not a per-attempt outcome record — per-attempt acceptance or
  rejection for those two calls is `UNAVAILABLE_FROM_PERSISTED_EVIDENCE`,
  and so is the rejected-tool-emissions total. Host-validation rejection
  of a substantively correct answer remains `NOT DETERMINABLE FROM
  RETAINED METADATA`. The stale-STATE-marker wrong-anchor pattern is
  corrected to apply only to synthetic-01, synthetic-02, and
  synthetic-03 — synthetic-05 matched both of its frozen positives and
  was never part of that pattern. No gate metric, disposition, TP, FP,
  miss, or cost count changed (TP 47, FP 9, misses 13, judgment matched
  7/20, missing-synthetic-label 5/10, stale-STATE-marker 2/10, clean
  flagged 1/166, run-1 15 COMPLETED/2 FAILED/7 EXHAUSTED, run-1 real
  calls 17, run-2 24 EXHAUSTED/zero real calls — all unchanged). No
  model call or gate rerun occurred; no implementation, prompt, scorer,
  fixture, threshold, model, or gate contract changed. The
  diagnostic-recording substep is now corrected and complete. **Phase 3
  remains OPEN. Q-77 remains OPEN.** The separately approved remediation
  ADR remains the next decision point and is not designed here.
- 2026-08-05 — Phase 3 IMPLEMENTATION (dispatch q77-p3-a; commit SHA
  recorded in the kristian-os Q-77 annotation, not embedded here — a
  commit cannot truthfully cite its own hash). Landed: `agents/checker/`
  (config, auth fail-closed override check, ECB FX resolution, run-
  scoped EUR-budget coordinator, host-side evidence validation, the
  one `emit_finding` in-process MCP tool, SDK harness implementing
  `JudgmentStub` as `CagedCheckerStub`) with zero changes to the
  existing `checks/judgment/stubs.py` Protocol or its two adapters;
  `agent_calls` main-ledger audit table (additive, idempotent DDL,
  no second database); `--judgment-mode stub|agent` (default `stub`,
  unchanged Phase-2 behavior; `SentinelDailyRun`'s resolved command
  carries no such flag and is unedited); real agent-mode CostRow
  aggregation checked from ledger state so crash-recovery reconciliation
  is unaffected; `tests/test_bounds.py` (49 tests: cage construction,
  circuit breaker, shared run budget, FX/auth fail-closed paths,
  durable audit, evidence-fabrication/prompt-injection resistance,
  fingerprint stability, containment, credential-leak canary);
  `test_per_run_cost_cap_halts_checker` activated for real (the
  self-guard test now expects exactly the three remaining Phase-4
  skips); `scripts/run_phase3_dev_gate.py` (reads `fixtures/`/`evals/`
  read-only; validated end-to-end in stub mode: all four deterministic
  classes score 40/40 true positives, 0 false positives, 0 clean
  false-flags, all invariants green — confirming the scorer and
  pipeline wiring before any real model call); `THREAT_MODEL.md`;
  `MODEL_CARD.md` (draft); `DATA_CONTRACT.md`/`DATA_RETENTION_POLICY.md`
  updated for the `agent_calls` addition. Test suite: 532 passed, 3
  skipped (all Phase 4), coverage measured over `agents` too. A real
  environmental finding surfaced and confirmed correct during this
  build: the auth-override fail-closed check correctly refused to
  proceed inside the build session's own tool-execution environment
  (which carries `ANTHROPIC_BASE_URL` for its own unrelated routing) —
  flagged to the owner before the designated gate is attempted from a
  clean environment. **Phase 3 does not close on this commit** — see
  Phase status above; the designated Haiku dev gate is next.
- 2026-07-13 — repo created; BLUEPRINT v1.0, CLAUDE.md, decisions/0001,
  STATE.md committed in scaffold commit (Fable session).
- 2026-07-13 — publish-gate FAIL on internal references (caught
  pre-push by the gate); redacted per spec; labeled cross-reference map
  preserved privately.
- 2026-07-13 — first push to origin/main used --no-verify (documented
  exception, marketing-repo precedent): the freshness ancestor check is
  unsatisfiable on a brand-new remote with no origin/main yet. One-time
  only; hook file untouched and fully enforcing from the second push
  onward. Not precedent for any push where origin/main exists.
- 2026-08-04 — Phase 1 scope ruling (ADR 0004): v1 check-class set
  frozen at six — readme-structure added, gating the README structural
  check the pipeline already claimed; site gate-statement parity
  deferred and ungated per BLUEPRINT §11(h), with a P6 backstop;
  blind-review wording corrected in BLUEPRINT §5 / SPEC §4 (the review
  certifies defect presence and location, not blind class discovery).
- 2026-08-04 — Phase 1 CLOSED: eval gate frozen by this commit.
  Corpus: 8 synthetic snapshots (owner feasibility ruling; two
  all-clean), 60 injected positives — 10 per class across the six
  classes — and 166 exhaustively enumerated clean units. Quantized
  integers frozen: pooled recall 9 misses of 60; per-class recall 2
  of 10; precision ≥ 0.90 over actual emitted findings (reference 6
  false positives at 60); clean false-flag 16 of 166; invariants at
  100%. Answer-key review (SPEC §4, cost-adapted): sample pass of 24
  positives + 6 clean controls, reviewer GPT-5.6 Thinking, zero
  target-injection disagreements; all 19 additional reviewer
  observations dispositioned as genuine co-occurring answer-key
  rows; zero over-flags; no corpus-integrity event; full pass not
  triggered. D6 reconciliation restored eight undercounted clean
  units (clean-159..166, provenance-marked, scorable but
  control-ineligible) — counting defect record in
  evals/adjudication.md. Owner approved the corrected package and
  ratified the recorded deviations, 2026-08-04.
- 2026-08-05 — Phase 2 CLOSED: deterministic control plane end to end.
  Implementation commit `bfa56d680c6a0980cef8b9494b3a307defd4318e`
  (q77-p2-c; CI green, ubuntu-latest/Python 3.12). Closure commit is
  this one — its own exact SHA and CI run are recorded in the
  kristian-os Q-77 annotation (`q77-p2-record-a`), not embedded here (a
  commit cannot truthfully cite its own hash). Landed: live inventory
  (unauthenticated GitHub API, zero hand-maintained list), one
  CheckTask per surface × check class, four real deterministic
  checkers (broken-link, number-mismatch, missing-required-file,
  readme-structure), two Phase-3-stubbed judgment classes
  (stale-STATE-marker, missing-synthetic-label), fingerprint dedup +
  OPEN/RESOLVED finding lifecycle on the frozen SQLite ledger,
  crash-consistent finalization, FINDINGS.md writer, structured JSONL
  logging, zero-cost CostRow telemetry. Live required-file/
  readme-structure applicability is derived per repository, per run,
  from that repository's own public `.githooks/pre-push` and gate-file
  content — never a static list or private record. Test evidence: 482
  tests passing, exactly 4 skips (Phase 3/4 stubs only), 89.9% line
  coverage (`contracts, telemetry, sentinel, checks`). Phase-1 freeze
  guard PASS at close (fixtures/evals byte-identical to freeze commit
  `4d46c1d4fc3c4f485a83f44fa54afa6b04b1f541`). Scheduler-gate evidence
  (LIVE — real data, own public repos): one manual measurement run
  (`r-423e958baa004acfa0a5c6a8511efdb9`, 2026-08-05T08:51:45Z–08:52:53Z,
  190/190 tasks terminal, 4 findings) fixed 20-minute burst spacing
  (measured ~11 api.github.com calls/run, well under the 25-request
  threshold). Two consecutive Windows-Task-Scheduler-triggered runs
  followed with zero manual invocation between them:
  `r-91ec8071505a4ba7905fe6f9ef4c53f4` (started 2026-08-05T09:01:39Z)
  and `r-5ac95d4bc6fd4c55a7f739547090098f` (started
  2026-08-05T09:21:40Z) — both COMPLETED, 190/190 tasks terminal, all
  DONE, `LastTaskResult=0` on both, dedup/lifecycle exact on the second
  (0 new, 4 still-open, 0 resolved). Every CostRow across all three
  runs shows `model="none-deterministic"`, 0 input tokens, 0 output
  tokens, 0 micro-euros. Scheduler disposition: temporary
  `SentinelGateBurst` task removed and verified absent; standing
  `SentinelDailyRun` task installed, daily at 07:15 local, Interactive
  logon, RunLevel Limited, no password or credential stored.
  `DATA_CONTRACT.md`/`DATA_RETENTION_POLICY.md` landed at Commit A.
  Two real PowerShell-5.1 runtime defects were found and fixed while
  exercising the scheduler tooling live (non-ASCII string-literal
  encoding; `$PSScriptRoot` unreliable inside a `param()` default) —
  disclosed in the Phase 2 gate post. **No production or
  production-ready claim is made or implied — status stays "in
  development toward production-ready." Q-77 remains OPEN through the
  remaining production-readiness phases.** Next action: Phase 3 (caged
  checker agent), named only, not designed here.
- 2026-08-19 — Phase 3 remediation ADR ADOPTED (dispatch
  q77-p3-remediation-adr-adopt-a). `adr/0005-phase3-gate-remediation.md`
  created with Status: ADOPTED; owner approved 2026-08-19 on the
  evidence basis of the read-only design sessions
  (q77-p3-remediation-adr-design-a, q77-p3-remediation-adr-finalize-a).
  Implementation is authorized but NOT performed in this session — no
  checker, prompt, config, harness, gate-runner, test, or BLUEPRINT
  cost-value change landed here. Final cost rulings adopted: Haiku
  iteration/dev/live per-run cap EUR 0.75 (was 0.50); per-call
  reservation ceiling 150,000 micro-EUR (was 100,000); SDK allowance
  safety margin 0.70 unchanged; maximum two-run re-gate session spend
  EUR 1.50 (two independent per-run coordinators, one per designated
  run ID); monthly EUR 50 lane ceiling and EUR 5 Sonnet official-gate
  cap unchanged. Approved remediation categories: ordered
  scan-identify-emit-stop prompt contract (with stale-STATE dated-entry
  evidence ordering and the missing-synthetic-label
  provenance/applicability rule), deterministic no-model-call path for
  `request.text is None`, and separate `RunBudgetCoordinator`
  instances for gate run 1 and run 2. Model
  `claude-haiku-4-5-20251001` and all frozen eval/scoring surfaces
  (fixtures, labels, answer key, clean manifest, scorer, thresholds,
  `max_regates`) unchanged. Exactly one re-gate remains; a FAIL at the
  amended settings does not authorize another adjustment or a third
  gate run under the current BLUEPRINT. Per-tool-attempt evidence
  persistence remains deferred per the ADR. A separate routine
  operational-record commit preceding this adoption recorded ten
  scheduled live runs (2026-08-06..17, all deterministic-only, zero
  cost) — it contains no remediation and does not count as the
  remediation implementation commit. No model call and no gate rerun
  occurred in the adoption session. **Phase 3 remains OPEN. Q-77
  remains OPEN.** Next action: a separate remediation implementation
  dispatch.
- 2026-08-19 — Phase 3 remediation IMPLEMENTED (dispatch
  q77-p3-remediation-implement-a). `adr/0005-phase3-gate-remediation.md`
  executed exactly as adopted, in one dedicated remediation
  implementation commit containing remediation only. Categories landed:
  **(1) Budget bounds.** `RUN_BUDGET_EUR_MICROS` 500,000 → **750,000**
  micro-EUR (EUR 0.75, the general Haiku iteration/dev/live per-run
  breaker, not a gate-only exception); `MAX_PER_CALL_RESERVE_EUR_MICROS`
  100,000 → **150,000** micro-EUR; `SDK_ALLOWANCE_SAFETY_MARGIN`
  **0.70 unchanged**; `MAX_TURNS` **10 unchanged**;
  `MAX_TOOL_CALLS_PER_CHECK` **5 unchanged**. One run-level coordinator
  still owns the run budget and each call still reserves a bounded
  slice of it with the SDK allowance derived from that reservation — no
  second budget pool was introduced. **(2) Prompt contract** rewritten
  as an ordered scan-identify-emit-stop algorithm: scan the complete
  document before emitting anything; identify every genuine defect;
  only then emit one tool call per identified defect; do not stop after
  the first; no speculative or duplicate findings; terminate without
  unnecessary explanatory prose; no genuine defect means no tool call.
  Conciseness now governs the termination step only, never the scan.
  Every existing containment rule (untrusted-data framing, verbatim
  line/excerpt citation, closed reason codes, per-class evidence count,
  one-tool cage) is preserved and asserted in tests. **(3)
  `stale-STATE-marker` evidence ordering changed**: evidence item 1
  (primary) must be the dated historical entry and item 2 the
  current-state text it contradicts, aligning the model contract with
  the frozen positional primary-location scoring semantics. **(4)
  `missing-synthetic-label` provenance rule changed**: a figure
  genuinely derived from synthetic/labeled evaluation or test data
  requires the adjacent synthetic qualifier, and a number whose
  provenance does not invoke that convention does not. No filename
  shortcuts, and no frozen fixture string or answer-key identifier
  appears in any prompt. **(5) Absent-file deterministic no-call path
  added**: `JudgmentRequest.text is None` returns the empty result
  before any budget reservation, SDK allowance construction or model
  call, and writes no `agent_calls` row — a legitimate `Confirmed([])`,
  not an agent failure. **(6) Gate runner** now builds one independent
  `RunBudgetCoordinator` per designated run ID instead of sharing one,
  and its own deliberately-literal cost cross-check (never imported
  from config) checks each run against 750,000 micro-EUR and the
  two-run gate session against **1,500,000 micro-EUR (EUR 1.50)
  aggregate**. Run 2 can therefore genuinely exercise the real agent at
  the re-gate rather than passing its idempotent-rerun and dedup
  invariants on exhaustion containment. Tests: `tests/test_bounds.py`
  gained adopted-bound pins, the absent-file no-call/no-row/no-charge
  proof (including that the skip precedes even the auth check) and the
  prompt-contract assertions; `tests/test_failures.py`'s one-call
  cost-cap failure-injection test resized to the 150,000-micro-EUR
  reservation with its meaning intact (the breaker still halts further
  checker execution); new `tests/test_phase3_gate_runner.py` covers the
  coordinator split, non-transfer of exhaustion between runs, and both
  cost cross-checks. Evidence: 563 tests passing, 3 skips (Phase 3/4
  stubs only), `python -m pip check` clean, Tier 0 artifact validator
  PASS, Phase-1 freeze guard PASS. `BLUEPRINT.md` §7 amended to EUR
  0.75 with the worst-case month restated (30 × EUR 0.75 = EUR 22.50,
  plus one EUR 5.00 Sonnet official gate = EUR 27.50 — below the EUR 40
  frequency-drop trigger and inside the unchanged EUR 50 monthly hard
  ceiling); `SPEC.md` §6 synchronized to the same figures by owner
  authorization this date (derived spec — BLUEPRINT governs);
  `MODEL_CARD.md`, `THREAT_MODEL.md` and `DATA_CONTRACT.md` record the
  amended values, the absent-file no-call semantics and the EUR 1.50
  session bound. Model `claude-haiku-4-5-20251001` unchanged; fixtures,
  synthetic labels, answer key, clean manifest, scorer, scoring
  thresholds and `max_regates` all unchanged; `adr/0005`,
  `EVAL_RESULTS.md`, `PHASE3_GATE_DIAGNOSIS.md` and `artifacts/`
  untouched. `SentinelDailyRun` unchanged and still stub-mode. **No
  model call of any kind occurred in this implementation session and no
  gate rerun occurred.** The remediation is implemented and
  CI-verified; it has not been tested against Haiku, and nothing here
  claims it works. **Phase 3 remains OPEN. Q-77 remains OPEN. Exactly
  one re-gate remains.** Next action: the separately authorized
  `q77-p3-remediation-regate-a` (or equivalent) gate dispatch.
- 2026-08-20 — Phase 3 ONE PERMITTED RE-GATE: honest **OVERALL FAIL**
  (recording dispatch q77-p3-remediation-regate-record-a; this is a
  recording-only entry — the re-gate itself ran 2026-08-19). Source
  commit `c12beee577b929f58cd6f91ff36d048fe955d73f` (the ADR-0005
  remediation implementation commit). Model
  `claude-haiku-4-5-20251001`, auth mode
  `operator-subscription-oauth-assumed`, judgment mode `agent`. Run 1
  (primary/scoring) `r-80b91c34a10a4925a62d573a473cfb4d`, run 2
  (doubled-fixture) `r-659b534850f945c2bb614f0065eaa6e7`. Fresh
  evidence directory and database per the ADR re-gate protocol:
  `var/phase3_regate/` (gitignored), SHA-256 `gate.sqlite3`
  `1e013b5d352fcccb724776748d7575a862aeab923214b49e3419c52024121d16`,
  `gate.jsonl`
  `0aaa2c0d0f0983a7eb4e71d8f674319b501d781f4952aff479c245a107da3794`,
  `cost_ledger.jsonl`
  `332cad54b503a42df15b56eaecd6cdc5cba07b20a32dee9a6d1fb3e78f2190da`,
  `FINDINGS.md`
  `4101ee1dfeb10e2085c05420d0251a5b5083ae146db2d59cc843a2bc26080d42`
  (identical before and after the read-only queries behind this
  record). **Scoring: all PASS.** 60 emitted, 60 true positives, 0
  false positives, 0 misses against the 60 frozen positives; pooled
  precision 60/60 = 1.0000 (≥ 0.90 PASS); pooled recall 60/60 = 1.0000
  (≥ 0.85 PASS); per-class recall 10/10 on all six classes including
  the two that failed the original gate; clean false-flag 0/166 (PASS,
  ≤ 16 allowed). Verification status of those scoring figures: they are
  transcribed from the gate runner's own artifact and corroborated by
  the persisted ledger shape (60 OPEN findings first seen in run 1 plus
  the one later-resolved run-1 row; 80/80 terminal tasks per run; 23
  COMPLETED `agent_calls` per run, 46 total, zero FAILED, REJECTED or
  EXHAUSTED rows) — they were **not** independently rescored against
  `evals/answer_key.jsonl` in the recording session, because reading
  that path is blocked by an operator-level deny rule matching the
  filename and the recording dispatch forbids re-running the gate or
  the scorer. **Invariants: two FAIL.** `every_task_terminal` PASS,
  `zero_lost_tasks` PASS; `idempotent_rerun` **FAIL** (predicate: run 2
  `findings_new == 0`; observed 1);
  `dedup_correct_on_doubled_fixture_run` **FAIL** (predicate: run 2
  `findings_still_open == TP + FP` and `findings_resolved == 0`;
  observed 59 vs 60, and 1). Cost: run 1 629,131 micro-EUR and run 2
  636,623 micro-EUR (each ≤ the 750,000 per-run cap, PASS), session
  total 1,265,754 micro-EUR (≤ the 1,500,000 cap, PASS). Unlike the
  original gate, run 2 genuinely exercised the real agent, so the
  earlier zero-real-call qualification does not apply to these
  invariants. **Root cause, reconstructed independently from the
  persisted gate evidence and the committed source (not from any
  summary):** exactly one semantic defect was implicated —
  `missing-synthetic-label` on `synthetic-05/EVAL_RESULTS.md`, location
  `EVAL_RESULTS.md:14`, reason code
  `FIGURE_WITHOUT_ADJACENT_SYNTHETIC_LABEL`, frozen source line
  `- Coverage: 85.5 percent`. Run 1 cited the excerpt
  `Coverage: 85.5 percent`; run 2 cited `- Coverage: 85.5 percent`.
  Both are valid verbatim spans of that same frozen line, and
  `agents/checker/evidence.py` admits both (it requires only that the
  excerpt appear verbatim within the cited line). Because the
  model-selected excerpt participates in `normalized_content`, which
  `sentinel/lifecycle.py` feeds through `compute_content_hash` and then
  `compute_fingerprint`, the two valid spans produced different
  `content_hash` values (`bc6de62d…c298119e` vs `e4a1ff1d…5b772d5d4`)
  and therefore different fingerprints (`43869a66…d662b71`, ledger id
  48, now RESOLVED; `839cd93d…f3e541d8`, ledger id 61, OPEN) while
  surface, check class, location and reason code were identical. Both
  hash pairs were recomputed from the committed `contracts/schemas.py`
  functions during recording and reproduce the persisted rows exactly.
  The deterministic lifecycle then behaved correctly for the
  fingerprints it was given: the run-2 observation was inserted as new
  and the run-1 identity auto-resolved. Scope: exactly 1 semantic
  defect and 2 ledger rows; the other 59 run-1 findings advanced
  cleanly. The structural identity vulnerability **predates**
  `c12beee` — `agents/checker/evidence.py`, `contracts/schemas.py` and
  `sentinel/lifecycle.py` are not among the 13 files that commit
  changed, and their identity behavior last changed at `cf71364` or
  earlier; the remediation did not introduce it. The exact cross-run
  manifestation was not previously observable because the original
  designated run 2 made zero real model calls, so no second set of
  model-selected excerpts existed to compare. **No claim is made that a
  hypothetical pre-remediation real run 2 would certainly have failed;
  that counterfactual is not determinable.** Note that
  `artifacts/phase3_dev_gate.json` now carries the re-gate artifact,
  committed exactly as the gate runner wrote it; the original gate's
  artifact is preserved at commit
  `f9b7ea4e0762161a2519158ec817288308128584`, blob
  `2b34e31e13ab8c6dd4e59fd9110e40159b48bcb4`, and transcribed in
  `EVAL_RESULTS.md`. One pointer in `PHASE3_GATE_DIAGNOSIS.md` was
  repaired to that historical reference under a narrow owner
  authorization; no diagnosis, finding, metric, conclusion or
  historical meaning in that document changed.
  `artifacts/phase3_gate_diagnosis.json` carries the same stale path
  strings and was deliberately left byte-frozen as a machine-written
  artifact of the original gate — disclosed, not edited. **The one
  permitted re-gate is CONSUMED. No third gate run is authorized under
  the current BLUEPRINT or ADR 0005. Phase 3 remains OPEN. Q-77 remains
  OPEN.** Remediation design and any subsequent validation path require
  a separate owner-governed decision; the exact governance form is not
  decided in this record. `SentinelDailyRun` unchanged and still
  stub-mode. This recording session made no Sentinel checker-agent
  model call, no Haiku or Sonnet gate or re-gate call, no manual
  Sentinel judgment call and no additional evaluation execution; no
  prompt, model configuration, budget, fixture, answer key, clean
  manifest, scorer, threshold, `max_regates`, lifecycle, fingerprint,
  evidence-validation, test or production-code change landed here. This
  commit does not self-cite its own SHA — that SHA and its exact CI run
  are recorded in the private operations OS's Q-77 annotation.
- 2026-08-20 — Phase 3 identity-remediation ADR ADOPTED (dispatch
  q77-p3-remediation-adr-adopt-b). `adr/0006-judgment-finding-identity.md`
  created with Status: ADOPTED, owner approved 2026-08-20. Required
  because ADR 0005 both froze `agents/checker/evidence.py` and stated
  that a failed re-gate authorizes no further adjustment of any kind,
  any subsequent path needing a new owner-approved ADR. **Decision —
  Option C:** separate persistent finding identity from descriptive,
  model-selected evidence. For the two judgment classes built through
  `evidence.py`, `normalized_content` becomes `"reason=<reason_code>"`,
  so judgment finding identity is effectively `(surface, check_class,
  primary location, closed validated reason_code)`. Model-selected
  excerpt text and the stale-STATE secondary anchor remain validated
  and retained in `detail` as audit evidence but leave persistent
  identity; `detail` is defined explicitly as first-seen audit
  evidence, not latest-run evidence. `compute_content_hash` and
  `compute_fingerprint` are unchanged, as are the ledger schema,
  lifecycle semantics, deterministic checkers, prompts, tool schema,
  budget configuration, model, fixtures, answer key, clean manifest,
  scorer, thresholds and `max_regates`. Root cause recorded from the
  consumed re-gate: two equally valid verbatim excerpt spans of the
  same frozen line (`Coverage: 85.5 percent` and
  `- Coverage: 85.5 percent` on `synthetic-05/EVAL_RESULTS.md:14`) both
  passed host validation and produced two fingerprints for one semantic
  defect. Alternatives A, B, D, E and F recorded as rejected, with F
  recorded precisely: F **does** remove the secondary-anchor
  fragmentation path and is rejected instead because primary source
  content would remain part of identity. Honest residuals recorded: the
  primary line stays in identity; two distinct same-class, same-reason
  defects on one line would collapse to one identity (the frozen answer
  key has no such collision and is one-to-one on `(check_class,
  surface, location)`, verified); a source-text change at a stable
  location is deliberately one continuing finding. Scoring disclosure
  recorded narrowly: the frozen duplicate-as-FP rule cannot distinguish
  same-identity judgment emissions differing only in evidence text —
  **no** broader claim that the rule becomes unreachable. Migration:
  none — no schema migration, no `schema_version` bump, no rewrite of
  historical gate databases or artifacts; re-verified at adoption that
  the operational ledger holds zero judgment-class findings and zero
  `agent_calls` rows. The ADR requires the correction to land together
  with a model-free T1–T8 regression suite, recorded as the only
  **pre-validation** evidence currently authorized — not the only
  evidence the correction will ever have. `MODEL_CARD.md` and
  `THREAT_MODEL.md` corrected in this same commit as documentation
  truth repair: they no longer state that the re-gate is pending or
  that one re-gate remains, they no longer claim that no free-form
  model text reaches a fingerprint-relevant field under the current
  implementation, and they distinguish current behavior from the
  adopted-but-unimplemented target. `DATA_CONTRACT.md` unchanged — its
  frozen hash formulas remain correct. **No implementation landed
  here**: no change to `agents/checker/evidence.py` or any production
  code, no test, prompt, fixture, answer-key, clean-manifest, scorer,
  threshold, `max_regates`, lifecycle, fingerprint or schema change.
  This session made no Sentinel checker-agent model call, no Haiku or
  Sonnet gate or re-gate call, no manual Sentinel judgment call, and no
  gate, eval or scorer execution of any kind. The one permitted re-gate
  remains CONSUMED and no third gate run is authorized; ADR 0006
  authorizes no new real-model gate or validation run. **Phase 3
  remains OPEN. Q-77 remains OPEN.** `SentinelDailyRun` unchanged and
  still stub-mode. Artifact-cap handling in this same commit: the
  repository's Tier-0 pre-push validator blocked the first push attempt
  because `adr/` plus `decisions/` then held six non-template decision
  records against the governing ARTIFACT_STANDARD cap of five. By owner
  ruling this date, historical decision 0001 (Separate track) was
  consolidated into this STATE.md — see the "Decision 0001 — Separate
  track" block above — and the standalone file at the former path
  `decisions/0001-separate-track.md` was deleted. Representation only:
  no substantive decision was revoked or superseded, no fleet governing
  standard was changed, the validator was not modified, `--no-verify`
  was not used, and the original file remains recoverable in Git
  history (blob `f8a6696a678e30baca940e605d77dc2cb82aecc7`). The
  decision-record population is now exactly five non-template records,
  `adr/0002` through `adr/0006`. This commit does not self-cite its own
  SHA — that SHA and its exact CI run are recorded in the private
  operations OS's Q-77 annotation. Next action: a separate
  implementation session.
- 2026-08-20 — ADR-0006 identity remediation IMPLEMENTED (dispatch
  q77-p3-identity-implement-a). `adr/0006-judgment-finding-identity.md`
  implemented exactly as adopted, in one dedicated commit, together with
  the model-free T1–T8 regression suite the ADR requires to land with
  it. **Exact implemented rule:** in `agents/checker/evidence.py`,
  `normalized_content = f"reason={reason_code}"` for both judgment
  classes, replacing `f"{reason_code}|{primary.excerpt}"` and
  `f"{reason_code}|{primary.excerpt}|{secondary.excerpt}"`. Persistent
  judgment finding identity is therefore `(surface, check_class,
  primary location, closed validated reason_code)`. `location`
  (`path:primary.line`), `detail` (both branches, byte-identical to
  before), `compute_content_hash`, `compute_fingerprint`, the ledger
  schema, `schema_version`, lifecycle semantics, the four deterministic
  checkers, `agents/checker/tools.py`, `prompts.py`, the tool schema,
  budget configuration, model, fixtures, answer key, clean manifest,
  scorer, thresholds and `max_regates` are all unchanged. No schema
  migration and no `schema_version` bump: `normalized_content` is never
  persisted, only its derived `content_hash` is; the ADR-0006 §8
  determination was re-verified against current source before the edit.
  No historical gate artifact, gate database or gate result was touched.
  **T1–T8, all model-free, all passing (16 tests):** T1 excerpt
  variation — the consumed re-gate's own two spans of
  `- Coverage: 85.5 percent` now yield one `normalized_content`, one
  `content_hash` and one fingerprint, with `detail` still differing as
  first-seen audit evidence, plus a stronger check that no document text
  reaches the identity string for either evidence count; T2 distinct
  defects stay distinct across different primary lines, different
  surfaces at the same path, and different check classes at the same
  location; T3 stale-STATE identity is stable across both a different
  valid secondary anchor line and a different valid span of the same
  secondary line, while different primary lines stay distinct; T4
  fail-closed validation is unchanged — fabricated, non-verbatim,
  empty, out-of-range and wrong-line excerpts are still rejected, the
  closed reason-code set and per-class evidence count still enforced,
  the stale-STATE **secondary** item proven still validated exactly as
  hard as the primary (the specific new risk), and a rejected proposal
  still records no finding; T5 all four deterministic checkers'
  `normalized_content` pinned as hand-written literals, unchanged; T6
  lifecycle rerun proxy — run 2 citing a different valid span of the
  same defect gives `findings_new = 0`, the finding advances,
  `findings_resolved = 0`, `findings_still_open = 1`, one ledger row,
  `detail` not rewritten (the direct regression proof for the two
  invariants that failed in the consumed re-gate); T7 old-identity
  compatibility — an OPEN finding created under the old excerpt-bearing
  rule resolves exactly once while the new fingerprint inserts once,
  zero rows deleted, no exception, and run 3 stable, with no migration
  and no historical-DB rewrite; T8 within-call dedup — two accepted
  emissions of one identity differing only in valid span collapse to one
  finding, with the widening recorded explicitly as deliberate per ADR
  0006 §7 (the scorer is NOT changed to compensate), and distinct
  primary lines proven still to produce two findings.
  **Full suite: 579 passing, 3 skips** (563 + 16 new); `pip check`
  clean; Tier-0 artifact validator PASS; Phase-1 freeze guard PASS;
  pre-push hook PASS without `--no-verify`. Documentation truth repair
  in this same commit, narrow: `MODEL_CARD.md` §4/§4a and
  `THREAT_MODEL.md` §4 no longer say the correction is NOT YET
  IMPLEMENTED, restate the excerpt-in-identity defect as historical
  (behavior through the consumed re-gate, all re-gate evidence
  preserved), and record that the correction's only evidence is
  model-free regression evidence; `DATA_CONTRACT.md`'s judgment
  evidence-contract bullet corrected under a binding owner instruction
  this date — its stale claim that no free-form model text reaches
  `location`, `normalized_content` or `detail` is replaced with the
  accurate split (host-owned `surface`/`check_class`/`path`;
  model-selected but host-validated primary line contributing to
  `location` and identity; model-selected `reason_code` from the closed
  host-enforced set; no excerpt text in `normalized_content` or the
  fingerprint; excerpts and the stale-STATE secondary item retained as
  validated first-seen audit evidence outside identity). No other
  `DATA_CONTRACT.md` content changed. One stale non-blocking
  parenthetical in the Open-decisions line above was narrowly corrected:
  this repo holds no `decisions/` folder since the 2026-08-20
  consolidation, so it is no longer among the repos affected by the
  canonical-validator gap; that gap's canonical patch remains routed to
  the private operations OS's hook-maintenance batch and no
  hook-maintenance or fleet-standard work was done here. **This session
  made no Sentinel checker-agent model call, no Haiku or Sonnet call, no
  gate or re-gate execution, no eval or scorer execution, and no manual
  Sentinel judgment call.** Model-free tests are not Phase-3 closure and
  are not claimed as such: no evidence exists that the correction
  produces a passing gate, and the ADR-0006 §6 residuals stand — the
  primary line remains in identity, so a defect re-cited at a different
  line would still fragment, and two distinct same-class, same-reason
  defects on one line would collapse. The one permitted re-gate remains
  **CONSUMED**; **no new real-model validation is authorized**.
  **Phase 3 remains OPEN. Q-77 remains OPEN.** `SentinelDailyRun`
  unchanged and still stub-mode. No Phase 4 work. This commit does not
  self-cite its own SHA — that SHA and its exact CI run are recorded in
  the private operations OS's Q-77 annotation. Next action: a separate
  owner-governed decision about the prospective validation path.
- 2026-08-20 — ARTIFACT_STANDARD v2.6 cap removal SYNCED; decision 0001
  RESTORED (dispatch adr-cap-removal-a). ARTIFACT_STANDARD v2.6
  (governing decision in the private operations OS,
  ADR-2026-08-20-artifact-standard-v2.6-adr-cap-removal) supersedes the
  five-record decision-record maximum; `.githooks/validate_artifacts.py`
  synced to the no-hard-cap behavior (count>5 failure removed;
  zero-count wording now "need at least 1"; folder-required and
  at-least-one-record failures retained). Previous cap and consolidation
  records in this change log remain historical and accurate for their
  dates. The standalone `decisions/0001-separate-track.md` — consolidated
  into this STATE.md on 2026-08-20 under the then-current five-record
  rule — was restored at its exact original path, byte-exact from its
  pre-consolidation Git blob `f8a6696a678e30baca940e605d77dc2cb82aecc7`
  (hash re-verified on the restored file), recreating the `decisions/`
  folder; the consolidation record above stands as history.
  `adr/0002-architecture-skeleton-reuse.md` remains on disk, untouched.
  A future ADR-0007 may coexist with ADR-0002 through ADR-0006 when a
  genuine material decision warrants it; none is created by this commit.
  **No Sentinel functional/evaluation behavior changed** — no evaluation
  contract, model, prompt, fixture, answer key, scorer, threshold,
  budget, identity, lifecycle, or Phase-3 result is altered. **This
  session made no Sentinel model call; no real-model validation is
  authorized by this commit.** Phase 3 remains OPEN. Q-77 remains OPEN.
  Next action: a separate owner-governed decision about the prospective
  validation path.
- 2026-08-20 — Prospective-validation governance ADOPTED (Q-77 Stage-1
  governance-adoption dispatch; owner-approved, independently
  red-teamed design). `adr/0007-prospective-validation-protocol.md`
  created with Status: ADOPTED, and BLUEPRINT amended to v1.3 with the
  dated §11(i) amendment plus a one-sentence §5 pointer: exactly ONE
  new prospective Phase-3 validation cycle is authorized for the
  current Sentinel-v1 lineage, governed entirely by ADR 0007 — its
  runner-self-validated execution-validity predicates (both runs
  COMPLETED with gate exit code 0; zero FAILED/DEAD_LETTER tasks;
  every relevant agent_call COMPLETED with zero
  FAILED/REJECTED/EXHAUSTED/RESERVED rows; mechanical Run-2 coverage —
  run 2 must contain >0 relevant COMPLETED agent_calls AND match
  run 1's relevant COMPLETED count; clean exact-SHA source
  attestation; frozen metrics/invariants/cost caps unchanged;
  independent read-only reconstruction for BOTH PASS and FAIL; fresh,
  non-default, initially nonexistent gate-root/artifact paths), its
  consumption boundary on C = persisted agent_calls rows with
  reserved_eur_micros > 0 across the prospective run IDs with exactly
  four dispositions and no fifth (C==0 → PRE-CALL ABORT, not
  consumed, same SHA re-attemptable; C>0 + independently verified
  PASS → PASS, Phase 3 may close, Phase-4 progression becomes
  permitted, Q-77 stays OPEN for its remaining phases; C>0 + a
  complete result failing a binding condition or a claimed PASS
  contradicted by reconstruction → VALID COMPLETED FAIL, terminal for
  this lineage, Phase 3 stays OPEN, no Phase 4; C>0 + no parseable
  complete result → CONSUMED-PARTIAL / NO RESULT, consumed, no retry,
  Phase 3 stays OPEN, no Phase 4, and explicitly NOT evidence of a
  failed completed gate), its bound sequence A/A2/B/C/D/E/F (Stage-1
  adoption; exact-SHA CI green on Stage 1 before Stage 2 may begin;
  separate Stage-2 runner implementation with model-free regression
  tests and STATE record; exact-SHA CI + freeze green on Stage 2;
  external SHA pin; only then the one execution; then independent
  verification), the §5 execution preflight (origin fetched and
  origin/main == HEAD; HEAD equals the pinned Stage-2 SHA; clean
  status; the runner receives the mandatory pinned source SHA;
  Phase-1 freeze guard PASS; fresh nonexistent paths), and the rule
  that any repository change after the external pin invalidates it
  and stops execution for reassessment. ADR 0007 also records the
  binding honest disclosures: consumed re-gate model-selected
  primary-line stability was 20/20, not 60/60 (the other 40/60
  findings were deterministic host-computed); the highest re-gate run
  cost was 636,623 micro-EUR against the 750,000 micro-EUR per-run
  cap (113,377 micro-EUR = 15.1% headroom); the frozen corpus is
  remediation-informed acceptance evidence, not unseen-generalization
  evidence; retrospective application of the ADR-0006 identity to the
  consumed re-gate removes the fragmentation but does NOT relabel the
  historical result; and the tailoring/gate-shopping objection is
  disclosed, not claimed eliminated. `SPEC.md` §3 synchronized
  (derived spec — BLUEPRINT governs); `MODEL_CARD.md` (§3 end, §4a
  evidence-status) and `THREAT_MODEL.md` (§4 evidence-status)
  narrowly truth-repaired so their authorization statements match
  this HEAD — no other content in either file changed. ADR 0006
  remains closed and unchanged; both historical gate results remain
  FAILs; `evals/eval_config.yaml` `max_regates` remains 1; ADRs
  0002–0006, decisions/0001, the template, `EVAL_RESULTS.md`,
  `PHASE3_GATE_DIAGNOSIS.md` and `artifacts/` are untouched — no ADR
  deleted or consolidated. **This session made no Sentinel
  checker-agent model call, no Haiku or Sonnet call, no gate, re-gate,
  eval or scorer execution, and no manual Sentinel judgment call; no
  production code, test, prompt, fixture, answer-key, clean-manifest,
  scorer, threshold, identity, lifecycle, fingerprint or eval-config
  change landed here; Stage 2 has NOT begun.** `SentinelDailyRun`
  unchanged and still stub-mode. No Phase 4 work. **Phase 3 remains
  OPEN. Q-77 remains OPEN.** This commit does not self-cite its own
  SHA — that SHA and its exact CI run are recorded in the private
  operations OS's Q-77 annotation. Next action: sequence step A2
  (exact-SHA CI green on this commit), then the separate Stage-2
  implementation dispatch.
- 2026-08-20 — ADR-0007 Stage 2 IMPLEMENTED (dispatch
  q77-stage2-adr0007-implement-a, sequence step B; Stage-1 exact-SHA
  CI was green before this session began — step A2 satisfied).
  `scripts/run_phase3_dev_gate.py` now self-validates the ADR-0007 §2
  execution-validity predicates: OVERALL PASS requires BOTH the
  unchanged frozen scoring/invariant/cost checks AND a new
  `evaluate_execution_validity` result reconstructed read-only from
  persisted ledger state — both designated runs COMPLETED with exit
  code 0; zero FAILED and zero DEAD_LETTER tasks; every relevant
  agent_call COMPLETED with zero FAILED/REJECTED/EXHAUSTED/RESERVED
  rows ("relevant" = all agent_calls rows for the two designated run
  IDs, the table being schema-constrained to the two judgment
  classes); mechanical Run-2 coverage (>0 relevant COMPLETED
  agent_calls AND equal to run 1's count); and mechanical source
  attestation (the preflight-verified HEAD SHA must exactly equal the
  mandatory `--require-source-sha` 40-lowercase-hex value — presence
  alone never attests; the artifact's authoritative `source_commit`
  is the preflight-verified SHA, never a post-run HEAD re-read). The
  full predicate/count structure is embedded in the returned and
  persisted gate result for independent reconstruction, including the
  per-run `reserved_eur_micros > 0` counts (the §3 C components) —
  the runner records them and adjudicates no disposition. The §5
  preflight (`run_prospective_preflight`) is mandatory in agent mode
  and fails closed BEFORE `run_gate` — before any auth check, FX
  resolution, coordinator construction or model call, hence before
  any positive-reservation agent_call row can be persisted (C == 0 on
  preflight failure): valid 40-hex SHA; fetch origin; origin/main ==
  HEAD == required SHA; clean `git status`; Phase-1 freeze guard PASS
  (in-process invocation of `scripts/check_phase1_frozen.py`);
  explicit fresh, non-default, initially nonexistent `--gate-root`
  and `--artifacts-dir` (both now required arguments; the historical
  locations `var/phase3_gate`, `var/phase3_regate` and `artifacts/`
  are rejected). The former delete-existing-evidence path and the
  `"unknown"` source-commit fallback are removed entirely; existing
  evidence is never deleted or overwritten (fresh-dir creation uses
  `exist_ok=False` in both modes). Exit codes: 0 OVERALL PASS,
  1 complete result failing any condition, 2 usage/preflight failure
  before any consumption. No frozen metric, invariant, threshold,
  cost cap, fixture, answer key, scoring rule, schema, ledger,
  harness, evidence, lifecycle, fingerprint or eval-config change;
  `max_regates` remains 1. Evidence (model-free, network-free):
  `tests/test_phase3_gate_runner.py` extended from 10 to 56 tests
  covering the full previously approved V1–V12 substance — the exact
  historical false-PASS shape (all frozen checks PASS while judgment
  work FAILED/DEAD_LETTERed → OVERALL PASS now impossible, only
  execution-validity lines FAIL), every blocking run/task/agent_call
  state parametrically, Run-2 zero-coverage and count-inequality,
  mechanical source attestation (mismatched/malformed/absent SHA
  pairs can never validate), all preflight fail-closed cases on an
  injected fake git, the consumption-boundary ordering proof, the
  CLI contract, and the no-deletion guarantee; full suite 625 passed
  + 3 skipped (Phase 3/4 stubs only), 89.6% line coverage;
  `.githooks/validate_artifacts.py` Tier 0 PASS;
  `scripts/check_phase1_frozen.py` PASS. **This session made no
  Sentinel checker-agent model call, no gate, re-gate, eval or scorer
  execution, and no manual judgment call; the prospective validation
  cycle has NOT executed.** `SentinelDailyRun` unchanged and still
  stub-mode. No Phase 4 work. **Phase 3 remains OPEN. Q-77 remains
  OPEN.** This commit does not self-cite its own SHA — that SHA and
  its exact CI run belong in the private operations OS's Q-77
  annotation. Next action: sequence step C — exact-SHA CI and freeze
  green on this Stage-2 commit — then step D, the external pin of
  this Stage-2 SHA in the private operations OS's Q-77 annotation;
  only then may the single prospective cycle execute (step E) under
  the §5 preflight, followed by independent verification (step F).
- 2026-08-20 — ADR-0007 PROSPECTIVE VALIDATION CYCLE: honest **VALID
  COMPLETED FAIL** (Step-F recording-only entry — the cycle itself
  ran 2026-08-20, sequence step E, after step C exact-SHA CI/freeze
  green on the Stage-2 commit and the step-D external pin recorded in
  the private operations OS's Q-77 annotation). Source commit
  `8c235af1ba254e9a238a797be558129bc2a82f99` (the ADR-0007 Stage-2
  implementation commit; `required_source_sha`, `attested_source_sha`
  and `source_commit` in the gate artifact are all equal to it).
  Model `claude-haiku-4-5-20251001`, auth mode
  `operator-subscription-oauth-assumed`, judgment mode `agent`. Run 1
  (primary/scoring) `r-e8e27a1133754705ac76fd0f0842c101`, run status
  FAILED; run 2 (doubled-fixture)
  `r-a2ed87b770014722a5f7bd583b9637db`, run status COMPLETED.
  Disposition per ADR-0007 §3, independently verified in Step F:
  `C = 46 > 0` (independently reconstructed as 23
  positive-reservation `agent_calls` rows per run) with a complete
  parseable gate result failing binding conditions → **VALID
  COMPLETED FAIL** — the one authorized prospective cycle is
  **consumed** and the result is **terminal for the current
  Sentinel-v1 Phase-3 validation lineage**: Phase 3 stays OPEN, Phase
  4 is not permitted under this lineage, no further validation cycle
  is authorized by ADR-0007, Q-77 stays OPEN. Scoring: every frozen
  threshold PASSED — pooled precision 58/58 = 1.0000, pooled recall
  58/60 = 0.9667, per-class recall 10/10 on five classes and 8/10 =
  0.8000 on `missing-synthetic-label` (all ≥ 0.80), clean false flags
  0/166; emitted 58, TP 58, FP 0, misses 2; independent Step-F
  reconstruction exactly matched the artifact on every figure, with
  zero artifact/reconstruction disagreements. The two misses are
  exactly `inj-004` (`synthetic-01/EVAL_RESULTS.md:12`) and `inj-005`
  (`synthetic-01/EVAL_RESULTS.md:13`), both inside the run-1
  DEAD_LETTER scope — there is no additional independently observed
  model-quality miss. Execution validity (ADR-0007 §2) FAILED on six
  reconstructed predicates — `run1_completed`, `runs_exit_code_zero`,
  `zero_dead_letter_tasks`, `all_agent_calls_completed`,
  `zero_agent_calls_failed`, `run2_call_count_equals_run1` — and
  PASSED the rest including `source_sha_attested`; run 1 FAILED with
  80 tasks terminal (79 DONE, 1 DEAD_LETTER; 22 COMPLETED + 1 FAILED
  agent calls), run 2 COMPLETED with 80 DONE and 23 COMPLETED calls,
  zero FAILED/REJECTED/EXHAUSTED/RESERVED rows. Root cause of the
  FAIL: one run-1 Haiku call (`agent_calls` id 1, scope
  `synthetic-01/EVAL_RESULTS.md`, class `missing-synthetic-label`)
  failed at the SDK per-call budget ceiling (persisted error,
  verbatim: "Exception: Claude Code returned an error result: Reached
  maximum budget ($0.1226)"; `reserved_eur_micros = 150000`,
  `charged_eur_micros = 150000` — conservatively charged its full
  reservation because final SDK usage was not recoverable); the
  harness handled it fail-closed as designed (FAILED agent call →
  Inconclusive → DEAD_LETTER task → run 1 FAILED), and
  `idempotent_rerun` FAILED on the frozen predicate (run 2
  `findings_new = 2`, both findings in that exact dead-lettered
  scope, which run 2 completed for 26,583 micro-EUR) — an execution
  gap, not identity instability. The ADR-0006 identity defect did NOT
  recur: all 58 run-1 findings that existed were re-observed in run 2
  with stable identity, the 60 persisted finding rows carry 60
  distinct fingerprints, zero spurious resolutions, zero identity
  fragmentation, and `dedup_correct_on_doubled_fixture_run` PASSED.
  The narrower evidenced conclusion is recorded, never "there was no
  system issue": the frozen scoring/identity/dedup behavior was
  correct on the completed work; the gate failed because one
  stochastic model call hit its configured SDK per-call budget
  ceiling; the current execution policy does not tolerate even one
  such transient incomplete judgment, so the binding gate correctly
  failed; and the evidence does not establish why that individual
  call consumed unusually high model budget. Cost: run 1 charged
  648,422, run 2 charged 493,293, session total 1,141,715 micro-EUR —
  all frozen caps PASS (750,000 per run; 1,500,000 per session).
  Evidence: persisted locally outside the repository under
  evidence-parent basename `prospective-20260820T182647Z-033e8a9b`
  (fresh per the §5 preflight; retained locally); Step-F before/after
  SHA-256 byte-identical for all six files, recorded in
  `EVAL_RESULTS.md` (raw prospective gate artifact SHA-256
  `9e401356e7682bd8ab07e92f53b7ef034d2dd1edefed35da89d1f21fa95e24bb`).
  The raw artifact is NOT committed to this public repository: its
  byte-verbatim machine output contains absolute local paths
  prohibited by this repo's public-live writing rule, and editing
  evidence would break verbatimness — it stays external,
  hash-identified. The committed fixed-path
  `artifacts/phase3_dev_gate.json` is untouched and continues to
  carry the 2026-08-19 re-gate artifact; no displaced-artifact
  pointer is created because nothing was displaced. Recording surface
  of this commit: `EVAL_RESULTS.md` (third result section),
  `STATE.md`, and narrow truth repairs in `MODEL_CARD.md` (§3 end,
  §4a evidence status, §6), `THREAT_MODEL.md` (§4 evidence status)
  and `SPEC.md` §3 — statements that the prospective cycle had not
  executed. Both prior `EVAL_RESULTS.md` sections and both historical
  FAILs are preserved unchanged; no remediation, retry design, budget
  change, ADR-0008, or further validation cycle is designed or
  authorized here; `evals/eval_config.yaml` `max_regates` remains 1;
  fixtures, answer key, scorer, thresholds, prompts, identity,
  lifecycle and fingerprint semantics are untouched. **This recording
  session made no Sentinel checker-agent model call, no Haiku or
  Sonnet gate call, no manual Sentinel judgment call and no
  additional evaluation execution.** `SentinelDailyRun` unchanged and
  still stub-mode. No Phase 4 work. **Phase 3 remains OPEN. Q-77
  remains OPEN.** This commit does not self-cite its own SHA — that
  SHA and its exact CI run are recorded in the private operations
  OS's Q-77 annotation. Next action: none in this repo under ADR-0007
  — any subsequent validation path requires a new owner-governed
  decision.
- 2026-08-20 — ADR-ledger representation NORMALIZED (dispatch
  adr-ledger-cleanup-a; representation/information-architecture only).
  The historical decision `decisions/0001-separate-track.md` was moved
  via git rename to `adr/0001-separate-track.md`, byte-identical (same
  blob `f8a6696a678e30baca940e605d77dc2cb82aecc7`); the then-empty
  `decisions/` directory disappears with the move. The numbered
  placeholder `adr/0001-template.md` was deleted with no replacement:
  it was a template, never an adopted decision, and no governing rule
  (canonical ARTIFACT_STANDARD v2.6, BLUEPRINT, or repo instructions)
  requires a repo-local ADR template — the canonical template stays
  centralized in the private operations OS. The public ledger is now a
  single chronological sequence `adr/0001`–`adr/0007`. Mechanical
  reference repairs: BLUEPRINT §4 repo tree (one `adr/` ledger line)
  and §10 item 7 (exact path `adr/0001-separate-track.md` governs);
  the stale Open-decisions validator paragraph above replaced with the
  current truth (the validator recognizes both `adr/` and `decisions/`;
  no validator change required); the Decision-0001 artifact note above
  extended with the restore-then-move history. Historical dated
  references to the former `decisions/` path (BLUEPRINT Phase-0 gate
  row, README v0.1 version-log row, prior change-log entries here)
  remain unchanged — accurate for their dates. ADRs 0002–0007 have
  zero diff: no renumbering, no substantive edits, no ADR-0008; no
  decision content lost; git history preserves both former paths.
  `.githooks/validate_artifacts.py`, CI, tests, fixtures, evals,
  budgets, `EVAL_RESULTS.md` and `artifacts/` untouched. **This
  session made no Sentinel checker-agent model call, no Haiku or
  Sonnet call, no gate, re-gate, eval or scorer execution, and no
  manual Sentinel judgment call.** `SentinelDailyRun` unchanged and
  still stub-mode. No Phase 4 work. ADR-0007's terminal VALID
  COMPLETED FAIL disposition and consumed cycle are unaffected.
  **Phase 3 remains OPEN. Q-77 remains OPEN.** Next action: unchanged
  — any subsequent validation path requires a new owner-governed
  decision.
- 2026-08-20 — ADR-0008 ADOPTED (dispatch q77-p3-adr8-adopt-a;
  adoption-only commit).
  `adr/0008-judgment-call-execution-reliability.md` created with
  Status: ADOPTED, owner approved 2026-08-20 on the evidence basis of
  the read-only design session (q77-p3-adr8-design-a) and its owner
  red-team. Decision, in brief: the smallest runtime contract that
  makes one pathological judgment-model call a contained, diagnosable
  event — (1) bounded failed-call observability sufficient to
  diagnose caught in-process judgment-call failures (the ADR-0005 §6
  pre-registered revisit trigger fired: the 2026-08-20 failed real
  call could not be adequately diagnosed from retained evidence);
  (2) a mechanized failure taxonomy classified from captured typed
  SDK signals, never from exception prose; (3) exactly ONE same-run
  re-execution for exactly ONE failure class — captured terminal SDK
  subtype `error_max_budget_usd` — inside the SAME run-scoped budget
  state, with an ordinary reservation, a maximum of two actual model
  invocations per logical judgment task, and fail-closed dead-letter
  on any second-invocation failure; (4) honest overshoot accounting
  replacing the silent `min(converted_estimate, reservation)` clamp
  on recovered successful-call cost estimates (a latent
  accounting/enforcement design vulnerability found by design review;
  no historical occurrence is evidenced); (5) fail-closed behavior
  preserved for every other failure class. The evidence boundary is
  recorded in the ADR and stays narrow: the failed call's root cause
  is not proven transient or stochastic — this is the only currently
  observed execution-failure class for which the incident was
  followed by successful same-scope re-execution. Frozen values
  unchanged: RUN_BUDGET_EUR_MICROS 750000,
  MAX_PER_CALL_RESERVE_EUR_MICROS 150000,
  SDK_ALLOWANCE_SAFETY_MARGIN 0.70, MAX_TURNS 10,
  MAX_TOOL_CALLS_PER_CHECK 5; ADR-0005's "FAIL -> raise again ->
  rerun" prohibition stands. The BLUEPRINT.md change in this commit
  is exactly one descriptive repository-tree line (decision records
  0001–0007 -> 0001–0008); no substantive BLUEPRINT amendment.
  **ADR-0008 is ADOPTED but NOT implemented** — no runtime, Python,
  ledger-schema, test, prompt, model, fixture, answer-key, scorer,
  threshold, identity or lifecycle change landed here, and the
  required model-free proof package (ADR-0008 §9, R1–R24) does not
  yet exist. **ADR-0008 authorizes no real-model validation cycle**:
  ADR-0007's one prospective cycle remains consumed and terminal for
  the current Sentinel-v1 Phase-3 validation lineage, `max_regates`
  remains consumed, no gate runner is amended, no gate predicate is
  relaxed or reinterpreted, and all three historical FAILs stand
  unrelabeled. **This session made no Sentinel checker-agent model
  call, no Haiku or Sonnet call, no gate, re-gate, eval or scorer
  execution, and no manual Sentinel judgment call.**
  `SentinelDailyRun` unchanged and still stub-mode. No Phase 4 work.
  **Phase 3 remains OPEN. Phase 4 is not permitted under the current
  lineage. Q-77 remains OPEN.**
  This commit does not self-cite its own SHA — that SHA and its exact
  CI run are recorded in the private operations OS's annotation for
  this work item. Independent post-adoption read (2026-08-21): PASS —
  ADR-0008 survives review. Next action: a separate ADR-0008
  implementation dispatch for runtime changes plus the complete
  model-free R1–R24 proof package. No real-model validation is
  authorized.
- 2026-08-21 — ADR-0008 IMPLEMENTED (dispatch q77-p3-adr8-impl-a, with
  owner correction 1). `adr/0008-judgment-call-execution-reliability.md`
  implemented exactly as adopted, in one commit, together with the
  complete model-free R1–R24 proof package the ADR requires to land
  with it. The ADR decision text itself is unchanged.
  **Pre-write R10 proof.** Owner ruled R10 PASS conditional on
  mechanically pinning the logical-task uniqueness invariant BEFORE any
  runtime edit, against the ACTUAL production constructors rather than
  hand-built policy objects. Eight tests landed and passed first, with
  zero change to any inventory module: the fixture path list pinned
  directly and through the real `FixtureSurfaceProvider`; the live
  `_derive_policy` and `build_repo_surfaces` driven with a fake HTTP
  client returning a git tree that repeats blob paths and a repo name
  repeated across two pagination pages; `build_site_surface` driven the
  same way; carry-forward `open_scopes` proven unable to reintroduce a
  duplicate path; `_dedupe_repos_by_owner` proven to collapse doubled
  owners before any work unit exists; and a combined case asserting
  `(surface, check_class)` — and therefore `task_key` — is unique
  across every production branch in one run. `(run_id, task_key)` is
  reused unchanged: no task_id was added to `JudgmentRequest`, and no
  attempt ordinal, retry-of reference or other identity mechanism was
  invented. **Runtime landed.** `MAX_MODEL_ATTEMPTS_PER_TASK = 2` bounds
  ACTUAL SDK invocations per logical judgment task (a pre-call
  `REJECTED`/`EXHAUSTED` row, where no model call occurred, does not
  count). The terminal `ResultMessage` is now captured out of the SDK
  stream and carried beside any trailing exception, closing the
  information-loss path that destroyed the typed subtype, token counts
  and cost estimate of the 2026-08-20 failed call. One mechanized
  classifier maps an invocation to exactly one class of the adopted
  taxonomy; only a captured typed subtype `error_max_budget_usd` is
  retryable, exception prose never authorizes a retry, and a tripped
  tool breaker outranks a coexisting budget subtype. The second
  invocation is ordinary: same request, prompts, model, cage,
  `MAX_TURNS`, `MAX_TOOL_CALLS_PER_CHECK` and run-scoped coordinator,
  with an ordinary reservation from remaining capacity. Each invocation
  gets fresh host state, so a failed attempt's findings stay audit
  evidence and never become live. **Observability.** New additive,
  strictly append-only `agent_tool_attempts` table (no `ALTER TABLE`, no
  `schema_version` bump, `IF NOT EXISTS` throughout, `DELETE` and
  `UPDATE` both refused by trigger) records per proposal: parent call,
  ordinal within that invocation, proposed reason code, proposed
  evidence count, up to two proposed coordinates, outcome
  (ACCEPTED/REJECTED/DUPLICATE/BREAKER_REFUSED) and a closed rejection
  category. **Excerpt retention was decided by test, not assertion**, as
  the owner correction required: an adversarial pair — a substantively
  correct near-miss and an outright fabrication — was shown to reject
  with identical reason code, coordinate and category, collapsing a
  coordinates-only record into one indistinguishable row. A bounded
  snippet is therefore retained, and only on that single
  text-discriminated category, capped at 80 characters in code and by
  DDL CHECK, redacted through the existing first-party boundary before
  deterministic truncation; every other outcome retains no proposed
  text. The older unbounded leak is closed too: `rejection_reason` no
  longer stores raw host-validation prose that embedded the proposed
  excerpt verbatim. **Accounting.** The silent
  `min(converted_estimate, reservation)` clamp is gone: a completed call
  with a recoverable estimate is charged the full converted estimate, a
  failed call `max(reservation, estimate)`, and either state without a
  recoverable estimate burns the full reservation. Known overshoot may
  drive remaining run capacity to zero or negative, after which
  `reserve()` refuses — a truthful record, never permission for more
  spend. **Cross-layer ordering**, per the owner correction: tool-attempt
  audit and call finalization commit in ONE ledger transaction, and the
  in-memory coordinator advances only AFTER that transaction commits, so
  a persistence failure can never leave accounting ahead of the audit
  trail; failure injection on both legs proves the row stays RESERVED,
  zero partial attempt rows survive, the reservation is still held at
  its conservative post-reservation figure, no retry follows, and the
  judgment fails closed. Process-crash behaviour is unchanged and no
  crash-proof per-tool telemetry is claimed. Frozen values untouched:
  `RUN_BUDGET_EUR_MICROS` 750000, `MAX_PER_CALL_RESERVE_EUR_MICROS`
  150000, `SDK_ALLOWANCE_SAFETY_MARGIN` 0.70, `MAX_TURNS` 10,
  `MAX_TOOL_CALLS_PER_CHECK` 5. Unchanged surfaces verified by diff:
  prompts, evidence validation, the two judgment adapters, the judgment
  stub protocol, the pipeline, every inventory module, `sentinel/config.py`,
  `BLUEPRINT.md`, `EVAL_RESULTS.md`, fixtures, evals, artifacts, the
  Phase-3 gate runner, answer key, scorer, thresholds, model and
  ADR-0006 identity semantics. Documentation truth repair in the same
  commit, narrow: `THREAT_MODEL.md` withdraws its false claim that
  aggregate charged cost "cannot exceed" the run cap and states the
  start-condition invariant that is actually enforced; `DATA_CONTRACT.md`
  records the new table, the two-row logical task and the cost rules;
  `DATA_RETENTION_POLICY.md` gains a section stating exactly what is and
  is not retained, the snippet bound and its whitespace-normalization
  limitation; `MODEL_CARD.md` records the bounded re-execution contract;
  `SPEC.md` §6 records that caps govern whether a call may start.
  **This session made no Sentinel checker-agent model call, no Haiku or
  Sonnet call, no gate, re-gate, eval or scorer execution, and no manual
  Sentinel judgment call.** ADR-0007's one prospective cycle remains
  consumed and terminal, `max_regates` remains consumed, no gate runner
  is amended and no gate predicate is relaxed or reinterpreted; all
  three historical FAILs stand unrelabeled. Passing model-free tests is
  not Phase-3 closure. `SentinelDailyRun` unchanged and still stub-mode.
  No Phase 4 work. **Phase 3 remains OPEN. Phase 4 is not permitted
  under the current lineage.** This commit does not self-cite its own
  SHA — that SHA and its exact CI run are recorded in the private
  operations OS's annotation for this work item. Next action:
  independent post-implementation review of the exact implementation
  commit and its R1–R24 proof. No real-model validation is authorized.

- 2026-08-21 — ADR-0008 implementation-review gaps CLOSED (dispatch
  `q77-p3-adr8-impl-review-remed-a`). The independent
  post-implementation review of the ADR-0008 implementation commit
  found two narrow implementation/proof gaps and one documentation
  truth defect. The ADR-0008 decision itself was NOT reopened and is
  unchanged. **Gap A — the production capture path had no direct
  proof.** The R1–R24 package drives the harness through its injected
  `query_fn` seam and manufactures a `QueryOutcome` *after* the capture
  boundary, so `agents/checker/harness.py::run_query` — the function
  whose entire job is capturing the terminal typed `ResultMessage` out
  of the stream — was never executed by any test, and its body showed
  as unexecuted in the coverage report. That is precisely where the
  historical defect lived. New section R25 executes the REAL
  `run_query` body with only `claude_agent_sdk.query` replaced by a
  local deterministic stream: a real typed `ResultMessage`
  (`subtype = error_max_budget_usd`, `is_error`, cost, usage, turns)
  is yielded, the stream THEN raises the plain untyped exception whose
  prose quotes the CLI's maximum-budget text, and `run_query` is proven
  to RETURN both halves rather than raise, with subtype, cost, usage and
  turn count all still recoverable and the classification
  `SDK_BUDGET_CEILING` retryable *only* because of the captured typed
  subtype. The complementary case — the identical exception object
  raised BEFORE any typed result — is proven to yield
  `result is None`,
  `TRANSPORT_PROCESS_SDK_EXCEPTION_WITHOUT_CAPTURED_TYPED_RESULT` and
  NON-retryable; no exception prose is parsed anywhere. The clean-stream
  case is covered too. `run_query` itself needed no change: the
  implementation was correct, only unproven. The async body is driven
  without an event loop, so conftest's blanket no-network guard stays
  fully armed and no proof is skipped. **Gap B — a post-durable
  accounting fault did not stop the run.** `TerminalAccountingError`
  claimed that no further model invocation is started, but nothing
  enforced it: if `coordinator.commit()` failed AFTER the terminal
  SQLite transaction had committed, the pipeline dead-lettered that one
  task and the same judgment stub kept serving later tasks in the same
  run on in-memory budget figures known to be wrong. `CagedCheckerStub`
  now carries a private run-lifetime latch, set before
  `TerminalAccountingError` is raised, and every later non-absent
  judgment on that stub fails closed ahead of the auth check, ahead of
  `reserve()` and ahead of `query_fn` — zero further model invocations.
  Nothing is written to make the two layers agree: the faulted call's
  durable terminal row and its tool-attempt audit are proven untouched,
  unrewritten and undeleted. The deterministic confirmed-absent
  short-circuit stays ahead of the latch, since the invariant enforced
  is no further MODEL INVOCATION. New section R26 proves all of it,
  distinct from R23 (which injects inside the ledger transaction, where
  nothing becomes durable and the row stays RESERVED). No pipeline,
  task-lifecycle, schema, retry-taxonomy, budget-reset or
  second-coordinator change was made. **Gap C — stale documentation.**
  `THREAT_MODEL.md` §6 still said an in-process catchable failure is
  charged at the full reservation; it now states the truthful adopted
  rule — recoverable estimate `max(reservation, converted estimate)`,
  no recoverable estimate the full reservation, never zero, the SDK
  figure never authoritative billing — with process-crash `RESERVED`
  reconciliation unchanged. The other documents touched by the
  implementation commit were searched for a directly equivalent stale
  claim and none was found; no general prose cleanup was done. The
  historical implementation entry above is untouched and unrelabeled.
  **No Sentinel checker-agent model call, Haiku or Sonnet judgment
  call, Phase-3 gate, Phase-3 re-gate, eval, scorer, or real-model
  validation occurred in this session.** The repository publication
  gate DID run, as the publication control it is; it is not a Phase-3
  gate and proves nothing about judgment quality. Passing model-free
  tests is not Phase-3 closure. `SentinelDailyRun` unchanged and still
  stub-mode. No Phase 4 work. **Phase 3 remains OPEN. Phase 4 is not
  permitted under the current lineage. No real-model validation is
  authorized.** This commit does not self-cite its own SHA — that SHA
  and its exact CI run are recorded in the private operations OS's
  annotation for this work item. Next action: independent reread of
  this remediation commit before any validation-protocol decision.
- 2026-08-21 — ADR-0009 ADOPTED (dispatch q77-p3-adr9-adopt-a, with
  owner corrections A and B; adoption-only commit).
  `adr/0009-post-adr0008-phase3-validation-protocol.md` created with
  Status: ADOPTED, owner approved 2026-08-21.
  **Evidence lineage, stated precisely.** ADR-0008 is IMPLEMENTED
  (2026-08-21) with the complete model-free **R1–R24** proof package
  that ADR itself required; the subsequent independent review's
  remediation added and passed **R25–R26**, and that remediation has
  now been **independently reread PASS**. R25–R26 are later
  review-remediation proofs — ADR-0008's own requirement was R1–R24
  and is not retroactively restated as R1–R26.
  **Why a new decision record was required at all**, and the only
  reason: ADR-0007's one prospective cycle is consumed and terminal
  for that lineage; ADR-0008 authorizes no real-model validation
  (its §11 firewall); and ADR-0007 §2's execution-validity rule —
  every relevant `agent_call` COMPLETED, zero FAILED `agent_calls` —
  cannot evaluate the bounded-recovery sequence
  `FAILED (SDK budget ceiling) -> COMPLETED` that ADR-0008 already
  adopted. ADR-0007 is NOT reinterpreted to accommodate it: its §2
  predicates stay historical fact, and ADR-0008 §11 forbids silently
  reinterpreting predicates such as `zero_agent_calls_failed`.
  **Decision.** Exactly ONE new prospective Phase-3 validation cycle
  is authorized, for the post-ADR-0008 implementation. It is not
  another re-gate under ADR-0005, not a rerun under ADR-0007, and not
  a relabeling of any historical result; all three historical FAILs
  (2026-08-05, 2026-08-19, 2026-08-20) stand unrelabeled and
  `max_regates` remains 1. The frozen quality contract is unchanged in
  every part: fixtures, answer key, clean manifest, scorer,
  thresholds, model, prompts, reason codes, ADR-0006 finding identity,
  deterministic checks. No cap rises.
  **The new execution-validity rule is prospective** — defined before
  any further model call, and binding on ADR-0009's cycle only.
  Judgment attempts group by the already-proven `(run_id, task_key)`
  identity ordered by `agent_calls.id`, and for every logical judgment
  task entering the model path exactly one history is valid: NORMAL
  `[COMPLETED]`, or BOUNDED RECOVERY `[FAILED, COMPLETED]` where the
  FAILED row's **persisted mechanized failure classification is
  `SDK_BUDGET_CEILING`**. Per owner correction A, the authoritative
  condition is that mechanized classification, **never the SDK subtype
  alone and never exception prose** — ADR-0008's classifier gives local
  containment failures precedence, so a `TOOL_BREAKER` call can persist
  while still carrying `sdk_subtype = error_max_budget_usd`, and
  `[FAILED classified TOOL_BREAKER carrying that subtype, COMPLETED]`
  is explicitly INVALID, as is any other non-`SDK_BUDGET_CEILING`
  class followed by COMPLETED. Subtype `error_max_budget_usd`,
  `sdk_is_error` true and a positive reservation are required
  corroboration, not the authorization. Also invalid: `[FAILED]`;
  `[FAILED, FAILED]`; `[FAILED (other subtype), COMPLETED]`;
  `[FAILED (SDK_BUDGET_CEILING), FAILED]`; `[COMPLETED, COMPLETED]`;
  three or more invocation rows; any REJECTED, EXHAUSTED or RESERVED
  row. First-attempt findings remain audit-only; only the completed
  logical outcome contributes live findings. Cross-run coverage is
  compared as distinct model-path logical `task_key`s rather than raw
  COMPLETED call counts, since a valid recovered history is two call
  rows but ONE logical task. **No schema was added or changed here**:
  ADR-0009 §2A binds the semantic requirement only and defers to Stage
  2 the question of whether the existing durable fields reconstruct the
  mechanized class unambiguously — if they cannot without ambiguous
  free-text parsing, Stage 2 STOPS for owner adjudication rather than
  adding schema or weakening the rule, and Stage 2 must carry a
  deterministic negative test for TOOL_BREAKER carrying the budget
  subtype.
  **Cost.** This lineage declares its own accounted-consumption
  acceptance ceilings, as ADR-0008 §8 requires: 750,000 micro-EUR per
  run and 1,500,000 micro-EUR across the two runs. They are acceptance
  ceilings, not guaranteed pre-spend limits — ADR-0008 §7 permits
  detected post-call overshoot. All known overshoot is accounted
  honestly, exceeding either ceiling is a FAIL, cost is never clamped
  to obtain a PASS, and overshoot never authorizes another invocation
  or another cycle.
  **Finality.** ADR-0007's four-disposition structure is reused over
  `C` = persisted `agent_calls` rows across the two designated run IDs
  with `reserved_eur_micros > 0`: `C == 0` PRE-CALL ABORT (not
  consumed); `C > 0` plus complete independently verified PASS (Phase 3
  may close, Phase 4 becomes permitted); `C > 0` plus a complete result
  failing any binding condition VALID COMPLETED FAIL (consumed, Phase 3
  OPEN, no Phase 4); `C > 0` plus no parseable complete result
  CONSUMED-PARTIAL / NO RESULT (consumed, Phase 3 OPEN, no Phase 4).
  No fifth disposition. **A consumed non-PASS gets no retry under
  ADR-0009 and creates no automatic further remediation or validation
  cycle** — any further path requires a new owner decision, and the
  default posture is PARK. The strongest case against is recorded in
  the ADR and not dismissed: this is a further validation opportunity
  after three failed Phase-3 outcomes, so gate-shopping is a
  legitimate concern; what is offered against it is that nothing in
  the scoring, corpus, model, prompts, caps, retry taxonomy or
  identity changes, that ADR-0008 was adopted and implemented on its
  own unattended-reliability merits, that the new rule is defined
  prospectively and is stricter on the recovery path than subtype
  matching would be, and that one consumed non-PASS ends this
  authorization.
  **This is Stage 1 (bound sequence step A) — governance only.
  ADR-0009 is ADOPTED, NOT IMPLEMENTED.** No runtime, Python, schema,
  ledger, test, runner, prompt, model, fixture, answer-key, scorer,
  threshold, identity or lifecycle change landed here; Stage 2 has NOT
  begun. The BLUEPRINT.md changes in this commit are the new dated
  §11(j) amendment, the version-line and §11-header version bump, and
  one descriptive repository-tree line (decision records 0001–0008 ->
  0001–0009); §11(i) is preserved verbatim. SPEC.md was checked
  clause by clause against the objectively-false test and left
  unchanged: its statements are scoped to ADR-0007 and remain true,
  and BLUEPRINT governs where the two diverge.
  **This session made no Sentinel checker-agent model call, no Haiku
  or Sonnet call, no Phase-3 gate, re-gate, eval, scorer or
  validation execution, and no manual Sentinel judgment call.** The
  repository publication gate DID run, as the publication control it
  is; it is not a Phase-3 gate and proves nothing about judgment
  quality. `SentinelDailyRun` unchanged and still stub-mode. No Phase
  4 work. **Phase 3 remains OPEN. Phase 4 is NOT permitted.** This
  commit does not self-cite its own SHA — that SHA and its exact CI
  run are recorded in the private operations OS's annotation for this
  work item. Next action: independent read of ADR-0009, then a
  separate Stage-2 implementation dispatch if that read is PASS.
- 2026-08-21 — ADR-0009 STAGE-2 RUNNER IMPLEMENTED, model-free
  (dispatch q77-p3-adr9-stage2-a; bound-sequence steps B and C).
  **Missing transition recorded here prospectively, rather than by
  editing any historical entry.** After the Stage-1 adoption commit
  and BEFORE this Stage-2 implementation, the independent read of
  `adr/0009-post-adr0008-phase3-validation-protocol.md` was performed
  and returned **PASS**. That read found **no substantive ADR defect
  requiring amendment**: ADR-0009 stands exactly as adopted on
  2026-08-21, unamended, and Stage 2 then proceeded under the adopted
  ADR-0009 protocol as written. The earlier change-log entries whose
  recorded next action was that read are preserved byte-for-byte and
  were NOT rewritten to reflect its completion — a historical "next
  action" is evidence of what was true when written, not a
  current-state assertion needing retroactive repair. **This is
  implementation, not validation** — nothing was validated, executed
  or claimed to work.
  **What changed.** `scripts/run_phase3_dev_gate.py` now self-validates
  the ADR-0009 §2/§3 execution-validity contract instead of ADR-0007
  §2's failed-call and raw-call-count semantics; `tests/
  test_phase3_gate_runner.py` carries the deterministic proof package;
  STATE.md records this. Nothing else changed. ADR-0007 is NOT
  reinterpreted: its §2 predicates remain historical fact, and the
  historical implementation remains in git history.
  **Execution validity is now evaluated over LOGICAL judgment
  histories**, grouped by the already-proven `(run_id, task_key)`
  identity with `agent_calls.id` as the deterministic attempt order.
  Exactly two histories are valid — `[COMPLETED]` (NORMAL) and
  `[FAILED reconstructed as SDK_BUDGET_CEILING, COMPLETED]` (BOUNDED
  RECOVERY). Everything else is invalid with a closed structured
  reason code: `[FAILED]`; `[FAILED, FAILED]`; `[FAILED (other
  subtype), COMPLETED]`; `[COMPLETED, COMPLETED]`; three or more
  invocation rows; any REJECTED, EXHAUSTED or RESERVED row.
  **The mechanized class is reconstructed from durable structured
  ledger fields only, and no schema was added.** Stage 2's §2A
  determination is that the existing columns are sufficient: the
  persisted `agent_tool_attempts` outcomes plus the typed SDK metadata
  already on the `agent_calls` row reconstruct the class unambiguously,
  because ADR-0008's tool state increments its attempt counter before
  recording every proposal, records exactly one bounded audit row per
  proposal, records `BREAKER_REFUSED` on every actual breaker refusal,
  and flushes that audit in the SAME transaction that finalizes the
  call. **A persisted `BREAKER_REFUSED` outcome takes precedence and
  makes the recovery INVALID even when the row still carries
  `sdk_subtype = error_max_budget_usd`, `sdk_is_error` true and a
  positive reservation** — ADR-0008's classifier gives local
  containment precedence, so subtype alone would promote a contained
  call into a valid recovery, and it must not. Subtype, `sdk_is_error`
  and positive reservation are corroboration, never the authorization.
  **`rejection_reason` prose is not parsed** — not matched, not
  regexed, not prefix-compared, not compared to any class name — and
  neither is exception prose; a structural AST proof pins that no
  executable statement in the runner reads that field at all.
  **Audit completeness is verified BEFORE the absence of
  `BREAKER_REFUSED` is trusted**: the persisted attempt rows must
  number exactly `agent_calls.tool_attempts` with ordinals 1..N in
  order, otherwise the failure class is not safely reconstructable and
  the recovery fails closed rather than being inferred from a missing
  row.
  **Cross-run coverage counts distinct model-path logical `task_key`s,
  not raw call rows** (§3): a recovered logical task is two invocation
  rows but ONE task and counts once. Raw COMPLETED-agent-call-count
  equality is gone; a global zero-FAILED-rows requirement is gone. A
  FAILED row is permitted ONLY as the first row of the exact valid
  bounded-recovery history. Every other ADR-0007 protection is retained
  unweakened: both designated runs COMPLETED, both exit codes zero,
  zero FAILED tasks, zero DEAD_LETTER tasks, no REJECTED/EXHAUSTED/
  RESERVED agent-call row, exact source-SHA attestation, the §5
  preflight, the clean-tree and origin/main == HEAD requirements, the
  Phase-1 freeze requirement, fresh non-default nonexistent evidence
  locations, and every frozen scoring check, threshold and invariant.
  **Cost.** No numeric value moved: 750,000 micro-EUR per run and
  1,500,000 micro-EUR across two runs. The correction is semantic and
  documentary — these are accounted-consumption ACCEPTANCE ceilings,
  not guaranteed maximum real-model spend. Stale runner wording
  claiming a guaranteed EUR 1.50 maximum was repaired. Known post-call
  overshoot is accounted in full, never clamped to obtain a PASS;
  exceeding either ceiling is a FAIL; overshoot authorizes no further
  invocation and no further cycle.
  **Frozen surfaces unchanged, verified by diff.** Zero schema changes;
  zero `agents/checker/` changes; zero scorer, fixture, answer-key,
  clean-manifest, threshold or eval changes; zero model or prompt
  changes; zero retry-taxonomy changes; zero cap increases; zero
  finding-identity, finding-lifecycle or task-lifecycle changes. The
  gate scorer still reads LIVE persisted findings, never the
  tool-attempt audit, and no path converts a failed attempt's tool
  proposal into a scoring finding. `.publicgate-allow` unmodified.
  **Proof.** Model-free throughout, seeded through the real ledger
  writers under the real SQLite schema and evaluated by the real
  ADR-0009 evaluator over that database — including the load-bearing
  negative (a `TOOL_BREAKER` row carrying the budget subtype AND
  deliberately misleading budget-shaped `rejection_reason` prose, which
  must and does yield INVALID), the incomplete-audit fail-closed cases,
  the prose-invariance cases, the logical-coverage cases, the cost
  boundary and honest-overshoot cases, and two end-to-end model-free
  `run_gate` composition proofs where flipping ONLY the first failed
  row's structured tool audit to `BREAKER_REFUSED` flips OVERALL from
  PASS to FAIL through execution validity and never through a relaxed
  scoring check. Full suite: 741 passed, 3 skipped (the existing
  intentional Phase-4 stubs only), 91.1% line coverage; ADR-0008
  R1–R26, ADR-0006 identity and the frozen scoring/invariant suites all
  green; Tier 0 PASS; Phase-1 freeze guard PASS; repository publication
  gate PASS.
  **This session made no Sentinel checker-agent model call, no Haiku or
  Sonnet call, no Phase-3 gate, re-gate, eval, scorer or validation
  execution, and no manual Sentinel judgment call.** No Phase-3
  validation cycle was executed and none is claimed. **The external
  Stage-2 validation SHA is NOT YET PINNED** (sequence step D has not
  run), so steps E and F have not begun. `SentinelDailyRun` unchanged
  and still stub-mode. No Phase 4 work. **Phase 3 remains OPEN. Phase 4
  is NOT permitted.** The governing task item's status is unchanged by
  this commit and is tracked in the private operations OS. This commit
  does not self-cite its own SHA — that SHA and its exact CI run are
  recorded in the private operations OS's annotation for this work
  item. Next action: independent review of this Stage-2 implementation
  commit.
- 2026-08-22 — ADR-0009 STEP-F PUBLIC RECORD: independently verified
  **PASS**, **Phase 3 CLOSED** (dispatch q77-adr0009-stepf-record-a;
  bound-sequence steps D, E and F). **This is a recording session, not
  a validation session.** No Sentinel checker-agent model call, no
  Haiku or Sonnet call, no manual Sentinel judgment call, and no
  Phase-3 gate, re-gate, eval, scorer or validation execution occurred
  here; `scripts/run_phase3_dev_gate.py` was not invoked. The
  validation itself ran earlier, under step E, and was verified
  earlier, under step F; this entry records that completed result.
  **Step D (external pin).** The Stage-2 validation SHA was pinned
  before execution, outside this repository, in the private operations
  OS annotation for this work item: commit
  `bd41f211905288e143746f2237ff02a4cf85790a`.
  **Step E (execution).** The one authorized ADR-0009 prospective
  validation cycle executed 2026-08-21/22 (window
  2026-08-21T23:13Z–23:40Z) under the §5 preflight at validated source
  commit `54f5ce3d0e066417104b47fecbc49d05b5303859`, with
  `source_commit`, `required_source_sha` and `attested_source_sha` all
  equal to it. Model `claude-haiku-4-5-20251001`, auth mode
  `operator-subscription-oauth-assumed`, judgment mode `agent`. Run 1
  `r-cce0280d1a824ca6a12ac8faf42a30e1` and run 2
  `r-e68b8878b62b453eaf6cf5fe2544a6bb`, both COMPLETED, 80/80 tasks
  terminal and all DONE in each.
  **Step F (independent verification).** Disposition **PASS** under
  ADR-0009 §5: `C = 47 > 0` — independently reconstructed from the
  persisted SQLite ledger — together with a complete, independently
  verified PASS. The one authorized cycle is **consumed and complete**;
  ADR-0009's A–F bound sequence is complete.
  **Scoring (SYNTHETIC fixture bed, REAL model calls).** 60 frozen
  positives, 60 emitted, 60 true positives, 0 false positives, 0
  misses; pooled precision 60/60 = 1.0000; pooled recall 60/60 =
  1.0000; per-class recall 10/10 on all six classes (broken-link,
  missing-required-file, missing-synthetic-label, number-mismatch,
  readme-structure, stale-STATE-marker); clean false flags 0/166. All
  four frozen invariants PASS (`every_task_terminal`,
  `zero_lost_tasks`, `idempotent_rerun`,
  `dedup_correct_on_doubled_fixture_run`). Every ADR-0009
  execution-validity predicate PASS, with cross-run logical
  judgment-task coverage 23 == 23 and zero invalid logical histories:
  run 1 carried 24 model-invocation rows over 23 logical model-path
  tasks (22 NORMAL histories plus exactly one BOUNDED_RECOVERY), run 2
  carried 23 rows over 23 logical tasks (23 NORMAL, zero recovered).
  Persistent finding identity held: 60 finding rows carrying 60
  distinct fingerprints, run 2 `findings_new = 0`,
  `findings_still_open = 60`, `findings_resolved = 0`.
  **Bounded recovery (the ADR-0008 seam, exercised for real).** Exactly
  one recovered logical history exists across both runs: `task_key`
  `synthetic-01/EVAL_RESULTS.md::missing-synthetic-label`, ordered
  `agent_calls` ids `[1, 2]`, states FAILED -> COMPLETED. The first row
  carries `sdk_is_error` true, `sdk_subtype` `error_max_budget_usd`,
  `reserved_eur_micros` 150000, `charged_eur_micros` 150000 and
  `tool_attempts` 2, with both persisted tool attempts ACCEPTED at
  ordinals 1 and 2 and a `BREAKER_REFUSED` count of 0; the second row
  is the same run and the same `task_key` at a later `agent_calls.id`,
  state COMPLETED, `reserved_eur_micros` 150000, `charged_eur_micros`
  20634. That is the exact ADR-0009-valid `BOUNDED_RECOVERY` history,
  not an invalid failed call — and the zero `BREAKER_REFUSED` count is
  load-bearing, since a persisted containment refusal would have made
  the recovery INVALID even carrying the same SDK subtype. Charging the
  first row its full reservation is the ADR-0008 §6 accounting path for
  an unrecoverable final estimate; **no overshoot above a reservation
  or above any acceptance ceiling is claimed here, and none is
  established by this evidence.** One observed recovery is not a
  guarantee that every future budget-ceiling event recovers.
  **Cost.** Accounted consumption 645,883 micro-EUR (run 1) and 575,877
  micro-EUR (run 2), 1,221,760 micro-EUR combined, against the declared
  accounted-consumption acceptance ceilings of 750,000 per run and
  1,500,000 across two runs — all PASS. These are acceptance ceilings,
  not guaranteed physical or provider-spend maxima.
  **Auth environment, adjudicated.** `ANTHROPIC_BASE_URL` held the
  value `https://api.anthropic.com` in the orchestration environment
  and was unset for the runner subprocess only, before execution.
  Adjudicated ACCEPTABLE / NON-BLOCKING: the committed fail-closed auth
  control explicitly requires override-capable variables, that one
  included, to be unset before agent mode; no alternate URL was
  substituted, no routing was changed, and no credential, config or
  code file was changed.
  **Raw evidence provenance.** The raw evidence is retained externally
  and locally and is **not committed** to this repository:
  `gate.sqlite3`
  `e965dc9d6311e558631a145d8999b574820ef2ae77c5ab7df1d57f12ffc7a5ec`;
  `gate.jsonl`
  `2585a8922fd88d87b491a893c43882f4569f9c6b8d5bbf2db374bbf0c4b46b8b`;
  `cost_ledger.jsonl`
  `eb636b1738b05fc59af8668a7e1f10a2bf64b8c9f0b30085e863b5dfcb6e9b36`;
  `FINDINGS.md`
  `07a6680646800515f7e348f2063a5bc25e34c0fdcc6cc7c1bd3afe78ea66c175`;
  the runner artifact
  `c3dc96acf42a983d908e75255537754f0797596ec98f3d15586bb1704db80845`;
  the terminal transcript
  `fc692e43cae681b06d907c49dba57a3f86cc6c87d761f63bd93bb7971b090a6f`;
  the SHA-256 manifest
  `c54b08c563d2664dfbbc2e1c70cbc74ea36cfd8ffa4af406a89ab39190e8a6c1`;
  and the transport ZIP
  `8b3d178dcd522cd0efa98ba74c19f801f350f5b853bd2a01a1d5004fc0281a5b`.
  `gate.sqlite3-wal` and `gate.sqlite3-shm` were **ABSENT** after a
  clean SQLite close. Independent Step-F integrity result: the ZIP hash
  matched, every packaged raw file matched its manifest hash, `C` was
  independently reconstructed as 47, the bounded recovery was
  independently reconstructed from durable structured rows, the cost
  rows independently summed to 1,221,760 micro-EUR, and no binding
  disagreement with the runner artifact was found. The committed
  fixed-path `artifacts/phase3_dev_gate.json` is untouched and
  continues to carry the 2026-08-19 re-gate artifact.
  **What changed in this repository.** Exactly six recording and
  truth-repair files: `EVAL_RESULTS.md` (the fourth result appended,
  the three historical records preserved and unrelabeled), `STATE.md`,
  `MODEL_CARD.md`, `THREAT_MODEL.md`, `SPEC.md` and `README.md`. No
  ADR, BLUEPRINT, runtime, test, schema, fixture, answer-key,
  clean-manifest, scorer, threshold, prompt, model, retry, cost,
  identity or lifecycle change; `.publicgate-allow` unmodified; no new
  post; no raw evidence committed. Local verification in this recording
  session: 741 tests passing, 3 skipped (the existing intentional
  Phase-4 stubs only), 91.1% coverage, `pip check` clean, Tier 0 PASS,
  Phase-1 freeze guard PASS, repository publication gate PASS.
  **Disposition.** **Phase 3 CLOSED 2026-08-22. Phase 4 PERMITTED but
  NOT STARTED** — no Phase-4 work has begun. The governing task item
  remains OPEN and is tracked in the private operations OS.
  `SentinelDailyRun` unchanged and still stub-mode. The overall
  production-readiness program remains OPEN: Phases 4–6 and the
  remaining program gates are open, no production or production-ready
  claim is permitted yet, and the status language is unchanged — in
  development toward production-ready. This commit does not self-cite
  its own SHA; that SHA and its exact CI run are recorded in the
  private operations OS annotation for this work item. Next action:
  independent read-only review of this public recording commit and its
  exact-SHA CI, then a separate Phase-4 dispatch.
- 2026-08-22 — Phase-4 loop-safety governance ADOPTED (dispatch
  q77-p4-adr10-adopt-a). `adr/0010-phase4-loop-safety-controls.md`
  created with Status: ADOPTED, owner approved 2026-08-22. Required
  because Phase 4 introduces a supervisory unit no existing machinery
  or ADR covers — ONE bounded-loop execution spanning multiple
  complete Sentinel runs — and because BLUEPRINT §6 P4 freezes only
  the shape (N ≤ 10 under caps, cost and consecutive-failure breakers
  proven by SEEDED faults, failure alerting, published ITERATION_LOG,
  frozen gate) and not the breaker semantics or the loop-wide ceiling.
  Without a prospective freeze an implementer would choose policy
  while writing the gate that judges it. **Owner decisions frozen
  prospectively, before implementation.** (1) **Failure unit.** An
  iteration is failed iff its underlying `RunOutcome.status` is not
  `COMPLETED`; the durable source of truth is `runs.status` / the
  reconstructed `RunOutcome` status, never the exit code alone. A
  dead-lettered task, an individual failed `agent_call`, an ADR-0008
  bounded-recovery first attempt, an HTTP retry and a tool breaker
  event do NOT individually count as loop failures — they stay sub-run
  mechanisms. Only a `COMPLETED` iteration resets the streak; nothing
  else does. Threshold exactly **3** consecutive failed iterations.
  The streak belongs to one bounded-loop execution identified by
  `loop_id`, is durable across a crash and resume of that same loop,
  and never persists across a separately launched loop, a scheduler
  invocation or a later operator session. On trip the loop refuses the
  NEXT iteration; it never aborts a run in progress and creates no
  permanent or global lock. (2) **Loop cost ceiling.**
  `LOOP_BUDGET_EUR_MICROS = 750_000` for Phase 4 — a real pre-start
  loop ceiling, not an after-the-fact acceptance metric. It does not
  replace or raise the existing EUR 0.75 per-run cap, which is
  unchanged, and no Phase-4 flag, config value or environment variable
  may raise it; any operation above it needs a separate dated
  owner-governed decision, which this ADR does not pre-authorize.
  Accounted consumption is reconstructed from durable `CostRow`s
  belonging to the loop's own iteration `run_id`s, never from a
  volatile counter; the next iteration runs at
  `min(existing_per_run_cap, remaining_loop_budget)` propagated
  downward into the existing run/model budget mechanism, and if that
  reduced allowance cannot be enforced the iteration is refused
  fail-closed rather than silently restored to EUR 0.75. Known
  overshoot is accounted in full and never clamped. (3) **Termination
  precedence** frozen in order after a finalized iteration: accounted
  overshoot (`> ceiling`) → `COST_BREAKER_TRIPPED`, nonzero; else
  streak ≥ 3 → `CONSECUTIVE_FAILURE_BREAKER_TRIPPED`, nonzero, which
  outranks normal completion; else iterations ≥ N →
  `COMPLETED_ITERATION_CAP`, exit 0; else, only if another iteration
  would start, `remaining_loop_budget <= 0` → `COST_BREAKER_TRIPPED`,
  refuse, nonzero; otherwise continue. Six boundary consequences are
  written verbatim into the ADR. Recorded explicitly: **post-iteration
  overshoot uses strict `>` while pre-start refusal uses remaining
  `<= 0`** — the asymmetry is intentional and must not later be
  normalized into one operator. (4) **Durable iteration intent.**
  Before an underlying run for iteration k may begin,
  `(loop_id, iteration_index)` must already hold exactly one durably
  persisted `planned_run_id`, generated once, reused, and passed to
  `execute_run` as `RunConfig.run_id`; the four recovery cases (no run
  row yet, terminal row, RUNNING row, terminal row with incomplete
  derived outputs) are frozen, with the invariant that a terminal
  underlying run is never repeated merely because loop bookkeeping
  crashed after run finalization. The SQLite representation is
  deliberately NOT frozen. (5) **Failure alert contract.** No new
  notification channel: a proven breaker/failure alert requires all
  four of a structured ERROR-severity event from the closed logging
  vocabulary, a durable `stop_reason`, a nonzero process exit and a
  labeled `ITERATION_LOG.md` evidence line. No email, Slack, webhook,
  push notification or dashboard; loop operational failures are never
  appended into monitored-surface findings to manufacture an alert.
  (6) **Closed stop-reason vocabulary**: `COMPLETED_ITERATION_CAP`
  (exit 0), `COST_BREAKER_TRIPPED`,
  `CONSECUTIVE_FAILURE_BREAKER_TRIPPED`, `LOOP_ABORTED_ERROR` (all
  fail-closed, nonzero); exactly one is authoritative per loop.
  (7) **Technical gate frozen before implementation and MODEL-FREE** —
  no Haiku, no Sonnet, no provider contact, no real model spend —
  across four legs (normal N = 10; cost breaker with seven sub-cases
  at the fixed 750000 ceiling and zero real provider spend;
  consecutive failure with trip, reset and terminal-boundary cases;
  crash/recovery at the finalization seam), plus a self-check of the
  public derived ITERATION_LOG figures against durable machine state.
  (8) **The technical gate is not Phase-4 closure**: ADR-0003 maps
  `TEST_MATRIX.md`, `INCIDENT_RESPONSE.md`, `MONITORING.md` (draft)
  and `RUNBOOK.md` (draft) to P4, authored from implemented capability
  and evidence and never as placeholders; Phase 4 closes only when the
  gate PASSes AND all four exist and pass their artifact/publication
  controls. Residuals recorded honestly rather than engineered away:
  a very small remaining loop budget can round down to 0.0000 in the
  SDK's four-decimal USD allowance, which is fail-closed and gets no
  invented positive floor; and the 750,000 micro-EUR loop ceiling
  equals exactly one per-run cap, so a real model-calling loop is
  effectively bounded to roughly one full-cost iteration — deliberate
  fail-closed conservatism, consistent with the model-free gate, and
  raisable only by a separate dated owner decision. **No
  implementation landed here**: no runner package, no loop supervisor,
  no loop-state persistence, no breaker code, no crash-recovery code,
  no fault-injection seam, no ITERATION_LOG support, no test, schema,
  runtime, scheduler, BLUEPRINT or SPEC change. Exactly two files
  changed: this ADR and `STATE.md`. This session made no Sentinel
  checker-agent model call, no Haiku or Sonnet call of any kind, and
  ran no gate, re-gate, eval, scorer or Phase-4 loop gate. Preserved
  unchanged: Phase 3 CLOSED; the ADR-0009 cycle PASS, complete and
  consumed; no Phase-3 gate reopening; no fixture, answer-key,
  clean-manifest, scorer, threshold, model or prompt change; no
  ADR-0008 retry-taxonomy change; the EUR 0.75 per-run cap;
  already-proven cross-run dedup, not re-gated; the separately tracked
  Postgres / storage-backend work item, outside Phase 4; no
  SQLAlchemy, Alembic or storage-backend migration; the GitHub Actions
  scheduler migration and the official Sonnet gate, both still Phase
  5; the site-owner collision, still later-program-blocking and not a
  P4-loop implementation blocker. Local verification in this adoption
  session: `python -m pip check` clean, 741 tests passing, 3 skipped
  (the existing intentional Phase-4 stubs only), 91.1% coverage,
  Tier 0 artifact validator PASS, Phase-1 freeze guard PASS,
  repository publication gate PASS. **Phase 4 is now IN PROGRESS
  (2026-08-22) and remains OPEN; implementation has not landed and the
  technical gate has not run.** The governing task item remains OPEN.
  `SentinelDailyRun` unchanged and still stub-mode. No production or
  production-ready claim is made or implied — status stays "in
  development toward production-ready." This commit does not self-cite
  its own SHA; that SHA and its exact CI run are recorded in the
  private operations OS annotation for this work item. Next action:
  exact-SHA CI success, then the separate `q77-p4-runner-a`
  implementation session.

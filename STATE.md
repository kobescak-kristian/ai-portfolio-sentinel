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
**Phase status:** Phase 3 remains **OPEN** after **two** recorded
results, neither of which replaces the other. (1) The designated Haiku
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
cross-run finding-identity defect. Full figures, invariant predicates,
cost evidence and the root-cause record for **both** results:
`EVAL_RESULTS.md`. Note that `artifacts/phase3_dev_gate.json` now
carries the re-gate artifact; the original gate's artifact is preserved
at commit `f9b7ea4e0762161a2519158ec817288308128584`, blob
`2b34e31e13ab8c6dd4e59fd9110e40159b48bcb4`. No fixture, label,
answer-key, scoring, threshold, model, prompt, lifecycle, fingerprint
or evidence-validation change was made after seeing either result. The
one permitted re-gate is now **consumed** and no third gate run is
authorized under the current BLUEPRINT or ADR 0005; remediation design
and any subsequent validation path require a separate owner-governed
decision, and the exact governance form is not decided in these
records. Implementation itself (the caged checker
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
success; 36/36 tests, ubuntu-latest, Python 3.12). Next action: see the
Plan field below — the re-gate is spent, so what follows is a separate
owner-governed decision, not another run.
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
authorized under the current BLUEPRINT or ADR 0005. Next action:
remediation design and any subsequent validation path require a
separate owner-governed decision; the exact governance form is not
decided in this record, and nothing here designs, authorizes or implies
one. Activating the standing scheduled task in agent mode remains a
separate, later decision either way — SentinelDailyRun stays
stub-mode, unedited.
**Open decisions:** rename window CLOSED 2026-08-03 (expired by date;
name kept). Internal path reference removed from the Visibility line
2026-08-03 (this repo's own public-live rule; content unchanged
otherwise). Fixture final counts → quantization integers: RESOLVED
2026-08-04 at the Phase 1 freeze (integers stated in
evals/eval_config.yaml and CI-enforced). Canonical validator does not
recognize decisions/ (two repos
now affected: marketing, this one). Canonical patch belongs to the
queued hook-maintenance batch in the private operations OS — not this
repo's work.

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

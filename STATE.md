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
authorized under ADR 0005; exactly one prospective validation cycle is
separately authorized by `adr/0007-prospective-validation-protocol.md`
via BLUEPRINT §11(i) (ADOPTED 2026-08-20 — governance only, not
implemented, not executed; both historical FAILs stand unrelabeled). The identity
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
re-gate remains **consumed** and **Phase 3 remains OPEN** — passing
model-free tests is not Phase-3 closure. The subsequent validation
path has now been decided: `adr/0007-prospective-validation-protocol.md`
(ADOPTED 2026-08-20) authorizes exactly one prospective validation
cycle via BLUEPRINT §11(i) — governance only; nothing is implemented
or executed under it yet.
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
success; 36/36 tests, ubuntu-latest, Python 3.12). Next action: see the
Plan field below — the prospective validation path for the
now-implemented ADR-0006 correction is governed by `adr/0007`
(ADOPTED 2026-08-20). The re-gate is spent; the one authorized
prospective cycle is a new, separately governed cycle under that ADR's
protocol, not another re-gate.
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
BLUEPRINT §11(i)): exactly one prospective validation cycle is
authorized under that ADR's protocol — governance only, nothing
implemented or executed under it yet. Next action: ADR-0007 sequence
step A2 — exact-SHA CI green on the Stage-1 governance-adoption
commit — then the separate Stage-2 implementation dispatch (step B).
Stage 2 must not begin before the exact Stage-1 SHA is green; steps
C–F follow, and nothing executes before the external Stage-2 SHA pin
and the ADR-0007 §5 preflight. Activating the
standing scheduled task in agent mode remains a separate, later
decision either way — SentinelDailyRun stays stub-mode, unedited.
**Open decisions:** rename window CLOSED 2026-08-03 (expired by date;
name kept). Internal path reference removed from the Visibility line
2026-08-03 (this repo's own public-live rule; content unchanged
otherwise). Fixture final counts → quantization integers: RESOLVED
2026-08-04 at the Phase 1 freeze (integers stated in
evals/eval_config.yaml and CI-enforced). Canonical validator does not
recognize decisions/ (this repo is no longer affected — it holds no
`decisions/` folder since the 2026-08-20 consolidation; the marketing
repo remains affected). Canonical patch belongs to the
queued hook-maintenance batch in the private operations OS — not this
repo's work.

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
governing ARTIFACT_STANDARD decision-record cap of five non-template
records. This is a representation change only: the decision is neither
revoked nor superseded, and no replacement standalone artifact exists.

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

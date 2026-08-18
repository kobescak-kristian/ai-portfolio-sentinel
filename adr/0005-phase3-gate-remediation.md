# 0005 — Phase 3 gate remediation

Status: ADOPTED

Date: 2026-08-18

## Context

The designated Phase-3 Haiku dev gate (dispatch q77-p3-a, source commit
`cf713649bc1aaf31f1494112921d7741493533b0`) ran 2026-08-05 and recorded
an honest FAIL: pooled precision 47/56 = 0.8393 (< 0.90), pooled recall
47/60 = 0.7833 (< 0.85), per-class recall FAIL on `stale-STATE-marker`
(2/10) and `missing-synthetic-label` (5/10). The four deterministic
classes and the clean-false-flag control PASS. The diagnosis (dispatch
q77-p3-diagnose-a, corrected by q77-p3-diagnose-fix-a) established the
exact failure pattern from persisted evidence without any model call or
rerun. BLUEPRINT §5 pre-declares one re-gate maximum (`max_regates: 1`
in `evals/eval_config.yaml`); none has occurred. This ADR is the
separately approved remediation design STATE.md requires before that
one re-gate. Phase 3 and Q-77 remain OPEN.

## Evidence

All figures re-derived from `var/phase3_gate/gate.sqlite3` (SHA-256
`6a66b70b2131343b3e5f65a035ff1ea0607fa278a32175a47bd9b1b6a07ff25f`,
verified unchanged) during the read-only design sessions.

- Judgment classes: 7/20 matched, 13 misses, 9 false positives.
  Budget-related misses: 4. inj-004/inj-005 — one real call
  (`agent_calls` id=1) entered the harness exception path and finalized
  FAILED at its reservation-derived SDK ceiling ($0.0808, from a full
  100,000-µEUR reservation at FX 1.1554 × 0.70 margin), charged its
  full reservation. inj-059/inj-060 — no real call; the shared run
  coordinator reached zero at call id=17. Completed-call misses: 9.
- Call id=17's failure ($0.0241 ceiling) derived from a 29,835-µEUR
  tail reservation (`min(remaining, cap)`) — a run-cap-exhaustion
  artifact, distinct from id=1's true per-call-ceiling breach.
- Run 1 charge decomposition: 15 COMPLETED calls sum 370,165 µEUR
  (avg 24,678; min 11,412; max 54,965); 2 FAILED calls sum 129,835
  (100,000 + 29,835); total exactly 500,000 — the cap. 7 of 24
  judgment tasks received no call. Per-class completed averages:
  README/missing-synthetic-label 37,498 (output 3,480–7,762 tokens —
  the verbosity hotspot); EVAL_RESULTS/missing-synthetic-label 14,312;
  STATE/stale-STATE-marker 20,820.
- Exactly one judgment surface in the frozen bed is absent on disk
  (`synthetic-04/STATE.md`); its deterministically-answerable call
  (id=12, input 10 tokens) charged 27,832 µEUR. Corrected workload:
  23 real calls per run.
- Miss patterns on completed calls: `missing-synthetic-label` —
  first-of-two-positives extraction on multi-positive EVAL_RESULTS
  surfaces (inj-030/041/057) plus 3 README false positives;
  `stale-STATE-marker` — wrong-anchor pattern on synthetic-01/02/03
  only (current-state lines STATE.md:6/:7 cited instead of dated-entry
  lines STATE.md:15/:16; synthetic-05 matched both positives),
  producing 6 of the 9 false positives.
- The current prompt contains no full-scan, continued-enumeration,
  concise-termination, evidence-ordering, or figure-provenance
  instruction (direct read of `agents/checker/prompts.py`).
- `scripts/run_phase3_dev_gate.py` intentionally shares one
  `RunBudgetCoordinator` across both run IDs (its docstring says so);
  run 2 therefore made zero real calls and its idempotent-rerun and
  dedup invariants passed on exhaustion containment, not real-agent
  re-execution.
- Rejected-tool-emission total: UNAVAILABLE_FROM_PERSISTED_EVIDENCE.
  Whether host validation rejected a substantively correct answer:
  NOT DETERMINABLE FROM RETAINED METADATA.
- Observed turns on completed calls 1–3 (limit 10); tool attempts 0–2
  (limit 5). No turn- or tool-ceiling event exists in the evidence.

## Decision drivers

1. Exactly one re-gate remains; the remediation must be evidence-backed
   and must generalize beyond the observed fixtures.
2. The frozen scoring contract (fixtures, labels, answer key, clean
   manifest, scorer, thresholds, model) must not move.
3. The gate must be valid, not merely green: run 2 must genuinely
   exercise the real agent for its invariants to count.
4. A cost-cap change after a cap-related failure must survive an
   explicit gate-shopping analysis or be rejected.
5. Host/validator changes require a demonstrated failure the prompt
   contract cannot control; none exists yet.

## Considered options

**1. Prompt-only.** Addresses the 9 completed-call misses and README
false positives. Leaves: absent-file waste, the exhaustion misses
(point projection 560,220 µEUR for 23 calls already exceeds the
500,000 cap on old-prompt costs), the invalid run-2 qualification.
Surface: `prompts.py`. Cost: none. Regression risk: low.
Gate-shopping risk: none. Strongest case against: forfeits coverage —
the corrected workload likely cannot fit €0.50 even before failures.
Would change the decision: evidence the new prompt cuts net cost ≥11%
with zero failures — unobtainable without spending the re-gate.

**2. Cap increase only.** Addresses the 4 budget-related misses.
Leaves: all 9 completed-call misses, both false-positive patterns,
absent-file waste, run-2 validity. Surface: `config.py`,
`BLUEPRINT.md`. Cost: +€0.25/run. Gate-shopping risk: high — pays for
the same defective execution. Strongest case against: recall cannot
reach threshold on budget alone. Rejected.

**3. Prompt + efficiency fixes at €0.50.** Addresses judgment quality,
absent-file waste, run-2 validity. Leaves: coverage risk — 23 × 24,678
µEUR observed average = 567,594 > 500,000; one failed-call reservation
burn makes the shortfall certain. Surface: as option 4 minus the cap
files. Gate-shopping risk: none. Strongest case against: knowingly
re-running a workload whose point projection exceeds the cap converts
the one re-gate into a coin flip on prompt-driven cost reduction.
Would change the decision: same unobtainable evidence as option 1.

**4. Prompt + efficiency fixes + evidence-backed cap recalibration
(recommended).** Addresses all diagnosed failure modes and gate
validity. Cost: per-run cap €0.50 → €0.75; session bound €1.50.
Surface: the allowlist below. Regression risk: low-moderate, narrowly
scoped; deterministic classes untouched. Gate-shopping risk: bounded
and analyzed below. Strongest case against: see Cost-cap calibration.
Would change the decision: evidence that completed-call costs were
dominated by a defect the prompt provably removes — retained metadata
cannot establish this.

**5. Structural redesign / new model / scorer / fixture / threshold
changes.** Touches frozen surfaces; tests a different system than the
one Phase 3 gates. Rejected.

## Decision

Adopt option 4:

1. Prompt contract rewritten as an ordered algorithm (below).
2. Absent-file deterministic short-circuit (below).
3. Per-run Haiku cap €0.50 → €0.75; per-call reservation ceiling
   100,000 → 150,000 µEUR; SDK safety margin 0.70 unchanged;
   MAX_TURNS 10 and MAX_TOOL_CALLS_PER_CHECK 5 unchanged.
4. Separate per-run coordinators in the gate runner; session bound
   €1.50.
5. No `evidence.py`/tool-contract change: prompt-level ordering is
   untested and no scored failure demonstrates the prompt cannot
   control it. Identified fallback if the re-gate fails on ordering:
   a labeled-fields tool schema (dated-entry / current-state) — a
   tool-contract redesign, out of scope under driver 5.
6. Per-attempt tool-emission persistence: deferred. It did not cause
   the scored failure. Trigger: revisit before any unattended/
   scheduled agent-mode activation of SentinelDailyRun, or earlier if
   another failed real call cannot be diagnosed from retained
   evidence. Remaining limitation until then: substantively-correct-
   but-rejected output stays indistinguishable from no usable output
   for failed calls.

## Cost-cap calibration

Strongest case against: €0.50 was intentionally a hard efficiency
constraint. Raising it after a failure can conceal inefficient agent
behavior and reduces the strength of the original bounded-cost claim.
The two per-call SDK-ceiling failures also show total run budget is
not the only cost constraint; increasing run budget alone does not
guarantee those calls complete.

Why the evidence supports the amendment anyway: the cap predates all
real-agent workload evidence; the corrected 23-call workload's point
projection from persisted completed-call costs is 560,220 µEUR — above
€0.50 with zero failures, zero retries, and absent-file waste already
removed — so €0.50 fails not by inefficiency but by measured workload
size; the inefficiencies the FAIL exposed are separately and directly
remediated (verbosity via the concise-termination contract,
absent-file waste via the deterministic skip), not papered over; and
the per-call-ceiling objection is answered by raising the reservation
ceiling with the run cap, preserving the 20% burn fraction.

Candidates: €0.50 short of projection (−12%); €0.60 +7%, cannot absorb
one failed-call burn; €0.75 +34% (189,780 µEUR headroom), absorbs one
worst-case 150,000-µEUR burn and residual growth, still trips at 59%
of a worst-observed-cost runaway (23 × 54,965 = 1,264,195); €1.00
+79%, exceeds need. Adopted: €0.75 — the smallest cap with defensible
engineering margin, not the smallest that could theoretically pass.

Monthly arithmetic: 30 × €0.75 = €22.50; plus one €5.00 Sonnet
official gate = €27.50 — below the €40 frequency-drop line and the
unchanged €50/month hard ceiling. BLUEPRINT §7's worst-case sentence
updates accordingly.

Not gate shopping because: the cap predated real workload evidence;
the designated run empirically saturated the breaker before workload
coverage completed; the amendment is by ADR before any re-gate;
precision/recall/clean thresholds, model, fixtures, answer key and
scorer remain frozen; the amended cap applies generally to Haiku
iteration/dev/live runs; after adoption it is frozen for the remaining
re-gate; and FAIL at €0.75 does not authorize another increase.
"FAIL → raise again → rerun" is prohibited. No third gate run exists
under the current BLUEPRINT.

## Prompt contract

The system prompt must express, semantically:

1. Scan the complete supplied document before emitting any finding.
2. Identify all genuine defects for the assigned check class.
3. Only after the complete scan, emit exactly one tool call for each
   identified genuine defect.
4. Do not stop merely because the first genuine defect has been
   emitted.
5. Do not emit speculative or duplicate findings.
6. After all identified defects are emitted, terminate without
   unnecessary explanatory prose.
7. If no genuine defect exists, call no tool.

`stale-STATE-marker`: evidence item 1 (primary) must be the dated
entry; evidence item 2 must be the current-state text it contradicts —
aligning the model contract with the frozen positional primary-
location scoring semantics.

`missing-synthetic-label`: the prompt must teach the provenance rule —
a figure genuinely derived from synthetic/labeled evaluation or test
data requires the adjacent synthetic qualifier; a number whose
provenance does not invoke that convention does not. No filename-based
shortcuts; the prompt must not teach "README numbers are clean."

The prior "enumerate everything but be concise" framing is superseded:
conciseness governs only the termination step, never the scan.

## Budget and per-call bounds

- `RUN_BUDGET_EUR_MICROS`: 500_000 → 750_000.
- `MAX_PER_CALL_RESERVE_EUR_MICROS`: 100_000 → 150_000. Rationale: the
  sole true per-call-ceiling failure (id=1) was a multi-positive task
  of the class the new prompt requires to do more emission work; the
  most expensive completed call ($0.0635) was single-emission, leaving
  only 27% margin under the 100k-derived $0.0808 ceiling versus 91%
  under 150k; the failed-call burn fraction (20% of run budget) and
  worst-case failures-to-exhaustion (5) are unchanged. Accepted
  downside: worst-case single-failure waste rises to 150,000 µEUR.
- `SDK_ALLOWANCE_SAFETY_MARGIN`: 0.70 unchanged — it absorbs the SDK's
  post-call budget-check overshoot; no persisted failure implicates
  it, and raising it would buy execution through the back door.
- `MAX_TURNS` 10 and `MAX_TOOL_CALLS_PER_CHECK` 5 unchanged: observed
  maxima 3 turns / 2 attempts; hardest frozen task requires 2 genuine
  emissions, leaving 3 rejected-retry attempts; failure behavior
  remains fail-closed dead-letter; both are runaway-stops, not values
  fitted to the fixtures.

## Absent-file deterministic behavior

When `JudgmentRequest.text is None`, `CagedCheckerStub.judge()` must
return an empty result deterministically — no budget reservation, no
SDK allowance construction, no model call, no `agent_calls` row. The
condition is already established deterministically upstream
(`ConfirmedAbsent` in the three-state fetch contract) and requires no
model judgment. Empirical saving: 27,832 µEUR observed for the one
absent surface in the frozen bed; the corrected workload is 23 real
calls per run.

## Gate-runner coordinator lifecycle

`scripts/run_phase3_dev_gate.py` constructs one `RunBudgetCoordinator`
per designated run ID. Run 1 maximum €0.75; run 2 maximum €0.75;
maximum real-model gate-session spend €1.50. These are three distinct
things: the per-run breaker (€0.75), the gate-session bound (€1.50),
and the monthly lane ceiling (€50, unchanged). Run 2 executes the same
frozen fixture workload under its own budget and must make real model
calls; its idempotent-rerun and dedup invariants count only as
real-agent evidence. A nominal invariant PASS produced by exhaustion
is no longer acceptable for gate closure. The runner's independent
cost cross-check remains a deliberate literal (updated to 750_000
per-run / 1_500_000 total) and must not be imported from config.

## Exact implementation scope

- `adr/0005-phase3-gate-remediation.md` (adoption session).
- `BLUEPRINT.md` — §7 cap line to `iteration/dev/live run ≤ EUR 0.75
  (Haiku)` and the worst-case-month arithmetic; nothing else.
- `agents/checker/config.py` — the two constants above only.
- `agents/checker/prompts.py` — the prompt contract above.
- `agents/checker/harness.py` — the absent-file short-circuit.
- `scripts/run_phase3_dev_gate.py` — two coordinators; cross-check
  literals 750_000 / 1_500_000.
- `tests/test_bounds.py` — constant-dependent cage assertions plus the
  new absent-file short-circuit test.
- `tests/test_failures.py` — resize the one-call-budget FI test to the
  150,000-µEUR reservation ceiling.
- `tests/test_phase3_gate_runner.py` (new) — coordinator-lifecycle and
  cost cross-check assertions only; the gate runner currently has no
  test coverage and the split must not land untested.
- `MODEL_CARD.md`, `THREAT_MODEL.md`, `DATA_CONTRACT.md` — record the
  amended values, the absent-file no-call path, the €1.50 session
  bound.
- `STATE.md` — adoption record; post-run recording.
- `EVAL_RESULTS.md` — post-re-gate recording only.

## Non-goals / frozen surfaces

No change to: `fixtures/`; synthetic labels; `evals/answer_key.jsonl`;
`evals/clean_surfaces.jsonl`; `evals/SCORING.md`; precision/recall/
clean-control thresholds in `evals/eval_config.yaml`; `max_regates`;
model `claude-haiku-4-5-20251001`; deterministic checkers;
`agents/checker/evidence.py`; `agents/checker/tools.py`;
`agents/checker/budget.py` class definition; `checks/judgment/`;
`SentinelDailyRun` activation or command; the €50/month ceiling; the
€5 Sonnet official-gate cap; any Phase-4 implementation. Scoring
semantics do not move to accommodate the remediation.

## Dirty-tree precondition

Before any remediation implementation write: (1) fetch origin;
(2) classify the current `FINDINGS.md` and
`telemetry/cost_ledger.jsonl` differences; (3) if they are legitimate
routine scheduled-run records, preserve them through the repository's
normal separate operational-record path — such a commit is routine
recording, contains no remediation, and does not count as the
remediation implementation commit; (4) if another mechanism owns them,
follow that mechanism; (5) never delete, reset, or absorb them merely
to make the tree clean; (6) remediation implementation begins only
after HEAD = origin/main and the working tree is clean. The
remediation implementation commit contains remediation only.

## Re-gate protocol

One ADR-approved remediation package; one dedicated remediation
implementation commit, public and CI green before any model call;
routine scheduled-output state resolved separately first; model,
fixtures, labels, answer key, clean manifest, scorer, and scoring
thresholds unchanged; one re-gate maximum; fresh evidence directory
and database; the exact remediation source SHA written into the gate
artifact; two fresh run IDs; independent per-run coordinators; run 2
genuinely invokes the real agent; per-run costs recorded separately;
total gate-session cost recorded (≤ €1.50); all existing frozen
thresholds and invariants must PASS; bounds/cost requirements must
PASS; PASS may close Phase 3; FAIL must be recorded honestly; FAIL
does not authorize another prompt/model/budget/fixture/scorer/
threshold change under this ADR; no third gate run under the current
BLUEPRINT.

## Consequences and risks

The two failing judgment classes get an evidence-targeted remediation
without moving the frozen scoring contract; the re-gate becomes a
valid test of real-agent rerun and dedup behavior; per-run spend
ceiling rises 50% with monthly worst-case still at 55% of the lane
ceiling. Risks accepted: the prompt algorithm is untested Haiku
behavior — the stale-STATE ordering instruction in particular carries
the load for 6 of 13 misses, and no pre-gate validation path exists
that does not spend the re-gate; enumeration may raise per-call cost
in partial offset of the conciseness gain; a failed call now burns up
to 150,000 µEUR; three changes land together, so a second FAIL will
need its own diagnosis to attribute cause. These are stated as
probabilities, not certainties; nothing here guarantees a PASS.

## Failure outcome

If the re-gate FAILs, Phase 3 stays OPEN, the FAIL is recorded with
the same honesty discipline as the first, and this ADR authorizes no
further adjustment of any kind. Any subsequent path requires a new
owner-approved ADR under whatever blueprint revision the owner then
chooses. There is no third gate run under the current BLUEPRINT.

## Reopening conditions

This ADR's scope closes on the one re-gate's PASS or FAIL. It reopens
before the re-gate only if evidence surfaces that a decision above
rests on a since-corrected fact (per the diagnosis-correction
precedent on record for this phase).

## Owner approval

Approved by owner — 2026-08-19.

<!-- Diagnostic-only record for dispatch q77-p3-diagnose-a. No model call,
no gate rerun, no fixture/label/answer-key/scoring/threshold/model/prompt
change. This file transcribes artifacts/phase3_gate_diagnosis.json —
every number here is derived from persisted evidence (var/phase3_gate/
gate.sqlite3, gate.jsonl, cost_ledger.jsonl, evals/answer_key.jsonl,
evals/clean_surfaces.jsonl), not recomputed differently. This is a
reconstruction of what happened during the designated Phase-3 Haiku dev
gate honest FAIL already recorded in EVAL_RESULTS.md and STATE.md — it
proposes no remediation and designs no ADR. -->

# PHASE3_GATE_DIAGNOSIS — evidence-only reconstruction

## Scope and non-remediation disclaimer

This record answers "what happened" from persisted evidence only. It
does not propose a fix, does not change any implementation, fixture,
scorer, threshold, or model, and does not rerun the gate. Any remediation
requires a separately approved ADR, not designed here.

## Source identification

| Field | Value |
|---|---|
| Designated gate source commit | `cf713649bc1aaf31f1494112921d7741493533b0` |
| Honest-FAIL record commit (this diagnosis's starting HEAD) | `f9b7ea4e0762161a2519158ec817288308128584` |
| Database | `var/phase3_gate/gate.sqlite3` |
| Database SHA-256 (before and after all querying) | `6a66b70b2131343b3e5f65a035ff1ea0607fa278a32175a47bd9b1b6a07ff25f` (identical before/after — confirmed unchanged) |
| WAL/SHM sidecars present at read time | None |
| Run 1 (primary/scoring) | `r-8f646359aef946178f2863acd75887c4` |
| Run 2 (doubled-fixture) | `r-06dc9ec88f6c4cdc9057dacec88a1a0a` |

## Run-state counts

| Field | Run 1 | Run 2 |
|---|---|---|
| Status | FAILED | FAILED |
| Started (UTC) | 2026-08-05T18:36:48 | 2026-08-05T18:47:25 |
| Finished (UTC) | 2026-08-05T18:47:25 | 2026-08-05T18:47:26 |
| all_check_tasks_total | 80 | 80 |
| judgment_tasks_expected | 24 | 24 |
| judgment_audit_rows_created | 24 | 24 |
| real_model_calls_started | 17 | 0 |
| calls_completed_successfully | 15 | 0 |
| calls_failed | 2 | 0 |
| calls_exhausted | 7 | 24 |
| calls_rejected_before_model_execution | 7 | 24 |
| dead_letter_tasks (all classes) | 9 | 24 |
| input_tokens | 24,487 | 0 |
| output_tokens | 43,624 | 0 |
| recoverable USD cost estimate (sum of completed-call `usd_cost_estimate`) | 0.427679 | 0 |
| charged_eur_micros | 500,000 | 0 |
| final_remaining_budget_eur_micros | 0 | 0 |
| any_real_model_call_occurred | **YES** | **NO** |

**Run 2 explicit statement:** all 24 judgment tasks in run 2 were
EXHAUSTED, and zero real model calls occurred in run 2. Rejection text
on every run-2 judgment row: `run budget exhausted: 500000 EUR-micros
total, 500000 charged, 0 reserved` — identical to the text on run 1's
own post-exhaustion rows, confirming the coordinator's budget pool is
shared across both designated run IDs by the landed gate runner design.

Reserved-EUR note: summing the per-call `reserved_eur_micros` column
across run 1's 24 rows gives 1,539,976, but this is not a distinct
"total reserved" figure — each call's reservation is bounded by whatever
remained in the shared pool at that moment (row id=15 reserved exactly
62,938 micro-EUR, which equals 500,000 minus the cumulative charged
total through row id=14) and converts in full to a charge on completion
or failure. The persisted end-of-run figure is 0 reserved (from the
exhaustion rejection text itself).

## Execution order

`agent_calls.id` (ascending) was cross-checked against `started_at_utc`
for all 24 run-1 judgment rows. For the 17 rows with a real model call
(ids 1–17), id order exactly equals `started_at_utc` order — no ties,
schema-proven. Ids 18–24 (all EXHAUSTED) share one identical
`started_at_utc`/`finished_at_utc` (2026-08-05T18:47:25Z), the moment
the coordinator detected zero remaining budget; no real call was
dispatched for any of them, so their relative order among each other is
**UNAVAILABLE_FROM_PERSISTED_EVIDENCE** — `id` is used only as a stable
tie-breaker for listing, not proof of a dispatch sequence.

## Ordered task-level table — run 1 (24 judgment-class rows)

| # | Surface | Class | Audit state | Real call? | Tool attempts | Accepted findings | Charged (µEUR) | Outcome |
|---|---|---|---|---|---|---|---|---|
| 1 | synthetic-01/EVAL_RESULTS.md | missing-synthetic-label | FAILED | yes | 1 | 0 | 100,000 | missed inj-004, inj-005 (call failed: "Reached maximum budget ($0.0808)") |
| 2 | synthetic-01/README.md | missing-synthetic-label | COMPLETED | yes | 0 | 0 | 25,428 | no positive expected — correctly silent |
| 3 | synthetic-01/STATE.md | stale-STATE-marker | COMPLETED | yes | 2 | 2 | 19,893 | missed inj-011, inj-012; emitted 2 FPs at STATE.md:6/:7 |
| 4 | synthetic-02/EVAL_RESULTS.md | missing-synthetic-label | COMPLETED | yes | 1 | 1 | 12,081 | matched inj-017 (TP) |
| 5 | synthetic-02/README.md | missing-synthetic-label | COMPLETED | yes | 1 | 1 | 43,924 | no positive expected — emitted 1 FP at README.md:22 |
| 6 | synthetic-02/STATE.md | stale-STATE-marker | COMPLETED | yes | 2 | 2 | 31,234 | missed inj-023, inj-024; emitted 2 FPs at STATE.md:6/:7 |
| 7 | synthetic-03/EVAL_RESULTS.md | missing-synthetic-label | COMPLETED | yes | 1 | 1 | 17,353 | matched inj-029 (TP); missed inj-030 |
| 8 | synthetic-03/README.md | missing-synthetic-label | COMPLETED | yes | 0 | 0 | 17,624 | no positive expected — correctly silent |
| 9 | synthetic-03/STATE.md | stale-STATE-marker | COMPLETED | yes | 2 | 2 | 16,419 | missed inj-035, inj-036; emitted 2 FPs at STATE.md:6/:7 |
| 10 | synthetic-04/EVAL_RESULTS.md | missing-synthetic-label | COMPLETED | yes | 1 | 1 | 13,348 | matched inj-040 (TP); missed inj-041 |
| 11 | synthetic-04/README.md | missing-synthetic-label | COMPLETED | yes | 1 | 1 | 54,965 | no positive expected — emitted 1 FP at README.md:39 (matches clean-162) |
| 12 | synthetic-04/STATE.md | stale-STATE-marker | COMPLETED | yes | 0 | 0 | 27,832 | file does not exist (already flagged by missing-required-file) — correctly silent |
| 13 | synthetic-05/EVAL_RESULTS.md | missing-synthetic-label | COMPLETED | yes | 1 | 1 | 11,412 | matched inj-049 (TP) |
| 14 | synthetic-05/README.md | missing-synthetic-label | COMPLETED | yes | 1 | 1 | 45,549 | no positive expected — emitted 1 FP at README.md:17 |
| 15 | synthetic-05/STATE.md | stale-STATE-marker | COMPLETED | yes | 2 | 2 | 15,735 | matched inj-051, inj-052 (both TP) |
| 16 | synthetic-06/EVAL_RESULTS.md | missing-synthetic-label | COMPLETED | yes | 1 | 1 | 17,368 | matched inj-056 (TP); missed inj-057 |
| 17 | synthetic-06/README.md | missing-synthetic-label | FAILED | yes | 1 | 0 | 29,835 | no positive expected — call failed ("Reached maximum budget ($0.0241)"); **this charge brought run 1's cumulative total to exactly the 500,000-µEUR cap** |
| 18 | synthetic-06/STATE.md | stale-STATE-marker | EXHAUSTED | no | 0 | 0 | 0 | missed inj-059, inj-060 (budget exhausted, no call) |
| 19 | synthetic-07/EVAL_RESULTS.md | missing-synthetic-label | EXHAUSTED | no | 0 | 0 | 0 | no positive expected — exhausted before any call |
| 20 | synthetic-07/README.md | missing-synthetic-label | EXHAUSTED | no | 0 | 0 | 0 | no positive expected — exhausted before any call |
| 21 | synthetic-07/STATE.md | stale-STATE-marker | EXHAUSTED | no | 0 | 0 | 0 | no positive expected — exhausted before any call |
| 22 | synthetic-08/EVAL_RESULTS.md | missing-synthetic-label | EXHAUSTED | no | 0 | 0 | 0 | no positive expected — exhausted before any call |
| 23 | synthetic-08/README.md | missing-synthetic-label | EXHAUSTED | no | 0 | 0 | 0 | no positive expected — exhausted before any call |
| 24 | synthetic-08/STATE.md | stale-STATE-marker | EXHAUSTED | no | 0 | 0 | 0 | no positive expected — exhausted before any call |

Full per-row detail (tokens, USD estimate, exact fingerprints, task keys)
is in `artifacts/phase3_gate_diagnosis.json` → `task_level_rows_run1`.

## Positive disposition (20 frozen positives, 2 judgment classes)

**`missing-synthetic-label` — 5/10 matched:**

| Injection | Surface:location | Classification |
|---|---|---|
| inj-004 | synthetic-01/EVAL_RESULTS.md:12 | MISSED_CALL_FAILED |
| inj-005 | synthetic-01/EVAL_RESULTS.md:13 | MISSED_CALL_FAILED |
| inj-017 | synthetic-02/EVAL_RESULTS.md:14 | MATCHED_TP_AFTER_COMPLETED_CALL |
| inj-029 | synthetic-03/EVAL_RESULTS.md:12 | MATCHED_TP_AFTER_COMPLETED_CALL |
| inj-030 | synthetic-03/EVAL_RESULTS.md:13 | MISSED_AFTER_COMPLETED_CALL |
| inj-040 | synthetic-04/EVAL_RESULTS.md:12 | MATCHED_TP_AFTER_COMPLETED_CALL |
| inj-041 | synthetic-04/EVAL_RESULTS.md:13 | MISSED_AFTER_COMPLETED_CALL |
| inj-049 | synthetic-05/EVAL_RESULTS.md:14 | MATCHED_TP_AFTER_COMPLETED_CALL |
| inj-056 | synthetic-06/EVAL_RESULTS.md:12 | MATCHED_TP_AFTER_COMPLETED_CALL |
| inj-057 | synthetic-06/EVAL_RESULTS.md:13 | MISSED_AFTER_COMPLETED_CALL |

**`stale-STATE-marker` — 2/10 matched:**

| Injection | Surface:location | Classification |
|---|---|---|
| inj-011 | synthetic-01/STATE.md:15 | MISSED_AFTER_COMPLETED_CALL |
| inj-012 | synthetic-01/STATE.md:16 | MISSED_AFTER_COMPLETED_CALL |
| inj-023 | synthetic-02/STATE.md:15 | MISSED_AFTER_COMPLETED_CALL |
| inj-024 | synthetic-02/STATE.md:16 | MISSED_AFTER_COMPLETED_CALL |
| inj-035 | synthetic-03/STATE.md:15 | MISSED_AFTER_COMPLETED_CALL |
| inj-036 | synthetic-03/STATE.md:16 | MISSED_AFTER_COMPLETED_CALL |
| inj-051 | synthetic-05/STATE.md:15 | MATCHED_TP_AFTER_COMPLETED_CALL |
| inj-052 | synthetic-05/STATE.md:16 | MATCHED_TP_AFTER_COMPLETED_CALL |
| inj-059 | synthetic-06/STATE.md:15 | MISSED_BUDGET_EXHAUSTED_NO_MODEL_CALL |
| inj-060 | synthetic-06/STATE.md:16 | MISSED_BUDGET_EXHAUSTED_NO_MODEL_CALL |

Full per-row evidence (matched fingerprints, supporting agent_calls row
ids) is in `artifacts/phase3_gate_diagnosis.json` → `positive_dispositions`.

## False-positive disposition (9 total)

| Fingerprint (short) | Surface:location | Class | Manifest match |
|---|---|---|---|
| 908156b3d53d | synthetic-01/STATE.md:6 | stale-STATE-marker | off-manifest |
| b30c9ab58c7e | synthetic-01/STATE.md:7 | stale-STATE-marker | off-manifest |
| 9eedc8767812 | synthetic-02/README.md:22 | missing-synthetic-label | off-manifest |
| 33b215cf0f27 | synthetic-02/STATE.md:6 | stale-STATE-marker | off-manifest |
| 97781a92121d | synthetic-02/STATE.md:7 | stale-STATE-marker | off-manifest |
| c2631bd01bac | synthetic-03/STATE.md:6 | stale-STATE-marker | off-manifest |
| 8614ef2cc5f7 | synthetic-03/STATE.md:7 | stale-STATE-marker | off-manifest |
| **020885314152** | **synthetic-04/README.md:39** | missing-synthetic-label | **matches clean-162** (the sole `clean_flagged: 1/166`) |
| 3d3c7740213d | synthetic-05/README.md:17 | missing-synthetic-label | off-manifest |

`false_positives` (9) and `clean_flagged` (1/166) are two different
manifests and must not be conflated: `false_positives` = findings that
do not exactly tuple-match any of the 60 answer-key positives;
`clean_flagged` = the subset of those that also exactly tuple-match one
of the 166 frozen clean_surfaces.jsonl rows. Only 1 of the 9 meets that
stricter bar; the other 8 are off-manifest — neither a registered
positive nor a registered clean control. This diagnosis does not infer
whether those 8 are substantively correct or incorrect beyond that fact;
their duplicate/wrong-location/wrong-class/unsupported-emission
sub-classification is **UNAVAILABLE_FROM_PERSISTED_EVIDENCE**.

By class: missing-synthetic-label 3 FPs, stale-STATE-marker 6 FPs.

## Validator and tool behavior

- Total tool attempts (run 1, judgment classes): 18
- `agent_calls.accepted` is a call-level 0/1 flag, not a per-finding
  count — cross-checked against the `findings` table: 4 accepted=1 calls
  (ids 3, 6, 9, 15, all `stale-STATE-marker`) each produced 2 findings;
  the other 8 accepted=1 calls (all `missing-synthetic-label`) each
  produced exactly 1. 4×2 + 8×1 = 16, matching the `findings` table
  exactly.
- Failed calls with at least one tool attempt and no persisted finding:
  2 — agent_calls id=1 and id=17. Both failed at the **call level** with
  an SDK maximum-budget exception (`"Reached maximum budget ($0.0808)"`
  and `"Reached maximum budget ($0.0241)"` respectively). Per
  `agents/checker/harness.py`, on any SDK/transport exception the call
  is finalized with `accepted=False` unconditionally — this is a
  call-level finalization field, not a record of whether the individual
  tool attempt itself was host-accepted, host-rejected,
  duplicate-suppressed, or never reached validation at all; no per-attempt
  outcome is persisted anywhere for these two calls. **Rejected tool
  emissions total: UNAVAILABLE_FROM_PERSISTED_EVIDENCE.** Per-attempt
  acceptance or rejection for these two failed calls:
  **UNAVAILABLE_FROM_PERSISTED_EVIDENCE.** No evidence proves either
  tool attempt was rejected by host content validation.
- Tool-call circuit-breaker events: **0**, confirmed — `gate.jsonl` (328
  lines) contains only event types `cost.row_appended`,
  `report.appended`, `run.failed`, `run.started`, `task.claimed`,
  `task.dead_letter`, `task.done`; no circuit-breaker event type or
  string match exists anywhere in the file. This is a confirmed zero,
  not an unknown.
- Completed calls with no accepted finding: 3 (ids 2, 8, 12), all with
  `tool_attempts=0` — legitimately silent (none of these 3 surfaces has
  an answer-key positive for its check class; id=12's surface,
  synthetic-04/STATE.md, does not exist as a file at all, already
  flagged separately by `missing-required-file`).
- Calls with zero tool attempts (run 1, judgment classes): 10 (3
  COMPLETED-but-silent + 7 EXHAUSTED-before-dispatch).

**Was a substantively correct model answer rejected by host-side
validation?** **NOT DETERMINABLE FROM RETAINED METADATA.** No
`agent_calls` row shows `tool_attempts>=1` with `accepted=0` for any
reason other than a budget-cap exception. No table or log persists the
model's raw free-text output, reasoning, or attempted tool-call payload
— there is no message/transcript/answer column anywhere in the schema.

## Exhaustion attribution and budget-sufficiency finding

**Did run 1 have enough budget for every required judgment task to
receive a real model call? `NO`.** 7 of 24 judgment tasks (ids 18–24)
were rejected before any model call, with rejection text `run budget
exhausted: 500000 EUR-micros total, 500000 charged, 0 reserved`.

The 13 total misses split into three separately-proven categories:

| Category | Count | Positive IDs |
|---|---|---|
| MISSED_BUDGET_EXHAUSTED_NO_MODEL_CALL | 2 | inj-059, inj-060 |
| MISSED_CALL_FAILED | 2 | inj-004, inj-005 |
| MISSED_AFTER_COMPLETED_CALL (not budget-related) | 9 | inj-011, inj-012, inj-023, inj-024, inj-030, inj-035, inj-036, inj-041, inj-057 |

**4 misses are budget-related in total. There is one shared run-budget
architecture** (`agents/checker/budget.py`'s `RunBudgetCoordinator`: one
coordinator owns the entire run's EUR budget; each call reserves a
bounded slice of that same shared pool via `reserve()`, capped at
`MAX_PER_CALL_RESERVE_EUR_MICROS`; the SDK-facing `max_budget_usd`
ceiling passed to `ClaudeAgentOptions` is derived from that call's
reservation and the resolved FX rate; an unresolved/SDK-failed call is
charged its full reservation via `commit_unresolved`) **with two
distinct failure modes within it — do not describe them as separate
budget pools:**
1. **Reservation-derived SDK-ceiling failure** (2 misses, inj-004/005):
   agent_calls id=1's real model call reached the SDK `max_budget_usd`
   ceiling that was itself derived from that call's reservation out of
   the shared pool, and failed before completing; proven by the literal
   rejection text `"Reached maximum budget ($0.0808)"`.
2. **Shared-budget-zero no-call miss** (2 misses, inj-059/060): by the
   time agent_calls id=18 was reached, the shared coordinator's
   remaining balance was zero, so the task was rejected before any
   model call; proven by the literal rejection text on agent_calls
   id=18 (`"run budget exhausted: 500000 EUR-micros total, 500000
   charged, 0 reserved"`).

Permitted framing: two distinct failure modes within one shared
run-budget architecture — not separate pools, not unrelated budgets.

The other 9 misses occurred after a call **completed successfully** and
are not budget-related at all — a model recall/extraction gap. The
observed pattern: every completed `EVAL_RESULTS.md` fixture with two
injected positives emitted only the first. Separately, in each of the
three completed `stale-STATE-marker` fixtures that missed its frozen
positives — synthetic-01, synthetic-02, and synthetic-03 — the model
emitted two findings at STATE.md:6 and STATE.md:7 rather than matching
the frozen positives at STATE.md:15 and STATE.md:16. This wrong-anchor
pattern does **not** apply to synthetic-05, whose completed call matched
both of its frozen positives (inj-051, inj-052) correctly.

**Budget boundary, precisely:** agent_calls id=17
(`synthetic-06/README.md::missing-synthetic-label`) is both the last
call that began with available budget (reserved 29,835 µEUR) and the
call that consumed the final available amount (charged 29,835 µEUR,
bringing run 1's cumulative charged total to exactly 500,000 — the cap).
Its final charge used the **reserved amount in full**, not a metered
recoverable actual-usage figure — the call failed before completion, so
`sdk_turns`/`input_tokens`/`output_tokens`/`usd_cost_estimate` are all
null on that row (the same pattern holds for id=1, the only other FAILED
row: charged exactly equals reserved, all metering fields null).

Missed positives attributable with certainty to run-level exhaustion: 2.
Misses not attributable to run-level exhaustion: 11 (2 per-call-budget
misses + 9 completed-call misses).

## Run-2 qualification

The coordinator was intentionally shared across both run IDs by the
landed gate runner. Run 1 consumed the complete shared allowance. Run 2
made zero real model calls. Run 2 therefore proves safe exhaustion
containment and no false resolution — it does **not** prove real-agent
rerun idempotency or real-agent dedup consistency (restating
`EVAL_RESULTS.md`'s existing qualification, not loosening it). Persisted
code and audit evidence examined in this diagnosis show no behavior
different from the landed design; this is not accidental state leakage.

## Pen verification

The owner's no-collision attestation was received for this dispatch. No
in-repo lock or PEN mechanism exists (no `PEN.md`, no `.lock` files in
this repository — pen discipline is external governance). This
diagnosis does **not** claim to have independently verified the absence
of any external concurrent session; no such mechanism is observable from
within this repository.

## Evidence limitations

- Host-side validation rejection of a substantively correct answer: **NOT
  DETERMINABLE FROM RETAINED METADATA** (no raw model text is persisted
  anywhere).
- Actual dispatch order among the 7 simultaneously-timestamped EXHAUSTED
  rows (ids 18–24): **UNAVAILABLE_FROM_PERSISTED_EVIDENCE**.
- Sub-classification (duplicate / wrong-location / wrong-class /
  unsupported-emission) of the 8 off-manifest false positives:
  **UNAVAILABLE_FROM_PERSISTED_EVIDENCE**.

## Diagnosis result: COMPLETE

Every required diagnostic question is either answered directly from
persisted evidence with exact supporting rows, or explicitly marked
unavailable/not-determinable, and every mandated reconciliation (TP/FP/
miss counts, per-class recall, token and cost totals) closes exactly
against the original Phase-3 gate artifact —
`artifacts/phase3_dev_gate.json` as committed at
`f9b7ea4e0762161a2519158ec817288308128584`, blob
`2b34e31e13ab8c6dd4e59fd9110e40159b48bcb4`; that working-tree path now
carries the 2026-08-19 re-gate artifact — and `EVAL_RESULTS.md`
(ORIGINAL DESIGNATED GATE section) with zero
unexplained discrepancy. The presence of the unknowns listed above does
not block any mandated reconciliation from closing.

## Facts the remediation ADR must account for

- The recall shortfall in both judgment classes is not one failure mode:
  `missing-synthetic-label`'s 5 non-exhaustion misses are all a
  "first-of-two-positives-in-one-file" extraction pattern (the model
  found the first figure lacking a label, never the second, in every
  multi-positive `EVAL_RESULTS.md` fixture it completed); `stale-STATE-
  marker`'s 6 non-exhaustion misses are all a wrong-anchor pattern (the
  model consistently flagged the "current-state" lines, STATE.md:6/:7,
  instead of the "dated entry" lines, STATE.md:15/:16, that the answer
  key's location convention requires) — this produced 6 of the 9 total
  false positives as a direct side effect.
- 2 of 20 frozen positives (inj-059, inj-060) never received a model
  call because the shared run-budget coordinator's remaining balance was
  zero by that point in dispatch order.
- 2 of 20 frozen positives (inj-004, inj-005) never received a completed
  call because their call reached the SDK maximum-budget ceiling, which
  is itself derived from that call's reservation out of the same shared
  coordinator — one shared run-budget architecture, two distinct failure
  modes within it, not two separate pools.
- Both failure modes are proven by exact persisted rejection text;
  neither is inferred.
- The two failed calls (id=1, id=17) each had one tool attempt and no
  persisted finding, but the individual tool-attempt acceptance/rejection
  outcome is unavailable from persisted evidence — `accepted=False` on
  these rows is a call-level finalization value set unconditionally on
  any SDK exception, not a record of what happened to that specific
  attempt.
- 1 of 9 false positives (synthetic-04/README.md:39) matches a
  registered frozen clean unit (clean-162); the other 8 are off-manifest
  and their substantive correctness is not determinable from this
  diagnosis's evidence.
- Whether host-side validation ever rejected a substantively correct
  model answer is not determinable from retained metadata — no schema
  anywhere persists raw model text, reasoning, or tool-call payloads.
- Run 2's 24/24 zero-real-call result demonstrates the shared-budget
  exhaustion path is safe (no false resolution, no silent pass) but does
  not exercise or validate real-agent rerun idempotency.
- Phase 3 remains OPEN. Q-77 remains OPEN.

<!-- Records the designated Phase-3 Haiku dev gate (BLUEPRINT §6 P3;
dispatch q77-p3-a). REAL DATA: real Haiku model calls under the
operator's subscription authentication, scored against the frozen
SYNTHETIC fixture bed (fixtures/, evals/) and its frozen answer key.
Status: in development toward production-ready. No production claim
is made in this document. This file transcribes
artifacts/phase3_dev_gate.json verbatim — no figure here is
recomputed or restated differently from that file. -->

# EVAL_RESULTS — Phase 3 designated Haiku dev gate

## Result: FAIL

Phase 3 remains **OPEN**. Per the binding gate discipline (dispatch
q77-p3-a): no fixture, label, answer-key, scoring, threshold, model,
or prompt change and rerun in this session. This is the honest,
recorded result of the one designated gate run.

## Identification

| Field | Value |
|---|---|
| Source commit | `cf713649bc1aaf31f1494112921d7741493533b0` |
| Model | `claude-haiku-4-5-20251001` |
| Auth mode | `operator-subscription-oauth-assumed` (subscription OAuth; not API-key billing — `cost_eur_micros` below is estimated model-equivalent consumption, never a literal invoice) |
| Judgment mode | `agent` (explicit) |
| Run 1 (primary/scoring pass) | `r-8f646359aef946178f2863acd75887c4` |
| Run 2 (doubled-fixture pass) | `r-06dc9ec88f6c4cdc9057dacec88a1a0a` |

## Metrics (against the frozen answer key, 60 positives / 166 clean units)

| Metric | Result | Threshold | Outcome |
|---|---|---|---|
| Pooled precision | 47/56 = 0.8393 | ≥ 0.90 | **FAIL** |
| Pooled recall | 47/60 = 0.7833 | ≥ 0.85 | **FAIL** |
| Per-class recall — broken-link | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — missing-required-file | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — missing-synthetic-label | 5/10 = 0.5000 | ≥ 0.80 | **FAIL** |
| Per-class recall — number-mismatch | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — readme-structure | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — stale-STATE-marker | 2/10 = 0.2000 | ≥ 0.80 | **FAIL** |
| Clean false-flag rate | 1/166 flagged | ≤ 16 flagged | PASS |

Emitted findings: 56. True positives: 47. False positives: 9. Misses: 13.

The four deterministic classes (unchanged from Phase 2) hold at
10/10 recall — the shortfall is isolated entirely to the two classes
this phase adds: `stale-STATE-marker` and `missing-synthetic-label`.

## Invariants

| Invariant | Result |
|---|---|
| every_task_terminal | PASS |
| zero_lost_tasks | PASS |
| idempotent_rerun | PASS — **qualified, see limitation below** |
| dedup_correct_on_doubled_fixture_run | PASS — **qualified, see limitation below** |

## Cost

| | Micro-EUR |
|---|---|
| Run 1 charged | 500,000 (the full shared run budget, EUR 0.50) |
| Run 2 charged | 0 |
| **Total charged** | **500,000** |
| Cap | 500,000 |
| Aggregate cost within cap | PASS |

## Documented limitation (flagged by the operator; recorded, not smoothed over)

Run 1 consumed the **entire** shared 500,000-micro-EUR run budget.
Run 2 (the doubled-fixture pass) therefore had **zero** budget
remaining for any judgment task and made **zero** real model calls
(`cost_row2_micros: 0`). Every judgment-class task in run 2 correctly
hit budget exhaustion and dead-lettered rather than silently passing —
`agents/checker/budget.py`'s "no call starts after exhaustion"
guarantee held. Because dead-lettered scopes are excluded from
auto-resolve (existing Phase-2 lifecycle behavior, unchanged), run 1's
findings correctly stayed OPEN and untouched in run 2.

**This is genuine evidence that budget exhaustion is handled safely —
no data loss, no false resolution, no silent pass. It is NOT evidence
that a second real agent pass would reproduce identical judgment
results, because no second real agent pass occurred.** The
`idempotent_rerun` and `dedup_correct_on_doubled_fixture_run`
invariants above must not be cited as successful real-agent-rerun or
real-agent-idempotency evidence without this qualification. The
deterministic classes' own idempotency (all four, unaffected by the
budget) is not subject to this caveat — their tasks in run 2 executed
for real, independent of the judgment-agent budget.

## Miss pattern (recorded only; no cause analysis or remediation designed here)

`stale-STATE-marker` (2/10 recall) and `missing-synthetic-label`
(5/10 recall) both fall well below the 0.80 per-class threshold, and
pooled precision also misses (9 false positives against a 6-false-positive
allowance at 56 emitted). No root-cause analysis, prompt change, or
remediation design is undertaken in this record — per the binding gate
discipline, any subsequent remediation requires a separately approved
ADR.

## Labels

Synthetic fixture bed (`fixtures/`, `evals/`) — **SYNTHETIC**, frozen,
unchanged since `4d46c1d4fc3c4f485a83f44fa54afa6b04b1f541`. The model
calls that scored against it were **REAL** — real Haiku 4.5 calls under
the operator's own subscription authentication, not simulated.

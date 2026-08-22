<!-- Records the Phase-3 Haiku validation results (BLUEPRINT §6 P3):
the original designated gate (dispatch q77-p3-a), the one permitted
re-gate under adr/0005 (recording dispatch
q77-p3-remediation-regate-record-a), the one prospective validation
cycle under adr/0007 (Step-F recording), and the one prospective
validation cycle under adr/0009 (Step-F recording). REAL DATA in all
four: real Haiku model calls under the operator's subscription
authentication, scored against the frozen SYNTHETIC fixture bed
(fixtures/, evals/) and its frozen answer key. Status: in development
toward production-ready. No production claim is made in this document.

Artifact provenance — the ORIGINAL DESIGNATED GATE section below
transcribes artifacts/phase3_dev_gate.json as committed at
f9b7ea4e0762161a2519158ec817288308128584 (blob
2b34e31e13ab8c6dd4e59fd9110e40159b48bcb4). The ONE PERMITTED RE-GATE
section transcribes that same path at its current content, which the
re-gate run overwrote and which remains the committed artifact — the
prospective cycle did NOT overwrite it. The PROSPECTIVE VALIDATION
CYCLE section transcribes the prospective runner's artifact persisted
locally OUTSIDE the repository (see that section's Identification for
the evidence-parent basename and the artifact's SHA-256); that raw
artifact is not committed here because its byte-verbatim machine
output contains absolute local paths prohibited by this repo's
public-live writing rule, and editing evidence to strip them would
break evidence verbatimness. The PROSPECTIVE VALIDATION CYCLE
(ADR-0009) section transcribes that cycle's runner artifact, likewise
persisted locally OUTSIDE the repository for the same reason and
identified by its SHA-256; neither prospective cycle overwrote the
committed fixed path, which still carries the 2026-08-19 re-gate
artifact. No section recomputes or restates any
figure differently from the artifact it transcribes. -->

# EVAL_RESULTS — Phase 3 Haiku dev gate

This file records FOUR results, in order: the original designated
gate (2026-08-05, FAIL), the one permitted re-gate (2026-08-19,
OVERALL FAIL), the one prospective validation cycle under `adr/0007`
(2026-08-20, VALID COMPLETED FAIL), and the one prospective validation
cycle under `adr/0009` (2026-08-22, PASS). None replaces another; each
earlier record below is preserved unchanged.

# ORIGINAL DESIGNATED GATE — 2026-08-05

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

# ONE PERMITTED RE-GATE — 2026-08-19

## Result: OVERALL FAIL

Phase 3 remains **OPEN**. Every scoring threshold and both single-run
invariants PASS; the failure is isolated to the two cross-run
invariants, `idempotent_rerun` and
`dedup_correct_on_doubled_fixture_run`. This is the honest, recorded
result of the one permitted re-gate authorized by
`adr/0005-phase3-gate-remediation.md`. That re-gate is now
**consumed**. No third gate run is authorized under the current
BLUEPRINT or under ADR 0005.

## Identification

| Field | Value |
|---|---|
| Source commit | `c12beee577b929f58cd6f91ff36d048fe955d73f` (the ADR-0005 remediation implementation commit) |
| Model | `claude-haiku-4-5-20251001` |
| Auth mode | `operator-subscription-oauth-assumed` (subscription OAuth; not API-key billing — `cost_eur_micros` below is estimated model-equivalent consumption, never a literal invoice) |
| Judgment mode | `agent` (explicit) |
| Run 1 (primary/scoring pass) | `r-80b91c34a10a4925a62d573a473cfb4d` |
| Run 2 (doubled-fixture pass) | `r-659b534850f945c2bb614f0065eaa6e7` |
| Persisted evidence directory | `var/phase3_regate/` (fresh directory and database per the ADR re-gate protocol; gitignored, retained locally) |

Evidence integrity — SHA-256 of the persisted files, taken before and
after the read-only queries that produced this record, identical both
times:

| File | SHA-256 |
|---|---|
| `gate.sqlite3` | `1e013b5d352fcccb724776748d7575a862aeab923214b49e3419c52024121d16` |
| `gate.jsonl` | `0aaa2c0d0f0983a7eb4e71d8f674319b501d781f4952aff479c245a107da3794` |
| `cost_ledger.jsonl` | `332cad54b503a42df15b56eaecd6cdc5cba07b20a32dee9a6d1fb3e78f2190da` |
| `FINDINGS.md` | `4101ee1dfeb10e2085c05420d0251a5b5083ae146db2d59cc843a2bc26080d42` |

## Metrics (against the frozen answer key, 60 positives / 166 clean units)

| Metric | Result | Threshold | Outcome |
|---|---|---|---|
| Pooled precision | 60/60 = 1.0000 | ≥ 0.90 | PASS |
| Pooled recall | 60/60 = 1.0000 | ≥ 0.85 | PASS |
| Per-class recall — broken-link | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — missing-required-file | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — missing-synthetic-label | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — number-mismatch | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — readme-structure | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — stale-STATE-marker | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Clean false-flag rate | 0/166 flagged | ≤ 16 flagged | PASS |

Emitted findings: 60. True positives: 60. False positives: 0. Misses: 0.

The two classes that failed the original gate — `stale-STATE-marker`
(2/10) and `missing-synthetic-label` (5/10) — both reach 10/10 here.

**Verification status of these scoring figures.** They are transcribed
from `artifacts/phase3_dev_gate.json` as the gate runner wrote it, and
they are corroborated by the shape of the persisted ledger: 60 OPEN
findings first seen in run 1 plus the one run-1 row later resolved,
80/80 terminal tasks per run, and 23 COMPLETED `agent_calls` rows per
run with no FAILED, REJECTED or EXHAUSTED row. They were **not**
independently rescored against `evals/answer_key.jsonl` in this
recording session: reading that path is blocked by an operator-level
deny rule matching the filename, and the recording dispatch forbids any
re-execution of the gate or the scorer. This limitation applies to the
scoring figures only. It does **not** apply to the invariant failure
and its root cause below, which were reconstructed independently from
the persisted gate evidence and the committed source.

## Invariants

| Invariant | Predicate (`scripts/run_phase3_dev_gate.py:310-317`) | Observed | Result |
|---|---|---|---|
| every_task_terminal | `tasks_created == tasks_terminal` in both runs | 80 == 80, 80 == 80 | PASS |
| zero_lost_tasks | both runs created > 0 tasks | 80, 80 | PASS |
| idempotent_rerun | run 2 `findings_new == 0` | `findings_new = 1` | **FAIL** |
| dedup_correct_on_doubled_fixture_run | run 2 `findings_still_open == TP + FP` **and** `findings_resolved == 0` | `59 != 60`; `findings_resolved = 1` | **FAIL** |

Run 2's recorded lifecycle counters: `findings_new = 1`,
`findings_still_open = 59`, `findings_resolved = 1`. Both invariant
failures trace to the same single observation; there is no second,
independent defect behind them.

## Cost

| | Micro-EUR | Cap | Outcome |
|---|---|---|---|
| Run 1 charged | 629,131 | 750,000 per run | PASS |
| Run 2 charged | 636,623 | 750,000 per run | PASS |
| **Total charged** | **1,265,754** | 1,500,000 per gate session | PASS |

Unlike the original gate, run 2 here genuinely exercised the real
agent: 23 COMPLETED model calls, 46 across the session, with zero
FAILED, REJECTED or EXHAUSTED rows in either run. The original gate's
zero-real-call qualification (see the documented limitation in the
section above) therefore does **not** apply to these two invariants.
They failed on real agent behavior, which is exactly what the ADR
re-gate protocol required run 2 to test.

## Root cause of the invariant failure

Reconstructed from the persisted gate evidence (`var/phase3_regate/`)
and the committed source at `c12beee…`. Nothing in this subsection is
inferred from a summary or attributed to any assistant; each step is
either a row in the ledger or a line of committed code, and the hash
chain was recomputed from the committed functions.

**One semantic defect was implicated**, on one line of one fixture:

| Field | Value |
|---|---|
| Surface | `synthetic-05/EVAL_RESULTS.md` |
| Check class | `missing-synthetic-label` |
| Location | `EVAL_RESULTS.md:14` |
| Reason code | `FIGURE_WITHOUT_ADJACENT_SYNTHETIC_LABEL` |
| Frozen source line 14 | `- Coverage: 85.5 percent` |

The two runs cited two different, both valid, verbatim spans of that
one line:

- Run 1 excerpt: `Coverage: 85.5 percent`
- Run 2 excerpt: `- Coverage: 85.5 percent`

Host-side evidence validation accepts both. `agents/checker/evidence.py`
requires only that the excerpt appear verbatim within the cited source
line (`if item.excerpt not in source_line: raise EvidenceRejected`), so
a span and the full line are equally admissible.

**The excerpt then participates in identity.**
`agents/checker/evidence.py` builds
`normalized_content = f"{reason_code}|{primary.excerpt}"`;
`sentinel/lifecycle.py` passes that through
`compute_content_hash(location, normalized_content)` and then
`compute_fingerprint(surface, check_class, content_hash)`. Surface,
check class, location and reason code were identical across the two
runs. Only the model-selected excerpt differed, and that difference
alone produced two distinct fingerprints:

| Excerpt | content_hash | fingerprint | Ledger row |
|---|---|---|---|
| `Coverage: 85.5 percent` | `bc6de62dcb94bead7cb9f953853afac4efe901c8198dfdd31d9d09acc298119e` | `43869a664aa18db41106fb256eecf35b096b5d50dc30c38c7be1e19e8d662b71` | id 48, now RESOLVED |
| `- Coverage: 85.5 percent` | `e4a1ff1d44d223baf43fe9468d47469c669c4ee6f4b6c7e6b524eca5b772d5d4` | `839cd93dbf24bc7898ec220b0c39cea81f020c98df77eebaa37c29e4f3e541d8` | id 61, OPEN |

Both hash pairs were recomputed from the committed
`contracts/schemas.py` functions during this recording session and
reproduce the persisted ledger rows exactly.

**The deterministic lifecycle then behaved correctly for the
fingerprints it was given.** Presented with an unseen fingerprint it
inserted a new finding (`findings_new = 1`); the run-1 identity was not
observed again in run 2, so it was auto-resolved
(`findings_resolved = 1`, `resolved_run_id`
`r-659b534850f945c2bb614f0065eaa6e7`). This is not a lifecycle or dedup
coding error. The identity handed to dedup was unstable across runs.

**Scope: exactly 1 semantic defect, 2 ledger rows.** The other 59 run-1
findings advanced cleanly as still-open, with unchanged fingerprints.

**The vulnerability predates the remediation commit.**
`agents/checker/evidence.py`, `contracts/schemas.py` and
`sentinel/lifecycle.py` are not among the 13 files changed by
`c12beee…`; their identity behavior last changed at `cf71364` or
earlier. The remediation did not introduce it.

**Why it was not observable before.** The original designated gate's
run 2 made zero real model calls (budget exhaustion, recorded above),
so no second set of model-selected excerpts existed to compare against
the first. Nothing here supports a claim that a hypothetical
pre-remediation real run 2 would certainly have failed the same way:
**that counterfactual is not determinable** from any evidence on record.

## Disposition

- Phase 3 remains **OPEN**.
- Q-77 remains **OPEN**.
- The one permitted re-gate is **consumed**.
- **No third gate run is authorized** under the current BLUEPRINT or
  ADR 0005.
- Remediation design and any subsequent validation path require a
  separate owner-governed decision; the exact governance form is not
  decided in this recording session.
- `SentinelDailyRun` is unchanged and remains stub-mode.
- No fixture, label, answer-key, scoring, threshold, model, prompt,
  lifecycle, fingerprint or evidence-validation change was made after
  seeing this result. No Sentinel checker-agent model call, no
  Haiku/Sonnet gate or re-gate call, no manual Sentinel judgment call
  and no additional evaluation execution occurred in this recording
  session.

## Labels

Synthetic fixture bed (`fixtures/`, `evals/`) — **SYNTHETIC**, frozen,
unchanged since `4d46c1d4fc3c4f485a83f44fa54afa6b04b1f541`. The model
calls that scored against it were **REAL** — real Haiku 4.5 calls under
the operator's own subscription authentication, not simulated.

# PROSPECTIVE VALIDATION CYCLE (ADR-0007) — 2026-08-20

## Result: VALID COMPLETED FAIL

Phase 3 remains **OPEN**. This is the §3 disposition of
`adr/0007-prospective-validation-protocol.md`, independently verified
in Step F: `C = 46 > 0` — independently reconstructed as 23
positive-reservation `agent_calls` rows in run 1 plus 23 in run 2 —
and a complete parseable gate result exists that fails binding
conditions. The one authorized prospective cycle is **consumed**, and
the result is **terminal for the current Sentinel-v1 Phase-3
validation lineage**: Phase 3 stays OPEN, Phase 4 is not permitted
under this lineage, no further validation cycle is authorized by
ADR-0007, and Q-77 remains OPEN. This is a real VALID COMPLETED FAIL
— recorded, not relabeled, not waived.

## Identification

| Field | Value |
|---|---|
| Source commit | `8c235af1ba254e9a238a797be558129bc2a82f99` (the ADR-0007 Stage-2 implementation commit) |
| Source pin | `required_source_sha`, `attested_source_sha` and `source_commit` in the gate artifact are all equal to the source commit above; the pre-execution external pin (sequence step D) is recorded in the private operations OS's Q-77 annotation |
| Model | `claude-haiku-4-5-20251001` |
| Auth mode | `operator-subscription-oauth-assumed` (subscription OAuth; not API-key billing — `cost_eur_micros` below is estimated model-equivalent consumption, never a literal invoice) |
| Judgment mode | `agent` (explicit) |
| Run 1 (primary/scoring pass) | `r-e8e27a1133754705ac76fd0f0842c101` (run status FAILED) |
| Run 2 (doubled-fixture pass) | `r-a2ed87b770014722a5f7bd583b9637db` (run status COMPLETED) |
| Persisted evidence parent | fresh local directory outside the repository, basename `prospective-20260820T182647Z-033e8a9b` (fresh per the §5 preflight; retained locally) |

Unlike the two prior results, the raw gate artifact of this cycle is
**not** committed to this repository, and the committed fixed-path
`artifacts/phase3_dev_gate.json` is untouched — it continues to carry
the 2026-08-19 re-gate artifact. The prospective runner wrote its
artifact into the fresh external evidence parent above; that
byte-verbatim machine output contains absolute local paths, which
this repo's public-live writing rule prohibits, and editing evidence
to strip them would break evidence verbatimness. The artifact
therefore stays external and is identified here by its SHA-256:
`9e401356e7682bd8ab07e92f53b7ef034d2dd1edefed35da89d1f21fa95e24bb`.

Evidence integrity — SHA-256 of the persisted files in the evidence
parent, taken before and after the read-only Step-F verification
queries that produced this record, byte-identical both times:

| File | SHA-256 |
|---|---|
| `artifacts/phase3_dev_gate.json` | `9e401356e7682bd8ab07e92f53b7ef034d2dd1edefed35da89d1f21fa95e24bb` |
| `console.log` | `7c0c846089a44ab7bfeb33cc8fadf9d3e3bc51e5862b75418d9e005418e8f6e3` |
| `gate/FINDINGS.md` | `6b430470cf3bee6c531e7b332a2c08d065af058a53fd10ebed849068dbe689bd` |
| `gate/cost_ledger.jsonl` | `d414bff2e46245904b9df7f6263af3757af21f7bca6cae631e3b1532c8192d04` |
| `gate/gate.jsonl` | `61dbd61dc0977c46171794e097eeb059fe7e046a596f8ff0a8d9ea709f3462f6` |
| `gate/gate.sqlite3` | `4e4dc913166ea229a6e63ae5c2978af0e0dacf689d11a84469578293c9c651d3` |

## Metrics (against the frozen answer key, 60 positives / 166 clean units)

| Metric | Result | Threshold | Outcome |
|---|---|---|---|
| Pooled precision | 58/58 = 1.0000 | ≥ 0.90 | PASS |
| Pooled recall | 58/60 = 0.9667 | ≥ 0.85 | PASS |
| Per-class recall — broken-link | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — missing-required-file | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — missing-synthetic-label | 8/10 = 0.8000 | ≥ 0.80 | PASS |
| Per-class recall — number-mismatch | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — readme-structure | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — stale-STATE-marker | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Clean false-flag rate | 0/166 flagged | ≤ 16 flagged | PASS |

Emitted findings: 58. True positives: 58. False positives: 0. Misses: 2.

**Verification status of these scoring figures.** Independent Step-F
reconstruction exactly matched the artifact on every figure above;
artifact/reconstruction disagreements: zero. The two misses are
exactly `inj-004` (`synthetic-01/EVAL_RESULTS.md:12`) and `inj-005`
(`synthetic-01/EVAL_RESULTS.md:13`), both inside the run-1
dead-lettered scope described below. There is no additional
independently observed model-quality miss.

## Execution validity (ADR-0007 §2 — first result recorded under it)

The gate result is complete and parseable, and every scoring
threshold, single-run invariant and cost cap PASSES — but the §2
execution-validity requirement FAILS, independently reconstructed
with zero disagreement against the artifact:

| Predicate | Observed | Result |
|---|---|---|
| run1_completed | run 1 status FAILED | **FAIL** |
| run2_completed | run 2 status COMPLETED | PASS |
| runs_exit_code_zero | run 1 did not exit 0 | **FAIL** |
| zero_failed_tasks | 0 FAILED tasks in both runs | PASS |
| zero_dead_letter_tasks | 1 DEAD_LETTER task in run 1 | **FAIL** |
| all_agent_calls_completed | 22 of 23 run-1 calls COMPLETED | **FAIL** |
| zero_agent_calls_failed | 1 FAILED call in run 1 | **FAIL** |
| zero_agent_calls_rejected | 0 REJECTED | PASS |
| zero_agent_calls_exhausted | 0 EXHAUSTED | PASS |
| zero_agent_calls_reserved | 0 left RESERVED | PASS |
| run2_has_completed_calls | 23 COMPLETED | PASS |
| run2_call_count_equals_run1 | 23 != 22 | **FAIL** |
| source_sha_attested | preflight-verified SHA == required SHA | PASS |

Run tallies: run 1 FAILED — 80 tasks all terminal (79 DONE, 1
DEAD_LETTER), agent calls 22 COMPLETED + 1 FAILED; run 2 COMPLETED —
80 tasks DONE, 23 COMPLETED agent calls, zero FAILED, REJECTED,
EXHAUSTED or RESERVED rows and zero dead-letter tasks.

## Invariants

| Invariant | Predicate (frozen, unchanged) | Observed | Result |
|---|---|---|---|
| every_task_terminal | `tasks_created == tasks_terminal` in both runs | 80 == 80, 80 == 80 | PASS |
| zero_lost_tasks | both runs created > 0 tasks | 80, 80 | PASS |
| idempotent_rerun | run 2 `findings_new == 0` | `findings_new = 2` | **FAIL** |
| dedup_correct_on_doubled_fixture_run | run 2 `findings_still_open == TP + FP` and `findings_resolved == 0` | satisfied — zero spurious resolutions, zero fragmentation | PASS |

Run 2's two new findings are both in the exact run-1 DEAD_LETTER
scope — `synthetic-01/EVAL_RESULTS.md`, check class
`missing-synthetic-label`, locations `EVAL_RESULTS.md:12` and
`EVAL_RESULTS.md:13`. Run 1 had no findings for that scope because
its judgment task did not complete; run 2 completed the same scope
and found both expected defects. `idempotent_rerun` therefore failed
because of the run-1 execution gap, NOT because of another identity
or fingerprint instability.

## Cost

| | Micro-EUR | Cap | Outcome |
|---|---|---|---|
| Run 1 charged | 648,422 | 750,000 per run | PASS |
| Run 2 charged | 493,293 | 750,000 per run | PASS |
| **Total charged** | **1,141,715** | 1,500,000 per gate session | PASS |

All frozen cost caps PASS. The failed run-1 call is conservatively
charged its full 150,000 micro-EUR reservation because its final SDK
usage was not recoverable. The identical scope later completed in
run 2 for 26,583 micro-EUR. The persisted evidence does NOT establish
why the failed call became unusually expensive; no cause is asserted
here beyond the recorded SDK budget-ceiling event.

## Why the gate failed (fail-closed, as designed)

One run-1 Haiku call — `agent_calls` id 1, scope
`synthetic-01/EVAL_RESULTS.md`, check class `missing-synthetic-label`
— failed at the SDK per-call budget ceiling. Persisted error,
verbatim:

    Exception: Claude Code returned an error result: Reached maximum budget ($0.1226)

That call had `reserved_eur_micros = 150000` and
`charged_eur_micros = 150000`. The harness handled the event
fail-closed exactly as designed: FAILED agent call → Inconclusive →
DEAD_LETTER task → run 1 FAILED. The current execution policy does
not tolerate even one such transient incomplete judgment, so the
binding gate correctly failed.

## Identity result (ADR-0006)

The ADR-0006 identity defect did NOT recur. All 58 run-1 findings
that existed were re-observed in run 2 with stable identity. The 60
persisted finding rows carry 60 distinct fingerprints — zero spurious
resolutions, zero identity fragmentation — and
`dedup_correct_on_doubled_fixture_run` PASSED.

**What this record does and does not conclude.** It does NOT record
"there was no system issue." The narrower evidenced conclusion is:
the previous cross-run identity defect did not recur; the frozen
scoring, identity and dedup behavior was correct on the completed
work; the prospective gate failed because one stochastic model call
hit its configured SDK per-call budget ceiling; Sentinel handled that
event fail-closed as designed; the current execution policy does not
tolerate even one such transient incomplete judgment, so the binding
gate correctly failed; and the evidence does not establish why that
individual call consumed unusually high model budget.

## Disposition

- ADR-0007 §3 disposition: **VALID COMPLETED FAIL** (`C = 46 > 0`; a
  complete parseable gate result exists and fails binding conditions).
- The one authorized prospective cycle is **consumed**.
- **Terminal for the current Sentinel-v1 Phase-3 validation lineage.**
- Phase 3 remains **OPEN**. Phase 4 is **not permitted** under this
  lineage. Q-77 remains **OPEN**.
- No further validation cycle is authorized by ADR-0007. Any
  subsequent path requires a new owner-governed decision; none is
  designed or authorized in this recording session.
- `SentinelDailyRun` is unchanged and remains stub-mode.
- No fixture, label, answer-key, scoring, threshold, model, prompt,
  budget, lifecycle, fingerprint or evidence-validation change was
  made after seeing this result. No Sentinel checker-agent model
  call, no Haiku or Sonnet call, no manual Sentinel judgment call and
  no additional evaluation execution occurred in this recording
  session.

## Labels

Synthetic fixture bed (`fixtures/`, `evals/`) — **SYNTHETIC**, frozen,
unchanged since `4d46c1d4fc3c4f485a83f44fa54afa6b04b1f541`. The model
calls that scored against it were **REAL** — real Haiku 4.5 calls under
the operator's own subscription authentication, not simulated.

# PROSPECTIVE VALIDATION CYCLE (ADR-0009) — 2026-08-22

Recorded 2026-08-22 local; execution window 2026-08-21T23:13Z–23:40Z.

## Result: PASS

Phase 3 is **CLOSED**. This is the §5 disposition of
`adr/0009-post-adr0008-phase3-validation-protocol.md`: `C = 47 > 0`
together with a complete, independently verified PASS. The one
authorized ADR-0009 cycle is **consumed and complete**. Phase 4 is
**permitted but NOT STARTED**. The governing task item remains
**OPEN**. `SentinelDailyRun` is unchanged and remains stub-mode.

This closes Phase 3 only. The overall production-readiness program
remains OPEN, Phases 4–6 and the remaining program gates are open, and
the permitted public status is unchanged: **in development toward
production-ready**. No production-ready claim is made here.

## Identification

| Field | Value |
|---|---|
| Validated source commit | `54f5ce3d0e066417104b47fecbc49d05b5303859` |
| Source pin | `source_commit`, `required_source_sha` and `attested_source_sha` in the gate artifact are all equal to the validated source commit above |
| Pre-execution external pin (sequence step D) | `bd41f211905288e143746f2237ff02a4cf85790a`, recorded in the private operations OS's annotation for this work item |
| Model | `claude-haiku-4-5-20251001` |
| Auth mode | `operator-subscription-oauth-assumed` (subscription OAuth; not API-key billing — `cost_eur_micros` below is estimated model-equivalent consumption, never a literal invoice) |
| Judgment mode | `agent` (explicit) |
| Run 1 (primary/scoring pass) | `r-cce0280d1a824ca6a12ac8faf42a30e1` (run status COMPLETED) |
| Run 2 (doubled-fixture pass) | `r-e68b8878b62b453eaf6cf5fe2544a6bb` (run status COMPLETED) |
| Persisted evidence parent | fresh local directories outside the repository, basenames `q77-adr9-step-e-a-20260821T231307Z-gate` and `q77-adr9-step-e-a-20260821T231307Z-artifacts` (fresh per the §5 preflight; retained locally) |
| Transport package | `q77-adr9-step-e-a-evidence.zip` (retained locally, not committed) |

Evidence integrity — SHA-256 of the persisted raw evidence, as verified
in the independent Step-F check:

| File | SHA-256 |
|---|---|
| `gate.sqlite3` | `e965dc9d6311e558631a145d8999b574820ef2ae77c5ab7df1d57f12ffc7a5ec` |
| `gate.sqlite3-wal` | **ABSENT** (clean SQLite close) |
| `gate.sqlite3-shm` | **ABSENT** (clean SQLite close) |
| `gate.jsonl` | `2585a8922fd88d87b491a893c43882f4569f9c6b8d5bbf2db374bbf0c4b46b8b` |
| `cost_ledger.jsonl` | `eb636b1738b05fc59af8668a7e1f10a2bf64b8c9f0b30085e863b5dfcb6e9b36` |
| `FINDINGS.md` | `07a6680646800515f7e348f2063a5bc25e34c0fdcc6cc7c1bd3afe78ea66c175` |
| `phase3_dev_gate.json` (runner artifact) | `c3dc96acf42a983d908e75255537754f0797596ec98f3d15586bb1704db80845` |
| terminal transcript | `fc692e43cae681b06d907c49dba57a3f86cc6c87d761f63bd93bb7971b090a6f` |
| SHA-256 manifest | `c54b08c563d2664dfbbc2e1c70cbc74ea36cfd8ffa4af406a89ab39190e8a6c1` |
| transport ZIP | `8b3d178dcd522cd0efa98ba74c19f801f350f5b853bd2a01a1d5004fc0281a5b` |

The raw evidence is retained **externally and locally; none of it is
committed to this repository** — for the same reason as the ADR-0007
cycle, its byte-verbatim machine output contains absolute local paths
that this repo's public-live writing rule prohibits, and editing
evidence to strip them would break evidence verbatimness. The committed
fixed-path `artifacts/phase3_dev_gate.json` is **untouched** by this
cycle and continues to represent its historical 2026-08-19 re-gate
artifact.

Independent Step-F evidence-integrity result: the ZIP hash matched;
every packaged raw file matched its manifest hash; WAL and SHM were
absent after a clean SQLite close; `C` was independently reconstructed
from SQLite as 47; the bounded recovery was independently reconstructed
from durable structured rows; the cost rows independently sum to
1,221,760 micro-EUR; and no binding disagreement with the runner
artifact was found.

## Metrics (against the frozen answer key, 60 positives / 166 clean units)

| Metric | Result | Threshold | Outcome |
|---|---|---|---|
| Pooled precision | 60/60 = 1.0000 | ≥ 0.90 | PASS |
| Pooled recall | 60/60 = 1.0000 | ≥ 0.85 | PASS |
| Per-class recall — broken-link | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — missing-required-file | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — missing-synthetic-label | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — number-mismatch | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — readme-structure | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Per-class recall — stale-STATE-marker | 10/10 = 1.0000 | ≥ 0.80 | PASS |
| Clean false-flag rate | 0/166 flagged | ≤ 16 flagged | PASS |

Positives total: 60. Emitted findings: 60. True positives: 60. False
positives: 0. Misses: 0.

## Execution validity (ADR-0009 §2 — logical judgment histories)

Every ADR-0009 execution-validity predicate PASSES.

| Run | Status | Tasks | Model-invocation rows | Logical model-path tasks | `agent_calls` | Logical histories |
|---|---|---|---|---|---|---|
| Run 1 | COMPLETED | 80/80 terminal, all DONE | 24 | 23 | 23 COMPLETED, 1 FAILED | 22 NORMAL, 1 BOUNDED_RECOVERY, 0 invalid |
| Run 2 | COMPLETED | 80/80 terminal, all DONE | 23 | 23 | 23 COMPLETED, 0 FAILED | 23 NORMAL, 0 recovered, 0 invalid |

Cross-run logical coverage — compared as distinct model-path logical
`task_key`s, never as raw call rows: **23 == 23, PASS**. Run 1's 24
invocation rows are 23 logical tasks precisely because exactly one
logical task is a two-row bounded recovery.

## The one bounded recovery (ADR-0008 behaviour, ADR-0009 validity)

Exactly one recovered logical history exists across both runs:

| Field | Value |
|---|---|
| `task_key` | `synthetic-01/EVAL_RESULTS.md::missing-synthetic-label` |
| Ordered `agent_calls` ids | `[1, 2]` |
| States | `FAILED` -> `COMPLETED` |

First row — the failed invocation:

| Field | Value |
|---|---|
| `sdk_is_error` | true |
| `sdk_subtype` | `error_max_budget_usd` |
| `reserved_eur_micros` | 150000 |
| `charged_eur_micros` | 150000 |
| `tool_attempts` | 2 |
| Persisted tool attempts | ordinal 1 ACCEPTED, ordinal 2 ACCEPTED |
| `BREAKER_REFUSED` count | 0 |

Second row — the completing invocation: same `run_id`, same `task_key`,
a later `agent_calls.id`, state `COMPLETED`, `reserved_eur_micros`
150000, `charged_eur_micros` 20634.

This is therefore the exact ADR-0009-valid `BOUNDED_RECOVERY` history —
`[FAILED reconstructed as SDK_BUDGET_CEILING, COMPLETED]` — and not an
invalid failed call. The zero `BREAKER_REFUSED` count is load-bearing,
not decoration: ADR-0008's classifier gives local containment
precedence, so a persisted `BREAKER_REFUSED` outcome would have made
this recovery INVALID even while the row still carried the same SDK
subtype, `sdk_is_error` true and a positive reservation. The failed
invocation is charged its full reservation because its final SDK usage
was not recoverable — that is the ADR-0008 §6 accounting path, **not** a
measured overshoot above the reservation.

## Invariants

| Invariant | Result |
|---|---|
| every_task_terminal | PASS |
| zero_lost_tasks | PASS |
| idempotent_rerun | PASS |
| dedup_correct_on_doubled_fixture_run | PASS |

## Persistent finding identity (ADR-0006)

60 persisted finding rows carrying 60 distinct fingerprints. Run 2's
lifecycle counters: `findings_new = 0`, `findings_still_open = 60`,
`findings_resolved = 0` — zero identity fragmentation, zero spurious
resolutions. The ADR-0006 identity correction held across a fully
completed two-run cycle.

## Cost

| | Micro-EUR | Acceptance ceiling | Outcome |
|---|---|---|---|
| Run 1 accounted | 645,883 | 750,000 per run | PASS |
| Run 2 accounted | 575,877 | 750,000 per run | PASS |
| **Total accounted** | **1,221,760** | 1,500,000 across two runs | PASS |

These are **accounted-consumption acceptance ceilings, not guaranteed
physical or provider-spend maxima** (ADR-0008 §7; ADR-0009). No
overshoot above a reservation or above either ceiling is claimed here,
and none is established by this evidence.

## Auth-environment note and its adjudication

`ANTHROPIC_BASE_URL` held the value `https://api.anthropic.com` in the
orchestration environment, and was unset **for the runner subprocess
only** before execution.

Adjudicated **ACCEPTABLE / NON-BLOCKING**: the committed fail-closed
auth control (`agents/checker/auth.py`) explicitly requires
override-capable variables — `ANTHROPIC_BASE_URL` among them — to be
unset before agent mode. No alternate URL was substituted, no routing
was changed, and no credential, config or code file was changed.

## Disposition

- ADR-0009 §5 disposition: **PASS** (`C = 47 > 0`, plus a complete,
  independently verified PASS).
- The one authorized ADR-0009 cycle is **consumed and complete**.
- **Phase 3 is CLOSED** (2026-08-22).
- **Phase 4 is permitted but NOT STARTED.**
- The governing task item remains **OPEN**.
- `SentinelDailyRun` is unchanged and remains stub-mode.
- The overall production-readiness program remains **OPEN**. No
  production-ready claim is made, and the permitted public status is
  unchanged: in development toward production-ready.
- The three historical results above stand exactly as recorded. No
  historical FAIL is relabeled, softened or superseded by this PASS.
- No fixture, label, answer-key, scoring, threshold, model, prompt,
  budget, lifecycle, fingerprint or evidence-validation change was made
  after seeing this result. **No Sentinel checker-agent model call, no
  Haiku or Sonnet call, no manual Sentinel judgment call, and no gate,
  re-gate, eval, scorer or validation execution occurred in this
  recording session.**

## Labels

Synthetic fixture bed (`fixtures/`, `evals/`) — **SYNTHETIC**, frozen,
unchanged since `4d46c1d4fc3c4f485a83f44fa54afa6b04b1f541`. The model
calls that scored against it were **REAL** — real Haiku 4.5 calls under
the operator's own subscription authentication, not simulated.

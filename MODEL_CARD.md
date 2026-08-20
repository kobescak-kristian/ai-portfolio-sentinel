<!-- DRAFT. Lands alongside the Phase-3 capability it describes
(BLUEPRINT §6 P3; adr/0003 P3). Status: in development toward
production-ready. No production claim is made in this document.
The designated Phase-3 gate and the one permitted re-gate have both
run; their measured figures are in EVAL_RESULTS.md. -->

# MODEL_CARD (DRAFT) — ai-portfolio-sentinel caged checker agent

## 1. What this model does

One narrowly-scoped model call per judgment task: given one
document's text (already fetched deterministically by
`checks/judgment/stale_state.py` / `synthetic_label.py`), decide
whether it contains a defect of exactly one check class, and if so,
propose bounded evidence (a closed reason code plus verbatim line/
excerpt citations) through one tool. It does not fetch anything, does
not decide what to check, does not write anything, and does not see
any content beyond the one document it was given.

Two check classes only:
- `stale-STATE-marker`: a dated STATE entry contradicting a
  current-state section elsewhere in the same document.
- `missing-synthetic-label`: a numeric figure lacking its required
  adjacent synthetic-data label.

Every other check class (`broken-link`, `number-mismatch`,
`missing-required-file`, `readme-structure`) stays fully deterministic,
unchanged from Phase 2 — zero model involvement.

## 2. Model

`claude-haiku-4-5-20251001` — the dev-tier model per BLUEPRINT §7
("Caged checker agent (Haiku dev)"). No Sonnet official gate is run or
claimed by this phase; the Sonnet official eval-gate leg is a later,
separate concern (BLUEPRINT §5, §9) and this document makes no claim
about it.

## 3. The cage

Detailed in `THREAT_MODEL.md`; summarized here: no built-in tools, no
inherited settings, no subagents, no skills, exactly one qualified
custom tool (`emit_finding`), a bounded turn count
(`agents/checker/config.py::MAX_TURNS`), an independent tool-call
circuit breaker, and a run-scoped EUR budget (not per-request) derived
into a conservative per-call USD ceiling via a freshly-resolved ECB
reference rate.

### 3a. Adopted bounds (`adr/0005-phase3-gate-remediation.md`, 2026-08-19)

- Haiku per-run cap **EUR 0.75** (`RUN_BUDGET_EUR_MICROS = 750_000`),
  raised from EUR 0.50 on measured-workload evidence, not on a
  post-hoc wish for more headroom. It is the general iteration/dev/
  live Haiku breaker, not a gate-only exception.
- Per-call reservation ceiling **150,000 micro-EUR**
  (`MAX_PER_CALL_RESERVE_EUR_MICROS`), raised from 100,000 — the same
  20% failed-call burn fraction of the run budget as before.
- SDK allowance safety margin **0.70, unchanged**. Deliberately not
  relaxed: raising it would buy execution through the back door.
- `MAX_TURNS` 10 and `MAX_TOOL_CALLS_PER_CHECK` 5, both unchanged.
- **Absent-file deterministic no-call path**: when a judgment
  request's document is confirmed absent (`JudgmentRequest.text is
  None`), `judge()` returns the empty result *before* any budget
  reservation, SDK allowance construction, or model call, and writes
  no `agent_calls` row. Such a surface never enters the model path at
  all; the condition is established deterministically upstream by the
  three-state fetch contract and needs no model judgment.
- The Phase-3 dev gate runs its two designated run IDs under **two
  independent `RunBudgetCoordinator` instances**, one EUR 0.75 breaker
  each, so the maximum real-model spend for a two-run gate session is
  **EUR 1.50**. Run 2 executes the same frozen workload under its own
  budget and must genuinely exercise the agent for its idempotent-rerun
  and dedup invariants to count.

These are the settings the one permitted re-gate ran under. That
re-gate ran 2026-08-19 and recorded an honest **OVERALL FAIL**: every
scoring threshold PASSED (pooled precision 60/60, pooled recall 60/60,
per-class recall 10/10 on all six classes, clean false flags 0/166),
and the failure is isolated to the two cross-run invariants,
`idempotent_rerun` and `dedup_correct_on_doubled_fixture_run` — see
§4a and `EVAL_RESULTS.md`. The re-gate is **consumed**; no third gate
run is authorized under `adr/0005`. The one prospective validation
cycle separately authorized by `adr/0007` / BLUEPRINT §11(i) (adopted
2026-08-20) executed 2026-08-20 under these same bounds and reached
**VALID COMPLETED FAIL** — consumed, terminal for the current
Sentinel-v1 Phase-3 validation lineage; see `EVAL_RESULTS.md`,
prospective section. Phase 3
remains OPEN. This document makes no claim that the bounds above
produce a passing gate.

## 4. Deterministic host canonicalization (evidence contract)

The model never emits a complete, ledger-ready finding. It proposes a
reason code (from a closed, per-class set) and one or two line/excerpt
pairs. `agents/checker/evidence.py` independently validates every
citation against the source document and *deterministically*
constructs `location`, `normalized_content`, and `detail` — the fields
that feed the ledger's dedup fingerprint — rejecting any citation whose
excerpt does not appear verbatim on the cited line.

**The identity defect this exposed, stated accurately (historical —
behavior through the consumed re-gate).** Host validation proves the
cited text is real, but the model still *selects* which span of the
line to cite, and through the consumed re-gate that model-selected span
reached `normalized_content` and therefore the fingerprint
(`normalized_content = f"{reason_code}|{primary.excerpt}"`, plus the
secondary excerpt for the two-evidence class). An earlier version of
this section claimed that no free-form model text ever reaches a
fingerprint-relevant field; that claim was wrong for model-selected
excerpt spans and was corrected here at the ADR-0006 adoption. The
consumed re-gate demonstrated the consequence: two equally valid
verbatim spans of one frozen line produced two fingerprints for one
semantic defect (`EVAL_RESULTS.md`, "Root cause of the invariant
failure"). **§4a below describes what the code does now.**

### 4a. Identity rule as implemented (`adr/0006-judgment-finding-identity.md`, adopted and landed 2026-08-20)

ADR 0006 adopted Option C and it is **implemented**: persistent finding
identity is separated from descriptive, model-selected evidence. For
the two judgment classes built through `evidence.py`,
`normalized_content` is `"reason=<reason_code>"`, so judgment identity
is effectively `(surface, check_class, primary location, closed
validated reason_code)`. Excerpt text and the stale-STATE secondary
anchor remain required, fail-closed host-validated and retained in
`detail` as **first-seen audit evidence** — they no longer participate
in persistent identity. `compute_content_hash` and
`compute_fingerprint` are unchanged, as are the ledger schema,
lifecycle semantics and the four deterministic checkers' identity.

Two limits recorded with the decision and not fixed by it: the primary
line number remains part of identity, and two genuinely distinct
defects of the same class and reason code on the exact same line of one
surface would collapse to one identity (ADR 0006 §6). Within-call
emissions differing only in evidence span now collapse before persisted
scoring; the frozen scorer, answer key and thresholds are unchanged
(ADR 0006 §7).

**Evidence status — read this before quoting anything above.** The
correction landed together with its model-free T1–T8 regression suite
(`tests/test_bounds.py`, `tests/test_lifecycle.py`,
`tests/test_checks_deterministic.py`). The one permitted ADR-0005
re-gate remains consumed. The one prospective validation cycle
authorized by `adr/0007` / BLUEPRINT §11(i) (adopted 2026-08-20)
executed 2026-08-20 and is now also consumed: on the work that
completed, the correction behaved as designed — the identity defect
did NOT recur, all 58 re-observed run-1 findings kept stable
identity, the 60 persisted finding rows carried 60 distinct
fingerprints, and `dedup_correct_on_doubled_fixture_run` PASSED —
but the cycle's overall disposition is **VALID COMPLETED FAIL** for
an execution-validity reason: one run-1 model call failed at the SDK
per-call budget ceiling and its scope dead-lettered fail-closed, so
`idempotent_rerun` failed on the resulting execution gap, not on
identity instability (`EVAL_RESULTS.md`, prospective section). That
result is terminal for the current Sentinel-v1 Phase-3 validation
lineage. Nothing here claims the
correction produces a passing gate, and **Phase 3 remains OPEN**.

## 5. Cost and accounting semantics

`total_cost_usd` (from the Agent SDK's `ResultMessage`) is the SDK's
own **client-side estimate**, not authoritative billing data — this is
the SDK's own documented caveat (checked against current official
docs, 2026-08), not this system's interpretation of it. Under the
operator's subscription authentication (never an API key for this
gate), `cost_eur_micros` recorded in this system's ledger and CostRows
represents **estimated model-equivalent consumption for Sentinel's own
internal EUR-0.75 run cap** — it is not, and is never described as, a
direct per-run invoice. If API-key billing is ever used instead, that
distinction would be stated explicitly wherever the figure appears.

## 6. Development evidence

Frozen fixture bed: `fixtures/repos/synthetic-01..08`, 60 injected
positives (10 per class across all six classes, this model responsible
for two of them), 166 clean units, scored per the frozen
`evals/SCORING.md` contract against locked thresholds in
`evals/eval_config.yaml`. **Actual measured figures for the two
judgment classes this model handles — precision, recall, per-class
recall, clean false-flag rate, and each run's real cost/token evidence
— are recorded in `EVAL_RESULTS.md` for the designated
2026-08-05 dev gate (honest FAIL), the one permitted 2026-08-19
re-gate (honest OVERALL FAIL: scoring thresholds all PASS, two
cross-run invariants FAIL) and the one 2026-08-20 ADR-0007
prospective validation cycle (honest VALID COMPLETED FAIL: all
scoring thresholds PASS; execution validity and `idempotent_rerun`
FAIL). This document transcribes no figure it does
not take from that record.**

## 7. Known limitations and failure modes

- Single-pass judgment on one document with no ability to consult
  anything else — a defect whose evidence spans more than the one
  fetched document is out of scope by design (matches the existing
  deterministic checkers' own single-surface scope).
- Host validation proves cited evidence is real, verbatim text at the
  cited location; it cannot independently judge whether that evidence
  actually supports the claimed reason code — a plausible-but-wrong
  judgment is a measured accuracy question (§6), not a cage failure.
- The SDK's `max_budget_usd` check is post-call, not pre-call (see
  `THREAT_MODEL.md` §3) — a documented, bounded overshoot risk the
  coordinator's safety margin exists to absorb.
- No API to positively confirm active authentication mode exists in
  current SDK docs (`THREAT_MODEL.md` §7) — this system fails closed
  on known override signals instead of positively confirming OAuth.

## 8. Claims this document does not make

No production or production-ready claim (program open, ADR 0003). No
claim that the standing `SentinelDailyRun` scheduled task uses this
model — it remains stub-mode, unedited, per the binding activation
decision (dispatch q77-p3-a). No claim about the Sonnet official gate.
No availability, uptime, or third-party-facing claim of any kind — n=1,
operator-owned, per the claims ladder (`CLAUDE.md`, `SPEC.md` §7).

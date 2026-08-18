<!-- DRAFT. Lands alongside the Phase-3 capability it describes
(BLUEPRINT §6 P3; adr/0003 P3). Status: in development toward
production-ready. No production claim is made in this document.
Finalized with measured dev-gate evidence after the designated Phase-3
gate runs (see EVAL_RESULTS.md for the actual figures once available). -->

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

These are the settings the one remaining re-gate will run under. This
document makes **no** claim that they produce a passing gate: the
re-gate has not been run, and the recorded result stays the honest
2026-08-05 FAIL until it is.

## 4. Deterministic host canonicalization (evidence contract)

The model never emits a complete, ledger-ready finding. It proposes a
reason code (from a closed, per-class set) and one or two line/excerpt
pairs. `agents/checker/evidence.py` independently validates every
citation against the source document and *deterministically*
constructs `location`, `normalized_content`, and `detail` — the fields
that feed the ledger's dedup fingerprint — from verified data only.
No free-form model text ever reaches a fingerprint-relevant field.

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
recall, clean false-flag rate, and the run's real cost/token evidence
— are recorded in `EVAL_RESULTS.md` after the designated Phase-3 dev
gate runs; this draft does not anticipate or assume a result.**

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

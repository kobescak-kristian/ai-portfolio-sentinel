# SPEC — ai-portfolio-sentinel (condensed operating spec)

Condensed from BLUEPRINT.md v1.1 (2026-08-03). Build sessions execute
this spec; they do not re-decide it. Where this file and BLUEPRINT.md
diverge, BLUEPRINT.md governs and this file is corrected.

## 1. Pipeline contract (one scheduled pass, v1 — BLUEPRINT §2)

Input: the live list of the operator's public repos, derived at run
time via the GitHub API (never a hardcoded list), plus the portfolio
site.

1. Inventory (deterministic): enumerate public repos + site pages;
   build CheckTasks — one per (surface × check class). No LLM.
2. Deterministic checks (no LLM): link liveness (HTTP status);
   README↔EVAL_RESULTS number equality (regex extraction + exact
   match); required-file presence (pre-push hook, gate files,
   STATE.md); README structural sections per the house conventions.
3. Judgment checks (caged checker agent; Haiku dev / Sonnet
   official): synthetic-label presence in context; STATE drift
   markers. AI only where determinism cannot reach — every check that
   CAN be deterministic IS.
4. Dedup + state (deterministic): findings fingerprinted
   (surface + check class + content hash); an OPEN finding is not
   re-reported — last_seen advances; findings absent in a new run
   auto-resolve with a dated RESOLVED row; ledger rows are never
   deleted.
5. Report (deterministic): run summary appended to FINDINGS.md — new
   / still-open / auto-resolved counts + per-finding proposal lines.
   The sentinel proposes; it never edits any surface it monitors. No
   write access to monitored repos, enforced by construction: it
   holds no credentials for them (read via public API only).

Data contracts: typed Pydantic models in contracts/schemas.py
(CheckTask, Finding, RunRecord, CostRow as phases land them); SQLite
ledger as the single audit trail; deterministic control plane (state
machine, queues, dead-letter task-atomic) per the inherited skeleton
(adr/0002).

## 2. Check classes (v1 — closed set)

broken-link · number-mismatch · stale-STATE-marker ·
missing-required-file · missing-synthetic-label. Nothing beyond these
in v1 (BLUEPRINT §8).

## 3. Eval gate — thresholds and quantization mandate (BLUEPRINT §5)

Fixture bed: ~6 synthetic repo snapshots, labeled synthetic on
adjacent lines, defects injected across the 5 classes; target ~10
injected cases per class (~50 positives) + ≥30 clean distractor
surfaces.

Thresholds (locked; changeable only by ADR before a run, never
after): pooled precision ≥ 0.90; pooled recall ≥ 0.85; per-class
recall ≥ 0.80; clean-distractor false-flag rate ≤ 0.10. Invariants at
100%: every task terminal, zero lost tasks, idempotent re-run, dedup
correctness on a doubled fixture run.

Quantization mandate: at Phase 1 freeze, eval_config comments state
the actual integer miss tolerance per threshold at final fixture
counts (at target sizing: per-class recall 0.80 at 10 cases = exactly
2 misses allowed; pooled recall 0.85 at 50 = 7 misses; precision 0.90
at ~50 flags = 5 false positives). A threshold whose integer meaning
is unstated is not frozen.

Official gate on Sonnet; dev legs on Haiku. Gate result publishes
green OR honest FAIL with miss-pattern analysis. One re-gate maximum.

## 4. Injection spec for the P1 answer key (BLUEPRINT §5)

Three-actor, cost-adapted protocol: the injection spec is pre-frozen
here at Phase 0 condensation; Code executes injections; a second
model blind-labels a 40% sample; the operator adjudicates
disagreements; sample disagreement rate > 10% escalates to a full
blind pass before freeze. Per class, the injection must produce a
deterministic ground-truth row (surface, class, injected location,
expected finding) written to the answer key at injection time — never
reconstructed afterward:

- broken-link: replace a live URL with a syntactically valid dead one.
- number-mismatch: alter one figure in a README so it diverges from
  the fixture's EVAL_RESULTS counterpart.
- stale-STATE-marker: insert a dated STATE entry contradicting a
  current-state section.
- missing-required-file: delete one required file (hook, gate file,
  STATE.md) from a fixture snapshot.
- missing-synthetic-label: remove the synthetic label from a line
  adjacent to a figure that requires one.

## 5. Cage rules (checker agent — BLUEPRINT §3, §7)

All four or no run: tool whitelist · turn cap · cost ceiling per run
· audit trail. Read-only by construction (no credentials for
monitored surfaces). Judgment checks only; deterministic logic
executes, AI recommends.

## 6. Cost caps (BLUEPRINT §0, §7 — enforced in code, not convention)

Hard ceiling EUR 50/month, all lane spend pooled; trailing-30-day
spend > EUR 40 drops run frequency one notch (daily → every-2-days →
weekly); frequency drops, caps and ceiling never rise. Per-run caps:
iteration/dev/live ≤ EUR 0.50 (Haiku); official gate ≤ EUR 5.00
(Sonnet). Cost telemetry from Phase 0: every run, including dev,
writes a CostRow (integer micro-euros; no floating-point currency).

## 7. Claims rules (mirrors BLUEPRINT §11(f); ladder in CLAUDE.md)

Three levels:

1. Factual operating claim — "runs unattended on a schedule against
   my real public repos" — permitted when true and evidenced.
2. While any production-readiness program gate is open (program
   opened 2026-08-03): no "in production" or "production-ready"
   claim, in any wording. Status language: "in development toward
   production-ready."
3. After every program gate passes, the sole permitted production
   claim, verbatim: "Production-ready for unattended, read-only
   monitoring of Kristian's own public repositories, operated at
   n=1."

Synthetic fixtures are labeled; live runs are real data; both labels
stated wherever results appear. Never: "battle-tested",
availability/uptime claims, anything implying users other than the
operator.

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
**Phase status:** Phase 1 CLOSED 2026-08-04 — eval gate frozen by
this commit (fixture corpus, answer key, clean inventory, scoring
contract, quantized thresholds and review evidence committed; see
the 2026-08-04 change-log entry and evals/). Phase 0 CLOSED
2026-08-03 — evidence: foundation and canary commits public on main;
repository publish gate OVERALL PASS from the closing HEAD; CI green
on push (Actions run 30852395018, conclusion success; 36/36 tests,
ubuntu-latest, Python 3.12). Next action: Phase 2 per BLUEPRINT §6
(deterministic control plane; P2 closure artifacts additionally wait
on the queued canonical hook-maintenance batch in the private
operations OS, per the plan's recorded dependency).
**Status:** in development toward production-ready (program opened by
owner ruling 2026-08-03); claim levels per the CLAUDE.md ladder as
amended 2026-08-03.
**License:** RESOLVED and LANDED — Apache-2.0 (portfolio default,
owner ruling 2026-08-03); LICENSE file committed 2026-08-03 at
2283b4f via the repo-exclusive rollout step. No remaining
license-related program-closure dependency.
**Plan:** Phases 0–6 per BLUEPRINT §6. Next action: Phase 2 build
dispatch (deterministic control plane on the frozen fixture bed).
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
Next action from this decision: at the next blueprint touch,
distribute these items into the existing phase gates as exit
criteria (CI belongs in Phase 0 — cheapest from the first commit)
and bump the blueprint changelog accordingly; until that lands,
this STATE entry is the decision's home and the blueprint is
one amendment behind. Amendment note for that touch: locked
decision 6 (Actions as SCHEDULER, Phase 5 exit criterion) and
this decision's CI-on-push (Phase 0) are two different uses of
the same platform — continuous integration on push vs. a
cron-scheduled workflow. Both stand; the amendment states this
explicitly so no session "resolves" a contradiction that does
not exist.

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
ladder. Blueprint absorbs this with the same next-touch amendment
already owed.

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

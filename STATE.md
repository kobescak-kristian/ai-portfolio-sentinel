# STATE — ai-portfolio-sentinel

Scheduled monitor over Kristian's own public portfolio repos — link
rot, number consistency, drift markers, required files, label presence
— findings proposed, never applied. LEARNING LANE: the deliverable is
the skill set (long-horizon agent ops); the monitor is the vehicle.
Eval gate runs on labeled synthetic fixtures; live runs on own repos;
no production claims.

**Classification:** EXPERIMENT · T0 (BLUEPRINT.md header; reclassification
pre-registered, BLUEPRINT §0).
**Phase status:** Phase 0 IN PROGRESS — scaffold commit is this commit.
**Plan:** Phases 0–6 per BLUEPRINT §6. Next action: complete Phase 0
gate items (telemetry dry run shown, leak-grep hook tested against
seeded fixture, repo-publish-gate run).
**Open decisions:** rename window open until 2026-07-20 or first
external link. Fixture final counts → quantization integers at Phase 1
freeze. Canonical validator does not recognize decisions/ (two repos
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

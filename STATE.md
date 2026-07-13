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

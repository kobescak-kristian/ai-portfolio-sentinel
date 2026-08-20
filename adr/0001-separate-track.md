# 0001 — Separate track (adopted 2026-07-13)

Status: ADOPTED. Ruling, main governance chat 2026-07-12;
marketing-repo precedent applies.

Decision: ai-portfolio-sentinel runs as its own track. Its task queue
is its own STATE.md. The private operations OS's task queue is
READ-ONLY from this repo's sessions — consulted for cross-dependencies,
never written. The private operations OS carries a one-line pointer to
this repo, nothing more.

Reopening condition (pre-registered): if this lane blocks or delays an
OS task queue item TWICE, track status is re-decided. Occurrences are
counted in this file, dated, append-only below.

Occurrences: (none)

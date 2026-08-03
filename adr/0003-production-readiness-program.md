# 0003 — Production-readiness program: closure gates, artifacts, claims

Status: ACCEPTED (2026-08-03)

## Context

Owner ruling 2026-08-03 (recorded in the private operations OS,
governing standard amendment dated the same day) designates this
system for operation at production standard and opens a
production-readiness build program. Under the governing standard, a
dated owner decision to operate a system at production standard opens
a program whose closure gates are a defined artifact set built
alongside the capability and evidence each artifact describes — never
as placeholders. Until every gate passes, the system's status language
is "in development toward production-ready."

## Decision

The program's closure-gate artifact set, mapped to the BLUEPRINT §6
phases (mapping recorded in BLUEPRINT §11(d)):

- P2: DATA_CONTRACT.md, DATA_RETENTION_POLICY.md
- P3: THREAT_MODEL.md, MODEL_CARD.md (draft)
- P4: TEST_MATRIX.md, INCIDENT_RESPONSE.md, MONITORING.md (draft),
  RUNBOOK.md (draft)
- P5: RUNBOOK.md, MONITORING.md, MODEL_CARD.md (final), SLO.md
- P6: SYSTEM_WALKTHROUGH.md, PRODUCTION_READINESS.md, SYSTEM_CARD.md

Each artifact lands alongside or after the capability and evidence it
describes. An artifact authored before its capability exists is a
placeholder and does not close its gate.

SLO.md framing (owner ruling 2026-08-03), verbatim header for the P5
artifact: "Internal operator objectives for an n=1 system. No
service, availability guarantee, or uptime commitment is offered to
another party." Permitted objective classes: scheduled-run success
rate; maximum consecutive failed or missed runs; finding-detection
latency; cost per run and monthly cost ceiling; telemetry
completeness. Never service availability or uptime percentages.
Objectives become claims only when backed by measured operating
history.

Claims-level consistency rule (2026-08-03), three levels, mirrored in
SPEC.md and BLUEPRINT §11(f):

1. Factual operating claim — "runs unattended on a schedule against
   my real public repos" — permitted when true and evidenced.
2. While any program gate is open: no "in production" or
   "production-ready" claim, in any wording.
3. After every program gate passes, the sole permitted production
   claim, verbatim: "Production-ready for unattended, read-only
   monitoring of Kristian's own public repositories, operated at
   n=1."

## Consequences

The Tier 2 artifact set above is authorized for this repo by this
ADR when each phase's capability and evidence exist — the repository
validator accepts these filenames only against a recorded trigger,
which this decision provides. Status language and the claims ladder
in CLAUDE.md, README.md, and STATE.md are bound to the program: no
production claim of any form before full closure, one bounded claim
after. The program adds artifact work to phases P2–P6; each phase's
gate now includes its mapped artifacts.

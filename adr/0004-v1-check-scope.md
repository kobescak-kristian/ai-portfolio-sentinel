# 0004 — v1 check scope: six classes, site parity deferred

Status: ACCEPTED (2026-08-04)

## Context

Phase 1 freezes the eval gate. At freeze time the v1 pipeline
(BLUEPRINT §2 step 2, SPEC §1) claims README structural checking as a
deterministic check, but the five-class set (BLUEPRINT §5, SPEC §2)
carried no eval class for it — a capability claimed in v1 would have
shipped ungated. Two adjacent gaps surfaced in the same review: site
gate-statement parity (BLUEPRINT §11(c)) is a claimed monitored
surface with no v1 eval class, and the protocol wording "blind-labels"
overstated what the cost-adapted answer-key review certifies.

## Decision

1. The v1 closed set is six classes (changeable only by ADR):
   broken-link · number-mismatch · stale-STATE-marker ·
   missing-required-file · missing-synthetic-label · readme-structure.
2. readme-structure required sequence, frozen for the Phase 1 corpus
   and matching the live artifact validator — exact headers, exact
   order: `## Problem`, `## Solution`, `## System`, `## Outcome`,
   `## Version Log`.
3. Site gate-statement parity is deferred by the dated ruling recorded
   verbatim in BLUEPRINT §11(h): in scope but ungated; not implemented
   and never reported by live runs until an ADR lands class 7 with
   paired fixtures, answer key, scoring rule and restated pooled
   integers before or with the check's code; the P6 stop-condition
   review must resolve it — it does not survive P6 as pending. This
   deferral does not weaken the bounded final claim recorded in
   adr/0003 (the Q-77 program's closing claim): that claim's scope is
   defined by the capabilities that are gated, and after this ruling
   every check capability claimed and gated in v1 has a defined class,
   fixtures, scoring rule and frozen threshold.
4. Blind-review terminology, binding for governing documents and
   committed evidence: the reviewer is blind to the expected answer
   and expected location, but packet shape may reveal the candidate
   class. The independent review certifies defect presence or absence
   and independently identifies the location; it does not certify
   blind class discovery.

## Consequences

Class parity becomes mechanically enforced: tests/test_class_parity.py
compares the CheckClass contract in contracts/schemas.py, the
machine-readable class block in SPEC §2, and (from the freeze commit)
the classes list in evals/eval_config.yaml as exact sets. The fixture
corpus sizes at 6 × 10 = 60 positives; quantization integers restate
at final counts per the BLUEPRINT §5 mandate. No live run reports
site-parity findings while the deferral stands.

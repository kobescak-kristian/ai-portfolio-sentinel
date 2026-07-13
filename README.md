# ai-portfolio-sentinel

> **LEARNING LANE (EXPERIMENT).** This is a personal learning project, not a
> product or a client-facing service. The eval gate runs on labeled
> **synthetic** fixtures; live scheduled runs monitor only Kristian's own
> public repositories. No production, uptime, autonomy, or third-party
> monitoring claim is made anywhere in this repo.

## Problem

Every engine in this portfolio so far has been demonstrated in a single,
human-initiated run. No artifact yet demonstrates the harder operational
class companies hire "agent reliability" people for: a system that runs
**unattended, on a schedule, for weeks**, where failures show up as
accumulation — duplicate findings, drifting state, silent crashes that
look like quiet success — rather than one wrong answer.

## Solution

A scheduled monitor over Kristian's own public portfolio repos. Each run
derives the repo inventory live from GitHub (no hand-maintained list to go
stale), runs deterministic checks (link liveness, README↔EVAL_RESULTS
number consistency, required-file presence) plus a small set of
judgment checks via a caged checker agent, deduplicates findings against
a persistent ledger, and appends proposals to `FINDINGS.md`. It never
writes to any repo it monitors — it holds no credentials for them.

## System

*Architecture diagram: pending Phase 2 (deterministic control plane).
Nothing runs end-to-end yet — see Phase status in [STATE.md](STATE.md).*

Full design, phase gates, and eval thresholds: [BLUEPRINT.md](BLUEPRINT.md).

## Outcome

Phase 0 (scaffold) in progress. No runs, no eval results, and no outcome
to report yet — this section updates only when a phase gate closes with
evidence (BLUEPRINT.md §6).

## Version Log

| version | date | change |
|---|---|---|
| v0.1 | 2026-07-13 | Tier 0 scaffold: BLUEPRINT.md, CLAUDE.md, decisions/0001, STATE.md committed. Phase 0 in progress. |

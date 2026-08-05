# ai-portfolio-sentinel

[![CI](https://github.com/kobescak-kristian/ai-portfolio-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/kobescak-kristian/ai-portfolio-sentinel/actions/workflows/ci.yml)

> **LEARNING LANE (EXPERIMENT).** This is a personal learning project, not a
> product or a client-facing service. The eval gate runs on labeled
> **synthetic** fixtures; live scheduled runs monitor only Kristian's own
> public repositories. No production, uptime, autonomy, or third-party
> monitoring claim is made anywhere in this repo.
> **Status: in development toward production-ready.** No production
> claim is made at the present development stage. A bounded
> production-ready claim may be made only after every
> production-readiness program gate passes (BLUEPRINT §11).

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
number consistency, required-file presence, README structure), deduplicates
findings against a persistent ledger, and appends proposals to
`FINDINGS.md`. Two judgment classes — stale state markers and missing
synthetic labels — are stubbed at Phase 2 and land with a caged checker
agent at Phase 3. It never writes to any repo it monitors — it holds no
credentials for them.

## System

One scheduled pass, end to end. Deterministic control plane, zero LLM
calls at this phase.

```mermaid
flowchart TD
  SCH["Windows Task Scheduler<br/>daily · current user · no stored secret"]
  MON["Monitored surfaces<br/>own public repos — READ-ONLY, no credentials held"]
  INV["1 · Inventory (deterministic)<br/>public repos derived live via GitHub API<br/>no hand-maintained list"]
  TSK["2 · CheckTasks<br/>one per surface × check class<br/>PENDING → IN_PROGRESS → DONE / FAILED / DEAD_LETTER"]
  DET["3a · Deterministic checkers — real<br/>broken-link · number-mismatch<br/>missing-required-file · readme-structure"]
  STB["3b · Judgment checkers — STUB at Phase 2<br/>stale-STATE-marker · missing-synthetic-label<br/>caged agent lands at Phase 3"]
  DED["4 · Dedup + lifecycle (deterministic)<br/>fingerprint = sha256(surface, class, content_hash)<br/>OPEN advances last_seen · absent → RESOLVED · rows never deleted"]
  DB[("SQLite ledger<br/>runs · tasks · findings<br/>delete-abort + lifecycle triggers")]
  FND["FINDINGS.md<br/>new / still-open / resolved counts + proposal lines"]
  LOG["Structured JSONL run log<br/>runtime-local"]
  CST["CostRow → telemetry/cost_ledger.jsonl<br/>Phase 2: 0 tokens, 0 micro-euros"]
  OPR(["Operator — decides on every proposal"])

  SCH -->|"python -m sentinel run --run-kind live"| INV
  MON -.->|"public read only"| INV
  INV --> TSK
  TSK --> DET
  TSK --> STB
  DET --> DED
  STB --> DED
  DED --> DB
  DB --> FND
  DB --> LOG
  DB --> CST
  FND -.->|"proposes, never edits"| OPR
```

**Runtime surface (decided at Phase 2, BLUEPRINT §11(a)).** Core
pipeline: Python 3.12 with pinned dependencies. Tested platform:
`ubuntu-latest` + Python 3.12 — the single declared CI leg, run on
every push. Scheduling host: the operator's current Windows
environment via Task Scheduler; the PowerShell scheduling tooling is
Windows-only and is not covered by the CI leg. No other platform is
claimed or tested.

Data shapes, ledger schema, hashing and lifecycle rules:
[DATA_CONTRACT.md](DATA_CONTRACT.md). What is stored, for how long, and
what is never stored: [DATA_RETENTION_POLICY.md](DATA_RETENTION_POLICY.md).
Full design, phase gates, and eval thresholds: [BLUEPRINT.md](BLUEPRINT.md).

## Outcome

Phase 2 closed 2026-08-05. The deterministic control plane runs end to
end and is triggered by a schedule, not by hand.

- **Checkers.** Four real deterministic checkers landed: broken-link,
  number-mismatch, missing-required-file, readme-structure. Two
  judgment classes (stale-STATE-marker, missing-synthetic-label) are
  stubbed — the caged checker agent replacing them is Phase 3 work.
- **Tests and coverage.** 482 tests passing, 4 skipped (the Phase-3/4
  breaker/cost-cap stubs only). Line coverage 89.9% (branch coverage
  also measured) over `contracts, telemetry, sentinel, checks`
  (`python -m coverage run -m pytest && python -m coverage report -m`).
- **Scheduled runs (LIVE — real data).** Two consecutive runs triggered
  by Windows Task Scheduler with no manual invocation between them:
  `r-91ec8071505a4ba7905fe6f9ef4c53f4` (started 2026-08-05T09:01:39Z)
  and `r-5ac95d4bc6fd4c55a7f739547090098f` (started
  2026-08-05T09:21:40Z) — both COMPLETED, 190/190 tasks terminal (all
  DONE), `LastTaskResult=0` on both. The second run's dedup/lifecycle
  behavior was exact: 0 new findings, 4 still open, 0 resolved.
  Standing schedule: `SentinelDailyRun`, daily at 07:15 local.
- **Cost.** Every CostRow this phase — including the manual
  measurement run that preceded the gate — shows 0 input tokens, 0
  output tokens, 0 micro-euros (`model="none-deterministic"`): zero
  model calls, a true measurement.
- **A real runtime defect, found and fixed by actually running the
  scheduler tooling.** The PowerShell script initially failed to parse
  under real Windows PowerShell 5.1: non-ASCII characters without a
  BOM broke string-literal tokenization, and `$PSScriptRoot` is not
  reliably populated inside a `param()` default-value expression on
  this PowerShell version. Both are fixed; see the Phase 2 gate post.

The eval gate runs on **synthetic** fixtures with a frozen answer key
(unchanged since `4d46c1d4fc3c4f485a83f44fa54afa6b04b1f541`); scheduled
runs are **live** — real data against the operator's own public
repositories. The two are labeled everywhere and neither borrows the
other's credibility. Status: in development toward production-ready.
No production claim is made. **Q-77 remains open — Phase 2 is closed,
Phase 3 (the caged checker agent) is next.**

## Version Log

| version | date | change |
|---|---|---|
| v0.1 | 2026-07-13 | Tier 0 scaffold: BLUEPRINT.md, CLAUDE.md, decisions/0001, STATE.md committed. Phase 0 in progress. |
| v0.2 | 2026-08-03 | Phase 0 closed: SPEC.md, claims-ladder amendments, program ADR, cost telemetry (CostRow + JSONL ledger + dry run + 36 tests), CI on push, publish-gate canary. Production-readiness program opened (owner ruling 2026-08-03). |
| v0.3 | 2026-08-05 | Phase 2 closed: deterministic control plane end to end (4 real checkers + 2 Phase-3-stubbed judgment classes), SQLite ledger with fingerprint dedup and OPEN/RESOLVED finding lifecycle, FINDINGS.md writer, structured JSONL logging, zero-cost CostRow telemetry, Windows Task Scheduler tooling with a standing `SentinelDailyRun` task (daily, 07:15 local). 482 tests passing, exactly 4 skips (Phase 3/4 stubs only), 89.9% line coverage. Implementation commit `bfa56d680c6a0980cef8b9494b3a307defd4318e`; this Version Log entry's own closure commit's exact SHA and CI run are recorded in the kristian-os Q-77 annotation (`q77-p2-record-a`), not embedded here (a commit cannot truthfully cite its own hash). Scheduler gate: two consecutive Task-Scheduler-triggered live runs (`r-91ec8071505a4ba7905fe6f9ef4c53f4`, `r-5ac95d4bc6fd4c55a7f739547090098f`), 190/190 tasks terminal on each, `LastTaskResult=0` on each, zero-cost CostRows, zero manual invocation between fires. Q-77 remains open; Phase 3 (caged checker agent) is next. |
| v0.4 | 2026-08-05 | Phase 3 caged checker agent implemented (`agents/checker/`: cage, run-scoped EUR budget, host-side evidence validation, main-ledger audit) at commit `cf713649bc1aaf31f1494112921d7741493533b0`, CI green, 535 tests passing / 3 skips (Phase 4 only). Designated Haiku dev gate then ran and recorded an honest **FAIL**: pooled precision 0.8393 (< 0.90), pooled recall 0.7833 (< 0.85); per-class recall FAIL on `stale-STATE-marker` (2/10) and `missing-synthetic-label` (5/10), PASS on all four deterministic classes (10/10 each) and the clean-false-flag rate. No fixture, threshold, prompt, or model change was made after seeing this result. Full evidence: `EVAL_RESULTS.md`; narrative: `posts/2026-08-05-phase-3-gate.md`. **Phase 3 remains open; Q-77 remains open.** `SentinelDailyRun` unchanged, stub-mode. No production or capability claim is made for the judgment classes. |

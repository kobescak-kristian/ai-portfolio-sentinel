<!-- DRAFT — Phase 4. Mapped Phase-4 closure artifact (BLUEPRINT §6 P4,
§11(d); adr/0003-production-readiness-program.md P4; adr/0010 §8). The
FINAL RUNBOOK.md is mapped to Phase 5 by adr/0003 and does not exist
yet — it is authored from real scheduled operation, which has not
happened. Every command below is transcribed from the landed CLI
surfaces. Status: in development toward production-ready. No
production, service or availability claim is made in this document. -->

# RUNBOOK (DRAFT — Phase 4) — ai-portfolio-sentinel

> **This is a Phase-4 draft, not the final runbook.** `adr/0003` maps
> the final `RUNBOOK.md` to Phase 5, where it is written from
> evidence-backed real operation (deploy / rollback / diagnose) after
> the GitHub Actions scheduler migration. This draft documents only
> what exists and runs today.

## A. Current operating boundary

- **n=1, operator-owned.** One operator, monitoring the operator's own
  public repositories. No third party, no service.
- **Monitored surfaces are read-only by construction.** The system
  holds no credentials for the repositories it monitors and reads them
  through the public API only. It proposes; it never edits a monitored
  surface.
- **The bounded-loop entry point is stub-only.**
  `--judgment-mode agent` is refused fail-closed, before any provider
  construction, with a deterministic exit code 2. That refusal is the
  feature, not a placeholder.
- **No flag, configuration value or environment variable raises the
  750,000 micro-EUR loop ceiling.** None exists, and none may be added
  without a separate dated owner-governed decision.
- **The standing scheduled task is unchanged and stub-mode.** Its
  resolved command carries no judgment-mode flag, so it stays in stub
  mode by construction.
- **GitHub Actions scheduling topology is implemented (P5-B, part of
  Phase 5) but has never operated.** Five workflow files exist under
  `.github/workflows/` (`sentinel-schedule.yml` and four manual/
  model-free lanes), contract-tested model-free; none has ever been
  dispatched, and the scheduled lane has never authenticated or made a
  model call.

## B. Pre-run checks

Two different situations, deliberately not conflated.

### B.1 Ordinary runtime

The code does **not** require a clean Git tree, a particular branch or
a pinned commit in order to run. What actually matters before an
ordinary run:

- The Python environment is the pinned one:

```bash
python -m pip check
```

- The database, findings, log and cost-ledger paths you intend to use
  are the ones you will pass. **The defaults write to repository-tracked
  files** (`FINDINGS.md`, `telemetry/cost_ledger.jsonl`), so a dev or
  experimental loop should be pointed at scratch paths instead.
- The loop's `--db` is the same ledger the runs use. Loop tables are
  co-located with `runs`/`tasks`/`findings` on purpose — an iteration is
  foreign-keyed to its run, so a separate database could not bind them.
- Mode is what you think it is: `--judgment-mode` defaults to `stub`,
  and the bounded loop accepts nothing else.
- No credentialed write access to any monitored repository is
  configured. There should be nothing to check here, and that is the
  point.

### B.2 Governed evidence session

Additionally, when a session produces evidence that will be committed
(a gate execution, an evidence recording):

- Working tree clean; `HEAD` equal to `origin/main`; both directional
  logs empty.
- `HEAD` equal to the exact source SHA the evidence is meant to be
  produced from.
- The official output paths are in the expected state before the run.
- Any repository change after a source is pinned invalidates that pin.

## C. Start and invocation

### C.1 The bounded loop (stub mode only)

```bash
python -m runner --loop-id <loop-id> --iterations <1..10> --run-kind dev --source fixtures
```

Required: `--loop-id`, `--iterations` (bounded `1 <= N <= 10`),
`--run-kind` (`dev` | `eval` | `live`), `--source`
(`fixtures` | `live`).

Optional, with defaults: `--fixtures-root` (`fixtures/repos`),
`--github-user`, `--site-repo`, `--db` (`var/sentinel.sqlite3`),
`--findings` (`FINDINGS.md`), `--log` (`var/logs/loop.jsonl`),
`--cost-ledger` (`telemetry/cost_ledger.jsonl`), `--judgment-mode`
(`stub`).

Validation rules the entry point applies before anything durable
exists: `--source live` requires `--github-user`; `--run-kind eval`
must not be combined with `--source live`; `--judgment-mode agent` is
refused. An out-of-range `N` is refused before any loop row, iteration
intent or run exists.

There is deliberately **no provider-capable bounded-loop invocation**.
If you need one, that is a governance decision, not a flag.

### C.2 A single run

```bash
python -m sentinel run --run-kind live --source live --github-user <user>
```

Exit codes: `0` COMPLETED, `1` FAILED (a coherent FAILED run record was
written), `2` usage/config error (no run row created).

### C.3 Recovery sweep

```bash
python -m sentinel recover --db var/sentinel.sqlite3 --log var/logs/sentinel.jsonl
```

Sweeps any interrupted run to a terminal FAILED state. Exit code `3`.

### C.4 Scheduled task (Windows, local)

Always inspect before changing anything:

```bash
pwsh -File scripts/Sentinel-Schedule.ps1 -Action Show
```

`-Action` accepts `Show`, `Install`, `Update`, `Remove`, `Evidence`.
Run any registering action with `-WhatIfOnly` first to print the
fully-resolved action, trigger and settings without registering
anything. Cadences are `Daily`, `EveryNDays`, `Weekly`, `GateBurst`.

This surface is Windows-only, is not covered by the CI leg, and is a
Phase-2 facility. It is not the bounded-loop runner and it does not
invoke one.

## D. Expected normal completion

For a bounded loop that finishes normally:

- `stop_reason` = `COMPLETED_ITERATION_CAP`
- process exit `0`
- `iterations_completed` equals N, with `N <= 10`
- every iteration `FINALIZED`, indexes contiguous from 0
- every underlying run terminal, `tasks_terminal == tasks_created`
- exactly one `CostRow` per run
- accounted cost at or below the 750,000 micro-EUR ceiling
- the terminal summary line printed by the entry point, carrying the
  stop reason, loop id, iterations completed and accounted cost

## E. Abnormal stop reasons

| `stop_reason` | Exit | Operator next step |
|---|---|---|
| `COST_BREAKER_TRIPPED` | nonzero | Read durable `CostRow`s for the loop's run ids; account overshoot in full; identify the cost cause. Do not raise the loop ceiling or the per-run cap. See `INCIDENT_RESPONSE.md` §4. |
| `CONSECUTIVE_FAILURE_BREAKER_TRIPPED` | nonzero | Read the three durable run outcomes; separate a common cause from independent ones; fix before starting a new loop. Do not reset or delete rows to clear the streak. See `INCIDENT_RESPONSE.md` §5. |
| `LOOP_ABORTED_ERROR` | nonzero | Inspect durable loop and run state before logs; separate a supervisor failure from a terminal underlying run; preserve evidence; never mint a replacement run id. See `INCIDENT_RESPONSE.md` §6. |

Exactly one terminal stop reason is authoritative per loop, and a
finished loop is never reopened.

## F. Crash and restart

Restart the **same** loop id. The supervisor reconciles from durable
state, in this order, always using the committed `planned_run_id`:

- Intent committed, no run row → the run starts once, with that same
  `planned_run_id`.
- Run row `RUNNING` → the existing interrupted-run recovery drives it
  terminal; no replacement run is created; the recovered terminal run
  is the iteration's result.
- Run row terminal → adopt it. It is not executed again.
- Run row terminal, derived outputs incomplete → reconcile the outputs.
  No rerun, and no second cost source.

Earlier unfinished iteration indexes are reconciled before any later
new iteration starts.

**Never rerun terminal work merely because a derived output is
missing.** A missing `FINDINGS.md` section or `CostRow` for an
already-terminal run is a reconciliation job.

Resuming a loop under different bounds is refused — a loop's bounds are
fixed at creation.

## G. Evidence and log inspection

| What | Where | Authority |
|---|---|---|
| Loop and iteration state | `loop_runs`, `loop_iterations` in the ledger | **Authoritative** |
| Run and task state | `runs`, `tasks` in the ledger | **Authoritative** |
| Finding lifecycle | `findings` in the ledger | **Authoritative** |
| Accounted cost | `CostRow`s in the cost ledger | **Authoritative** |
| Event stream | structured JSONL at the `--log` path | Durable record, redacted free text |
| Proposals/report | `FINDINGS.md` | **Derived** |
| Loop/gate evidence | `ITERATION_LOG.md` | **Derived** |

Derived output never overrules durable state. Diagnose from the ledger
and the cost rows; use the derived files as an index into them.

Ledger rows are never deleted — delete-abort triggers cover every
table, loop tables included.

## H. Cost checks

- Per-run cap: **EUR 0.75** (750,000 micro-EUR), unchanged by Phase 4.
- Phase-4 loop ceiling: **750,000 micro-EUR**, fixed. Not raisable from
  the command line, configuration or environment.
- Effective iteration allowance: `min(per-run cap, remaining loop
  budget)`, propagated downward; if it cannot be enforced, the
  iteration is refused fail-closed.
- Overshoot: accounted in full, never clamped.
- Lane monthly hard ceiling: **EUR 50**, all lane spend pooled.
- Frequency drop: if trailing-30-day spend exceeds **EUR 40**, run
  frequency drops one notch (daily → every-2-days → weekly).

**There is no cap-raise procedure in this runbook, by design.**
Frequency drops; caps and ceilings never rise to fit.

## I. Evidence hash check

The official `iteration_log_sha256` in
`artifacts/phase4_loop_gate.json` binds the exact committed **LF** bytes
of `ITERATION_LOG.md`.

On a Windows checkout where line-ending conversion changes working-copy
bytes, a working-tree hash mismatch does not by itself prove the Git
blob is corrupt. Compare against the committed blob — the exact
canonical bytes — before concluding anything.

**Never edit `ITERATION_LOG.md` to make a local hash match.** Full
semantics in `INCIDENT_RESPONSE.md` §9.

## J. Current limitations and Phase-5 handoff

Stated plainly, because a runbook that implies capability it does not
have is worse than no runbook:

- **No provider-capable runner CLI exists.** The bounded loop runs in
  stub mode only; agent mode is refused fail-closed.
- **The standing scheduled task is stub-mode** and unedited. It invokes
  a single run, not a bounded loop.
- **The GitHub Actions scheduler migration is implemented but has not
  operated.** The five workflow files exist and are contract-tested
  model-free; none has been dispatched, the Windows scheduler has not
  been cut over, and no Actions-scheduled run has occurred, qualifying
  or otherwise.
- **No five consecutive Actions-scheduled live runs exist.** There is no
  scheduled operating history to diagnose against.
- **No final `MONITORING.md`, no final `RUNBOOK.md`, no `SLO.md`.**
  This document and `MONITORING.md` are Phase-4 drafts.
- **A deploy/rollback procedure is not yet documented from real
  operation.** The five workflow files are now the deployment surface,
  but no deploy or rollback has actually been exercised against it;
  that evidence-backed procedure remains owed to the final `RUNBOOK.md`.
- **No production or production-ready claim** is made or implied. The
  status language is unchanged: in development toward production-ready.

## K. Verification commands

Used before committing anything in this repository:

```bash
python -m pip check
```

```bash
python -m coverage run -m pytest -p no:cacheprovider
```

```bash
python -m coverage report -m
```

```bash
python .githooks/validate_artifacts.py .
```

```bash
python scripts/check_phase1_frozen.py
```

The pre-push hook additionally enforces branch freshness, the Tier 0
artifact validator and a leak-grep over the pushed diff. It is never
bypassed.

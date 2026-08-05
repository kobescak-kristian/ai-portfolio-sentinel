<!-- Describes the system as landed through Phase 3 (BLUEPRINT §6 P2/P3,
§11(d); ADR 0003; dispatch q77-p3-a). Status: in development toward
production-ready. No production claim is made in this document. -->

# DATA_CONTRACT — ai-portfolio-sentinel

## 1. Scope and status

This document describes the deterministic control plane landed at
Phase 2 (live inventory, task creation, four real deterministic
checkers, SQLite ledger persistence, dedup/lifecycle, `FINDINGS.md`
reporting, structured logging) plus the Phase-3 addition: a real,
caged checker agent for the two judgment classes, selected explicitly
via `--judgment-mode agent` (default remains `stub`, unchanged Phase-2
behavior — see §5b). **Zero LLM/model calls occur in stub mode**, the
default and what the standing `SentinelDailyRun` scheduled task
invokes, unedited. Agent mode makes real, budget-capped model calls
only for the two judgment classes; every other check class stays fully
deterministic in both modes.

Synthetic/live labeling is never blurred: the frozen Phase 1 eval
gate (`fixtures/`, `evals/`) is **SYNTHETIC** data with a frozen
answer key; a scheduled or manual `--source live` run is **REAL
DATA** against the operator's own public repositories. `FINDINGS.md`
states which one produced each section explicitly.

Site gate-statement parity (class 7) is out of scope per BLUEPRINT
§11(h) / ADR 0004 §3 — not implemented, never reported by any run.

## 2. Runtime surface (decided at Phase 2, BLUEPRINT §11(a))

- **Core pipeline**: Python 3.12, dependencies pinned in
  `requirements.txt`/`requirements-dev.txt`, checked by `pip check`.
- **Tested platform**: `ubuntu-latest` + Python 3.12 — the single
  declared CI leg, run on every push, covering all platform-
  independent code (the entire pipeline).
- **Scheduling host**: the operator's current Windows environment via
  Task Scheduler (`scripts/Sentinel-Schedule.ps1`). This surface is
  Windows-only and is not covered by the CI leg; its argv/task-param
  construction (`sentinel/scheduling.py`) is a pure, platform-
  independent module that CI does exercise.
- No other platform is claimed or tested. No decorative OS/Python-
  version matrix.

## 3. Inputs and trust boundary

- Live inventory derives the operator's public repositories at run
  time from the unauthenticated public GitHub API
  (`sentinel/inventory/github_live.py`) — no hand-maintained list.
- The system **holds no credentials for any monitored surface** —
  read-only by construction, not by policy: `github_live.py` reads no
  environment variable and never sets an `Authorization` header
  (source-scanned and dynamically canary-tested,
  `tests/test_read_only_boundary.py`).
- Unauthenticated GitHub API access is rate-limited to 60 requests/
  hour/IP; bounded timeout (10s) and bounded retry (3 attempts, fixed
  backoff) apply to every request (`sentinel/config.py`,
  `sentinel/net/client.py`).
- All fetched content is treated as untrusted text: no execution, no
  deserialization into code paths.
- The portfolio-site repo (`{github_user}.github.io`) participates
  only in link-checking (BLUEPRINT §11(c)) — never required-file or
  readme-structure checks (`sentinel/inventory/site.py`).

## 4. Normalized surface grammar (frozen — `contracts/schemas.py`)

`<name>/<repo-relative-path>` — repo name or fixture snapshot
directory, then a forward-slash path with no scheme, host, colon,
leading slash, backslash, `..` segment or control character; at least
two non-empty segments. Site surfaces use `site/<page>` (root =
`site/index`). Enforced by `_validate_surface`; fingerprints depend
on this grammar staying exact.

## 5. Check classes and the Phase-2 stub boundary (ADR 0004)

The six frozen classes: `broken-link`, `number-mismatch`,
`stale-STATE-marker`, `missing-required-file`,
`missing-synthetic-label`, `readme-structure`. Set parity with
`SPEC.md` and `evals/eval_config.yaml` is mechanically enforced by
`tests/test_class_parity.py`.

**Real at Phase 2 (4 classes)** — `checks/deterministic/`:

- **broken-link**: markdown links + autolinks; fires only on a
  confirmed HTTP 404/410 (C3 closed status set — every other
  transport/HTTP outcome is `unknown` and never fires); whole-task
  determinism — one unresolved link in a file dead-letters the whole
  file's check rather than treating it as clean.
- **number-mismatch**: `- label: value [unit]` bullets compared
  between `README.md` and `EVAL_RESULTS.md`, joined on casefolded
  label, `Decimal` comparison (never `float`); ambiguous/duplicate
  labels skipped.
- **missing-required-file**: fires on a *confirmed* absent required
  path; a confirmed-absent result is itself the finding (re-advances
  every run it stays absent — never treated as "unobserved").
  Fixture/eval set (frozen): `STATE.md`, `.githooks/pre-push`,
  `evals/eval_config.yaml`. Live set: derived per repository, per
  run — see §5a.
- **readme-structure**: fixture/eval mode enforces ADR 0004's frozen
  five-header exact sequence; live mode enforces presence-only of
  that repository's own declared header list — see §5a.

**Judgment classes (2)** — `checks/judgment/`: `stale-STATE-marker`,
`missing-synthetic-label` route through an injectable `JudgmentStub`
(`checks/judgment/stubs.py`). `NullJudgmentStub` performs no I/O, no
model call, and returns nothing — the tasks still reach `DONE` with
zero findings; this is stub mode's production implementation and the
default. `CagedCheckerStub` (`agents/checker/harness.py`, landed at
Phase 3) is the real implementation, selected only via
`--judgment-mode agent` — see §5b. Neither adapter
(`checks/judgment/stale_state.py`, `synthetic_label.py`) changed to
support this: both call `ctx.judgment.judge(request)` exactly as
before, unaware of which implementation is behind the seam.

### 5b. Phase-3 real judgment: the caged checker agent

`agents/checker/` (new, first-party, outside `sentinel/`/`checks/`) is
the only place the Claude Agent SDK is imported anywhere in this
repository — `tests/test_read_only_boundary.py` bans it from
`sentinel/`/`checks/`; `tests/test_dependency_surface.py` bans it from
every other first-party root. Full architecture, cage, evidence
contract, and threat coverage: `THREAT_MODEL.md`, `MODEL_CARD.md`.
Summary of what's new to this contract:

- **Trust boundary**: the agent receives only `JudgmentRequest.text` —
  already fetched deterministically, exactly as the stub adapters
  always did. It has no fetch tool and cannot reach any surface beyond
  the one document it's given.
- **Evidence, not findings**: the model proposes a closed reason code
  plus verbatim line/excerpt citations through one tool
  (`emit_finding`); `agents/checker/evidence.py` independently
  validates every citation against `JudgmentRequest.text` and
  deterministically constructs the actual `ObservedFinding` — no
  free-form model text ever reaches `location`, `normalized_content`,
  or `detail`.
- **Cost**: a run-scoped EUR budget (not per-request), derived into a
  conservative per-call USD ceiling via a freshly-resolved ECB
  reference rate (`agents/checker/fx.py`) — never a hardcoded or
  cached rate. `cost_eur_micros` under subscription auth is estimated
  model-equivalent consumption for this system's own cap, never
  described as authoritative billing (`MODEL_CARD.md` §5).
- **Authentication**: fails closed before any model call if a
  documented override-capable environment variable (API key, auth
  token, base-URL override, cloud-provider routing flag) is present
  (`agents/checker/auth.py`) — the intended auth is the operator's
  local Claude subscription OAuth only.
- **Activation**: `--judgment-mode stub|agent`, default `stub`. The
  standing `SentinelDailyRun` scheduled task's resolved command carries
  no `--judgment-mode` flag and is unedited by this phase — it stays
  stub-mode by construction, unchanged Phase-2 behavior. Scheduled
  live-agent activation is a separate, later decision, not part of
  this phase's closure.

### 5a. Live applicability is policy-derived per repository, per run

Per an explicit owner ruling: `.githooks/pre-push` is the one flat
requirement for every monitored project repo (excluding the account's
profile repo and its `{user}.github.io` Pages repo — both structural
exclusions, not a name list). Beyond that, nothing is assumed:

- A **gate file** is required only where that repository's own
  active (non-commented) `.githooks/pre-push` actually invokes one
  (`_find_active_gate_invocation`, a deterministic text parse over
  non-comment lines).
- **`STATE.md`** is required only where that repository's own gate
  file's own source declares the check (`_declares_state_md_required`,
  an AST match for `(ROOT / "STATE.md").exists()` — not a textual
  heuristic, so a comment or docstring containing the substring can't
  false-positive).
- **readme-structure** is applicable only where a gate file is
  actively invoked, enforcing presence-only of that repository's own
  declared `REQUIRED_README_SECTIONS` list
  (`_extract_required_readme_sections`) — never Sentinel's own
  ADR-0004 sequence, never order.
- If a policy file is referenced but its content can't be fetched or
  parsed deterministically, the design does not guess: no new
  requirement is derived, no existing finding for that scope
  auto-resolves, and the task that would have confirmed the
  requirement dead-letters rather than silently passing.
- No private queue or governance record is ever consulted at
  runtime — only the two public files named above, read live, every
  run. (`sentinel/inventory/github_live.py`)

## 6. Record contracts (frozen — `contracts/schemas.py`)

- **`CheckTask`**: one row per (surface × check class) per run.
  `status ∈ {PENDING, IN_PROGRESS, DONE, FAILED, DEAD_LETTER}`,
  `extra="forbid"`.
- **`Finding`**: `status ∈ {OPEN, RESOLVED}`; `first_seen`/
  `last_seen`/`resolved` timestamp-and-run-id triple; the model
  validator recomputes and enforces `fingerprint =
  compute_fingerprint(surface, check_class, content_hash)`.
- **`RunRecord`**: `status ∈ {RUNNING, COMPLETED, FAILED}`; `RUNNING`
  iff `finished_at_utc` is absent; `COMPLETED` requires
  `tasks_terminal == tasks_created`.
- **`CostRow`**: the frozen eight fields; integer micro-euros only.
  **At Phase 2 every CostRow shows 0 input tokens, 0 output tokens, 0
  micro-euros, `model="none-deterministic"`** — a true measurement
  (zero model calls), not a placeholder
  (`sentinel/costs.py`).
- All datetimes are timezone-aware UTC, serialized via
  `serialize_db_datetime` to the exact 25-character
  `YYYY-MM-DDTHH:MM:SS+00:00` shape — SQLite's own datetime adapters
  are never used.
- Free-text guards reject machine-local absolute paths in identifiers
  and surfaces; `detail`/log messages permit ordinary URLs by a
  deliberately narrower delimiter set (reused directly from
  `contracts/schemas.py`'s detail-flavored guard in
  `sentinel/logs.py`, so the two can never drift apart).

## 7. Persistence: SQLite tables and relationships (`contracts/ledger_schema.sql`)

`runs` (PK `run_id`) → `tasks` (PK `(run_id, task_id)`, FK → `runs`) →
`findings` (surrogate `id` PK — a fingerprint may legitimately recur
after resolution, so it can't be the primary key; three FKs to `runs`
for `first_seen`/`last_seen`/`resolved_run_id`). `PRAGMA foreign_keys
= ON` is set on every connection (`sentinel/ledger.py::open_ledger`).
CHECK constraints mirror the Pydantic invariants at the DB layer.
`idx_findings_open_fingerprint` (unique, partial `WHERE status='OPEN'`)
makes a duplicate OPEN finding unrepresentable. Cost telemetry stays
in the separate, frozen Phase-0 JSONL ledger (`telemetry/cost_ledger.py`)
by design — unifying it with SQLite would alter a frozen contract and
needs its own ADR.

**Phase-3 addition**: `agent_calls` (surrogate `id` PK, FK `run_id` →
`runs`) is the caged checker agent's main-ledger audit trail — one row
per attempted judgment call, `RESERVED` before the SDK is invoked and
finalized to a terminal state (`COMPLETED`/`FAILED`/`REJECTED`/
`EXHAUSTED`) after (§5b; `THREAT_MODEL.md` §9). Purely additive: no
existing table's definition changed, and every statement in the DDL
file is `IF NOT EXISTS` so it applies safely to a pre-Phase-3 database
on its next open (`sentinel/ledger.py::initialize_schema`). Empty (no
rows) for every stub-mode run — `sentinel/costs.py::has_agent_calls_for_run`
is how the pipeline distinguishes a stub-mode run (zero-cost CostRow,
unchanged Phase-2 behavior) from an agent-mode run (real aggregated
CostRow) when writing `FINDINGS.md`/cost-ledger output, including after
crash recovery.

## 8. State transitions

- **Task**: `PENDING → IN_PROGRESS → {DONE | FAILED}`; `PENDING →
  FAILED` (abort/crash sweep); `FAILED → DEAD_LETTER` always, same
  transaction (Phase 2 has no retry-then-park). Enforced by a
  compare-and-swap `UPDATE ... WHERE status = :expected`
  (`sentinel/ledger.py::transition_task`) — an illegal or racing
  transition fails atomically before it can corrupt a run's
  accounting.
- **Run**: `RUNNING → {COMPLETED | FAILED}`. Any task ending
  `FAILED`/`DEAD_LETTER` closes the run `FAILED` by policy (default;
  `--allow-task-failure` overrides for diagnostics) — a monitor
  reporting green while a check silently died is exactly what this
  guards against.
- **Finding**: `(new) → OPEN`; `OPEN → OPEN` advancing `last_seen_utc`
  (monotonic, clamped); `OPEN → RESOLVED` stamping `resolved_at_utc`/
  `resolved_run_id`; recurrence after resolution is a **new INSERT**,
  never a resurrection. The `findings_lifecycle_guard` trigger
  enumerates the only two permitted UPDATE shapes; everything else
  aborts at the database.
- **Crash recovery**: a `RunRecord` found `RUNNING` at the start of
  any invocation is, by definition, an interrupted run (Phase 2 is a
  single local process). `recover_interrupted_runs` sweeps its tasks
  to `FAILED → DEAD_LETTER` and closes it `FAILED` — never touching
  findings, so nothing is lost or falsely resolved.

## 9. Dedup and lifecycle algorithm (`sentinel/lifecycle.py`)

Auto-resolve is **scope-limited, not run-limited**: only
`(surface, check_class)` scopes whose task reached `DONE` this run
participate (`scanned_scopes`). A scope whose task ended
`FAILED`/`DEAD_LETTER` is excluded entirely — its OPEN findings are
neither advanced nor resolved. This is deliberate: a single network
failure must never mass-resolve real findings. Carry-forward derives
work units for **every check class** from currently-OPEN findings
(not just link-scanned paths), so a deleted file or repository still
gets a task for every class that had an open finding against it.

## 10. Content-hash and fingerprint semantics (frozen)

`content_hash = sha256(location + "\n" + normalized_finding_content)`;
`fingerprint = sha256(surface + "\n" + check_class + "\n" +
content_hash)`. **Whole-file, whole-page and whole-surface hashing is
prohibited** — `normalized_finding_content` is always a small
extracted token set (a URL; a label + two numeric tokens; a required
path; a header name), never a raw line or file hash. Consequence
stated plainly: an HTTP status flip (404→503) on the same URL does
not mint a new fingerprint; an unrelated edit elsewhere in the same
file leaves an existing finding's hash unchanged; a line-level
location shift (an edit above the defect) does re-fingerprint it —
inherent to exact-line matching, not designed away.

## 11. Outputs

- **`FINDINGS.md`** (`sentinel/report.py`): append-only, one section
  per run, opening with `<!-- sentinel:run {run_id} -->` and closing
  with `<!-- /sentinel:run {run_id} -->` on its final line — the
  closing marker is what makes a section's completeness mechanically
  checkable. Idempotent by `run_id`; a crash-truncated trailing
  section is repaired (temp-file + atomic replace, scoped strictly to
  that trailing fragment) before the complete section is appended.
  States run kind, ledger status (including `FAILED (partial —
  N/M tasks terminal)` for a reconciled failed run), task/finding
  counts, and per-finding proposal lines. Never edits a monitored
  surface; never auto-converts a finding into a queue item.
- **Structured log** (`sentinel/logs.py`): JSONL, one object/line,
  UTF-8, sorted keys, compact separators. Closed event vocabulary
  (`EVENTS`). Fields: `schema_version`, `ts` (25-char UTC),
  `severity`, `event`, plus `run_id`/`task_id`/`check_class`/`surface`/
  `error_type`/`error_message` where applicable — every free-text
  field passes through `redact()` (path/secret-token redaction,
  control-char stripping, 200-char truncation). Local, gitignored,
  never a committed artifact.
- **`telemetry/cost_ledger.jsonl`**: one `CostRow` per run including
  dev runs, via the existing frozen `telemetry.cost_ledger` module. A
  crash-truncated trailing line is repaired the same way as
  `FINDINGS.md`'s trailing fragment.

## 12. Schema versioning

`schema_version` is `1` on every record type, `Literal[1]` /
`CHECK (schema_version = 1)` at both the Pydantic and SQLite layers —
an unversioned or mis-versioned row cannot be written. A version bump
requires an ADR, a stated read-compatibility policy, and a migration;
ledger rows are never rewritten in place to fit a new schema.

## 13. Validation and failure behavior

Validation happens twice: Pydantic before write, SQLite CHECK/trigger
at write — a row that passes one and fails the other is a bug, not a
tolerated condition. Per-task failure isolation never loses a task
(`sweep_non_terminal`, `fail_and_dead_letter`); a run-level failure
produces a coherent `FAILED` RunRecord, a non-zero process exit
(`1`), and structured log evidence (`run.failed`, `error_type`,
redacted `error_message`) — never a silent, indistinguishable-from-
success crash. Re-running against unchanged surfaces is idempotent:
`findings_new == 0`.

## 14. Data explicitly NOT accepted

No repository not owned by the operator; no private repository
content; no credentials, tokens, or `.env` content at any layer; no
third-party names (public-live writing rule); no personal data (no
users other than the operator — n=1); no machine-local absolute path
in any persisted record; no write access to, or data flow toward, any
monitored surface; no read of `evals/answer_key.jsonl` by any
production or test runtime path (it scores the eval gate — it is
never an implementation oracle); no site gate-statement parity
(deferred, ADR 0004 §3).

## 15. Known Phase-2 limitations

Two of six classes are stubs (real judgment lands at Phase 3); site
parity is deferred and ungated; the schedule fires only while the
operator's Windows session is active (an interactive-token task,
storing no secret — `StartWhenAvailable` catches up a missed
occurrence, and missed-run counts are visible in
`Get-ScheduledTaskInfo`); the unauthenticated GitHub API rate limit
(60 req/hr) bounds live-run cadence and any gate-burst spacing; the
SQLite ledger is single-host and unreplicated.

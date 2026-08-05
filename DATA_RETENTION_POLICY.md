<!-- Describes the system as landed at Phase 2 (BLUEPRINT §6 P2, §11(d);
ADR 0003). Status: in development toward production-ready. No production
claim is made in this document. -->

# DATA_RETENTION_POLICY — ai-portfolio-sentinel

## 1. Scope and status

This policy governs data handling for the Phase 2 deterministic
control plane as landed. Operator-only, n=1: there is no service, no
third party, and no data subject other than the operator.

## 2. Data classes at a glance

| Data | Store | Committed? | Lifetime |
|---|---|---|---|
| Ledger rows (runs/tasks/findings) | `var/sentinel.sqlite3` | No — gitignored | Never deleted; grows with cadence |
| Structured run log | `var/logs/*.jsonl` | No — gitignored | Local only, no rotation at P2 |
| Cost telemetry | `telemetry/cost_ledger.jsonl` | **Yes** | Append-only, never truncated |
| Findings report | `FINDINGS.md` | **Yes** | Append-only, operator commits at gate points |
| Fetched repo/site content | In-memory only, per check | No | Never persisted verbatim |
| Frozen Phase-1 eval bed | `fixtures/`, `evals/` | Yes (frozen) | Immutable — guarded by `scripts/check_phase1_frozen.py` |

## 3. Locally persisted data

`var/sentinel.sqlite3` (the runs/tasks/findings ledger) and
`var/logs/*.jsonl` (structured run logs) are runtime-local, gitignored,
single-host, and not replicated or backed up by the system itself.
Both are derived data in the sense that a fresh run rebuilds current
state — except the historical timeline in the ledger, which is the
entire point of keeping it (rows are never deleted; see §5).

## 4. Public and transient source data

Monitored content (README/EVAL_RESULTS/STATE.md/gate-file text, site
pages) is already-public repository content, fetched read-only per
run via the unauthenticated GitHub API. Fetched bodies are **transient**:
held in memory only for the duration of one check
(`sentinel/inventory/base.py::Content`) and never persisted verbatim.
What survives is the derived finding record — normalized content, a
hash, a location, and a short detail string. The sentinel is not a
mirror or an archive of any monitored repository.

## 5. SQLite ledger: never-delete rule and lifecycle

Rows are **never deleted** — enforced by `runs_never_deleted`,
`tasks_never_deleted`, `findings_never_deleted` (`BEFORE DELETE ...
RAISE(ABORT)` triggers in `contracts/ledger_schema.sql`): this is a
database property, not an application convention, and no code path in
`sentinel/ledger.py` issues a `DELETE FROM` statement
(mechanically checked, `tests/test_read_only_boundary.py`).
Resolution is a status transition with a dated stamp, never a
deletion; recurrence after resolution is a new row. There is no TTL,
rotation, or compaction of ledger rows at Phase 2 — the audit trail
is the deliverable. At the standing daily cadence, growth is a few
dozen rows/day at most; a growth review would only be warranted at a
much larger monitored-repo count than this operator's current n.

## 6. Structured-log handling

Retained locally under `var/logs/`, gitignored, JSONL append-only per
run. No rotation policy exists at Phase 2 — if one is added later it
would delete only log files, never ledger rows, and that distinction
would stay explicit. Logs never contain secrets, tokens, or
machine-local absolute paths: every free-text field passes through
`sentinel/logs.py::redact()` (path-guard reuse from
`contracts/schemas.py`, secret-token pattern redaction, control-
character stripping), and this is dynamically canary-tested
(`tests/test_read_only_boundary.py`).

## 7. Cost-ledger handling

`telemetry/cost_ledger.jsonl` is append-only and **committed** —
BLUEPRINT §9 treats a full calendar month of committed cost telemetry
as evidence, not scratch. Never truncated, never rewritten in place; a
correction would be a new row plus a dated note. Phase 2 rows record
zero tokens and zero micro-euros — a true measurement (zero model
calls this phase), not a placeholder. A crash-truncated trailing line
is repaired (temp-file + atomic replace, scoped to that one line;
`sentinel/costs.py::repair_trailing_fragment`) — every prior row is
untouched.

## 8. `FINDINGS.md` retention

Tracked at the repository root, append-only. Every run appends to it
locally; the operator commits it at gate/close points. **The
scheduled task never commits it** — the scheduler holds no git
credential and performs no git operation of any kind. Never rewritten
to make history look tidier; a superseded proposal is superseded in a
later run's section, never edited out of an earlier one. Contains
only derived findings about the operator's own public repositories —
no third-party content is reproduced.

## 9. Absence of secrets and third-party content

The system holds no credential for any monitored surface at any
layer; the Task Scheduler registration stores no password (an
interactive-token task, `-User $env:USERNAME`, no `-Password`).
Enforcement chain: `.githooks/pre-push`'s leak-grep on every pushed
diff; `.githooks/pre-commit`'s machine-local-path guard; the frozen
contract-level path validators (`contracts/schemas.py`); this
package's own log-message redaction (`sentinel/logs.py`); `.gitignore`
covering every runtime-local path. No third party appears by name
anywhere in this repository (the public-live writing rule). No
personal data of any kind is collected — n=1, operator only, no other
data-subject population exists.

## 10. Operator-owned backup/deletion boundary

The system performs **no** backup, no export, and no deletion of
anything (the never-delete rule in §5 is enforced at the database
layer, not merely a policy choice). Backing up `var/` is the
operator's own decision and action — this policy states that
explicitly rather than implying a capability the system doesn't have.
The only supported way to discard local runtime state is the operator
deleting `var/` by hand, which forfeits local run history and is not
reversible; committed evidence (`FINDINGS.md`, the cost ledger) is
unaffected because it lives in git.

## 11. Committed vs runtime-local — the authoritative split

| Path | Status | Rule |
|---|---|---|
| `FINDINGS.md` | committed | append-only; operator commits at gate points |
| `telemetry/cost_ledger.jsonl` | committed | append-only, never truncated |
| `var/sentinel.sqlite3` | runtime-local, gitignored | never committed; never deleted while it exists |
| `var/logs/*.jsonl` | runtime-local, gitignored | never committed |
| `scripts/sentinel.local.json` | runtime-local, gitignored | may contain a machine-local python path — never tracked |
| `fixtures/`, `evals/` | committed, **frozen** | Phase-1 boundary; guarded by `scripts/check_phase1_frozen.py` |

## 12. Current limitations (dated, honest)

Single host, single copy, no replication, no automated backup, no
encryption at rest beyond the host's own, no retention automation, no
verified restore exercise yet (a Phase-4 recovery-exercise concern
under the production-readiness program). No availability, durability,
or uptime commitment is made or implied anywhere in this document.

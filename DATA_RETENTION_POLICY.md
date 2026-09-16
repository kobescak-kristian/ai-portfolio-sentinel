<!-- Describes the system as landed through Phase 3 (BLUEPRINT §6 P2/P3,
§11(d); ADR 0003; dispatch q77-p3-a). Status: in development toward
production-ready. No production claim is made in this document. -->

# DATA_RETENTION_POLICY — ai-portfolio-sentinel

## 1. Scope and status

This policy governs data handling for the Phase 2 deterministic
control plane, the Phase-3 caged checker agent (§13), and the
Phase-5 GitHub Actions artifact-chain transport (§15). Operator-only,
n=1: there is no service, no third party, and no data subject other
than the operator. The agent's own credential relationship (the
operator's Claude subscription auth, and the Phase-5 WIF federation
path) is covered in `THREAT_MODEL.md` §7; it is unrelated to, and
never mixed with, any monitored repository's data.

## 2. Data classes at a glance

| Data | Store | Committed? | Lifetime |
|---|---|---|---|
| Ledger rows (runs/tasks/findings) | `var/sentinel.sqlite3` | No — gitignored | Never deleted; grows with cadence |
| Structured run log | `var/logs/*.jsonl` | No — gitignored | Local only, no rotation at P2 |
| Cost telemetry | `telemetry/cost_ledger.jsonl` | **Yes** | Append-only, never truncated |
| Findings report | `FINDINGS.md` | **Yes** | Append-only, operator commits at gate points |
| Fetched repo/site content | In-memory only, per check | No | Never persisted verbatim |
| Frozen Phase-1 eval bed | `fixtures/`, `evals/` | Yes (frozen) | Immutable — guarded by `scripts/check_phase1_frozen.py` |
| Durable Phase-5 receipt registry (§16) | `artifacts/phase5_receipt_registry.jsonl` | **Yes** | Append-only, hash-chained; never truncated or rewritten |

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
| Actions artifact bundles (GENESIS/slot/refusal/evidence, §15) | GitHub Actions artifact storage, not git | never committed to this repository; retained 90 days (platform maximum for a public repo) |
| `artifacts/phase5_receipt_registry.jsonl` (§16) | committed, **append-only** | hash-chained receipts of evidence already independently established from artifact bytes; `.githooks/pre-push` blocks any removed or rewritten line, including whole-file deletion |

## 12. Current limitations (dated, honest)

Single host, single copy, no replication, no automated backup, no
encryption at rest beyond the host's own, no retention automation, no
verified restore exercise yet (a Phase-4 recovery-exercise concern
under the production-readiness program). No availability, durability,
or uptime commitment is made or implied anywhere in this document.

## 13. Phase-3 addition: caged checker agent audit data

`agent_calls` rows (SQLite, additive to the same never-delete ledger
as §5 — same triggers, same discipline, no second database) persist,
per attempted judgment call: run/task identity, check class, surface,
model, an auth-mode label, call lifecycle timestamps and terminal
state, reserved and charged EUR micro-euros, SDK turn/result metadata,
token counts and the SDK's own USD cost estimate when available, FX
source/date/retrieval-time/exact rate, tool-call attempts, and
accepted/rejected status with reason. Empty (zero rows) for every
stub-mode run, including the standing scheduled task's runs.

**Never persisted by default**: raw complete prompts, full model
transcripts, authentication material, secrets, or machine-local
credential paths — the same absence-of-secrets discipline as §9,
extended to this table (`THREAT_MODEL.md` §8-9). A row still
`RESERVED` at reconciliation time (a crash mid-call) is never rewritten
to a terminal state — it stays visibly unresolved, and its reservation
is what's conservatively charged into the run's aggregate CostRow
(`sentinel/costs.py::build_agent_cost_row`), never the row itself.

Retention follows §5's rule exactly: never deleted, no TTL or rotation
at Phase 3, same growth-review threshold reasoning (an agent-mode run
adds a few rows per judgment task, not per finding).

One logical judgment task may now hold **two** `agent_calls` rows: a
bounded re-execution is permitted for exactly one failure class
(`adr/0008-judgment-call-execution-reliability`). The first, failed row
is never reused, rewritten, relabelled or deleted when a second attempt
succeeds — both remain, and both are charged.

## 14. ADR-0008 addition: per-proposal tool-attempt audit

`agent_tool_attempts` rows (SQLite, additive to the same never-delete
ledger) persist, per `emit_finding` proposal within one model
invocation: the parent `agent_calls` id, the 1-based proposal ordinal
inside that invocation, the model-proposed reason code, the proposed
evidence count, up to two proposed line coordinates, the outcome
(`ACCEPTED`, `REJECTED`, `DUPLICATE`, `BREAKER_REFUSED`), and a closed
rejection category. This closes the gap recorded in
`PHASE3_GATE_DIAGNOSIS.md`, where per-attempt acceptance or rejection
for a failed call was `UNAVAILABLE_FROM_PERSISTED_EVIDENCE`.

**Storage discipline.** Runtime-local, gitignored, never committed,
never deleted — and, unlike `agent_calls`, never **updated** either: an
individual attempt row has no update lifecycle, so the DDL refuses
`UPDATE` as well as `DELETE`. Run and task identity are not duplicated
here; they are derivable through the parent call.

**Retained model-proposed text, exactly.** Reason codes and coordinates
are the primary evidence and are preferred wherever they carry the same
diagnostic value. One bounded snippet of proposed text is retained, and
only under all of these conditions at once:

- the proposal was REJECTED, and
- its rejection category is `EXCERPT_NOT_VERBATIM` — the one category
  where the proposed text is itself the diagnostic discriminator.

Why it is retained at all: a test in `tests/test_adr0008.py`
(`test_9a_coordinates_and_category_alone_lose_the_required_distinction`)
demonstrates that a substantively correct near-miss (a cited span one
character off) and an outright fabrication reject with the *same*
reason code, the *same* coordinate and the *same* category, so a
coordinates-only record collapses them into one indistinguishable row.
That is precisely the distinction ADR-0008 §4 requires to remain
reconstructible. Every other rejection category retains **no** proposed
text, and an accepted or duplicate proposal retains none either.

**Bounds on that snippet.** At most 80 characters, enforced both in
code (`agents/checker/tools.py::MAX_PROPOSED_EXCERPT_CHARS`) and by a
DDL `CHECK`. It passes through the same first-party redaction boundary
as the structured logs (`sentinel/logs.py::redact` — control-character
stripping, secret-shaped and machine-local-path token replacement)
*before* deterministic truncation, because a monitored document can
itself carry injected secret-shaped text that a model might propose
back. Honest limitation: that boundary normalizes internal whitespace
runs to single spaces, so a near-miss differing from the source line
only by repeated whitespace is not distinguishable in this field.
Proposed reason codes are separately capped at 64 characters.

**Never persisted here**: chain-of-thought, full model responses,
transcripts, raw prompts, arbitrary raw tool JSON, or any unbounded
proposed payload. Nothing from this table is ever emitted into
`FINDINGS.md` or any other public output. The older
`agent_calls.rejection_reason` field no longer carries raw host
validation prose either — that prose embedded the proposed excerpt
verbatim and unbounded, which would have bypassed this section through
an existing text field; it now records the closed category instead.

**Durability boundary, stated plainly.** Attempt records are buffered
in memory during one invocation and flushed in the *same* SQLite
transaction that finalizes the parent call, so a caught in-process
failure can never finalize a call while silently dropping its audit —
a failure on either leg rolls both back and the call stays visibly
`RESERVED`. Host-process death mid-invocation is a different boundary
and is unchanged: the `RESERVED` row survives, reconciliation charges
its reservation conservatively, and the in-memory buffer may simply be
lost. **No crash-proof per-tool telemetry is claimed.**

## 15. P5-B addition: GitHub Actions artifact-chain transport

This section documents the capability landed in P5-B Part 3/3
(`sentinel/phase5/`, ADR-0011 §4). It states what the implementation
does; it does not claim the chain has ever carried a real qualifying
lineage: no real qualification window exists yet (P5-E). The Actions
rehearsal and one capped, one-shot WIF capability probe have both been
dispatched (P5-C); the scheduled lane itself has never been
dispatched.

**What travels in the chain.** Every Actions-era state bundle (a
GENESIS, a slot successor, or a control refusal) carries exactly the
same four authoritative files already governed elsewhere in this
policy: `state/ledger.sqlite3` (a full-fidelity SQLite snapshot taken
via the stdlib backup API, never a raw file copy), `state/FINDINGS.md`,
`state/cost_ledger.jsonl`, and `state/phase5_state.json` (the durable
`Phase5ControlState`), plus three small metadata files: the frozen
`QualificationWindowRecord`, a discriminated manifest, and a SHA-256
sidecar. One-shot markers (`OneShotMarker`) and non-lineage evidence
records travel as separate, smaller artifacts.

**Storage boundary: never git.** The raw operational SQLite database
travels *inside* Actions artifacts and is **never** committed to this
public repository, exactly as §11's never-commit rule for
`var/sentinel.sqlite3` already requires for the local database. GitHub's
Actions artifact store is not authoritative Git history; it is a
platform-managed, time-bounded transport layer, and this repository's
own `.gitignore` (unanchored `*.sqlite3`) is a second fence against any
local rehearsal residue.

**Retention.** 90 days per artifact, the maximum GitHub permits for a
public repository (the default; no shorter value is configured). An
artifact chain older than that window is not recoverable from GitHub
and is not relied upon as long-term history; public closure evidence
(later phases) may cite artifact names, numeric artifact IDs and
manifest SHA-256 hashes as pointers, never claim the artifacts
themselves are permanent.

**Integrity before trust.** A downloaded artifact is untrusted bytes
until `sentinel/phase5/bundle.py::validate_bundle` succeeds against it:
exact file-tree match, per-file SHA-256 digest, manifest/window/
control-state binding, and a `PRAGMA integrity_check` on the carried
SQLite snapshot. Zip extraction (`sentinel/phase5/github_evidence.py`)
independently rejects absolute paths, `..` traversal, symlink entries,
and archives exceeding bounded entry-count/size caps before any file
is written to disk. Predecessor selection is by cryptographic hash and
artifact identity together, never by name, timestamp or cache
freshness; GitHub's Actions cache is never treated as authoritative
state anywhere in this design.

**No new secret surface.** The artifact chain carries no credential,
token, or Anthropic Console identifier of any kind, only the same
classes of ledger/report/cost data this policy already governs
locally, now also transiting a GitHub-managed store between scheduled
runs.

Empty (zero rows) for every stub-mode run, including the standing
scheduled task's runs.

## 16. ADR-0012 Amendment A addition: durable receipt memory

`adr/0012-p5d-replacement-execution-envelope.md` (Amendment A, rule
A1; landed under dispatch q77-p5d-repair-stage1-implement-a, Stage 1)
adds one committed data class, `artifacts/phase5_receipt_registry.jsonl`
(`sentinel/phase5/receipts.py`).

**Why it exists.** Every Phase-5 workflow uploads its artifacts with
`retention-days: 90`, the maximum GitHub permits for a public
repository, after which the platform deletes them (§15). An
artifact-only mechanism therefore cannot truthfully carry permanent
one-shot consumption or a later reconstruction of the authoritative
P5-D history.

**What it is, and is not.**

- GitHub Actions artifact bytes remain the primary evidence while they
  are retained. The registry does not replace them.
- Actions artifacts expire after the platform retention period. The
  historical artifacts the registry currently covers expire between
  2026-11-22T22:09:06Z and 2026-11-23T17:56:54Z.
- The registry is committed, hash-chained (each receipt carries the
  SHA-256 of the immediately preceding canonical receipt; the first
  points at 64 zeroes) and append-only. It is durable memory of
  evidence that was ALREADY independently established: each receipt
  records that a named, numbered artifact existed, was downloaded
  through the safe extraction path, strict-parsed, correlated to its
  run, and produced the stated disposition, together with the SHA-256
  of the verified payload bytes.
- Receipt history is never truncated or rewritten. There is no update,
  delete, truncate or replace API in the module; a semantic event
  (receipt class, purpose, run id, run attempt) is immutable once
  established and a second receipt for it is refused regardless of its
  other fields; `.githooks/pre-push` blocks any push whose diff removes
  or rewrites a registry line, including deletion of the file. A
  missing registry is an error for every ordinary read, never empty
  history.
- Artifact expiry never resets one-shot consumption. Consumption truth
  comes from durable `ONESHOT_MARKER_CONSUMED` receipts, not from
  artifact retention.
- Receipts never recreate or infer missing quality content. A receipt
  carries no marker body, no evidence body, no model output and no
  finding text: only identity, provenance and hashes.
- Original official run `32880880053` has marker-consumption memory and
  an `EXECUTION_INVALID / NO_QUALITY_RESULT` execution-disposition
  receipt (governance reference `q77-p5d-invalid-run-record-a`, owner
  ruling `q77-p5d-replacement-owner-ruling-a`) and **no gate-evidence
  receipt**, because no gate-evidence artifact was ever published for
  that run. The disposition receipt carries no artifact fields and must
  never be read as implying that quality evidence existed.
- Stage 1 landed the registry and its historical contents only.
  **Stage 2A** (dispatch q77-p5d-repair-stage2-implement-a) wires the
  registry into one-shot discovery (`scripts/_phase5_common.py`), the
  structural replacement-eligibility check
  (`sentinel/phase5/replacement.py`), and the repaired P5-E seam
  (`scripts/run_phase5_window_freeze.py`) — durable receipts are
  consulted FIRST for existence, count and correlation truth, with any
  still-retained live artifact hash-verified against its receipt as
  defense in depth. This wiring is structural only: it arms nothing.
  The official gate's `PURPOSE` constant stays the original,
  permanently consumed `P5D_OFFICIAL_SONNET_GATE` value; a new
  structural replacement purpose, `P5D_REPLACEMENT_SONNET_GATE`, is
  defined but no script anywhere constructs or consumes a marker for
  it. The registry's own schema widens additively (two new receipt
  dispositions, `EXECUTION_INVALID / INFRASTRUCTURE_FAILURE` and
  `PUBLICATION_FAILED`, and the new purpose literal) without touching
  any of the four committed historical lines — registry head stays
  `9f060888ea963305a512f534873fe056e8f7fe0c08d05137d26c6d95aeccfc39`.
  Journal, execution envelope, finalizer, and rehearsal implementation
  remain later, separately bounded stages (2B/2C).

No production or production-ready claim follows from this section.

## 17. ADR-0012 Stage 2B-2 addition: gate operational journal and terminal publication

Dispatch q77-p5d-repair-stage2b2-implement-a wires the Stage-2B-1
terminal-publication library into the official-gate runner
(`scripts/run_phase5_official_gate.py`), a new finalizer and
publication-confirmation entrypoint (`scripts/run_phase5_gate_finalizer.py`)
and the official-gate workflow. It arms nothing: the gate `PURPOSE` stays
the permanently consumed original purpose, the replacement purpose is
unreachable, and no replacement marker exists.

**What is written, where.** Per official-gate execution, under the
runner-local work root on the ephemeral GitHub-hosted runner:

- `artifacts/phase5_official_gate.json`: the single terminal evidence
  record (the same file the gate already published), now written
  atomically.
- `artifacts/phase5_official_gate_checks.json`: the gate check lines,
  present only next to a trusted quality record.
- `artifacts/phase5_gate_journal.jsonl`: the operational journal. Every
  field is a closed vocabulary, a bounded integer, a SHA-256 digest or a
  bounded exception class name, so it cannot carry a model response,
  finding text, answer content, score, quality disposition, credential,
  token or local path. It is never echoed to workflow logs.
- `terminal-staging/` and `terminal-quarantine/`: siblings of the
  publication directory holding atomic-write temporaries and any
  quarantined untrusted or unexpected bytes. `confirm-download/` holds
  the confirmation step's re-download of the published artifact.

**What is published.** Exactly the three `artifacts/` files above, named
explicitly in the upload step (never the directory), in the existing
`sentinel-p5-gate-evidence-r<run>-a<attempt>` Actions artifact with
`overwrite: false` and the platform-maximum 90-day retention (§15).
Staging, quarantine, the confirmation download, the gate database and
the FX state are never uploaded and are destroyed with the ephemeral
runner.

**Suppressed operator output.** Before publication is confirmed, the
execute step points its standard output and error at `/dev/null` so no
provisional quality signal, SDK or CLI diagnostic can reach the public
workflow log. That output is discarded, not captured: it is never stored
or published anywhere. A failed run's CLI diagnostics are therefore not
retained.

**Nothing durable in git.** No journal, terminal record, checks file or
publication verdict is committed to this repository. The durable receipt
registry (§16) is byte-unchanged by this stage; recording a replacement
receipt remains later, separately governed work.

No production or production-ready claim follows from this section.

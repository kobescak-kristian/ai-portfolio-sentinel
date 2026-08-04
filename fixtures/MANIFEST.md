<!-- SYNTHETIC FIXTURE BED — public manifest. Aggregates only: the
per-snapshot injection matrix, per-injection index, deleted-file
lists and per-surface clean/dirty labels are deliberately NOT in this
file. -->

# Fixture corpus MANIFEST — ai-portfolio-sentinel eval bed

Everything under `fixtures/repos/` is synthetic. Eight snapshot
repositories form the frozen Phase 1 eval bed (BLUEPRINT §5, ADR
0004): `synthetic-01` … `synthetic-06` carry the injected defects;
`synthetic-07` and `synthetic-08` are all-clean baselines (owner
ruling 2026-08-04 resolving the readme-structure feasibility
correction; snapshot count 8 supersedes the ~6 target sizing).

## Fleet aggregates (final counts)

- Snapshots: **8**
- Injected positives: **60** — **10 per class** across the six frozen
  classes (SPEC §2): broken-link · number-mismatch ·
  stale-STATE-marker · missing-required-file ·
  missing-synthetic-label · readme-structure
- readme-structure composition: 4 READMEs × 2 header removals +
  2 READMEs × 1 section reorder; root `README.md` files only; a
  reordered README carries no other line-level injection
- Clean units: **166**, exhaustively enumerated in
  `evals/clean_surfaces.jsonl` (committed at the freeze; 158 at
  generation + 8 unlabeled-by-design units restored by the D6 item-1
  reconciliation of 2026-08-04, marked by their `provenance` field —
  fully scorable, excluded only from review control-eligibility)
- Fixture links: 40 total, 10 dead (the broken-link positives)

No answer-key labels or per-injection mappings were published before
the freeze; the key, the clean inventory and the review evidence land
together in the freeze commit. Observable artifacts — absent required
files, `.example.invalid` URLs — are not claimed hidden; the key adds
the ground-truth rows, not secrecy about what a reader can see.

## Baseline shape (identical for every snapshot, pre-injection)

Every snapshot starts from the same baseline file set and exact
per-file counts:

| Baseline unit | Count per snapshot |
|---|---|
| Files (README.md, EVAL_RESULTS.md, STATE.md, .githooks/pre-push, evals/eval_config.yaml) | 5 |
| README links / EVAL_RESULTS links | 3 / 2 |
| README figures / EVAL_RESULTS figures (mirrored pairs) | 4 / 4 |
| Synthetic labels (one adjacent line per figure) | 8 |
| STATE current-state facts / dated log entries | 3 / 3 |
| Unlabeled-by-design numeric lines (dates, versions) | 3 |
| Required README headers (`## Problem`, `## Solution`, `## System`, `## Outcome`, `## Version Log` — exact order) | 5 |

## Surface and location grammar (frozen — fingerprints depend on it)

- Surface: `<snapshot>/<repo-relative-path>`, e.g.
  `synthetic-01/README.md`. No scheme, host, colon, leading slash,
  backslash, `..` segment or control character.
- Location: `<path>:<line>` for line-level classes; bare `<path>` for
  file-level classes (`missing-required-file`; readme-structure CLEAN
  units). readme-structure POSITIVE locations follow the frozen
  semantics in `evals/SCORING.md`.

## Required-file set (frozen)

`STATE.md` · `.githooks/pre-push` · `evals/eval_config.yaml`
(the fixture gate file is a placeholder standing in for the required
path; the fixture hook is inert, comment-only content).

## Authoring constraints

- Every file opens with a file-type-compatible synthetic-fixture
  banner; every figure requiring a label carries `(synthetic figure)`
  on the adjacent line.
- Dead URLs are unique and use the RFC 2606 reserved `.invalid` TLD
  (`https://<label>.example.invalid/<path>`) — syntactically valid,
  permanently unresolvable, unsquattable. Live links use stable
  well-known roots only.
- No machine-local absolute paths, no secret-shaped strings, no
  private operating-vocabulary tokens anywhere in fixture content
  (swept before every fixture-touching commit).

## Link truth rule

`fixtures/link_truth.jsonl` maps every fixture URL to its declared
status (`live` / `dead`). Fixture link liveness is a committed corpus
property: the eval harness resolves fixture links through this map or
a stub — never live network requests — so transient third-party HTTP
behavior cannot affect the official gate.

## Newline rule (CRLF)

Fixture files are authored with LF newlines; the committed blobs are
LF. Before the freeze commit exists, semantic and line-location
checks run against normalized working-tree text; from the candidate
freeze commit onward, byte and hash verification reads committed blob
content via `git show <sha>:<path>`.

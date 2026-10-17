#!/usr/bin/env python
"""Establish the durable P5-D replacement receipts and record its CostRows
(ADR-0012 Amendment A, rule A1; ADR-0011 §5 daily-slot amendment, P5-E
prerequisite; owner approval 2026-10-17).

Local operator utility, not a workflow. GET/download only against GitHub:
no model call, no OIDC/WIF exchange, no provider call, no GitHub mutation.
The bearer token is read from ``GITHUB_TOKEN`` and never printed.

Two receipts for the ONE replacement execution that ran (run 37388164100
at its own source A, ``REPLACEMENT_SOURCE_SHA``) are established, in
order, after the four historical receipts already in the registry:

1. replacement one-shot marker consumed       (ONESHOT_MARKER_CONSUMED)
2. replacement gate evidence, disposition GREEN (GATE_EVIDENCE)

The gate-evidence artifact holds exactly the three files the official-gate
workflow uploads (terminal record, checks, journal); fewer or extra files
refuse. The terminal record passes the shared ``verify_terminal_bytes``
trust decision as TRUSTED_QUALITY, the journal passes the strict journal
reader with integrity OK and no signal, runner exception or terminal-write
failure, and the checks file holds the recorded 27 check lines with none
failing. All three file digests are bound into ``FACTS_SHA256``.

and the gate evidence's carried CostRows are appended to the committed
cost ledger exactly once (rows already present exactly once are left as
they are; any other count refuses).

Every source is independently discovered, digest-checked against the
artifact digests recorded in STATE on 2026-10-05, downloaded through the
existing safe extraction path, strict-parsed and correlated in memory
FIRST; only then may anything be written.

Dry-run / real-run handshake (same as the historical tool). ``--dry-run``
verifies everything, prints the source-derived facts and ``FACTS_SHA256``
(SHA-256 of the canonical bundle of both receipt facts and the serialized
CostRows) and writes nothing. The real run requires
``--expect-facts-sha256`` from a reviewed dry run, re-verifies every
source, recomputes the hash, and STOPS before any write if it differs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.establish_phase5_historical_receipts import (  # noqa: E402
    API_URL,
    EXPECTED_EVENT,
    FACT_KEYS,
    GATE_WORKFLOW_IDENTITY,
    REPOSITORY,
    EstablishmentRefused,
    HistoricalArtifact,
    HistoricalRun,
    _artifact_metadata,
    _correlate_marker,
    _discover_exact,
    _download_single_payload,
    _fact,
    _run_metadata,
    _sha256_bytes,
    _verify_run_binding,
    facts_sha256,
)
from sentinel.phase5.bundle import create_fresh_root  # noqa: E402
from sentinel.phase5.evidence_records import GateEvidenceRecord, validate_replacement_provenance  # noqa: E402
from sentinel.phase5.github_evidence import GithubEvidenceClient, GithubEvidenceError  # noqa: E402
from sentinel.phase5.journal import read_journal, summarize_journal  # noqa: E402
from sentinel.phase5.models import OneShotMarker  # noqa: E402
from sentinel.phase5.receipts import (  # noqa: E402
    DEFAULT_REGISTRY_PATH,
    GATE_PAYLOAD_FILENAME,
    MARKER_PAYLOAD_FILENAME,
    append_receipt,
    load_registry,
    receipt_sha256,
    registry_head_sha256,
)
from sentinel.phase5.replacement import (  # noqa: E402
    OWNER_RULING_ID,
    REPLACEMENT_OF_RUN_ID,
    REPLACEMENT_PURPOSE,
    REPLACEMENT_RUN_ID,
    REPLACEMENT_SOURCE_SHA,
)
from sentinel.phase5.terminal import (  # noqa: E402
    CHECKS_FILENAME,
    JOURNAL_FILENAME,
    MAX_TERMINAL_BYTES,
    TerminalIdentity,
    verify_terminal_bytes,
)
from telemetry.cost_ledger import append_cost_row, read_cost_rows, serialize_cost_row  # noqa: E402

# ---------------------------------------------------------------------------
# Recorded history (STATE 2026-10-05 replacement entry; never edited)
# ---------------------------------------------------------------------------

REPLACEMENT_RUN = HistoricalRun(
    run_id=REPLACEMENT_RUN_ID,
    run_attempt=1,
    source_sha=REPLACEMENT_SOURCE_SHA,
    workflow_identity=GATE_WORKFLOW_IDENTITY,
)
REPLACEMENT_MARKER = HistoricalArtifact(
    artifact_id=11379088051,
    artifact_name="sentinel-p5-oneshot-p5d-replacement-sonnet-gate-r37388164100",
    payload_filename=MARKER_PAYLOAD_FILENAME,
)
REPLACEMENT_GATE_EVIDENCE = HistoricalArtifact(
    artifact_id=11380108778,
    artifact_name="sentinel-p5-gate-evidence-r37388164100-a1",
    payload_filename=GATE_PAYLOAD_FILENAME,
)
RECORDED_DIGESTS = {
    REPLACEMENT_MARKER.artifact_id: "sha256:85d8917e1fb116486856ced73d893488fb3b5ae798771c6cec0884057c03773c",
    REPLACEMENT_GATE_EVIDENCE.artifact_id: "sha256:95b5799bd16c0deef7e469b8ef6a6cbe681f9db189a29b664488e1471c0442ea",
}
# Exactly the files sentinel-official-gate.yml uploads as gate evidence
# (identical at source A and at the current main).
GATE_ARTIFACT_FILES = (GATE_PAYLOAD_FILENAME, CHECKS_FILENAME, JOURNAL_FILENAME)
EXPECTED_CHECK_LINES = 27  # STATE 2026-10-05: "27 of 27 checks PASS"
REPLACEMENT_IDENTITY = TerminalIdentity(
    workflow_identity=GATE_WORKFLOW_IDENTITY, run_id=REPLACEMENT_RUN_ID, run_attempt=1,
    event=EXPECTED_EVENT, ref="refs/heads/main", source_sha=REPLACEMENT_SOURCE_SHA,
    expected_source_sha=REPLACEMENT_SOURCE_SHA, purpose=REPLACEMENT_PURPOSE,
)
EXPECTED_DISPOSITION = "GREEN"
EXPECTED_COST_RUN_IDS = ("r-bcacf20af69140c89b25ebb82977a296", "r-5d4869f8387d42419f6242f525415277")
EXPECTED_COST_TOTAL_EUR_MICROS = 1_073_575
HISTORICAL_RECEIPT_COUNT = 4


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without network)
# ---------------------------------------------------------------------------


def build_fact_bundle(facts: list[dict], cost_rows: tuple, gate_file_sha256: dict) -> bytes:
    """Canonical bytes of both receipt facts (exactly FACT_KEYS each), the
    serialized CostRows in carried order, and the SHA-256 of every file in
    the gate-evidence artifact."""
    if len(facts) != 2:
        raise EstablishmentRefused(f"fact bundle must hold exactly two receipt facts, got {len(facts)}")
    normalized = []
    for fact in facts:
        if set(fact) != set(FACT_KEYS):
            raise EstablishmentRefused("fact bundle contains a fact with an unexpected key set")
        normalized.append({key: fact[key] for key in FACT_KEYS})
    if sorted(gate_file_sha256) != sorted(GATE_ARTIFACT_FILES):
        raise EstablishmentRefused("fact bundle must bind exactly the three gate-evidence files")
    bundle = {
        "receipts": normalized,
        "cost_rows": [serialize_cost_row(row) for row in cost_rows],
        "gate_artifact_files": dict(sorted(gate_file_sha256.items())),
    }
    return json.dumps(bundle, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def check_registry_precondition(receipts: tuple) -> None:
    """Exactly the four historical receipts and nothing for the
    replacement purpose: this tool is a one-time establishment."""
    if len(receipts) != HISTORICAL_RECEIPT_COUNT:
        raise EstablishmentRefused(
            f"registry holds {len(receipts)} receipts; expected exactly the {HISTORICAL_RECEIPT_COUNT} historical ones"
        )
    if any(r.purpose == REPLACEMENT_PURPOSE for r in receipts):
        raise EstablishmentRefused("a replacement-purpose receipt already exists; refusing to establish again")


def cost_rows_to_append(cost_rows: tuple, ledger_rows: list) -> tuple:
    """Rows absent from the committed ledger are returned for appending;
    rows present exactly once are already recorded. A partial state or a
    duplicate refuses."""
    committed = [serialize_cost_row(row) for row in ledger_rows]
    counts = [committed.count(serialize_cost_row(row)) for row in cost_rows]
    if any(count > 1 for count in counts):
        raise EstablishmentRefused("a replacement CostRow appears more than once in the committed ledger")
    if 0 < sum(counts) < len(cost_rows):
        raise EstablishmentRefused("the committed ledger holds only some of the replacement CostRows")
    return tuple(row for row, count in zip(cost_rows, counts) if count == 0)


def check_checks_file(data: bytes) -> None:
    """The gate's check lines: a JSON list of exactly the recorded number
    of non-empty strings, none of which reports FAIL (a GREEN record has
    no failed check)."""
    if len(data) > MAX_TERMINAL_BYTES:
        raise EstablishmentRefused("checks file is oversize")
    try:
        lines = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EstablishmentRefused("checks file is not strict UTF-8 JSON") from exc
    if not isinstance(lines, list) or not all(isinstance(line, str) and line.strip() for line in lines):
        raise EstablishmentRefused("checks file is not a list of non-empty check lines")
    if len(lines) != EXPECTED_CHECK_LINES:
        raise EstablishmentRefused(f"checks file holds {len(lines)} check lines, recorded {EXPECTED_CHECK_LINES}")
    if any(line.rstrip().endswith("FAIL") for line in lines):
        raise EstablishmentRefused("checks file reports a failed check")


def check_journal_file(path: Path) -> None:
    """Strict journal read: integrity OK, at least one event, and no
    observed signal, runner exception or terminal-write failure."""
    result = read_journal(path)
    if result.integrity != "OK" or not result.events:
        raise EstablishmentRefused(f"gate journal integrity is {result.integrity} (OK with events required)")
    summary = summarize_journal(result)
    if summary.signals or summary.runner_exception_cause is not None or summary.terminal_write_failed:
        raise EstablishmentRefused("gate journal records a signal, runner exception or terminal-write failure")


def check_gate_record(record: GateEvidenceRecord) -> None:
    validate_replacement_provenance(record, expected_source_sha=REPLACEMENT_SOURCE_SHA)
    observed = (record.github_run_id, record.run_attempt, record.event, record.disposition)
    expected = (REPLACEMENT_RUN.run_id, REPLACEMENT_RUN.run_attempt, EXPECTED_EVENT, EXPECTED_DISPOSITION)
    if observed != expected:
        raise EstablishmentRefused("gate evidence does not correlate with the recorded replacement history")
    if tuple(row.run_id for row in record.cost_rows) != EXPECTED_COST_RUN_IDS:
        raise EstablishmentRefused("gate evidence CostRow run ids differ from the recorded run ids")
    total = sum(row.cost_eur_micros for row in record.cost_rows)
    if total != EXPECTED_COST_TOTAL_EUR_MICROS or record.accounted_total_eur_micros != total:
        raise EstablishmentRefused("gate evidence cost total differs from the recorded 1,073,575 micro-EUR")


# ---------------------------------------------------------------------------
# Source verification (GET/download only)
# ---------------------------------------------------------------------------


def _discover_with_digest(client, spec: HistoricalArtifact):
    ref, expires_at = _discover_exact(client, REPLACEMENT_RUN, spec)
    if _artifact_metadata(client, spec.artifact_id).get("digest") != RECORDED_DIGESTS[spec.artifact_id]:
        raise EstablishmentRefused(f"artifact {spec.artifact_id} digest differs from the recorded digest")
    return ref, expires_at


def _download_exact_files(client, work_root: Path, ref, spec: HistoricalArtifact, expected: tuple, index: int) -> dict:
    """Download through the safe extraction path; the extracted tree must
    be exactly ``expected`` as regular, non-symlink files at the root, with
    no directory and no extra or missing entry."""
    root = client.download_artifact(ref, work_root, work_root / f"artifact-{index}")
    entries = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    if entries != sorted(expected):
        raise EstablishmentRefused(
            f"artifact {spec.artifact_id} extracted entry set {entries} is not exactly {sorted(expected)}"
        )
    files = {}
    for name in expected:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise EstablishmentRefused(f"artifact {spec.artifact_id} entry {name} is not a regular file")
        files[name] = path
    return files


def verify_replacement_sources(client, work_root: Path):
    """Verify both sources; return (facts, cost_rows, gate_file_sha256,
    expiries). Raises before any write."""
    _verify_run_binding(client, REPLACEMENT_RUN)
    run = _run_metadata(client, REPLACEMENT_RUN.run_id)
    if (run.get("status"), run.get("conclusion")) != ("completed", "success"):
        raise EstablishmentRefused("replacement run is not completed with conclusion success")

    ref, marker_expiry = _discover_with_digest(client, REPLACEMENT_MARKER)
    payload = _download_single_payload(client, work_root, ref, REPLACEMENT_MARKER, 1)
    try:
        marker = OneShotMarker.model_validate_json(payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal
        raise EstablishmentRefused("replacement marker failed strict OneShotMarker parse") from exc
    _correlate_marker(marker, REPLACEMENT_RUN, REPLACEMENT_PURPOSE)
    if (marker.replacement_of_run_id, marker.owner_ruling_id) != (REPLACEMENT_OF_RUN_ID, OWNER_RULING_ID):
        raise EstablishmentRefused("replacement marker does not bind the frozen original run and owner ruling")
    marker_fact = _fact(
        receipt_class="ONESHOT_MARKER_CONSUMED", purpose=REPLACEMENT_PURPOSE,
        github_run_id=REPLACEMENT_RUN.run_id, run_attempt=REPLACEMENT_RUN.run_attempt,
        source_sha=REPLACEMENT_RUN.source_sha, artifact_name=REPLACEMENT_MARKER.artifact_name,
        artifact_id=REPLACEMENT_MARKER.artifact_id, payload_filename=REPLACEMENT_MARKER.payload_filename,
        payload_sha256=_sha256_bytes(payload), disposition="CONSUMED", owner_ruling_id=OWNER_RULING_ID,
    )

    ref, gate_expiry = _discover_with_digest(client, REPLACEMENT_GATE_EVIDENCE)
    files = _download_exact_files(client, work_root, ref, REPLACEMENT_GATE_EVIDENCE, GATE_ARTIFACT_FILES, 2)
    payload = files[GATE_PAYLOAD_FILENAME].read_bytes()
    verdict = verify_terminal_bytes(payload, REPLACEMENT_IDENTITY)
    if verdict.kind != "TRUSTED_QUALITY":
        raise EstablishmentRefused(f"replacement terminal record verdict is {verdict.kind}, not TRUSTED_QUALITY")
    try:
        record = GateEvidenceRecord.model_validate_json(payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise EstablishmentRefused("replacement gate evidence failed strict parse") from exc
    check_gate_record(record)
    checks = files[CHECKS_FILENAME].read_bytes()
    check_checks_file(checks)
    check_journal_file(files[JOURNAL_FILENAME])
    gate_file_sha256 = {
        GATE_PAYLOAD_FILENAME: _sha256_bytes(payload),
        CHECKS_FILENAME: _sha256_bytes(checks),
        JOURNAL_FILENAME: _sha256_bytes(files[JOURNAL_FILENAME].read_bytes()),
    }
    gate_fact = _fact(
        receipt_class="GATE_EVIDENCE", purpose=REPLACEMENT_PURPOSE,
        github_run_id=REPLACEMENT_RUN.run_id, run_attempt=REPLACEMENT_RUN.run_attempt,
        source_sha=REPLACEMENT_RUN.source_sha, artifact_name=REPLACEMENT_GATE_EVIDENCE.artifact_name,
        artifact_id=REPLACEMENT_GATE_EVIDENCE.artifact_id,
        payload_filename=REPLACEMENT_GATE_EVIDENCE.payload_filename,
        payload_sha256=_sha256_bytes(payload), disposition=record.disposition, owner_ruling_id=OWNER_RULING_ID,
    )
    expiries = {REPLACEMENT_MARKER.artifact_name: marker_expiry, REPLACEMENT_GATE_EVIDENCE.artifact_name: gate_expiry}
    return [marker_fact, gate_fact], tuple(record.cost_rows), gate_file_sha256, expiries


def _print_facts(
    facts: list[dict], cost_rows: tuple, pending: tuple, gate_file_sha256: dict, expiries: dict[str, str]
) -> None:
    for index, fact in enumerate(facts, start=1):
        for key in FACT_KEYS:
            print(f"FACT[{index}].{key}: {fact[key]}")
    for index, row in enumerate(cost_rows, start=1):
        state = "TO_APPEND" if row in pending else "ALREADY_RECORDED"
        print(f"COST_ROW[{index}]: {state} {serialize_cost_row(row)}")
    for name, digest in sorted(gate_file_sha256.items()):
        print(f"GATE_FILE_SHA256: {name} {digest}")
    for name, expires_at in expiries.items():
        print(f"EXPIRES_AT: {name} {expires_at}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--registry", type=Path, default=REPO_ROOT / DEFAULT_REGISTRY_PATH)
    parser.add_argument("--cost-ledger", type=Path, default=REPO_ROOT / "telemetry" / "cost_ledger.jsonl")
    parser.add_argument("--expect-facts-sha256", default=None)
    args = parser.parse_args(argv)

    if args.dry_run and args.expect_facts_sha256 is not None:
        print("error: --expect-facts-sha256 is only for the real run", file=sys.stderr)
        return 2
    if not args.dry_run and not re.fullmatch(r"[0-9a-f]{64}", args.expect_facts_sha256 or ""):
        print("error: real run requires --expect-facts-sha256 <64 lowercase hex> from a reviewed dry run", file=sys.stderr)
        return 2

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("error: GITHUB_TOKEN is not set (read-only GET/download only)", file=sys.stderr)
        return 2
    client = GithubEvidenceClient(api_url=API_URL, repository=REPOSITORY, token=token)
    del token

    temp_parent = Path(tempfile.mkdtemp(prefix="p5-replacement-receipts-"))
    print("work root: fresh OS temporary directory (transient extraction only, not evidence)")
    try:
        work_root = create_fresh_root(temp_parent, temp_parent / "work")
        try:
            check_registry_precondition(load_registry(args.registry))
            facts, cost_rows, gate_file_sha256, expiries = verify_replacement_sources(client, work_root)
            pending = cost_rows_to_append(cost_rows, read_cost_rows(args.cost_ledger))
        except (EstablishmentRefused, GithubEvidenceError, OSError, ValueError, RuntimeError) as exc:
            print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        digest = facts_sha256(build_fact_bundle(facts, cost_rows, gate_file_sha256))
        _print_facts(facts, cost_rows, pending, gate_file_sha256, expiries)
        print(f"FACTS_SHA256: {digest}")

        if args.dry_run:
            print(
                "DRY RUN: both replacement facts and the CostRows verified; nothing written. "
                "recorded_at_utc, prev_receipt_sha256, each receipt SHA-256 and the registry head "
                "are established only by the real append and are NOT claimed here."
            )
            return 0

        if digest != args.expect_facts_sha256:
            print("STOP: facts hash mismatch between the reviewed dry run and this real run; nothing written", file=sys.stderr)
            return 1

        try:
            for row in pending:
                append_cost_row(args.cost_ledger, row)
                print(f"COST_ROW_APPENDED: {row.run_id}")
            for index, fact in enumerate(facts, start=1):
                receipt = append_receipt(
                    args.registry, schema_version=1, replacement_of_run_id=REPLACEMENT_OF_RUN_ID, **fact,
                )
                print(f"APPENDED[{index}]: {receipt.receipt_class} {receipt.purpose} run={receipt.github_run_id}")
            receipts = load_registry(args.registry)
            if cost_rows_to_append(cost_rows, read_cost_rows(args.cost_ledger)):
                raise EstablishmentRefused("post-append ledger check: a replacement CostRow is still missing")
        except Exception as exc:  # noqa: BLE001 - report truthfully; never repair
            print(f"STOP: append/reload failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        if len(receipts) != HISTORICAL_RECEIPT_COUNT + 2:
            print(f"STOP: expected exactly six receipts after establishment, found {len(receipts)}", file=sys.stderr)
            return 1
        for index, receipt in enumerate(receipts, start=1):
            print(f"RECEIPT_SHA256[{index}]: {receipt_sha256(receipt)}")
        print(f"REGISTRY_HEAD_SHA256: {registry_head_sha256(receipts)}")
        line_count = args.registry.read_bytes().count(b"\n")
        print(f"REGISTRY_LINE_COUNT: {line_count}")
        return 0
    finally:
        shutil.rmtree(temp_parent, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

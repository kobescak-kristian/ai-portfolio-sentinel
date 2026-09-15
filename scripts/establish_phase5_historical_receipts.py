#!/usr/bin/env python
"""Establish the historical Phase-5 durable receipts (ADR-0012 Amendment
A, rule A1; dispatch q77-p5d-repair-stage1-implement-a, Stage 1).

Local operator utility, not a workflow. GET/download only: no model
call, no OIDC/WIF exchange, no provider call, no GitHub mutation. The
bearer token is read from ``GITHUB_TOKEN`` and never printed.

Four historical semantic events are established, in this order:

1. P5-C one-shot marker consumed            (run 32783229864)
2. P5-C probe evidence CAPABILITY_PASS       (run 32783229864)
3. original P5-D one-shot marker consumed    (run 32880880053)
4. original P5-D EXECUTION_INVALID / NO_QUALITY_RESULT disposition

Establishment is atomic in intent: EVERY historical source is
independently discovered, downloaded through the existing safe
extraction path, strict-parsed and correlated in memory FIRST; only
after all four proposed facts pass may registry writing begin.

Dry-run / real-run handshake. ``--dry-run`` verifies everything and
prints the immutable source-derived facts plus ``FACTS_SHA256`` (the
SHA-256 of the canonical ordered four-fact bundle) and writes nothing.
The real run requires ``--expect-facts-sha256``, independently
re-verifies every source, recomputes the same hash, and STOPS before
any append if it differs. ``recorded_at_utc``, ``prev_receipt_sha256``,
each receipt's SHA-256 and the registry head are established only by
the real append and are deliberately outside the fact bundle.

Transient artifact extraction happens in a fresh OS temporary directory
(never a repository path); those bytes are not a deliverable, are not
staged, and are not cited as evidence themselves.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sentinel.phase5 import artifact_names  # noqa: E402
from sentinel.phase5.bundle import create_fresh_root  # noqa: E402
from sentinel.phase5.evidence_records import ProbeEvidenceRecord  # noqa: E402
from sentinel.phase5.github_evidence import GithubEvidenceClient, GithubEvidenceError  # noqa: E402
from sentinel.phase5.models import OneShotMarker  # noqa: E402
from sentinel.phase5.receipts import (  # noqa: E402
    EXECUTION_INVALID_NO_QUALITY_RESULT,
    GATE_PAYLOAD_FILENAME,
    MARKER_PAYLOAD_FILENAME,
    PROBE_PAYLOAD_FILENAME,
    DEFAULT_REGISTRY_PATH,
    append_receipt,
    load_registry,
    receipt_sha256,
    registry_head_sha256,
)
from telemetry.cost_ledger import read_cost_rows, serialize_cost_row  # noqa: E402

API_URL = "https://api.github.com"
REPOSITORY = "kobescak-kristian/ai-portfolio-sentinel"
EXPECTED_EVENT = "workflow_dispatch"
PROBE_WORKFLOW_IDENTITY = ".github/workflows/sentinel-wif-probe.yml"
GATE_WORKFLOW_IDENTITY = ".github/workflows/sentinel-official-gate.yml"

# The twelve immutable, source-derived comparison fields (dispatch
# "DRY-RUN CORRECTION"). Nothing established by the append is here.
FACT_KEYS = (
    "receipt_class",
    "purpose",
    "github_run_id",
    "run_attempt",
    "source_sha",
    "artifact_name",
    "artifact_id",
    "payload_filename",
    "payload_sha256",
    "disposition",
    "governance_ref",
    "owner_ruling_id",
)


class EstablishmentRefused(RuntimeError):
    """A historical source is unavailable, expired, ambiguous, malformed
    or mismatched. Raised BEFORE any registry write."""


# ---------------------------------------------------------------------------
# Historical constants (verbatim from the dispatch; never edited)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoricalRun:
    run_id: str
    run_attempt: int
    source_sha: str
    workflow_identity: str


@dataclass(frozen=True)
class HistoricalArtifact:
    artifact_id: int
    artifact_name: str
    payload_filename: str


P5C_RUN = HistoricalRun(
    run_id="32783229864",
    run_attempt=1,
    source_sha="f5b2ae6e393252594efa5b48e1f86a1f2296f797",
    workflow_identity=PROBE_WORKFLOW_IDENTITY,
)
P5C_MARKER = HistoricalArtifact(
    artifact_id=9540505807,
    artifact_name="sentinel-p5-oneshot-p5c-wif-probe-r32783229864",
    payload_filename=MARKER_PAYLOAD_FILENAME,
)
P5C_PROBE_EVIDENCE = HistoricalArtifact(
    artifact_id=9540511349,
    artifact_name="sentinel-p5-probe-evidence-r32783229864-a1",
    payload_filename=PROBE_PAYLOAD_FILENAME,
)
P5C_EXPECTED_DISPOSITION = "CAPABILITY_PASS"

P5D_ORIGINAL_RUN = HistoricalRun(
    run_id="32880880053",
    run_attempt=1,
    source_sha="eef88a289cf465ad352ee223221d5497465469b3",
    workflow_identity=GATE_WORKFLOW_IDENTITY,
)
P5D_ORIGINAL_MARKER = HistoricalArtifact(
    artifact_id=9575720463,
    artifact_name="sentinel-p5-oneshot-p5d-official-sonnet-gate-r32880880053",
    payload_filename=MARKER_PAYLOAD_FILENAME,
)
P5D_ORIGINAL_INCIDENT_RECORD_REF = "q77-p5d-invalid-run-record-a"
P5D_ORIGINAL_OWNER_RULING_ID = "q77-p5d-replacement-owner-ruling-a"


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without network)
# ---------------------------------------------------------------------------


def build_fact_bundle(facts: list[dict]) -> bytes:
    """Canonical bytes of the ordered fact list: exactly FACT_KEYS per
    fact, sorted keys, compact separators, UTF-8. Refuses any fact whose
    key set is not exactly FACT_KEYS so append-established fields can
    never leak in."""
    if len(facts) != 4:
        raise EstablishmentRefused(f"fact bundle must hold exactly four facts, got {len(facts)}")
    normalized = []
    for fact in facts:
        if set(fact) != set(FACT_KEYS):
            raise EstablishmentRefused("fact bundle contains a fact with an unexpected key set")
        normalized.append({key: fact[key] for key in FACT_KEYS})
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def facts_sha256(bundle: bytes) -> str:
    return hashlib.sha256(bundle).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fact(**fields) -> dict:
    fact = {key: None for key in FACT_KEYS}
    for key, value in fields.items():
        if key not in FACT_KEYS:
            raise EstablishmentRefused(f"unexpected fact field {key!r}")
        fact[key] = value
    return fact


# ---------------------------------------------------------------------------
# Source verification (GET/download only)
# ---------------------------------------------------------------------------


def _artifact_metadata(client: GithubEvidenceClient, artifact_id: int) -> dict:
    data = client._get_json(f"/repos/{REPOSITORY}/actions/artifacts/{artifact_id}")
    if not isinstance(data, dict):
        raise EstablishmentRefused(f"artifact {artifact_id} metadata has an unexpected shape")
    return data


def _run_metadata(client: GithubEvidenceClient, run_id: str) -> dict:
    data = client._get_json(f"/repos/{REPOSITORY}/actions/runs/{run_id}")
    if not isinstance(data, dict):
        raise EstablishmentRefused(f"run {run_id} metadata has an unexpected shape")
    return data


def _verify_run_binding(client: GithubEvidenceClient, run: HistoricalRun) -> None:
    data = _run_metadata(client, run.run_id)
    observed = (
        data.get("path"),
        data.get("event"),
        int(data.get("run_attempt", 0)),
        str(data.get("head_sha", "")).lower(),
    )
    expected = (run.workflow_identity, EXPECTED_EVENT, run.run_attempt, run.source_sha)
    if observed != expected:
        raise EstablishmentRefused(f"run {run.run_id} binding mismatch (path/event/attempt/head_sha)")


def _discover_exact(client: GithubEvidenceClient, run: HistoricalRun, spec: HistoricalArtifact):
    refs = client.list_artifacts_for_run(run.run_id)
    matches = [r for r in refs if r.id == spec.artifact_id]
    if len(matches) != 1:
        raise EstablishmentRefused(
            f"artifact {spec.artifact_id} is not exactly-once discoverable (unexpired) for run {run.run_id}"
        )
    ref = matches[0]
    if ref.name != spec.artifact_name or ref.workflow_run_id != run.run_id:
        raise EstablishmentRefused(f"artifact {spec.artifact_id} name/run binding mismatch")
    meta = _artifact_metadata(client, spec.artifact_id)
    if meta.get("expired") is not False or meta.get("name") != spec.artifact_name:
        raise EstablishmentRefused(f"artifact {spec.artifact_id} metadata mismatch or expired")
    workflow_run = meta.get("workflow_run") or {}
    if str(workflow_run.get("id")) != run.run_id or str(workflow_run.get("head_sha", "")).lower() != run.source_sha:
        raise EstablishmentRefused(f"artifact {spec.artifact_id} workflow_run binding mismatch")
    expires_at = meta.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at:
        raise EstablishmentRefused(f"artifact {spec.artifact_id} has no expires_at")
    return ref, expires_at


def _download_single_payload(
    client: GithubEvidenceClient, work_root: Path, ref, spec: HistoricalArtifact, index: int
) -> bytes:
    root = client.download_artifact(ref, work_root, work_root / f"artifact-{index}")
    extracted = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    if extracted != [spec.payload_filename]:
        raise EstablishmentRefused(
            f"artifact {spec.artifact_id} extracted file set is not exactly [{spec.payload_filename}]"
        )
    return (root / spec.payload_filename).read_bytes()


def _correlate_marker(marker: OneShotMarker, run: HistoricalRun, purpose: str) -> None:
    observed = (
        marker.purpose, marker.github_run_id, marker.run_attempt, marker.source_sha,
        marker.workflow_identity, marker.event,
    )
    expected = (purpose, run.run_id, run.run_attempt, run.source_sha, run.workflow_identity, EXPECTED_EVENT)
    if observed != expected:
        raise EstablishmentRefused(f"marker for run {run.run_id} does not correlate with the recorded history")


def _verify_marker(client, work_root: Path, run: HistoricalRun, spec: HistoricalArtifact, purpose: str, index: int):
    ref, expires_at = _discover_exact(client, run, spec)
    payload = _download_single_payload(client, work_root, ref, spec, index)
    try:
        marker = OneShotMarker.model_validate_json(payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal
        raise EstablishmentRefused(f"marker {spec.artifact_id} failed strict OneShotMarker parse") from exc
    _correlate_marker(marker, run, purpose)
    fact = _fact(
        receipt_class="ONESHOT_MARKER_CONSUMED", purpose=purpose, github_run_id=run.run_id,
        run_attempt=run.run_attempt, source_sha=run.source_sha, artifact_name=spec.artifact_name,
        artifact_id=spec.artifact_id, payload_filename=spec.payload_filename,
        payload_sha256=_sha256_bytes(payload), disposition="CONSUMED",
    )
    return fact, expires_at


def _verify_probe_evidence(client, work_root: Path, run: HistoricalRun, spec: HistoricalArtifact, cost_ledger: Path, index: int):
    ref, expires_at = _discover_exact(client, run, spec)
    payload = _download_single_payload(client, work_root, ref, spec, index)
    try:
        record = ProbeEvidenceRecord.model_validate_json(payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise EstablishmentRefused(f"probe evidence {spec.artifact_id} failed strict parse") from exc
    observed = (
        record.github_run_id, record.run_attempt, record.source_sha, record.expected_source_sha,
        record.workflow_identity, record.event, record.disposition,
    )
    expected = (
        run.run_id, run.run_attempt, run.source_sha, run.source_sha,
        run.workflow_identity, EXPECTED_EVENT, P5C_EXPECTED_DISPOSITION,
    )
    if observed != expected:
        raise EstablishmentRefused("probe evidence does not correlate with the recorded P5-C history")
    if not record.cost_rows:
        raise EstablishmentRefused("probe evidence carries zero CostRows")
    # Exactly the seam-3 rule from run_phase5_window_freeze.py: every
    # carried CostRow present exactly once, byte-equivalently, in the
    # committed cost ledger.
    if not cost_ledger.exists():
        raise EstablishmentRefused("committed cost ledger is missing")
    committed_serialized = [serialize_cost_row(r) for r in read_cost_rows(cost_ledger)]
    for row in record.cost_rows:
        occurrences = committed_serialized.count(serialize_cost_row(row))
        if occurrences != 1:
            raise EstablishmentRefused(
                f"CostRow for run {row.run_id!r} appears {occurrences} times in the committed ledger (expected 1)"
            )
    fact = _fact(
        receipt_class="PROBE_EVIDENCE", purpose="P5C_WIF_PROBE", github_run_id=run.run_id,
        run_attempt=run.run_attempt, source_sha=run.source_sha, artifact_name=spec.artifact_name,
        artifact_id=spec.artifact_id, payload_filename=spec.payload_filename,
        payload_sha256=_sha256_bytes(payload), disposition=record.disposition,
    )
    return fact, expires_at


def _verify_original_p5d_inventory(client: GithubEvidenceClient) -> None:
    """The original run must have exactly the marker among surviving
    artifacts and no gate-evidence artifact anywhere. Logs, gate_root and
    model output are never inspected."""
    refs = client.list_artifacts_for_run(P5D_ORIGINAL_RUN.run_id)
    inventory = sorted((r.id, r.name) for r in refs)
    if inventory != [(P5D_ORIGINAL_MARKER.artifact_id, P5D_ORIGINAL_MARKER.artifact_name)]:
        raise EstablishmentRefused(
            "original P5-D run artifact inventory is inconsistent with the recorded history"
        )
    for ref in client.list_artifacts(artifact_names.GATE_EVIDENCE_PREFIX):
        parsed = artifact_names.parse_artifact_name(ref.name)
        if parsed is not None and parsed.run_id == P5D_ORIGINAL_RUN.run_id:
            raise EstablishmentRefused("a surviving gate-evidence artifact exists for the original P5-D run")
        if ref.workflow_run_id == P5D_ORIGINAL_RUN.run_id:
            raise EstablishmentRefused("a surviving gate-evidence artifact is bound to the original P5-D run")


def verify_all_sources(client: GithubEvidenceClient, work_root: Path, cost_ledger: Path):
    """Verify every historical source and return the ordered four-fact
    list plus the per-artifact expiry map. Raises before any write."""
    _verify_run_binding(client, P5C_RUN)
    _verify_run_binding(client, P5D_ORIGINAL_RUN)

    fact1, exp1 = _verify_marker(client, work_root, P5C_RUN, P5C_MARKER, "P5C_WIF_PROBE", 1)
    fact2, exp2 = _verify_probe_evidence(client, work_root, P5C_RUN, P5C_PROBE_EVIDENCE, cost_ledger, 2)
    fact3, exp3 = _verify_marker(
        client, work_root, P5D_ORIGINAL_RUN, P5D_ORIGINAL_MARKER, "P5D_OFFICIAL_SONNET_GATE", 3
    )
    _verify_original_p5d_inventory(client)
    fact4 = _fact(
        receipt_class="EXECUTION_DISPOSITION", purpose="P5D_OFFICIAL_SONNET_GATE",
        github_run_id=P5D_ORIGINAL_RUN.run_id, run_attempt=P5D_ORIGINAL_RUN.run_attempt,
        source_sha=P5D_ORIGINAL_RUN.source_sha, disposition=EXECUTION_INVALID_NO_QUALITY_RESULT,
        governance_ref=P5D_ORIGINAL_INCIDENT_RECORD_REF, owner_ruling_id=P5D_ORIGINAL_OWNER_RULING_ID,
    )
    expiries = {
        P5C_MARKER.artifact_name: exp1,
        P5C_PROBE_EVIDENCE.artifact_name: exp2,
        P5D_ORIGINAL_MARKER.artifact_name: exp3,
    }
    return [fact1, fact2, fact3, fact4], expiries


def _print_facts(facts: list[dict], expiries: dict[str, str]) -> None:
    for index, fact in enumerate(facts, start=1):
        for key in FACT_KEYS:
            print(f"FACT[{index}].{key}: {fact[key]}")
    for name, expires_at in expiries.items():
        print(f"EXPIRES_AT: {name} {expires_at}")
    print(f"EARLIEST_EXPIRY: {min(expiries.values())}")


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
    if not args.dry_run:
        if not re.fullmatch(r"[0-9a-f]{64}", args.expect_facts_sha256 or ""):
            print("error: real run requires --expect-facts-sha256 <64 lowercase hex> from a reviewed dry run", file=sys.stderr)
            return 2
        if args.registry.exists():
            print("error: registry already exists; Stage 1 is initial establishment only", file=sys.stderr)
            return 2

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("error: GITHUB_TOKEN is not set (read-only GET/download only)", file=sys.stderr)
        return 2
    client = GithubEvidenceClient(api_url=API_URL, repository=REPOSITORY, token=token)
    del token

    temp_parent = Path(tempfile.mkdtemp(prefix="p5-receipts-"))
    print("work root: fresh OS temporary directory (transient extraction only, not evidence)")
    try:
        work_root = create_fresh_root(temp_parent, temp_parent / "work")
        try:
            facts, expiries = verify_all_sources(client, work_root, args.cost_ledger)
        except (EstablishmentRefused, GithubEvidenceError, OSError, ValueError) as exc:
            print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        bundle = build_fact_bundle(facts)
        digest = facts_sha256(bundle)
        _print_facts(facts, expiries)
        print(f"FACTS_SHA256: {digest}")

        if args.dry_run:
            print(
                "DRY RUN: all four historical facts verified; no receipt written. "
                "recorded_at_utc, prev_receipt_sha256, each receipt SHA-256 and the registry "
                "head are established only by the real append and are NOT claimed here."
            )
            return 0

        if digest != args.expect_facts_sha256:
            print("STOP: facts hash mismatch between the reviewed dry run and this real run; no append", file=sys.stderr)
            return 1

        try:
            for index, fact in enumerate(facts, start=1):
                receipt = append_receipt(
                    args.registry, allow_create=(index == 1), schema_version=1, replacement_of_run_id=None,
                    **fact,
                )
                print(f"APPENDED[{index}]: {receipt.receipt_class} {receipt.purpose} run={receipt.github_run_id}")
            receipts = load_registry(args.registry)
        except Exception as exc:  # noqa: BLE001 - report truthfully; never repair
            print(f"STOP: registry append/reload failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        if len(receipts) != 4:
            print(f"STOP: expected exactly four receipts after establishment, found {len(receipts)}", file=sys.stderr)
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

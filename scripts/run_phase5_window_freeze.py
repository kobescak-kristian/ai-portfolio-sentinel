#!/usr/bin/env python
"""Phase-5 window-control / GENESIS-freeze entrypoint (P5-B Part 3/3,
workflow E: ``.github/workflows/sentinel-window-control.yml``).
Implements the real P5-E freeze plumbing; Part 3 never executes it and
no real qualification window is frozen by landing this file.

Model-free: no OIDC/WIF port exists in this script at all. Refuses to
construct a GENESIS bundle unless ALL of the following pass, in order:
independent expected-source verification (disk and live), the five
Windows-migration-evidence fields (all populated, exact shapes),
second-window protection (an intact active window can only be
superseded by explicitly naming it), the P5-C/P5-D provider-phase
prerequisite verification (seam 3, repaired -- ADR-0012 Amendment A1
and section 19; dispatch q77-p5d-repair-stage2-implement-a: the
committed durable receipt registry is now consulted FIRST for
existence, count and correlation truth, with any still-retained live
artifact hash-verified against its receipt as defense in depth), and
the EUR40 five-slot freeze headroom check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._phase5_common import (  # noqa: E402
    REPO_ROOT,
    Phase5ScriptError,
    assert_expected_source_live,
    assert_expected_source_on_disk,
    build_evidence_client,
    load_durable_history,
)
from sentinel.phase5 import artifact_names  # noqa: E402
from sentinel.phase5.bundle import (  # noqa: E402
    ActiveWindowAmbiguous,
    NoActiveWindow,
    build_bundle,
    create_fresh_root,
    latest_durable_control_state_source,
    validate_bundle,
)
from sentinel.phase5.cadence import trailing_30d_spend_eur_micros, window_freeze_headroom_ok  # noqa: E402
from sentinel.phase5.evidence_records import FreezeRefusalEvidence, StepEvidence  # noqa: E402
from sentinel.phase5.github_context import derive_github_context  # noqa: E402
from sentinel.phase5.models import (  # noqa: E402
    ExpectedSlot,
    GenesisManifest,
    Phase5ControlState,
    QualificationWindowRecord,
    sha256_hex_of_model,
)
from sentinel.phase5.orchestrator import discover_active_window, discover_chain, SCHEDULED_WORKFLOW_IDENTITY  # noqa: E402

CONTROL_WORKFLOW_IDENTITY = ".github/workflows/sentinel-window-control.yml"
FROZEN_REF = "refs/heads/main"
FROZEN_CRON = "37 6 * * *"
LEGACY_TABLES = (
    "runs", "tasks", "findings", "agent_calls", "agent_tool_attempts", "loop_runs", "loop_iterations",
)


def _parse_utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise Phase5ScriptError(f"{label} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise Phase5ScriptError(f"{label} must be UTC (offset +00:00)")
    return parsed


def _validate_migration_evidence(args: argparse.Namespace) -> dict:
    if args.windows_task_name != "SentinelDailyRun":
        raise Phase5ScriptError("windows_task_name must be exactly 'SentinelDailyRun'")
    disabled_at = _parse_utc(args.disabled_at_utc, "disabled_at_utc")
    verified_at = _parse_utc(args.dual_scheduler_verification_at_utc, "dual_scheduler_verification_at_utc")
    if not re.fullmatch(r"[0-9a-f]{64}", args.final_legacy_db_sha256 or ""):
        raise Phase5ScriptError("final_legacy_db_sha256 must be exactly 64 lowercase hexadecimal characters")
    try:
        row_counts = json.loads(args.legacy_row_counts)
    except ValueError as exc:
        raise Phase5ScriptError("legacy_row_counts is not valid JSON") from exc
    if not isinstance(row_counts, dict) or set(row_counts) != set(LEGACY_TABLES):
        raise Phase5ScriptError(
            "legacy_row_counts must be a JSON object with exactly the keys: " + ", ".join(LEGACY_TABLES)
        )
    for key, value in row_counts.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise Phase5ScriptError(f"legacy_row_counts[{key!r}] must be a non-negative integer")
    return {
        "windows_task_name": args.windows_task_name,
        "disabled_at_utc": disabled_at,
        "final_legacy_db_sha256": args.final_legacy_db_sha256,
        "legacy_row_counts": row_counts,
        "dual_scheduler_verification_at_utc": verified_at,
    }


def _artifact_ref_for_receipt(client, receipt):
    """Best-effort LIVE discovery of the exact artifact a durable
    receipt names (dispatch q77-p5d-repair-stage2-implement-a). Returns
    ``None`` if it is no longer discoverable (expired past the
    platform's 90-day retention) -- that is never treated as
    consumption reset or disposition change (ADR-0012 Amendment A1):
    the receipt alone remains authoritative regardless."""
    refs = client.list_artifacts_for_run(receipt.github_run_id)
    for ref in refs:
        if ref.id == receipt.artifact_id and ref.name == receipt.artifact_name:
            return ref
    return None


def _verify_retained_payload(client, work_root: Path, receipt, model_cls, label: str):
    """If ``receipt``'s exact artifact is still live-discoverable,
    download it, strict-parse it as ``model_cls``, and require its
    bytes to hash-match the receipt's own ``payload_sha256`` -- defense
    in depth while artifact bytes remain the primary evidence. Returns
    the parsed record, or ``None`` once the artifact has expired, in
    which case the durable receipt alone is trusted and nothing here
    invents or reconstructs the missing bytes (dispatch
    q77-p5d-repair-stage2-implement-a)."""
    ref = _artifact_ref_for_receipt(client, receipt)
    if ref is None:
        return None
    root = client.download_artifact(ref, work_root, work_root / f"receipt-verify-{label}")
    payload_path = root / receipt.payload_filename
    if not payload_path.exists():
        raise Phase5ScriptError(
            f"artifact {receipt.artifact_id} for {receipt.receipt_class} receipt is missing "
            f"its expected payload file {receipt.payload_filename!r}"
        )
    payload = payload_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != receipt.payload_sha256:
        raise Phase5ScriptError(
            f"artifact {receipt.artifact_id} payload bytes do not match its durable receipt "
            f"SHA-256 {receipt.payload_sha256!r}"
        )
    return model_cls.model_validate_json(payload.decode("utf-8"))


def _verify_provider_phase_prerequisites(
    client, work_root: Path, receipts, expected_source_sha: str
) -> tuple:
    """Seam 3, repaired for durable history (ADR-0012 Amendment A1 and
    section 19; dispatch q77-p5d-repair-stage2-implement-a).

    Durable receipts are the authoritative source of existence, count
    and correlation truth -- never live artifact counts alone, since a
    live artifact expires after at most 90 days while the receipt does
    not. Where a receipt's exact named artifact is still
    live-discoverable, its bytes are additionally hash-verified against
    the receipt's own ``payload_sha256`` as defense in depth; an
    expired artifact is never treated as "never happened" and never
    resets consumption.

    Three durable facts are required, in order:

    1. P5-C: exactly one consumed ``P5C_WIF_PROBE`` marker receipt and
       exactly one ``CAPABILITY_PASS`` ``PROBE_EVIDENCE`` receipt
       correlated to it by (github_run_id, run_attempt).
    2. Original P5-D: exactly one consumed
       ``P5D_OFFICIAL_SONNET_GATE`` marker receipt for the frozen
       original run, exactly one ``EXECUTION_DISPOSITION`` receipt for
       that same run under the frozen owner ruling, and ZERO
       ``GATE_EVIDENCE`` receipts for that purpose -- the original run
       is permanently non-qualifying and this seam never treats it as
       having produced quality evidence (ADR-0012 Amendment A1 rule 4).
    3. Replacement: exactly one consumed
       ``P5D_REPLACEMENT_SONNET_GATE`` marker receipt and exactly one
       ``GATE_EVIDENCE`` receipt for that purpose with disposition
       GREEN or HONEST_FAIL, correlated to the marker by
       (github_run_id, run_attempt); if its artifact is still retained,
       its bytes must independently pass
       ``validate_replacement_provenance`` and its parsed disposition
       must equal the durable receipt's own disposition.

    Any count other than exactly one, or any correlation mismatch,
    fails closed. GREEN and HONEST_FAIL are accepted identically, so
    downstream consequence at this seam is unchanged from before the
    incident -- this is provenance plumbing, not a methodology change.
    This dispatch arms nothing: durable history today shows zero
    replacement receipts of any class, so step 3 above always refuses
    until a later, separately governed dispatch actually produces one.

    Returns the combined tuple of ``CostRow`` objects recovered from
    whichever evidence records are still live-verifiable, to fold into
    headroom -- the byte-identity-in-committed-ledger contract is
    unchanged. Once an evidence artifact expires, its own cost rows can
    no longer be independently recovered (a durable receipt never
    carries payload content such as cost rows, by design), so this
    check is skipped for that evidence and the earlier one-time
    recording into the committed ledger (via
    ``scripts/record_phase5_cost_evidence.py``, run before expiry) is
    trusted from then on.
    """
    from sentinel.phase5.evidence_records import (
        GateEvidenceRecord,
        ProbeEvidenceRecord,
        validate_replacement_provenance,
    )
    from sentinel.phase5.receipts import evidence_receipts, execution_dispositions, marker_receipts
    from sentinel.phase5.replacement import (
        ORIGINAL_PURPOSE,
        OWNER_RULING_ID,
        REPLACEMENT_OF_RUN_ID,
        REPLACEMENT_PURPOSE,
    )
    from telemetry.cost_ledger import read_cost_rows, serialize_cost_row

    # -- 1. P5-C probe ---------------------------------------------------
    probe_markers = marker_receipts(receipts, "P5C_WIF_PROBE")
    if len(probe_markers) != 1:
        raise Phase5ScriptError(
            f"expected exactly one durable P5C_WIF_PROBE marker receipt, found {len(probe_markers)}"
        )
    probe_marker = probe_markers[0]
    probe_evidence_receipts = [
        r
        for r in evidence_receipts(receipts, "PROBE_EVIDENCE")
        if r.github_run_id == probe_marker.github_run_id and r.run_attempt == probe_marker.run_attempt
    ]
    if len(probe_evidence_receipts) != 1 or probe_evidence_receipts[0].disposition != "CAPABILITY_PASS":
        raise Phase5ScriptError(
            "expected exactly one CAPABILITY_PASS durable PROBE_EVIDENCE receipt correlated "
            "to the P5-C marker"
        )
    probe_record = _verify_retained_payload(
        client, work_root, probe_evidence_receipts[0], ProbeEvidenceRecord, "probe"
    )

    # -- 2. original P5-D: visible, permanently non-qualifying -----------
    original_markers = [
        r for r in marker_receipts(receipts, ORIGINAL_PURPOSE) if r.github_run_id == REPLACEMENT_OF_RUN_ID
    ]
    if len(original_markers) != 1:
        raise Phase5ScriptError(
            f"expected exactly one durable original P5-D marker receipt for run "
            f"{REPLACEMENT_OF_RUN_ID!r}, found {len(original_markers)}"
        )
    original_dispositions = [
        r
        for r in execution_dispositions(receipts, REPLACEMENT_OF_RUN_ID)
        if r.purpose == ORIGINAL_PURPOSE and r.owner_ruling_id == OWNER_RULING_ID
    ]
    if len(original_dispositions) != 1:
        raise Phase5ScriptError(
            "expected exactly one durable EXECUTION_DISPOSITION receipt for the original "
            f"P5-D run {REPLACEMENT_OF_RUN_ID!r} under owner ruling {OWNER_RULING_ID!r}"
        )
    original_gate_evidence = [
        r for r in evidence_receipts(receipts, "GATE_EVIDENCE") if r.purpose == ORIGINAL_PURPOSE
    ]
    if original_gate_evidence:
        raise Phase5ScriptError(
            "a GATE_EVIDENCE receipt exists for the original P5-D purpose -- it is permanently "
            "non-qualifying and must never be treated as quality evidence"
        )

    # -- 3. replacement: exactly one proven marker + terminal evidence ---
    replacement_markers = marker_receipts(receipts, REPLACEMENT_PURPOSE)
    if len(replacement_markers) != 1:
        raise Phase5ScriptError(
            f"expected exactly one durable replacement marker receipt, found "
            f"{len(replacement_markers)} -- the P5-E seam requires exactly one properly "
            "proven replacement"
        )
    replacement_marker = replacement_markers[0]
    replacement_gate_receipts = [
        r
        for r in evidence_receipts(receipts, "GATE_EVIDENCE")
        if r.purpose == REPLACEMENT_PURPOSE
        and r.github_run_id == replacement_marker.github_run_id
        and r.run_attempt == replacement_marker.run_attempt
    ]
    if len(replacement_gate_receipts) != 1 or replacement_gate_receipts[0].disposition not in (
        "GREEN",
        "HONEST_FAIL",
    ):
        raise Phase5ScriptError(
            "expected exactly one GREEN or HONEST_FAIL durable GATE_EVIDENCE receipt "
            "correlated to the replacement marker"
        )
    replacement_gate_receipt = replacement_gate_receipts[0]
    replacement_record = _verify_retained_payload(
        client, work_root, replacement_gate_receipt, GateEvidenceRecord, "replacement-gate"
    )
    if replacement_record is not None:
        try:
            validate_replacement_provenance(replacement_record, expected_source_sha=expected_source_sha)
        except ValueError as exc:
            raise Phase5ScriptError(f"replacement evidence provenance invalid: {exc}") from exc
        if replacement_record.disposition != replacement_gate_receipt.disposition:
            raise Phase5ScriptError(
                "live replacement evidence disposition does not match its durable receipt"
            )

    # -- cost-ledger byte-identity (unchanged contract, best-effort) -----
    all_rows: tuple = ()
    if probe_record is not None:
        all_rows = all_rows + tuple(probe_record.cost_rows)
    if replacement_record is not None:
        all_rows = all_rows + tuple(replacement_record.cost_rows)
    committed_path = REPO_ROOT / "telemetry" / "cost_ledger.jsonl"
    committed_rows = read_cost_rows(committed_path) if committed_path.exists() else []
    committed_serialized = [serialize_cost_row(r) for r in committed_rows]
    for row in all_rows:
        occurrences = committed_serialized.count(serialize_cost_row(row))
        if occurrences != 1:
            raise Phase5ScriptError(
                f"CostRow for run {row.run_id!r} appears {occurrences} times in the committed "
                "ledger (expected exactly once) — the P5-C/P5-D handoff is incomplete or duplicated"
            )
    return all_rows


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--first-slot-date", required=True, help="YYYY-MM-DD, UTC")
    parser.add_argument("--windows-task-name", required=True)
    parser.add_argument("--disabled-at-utc", required=True)
    parser.add_argument("--final-legacy-db-sha256", required=True)
    parser.add_argument("--legacy-row-counts", required=True, help="JSON object, exactly the seven legacy tables")
    parser.add_argument("--dual-scheduler-verification-at-utc", required=True)
    parser.add_argument("--supersedes", default=None)
    parser.add_argument("--work-root", type=Path, default=REPO_ROOT / "var" / "phase5-freeze")
    args = parser.parse_args(argv)

    env = os.environ
    work_root = args.work_root
    work_root.mkdir(parents=True, exist_ok=True)

    def refuse(reason: str, ctx=None) -> int:
        if ctx is not None:
            evidence = FreezeRefusalEvidence(
                schema_version=1, workflow_identity=CONTROL_WORKFLOW_IDENTITY,
                github_run_id=ctx.run_id, run_attempt=ctx.run_attempt, event=ctx.event,
                ref=ctx.ref, source_sha=ctx.sha, created_at_utc=datetime.now(timezone.utc),
                steps=(), expected_source_sha=args.expected_source_sha, reason=reason,
            )
            (work_root / "freeze_refusal.json").write_text(evidence.model_dump_json(), encoding="utf-8")
        print(f"FREEZE REFUSED: {reason}", file=sys.stderr)
        return 1

    try:
        assert_expected_source_on_disk(args.expected_source_sha)
        client = build_evidence_client(env)  # pops GITHUB_TOKEN
        assert_expected_source_live(client, args.expected_source_sha)
        ctx = derive_github_context(env)

        migration = _validate_migration_evidence(args)

        try:
            first_slot_date = datetime.strptime(args.first_slot_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise Phase5ScriptError("first_slot_date must be YYYY-MM-DD") from exc
        slot1 = first_slot_date.replace(hour=6, minute=37, second=0, microsecond=0)
        # Whole-second, matching exactly what models.serialize_db_datetime
        # persists for Phase5ControlState.last_evaluated_at_utc -- computing
        # spend against a sub-second-precision `now` while the persisted
        # control state truncates to whole seconds could otherwise exclude
        # a CostRow whose own (microsecond-preserving) timestamp falls
        # inside that truncated final second, making validate_bundle's
        # independent recompute disagree with the value just written.
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if slot1 <= now:
            raise Phase5ScriptError("first_slot_date's 06:37 UTC slot must be strictly in the future")
        if migration["disabled_at_utc"] + timedelta(hours=24) > slot1:
            raise Phase5ScriptError("disabled_at_utc must be at least 24 hours before slot 1")

        # -- second-window protection --------------------------------
        try:
            tip, genesis_state, discovered = discover_active_window(
                client, ctx, work_root, work_root / "discovery"
            )
        except NoActiveWindow:
            tip = None
        except ActiveWindowAmbiguous as exc:
            return refuse(f"AMBIGUOUS_EXISTING_WINDOWS: {exc}", ctx)

        if tip is not None:
            slot_candidates, refusal_candidates, chain_validated = discover_chain(
                client, tip.window, work_root, work_root / "chain"
            )
            _, _, durable_state, _ = latest_durable_control_state_source(
                tip.window, tip.genesis_artifact_identity, tip.genesis, genesis_state, slot_candidates, refusal_candidates
            )
            if not durable_state.window_consumed:
                if args.supersedes != tip.window.window_id:
                    return refuse("ACTIVE_WINDOW_INTACT", ctx)
            elif args.supersedes and args.supersedes != tip.window.window_id:
                return refuse("SUPERSEDES_MISMATCH", ctx)
        elif args.supersedes:
            return refuse("SUPERSEDES_TARGET_NOT_FOUND", ctx)

        # -- P5-C/P5-D prerequisite verification (seam 3, durable) ----
        receipts = load_durable_history()
        provider_cost_rows = _verify_provider_phase_prerequisites(
            client, work_root, receipts, args.expected_source_sha
        )

        # -- headroom ---------------------------------------------------
        committed_ledger = REPO_ROOT / "telemetry" / "cost_ledger.jsonl"
        ledger_copy = work_root / "committed_cost_ledger.jsonl"
        ledger_copy.write_bytes(committed_ledger.read_bytes() if committed_ledger.exists() else b"")
        spend = trailing_30d_spend_eur_micros(ledger_copy, now)
        if not window_freeze_headroom_ok(spend, "DAILY"):
            return refuse("HEADROOM_INSUFFICIENT", ctx)

        # -- construct GENESIS -------------------------------------------
        window_id = f"p5w-{ctx.run_id}"
        slots = tuple(
            ExpectedSlot(slot_index=i, expected_at_utc=slot1 + timedelta(days=i - 1)) for i in range(1, 6)
        )
        window_fields = dict(
            schema_version=1, window_id=window_id, created_at_utc=now,
            control_workflow_identity=CONTROL_WORKFLOW_IDENTITY, control_run_id=ctx.run_id,
            source_sha=args.expected_source_sha, scheduled_workflow_identity=SCHEDULED_WORKFLOW_IDENTITY,
            ref=FROZEN_REF, cron=FROZEN_CRON, timezone="UTC", tolerance_minutes=120,
            expected_slots=slots, qualifying_source="live", qualifying_judgment_mode="agent",
            **migration,
        )
        if args.supersedes:
            window_fields["supersedes_window_id"] = tip.window.window_id
            window_fields["supersedes_window_record_sha256"] = sha256_hex_of_model(tip.window)
        window = QualificationWindowRecord(**window_fields)

        genesis_state = Phase5ControlState(
            schema_version=1, window_id=window.window_id,
            window_record_sha256=sha256_hex_of_model(window),
            latest_authoritative_slot_index=0, window_consumed=False, window_consume_reason=None,
            cadence_level="DAILY", cadence_anchor_slot_utc=None,
            last_accounted_spend_eur_micros=spend, last_evaluated_at_utc=now,
        )

        source_root = work_root / "genesis-source"
        source_root.mkdir(parents=True, exist_ok=True)
        import sqlite3

        db_source = source_root / "ledger.sqlite3"
        conn = sqlite3.connect(str(db_source))
        conn.execute("SELECT 1")
        conn.close()
        findings_source = source_root / "FINDINGS.md"
        findings_source.write_text("# Phase-5 Actions-era lineage\n", encoding="utf-8")
        cost_source = source_root / "cost_ledger.jsonl"
        cost_source.write_bytes(ledger_copy.read_bytes())

        genesis_fields = {
            "schema_version": 1, "bundle_kind": "GENESIS", "workflow_identity": CONTROL_WORKFLOW_IDENTITY,
            "github_run_id": ctx.run_id, "run_attempt": 1, "event": ctx.event, "ref": window.ref,
            "source_sha": window.source_sha, "window_id": window.window_id,
            "window_record_sha256": genesis_state.window_record_sha256, "slot_index": 0,
            "no_run_outcome": "WINDOW_GENESIS", "window_consumed": False,
        }
        built = build_bundle(
            work_root, work_root / "genesis-out", window=window, manifest_fields=genesis_fields,
            source_ledger_path=db_source, source_ledger_trusted_root=source_root,
            findings_source_path=findings_source, findings_trusted_root=source_root,
            cost_ledger_source_path=cost_source, cost_ledger_trusted_root=source_root,
            control_state=genesis_state,
        )
        print(f"GENESIS BUILT: {built.root} (window_id={window.window_id})")
        return 0
    except Phase5ScriptError as exc:
        return refuse(str(exc), locals().get("ctx"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

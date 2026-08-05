"""Dedup and finding lifecycle (BLUEPRINT §6 P2; SPEC §1 step 4).

**C1/C2 bound — this module needs no special-casing for "confirmed
absence vs unknown" or "which classes carry-forward."** The scope set
{(surface, check_class) for tasks with status DONE} already gives the
correct semantics once the checker layer (checks/deterministic/*)
classifies outcomes correctly: a DONE task that observed nothing lets
its scope's OPEN findings auto-resolve; a DONE task that re-observes
the same missing-required-file defect re-emits the same
ObservedFinding every run, which the per-task step below handles as
*advance*, never *auto-resolve*. A task that can't reach a confirmed
determination is FAILED->DEAD_LETTER and is excluded from
``scanned_scopes`` entirely by the caller (sentinel.pipeline), so its
findings are neither advanced nor resolved this run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Collection, Sequence

from contracts.schemas import Finding, compute_content_hash, compute_fingerprint
from checks.base import ObservedFinding
from sentinel import ledger


@dataclass(frozen=True)
class ApplyResult:
    new_fingerprints: list[str]
    advanced_fingerprints: list[str]
    recurred_fingerprints: list[str]  # subset of new_fingerprints that had a prior RESOLVED row
    duplicate_within_run: list[str]


def compute_content_and_fingerprint(observed: ObservedFinding) -> tuple[str, str]:
    content_hash = compute_content_hash(observed.location, observed.normalized_content)
    fingerprint = compute_fingerprint(observed.surface, observed.check_class, content_hash)
    return content_hash, fingerprint


def apply_observed(
    conn, run_id: str, now: datetime, observed: Sequence[ObservedFinding]
) -> ApplyResult:
    """Apply one task's observed findings: advance an existing OPEN
    row, or insert a new one (a legitimate recurrence if a RESOLVED
    row with the same fingerprint already exists — a plain INSERT,
    never a resurrection). Within-run duplicates (same fingerprint
    observed twice by one task) collapse to a single application."""
    seen: set[str] = set()
    result = ApplyResult(
        new_fingerprints=[], advanced_fingerprints=[], recurred_fingerprints=[], duplicate_within_run=[]
    )
    for obs in observed:
        content_hash, fingerprint = compute_content_and_fingerprint(obs)
        if fingerprint in seen:
            result.duplicate_within_run.append(fingerprint)
            continue
        seen.add(fingerprint)
        existing = ledger.get_open_finding(conn, fingerprint)
        if existing is not None:
            ledger.advance_finding(
                conn, existing.id, last_seen_utc=now, last_seen_run_id=run_id
            )
            result.advanced_fingerprints.append(fingerprint)
        else:
            recurrence = ledger.has_resolved_finding(conn, fingerprint)
            finding = Finding(
                schema_version=1,
                fingerprint=fingerprint,
                surface=obs.surface,
                check_class=obs.check_class,
                content_hash=content_hash,
                location=obs.location,
                detail=obs.detail,
                status="OPEN",
                first_seen_utc=now,
                last_seen_utc=now,
                first_seen_run_id=run_id,
                last_seen_run_id=run_id,
            )
            ledger.insert_finding(conn, finding)
            result.new_fingerprints.append(fingerprint)
            if recurrence:
                result.recurred_fingerprints.append(fingerprint)
    return result


def resolve_absent(
    conn,
    run_id: str,
    now: datetime,
    *,
    scanned_scopes: Collection[tuple[str, str]],
    observed_fingerprints: Collection[str],
) -> list[str]:
    """Auto-resolve every OPEN finding within a scanned (DONE-task)
    scope whose fingerprint was not observed this run. Scope-limited,
    not run-limited — a scope excluded from ``scanned_scopes`` (its
    task ended FAILED/DEAD_LETTER, or it wasn't scanned this run at
    all) is left completely untouched."""
    resolved: list[str] = []
    observed_set = set(observed_fingerprints)
    for row in ledger.list_open_findings(conn, scopes=scanned_scopes):
        if row.finding.fingerprint in observed_set:
            continue
        ledger.resolve_finding(conn, row.id, resolved_at_utc=now, resolved_run_id=run_id)
        resolved.append(row.finding.fingerprint)
    return resolved


def compute_run_counts(conn, run_id: str) -> ledger.RunCounts:
    """Computed from committed ledger state, not in-memory counters,
    so a partial failure can't desync the reported counts from what's
    actually on disk."""
    tasks_terminal = ledger.count_tasks(
        conn, run_id, statuses=["DONE", "FAILED", "DEAD_LETTER"]
    )
    findings_new = len(ledger.list_findings_for_run(conn, run_id, role="first_seen"))
    findings_resolved = len(ledger.list_findings_for_run(conn, run_id, role="resolved"))
    still_open = sum(
        1
        for row in ledger.list_open_findings(conn)
        if row.finding.first_seen_run_id != run_id
    )
    return ledger.RunCounts(
        tasks_terminal=tasks_terminal,
        findings_new=findings_new,
        findings_still_open=still_open,
        findings_resolved=findings_resolved,
    )

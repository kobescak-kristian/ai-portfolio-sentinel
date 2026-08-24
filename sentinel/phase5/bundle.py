"""Phase-5 state-bundle machinery (ADR-0011 §4, §5; P5-B Part 2/3).

Path safety, SQLite snapshot/restore, local bundle construction, one
comprehensive bundle validator, active-window supersession, exact
cryptographic predecessor selection, the cross-bundle control-state
transition validator, deterministic CONTROL_REFUSAL decision
reconstruction, the durable refusal walker, and the exact 5-of-5
clean-completion proof.

Pure and model-free: no network, no GitHub API call, no Actions artifact
operation. Every path argument is validated against an explicit trusted
root before any filesystem operation reads or writes through it.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from pydantic import TypeAdapter

from contracts.schemas import CostRow
from telemetry.cost_ledger import read_cost_rows

from .cadence import CadenceDecision, evaluate_scheduled_trigger
from .models import (
    CarriedFile,
    ControlRefusalManifest,
    GenesisManifest,
    Phase5ControlState,
    QualificationWindowRecord,
    SlotSuccessorManifest,
    StateBundleManifest,
    canonical_json_bytes,
    sha256_hex_of_file,
    sha256_hex_of_model,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BundleSafetyError(Exception):
    """A path, source-file or construction safety rule was violated."""


class BundleValidationError(Exception):
    """A bundle failed the comprehensive local validator."""


class ActiveWindowAmbiguous(Exception):
    """The supplied candidate set does not resolve to exactly one
    unsuperseded, internally-consistent window."""


class NoActiveWindow(Exception):
    """No candidate window matches the caller's expected identity."""


class BrokenPredecessor(Exception):
    """A predecessor/successor chain link is missing, ambiguous or
    fails a hash/identity check."""


class NextSlotAbsent(Exception):
    """Legitimate end of the contiguous authoritative chain — the next
    slot has no candidate yet. Not corruption."""


class ControlStateTransitionError(Exception):
    """A predecessor -> successor control-state edge violates one of
    the cross-bundle invariants."""


# ---------------------------------------------------------------------------
# Trusted-root path safety
# ---------------------------------------------------------------------------


def _reject_unsafe_component(path: Path) -> None:
    if path.is_symlink():  # lstat-based, never follows the link
        raise BundleSafetyError(f"refusing symlink component: {path.name}")
    if hasattr(os.path, "isjunction") and os.path.isjunction(path):  # 3.12+, Windows
        raise BundleSafetyError(f"refusing junction/reparse component: {path.name}")


def assert_trusted_path(trusted_root: Path, target: Path) -> Path:
    """Verify every lexical component from ``trusted_root`` to ``target``
    is symlink/junction-free BEFORE any ``resolve()``; resolve only to
    confirm containment afterward. Returns the pre-resolve path — the
    one callers actually open. A not-yet-existing leaf is permitted;
    every existing component on the way must be safe."""
    _reject_unsafe_component(trusted_root)
    try:
        rel = target.relative_to(trusted_root)
    except ValueError:
        raise BundleSafetyError("target is not lexically under trusted root")
    current = trusted_root
    for part in rel.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _reject_unsafe_component(current)
    resolved = current.resolve()
    root_resolved = trusted_root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise BundleSafetyError("path escapes trusted root")
    return current


def assert_safe_relative_path(trusted_root: Path, relative_path: str) -> Path:
    if "\\" in relative_path:
        raise BundleSafetyError("forward slashes only")
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or any(part in ("..", "") for part in pure.parts):
        raise BundleSafetyError(f"unsafe path: {relative_path}")
    return assert_trusted_path(trusted_root, trusted_root / pure)


def create_fresh_root(destination_trusted_root: Path, destination_root: Path) -> Path:
    """Anchored creation only: the trusted anchor must already exist;
    every existing intermediate component is validated before any
    ``mkdir``; ``destination_root`` itself must not exist; each newly
    created level is revalidated as it is made."""
    if not destination_trusted_root.is_dir():
        raise BundleSafetyError("destination trusted root does not exist")
    _reject_unsafe_component(destination_trusted_root)
    try:
        rel = destination_root.relative_to(destination_trusted_root)
    except ValueError:
        raise BundleSafetyError("destination root is not lexically under trusted root")
    if any(part in ("..", "") for part in rel.parts):
        raise BundleSafetyError("unsafe destination path")
    if destination_root.exists() or destination_root.is_symlink():
        raise BundleSafetyError("bundle destination root must be fresh")
    current = destination_trusted_root
    for part in rel.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _reject_unsafe_component(current)
        else:
            current.mkdir()
            _reject_unsafe_component(current)
    _reject_unsafe_component(destination_root)
    return destination_root


def _validated_source_file(trusted_root: Path, source_path: Path) -> Path:
    checked = assert_trusted_path(trusted_root, source_path)
    if not checked.exists():
        raise BundleSafetyError("authoritative source file does not exist")
    if not checked.is_file():
        raise BundleSafetyError("authoritative source is not a regular file")
    return checked


def _write_regular_file(path: Path, data: bytes) -> None:
    if path.exists():
        raise BundleSafetyError("refusing to overwrite an existing bundle file")
    path.write_bytes(data)
    if path.is_symlink():
        raise BundleSafetyError("bundle file write landed on a symlink")


# ---------------------------------------------------------------------------
# SQLite snapshot / restore — stdlib backup() only, never a raw file copy
# ---------------------------------------------------------------------------


def snapshot_ledger(db_path: Path, snapshot_path: Path) -> str:
    if not db_path.exists():
        raise BundleSafetyError("source database does not exist")  # before connect —
        # sqlite3.connect() would otherwise silently create an empty database
    if not db_path.is_file():
        raise BundleSafetyError("source is not a regular file")
    if snapshot_path.exists():
        raise BundleSafetyError("refusing to overwrite existing snapshot path")
    source = sqlite3.connect(str(db_path))
    try:
        dest = sqlite3.connect(str(snapshot_path))
        try:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise BundleSafetyError("snapshot failed integrity_check")
        finally:
            dest.close()
    finally:
        source.close()
    return sha256_hex_of_file(snapshot_path)


def restore_ledger(snapshot_path: Path, restore_target_path: Path, *, expected_sha256: str) -> None:
    if sha256_hex_of_file(snapshot_path) != expected_sha256:
        raise BundleSafetyError("snapshot hash mismatch — refusing restore")
    source = sqlite3.connect(str(snapshot_path))
    try:
        if source.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BundleSafetyError("carried snapshot failed integrity_check")
    finally:
        source.close()
    if restore_target_path.exists():
        raise BundleSafetyError("restore destination must be fresh")
    source = sqlite3.connect(str(snapshot_path))
    try:
        dest = sqlite3.connect(str(restore_target_path))
        try:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise BundleSafetyError("restored DB failed integrity_check")
        finally:
            dest.close()
    finally:
        source.close()


# ---------------------------------------------------------------------------
# Bundle file set
# ---------------------------------------------------------------------------

_AUTHORITATIVE = (
    "state/ledger.sqlite3",
    "state/FINDINGS.md",
    "state/cost_ledger.jsonl",
    "state/phase5_state.json",
)
_METADATA = ("qualification_window.json", "manifest.json", "manifest.sha256")
_EXPECTED_DIRS = frozenset({"state"})
_EXPECTED_FILES = frozenset((*_AUTHORITATIVE, *_METADATA))

_MANIFEST_CLASSES = {
    "GENESIS": GenesisManifest,
    "SLOT_SUCCESSOR": SlotSuccessorManifest,
    "CONTROL_REFUSAL": ControlRefusalManifest,
}


def _construct_manifest(manifest_fields: Mapping[str, object], *, carried_files: tuple[CarriedFile, ...]):
    kind = manifest_fields.get("bundle_kind")
    cls = _MANIFEST_CLASSES.get(kind)  # type: ignore[arg-type]
    if cls is None:
        raise BundleSafetyError(f"unknown bundle_kind: {kind!r}")
    return cls(**{**dict(manifest_fields), "carried_files": carried_files})


@dataclass(frozen=True)
class BuiltBundle:
    root: Path
    manifest: StateBundleManifest
    manifest_sha256: str


@dataclass(frozen=True)
class ValidatedBundle:
    root: Path
    manifest: StateBundleManifest
    window: QualificationWindowRecord
    control_state: Phase5ControlState
    cost_rows: tuple[CostRow, ...]


# ---------------------------------------------------------------------------
# Bundle construction
# ---------------------------------------------------------------------------


def build_bundle(
    destination_trusted_root: Path,
    destination_root: Path,
    *,
    window: QualificationWindowRecord,
    manifest_fields: Mapping[str, object],
    source_ledger_path: Path,
    source_ledger_trusted_root: Path,
    findings_source_path: Path,
    findings_trusted_root: Path,
    cost_ledger_source_path: Path,
    cost_ledger_trusted_root: Path,
    control_state: Phase5ControlState,
) -> BuiltBundle:
    # 1. every authoritative SOURCE validated as a real safe file first
    ledger_src = _validated_source_file(source_ledger_trusted_root, source_ledger_path)
    findings_src = _validated_source_file(findings_trusted_root, findings_source_path)
    cost_src = _validated_source_file(cost_ledger_trusted_root, cost_ledger_source_path)
    try:
        read_cost_rows(cost_src)  # existing CostRow reader — malformed accounting
    except ValueError as exc:  # state fails BEFORE any bundle can be returned
        raise BundleSafetyError("cost ledger unreadable or malformed") from exc

    # 2. anchored fresh destination
    root = create_fresh_root(destination_trusted_root, destination_root)
    (root / "state").mkdir()
    _reject_unsafe_component(root / "state")

    # 3. authoritative state — SQLite via backup(); text files byte-preserved
    ledger_dest = assert_safe_relative_path(root, "state/ledger.sqlite3")
    snapshot_ledger(ledger_src, ledger_dest)
    _write_regular_file(assert_safe_relative_path(root, "state/FINDINGS.md"), findings_src.read_bytes())
    _write_regular_file(assert_safe_relative_path(root, "state/cost_ledger.jsonl"), cost_src.read_bytes())
    _write_regular_file(
        assert_safe_relative_path(root, "state/phase5_state.json"), canonical_json_bytes(control_state)
    )

    # 4. hashes computed strictly AFTER all state writes are finalized
    carried_files = tuple(
        CarriedFile(relative_path=rel, sha256=sha256_hex_of_file(root / rel)) for rel in _AUTHORITATIVE
    )
    manifest = _construct_manifest(manifest_fields, carried_files=carried_files)

    # 5. fixed metadata
    _write_regular_file(assert_safe_relative_path(root, "qualification_window.json"), canonical_json_bytes(window))
    if manifest.window_record_sha256 != sha256_hex_of_model(window):
        raise BundleSafetyError(
            "qualification_window.json hash does not match manifest.window_record_sha256"
        )
    _write_regular_file(assert_safe_relative_path(root, "manifest.json"), canonical_json_bytes(manifest))
    _write_regular_file(
        assert_safe_relative_path(root, "manifest.sha256"), sha256_hex_of_model(manifest).encode("ascii")
    )

    validate_bundle(root)  # the ONE comprehensive validator, before this may be handed to Part 3
    return BuiltBundle(root=root, manifest=manifest, manifest_sha256=sha256_hex_of_model(manifest))


# ---------------------------------------------------------------------------
# Comprehensive bundle validator
# ---------------------------------------------------------------------------


def validate_bundle(bundle_root: Path) -> ValidatedBundle:
    _reject_unsafe_component(bundle_root)

    # EXACT TREE — every entry checked for symlink/junction before typing;
    # the directory set and file set must match exactly
    found_dirs: set[str] = set()
    found_files: set[str] = set()
    stack = [bundle_root]
    while stack:
        current_dir = stack.pop()
        for entry in current_dir.iterdir():
            _reject_unsafe_component(entry)
            rel = str(entry.relative_to(bundle_root)).replace("\\", "/")
            if entry.is_dir():
                found_dirs.add(rel)
                stack.append(entry)
            elif entry.is_file():
                found_files.add(rel)
            else:
                raise BundleValidationError(f"unsupported entry type: {rel}")
    if found_dirs != _EXPECTED_DIRS or found_files != _EXPECTED_FILES:
        raise BundleValidationError("bundle tree mismatch")
    for rel in _EXPECTED_FILES:
        assert_safe_relative_path(bundle_root, rel)

    # MANIFEST
    manifest = TypeAdapter(StateBundleManifest).validate_json((bundle_root / "manifest.json").read_bytes())
    manifest_hash = sha256_hex_of_model(manifest)
    recorded = (bundle_root / "manifest.sha256").read_text(encoding="ascii").strip()
    if recorded != manifest_hash:
        raise BundleValidationError("manifest.sha256 sidecar mismatch")
    if sorted(c.relative_path for c in manifest.carried_files) != sorted(_AUTHORITATIVE):
        raise BundleValidationError("carried_files must be exactly the four authoritative paths")
    for carried in manifest.carried_files:
        if sha256_hex_of_file(bundle_root / carried.relative_path) != carried.sha256:
            raise BundleValidationError(f"carried digest mismatch: {carried.relative_path}")

    # WINDOW
    window = QualificationWindowRecord.model_validate_json(
        (bundle_root / "qualification_window.json").read_bytes()
    )
    if sha256_hex_of_model(window) != manifest.window_record_sha256:
        raise BundleValidationError("window hash does not match manifest")
    if (
        manifest.window_id != window.window_id
        or manifest.source_sha != window.source_sha
        or manifest.ref != window.ref
    ):
        raise BundleValidationError("manifest/window binding mismatch")

    # CONTROL STATE — window identity mandatory and bound
    control_state = Phase5ControlState.model_validate_json(
        (bundle_root / "state/phase5_state.json").read_bytes()
    )
    if control_state.window_id is None or control_state.window_record_sha256 is None:
        raise BundleValidationError("bundled control state must carry window identity")
    if (
        control_state.window_id != window.window_id
        or control_state.window_record_sha256 != manifest.window_record_sha256
    ):
        raise BundleValidationError("control-state window binding mismatch")

    # LOCAL ACCOUNTING TRUTH — every bundle kind, GENESIS included
    if manifest.window_consumed != control_state.window_consumed:
        raise BundleValidationError("manifest/control window_consumed mismatch")
    try:
        cost_rows = read_cost_rows(bundle_root / "state/cost_ledger.jsonl")
    except ValueError as exc:
        raise BundleValidationError("bundle cost ledger unreadable or malformed") from exc
    cutoff = control_state.last_evaluated_at_utc - timedelta(days=30)
    recomputed = sum(
        row.cost_eur_micros for row in cost_rows if cutoff <= row.recorded_at_utc <= control_state.last_evaluated_at_utc
    )
    if control_state.last_accounted_spend_eur_micros != recomputed:
        raise BundleValidationError("stored spend disagrees with this bundle's own cost ledger")

    # GENESIS local invariants — a poisoned genesis can never seed a chain
    if manifest.bundle_kind == "GENESIS":
        if control_state.latest_authoritative_slot_index != 0:
            raise BundleValidationError("genesis control state must be at slot 0")
        if control_state.window_consumed or control_state.window_consume_reason is not None:
            raise BundleValidationError("genesis control state must be unconsumed")

    # SQLITE
    conn = sqlite3.connect(str(bundle_root / "state/ledger.sqlite3"))
    try:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BundleValidationError("bundle SQLite failed integrity_check")
    finally:
        conn.close()

    return ValidatedBundle(
        root=bundle_root, manifest=manifest, window=window, control_state=control_state, cost_rows=tuple(cost_rows)
    )


# ---------------------------------------------------------------------------
# Candidate types — entered only via validate_bundle of their own bundle,
# so manifest, control state and cost rows are already mutually bound
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActiveWindowCandidate:
    window: QualificationWindowRecord
    genesis: GenesisManifest
    genesis_artifact_identity: str


@dataclass(frozen=True)
class SlotSuccessorCandidate:
    artifact_identity: str
    manifest: SlotSuccessorManifest
    control_state: Phase5ControlState
    cost_rows: tuple[CostRow, ...]


@dataclass(frozen=True)
class ControlRefusalCandidate:
    artifact_identity: str
    manifest: ControlRefusalManifest
    control_state: Phase5ControlState
    cost_rows: tuple[CostRow, ...]


# ---------------------------------------------------------------------------
# Active window discovery
# ---------------------------------------------------------------------------


def select_active_window(
    candidates: Sequence[ActiveWindowCandidate],
    *,
    expected_source_sha: str,
    expected_ref: str,
    expected_cron: str,
    expected_scheduled_workflow_identity: str,
) -> ActiveWindowCandidate:
    by_id: dict[str, ActiveWindowCandidate] = {}
    for c in candidates:
        w, g, gid = c.window, c.genesis, c.genesis_artifact_identity
        if (
            g.window_id != w.window_id
            or g.window_record_sha256 != sha256_hex_of_model(w)
            or g.source_sha != w.source_sha
            or g.workflow_identity != w.control_workflow_identity
            or g.github_run_id != w.control_run_id
            or g.ref != w.ref
            or g.slot_index != 0
        ):
            raise ActiveWindowAmbiguous("genesis is not bound to this window's freeze operation")
        if w.window_id in by_id:
            existing = by_id[w.window_id]
            if sha256_hex_of_model(existing.window) != sha256_hex_of_model(w):
                raise ActiveWindowAmbiguous("duplicate window_id, differing canonical bytes")
            if existing.genesis_artifact_identity != gid:
                raise ActiveWindowAmbiguous("ambiguous genesis artifact identity for same window")
            if sha256_hex_of_model(existing.genesis) != sha256_hex_of_model(g):
                raise ActiveWindowAmbiguous(
                    "same genesis artifact identity with differing genesis bytes"
                )
            continue
        by_id[w.window_id] = c

    superseded_by: dict[str, str] = {}
    for wid, c in by_id.items():
        w = c.window
        if w.supersedes_window_id is None:
            continue
        target = by_id.get(w.supersedes_window_id)
        if target is None:
            raise ActiveWindowAmbiguous("missing superseded record")
        if w.supersedes_window_record_sha256 != sha256_hex_of_model(target.window):
            raise ActiveWindowAmbiguous("supersession hash mismatch")
        if w.supersedes_window_id in superseded_by:
            raise ActiveWindowAmbiguous("forked supersession")
        superseded_by[w.supersedes_window_id] = wid

    # GLOBAL cycle detection over EVERY node — a disconnected corrupt
    # cycle fails discovery even when another component has a valid tip
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {wid: WHITE for wid in by_id}

    def visit(wid: str) -> None:
        color[wid] = GRAY
        parent = by_id[wid].window.supersedes_window_id
        if parent is not None:
            if color.get(parent) == GRAY:
                raise ActiveWindowAmbiguous(f"supersession cycle involving {wid}")
            if color.get(parent) == WHITE:
                visit(parent)
        color[wid] = BLACK

    for wid in by_id:
        if color[wid] == WHITE:
            visit(wid)

    tips = [wid for wid in by_id if wid not in superseded_by]
    if not tips:
        raise NoActiveWindow()
    if len(tips) > 1:
        raise ActiveWindowAmbiguous("multiple unsuperseded tips")

    tip = by_id[tips[0]]
    w = tip.window
    if (
        w.source_sha != expected_source_sha
        or w.ref != expected_ref
        or w.cron != expected_cron
        or w.scheduled_workflow_identity != expected_scheduled_workflow_identity
    ):
        raise NoActiveWindow()
    return tip


# ---------------------------------------------------------------------------
# Cryptographic predecessor chain
# ---------------------------------------------------------------------------


def _find_slot_candidates(
    window: QualificationWindowRecord, slot_n: int, candidates: Sequence[SlotSuccessorCandidate]
) -> list[SlotSuccessorCandidate]:
    return [
        c
        for c in candidates
        if c.manifest.window_id == window.window_id
        and c.manifest.window_record_sha256 == sha256_hex_of_model(window)
        and c.manifest.source_sha == window.source_sha
        and c.manifest.workflow_identity == window.scheduled_workflow_identity
        and c.manifest.ref == window.ref
        and c.manifest.slot_index == slot_n
        and c.manifest.event == "schedule"
        and c.manifest.run_attempt == 1
    ]


def _verify_chain_to_genesis(
    window: QualificationWindowRecord,
    slot_n: int,
    genesis_identity: str,
    genesis: GenesisManifest,
    candidates: Sequence[SlotSuccessorCandidate],
) -> tuple[str, StateBundleManifest]:
    matches = _find_slot_candidates(window, slot_n, candidates)
    if not matches:
        raise BrokenPredecessor(f"gap: slot {slot_n} missing from an otherwise-later chain")
    if len(matches) > 1:
        raise BrokenPredecessor(f"ambiguous candidates for slot {slot_n}")
    candidate = matches[0]
    if slot_n == 1:
        expected_identity, expected_manifest = genesis_identity, genesis
    else:
        expected_identity, expected_manifest = _verify_chain_to_genesis(
            window, slot_n - 1, genesis_identity, genesis, candidates
        )
    if candidate.manifest.predecessor_manifest_sha256 != sha256_hex_of_model(expected_manifest):
        raise BrokenPredecessor(f"slot {slot_n} predecessor hash mismatch")
    if candidate.manifest.predecessor_artifact_id_or_name != expected_identity:
        raise BrokenPredecessor(f"slot {slot_n} predecessor artifact identity mismatch")
    return candidate.artifact_identity, candidate.manifest


def resolve_slot(
    window: QualificationWindowRecord,
    slot_n: int,
    genesis_identity: str,
    genesis: GenesisManifest,
    candidates: Sequence[SlotSuccessorCandidate],
) -> tuple[str, StateBundleManifest]:
    if not _find_slot_candidates(window, slot_n, candidates):
        raise NextSlotAbsent(slot_n)  # legitimate: the chain hasn't reached here yet
    return _verify_chain_to_genesis(window, slot_n, genesis_identity, genesis, candidates)


def authorized_predecessor(
    window: QualificationWindowRecord,
    slot_n: int,
    genesis_identity: str,
    genesis: GenesisManifest,
    candidates: Sequence[SlotSuccessorCandidate],
) -> tuple[str, StateBundleManifest]:
    if slot_n == 1:
        return genesis_identity, genesis
    return _verify_chain_to_genesis(window, slot_n - 1, genesis_identity, genesis, candidates)


def _candidate_for(identity: str, candidates: Sequence[SlotSuccessorCandidate]) -> SlotSuccessorCandidate:
    matches = [c for c in candidates if c.artifact_identity == identity]
    if len(matches) != 1:
        raise BrokenPredecessor(f"cannot uniquely resolve candidate for artifact identity {identity!r}")
    return matches[0]


# ---------------------------------------------------------------------------
# Cross-bundle control-state transition validator
# ---------------------------------------------------------------------------

_CADENCE_ALLOWED_TRANSITIONS = {
    "DAILY": {"DAILY", "EVERY_2_DAYS"},
    "EVERY_2_DAYS": {"EVERY_2_DAYS", "WEEKLY"},
    "WEEKLY": {"WEEKLY"},
}


def validate_control_state_transition(
    *,
    window: QualificationWindowRecord,
    predecessor_state: Phase5ControlState,
    successor: "SlotSuccessorCandidate | ControlRefusalCandidate",
    cadence_decision: CadenceDecision | None = None,
) -> None:
    s = successor.control_state
    m = successor.manifest

    # WINDOW IDENTITY — never changes or becomes null within one window
    if (
        s.window_id != window.window_id
        or s.window_record_sha256 != sha256_hex_of_model(window)
        or predecessor_state.window_id != window.window_id
        or predecessor_state.window_record_sha256 != sha256_hex_of_model(window)
    ):
        raise ControlStateTransitionError("window identity changed or missing across transition")

    # SLOT INDEX
    if m.bundle_kind == "SLOT_SUCCESSOR":
        if s.latest_authoritative_slot_index != m.slot_index:
            raise ControlStateTransitionError("successor control state does not record its own slot")
        if m.slot_index != predecessor_state.latest_authoritative_slot_index + 1:
            raise ControlStateTransitionError("slot index must advance by exactly one")
    else:  # CONTROL_REFUSAL never advances slot credit
        if s.latest_authoritative_slot_index != predecessor_state.latest_authoritative_slot_index:
            raise ControlStateTransitionError("a refusal must not advance the slot index")

    # WINDOW CONSUMPTION — monotone; reasons never silently rewritten/removed
    if predecessor_state.window_consumed:
        if not s.window_consumed:
            raise ControlStateTransitionError("a consumed window can never become unconsumed")
        if s.window_consume_reason != predecessor_state.window_consume_reason:
            raise ControlStateTransitionError("consumption reason cannot be rewritten or removed")
    elif m.bundle_kind == "CONTROL_REFUSAL" and cadence_decision is not None:
        # explicit three-way agreement for a refusal edge from an
        # unconsumed predecessor (ChatGPT execution clarification #1)
        if cadence_decision.consume_active_window:
            if not s.window_consumed or s.window_consume_reason != cadence_decision.window_consume_reason:
                raise ControlStateTransitionError(
                    "refusal consumption does not match the reconstructed decision"
                )
        else:
            if s.window_consumed or s.window_consume_reason is not None:
                raise ControlStateTransitionError(
                    "refusal must remain unconsumed when its decision does not consume"
                )

    # CADENCE — degrade-only, one notch at a time; anchor stable unless transitioning
    if s.cadence_level not in _CADENCE_ALLOWED_TRANSITIONS[predecessor_state.cadence_level]:
        raise ControlStateTransitionError("illegal cadence transition")
    if s.cadence_level != predecessor_state.cadence_level:
        if cadence_decision is None or cadence_decision.cadence_transition_to != s.cadence_level:
            raise ControlStateTransitionError("cadence transition without a matching cadence decision")
        if s.cadence_anchor_slot_utc != cadence_decision.new_anchor:
            raise ControlStateTransitionError("cadence transition anchor mismatch")
    else:
        if s.cadence_anchor_slot_utc != predecessor_state.cadence_anchor_slot_utc:
            raise ControlStateTransitionError("cadence anchor mutated without a transition")

    if m.bundle_kind == "CONTROL_REFUSAL":
        if cadence_decision is None or m.no_run_outcome != cadence_decision.outcome:
            raise ControlStateTransitionError("refusal disagrees with its cadence decision")

    # ACCOUNTED SPEND — evidence-backed, monotone evaluation time
    if s.last_evaluated_at_utc < predecessor_state.last_evaluated_at_utc:
        raise ControlStateTransitionError("last_evaluated_at_utc moved backward")
    cutoff = s.last_evaluated_at_utc - timedelta(days=30)
    recomputed = sum(
        r.cost_eur_micros for r in successor.cost_rows if cutoff <= r.recorded_at_utc <= s.last_evaluated_at_utc
    )
    if s.last_accounted_spend_eur_micros != recomputed:
        raise ControlStateTransitionError("stored spend disagrees with carried accounting evidence")


# ---------------------------------------------------------------------------
# Deterministic CONTROL_REFUSAL decision reconstruction
# ---------------------------------------------------------------------------


def reconstruct_refusal_decision(
    window: QualificationWindowRecord,
    predecessor_state: Phase5ControlState,
    refusal: ControlRefusalCandidate,
) -> CadenceDecision:
    """Pure reconstruction from evidence the validated bundles already
    carry — no caller discretion, no execution-time placeholder."""
    m, s = refusal.manifest, refusal.control_state
    if m.slot_index is not None or m.expected_slot_utc is None:
        raise ControlStateTransitionError("refusal must carry expected_slot_utc and no slot index")
    if m.event != "schedule" or m.run_attempt != 1:
        raise ControlStateTransitionError("refusal must be a first-attempt scheduled execution")
    if m.sentinel_run_id is not None:
        raise ControlStateTransitionError("refusal must not carry a Sentinel run id")

    cutoff = s.last_evaluated_at_utc - timedelta(days=30)
    spend = sum(r.cost_eur_micros for r in refusal.cost_rows if cutoff <= r.recorded_at_utc <= s.last_evaluated_at_utc)

    active_window = window if not predecessor_state.window_consumed else None
    decision = evaluate_scheduled_trigger(predecessor_state, spend, active_window, m.expected_slot_utc)

    if decision.provider_call_permitted:
        raise ControlStateTransitionError("a refusal bundle exists for a PROCEED decision")
    if decision.outcome != m.no_run_outcome:
        raise ControlStateTransitionError("refusal outcome disagrees with the reconstructed decision")
    return decision


# ---------------------------------------------------------------------------
# Latest-authoritative-state selectors
# ---------------------------------------------------------------------------


def latest_contiguous_authoritative_qualification_state(
    window: QualificationWindowRecord,
    genesis_identity: str,
    genesis: GenesisManifest,
    candidates: Sequence[SlotSuccessorCandidate],
) -> tuple[str, StateBundleManifest, int]:
    """GENESIS/SLOT_SUCCESSOR only — the only chain eligible to be a
    qualifying slot's predecessor."""
    identity, manifest, slot_n = genesis_identity, genesis, 0
    for n in range(1, 6):
        try:
            identity, manifest = resolve_slot(window, n, genesis_identity, genesis, candidates)
        except NextSlotAbsent:
            break
        slot_n = n
    return identity, manifest, slot_n


def latest_durable_control_state_source(
    window: QualificationWindowRecord,
    genesis_identity: str,
    genesis: GenesisManifest,
    genesis_state: Phase5ControlState,
    slot_candidates: Sequence[SlotSuccessorCandidate],
    refusal_candidates: Sequence[ControlRefusalCandidate],
) -> tuple[str, StateBundleManifest, Phase5ControlState, int]:
    """May additionally advance through zero or more consecutive linked
    CONTROL_REFUSAL bundles beyond the qualification frontier — for
    operational continuity only, never qualification credit. Every edge
    is transition-validated using its mechanically reconstructed cadence
    decision."""
    identity, manifest, slot_n = latest_contiguous_authoritative_qualification_state(
        window, genesis_identity, genesis, slot_candidates
    )
    state = genesis_state if slot_n == 0 else _candidate_for(identity, slot_candidates).control_state

    seen_identities = {identity}
    while True:
        linked = [
            r
            for r in refusal_candidates
            if r.manifest.window_id == window.window_id
            and r.manifest.window_record_sha256 == sha256_hex_of_model(window)
            and r.manifest.source_sha == window.source_sha
            and r.manifest.workflow_identity == window.scheduled_workflow_identity
            and r.manifest.ref == window.ref
            and r.manifest.predecessor_manifest_sha256 == sha256_hex_of_model(manifest)
            and r.manifest.predecessor_artifact_id_or_name == identity
        ]
        if not linked:
            break  # legitimate durable frontier
        if len(linked) > 1:
            raise BrokenPredecessor("ambiguous CONTROL_REFUSAL at this frontier")
        candidate = linked[0]
        if candidate.artifact_identity in seen_identities:
            raise BrokenPredecessor("reused artifact identity in refusal chain — cycle")
        decision = reconstruct_refusal_decision(window, state, candidate)
        validate_control_state_transition(
            window=window, predecessor_state=state, successor=candidate, cadence_decision=decision
        )
        seen_identities.add(candidate.artifact_identity)
        identity, manifest, state = candidate.artifact_identity, candidate.manifest, candidate.control_state
        # slot_n intentionally never advances — a refusal earns zero credit
    return identity, manifest, state, slot_n


# ---------------------------------------------------------------------------
# Exact 5-of-5 clean-completion proof
# ---------------------------------------------------------------------------


def is_clean_five_of_five_completion(
    window: QualificationWindowRecord,
    genesis_identity: str,
    genesis: GenesisManifest,
    genesis_state: Phase5ControlState,
    slot_candidates: Sequence[SlotSuccessorCandidate],
) -> bool:
    """True ONLY when the exact cryptographic chain GENESIS -> slots 1..5
    exists, every slot manifest is QUALIFYING, every control-state
    transition validates, and slot 5's control state has the clean
    shape. Never derived from the final control state alone."""
    if genesis_state.latest_authoritative_slot_index != 0:
        raise ControlStateTransitionError("genesis control state must be at slot 0")
    prev_state = genesis_state
    last_candidate: SlotSuccessorCandidate | None = None
    for n in range(1, 6):
        try:
            identity, manifest = resolve_slot(window, n, genesis_identity, genesis, slot_candidates)
        except (NextSlotAbsent, BrokenPredecessor):
            return False
        candidate = _candidate_for(identity, slot_candidates)
        if manifest.qualification_outcome != "QUALIFYING":
            return False
        try:
            validate_control_state_transition(window=window, predecessor_state=prev_state, successor=candidate)
        except ControlStateTransitionError:
            return False
        prev_state = candidate.control_state
        last_candidate = candidate
    assert last_candidate is not None
    final = last_candidate.control_state
    return (
        final.window_consumed is True
        and final.window_consume_reason is None
        and final.latest_authoritative_slot_index == 5
    )

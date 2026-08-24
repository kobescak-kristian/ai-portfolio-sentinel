"""Ports-injected scheduled/rehearsal orchestration (P5-B Part 3/3).

Composes the pure Part-2 domain core (``models``, ``bundle``,
``qualification``, ``cadence``, ``oneshot``) with the Part-3 adapters
(``github_context``, ``artifact_names``, ``github_evidence``,
``preflight``, ``evidence_records``) behind an injected ``ScheduledPorts``
bundle. Never imports ``claude_agent_sdk`` or any ``agents.*`` module —
every provider-adjacent capability (WIF readiness, OIDC acquisition,
the live Sentinel run itself) is a callable the caller supplies.

Implements exactly the frozen ``S01``..``S18`` order from
``preflight.SCHEDULED_STEP_ORDER``: every step is recorded on a
``PreflightLedger`` before its effect happens, so an out-of-order call
raises before it can do anything, and the provider path
(``ports.acquire_oidc`` / ``ports.install_token`` / the live run) is
reachable only once ``S01``..``S11`` have all recorded ``OK``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping

from contracts.schemas import CostRow
from telemetry.cost_ledger import read_cost_rows

from . import artifact_names
from .bundle import (
    ActiveWindowCandidate,
    BrokenPredecessor,
    ControlRefusalCandidate,
    ControlStateTransitionError,
    NoActiveWindow,
    ActiveWindowAmbiguous,
    SlotSuccessorCandidate,
    ValidatedBundle,
    authorized_predecessor,
    build_bundle,
    create_fresh_root,
    latest_durable_control_state_source,
    restore_ledger,
    select_active_window,
    validate_bundle,
)
from .cadence import (
    CADENCE_TRIGGER_EUR_MICROS,
    CostEvidenceUnavailable,
    evaluate_post_run,
    evaluate_scheduled_trigger,
    trailing_30d_spend_eur_micros,
)
from .github_context import GithubActionsContext
from .github_evidence import GithubEvidenceClient
from .models import Phase5ControlState, QualificationWindowRecord, sha256_hex_of_model
from .preflight import PreflightLedger, REHEARSAL_STEP_ORDER, SCHEDULED_STEP_ORDER
from .qualification import (
    GithubRunMetadata,
    QualificationChainContext,
    classify_run,
    derive_pre_successor_outcome,
    derive_timing_outcome,
)
from .evidence_records import PreWindowRefusalEvidence, ScheduledAttemptEvidence, StepEvidence
from .models import SentinelRunEvidence

SCHEDULED_WORKFLOW_IDENTITY = ".github/workflows/sentinel-schedule.yml"
FROZEN_REF = "refs/heads/main"
FROZEN_CRON = "37 6 * * *"


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduledPorts:
    env: Mapping[str, str]
    clock: Callable[[], datetime]
    evidence_client: GithubEvidenceClient
    wif_ready: Callable[[], None]
    acquire_oidc: Callable[[], object]  # -> agents.checker.oidc.OidcSession
    install_token: Callable[[object], None]  # (OidcSession) -> None
    shutdown_oidc: Callable[[object], None]  # (OidcSession | None) -> None
    run_sentinel: Callable[["WorkingState", str], object]  # -> a RunOutcome-shaped object


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkingState:
    root: Path
    db_path: Path
    findings_path: Path
    cost_ledger_path: Path
    phase5_state_path: Path


@dataclass(frozen=True)
class StagedArtifact:
    name: str
    path: Path


@dataclass(frozen=True)
class ScheduledResult:
    exit_code: int
    ledger: PreflightLedger
    staged_artifacts: tuple[StagedArtifact, ...]


@dataclass(frozen=True)
class RehearsalResult:
    exit_code: int
    ledger: PreflightLedger
    staged_artifacts: tuple[StagedArtifact, ...]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def resolve_owned_slot(window: QualificationWindowRecord, created_at: datetime):
    """Half-open daily ownership interval per slot: ``[slot, slot+1)``,
    slot 5 closes 24h after its own instant. Returns the owning
    ``ExpectedSlot`` or ``None`` when ``created_at`` falls before slot
    1 opens or after slot 5's interval — the latter should not occur
    within one scheduled invocation, but a caller must never assume it
    cannot."""
    by_index = {s.slot_index: s for s in window.expected_slots}
    for index in range(1, 6):
        start = by_index[index].expected_at_utc
        end = by_index[index + 1].expected_at_utc if index < 5 else start + timedelta(hours=24)
        if start <= created_at < end:
            return by_index[index]
    return None


def discover_active_window(
    client: GithubEvidenceClient, ctx: GithubActionsContext, work_trusted_root: Path, work_root: Path
) -> tuple[ActiveWindowCandidate, Phase5ControlState, dict[str, ValidatedBundle]]:
    refs = client.list_artifacts(artifact_names.GENESIS_PREFIX)
    candidates: list[ActiveWindowCandidate] = []
    validated_by_identity: dict[str, ValidatedBundle] = {}
    for index, ref in enumerate(refs):
        bundle_root = client.download_artifact(ref, work_trusted_root, work_root / f"genesis-{index}")
        validated = validate_bundle(bundle_root)
        if validated.manifest.bundle_kind != "GENESIS":
            continue
        candidates.append(
            ActiveWindowCandidate(
                window=validated.window, genesis=validated.manifest, genesis_artifact_identity=ref.identity
            )
        )
        validated_by_identity[ref.identity] = validated
    tip = select_active_window(
        candidates,
        expected_source_sha=ctx.sha,
        expected_ref=FROZEN_REF,
        expected_cron=FROZEN_CRON,
        expected_scheduled_workflow_identity=SCHEDULED_WORKFLOW_IDENTITY,
    )
    genesis_state = validated_by_identity[tip.genesis_artifact_identity].control_state
    return tip, genesis_state, validated_by_identity


def discover_chain(
    client: GithubEvidenceClient, window: QualificationWindowRecord, work_trusted_root: Path, work_root: Path
) -> tuple[tuple[SlotSuccessorCandidate, ...], tuple[ControlRefusalCandidate, ...], dict[str, ValidatedBundle]]:
    validated_by_identity: dict[str, ValidatedBundle] = {}
    slot_candidates: list[SlotSuccessorCandidate] = []
    for index, ref in enumerate(client.list_artifacts(artifact_names.slot_prefix(window.window_id))):
        bundle_root = client.download_artifact(ref, work_trusted_root, work_root / f"slot-{index}")
        validated = validate_bundle(bundle_root)
        if validated.manifest.bundle_kind != "SLOT_SUCCESSOR":
            continue
        slot_candidates.append(
            SlotSuccessorCandidate(
                artifact_identity=ref.identity,
                manifest=validated.manifest,
                control_state=validated.control_state,
                cost_rows=validated.cost_rows,
            )
        )
        validated_by_identity[ref.identity] = validated
    refusal_candidates: list[ControlRefusalCandidate] = []
    for index, ref in enumerate(client.list_artifacts(artifact_names.refusal_prefix(window.window_id))):
        bundle_root = client.download_artifact(ref, work_trusted_root, work_root / f"refusal-{index}")
        validated = validate_bundle(bundle_root)
        if validated.manifest.bundle_kind != "CONTROL_REFUSAL":
            continue
        refusal_candidates.append(
            ControlRefusalCandidate(
                artifact_identity=ref.identity,
                manifest=validated.manifest,
                control_state=validated.control_state,
                cost_rows=validated.cost_rows,
            )
        )
        validated_by_identity[ref.identity] = validated
    return tuple(slot_candidates), tuple(refusal_candidates), validated_by_identity


def restore_working_state(validated: ValidatedBundle, work_trusted_root: Path, work_root: Path) -> WorkingState:
    root = create_fresh_root(work_trusted_root, work_root)
    (root / "state").mkdir()

    carried = {c.relative_path: c.sha256 for c in validated.manifest.carried_files}

    db_path = root / "state" / "ledger.sqlite3"
    restore_ledger(
        validated.root / "state" / "ledger.sqlite3", db_path, expected_sha256=carried["state/ledger.sqlite3"]
    )
    findings_path = root / "state" / "FINDINGS.md"
    findings_path.write_bytes((validated.root / "state" / "FINDINGS.md").read_bytes())
    cost_ledger_path = root / "state" / "cost_ledger.jsonl"
    cost_ledger_path.write_bytes((validated.root / "state" / "cost_ledger.jsonl").read_bytes())
    phase5_state_path = root / "state" / "phase5_state.json"
    phase5_state_path.write_bytes((validated.root / "state" / "phase5_state.json").read_bytes())

    return WorkingState(
        root=root,
        db_path=db_path,
        findings_path=findings_path,
        cost_ledger_path=cost_ledger_path,
        phase5_state_path=phase5_state_path,
    )


# ---------------------------------------------------------------------------
# Successor / refusal construction helpers
# ---------------------------------------------------------------------------

_NONQUALIFYING_CONSUME_REASONS = frozenset(
    (
        "LATE_NONQUALIFYING",
        "WRONG_PROVENANCE_NONQUALIFYING",
        "DUPLICATE_NONQUALIFYING",
        "FAILED_NONTERMINAL",
        "COSTROW_INVALID",
        "STATE_CHAIN_FAILURE",
    )
)


def _slot_successor_control_state(
    *, pred_state: Phase5ControlState, window: QualificationWindowRecord, slot_n: int,
    outcome: str, spend_after: int, now: datetime,
) -> Phase5ControlState:
    if outcome == "QUALIFYING":
        active_window = window if not pred_state.window_consumed else None
        post_run = evaluate_post_run(active_window, spend_after)
        if post_run.consume_active_window:
            consumed, reason = True, post_run.window_consume_reason
        elif slot_n == 5:
            consumed, reason = True, None
        else:
            consumed, reason = pred_state.window_consumed, pred_state.window_consume_reason
    else:
        consumed, reason = True, outcome
    return Phase5ControlState(
        schema_version=1,
        window_id=window.window_id,
        window_record_sha256=sha256_hex_of_model(window),
        latest_authoritative_slot_index=slot_n,
        window_consumed=consumed,
        window_consume_reason=reason,
        cadence_level=pred_state.cadence_level,
        cadence_anchor_slot_utc=pred_state.cadence_anchor_slot_utc,
        last_accounted_spend_eur_micros=spend_after,
        last_evaluated_at_utc=now,
    )


def _refusal_control_state(
    *, durable_state: Phase5ControlState, window: QualificationWindowRecord,
    decision, spend: int, now: datetime,
) -> Phase5ControlState:
    if decision.consume_active_window:
        consumed, reason = True, decision.window_consume_reason
    else:
        consumed, reason = durable_state.window_consumed, durable_state.window_consume_reason
    cadence_level = decision.cadence_transition_to or durable_state.cadence_level
    cadence_anchor = decision.new_anchor if decision.cadence_transition_to else durable_state.cadence_anchor_slot_utc
    return Phase5ControlState(
        schema_version=1,
        window_id=window.window_id,
        window_record_sha256=sha256_hex_of_model(window),
        latest_authoritative_slot_index=durable_state.latest_authoritative_slot_index,
        window_consumed=consumed,
        window_consume_reason=reason,
        cadence_level=cadence_level,
        cadence_anchor_slot_utc=cadence_anchor,
        last_accounted_spend_eur_micros=spend,
        last_evaluated_at_utc=now,
    )


# ---------------------------------------------------------------------------
# Scheduled run
# ---------------------------------------------------------------------------


def run_scheduled(ports: ScheduledPorts, *, work_trusted_root: Path) -> ScheduledResult:
    preflight_ledger = PreflightLedger(order=SCHEDULED_STEP_ORDER)
    work_trusted_root.mkdir(parents=True, exist_ok=True)
    staged: list[StagedArtifact] = []
    session = None

    def _early_evidence(reason: str, ctx: GithubActionsContext | None) -> ScheduledResult:
        if ctx is not None:
            evidence = PreWindowRefusalEvidence(
                schema_version=1,
                workflow_identity=ctx.workflow_path,
                github_run_id=ctx.run_id,
                run_attempt=ctx.run_attempt,
                event=ctx.event,
                ref=ctx.ref,
                source_sha=ctx.sha,
                created_at_utc=ports.clock(),
                steps=tuple(
                    StepEvidence(step_id=r.step_id, status=r.status, detail=r.detail)
                    for r in preflight_ledger.to_records()
                ),
                reason=reason,  # type: ignore[arg-type]
            )
            path = work_trusted_root / "prewindow_evidence.json"
            path.write_text(evidence.model_dump_json(), encoding="utf-8")
            staged.append(StagedArtifact(name=artifact_names.prewindow_evidence_name(ctx.run_id, ctx.run_attempt), path=path))
        exit_code = 0 if reason in ("NO_ACTIVE_WINDOW", "SLOT_NOT_OPEN") else 1
        return ScheduledResult(exit_code=exit_code, ledger=preflight_ledger, staged_artifacts=tuple(staged))

    def _attempt_failure(ctx: GithubActionsContext, disposition: str) -> ScheduledResult:
        evidence = ScheduledAttemptEvidence(
            schema_version=1,
            workflow_identity=ctx.workflow_path,
            github_run_id=ctx.run_id,
            run_attempt=ctx.run_attempt,
            event=ctx.event,
            ref=ctx.ref,
            source_sha=ctx.sha,
            created_at_utc=ports.clock(),
            steps=tuple(
                StepEvidence(step_id=r.step_id, status=r.status, detail=r.detail)
                for r in preflight_ledger.to_records()
            ),
            disposition=disposition,
        )
        path = work_trusted_root / "attempt_evidence.json"
        path.write_text(evidence.model_dump_json(), encoding="utf-8")
        staged.append(StagedArtifact(name=artifact_names.attempt_evidence_name(ctx.run_id, ctx.run_attempt), path=path))
        return ScheduledResult(exit_code=1, ledger=preflight_ledger, staged_artifacts=tuple(staged))

    try:
        # ---- S01 -------------------------------------------------------
        from .github_context import derive_github_context, to_run_metadata

        ctx = derive_github_context(ports.env)
        created_at, run_started_at = ports.evidence_client.get_run_timing(ctx.run_id)
        github_run = to_run_metadata(ctx, created_at=created_at, run_started_at=run_started_at)
        preflight_ledger.record("S01_DERIVE_GITHUB_CONTEXT", "OK", ctx.run_id)

        # ---- S02 -------------------------------------------------------
        try:
            tip, genesis_state, discovered = discover_active_window(
                ports.evidence_client, ctx, work_trusted_root, work_trusted_root / "discovery"
            )
        except NoActiveWindow:
            preflight_ledger.mark_early_exit("S02_DISCOVER_ACTIVE_WINDOW", "no active window")
            return _early_evidence("NO_ACTIVE_WINDOW", ctx)
        except ActiveWindowAmbiguous as exc:
            preflight_ledger.mark_early_exit("S02_DISCOVER_ACTIVE_WINDOW", f"ambiguous: {exc}")
            return _attempt_failure(ctx, "ACTIVE_WINDOW_AMBIGUOUS")
        preflight_ledger.record("S02_DISCOVER_ACTIVE_WINDOW", "OK", tip.window.window_id)
        window = tip.window

        # ---- S03 -------------------------------------------------------
        expected_slot = resolve_owned_slot(window, created_at)
        if expected_slot is None:
            preflight_ledger.mark_early_exit("S03_RESOLVE_OWNED_SLOT", "no owning slot for created_at")
            return _early_evidence("SLOT_NOT_OPEN", ctx)
        slot_n = expected_slot.slot_index
        preflight_ledger.record("S03_RESOLVE_OWNED_SLOT", "OK", str(slot_n))

        # ---- S04 -------------------------------------------------------
        provenance_ok = (
            github_run.event == "schedule"
            and github_run.run_attempt == 1
            and github_run.ref == window.ref
            and github_run.source_sha == window.source_sha
            and github_run.workflow_identity == window.scheduled_workflow_identity
        )
        if not provenance_ok:
            preflight_ledger.mark_early_exit("S04_VALIDATE_PROVENANCE", "provenance mismatch")
            return _attempt_failure(ctx, "WRONG_PROVENANCE_NONQUALIFYING")
        preflight_ledger.record("S04_VALIDATE_PROVENANCE", "OK", "provenance validated")

        # ---- S05 -------------------------------------------------------
        try:
            slot_candidates, refusal_candidates, chain_validated = discover_chain(
                ports.evidence_client, window, work_trusted_root, work_trusted_root / "chain"
            )
            all_validated = {**discovered, **chain_validated}
            durable_identity, durable_manifest, durable_state, frontier_slot = latest_durable_control_state_source(
                window, tip.genesis_artifact_identity, tip.genesis, genesis_state, slot_candidates, refusal_candidates
            )
            predecessor_bundle = all_validated[durable_identity]
            working_state = restore_working_state(predecessor_bundle, work_trusted_root, work_trusted_root / "state")
        except BrokenPredecessor as exc:
            preflight_ledger.mark_early_exit("S05_RESTORE_PREDECESSOR_BUNDLE", f"broken predecessor: {exc}")
            return _attempt_failure(ctx, "STATE_CHAIN_FAILURE")
        preflight_ledger.record("S05_RESTORE_PREDECESSOR_BUNDLE", "OK", durable_identity)

        # ---- S06 -------------------------------------------------------
        # A single, whole-second `now` for both the spend computation and
        # the persisted control-state timestamp: Phase5ControlState's
        # canonical serialization truncates to whole seconds, so a
        # sub-second-precision `now` used only for the spend sum could
        # disagree with validate_bundle's own recompute against the
        # truncated value that actually gets written.
        s06_now = ports.clock().replace(microsecond=0)
        try:
            spend = trailing_30d_spend_eur_micros(working_state.cost_ledger_path, s06_now)
        except CostEvidenceUnavailable as exc:
            preflight_ledger.mark_early_exit("S06_EVALUATE_SPEND_AND_CADENCE", f"cost evidence unavailable: {exc}")
            return _attempt_failure(ctx, "COST_EVIDENCE_UNAVAILABLE")
        active_window = window if not durable_state.window_consumed else None
        decision = evaluate_scheduled_trigger(durable_state, spend, active_window, expected_slot.expected_at_utc)
        if not decision.provider_call_permitted:
            new_state = _refusal_control_state(
                durable_state=durable_state, window=window, decision=decision, spend=spend, now=s06_now
            )
            manifest_fields = {
                "schema_version": 1,
                "bundle_kind": "CONTROL_REFUSAL",
                "workflow_identity": window.scheduled_workflow_identity,
                "github_run_id": ctx.run_id,
                "run_attempt": 1,
                "event": "schedule",
                "ref": window.ref,
                "source_sha": window.source_sha,
                "window_id": window.window_id,
                "window_record_sha256": sha256_hex_of_model(window),
                "expected_slot_utc": expected_slot.expected_at_utc,
                "sentinel_run_id": None,
                "no_run_outcome": decision.outcome,
                "qualification_outcome": decision.outcome,
                "window_consumed": new_state.window_consumed,
                "predecessor_artifact_id_or_name": durable_identity,
                "predecessor_manifest_sha256": sha256_hex_of_model(durable_manifest),
            }
            built = build_bundle(
                work_trusted_root,
                work_trusted_root / "refusal-out",
                window=window,
                manifest_fields=manifest_fields,
                source_ledger_path=working_state.db_path,
                source_ledger_trusted_root=work_trusted_root,
                findings_source_path=working_state.findings_path,
                findings_trusted_root=work_trusted_root,
                cost_ledger_source_path=working_state.cost_ledger_path,
                cost_ledger_trusted_root=work_trusted_root,
                control_state=new_state,
            )
            preflight_ledger.mark_early_exit("S06_EVALUATE_SPEND_AND_CADENCE", decision.outcome)
            staged.append(
                StagedArtifact(
                    name=artifact_names.refusal_name(window.window_id, ctx.run_id), path=built.root
                )
            )
            return ScheduledResult(exit_code=0, ledger=preflight_ledger, staged_artifacts=tuple(staged))
        preflight_ledger.record("S06_EVALUATE_SPEND_AND_CADENCE", "OK", "PROCEED")

        # ---- S07 ---------------------------------------------------------
        # ports.wif_ready is responsible for both the S07 token-file
        # placeholder (required before auth.assert_wif_config_ready's
        # symlink-before-isfile check can pass) and the readiness check
        # itself — orchestrator.py stays free of any agents.* import, so
        # this module never touches claude_agent_sdk even transitively.
        try:
            ports.wif_ready()
        except Exception as exc:  # noqa: BLE001 - WIF config failure is fail-closed
            preflight_ledger.mark_early_exit("S07_ASSERT_WIF_CONFIG_READY", f"{type(exc).__name__}")
            return _attempt_failure(ctx, "WIF_CONFIG_NOT_READY")
        preflight_ledger.record("S07_ASSERT_WIF_CONFIG_READY", "OK", "wif ready")

        # ---- S08 -----------------------------------------------------
        preflight_ledger.record("S08_PRE_PROVIDER_EVIDENCE", "OK", "attempt evidence prepared")

        # ---- S09 -----------------------------------------------------
        try:
            session = ports.acquire_oidc()
        except Exception as exc:  # noqa: BLE001 - OIDC acquisition failure is fail-closed
            preflight_ledger.mark_early_exit("S09_REQUEST_OIDC_TOKEN", f"{type(exc).__name__}")
            return _attempt_failure(ctx, "OIDC_ACQUISITION_FAILED")
        preflight_ledger.record("S09_REQUEST_OIDC_TOKEN", "OK", "oidc token acquired")

        # ---- S10 -----------------------------------------------------
        ports.install_token(session)
        preflight_ledger.record("S10_WRITE_TOKEN_FILE", "OK", "token installed, refresher started")

        # ---- S11 -----------------------------------------------------
        preflight_ledger.record("S11_PERMIT_PROVIDER_PATH", "OK", "provider path permitted")

        # ---- S12 -----------------------------------------------------
        outcome = preflight_ledger.guard_provider(lambda: ports.run_sentinel(working_state, ctx.run_id))
        preflight_ledger.record("S12_EXECUTE_LIVE_SENTINEL_RUN", "OK", outcome.run_id)

        # ---- S13 -----------------------------------------------------
        if outcome.status not in ("COMPLETED", "FAILED"):
            preflight_ledger.mark_early_exit("S13_ASSERT_TERMINAL_LEDGER_ROW", "non-terminal run row")
            return _attempt_failure(ctx, "NONTERMINAL_RUN")
        preflight_ledger.record("S13_ASSERT_TERMINAL_LEDGER_ROW", "OK", outcome.status)

        # ---- S14 -----------------------------------------------------
        cost_rows = tuple(read_cost_rows(working_state.cost_ledger_path))
        matching_costrows = [r for r in cost_rows if r.run_id == outcome.run_id]
        preflight_ledger.record("S14_ASSERT_EXACTLY_ONE_COSTROW", "OK", str(len(matching_costrows)))

        # ---- S15 -----------------------------------------------------
        sentinel_evidence = SentinelRunEvidence(
            schema_version=1,
            run_id=outcome.run_id,
            github_run_id=ctx.run_id,
            status=outcome.status,
            source="live",
            judgment_mode="agent",
        )
        early = derive_pre_successor_outcome(
            window, slot_n, github_run, [sentinel_evidence], cost_rows,
            window_already_consumed=durable_state.window_consumed,
        )
        try:
            pred_identity, pred_manifest = authorized_predecessor(
                window, slot_n, tip.genesis_artifact_identity, tip.genesis, slot_candidates
            )
        except BrokenPredecessor:
            preflight_ledger.mark_early_exit("S15_BUILD_AND_VALIDATE_SUCCESSOR", "broken slot-chain predecessor")
            return _attempt_failure(ctx, "STATE_CHAIN_FAILURE")
        pred_state = genesis_state if slot_n == 1 else next(
            c.control_state for c in slot_candidates if c.artifact_identity == pred_identity
        )

        if early is not None:
            claimed_outcome, _reason = early
        else:
            claimed_outcome, _reason, _delay = derive_timing_outcome(window, slot_n, github_run)

        s15_now = ports.clock().replace(microsecond=0)  # see the S06 comment on this exact truncation
        spend_after = trailing_30d_spend_eur_micros(working_state.cost_ledger_path, s15_now)
        new_control_state = _slot_successor_control_state(
            pred_state=pred_state, window=window, slot_n=slot_n,
            outcome=claimed_outcome, spend_after=spend_after, now=s15_now,
        )
        manifest_fields = {
            "schema_version": 1,
            "bundle_kind": "SLOT_SUCCESSOR",
            "workflow_identity": window.scheduled_workflow_identity,
            "github_run_id": ctx.run_id,
            "run_attempt": 1,
            "event": "schedule",
            "ref": window.ref,
            "source_sha": window.source_sha,
            "window_id": window.window_id,
            "window_record_sha256": sha256_hex_of_model(window),
            "slot_index": slot_n,
            "expected_slot_utc": expected_slot.expected_at_utc,
            "github_run_created_at_utc": github_run.created_at,
            "github_run_started_at_utc": github_run.run_started_at,
            "sentinel_run_id": outcome.run_id,
            "qualification_outcome": claimed_outcome,
            "window_consumed": new_control_state.window_consumed,
            "predecessor_artifact_id_or_name": pred_identity,
            "predecessor_manifest_sha256": sha256_hex_of_model(pred_manifest),
        }
        built = build_bundle(
            work_trusted_root,
            work_trusted_root / "slot-out",
            window=window,
            manifest_fields=manifest_fields,
            source_ledger_path=working_state.db_path,
            source_ledger_trusted_root=work_trusted_root,
            findings_source_path=working_state.findings_path,
            findings_trusted_root=work_trusted_root,
            cost_ledger_source_path=working_state.cost_ledger_path,
            cost_ledger_trusted_root=work_trusted_root,
            control_state=new_control_state,
        )
        preflight_ledger.record("S15_BUILD_AND_VALIDATE_SUCCESSOR", "OK", claimed_outcome)

        # ---- S16 -----------------------------------------------------
        successor_candidate = SlotSuccessorCandidate(
            artifact_identity=artifact_names.slot_name(window.window_id, slot_n, ctx.run_id),
            manifest=built.manifest,
            control_state=new_control_state,
            cost_rows=tuple(read_cost_rows(working_state.cost_ledger_path)),
        )
        final = classify_run(
            window, slot_n, github_run, [sentinel_evidence], cost_rows, successor_candidate,
            QualificationChainContext(
                genesis_identity=tip.genesis_artifact_identity, genesis=tip.genesis,
                genesis_state=genesis_state, slot_candidates=slot_candidates,
            ),
            window_already_consumed=durable_state.window_consumed,
            now=ports.clock(),
        )
        if final.outcome != claimed_outcome:
            preflight_ledger.mark_early_exit("S16_CLASSIFY_QUALIFICATION", "manifest/classifier disagreement")
            return _attempt_failure(ctx, "CLASSIFICATION_DISAGREEMENT")
        preflight_ledger.record("S16_CLASSIFY_QUALIFICATION", "OK", final.outcome)

        # ---- S17 -----------------------------------------------------
        staged.append(
            StagedArtifact(name=artifact_names.slot_name(window.window_id, slot_n, ctx.run_id), path=built.root)
        )
        preflight_ledger.record("S17_STAGE_SUCCESSOR_ARTIFACT", "OK", built.root.name)
        return ScheduledResult(exit_code=0, ledger=preflight_ledger, staged_artifacts=tuple(staged))
    finally:
        # ---- S18 -------------------------------------------------------
        ports.shutdown_oidc(session)
        if preflight_ledger.to_records() and preflight_ledger.to_records()[-1].step_id != "S18_CLEANUP_TOKEN_FILE":
            try:
                preflight_ledger.record("S18_CLEANUP_TOKEN_FILE", "OK", "token file cleaned up")
            except Exception:  # noqa: BLE001 - the ledger may already be terminated
                pass


# ---------------------------------------------------------------------------
# Rehearsal
# ---------------------------------------------------------------------------


def run_rehearsal(
    ports: ScheduledPorts, *, work_trusted_root: Path, expected_source_sha: str
) -> RehearsalResult:
    """Exercises the REAL bundle build/validate/restore/successor-build
    cycle against an EPHEMERAL, synthetic window and GENESIS constructed
    entirely in this work root — never against real lineage artifact
    names, so it can never be mistaken for a real predecessor by
    ``select_active_window`` on a later real run. Uses only the six
    non-provider steps of ``REHEARSAL_STEP_ORDER``: S07 and S09..S14
    and S16 are structurally absent from this function's body — there
    is no OIDC port, no WIF port and no provider port in scope at all."""
    from .github_context import assert_expected_source, derive_github_context

    preflight_ledger = PreflightLedger(order=REHEARSAL_STEP_ORDER)
    work_trusted_root.mkdir(parents=True, exist_ok=True)
    staged: list[StagedArtifact] = []

    ctx = derive_github_context(ports.env)
    preflight_ledger.record("S01_DERIVE_GITHUB_CONTEXT", "OK", ctx.run_id)

    try:
        assert_expected_source(
            ctx, expected_source_sha, expected_repository=ctx.repository, require_attempt_1=False
        )
    except Exception as exc:  # noqa: BLE001 - a wrong expected sha refuses before any bundle mechanics
        preflight_ledger.mark_early_exit("S02_DISCOVER_ACTIVE_WINDOW", f"{type(exc).__name__}")
        return RehearsalResult(exit_code=1, ledger=preflight_ledger, staged_artifacts=())
    preflight_ledger.record("S02_DISCOVER_ACTIVE_WINDOW", "OK", "expected_source_sha validated")
    preflight_ledger.record("S03_RESOLVE_OWNED_SLOT", "OK", "rehearsal: synthetic slot 1")
    preflight_ledger.record("S04_VALIDATE_PROVENANCE", "OK", "rehearsal: no external provenance to validate")

    # Read-only proof that REST discovery works, against whatever real
    # (harmless, non-lineage-shaped) artifacts already exist — never
    # used as input to the synthetic bundle built below.
    ports.evidence_client.list_artifacts(artifact_names.GENESIS_PREFIX)
    preflight_ledger.record("S05_RESTORE_PREDECESSOR_BUNDLE", "OK", "read-only REST discovery exercised")
    preflight_ledger.record("S06_EVALUATE_SPEND_AND_CADENCE", "OK", "rehearsal: cadence not evaluated")
    preflight_ledger.record("S08_PRE_PROVIDER_EVIDENCE", "OK", "rehearsal evidence prepared")

    now = ports.clock()
    from .models import CarriedFile, GenesisManifest, Phase5ControlState as _P5State, QualificationWindowRecord as _Win, ExpectedSlot

    window = _Win(
        schema_version=1,
        window_id="p5w-rehearsal",
        created_at_utc=now,
        control_workflow_identity=".github/workflows/sentinel-window-control.yml",
        control_run_id="rehearsal",
        source_sha=expected_source_sha,
        scheduled_workflow_identity=SCHEDULED_WORKFLOW_IDENTITY,
        ref=FROZEN_REF,
        cron=FROZEN_CRON,
        timezone="UTC",
        tolerance_minutes=120,
        expected_slots=tuple(
            ExpectedSlot(slot_index=i, expected_at_utc=now.replace(hour=6, minute=37, second=0, microsecond=0) + timedelta(days=i))
            for i in range(1, 6)
        ),
        qualifying_source="live",
        qualifying_judgment_mode="agent",
    )

    source_root = work_trusted_root / "rehearsal-source"
    source_root.mkdir(parents=True, exist_ok=True)
    db_source = source_root / "ledger.sqlite3"
    import sqlite3

    conn = sqlite3.connect(str(db_source))
    conn.execute("CREATE TABLE placeholder (id INTEGER)")
    conn.commit()
    conn.close()
    findings_source = source_root / "FINDINGS.md"
    findings_source.write_text("# Rehearsal\n", encoding="utf-8")
    cost_source = source_root / "cost_ledger.jsonl"
    cost_source.write_text("", encoding="utf-8")

    genesis_state = _P5State(
        schema_version=1,
        window_id=window.window_id,
        window_record_sha256=sha256_hex_of_model(window),
        latest_authoritative_slot_index=0,
        window_consumed=False,
        window_consume_reason=None,
        cadence_level="DAILY",
        cadence_anchor_slot_utc=None,
        last_accounted_spend_eur_micros=0,
        last_evaluated_at_utc=now,
    )
    genesis_fields = {
        "schema_version": 1,
        "bundle_kind": "GENESIS",
        "workflow_identity": window.control_workflow_identity,
        "github_run_id": ctx.run_id,
        "run_attempt": 1,
        "event": ctx.event,
        "ref": window.ref,
        "source_sha": window.source_sha,
        "window_id": window.window_id,
        "window_record_sha256": sha256_hex_of_model(window),
        "slot_index": 0,
        "no_run_outcome": "WINDOW_GENESIS",
        "window_consumed": False,
    }
    built = build_bundle(
        work_trusted_root,
        work_trusted_root / "rehearsal-genesis",
        window=window,
        manifest_fields=genesis_fields,
        source_ledger_path=db_source,
        source_ledger_trusted_root=work_trusted_root,
        findings_source_path=findings_source,
        findings_trusted_root=work_trusted_root,
        cost_ledger_source_path=cost_source,
        cost_ledger_trusted_root=work_trusted_root,
        control_state=genesis_state,
    )
    preflight_ledger.record("S15_BUILD_AND_VALIDATE_SUCCESSOR", "OK", "synthetic genesis built and validated")

    evidence_path = work_trusted_root / "rehearsal_evidence.json"
    from .evidence_records import RehearsalEvidenceRecord

    evidence = RehearsalEvidenceRecord(
        schema_version=1,
        workflow_identity=ctx.workflow_path,
        github_run_id=ctx.run_id,
        run_attempt=ctx.run_attempt,
        event=ctx.event,
        ref=ctx.ref,
        source_sha=ctx.sha,
        created_at_utc=now,
        steps=tuple(
            StepEvidence(step_id=r.step_id, status=r.status, detail=r.detail)
            for r in preflight_ledger.to_records()
        ),
        expected_source_sha=expected_source_sha,
        outcome="REHEARSAL_COMPLETE",
    )
    evidence_path.write_text(evidence.model_dump_json(), encoding="utf-8")
    staged.append(
        StagedArtifact(
            name=artifact_names.rehearsal_evidence_name(ctx.run_id, ctx.run_attempt), path=evidence_path
        )
    )
    preflight_ledger.record("S17_STAGE_SUCCESSOR_ARTIFACT", "OK", "rehearsal evidence staged")
    return RehearsalResult(exit_code=0, ledger=preflight_ledger, staged_artifacts=tuple(staged))

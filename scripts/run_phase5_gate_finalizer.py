#!/usr/bin/env python
"""P5-D official-gate finalizer and publication confirmation (ADR-0012
sections 4, 10, 21 and Amendment A2/A5/A6; dispatch
q77-p5d-repair-stage2b2-implement-a, Stage 2B-2).

Two subcommands, run by ``.github/workflows/sentinel-official-gate.yml``
after the execute step:

``finalize`` (``if: always()``, before the evidence upload) establishes
whether THIS attempt consumed the one-shot marker, classifies the ONE
fixed terminal candidate with the Stage-2B-1 strict verifier, applies the
frozen pure decision table, and -- only where no trusted runner terminal
evidence exists -- writes a distinct execution-invalid record. It is
structurally incapable of authoring a quality result: every record it
writes comes from ``terminal.build_invalid_record``. A trusted terminal
record is never replaced. An ordinary exception emits no
``terminal_required`` output, so the upload fails open toward
publication.

``confirm`` (after the upload) proves publication from the external
artifact itself: exact name and run, artifact-id and digest consistency
where available, safe download, a permitted file tree and strict
``verify_terminal_bytes`` over the DOWNLOADED terminal bytes. Strict-valid
published bytes are authoritative; the ephemeral local candidate can only
add a warning. Only after a ``*_PUBLISHED`` state is the disposition
printed. An upload-step failure alone never proves absence.

Neither subcommand calls a model or provider, performs OIDC, creates or
consumes a marker, or writes anything durable outside the runner-local
work root. The gate purpose and envelope identity are imported from the
ADR-pinned runner, so they can never diverge from it.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._phase5_common import (  # noqa: E402
    attribute_invalid_record,
    build_evidence_client,
    emit_github_output,
    terminal_identity,
    terminal_layout,
)
from scripts.run_phase5_official_gate import (  # noqa: E402
    ENVELOPE,
    PURPOSE,
    _bounded_exception_type,
    gate_profile_identity,
)
from sentinel.phase5 import artifact_names  # noqa: E402
from sentinel.phase5.github_context import derive_github_context  # noqa: E402
from sentinel.phase5.github_evidence import ArtifactRef, ArtifactUnsafe  # noqa: E402
from sentinel.phase5.journal import (  # noqa: E402
    OperationalJournal,
    install_observing_signal_handlers,
    read_journal,
    summarize_journal,
)
from sentinel.phase5.terminal import (  # noqa: E402
    CHECKS_FILENAME,
    JOURNAL_FILENAME,
    PRIOR_ABSENT,
    PUBLICATION_ALLOWLIST,
    TERMINAL_FILENAME,
    TRUSTED_KINDS,
    TerminalEvidenceError,
    TerminalStateError,
    UntrustedPrior,
    assert_transition,
    build_invalid_record,
    classify_candidate,
    consumption_from,
    copy_to_quarantine,
    decide_finalization,
    inventory_publication_root,
    move_to_quarantine,
    verify_terminal_bytes,
    write_terminal_atomically,
)

# ---------------------------------------------------------------------------
# Bounded REST budgets (inside the finalize/confirm step timeouts, which
# total at most 4 minutes of GitHub's 5-minute cancellation window)
# ---------------------------------------------------------------------------

FINALIZE_REQUEST_TIMEOUT_S = 8.0
FINALIZE_MAX_ATTEMPTS = 3
FINALIZE_SLEEP_S = 4.0
FINALIZE_LATEST_ATTEMPT_START_S = 16.0
FINALIZE_BUDGET_S = 30.0

CONFIRM_REQUEST_TIMEOUT_S = 8.0
CONFIRM_DOWNLOAD_TIMEOUT_S = 12.0
CONFIRM_BUDGET_S = 45.0
REQUIRED_ZERO_OBSERVATIONS = 3
MAX_LISTING_ATTEMPTS = 3
LISTING_SLEEP_S = 4.0
LISTING_DEADLINE_S = 34.0
LATEST_ATTEMPT_START_S = 26.0
MIN_ZERO_OBSERVATION_SPAN_S = 8.0
LATEST_DOWNLOAD_START_S = 33.0
CONFIRM_DOWNLOAD_DIRNAME = "confirm-download"

PUBLISHED_STATES = frozenset({"TERMINAL_EVIDENCE_PUBLISHED", "INVALID_EVIDENCE_PUBLISHED"})
_CONFIRM_EXIT = {
    "TERMINAL_EVIDENCE_PUBLISHED": 0,
    "INVALID_EVIDENCE_PUBLISHED": 1,
    "PUBLICATION_UNCONFIRMED": 2,
    "PUBLICATION_FAILED": 3,
}
_HEX64 = re.compile(r"[0-9a-f]{64}")


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------


def rest_marker_visible(
    client, run_id: str, marker_name: str, *,
    sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
) -> "bool | None":
    """True when an exact, non-expired marker artifact of this run is
    visible; False only when every attempt made succeeded with zero
    matches and at least two completed inside the budget; None (assumed
    consumed by the caller) on any REST error or budget exhaustion."""
    t0 = monotonic()
    completed_zero = 0
    for attempt in range(FINALIZE_MAX_ATTEMPTS):
        if attempt:
            sleep(FINALIZE_SLEEP_S)
        if monotonic() - t0 > FINALIZE_LATEST_ATTEMPT_START_S:
            break
        try:
            entries = client.list_run_artifacts_named(run_id, marker_name)
        except Exception:  # noqa: BLE001 - any REST fault is unknown, never absence
            return None
        if any(e.name == marker_name and e.workflow_run_id == run_id and not e.expired for e in entries):
            return True
        if monotonic() - t0 > FINALIZE_BUDGET_S:
            break
        completed_zero += 1
    return False if completed_zero >= 2 else None


def _emit_terminal_required(value: str, action: str) -> None:
    emit_github_output("terminal_required", value)
    emit_github_output("decision", action)


def _quarantine(journal: OperationalJournal, decision, *, publication_root: Path, quarantine_root: Path) -> None:
    if decision.quarantine_candidate:
        copy_to_quarantine(publication_root / TERMINAL_FILENAME, quarantine_root, publication_root=publication_root)
    inventory = inventory_publication_root(publication_root)
    if decision.quarantine_ancillary and CHECKS_FILENAME in inventory.present_allowlisted:
        digest = move_to_quarantine(
            publication_root / CHECKS_FILENAME, quarantine_root, publication_root=publication_root,
            path_class="ANCILLARY_WITHOUT_TRUSTED_QUALITY",
        )
        journal.append("FINALIZER_QUARANTINED", path_class="ANCILLARY_WITHOUT_TRUSTED_QUALITY", sha256=digest)
    if decision.quarantine_unexpected:
        for name in inventory.unexpected:
            path = publication_root / name
            try:
                if not stat.S_ISREG(os.lstat(path).st_mode):
                    continue  # never in the explicit upload list; left in place
            except OSError:
                continue
            digest = move_to_quarantine(path, quarantine_root, publication_root=publication_root, path_class="UNEXPECTED")
            journal.append("FINALIZER_QUARANTINED", path_class="UNEXPECTED", sha256=digest)


def _finalize_consumed(
    journal: OperationalJournal, *, identity, summary, consumption: str, execute_outcome: str,
    publication_root: Path, staging_root: Path, quarantine_root: Path,
) -> int:
    journal.append("FINALIZER_CONSUMPTION", consumption=consumption)
    try:
        verdict = classify_candidate(publication_root, identity)
    except TerminalEvidenceError:
        journal.append("FINALIZER_DECISION", action="INTERNAL_ERROR")
        _emit_terminal_required("true", "INTERNAL_ERROR")
        print(f"FINALIZE: action=INTERNAL_ERROR consumption={consumption} candidate=n/a")
        return 4
    candidate_fields = {"sha256": verdict.sha256} if verdict.sha256 else {}
    journal.append("FINALIZER_CANDIDATE", candidate_verdict=verdict.kind, **candidate_fields)

    decision = decide_finalization(
        run_attempt=identity.run_attempt, consumption=consumption, candidate=verdict.kind,
        journal=summary, execute_step_outcome=execute_outcome, candidate_replaceable=verdict.replaceable,
    )
    journal.append("FINALIZER_DECISION", action=decision.action)
    line = f"FINALIZE: action={decision.action} consumption={consumption} candidate={verdict.kind}"

    if decision.action == "INTERNAL_ERROR":
        _emit_terminal_required("true", decision.action)
        print(line)
        return 4
    if decision.action == "PRESERVE_RUNNER_EVIDENCE":
        _quarantine(journal, decision, publication_root=publication_root, quarantine_root=quarantine_root)
        _emit_terminal_required("true", decision.action)
        print(line)
        return 0
    if decision.action not in ("WRITE_INFRASTRUCTURE_INVALID", "WRITE_UNCLASSIFIED"):
        raise TerminalEvidenceError("unexpected finalizer action for a consumed attempt")

    _quarantine(journal, decision, publication_root=publication_root, quarantine_root=quarantine_root)
    model, profile_name = gate_profile_identity()
    common = dict(
        identity=identity, envelope=ENVELOPE, created_at_utc=datetime.now(timezone.utc),
        model=model, profile_name=profile_name,
    )
    if decision.action == "WRITE_INFRASTRUCTURE_INVALID":
        record = build_invalid_record(
            **common, infrastructure_cause=decision.infrastructure_cause, writer="FINALIZER",
        )
    else:
        record = build_invalid_record(
            **common, unclassified_basis=decision.unclassified_basis,
            observed_signals=decision.observed_signals,
        )
    record = attribute_invalid_record(record, purpose=PURPOSE)
    prior = PRIOR_ABSENT if verdict.kind == "ABSENT" else UntrustedPrior(sha256=verdict.sha256)
    write_terminal_atomically(
        record, publication_root=publication_root, staging_root=staging_root, prior=prior, identity=identity,
    )
    try:
        assert_transition(summary.last_state, "INVALID_EVIDENCE_WRITTEN", writer="FINALIZER")
    except TerminalStateError:
        pass
    else:
        journal.append("STATE_TRANSITION", state_from=summary.last_state, state_to="INVALID_EVIDENCE_WRITTEN")
    _emit_terminal_required("true", decision.action)
    print(line)
    return 0


def _finalize(
    args, env, *, sleep: Callable[[float], None], monotonic: Callable[[], float],
) -> int:
    ctx = derive_github_context(env)
    identity = terminal_identity(ctx, args.expected_source_sha, PURPOSE)
    publication_root, staging_root, quarantine_root = terminal_layout(args.artifacts_dir)
    journal_path = publication_root / JOURNAL_FILENAME
    # Read the runner's journal BEFORE the finalizer appends anything.
    summary = summarize_journal(read_journal(journal_path))
    execute_outcome = env.get("EXECUTE_STEP_OUTCOME", "")

    if ctx.run_attempt != 1:
        decision = decide_finalization(
            run_attempt=ctx.run_attempt, consumption="CONSUMED", candidate="ABSENT",
            journal=summary, execute_step_outcome=execute_outcome,
        )
        _emit_terminal_required("false", decision.action)
        print(f"FINALIZE: action={decision.action} consumption=n/a candidate=n/a")
        return 0

    marker_outcome = env.get("MARKER_STEP_OUTCOME", "")
    rest_visible: "bool | None" = None
    if marker_outcome not in ("success", "skipped"):
        try:
            client = build_evidence_client(
                env, request_timeout_s=FINALIZE_REQUEST_TIMEOUT_S, download_timeout_s=FINALIZE_REQUEST_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 - no REST answer: fail toward consumed
            client = None
        if client is not None:
            rest_visible = rest_marker_visible(
                client, ctx.run_id, artifact_names.oneshot_marker_name(PURPOSE, ctx.run_id),
                sleep=sleep, monotonic=monotonic,
            )
    consumption = consumption_from(marker_outcome, rest_visible)

    if consumption == "NOT_CONSUMED_BY_THIS_ATTEMPT":
        decision = decide_finalization(
            run_attempt=ctx.run_attempt, consumption=consumption, candidate="ABSENT",
            journal=summary, execute_step_outcome=execute_outcome,
        )
        _emit_terminal_required("false", decision.action)
        print(f"FINALIZE: action={decision.action} consumption={consumption} candidate=n/a")
        return 0

    journal = OperationalJournal(journal_path, writer="FINALIZER").open()
    restore_signal_handlers = install_observing_signal_handlers(journal)
    try:
        return _finalize_consumed(
            journal, identity=identity, summary=summary, consumption=consumption,
            execute_outcome=execute_outcome, publication_root=publication_root,
            staging_root=staging_root, quarantine_root=quarantine_root,
        )
    finally:
        restore_signal_handlers()
        journal.close()


def cmd_finalize(
    args: argparse.Namespace, *,
    sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
) -> int:
    try:
        return _finalize(args, os.environ, sleep=sleep, monotonic=monotonic)
    except Exception as exc:  # noqa: BLE001 - ordinary exceptions only; no output means fail open
        print(f"FINALIZE ERROR: exception_type={_bounded_exception_type(exc)}")
        return 5


# ---------------------------------------------------------------------------
# confirm
# ---------------------------------------------------------------------------


class ConfirmResult(NamedTuple):
    state: str
    reason: "str | None" = None
    artifact_id: "int | None" = None
    warnings: "tuple[str, ...]" = ()
    disposition: "str | None" = None
    exception_type: "str | None" = None


def _normalize_digest(value: "str | None") -> "str | None":
    if not value or not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text.startswith("sha256:"):
        text = text[len("sha256:"):]
    return text if _HEX64.fullmatch(text) else None


def confirm_publication(
    *, client, identity, artifact_name: str, upload_outcome: str, uploaded_artifact_id: str,
    uploaded_digest: str, publication_root: Path, work_root: Path,
    sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
) -> ConfirmResult:
    t0 = monotonic()

    def elapsed() -> float:
        return monotonic() - t0

    run_id = identity.run_id
    rest_error = False
    budget_exhausted = False
    zero_starts: "list[float]" = []
    matches = None

    for attempt in range(MAX_LISTING_ATTEMPTS):
        if attempt:
            sleep(LISTING_SLEEP_S)
        started = elapsed()
        if started > LATEST_ATTEMPT_START_S:
            budget_exhausted = True
            break
        try:
            entries = client.list_run_artifacts_named(run_id, artifact_name)
        except Exception:  # noqa: BLE001 - an error is never absence; keep observing
            rest_error = True
            continue
        exact = [e for e in entries if e.name == artifact_name]
        if exact:
            matches = exact
            break
        if elapsed() <= LISTING_DEADLINE_S:
            zero_starts.append(started)
        else:
            budget_exhausted = True

    if matches is None:
        if rest_error:
            return ConfirmResult("PUBLICATION_UNCONFIRMED", "REST_UNAVAILABLE")
        if budget_exhausted or len(zero_starts) < REQUIRED_ZERO_OBSERVATIONS:
            return ConfirmResult("PUBLICATION_UNCONFIRMED", "CONFIRM_BUDGET_EXHAUSTED")
        if zero_starts[-1] - zero_starts[0] < MIN_ZERO_OBSERVATION_SPAN_S:
            return ConfirmResult("PUBLICATION_UNCONFIRMED", "OBSERVATION_WINDOW_INCOMPLETE")
        if upload_outcome == "failure":
            return ConfirmResult("PUBLICATION_FAILED", "POSITIVELY_ABSENT")
        return ConfirmResult("PUBLICATION_UNCONFIRMED", "NOT_YET_VISIBLE")

    if len(matches) > 1:
        return ConfirmResult("PUBLICATION_UNCONFIRMED", "AMBIGUOUS")
    entry = matches[0]
    if entry.workflow_run_id != run_id or entry.expired:
        return ConfirmResult("PUBLICATION_FAILED", "ARTIFACT_IDENTITY_INVALID", entry.id)
    uploaded_id = (uploaded_artifact_id or "").strip()
    if uploaded_id and uploaded_id != str(entry.id):
        return ConfirmResult("PUBLICATION_FAILED", "ARTIFACT_ID_MISMATCH", entry.id)
    uploaded = _normalize_digest(uploaded_digest)
    listed = _normalize_digest(entry.digest)
    if uploaded and listed and uploaded != listed:
        return ConfirmResult("PUBLICATION_FAILED", "DIGEST_MISMATCH", entry.id)

    if elapsed() > LATEST_DOWNLOAD_START_S:
        return ConfirmResult("PUBLICATION_UNCONFIRMED", "CONFIRM_BUDGET_EXHAUSTED", entry.id)
    try:
        root = client.download_artifact(
            ArtifactRef(id=entry.id, name=entry.name, workflow_run_id=entry.workflow_run_id),
            work_root, Path(work_root) / CONFIRM_DOWNLOAD_DIRNAME,
        )
    except ArtifactUnsafe:
        return ConfirmResult("PUBLICATION_FAILED", "TREE_INVALID", entry.id)
    except Exception:  # noqa: BLE001 - transport/extraction-root faults are unknown, not failure
        return ConfirmResult("PUBLICATION_UNCONFIRMED", "DOWNLOAD_UNAVAILABLE", entry.id)

    names = set()
    for child in Path(root).iterdir():
        if child.is_symlink() or not child.is_file():
            return ConfirmResult("PUBLICATION_FAILED", "TREE_INVALID", entry.id)
        names.add(child.name)
    if not names <= PUBLICATION_ALLOWLIST or TERMINAL_FILENAME not in names:
        return ConfirmResult("PUBLICATION_FAILED", "TREE_INVALID", entry.id)

    data = (Path(root) / TERMINAL_FILENAME).read_bytes()
    verdict = verify_terminal_bytes(data, identity)
    if verdict.kind not in TRUSTED_KINDS or verdict.record is None:
        return ConfirmResult("PUBLICATION_FAILED", "PUBLISHED_EVIDENCE_UNTRUSTED", entry.id)
    state = "TERMINAL_EVIDENCE_PUBLISHED" if verdict.kind == "TRUSTED_QUALITY" else "INVALID_EVIDENCE_PUBLISHED"

    # Post-publication integrity warnings: consistency only, never authority.
    warnings: "list[str]" = []
    try:
        local = classify_candidate(publication_root, identity)
    except Exception:  # noqa: BLE001 - an unusable local root is only a warning
        local = None
    if local is not None and local.kind in TRUSTED_KINDS:
        if local.sha256 != hashlib.sha256(data).hexdigest():
            warnings.append("LOCAL_PAYLOAD_SHA_MISMATCH")
    else:
        warnings.append("LOCAL_CANDIDATE_UNAVAILABLE")
    if verdict.kind == "TRUSTED_QUALITY" and CHECKS_FILENAME not in names:
        warnings.append("CHECKS_ABSENT")
    return ConfirmResult(state, None, entry.id, tuple(warnings), verdict.record.disposition)


def _write_step_summary(result: ConfirmResult, artifact_name: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("### P5-D gate evidence publication\n\n")
        handle.write(f"- publication state: `{result.state}`\n")
        handle.write(f"- artifact: `{artifact_name}` (id `{result.artifact_id}`)\n")
        handle.write(f"- disposition: `{result.disposition}`\n")
        handle.write(f"- warnings: `{','.join(result.warnings) or 'none'}`\n")


def report_confirmation(result: ConfirmResult, artifact_name: str) -> int:
    warnings = ",".join(result.warnings) or "none"
    line = (
        f"PUBLICATION: state={result.state} artifact={artifact_name} "
        f"artifact_id={result.artifact_id if result.artifact_id is not None else 'n/a'} "
        f"reason={result.reason or 'none'} warnings={warnings}"
    )
    if result.exception_type:
        line += f" exception_type={result.exception_type}"
    print(line)
    emit_github_output("publication_state", result.state)
    emit_github_output("publication_warnings", warnings)
    # The disposition is surfaced ONLY after authoritative publication.
    if result.state in PUBLISHED_STATES and result.disposition:
        print(f"DISPOSITION: {result.disposition}")
        _write_step_summary(result, artifact_name)
    return _CONFIRM_EXIT[result.state]


def cmd_confirm(
    args: argparse.Namespace, *,
    sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
) -> int:
    env = os.environ
    artifact_name = "n/a"
    try:
        ctx = derive_github_context(env)
        identity = terminal_identity(ctx, args.expected_source_sha, PURPOSE)
        artifact_name = artifact_names.gate_evidence_name(ctx.run_id, ctx.run_attempt)
        publication_root, _staging_root, _quarantine_root = terminal_layout(args.artifacts_dir)
        client = build_evidence_client(
            env, request_timeout_s=CONFIRM_REQUEST_TIMEOUT_S, download_timeout_s=CONFIRM_DOWNLOAD_TIMEOUT_S,
        )
        result = confirm_publication(
            client=client, identity=identity, artifact_name=artifact_name,
            upload_outcome=env.get("UPLOAD_STEP_OUTCOME", ""),
            uploaded_artifact_id=env.get("UPLOADED_ARTIFACT_ID", ""),
            uploaded_digest=env.get("UPLOADED_ARTIFACT_DIGEST", ""),
            publication_root=publication_root, work_root=args.work_root,
            sleep=sleep, monotonic=monotonic,
        )
    except Exception as exc:  # noqa: BLE001 - ordinary exceptions only; never read as published
        result = ConfirmResult(
            "PUBLICATION_UNCONFIRMED", "CONFIRM_EXCEPTION", exception_type=_bounded_exception_type(exc),
        )
    return report_confirmation(result, artifact_name)


def main(argv: "list[str]") -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    fin = sub.add_parser("finalize")
    fin.add_argument("--expected-source-sha", required=True)
    fin.add_argument("--artifacts-dir", type=Path, required=True)

    con = sub.add_parser("confirm")
    con.add_argument("--expected-source-sha", required=True)
    con.add_argument("--artifacts-dir", type=Path, required=True)
    con.add_argument("--work-root", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "finalize":
        return cmd_finalize(args)
    return cmd_confirm(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

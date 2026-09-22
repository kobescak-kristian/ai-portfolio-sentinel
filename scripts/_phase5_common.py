"""Shared, tested-once plumbing for the six ``run_phase5_*.py`` entry
points (P5-B Part 3/3). Not itself a workflow entrypoint.

Deliberately outside ``sentinel/``, ``checks/``, ``contracts/``,
``telemetry/``, ``agents/`` and ``runner/`` — none of
``tests/test_dependency_surface.py``'s per-root third-party allowlist
or ``tests/test_read_only_boundary.py``'s SDK-import ban applies to
``scripts/``, matching the existing ``run_phase3_dev_gate.py`` /
``run_phase4_loop_gate.py`` precedent of a repo-root-relative,
directly-executed script.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
from pathlib import Path
from typing import Callable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.checker import oidc  # noqa: E402
from sentinel.phase5 import artifact_names  # noqa: E402
from sentinel.phase5.bundle import BundleSafetyError, create_fresh_root  # noqa: E402
from sentinel.phase5.github_context import GithubContextError, derive_github_context  # noqa: E402
from sentinel.phase5.github_evidence import GithubEvidenceClient  # noqa: E402
from sentinel.phase5.models import OneShotMarker  # noqa: E402
from sentinel.phase5.oneshot import (  # noqa: E402
    OneShotAlreadyConsumed,
    OneShotDiscoveryAmbiguous,
    assert_purpose_not_yet_consumed,
    assert_purpose_not_yet_consumed_durably,
)
from sentinel.phase5.receipts import (  # noqa: E402
    DEFAULT_REGISTRY_PATH,
    Phase5Receipt,
    ReceiptRegistryError,
    load_registry,
)
from sentinel.phase5.replacement import replacement_history_verdict  # noqa: E402
from sentinel.phase5.evidence_records import GateEvidenceRecord, TerminalWriter  # noqa: E402
from sentinel.phase5.journal import OperationalJournal, read_journal  # noqa: E402
from sentinel.phase5.replacement import (  # noqa: E402
    OWNER_RULING_ID,
    REPLACEMENT_OF_RUN_ID,
    REPLACEMENT_PURPOSE,
)
from sentinel.phase5.terminal import (  # noqa: E402
    JOURNAL_FILENAME,
    EnvelopeIdentity,
    TerminalIdentity,
    assert_transition,
)


class Phase5ScriptError(RuntimeError):
    """A Phase-5 entrypoint refused before reaching its provider-capable
    or lineage-mutating boundary. Exit code 2 by convention."""


# ---------------------------------------------------------------------------
# Bounded pre-provider retry (Q-77 B5-P0; plan q77-p5d-repair-stage2cb5-plan-d
# Part 7). Deliberately NOT a general retry framework: it is applied to exactly
# three idempotent external READS in this repository (GitHub live-main
# verification here, plus GitHub prior-run discovery and the ECB FX fetch at the
# timing driver's own call sites) and to GitHub assertion acquisition inside
# ``agents/checker/oidc.py``. No provider/model exchange, no workflow rerun and
# no rehearsal rerun is ever retried.
# ---------------------------------------------------------------------------

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (1.0, 2.0)
TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_HTTP_STATUS_IN_MESSAGE = re.compile(r"(?:returned|failed with) HTTP (\d{3})")
_TRANSIENT_TEXT_MARKERS = ("transport error", "timed out", "timeout")


def is_transient_read_failure(exc: BaseException) -> bool:
    """True ONLY for transport / service-availability failures.

    Deterministic failures fail closed on the first attempt, because the
    next identical attempt fails identically and retrying would merely
    delay the refusal: invalid or malformed parsed evidence, a schema or
    shape mismatch, an authorization or configuration rejection, a
    frozen-hash mismatch, a source mismatch, a prior run actually being
    present, and every other deterministic validation failure.

    ``urllib.error.HTTPError`` is tested BEFORE ``URLError`` because it
    subclasses it -- a real ``urlopen`` raises ``HTTPError`` for every
    non-2xx status, so classifying on ``URLError`` alone would wrongly
    make a 403 look transient.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, urllib.error.HTTPError):
            return current.code in TRANSIENT_HTTP_STATUSES
        if isinstance(current, (urllib.error.URLError, TimeoutError)):
            return True
        current = current.__cause__
    match = _HTTP_STATUS_IN_MESSAGE.search(str(exc))
    if match is not None:
        return int(match.group(1)) in TRANSIENT_HTTP_STATUSES
    text = str(exc).lower()
    if any(marker in text for marker in _TRANSIENT_TEXT_MARKERS):
        return True
    return isinstance(exc, OSError)


def bounded_read_retry(
    operation: Callable,
    *,
    sleep: Callable[[float], None] = time.sleep,
    is_transient: Callable[[BaseException], bool] = is_transient_read_failure,
):
    """Call ``operation`` up to ``RETRY_ATTEMPTS`` times, re-raising the
    last error once the bound is exhausted.

    Backoff is 1s then 2s, and there is deliberately NO sleep after the
    final failed attempt. ``operation`` must wrap ONLY the external read
    itself -- never the validation that consumes its result -- so a
    deterministic refusal derived from a successful read can never be
    retried.
    """
    last: BaseException | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 - re-raised unless classified transient
            last = exc
            if not is_transient(exc):
                raise
            if attempt < len(RETRY_BACKOFF_SECONDS):
                sleep(RETRY_BACKOFF_SECONDS[attempt])
    raise last  # noqa: RSE102 - the loop body guarantees a non-None last error


def build_evidence_client(
    env=None, *, request_timeout_s: float = 30.0, download_timeout_s: float = 60.0
) -> GithubEvidenceClient:
    """Pop GITHUB_TOKEN (seam 4) and construct a REST client. Must run
    before any Agent-SDK-capable port is built in the same process.
    The timeout keywords default to the client's historical values;
    only the Stage-2B-2 finalizer passes shorter, bounded ones."""
    env = env if env is not None else os.environ
    token = oidc.pop_github_token(env)
    ctx = derive_github_context(env)
    return GithubEvidenceClient(
        api_url=ctx.api_url, repository=ctx.repository, token=token,
        request_timeout_s=request_timeout_s, download_timeout_s=download_timeout_s,
    )


def git(args: Sequence[str]) -> str:
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def assert_expected_source_on_disk(expected_source_sha: str) -> str:
    """Independent, local re-check that the checked-out HEAD (and
    origin/main) equal the operator-supplied ``expected_source_sha`` —
    never trusts ``GITHUB_SHA`` alone (seam 3). Returns the verified
    HEAD SHA."""
    import re

    if not re.fullmatch(r"[0-9a-f]{40}", expected_source_sha or ""):
        raise Phase5ScriptError("expected_source_sha is not exactly 40 lowercase hexadecimal characters")
    git(["fetch", "origin"])
    head = git(["rev-parse", "HEAD"])
    if head != expected_source_sha:
        raise Phase5ScriptError(f"HEAD {head} != expected_source_sha {expected_source_sha}")
    origin_main = git(["rev-parse", "origin/main"])
    if origin_main != head:
        raise Phase5ScriptError(f"origin/main {origin_main} != HEAD {head}")
    return head


def assert_expected_source_live(client: GithubEvidenceClient, expected_source_sha: str) -> None:
    """Independent, LIVE re-check against GitHub's own current main
    head (seam 3) — immediately before the irreversible boundary
    (marker upload / OIDC / GENESIS), not only at process start.

    The bounded retry covers ONLY the GitHub read. The comparison below
    it is deterministic: a genuine source mismatch refuses on the first
    attempt and is never retried."""
    live_head = bounded_read_retry(client.get_main_head_sha)
    if live_head != expected_source_sha:
        raise Phase5ScriptError(
            f"live origin/main head {live_head} != expected_source_sha {expected_source_sha}"
        )


def prepare_fresh_work_root(work_root: Path) -> Path:
    """Safely establish ``work_root`` as a fresh directory beneath its
    already-existing trusted parent, using the SAME anchored-creation
    safety primitive (``bundle.create_fresh_root``) that later governs
    extracting downloaded artifact content beneath it (dispatch
    q77-p5d-premarker-workroot-init-repair-a).

    ``discover_oneshot_markers`` treats ``work_root`` as a trusted
    extraction anchor, but nothing previously established that
    ``work_root`` itself was ever safely created — on GitHub Actions,
    ``WORK_ROOT`` (``${{ runner.temp }}/p5-gate`` or ``.../p5-probe``)
    is a subdirectory of the runner-guaranteed ``runner.temp``, never
    created by any workflow step. ``create_fresh_root`` requires its
    OWN destination_trusted_root argument to already exist, so calling
    it with ``work_root`` itself as that argument (as every existing
    caller previously did, unconditionally) fails whenever
    ``work_root`` does not yet exist -- exactly the
    ``BundleSafetyError: destination trusted root does not exist``
    crash observed in a real preflight rehearsal the instant a real
    marker existed to discover (P5-C's own run never hit this because
    it discovered zero markers).

    This call must run BEFORE any one-shot discovery. It fails closed
    (raises ``Phase5ScriptError``, caught by the same
    ``except Phase5ScriptError`` every other preflight check already
    uses) if ``work_root`` already exists, is a symlink, or its parent
    is missing/unsafe -- never silently reused, and never using
    ``mkdir(parents=True, exist_ok=True)`` as a substitute for that
    freshness contract."""
    try:
        return create_fresh_root(work_root.parent, work_root)
    except BundleSafetyError as exc:
        raise Phase5ScriptError(f"work-root preparation failed: {exc}") from exc


def discover_oneshot_markers(client: GithubEvidenceClient, work_root: Path) -> list[OneShotMarker]:
    """List + download + parse every one-shot-marker artifact
    (both purposes; the caller filters by purpose via
    ``oneshot.assert_purpose_not_yet_consumed``). A malformed marker
    body fails closed rather than being silently skipped."""
    refs = client.list_artifacts(artifact_names.ONESHOT_PREFIX)
    markers: list[OneShotMarker] = []
    for index, ref in enumerate(refs):
        root = client.download_artifact(ref, work_root, work_root / f"marker-{index}")
        marker_path = root / "marker.json"
        if not marker_path.exists():
            raise Phase5ScriptError(f"one-shot marker artifact {ref.name!r} is missing marker.json")
        markers.append(OneShotMarker.model_validate_json(marker_path.read_text(encoding="utf-8")))
    return markers


def load_durable_history(registry_path: "Path | None" = None) -> tuple[Phase5Receipt, ...]:
    """Strict-load the committed Phase-5 receipt registry (ADR-0012
    Amendment A1; dispatch q77-p5d-repair-stage2-implement-a). A
    missing, empty, truncated or chain-broken registry fails closed as
    ``Phase5ScriptError`` -- absence is never "nothing was consumed".
    Defaults to the repository's own committed path so every caller
    consults the SAME registry the pre-push append-only guard
    protects. Read-only: never writes, never mutates the registry."""
    path = registry_path if registry_path is not None else (REPO_ROOT / DEFAULT_REGISTRY_PATH)
    try:
        return load_registry(path)
    except ReceiptRegistryError as exc:
        raise Phase5ScriptError(f"durable receipt registry failed to load: {exc}") from exc


def assert_oneshot_not_consumed_durably(
    purpose: str, receipts: "tuple[Phase5Receipt, ...]", markers: "list[OneShotMarker]"
) -> None:
    """Durable-history-first one-shot refusal at the script boundary
    (dispatch q77-p5d-repair-stage2-implement-a). Wraps
    ``sentinel.phase5.oneshot.assert_purpose_not_yet_consumed_durably``,
    translating its exceptions into ``Phase5ScriptError`` so every
    Phase-5 entrypoint reports refusal through its existing
    ``except Phase5ScriptError`` path rather than an unhandled
    traceback. Consumption truth comes from ``receipts`` FIRST --
    artifact expiry of every live marker can never silently un-consume
    a purpose -- and only then, defensively, from ``markers``."""
    try:
        assert_purpose_not_yet_consumed_durably(purpose, receipts, markers)
    except OneShotAlreadyConsumed as exc:
        raise Phase5ScriptError(f"one-shot purpose already consumed: {exc}") from exc
    except OneShotDiscoveryAmbiguous as exc:
        raise Phase5ScriptError(f"one-shot discovery ambiguous: {exc}") from exc


def assert_replacement_history_permits(
    receipts: "tuple[Phase5Receipt, ...]", markers: "list[OneShotMarker]", purpose: str
) -> None:
    """Structural replacement-eligibility gate (dispatch
    q77-p5d-repair-stage2-implement-a). NOT ARMED by this dispatch: no
    script in this repository constructs a marker for the replacement
    purpose, and ``PURPOSE`` in ``scripts/run_phase5_official_gate.py``
    stays the original ``P5D_OFFICIAL_SONNET_GATE`` value -- for which
    this check, and ``assert_oneshot_not_consumed_durably`` ahead of
    it, both already and correctly refuse today, since the original
    purpose is durably consumed and permanently non-qualifying. Wired
    ahead of time so a later, separately governed arming dispatch needs
    only to switch that constant; this eligibility check requires no
    further change to do its job then. Raises ``Phase5ScriptError``
    unless durable history and live markers currently permit exactly
    one future replacement for ``purpose``."""
    verdict = replacement_history_verdict(receipts, markers, purpose)
    if not verdict.permits_one_replacement:
        raise Phase5ScriptError(f"replacement not permitted by durable history: {verdict.reason}")


def assert_marker_visible_for_this_run(client: GithubEvidenceClient, run_id: str, expected_name: str) -> None:
    """Confirm the just-uploaded, immutable one-shot marker is actually
    visible via REST for THIS run before any OIDC/provider activity —
    a provider failure can never occur before the marker exists."""
    names = {ref.name for ref in client.list_artifacts_for_run(run_id)}
    if expected_name not in names:
        raise Phase5ScriptError(
            f"one-shot marker artifact {expected_name!r} is not yet visible for run {run_id}"
        )


def write_marker_json(marker: OneShotMarker, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(marker.model_dump_json(), encoding="utf-8")
    return path


def write_json_artifact(payload: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def emit_github_output(name: str, value: str) -> None:
    """Write one ``name=value`` line to ``$GITHUB_OUTPUT`` if set
    (real Actions runs); a no-op locally so scripts stay directly
    runnable outside Actions."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


# ---------------------------------------------------------------------------
# Stage 2B-2 terminal-publication wiring (ADR-0012 sections 9, 10, 21 and
# Amendment A2/A6; dispatch q77-p5d-repair-stage2b2-implement-a). Shared by
# the official gate runner and the gate finalizer; arms nothing.
# ---------------------------------------------------------------------------

TERMINAL_STAGING_DIRNAME = "terminal-staging"
TERMINAL_QUARANTINE_DIRNAME = "terminal-quarantine"


def terminal_layout(artifacts_dir: Path) -> "tuple[Path, Path, Path]":
    """(publication root, staging root, quarantine root). Staging and
    quarantine are same-device siblings of the publication root, never
    inside it, so neither can ever become a publication candidate."""
    artifacts_dir = Path(artifacts_dir)
    parent = artifacts_dir.parent
    return artifacts_dir, parent / TERMINAL_STAGING_DIRNAME, parent / TERMINAL_QUARANTINE_DIRNAME


def create_terminal_layout(artifacts_dir: Path) -> "tuple[Path, Path, Path]":
    """Create all three layout directories fresh. Any pre-existing
    directory or missing parent fails closed as ``Phase5ScriptError``."""
    layout = terminal_layout(artifacts_dir)
    try:
        for directory in layout:
            directory.mkdir(exist_ok=False)
    except OSError as exc:
        raise Phase5ScriptError(f"terminal layout creation failed: {type(exc).__name__}") from exc
    return layout


def establish_preflight_journal(artifacts_dir: Path, *, fsync=None) -> Path:
    """Fail-closed, pre-marker establishment of the terminal layout and
    the RUNNER operational journal with exactly one PREFLIGHTED state
    transition.

    ``OperationalJournal`` deliberately latches faults instead of
    raising, so every latch, a missing append result and a durable
    read-back are all checked explicitly here. Any failure raises
    ``Phase5ScriptError`` so preflight stops BEFORE the one-shot marker
    is written: a known terminal-publication instrumentation fault never
    consumes a marker. ``fsync`` is injectable for tests only. Returns
    the journal path."""
    publication_root, _staging, _quarantine = create_terminal_layout(artifacts_dir)
    path = publication_root / JOURNAL_FILENAME
    journal = OperationalJournal(path, writer="RUNNER", fsync=fsync if fsync is not None else os.fsync)
    journal.open()
    if journal.broken:
        journal.close()
        raise Phase5ScriptError("operational journal could not be established")
    assert_transition(None, "PREFLIGHTED", writer="RUNNER")
    event = journal.append("STATE_TRANSITION", state_to="PREFLIGHTED")
    broken_after_append = journal.broken
    journal.close()
    if event is None or broken_after_append or journal.broken:
        raise Phase5ScriptError("PREFLIGHTED journal append failed")
    readback = read_journal(path)
    events = readback.events
    if (
        readback.integrity != "OK"
        or [e.event for e in events] != ["JOURNAL_OPENED", "STATE_TRANSITION"]
        or [e.seq for e in events] != [1, 2]
        or any(e.writer != "RUNNER" for e in events)
        or events[1].state_from is not None
        or events[1].state_to != "PREFLIGHTED"
    ):
        raise Phase5ScriptError("PREFLIGHTED journal read-back failed")
    return path


def terminal_identity(ctx, expected_source_sha: str, purpose: str) -> TerminalIdentity:
    return TerminalIdentity(
        workflow_identity=ctx.workflow_path, run_id=ctx.run_id, run_attempt=ctx.run_attempt,
        event=ctx.event, ref=ctx.ref, source_sha=ctx.sha,
        expected_source_sha=expected_source_sha, purpose=purpose,
    )


def terminal_writer_for(purpose: str, role: TerminalWriter) -> "TerminalWriter | None":
    """Purpose-gated writer attribution. ``verify_terminal_bytes`` trusts
    a record under any non-replacement purpose only when every
    replacement-provenance field AND ``terminal_writer`` are None; that
    rule is kept unchanged, so under the unarmed original purpose every
    writer is recorded as None. Only the replacement purpose (reachable
    solely after a separately governed arming dispatch) names the role."""
    return role if purpose == REPLACEMENT_PURPOSE else None


def replacement_provenance_fields(purpose: str, envelope: "EnvelopeIdentity | None") -> dict:
    if purpose != REPLACEMENT_PURPOSE:
        return {}
    assert_purpose_armable(purpose, envelope)
    return dict(
        replacement_of_run_id=REPLACEMENT_OF_RUN_ID,
        owner_ruling_id=OWNER_RULING_ID,
        marker_purpose=REPLACEMENT_PURPOSE,
        envelope_id=envelope.envelope_id,
        envelope_version=envelope.envelope_version,
    )


def attribute_invalid_record(record: GateEvidenceRecord, *, purpose: str) -> GateEvidenceRecord:
    """Apply purpose-gated writer attribution to a
    ``terminal.build_invalid_record`` result (which always names a
    writer). Re-validated, never ``model_copy``-patched."""
    if purpose == REPLACEMENT_PURPOSE:
        return record
    return GateEvidenceRecord.model_validate({**record.model_dump(), "terminal_writer": None})


def assert_purpose_armable(purpose: str, envelope: "EnvelopeIdentity | None") -> None:
    """The replacement purpose is never executable without a Stage-2C
    execution-envelope identity: every replacement record would fail
    strict provenance validation and be overwritten by the finalizer.
    Fails closed before any marker (preflight) or OIDC (execute)."""
    if purpose == REPLACEMENT_PURPOSE and envelope is None:
        raise Phase5ScriptError(
            "the replacement purpose requires a Stage-2C execution envelope identity; not armable"
        )

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
import subprocess
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.checker import oidc  # noqa: E402
from sentinel.phase5 import artifact_names  # noqa: E402
from sentinel.phase5.bundle import create_fresh_root  # noqa: E402
from sentinel.phase5.github_context import GithubContextError, derive_github_context  # noqa: E402
from sentinel.phase5.github_evidence import GithubEvidenceClient  # noqa: E402
from sentinel.phase5.models import OneShotMarker  # noqa: E402
from sentinel.phase5.oneshot import assert_purpose_not_yet_consumed  # noqa: E402


class Phase5ScriptError(RuntimeError):
    """A Phase-5 entrypoint refused before reaching its provider-capable
    or lineage-mutating boundary. Exit code 2 by convention."""


def build_evidence_client(env=None) -> GithubEvidenceClient:
    """Pop GITHUB_TOKEN (seam 4) and construct a REST client. Must run
    before any Agent-SDK-capable port is built in the same process."""
    env = env if env is not None else os.environ
    token = oidc.pop_github_token(env)
    ctx = derive_github_context(env)
    return GithubEvidenceClient(api_url=ctx.api_url, repository=ctx.repository, token=token)


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
    (marker upload / OIDC / GENESIS), not only at process start."""
    live_head = client.get_main_head_sha()
    if live_head != expected_source_sha:
        raise Phase5ScriptError(
            f"live origin/main head {live_head} != expected_source_sha {expected_source_sha}"
        )


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

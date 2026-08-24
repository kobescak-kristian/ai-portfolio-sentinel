"""Pure GitHub Actions execution-context parsing (P5-B Part 3/3).

Reads no environment variable itself — every function here takes an
injected mapping. This is what keeps ``sentinel/phase5/`` inside the
package's own no-network, no-GitHub-API-call, stdlib(+pydantic)-only
purity claim: parsing the *shape* GitHub already put in the job
environment is not a network or provider operation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .qualification import GithubRunMetadata

_HEX40 = re.compile(r"[0-9a-f]{40}")

_REQUIRED_VARS = (
    "GITHUB_REPOSITORY",
    "GITHUB_REPOSITORY_OWNER",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_EVENT_NAME",
    "GITHUB_REF",
    "GITHUB_SHA",
    "GITHUB_WORKFLOW_REF",
    "GITHUB_API_URL",
    "GITHUB_SERVER_URL",
)


class GithubContextError(RuntimeError):
    """The job environment does not describe a valid GitHub Actions
    execution context, or a caller-supplied expectation does not match
    it. Never carries a token, secret, or absolute local path — only
    identifiers already public in the workflow run itself."""


@dataclass(frozen=True)
class GithubActionsContext:
    repository: str
    repository_owner: str
    run_id: str
    run_attempt: int
    event: str
    ref: str
    sha: str
    workflow_path: str
    api_url: str
    server_url: str


def _workflow_path_from_ref(workflow_ref: str, repository: str) -> str:
    """``GITHUB_WORKFLOW_REF`` is
    ``<owner>/<repo>/.github/workflows/<file>.yml@<ref>``. This returns
    just the ``.github/workflows/<file>.yml`` slice — the exact string
    the REST run object's own ``path`` field carries, and the
    ``workflow_identity`` value used throughout the Phase-5 domain
    core."""
    before_at, sep, _after_at = workflow_ref.partition("@")
    if not sep:
        raise GithubContextError("GITHUB_WORKFLOW_REF is missing the '@<ref>' suffix")
    prefix = f"{repository}/"
    if not before_at.startswith(prefix):
        raise GithubContextError("GITHUB_WORKFLOW_REF does not start with '<repository>/'")
    path = before_at[len(prefix):]
    if not path.startswith(".github/workflows/"):
        raise GithubContextError("GITHUB_WORKFLOW_REF does not name a .github/workflows/ file")
    return path


def derive_github_context(env) -> GithubActionsContext:
    """Parse the exact ``GithubActionsContext`` this job is running as.
    Fails closed (``GithubContextError``) on any missing or malformed
    value — this is step S01 of the frozen preflight order and must
    never guess."""
    missing = [name for name in _REQUIRED_VARS if not env.get(name)]
    if missing:
        raise GithubContextError(
            "missing required GitHub Actions context variable(s): " + ", ".join(sorted(missing))
        )
    repository = env["GITHUB_REPOSITORY"]
    try:
        run_attempt = int(env["GITHUB_RUN_ATTEMPT"])
    except ValueError as exc:
        raise GithubContextError("GITHUB_RUN_ATTEMPT is not an integer") from exc
    if run_attempt < 1:
        raise GithubContextError("GITHUB_RUN_ATTEMPT must be >= 1")
    sha = env["GITHUB_SHA"]
    if not _HEX40.fullmatch(sha):
        raise GithubContextError("GITHUB_SHA is not exactly 40 lowercase hexadecimal characters")
    workflow_path = _workflow_path_from_ref(env["GITHUB_WORKFLOW_REF"], repository)
    return GithubActionsContext(
        repository=repository,
        repository_owner=env["GITHUB_REPOSITORY_OWNER"],
        run_id=env["GITHUB_RUN_ID"],
        run_attempt=run_attempt,
        event=env["GITHUB_EVENT_NAME"],
        ref=env["GITHUB_REF"],
        sha=sha,
        workflow_path=workflow_path,
        api_url=env["GITHUB_API_URL"],
        server_url=env["GITHUB_SERVER_URL"],
    )


def to_run_metadata(
    ctx: GithubActionsContext, *, created_at: datetime, run_started_at: datetime | None
) -> GithubRunMetadata:
    """Bind the env-derived identity to REST-derived timing evidence —
    the exact shape ``qualification.classify_run`` and
    ``qualification.associate_runs_to_slots`` consume."""
    return GithubRunMetadata(
        workflow_identity=ctx.workflow_path,
        github_run_id=ctx.run_id,
        run_attempt=ctx.run_attempt,
        event=ctx.event,
        ref=ctx.ref,
        source_sha=ctx.sha,
        created_at=created_at,
        run_started_at=run_started_at,
    )


def assert_expected_source(
    ctx: GithubActionsContext,
    expected_source_sha: str,
    *,
    expected_repository: str,
    require_attempt_1: bool,
) -> None:
    """Seam 3 (revision c): independent verification of an
    operator-supplied ``expected_source_sha`` against this job's own
    context — never against ``GITHUB_SHA`` alone, and never trusted as
    self-attestation. Every check is evaluated (not short-circuited on
    the first mismatch is fine for correctness, but message text stays
    identifier-only and generic) before raising."""
    if not _HEX40.fullmatch(expected_source_sha):
        raise GithubContextError("expected_source_sha is not exactly 40 lowercase hexadecimal characters")
    if ctx.repository != expected_repository:
        raise GithubContextError("repository does not match the expected repository")
    if ctx.ref != "refs/heads/main":
        raise GithubContextError("ref is not refs/heads/main")
    if require_attempt_1 and ctx.run_attempt != 1:
        raise GithubContextError("run_attempt is not 1")
    if ctx.sha != expected_source_sha:
        raise GithubContextError("GITHUB_SHA does not match expected_source_sha")

"""Stdlib-only GitHub REST/artifact client (P5-B Part 3/3).

The only network-touching module in ``sentinel/phase5/`` — every other
module in this package stays pure. Uses only ``urllib.request`` and
``zipfile`` (both stdlib), matching ``PER_ROOT_ALLOWED_THIRD_PARTY``'s
``sentinel: {"pydantic"}`` allowance exactly (no new third-party
dependency).

The bearer token is a constructor argument, held only as a private
instance attribute, and is never interpolated into any log line, error
message, or ``repr``. Downloaded bytes are always treated as untrusted
until ``bundle.validate_bundle`` (for a bundle) or a pydantic
``model_validate_json`` (for a marker/evidence record) succeeds —
this module performs no trust decision of its own.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .bundle import assert_trusted_path, create_fresh_root

_MAX_ARTIFACT_ENTRIES = 64
_MAX_ARTIFACT_UNCOMPRESSED_BYTES = 64 * 1024 * 1024  # 64 MiB — generous, still bounded

_DEFAULT_PORT_BY_SCHEME = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, int | None]:
    """(scheme, hostname, effective port) triple for ``url`` — the
    narrow, deterministic origin definition this module's redirect
    handling uses (dispatch
    q77-p5d-premarker-redirect-origin-tighten-a). Case-normalized
    (scheme/hostname are lowercased by ``urlsplit`` already); an
    unspecified port resolves to the scheme's registered default so
    ``https://x`` and ``https://x:443`` compare equal. Paths and query
    strings never participate in this comparison."""
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "").lower()
    hostname = parts.hostname or ""
    port = parts.port if parts.port is not None else _DEFAULT_PORT_BY_SCHEME.get(scheme)
    return (scheme, hostname, port)


class _NoAuthOnCrossOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirects exactly like ``urllib.request``'s stdlib default,
    except the ``Authorization`` header is stripped from the
    redirected request ONLY when the redirect target is a different
    origin (scheme + hostname + effective port; see ``_origin``) than
    the original request (dispatch
    q77-p5d-premarker-redirect-origin-tighten-a, narrowing the earlier
    unconditional strip from q77-p5d-premarker-artifact-redirect-repair-a).

    GitHub's artifact-download endpoint
    (``GET /repos/{repo}/actions/artifacts/{id}/zip``) responds with a
    302 to a pre-signed, time-limited storage URL on a DIFFERENT
    origin that authenticates via its own query-string signature and
    rejects an unexpected ``Authorization`` header with HTTP 401 — the
    stdlib default ``HTTPRedirectHandler.redirect_request`` copies
    every original header except ``Content-Length``/``Content-Type``
    onto the redirected request regardless of origin (confirmed by
    reading ``inspect.getsource`` of that method, 2026-08), so
    ``Authorization`` is forwarded by default. This override reuses
    that exact logic via ``super()`` and strips the one header that
    must never cross an origin boundary — a same-origin GitHub REST
    redirect (were one ever to occur) keeps its Authorization header
    intact, preserving normal GitHub REST authentication semantics."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None and _origin(req.full_url) != _origin(newurl):
            new_req.remove_header("Authorization")
        return new_req


def _redirect_safe_default_opener() -> Callable:
    """The default network opener for ``GithubEvidenceClient``: behaves
    exactly like ``urllib.request.urlopen`` for every existing call
    site, except a CROSS-ORIGIN redirect never carries the bearer
    token to its destination (see
    ``_NoAuthOnCrossOriginRedirectHandler``). Built once at import
    time — ``OpenerDirector.open`` has the same
    ``(request, timeout=...)`` call signature ``urlopen`` does, so
    every test that injects its own fake ``opener`` (replacing this
    default entirely) is completely unaffected."""
    return urllib.request.build_opener(_NoAuthOnCrossOriginRedirectHandler).open


_DEFAULT_OPENER = _redirect_safe_default_opener()


class GithubEvidenceError(RuntimeError):
    """A GitHub REST call failed, returned an unexpected shape, or a
    downloaded artifact failed the local safety checks below. Never
    carries the bearer token."""


class DiscoveryOverflow(GithubEvidenceError):
    """More result pages exist than ``page_limit`` permits. Fails
    closed rather than silently truncating discovery — a truncated
    listing could hide a real predecessor or a real one-shot marker."""


class ArtifactUnsafe(GithubEvidenceError):
    """A downloaded artifact zip contains an unsafe entry (absolute
    path, ``..`` traversal, backslash, symlink, or exceeds the bounded
    entry-count/size caps)."""


@dataclass(frozen=True)
class ArtifactRef:
    id: int
    name: str
    workflow_run_id: str

    @property
    def identity(self) -> str:
        return f"{self.name}::{self.id}"


@dataclass(frozen=True)
class ArtifactDetail:
    """One run-artifact listing entry with the fields publication
    confirmation needs (dispatch q77-p5d-repair-stage2b2-implement-a).
    ``expired`` entries are kept, never filtered, so the caller decides;
    ``digest`` is whatever the REST surface reports (possibly None)."""

    id: int
    name: str
    workflow_run_id: str
    expired: bool
    digest: str | None
    size_in_bytes: int | None


@dataclass(frozen=True)
class JobStep:
    """One step of a job (Stage 2C-B6-2): the replacement latch's
    prior-run proof reads only these returned fields."""

    name: str
    status: str
    conclusion: str | None
    number: int


@dataclass(frozen=True)
class JobDetail:
    """One entry of the attempt-scoped jobs listing (dispatch
    q77-p5d-repair-stage2c1-implement-a). Carries only the returned
    fields the job-start anchor resolver needs. The run attempt is NOT a
    field: it is part of the REQUEST identity of
    ``GET /repos/{owner}/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs``
    and is never read from a job body."""

    id: int
    run_id: str
    name: str
    status: str
    started_at: datetime | None
    runner_name: str | None


@dataclass(frozen=True)
class JobEvidence:
    """One job of an attempt with its ``conclusion`` and ``steps``
    (Stage 2C-B6-2: the replacement latch's prior-run proof). A separate
    type, so ``JobDetail``'s pinned field set stays unchanged."""

    id: int
    run_id: str
    name: str
    status: str
    conclusion: str | None
    steps: tuple[JobStep, ...]


def _job_detail_from(job: dict) -> JobDetail:
    """One attempt-jobs entry as a ``JobDetail``. Raises KeyError /
    TypeError / ValueError on any unexpected shape."""
    job_id = job["id"]
    job_run_id = job["run_id"]
    if isinstance(job_id, bool) or not isinstance(job_id, int):
        raise TypeError("id")
    if isinstance(job_run_id, bool) or not isinstance(job_run_id, int):
        raise TypeError("run_id")
    name = job["name"]
    status = job["status"]
    if not isinstance(name, str) or not isinstance(status, str):
        raise TypeError("name/status")
    started_raw = job.get("started_at")
    if started_raw is not None and not isinstance(started_raw, str):
        raise TypeError("started_at")
    runner_name = job.get("runner_name")
    if runner_name is not None and not isinstance(runner_name, str):
        raise TypeError("runner_name")
    return JobDetail(
        id=job_id,
        run_id=str(job_run_id),
        name=name,
        status=status,
        started_at=_parse_utc(started_raw),
        runner_name=runner_name,
    )


@dataclass(frozen=True)
class RunRef:
    run_id: str
    run_attempt: int
    event: str
    ref: str
    sha: str
    workflow_path: str
    created_at: datetime
    run_started_at: datetime | None
    # Stage 2C-B6-2 (replacement latch, run-number continuity). Defaulted,
    # so existing constructions and ``list_workflow_runs`` are unchanged.
    run_number: int | None = None
    status: str | None = None
    conclusion: str | None = None
    head_branch: str | None = None


@dataclass(frozen=True)
class CommitFile:
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str | None


@dataclass(frozen=True)
class CommitDetail:
    """``GET /repos/{owner}/{repo}/commits/{sha}``, reduced to what the
    replacement latch's commit-A check reads (Stage 2C-B6-2)."""

    sha: str
    parents: tuple[str, ...]
    files: tuple[CommitFile, ...]


@dataclass(frozen=True)
class PushActivity:
    """One ``push`` entry of ``GET /repos/{owner}/{repo}/activity``: GitHub's
    server-side record that ``ref`` moved from ``before`` to ``after`` at
    ``timestamp`` (Stage 2C-B6-2)."""

    before: str
    after: str
    ref: str
    activity_type: str
    timestamp: datetime


def _parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_aware_utc(value: object, label: str) -> datetime:
    """Stage 2C-B6-2 parsers only: a required timestamp that must carry a
    zero UTC offset, so a naive value can never reach a latch comparison."""
    parsed = _parse_utc(_required_str(value, label))
    offset = parsed.utcoffset() if parsed is not None else None
    if parsed is None or offset is None or offset.total_seconds() != 0:
        raise ValueError(label)
    return parsed


_HEX40 = re.compile(r"[0-9a-f]{40}")
_HTTP_DATE = re.compile(
    r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), (\d{2}) "
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4}) (\d{2}):(\d{2}):(\d{2}) GMT"
)
_HTTP_MONTHS = {
    name: index for index, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1
    )
}
_HTTP_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_http_date(value: object) -> datetime:
    """Strict RFC 9110 IMF-fixdate (``Sun, 06 Nov 1994 08:49:37 GMT``) as
    an aware UTC datetime. Locale-independent; the weekday must match the
    date. Anything else fails closed."""
    if not isinstance(value, str):
        raise GithubEvidenceError("GitHub response carries no Date header")
    match = _HTTP_DATE.fullmatch(value)
    if match is None:
        raise GithubEvidenceError("GitHub Date header is not an IMF-fixdate")
    weekday, day, month, year, hour, minute, second = match.groups()
    try:
        parsed = datetime(
            int(year), _HTTP_MONTHS[month], int(day), int(hour), int(minute), int(second), tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise GithubEvidenceError("GitHub Date header is not a valid date") from exc
    if _HTTP_WEEKDAYS[parsed.weekday()] != weekday:
        raise GithubEvidenceError("GitHub Date header weekday does not match its date")
    return parsed


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(label)
    return value


def _optional_str(value: object, label: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise TypeError(label)
    return value


def _required_str(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(label)
    return value


def _run_ref_from(run: object) -> RunRef:
    """Strict ``RunRef`` from one workflow-run object, including the
    Stage 2C-B6-2 fields. Any unexpected shape fails closed."""
    try:
        if not isinstance(run, dict):
            raise TypeError("run")
        created_at = _parse_aware_utc(run["created_at"], "created_at")
        started_raw = _optional_str(run.get("run_started_at"), "run_started_at")
        head_branch = _optional_str(run.get("head_branch"), "head_branch")
        return RunRef(
            run_id=str(_strict_int(run["id"], "id")),
            run_attempt=_strict_int(run["run_attempt"], "run_attempt"),
            event=_required_str(run["event"], "event"),
            ref=f"refs/heads/{head_branch}" if head_branch else "",
            sha=_required_str(run["head_sha"], "head_sha"),
            workflow_path=_required_str(run["path"], "path"),
            created_at=created_at,
            run_started_at=_parse_utc(started_raw),
            run_number=_strict_int(run["run_number"], "run_number"),
            status=_required_str(run["status"], "status"),
            conclusion=_optional_str(run.get("conclusion"), "conclusion"),
            head_branch=head_branch,
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise GithubEvidenceError("workflow run object has an unexpected shape") from exc


def _job_step_from(step: object) -> JobStep:
    if not isinstance(step, dict):
        raise TypeError("step")
    return JobStep(
        name=_required_str(step["name"], "step.name"),
        status=_required_str(step["status"], "step.status"),
        conclusion=_optional_str(step.get("conclusion"), "step.conclusion"),
        number=_strict_int(step["number"], "step.number"),
    )


class GithubEvidenceClient:
    def __init__(
        self,
        api_url: str,
        repository: str,
        token: str,
        *,
        opener: Callable = _DEFAULT_OPENER,
        page_limit: int = 10,
        request_timeout_s: float = 30.0,
        download_timeout_s: float = 60.0,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._repository = repository
        self._token = token
        self._opener = opener
        self._page_limit = page_limit
        # Stage 2B-2: optional bounded timeouts for the finalizer's
        # cancellation-window steps; the defaults are the historical
        # 30 s / 60 s, so every existing caller is unchanged.
        self._request_timeout_s = request_timeout_s
        self._download_timeout_s = download_timeout_s

    def __repr__(self) -> str:  # never leak the token
        return f"GithubEvidenceClient(api_url={self._api_url!r}, repository={self._repository!r})"

    # -- transport -----------------------------------------------------

    def _get_json(self, path: str) -> object:
        url = f"{self._api_url}{path}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._opener(request, timeout=self._request_timeout_s) as response:
                status = response.status
                body = response.read()
        except urllib.error.URLError as exc:
            raise GithubEvidenceError(f"GitHub REST request failed with a transport error") from exc
        if status != 200:
            raise GithubEvidenceError(f"GitHub REST request returned HTTP {status}")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise GithubEvidenceError("GitHub REST response was not valid JSON") from exc

    def _get_bytes(self, path: str) -> bytes:
        url = f"{self._api_url}{path}"
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._token}"}
        )
        try:
            with self._opener(request, timeout=self._download_timeout_s) as response:
                status = response.status
                body = response.read()
        except urllib.error.URLError as exc:
            raise GithubEvidenceError("GitHub artifact download failed with a transport error") from exc
        if status != 200:
            raise GithubEvidenceError(f"GitHub artifact download returned HTTP {status}")
        return body

    # -- run metadata ----------------------------------------------------

    def get_run_timing(self, run_id: str) -> tuple[datetime, datetime | None]:
        data = self._get_json(f"/repos/{self._repository}/actions/runs/{run_id}")
        created_at = _parse_utc(data.get("created_at"))
        if created_at is None:
            raise GithubEvidenceError("run object is missing created_at")
        run_started_at = _parse_utc(data.get("run_started_at"))
        return created_at, run_started_at

    def list_run_attempt_jobs(self, run_id: str, attempt: int) -> list[JobDetail]:
        """Every job of exactly ``run_id`` / ``attempt`` via the
        attempt-scoped jobs endpoint (dispatch
        q77-p5d-repair-stage2c1-implement-a). The attempt is request
        identity; no response-body ``run_attempt`` is required or read.
        Fails closed on a non-numeric run id, a non-positive attempt, a
        non-object response, a missing or non-list ``jobs``, a
        ``total_count`` that is not an integer equal to the number of
        entries returned, or any entry of unexpected shape."""
        results: list[JobDetail] = []
        for job in self._attempt_jobs_payload(run_id, attempt):
            try:
                results.append(_job_detail_from(job))
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise GithubEvidenceError("run attempt jobs listing entry has an unexpected shape") from exc
        return results

    def list_run_attempt_job_evidence(self, run_id: str, attempt: int) -> list[JobEvidence]:
        """The same attempt-scoped listing as ``list_run_attempt_jobs``,
        with each job's ``conclusion`` and ``steps`` (Stage 2C-B6-2: the
        replacement latch's prior-run proof). Same fail-closed rules; an
        absent ``steps`` key parses as empty, which the proof refuses."""
        results: list[JobEvidence] = []
        for job in self._attempt_jobs_payload(run_id, attempt):
            try:
                detail = _job_detail_from(job)
                steps_raw = job.get("steps")
                if steps_raw is not None and not isinstance(steps_raw, list):
                    raise TypeError("steps")
                results.append(
                    JobEvidence(
                        id=detail.id,
                        run_id=detail.run_id,
                        name=detail.name,
                        status=detail.status,
                        conclusion=_optional_str(job.get("conclusion"), "conclusion"),
                        steps=tuple(_job_step_from(step) for step in steps_raw or ()),
                    )
                )
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise GithubEvidenceError("run attempt jobs listing entry has an unexpected shape") from exc
        return results

    def _attempt_jobs_payload(self, run_id: str, attempt: int) -> list:
        if not isinstance(run_id, str) or not run_id.isdigit():
            raise GithubEvidenceError("run_id must be a non-empty decimal string")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise GithubEvidenceError("attempt must be a positive integer")
        data = self._get_json(
            f"/repos/{self._repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
        )
        if not isinstance(data, dict):
            raise GithubEvidenceError("run attempt jobs listing is not a JSON object")
        jobs = data.get("jobs")
        total = data.get("total_count")
        if not isinstance(jobs, list) or not isinstance(total, int) or isinstance(total, bool):
            raise GithubEvidenceError("run attempt jobs listing is missing jobs or total_count")
        if total != len(jobs):
            raise GithubEvidenceError("run attempt jobs listing is incomplete (total_count mismatch)")
        return jobs

    def get_main_head_sha(self) -> str:
        data = self._get_json(f"/repos/{self._repository}/git/ref/heads/main")
        obj = data.get("object") if isinstance(data, dict) else None
        sha = obj.get("sha") if isinstance(obj, dict) else None
        if not isinstance(sha, str) or not sha:
            raise GithubEvidenceError("git ref response is missing object.sha")
        return sha.lower()

    def list_workflow_runs(
        self, workflow_path: str, *, created_after: datetime, created_before: datetime
    ) -> list[RunRef]:
        # GitHub's workflow-runs listing takes the workflow file basename
        # or numeric id in its path segment; the caller-supplied
        # workflow_path is always ".github/workflows/<file>.yml".
        results: list[RunRef] = []
        page = 1
        while True:
            if page > self._page_limit:
                raise DiscoveryOverflow(f"more than {self._page_limit} pages of workflow runs")
            data = self._get_json(
                f"/repos/{self._repository}/actions/workflows/{workflow_path.split('/')[-1]}"
                f"/runs?per_page=100&page={page}"
            )
            runs = data.get("workflow_runs", []) if isinstance(data, dict) else []
            if not runs:
                break
            for run in runs:
                created_at = _parse_utc(run.get("created_at"))
                if created_at is None or not (created_after <= created_at <= created_before):
                    continue
                results.append(
                    RunRef(
                        run_id=str(run["id"]),
                        run_attempt=int(run["run_attempt"]),
                        event=run["event"],
                        ref=run["head_branch"] and f"refs/heads/{run['head_branch']}" or run.get("head_branch", ""),
                        sha=run["head_sha"],
                        workflow_path=run.get("path", workflow_path),
                        created_at=created_at,
                        run_started_at=_parse_utc(run.get("run_started_at")),
                    )
                )
            if len(runs) < 100:
                break
            page += 1
        return results

    # -- Stage 2C-B6-2: replacement-latch evidence ----------------------------

    def server_time_utc(self) -> datetime:
        """GitHub server time: the HTTP ``Date`` header of ``GET /rate_limit``
        (an endpoint that costs no rate limit). Strictly parsed; a missing or
        malformed header fails closed. Never falls back to a local clock."""
        request = urllib.request.Request(
            f"{self._api_url}/rate_limit",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._opener(request, timeout=self._request_timeout_s) as response:
                status = response.status
                headers = getattr(response, "headers", None)
                date_value = headers.get("Date") if headers is not None else None
                response.read()
        except urllib.error.URLError as exc:
            raise GithubEvidenceError("GitHub REST request failed with a transport error") from exc
        if status != 200:
            raise GithubEvidenceError(f"GitHub REST request returned HTTP {status}")
        return parse_http_date(date_value)

    def get_run(self, run_id: str) -> RunRef:
        """One workflow run with its ``run_number``, ``status``,
        ``conclusion`` and ``head_branch``. Fails closed on any shape fault."""
        if not isinstance(run_id, str) or not run_id.isdigit():
            raise GithubEvidenceError("run_id must be a non-empty decimal string")
        return _run_ref_from(self._get_json(f"/repos/{self._repository}/actions/runs/{run_id}"))

    def list_workflow_runs_counted(
        self, workflow_path: str, *, created_after: datetime, created_before: datetime
    ) -> list[RunRef]:
        """Every run of one workflow, with completeness proven: across all
        pages, the number of entries returned must equal the API's
        ``total_count`` (constant across pages) BEFORE the created-window
        filter is applied. Overflow, a mismatch or any malformed entry
        fails closed -- an incomplete listing is never read as absence."""
        entries: list[RunRef] = []
        total_count: int | None = None
        page = 1
        while True:
            if page > self._page_limit:
                raise DiscoveryOverflow(f"more than {self._page_limit} pages of workflow runs")
            data = self._get_json(
                f"/repos/{self._repository}/actions/workflows/{workflow_path.split('/')[-1]}"
                f"/runs?per_page=100&page={page}"
            )
            if not isinstance(data, dict):
                raise GithubEvidenceError("workflow run listing is not a JSON object")
            runs = data.get("workflow_runs")
            total = data.get("total_count")
            if not isinstance(runs, list) or isinstance(total, bool) or not isinstance(total, int):
                raise GithubEvidenceError("workflow run listing is missing workflow_runs or total_count")
            if total_count is None:
                total_count = total
            elif total != total_count:
                raise GithubEvidenceError("workflow run listing total_count changed during pagination")
            entries.extend(_run_ref_from(run) for run in runs)
            if len(runs) < 100:
                break
            page += 1
        if len(entries) != total_count:
            raise GithubEvidenceError("workflow run listing is incomplete (total_count mismatch)")
        return [run for run in entries if created_after <= run.created_at <= created_before]

    def get_commit(self, sha: str) -> CommitDetail:
        """Parents and changed files (with patches) of one commit. A file
        list of 300 or more entries may be paginated by GitHub and fails
        closed rather than being read as complete."""
        if not isinstance(sha, str) or not _HEX40.fullmatch(sha):
            raise GithubEvidenceError("sha must be exactly 40 lowercase hexadecimal characters")
        data = self._get_json(f"/repos/{self._repository}/commits/{sha}")
        try:
            if not isinstance(data, dict):
                raise TypeError("commit")
            parents = data["parents"]
            files = data["files"]
            if not isinstance(parents, list) or not isinstance(files, list):
                raise TypeError("parents/files")
            if len(files) >= 300:
                raise ValueError("files")
            return CommitDetail(
                sha=_required_str(data["sha"], "sha").lower(),
                parents=tuple(_required_str(parent["sha"], "parent.sha").lower() for parent in parents),
                files=tuple(
                    CommitFile(
                        filename=_required_str(item["filename"], "filename"),
                        status=_required_str(item["status"], "status"),
                        additions=_strict_int(item["additions"], "additions"),
                        deletions=_strict_int(item["deletions"], "deletions"),
                        patch=_optional_str(item.get("patch"), "patch"),
                    )
                    for item in files
                ),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise GithubEvidenceError("commit response has an unexpected shape") from exc

    def list_push_activity(self, ref: str) -> list[PushActivity]:
        """The most recent (up to 100) ``push`` activity entries for
        ``ref``: GitHub's server-side record of each ref update."""
        query = urllib.parse.urlencode({"ref": ref, "activity_type": "push", "per_page": 100})
        data = self._get_json(f"/repos/{self._repository}/activity?{query}")
        try:
            if not isinstance(data, list):
                raise TypeError("activity")
            return [
                PushActivity(
                    before=_required_str(item["before"], "before").lower(),
                    after=_required_str(item["after"], "after").lower(),
                    ref=_required_str(item["ref"], "ref"),
                    activity_type=_required_str(item["activity_type"], "activity_type"),
                    timestamp=_parse_aware_utc(item["timestamp"], "timestamp"),
                )
                for item in data
            ]
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise GithubEvidenceError("repository activity response has an unexpected shape") from exc

    # -- artifact discovery -----------------------------------------------

    def list_artifacts(self, prefix: str) -> list[ArtifactRef]:
        results: list[ArtifactRef] = []
        page = 1
        while True:
            if page > self._page_limit:
                raise DiscoveryOverflow(f"more than {self._page_limit} pages of artifacts")
            data = self._get_json(
                f"/repos/{self._repository}/actions/artifacts?per_page=100&page={page}"
            )
            artifacts = data.get("artifacts", []) if isinstance(data, dict) else []
            if not artifacts:
                break
            for artifact in artifacts:
                if artifact.get("expired"):
                    continue
                name = artifact.get("name", "")
                if not name.startswith(prefix):
                    continue
                results.append(
                    ArtifactRef(
                        id=int(artifact["id"]),
                        name=name,
                        workflow_run_id=str(artifact["workflow_run"]["id"]),
                    )
                )
            if len(artifacts) < 100:
                break
            page += 1
        return results

    def list_artifacts_for_run(self, run_id: str) -> list[ArtifactRef]:
        data = self._get_json(f"/repos/{self._repository}/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = data.get("artifacts", []) if isinstance(data, dict) else []
        return [
            ArtifactRef(id=int(a["id"]), name=a["name"], workflow_run_id=str(a["workflow_run"]["id"]))
            for a in artifacts
            if not a.get("expired")
        ]

    def list_run_artifacts_named(self, run_id: str, name: str) -> list[ArtifactDetail]:
        """Every artifact of ``run_id`` whose name is exactly ``name``,
        expired entries included (dispatch
        q77-p5d-repair-stage2b2-implement-a). Fails closed unless the
        response's ``total_count`` is an integer equal to the number of
        entries returned, so a truncated or malformed listing can never
        be read as absence."""
        query = urllib.parse.urlencode({"name": name, "per_page": 100})
        data = self._get_json(f"/repos/{self._repository}/actions/runs/{run_id}/artifacts?{query}")
        if not isinstance(data, dict):
            raise GithubEvidenceError("run artifact listing is not a JSON object")
        artifacts = data.get("artifacts")
        total = data.get("total_count")
        if not isinstance(artifacts, list) or not isinstance(total, int) or isinstance(total, bool):
            raise GithubEvidenceError("run artifact listing is missing artifacts or total_count")
        if total != len(artifacts):
            raise GithubEvidenceError("run artifact listing is incomplete (total_count mismatch)")
        results: list[ArtifactDetail] = []
        for artifact in artifacts:
            try:
                workflow_run = artifact.get("workflow_run") or {}
                size = artifact.get("size_in_bytes")
                results.append(
                    ArtifactDetail(
                        id=int(artifact["id"]),
                        name=str(artifact["name"]),
                        workflow_run_id=str(workflow_run.get("id", "")),
                        expired=bool(artifact.get("expired")),
                        digest=artifact.get("digest") if isinstance(artifact.get("digest"), str) else None,
                        size_in_bytes=size if isinstance(size, int) else None,
                    )
                )
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise GithubEvidenceError("run artifact listing entry has an unexpected shape") from exc
        return [r for r in results if r.name == name]

    # -- artifact download -------------------------------------------------

    def download_artifact(self, ref: ArtifactRef, dest_trusted_root: Path, dest_dir: Path) -> Path:
        body = self._get_bytes(f"/repos/{self._repository}/actions/artifacts/{ref.id}/zip")
        root = create_fresh_root(dest_trusted_root, dest_dir)
        _safe_extract_zip(body, root)
        return root


def _safe_extract_zip(zip_bytes: bytes, dest_root: Path) -> None:
    import io

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        infos = archive.infolist()
        if len(infos) > _MAX_ARTIFACT_ENTRIES:
            raise ArtifactUnsafe(f"artifact contains more than {_MAX_ARTIFACT_ENTRIES} entries")
        total_uncompressed = sum(info.file_size for info in infos)
        if total_uncompressed > _MAX_ARTIFACT_UNCOMPRESSED_BYTES:
            raise ArtifactUnsafe("artifact exceeds the bounded total uncompressed size")
        for info in infos:
            name = info.filename
            if name.startswith("/") or "\\" in name:
                raise ArtifactUnsafe(f"unsafe artifact entry path: {name}")
            parts = name.split("/")
            if any(part in ("", "..") for part in parts if part):
                raise ArtifactUnsafe(f"unsafe artifact entry path: {name}")
            # Unix symlink bit in the external_attr high 16 bits (0o120000)
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and (mode & 0o170000) == 0o120000:
                raise ArtifactUnsafe(f"artifact entry is a symlink: {name}")
        for info in infos:
            if info.is_dir():
                continue
            target = assert_trusted_path(dest_root, dest_root / info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as handle:
                handle.write(source.read())

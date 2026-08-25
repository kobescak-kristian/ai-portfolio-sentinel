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
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .bundle import assert_trusted_path, create_fresh_root

_MAX_ARTIFACT_ENTRIES = 64
_MAX_ARTIFACT_UNCOMPRESSED_BYTES = 64 * 1024 * 1024  # 64 MiB — generous, still bounded


class _NoAuthOnRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirects exactly like ``urllib.request``'s stdlib default,
    except the ``Authorization`` header is never carried over to the
    redirected request (dispatch
    q77-p5d-premarker-artifact-redirect-repair-a).

    GitHub's artifact-download endpoint
    (``GET /repos/{repo}/actions/artifacts/{id}/zip``) responds with a
    302 to a pre-signed, time-limited storage URL that authenticates
    via its own query-string signature and rejects an unexpected
    ``Authorization`` header with HTTP 401. The stdlib default
    ``HTTPRedirectHandler.redirect_request`` copies every original
    header except ``Content-Length``/``Content-Type`` onto the
    redirected request, so ``Authorization`` is forwarded by default
    (confirmed by reading ``inspect.getsource`` of that method,
    2026-08) — this override reuses that exact logic via ``super()``
    and strips only the one header that must never leave the original
    host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            new_req.remove_header("Authorization")
        return new_req


def _redirect_safe_default_opener() -> Callable:
    """The default network opener for ``GithubEvidenceClient``: behaves
    exactly like ``urllib.request.urlopen`` for every existing call
    site, except a redirect never carries the bearer token to its
    destination (see ``_NoAuthOnRedirectHandler``). Built once at
    import time — ``OpenerDirector.open`` has the same
    ``(request, timeout=...)`` call signature ``urlopen`` does, so
    every test that injects its own fake ``opener`` (replacing this
    default entirely) is completely unaffected."""
    return urllib.request.build_opener(_NoAuthOnRedirectHandler).open


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
class RunRef:
    run_id: str
    run_attempt: int
    event: str
    ref: str
    sha: str
    workflow_path: str
    created_at: datetime
    run_started_at: datetime | None


def _parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GithubEvidenceClient:
    def __init__(
        self,
        api_url: str,
        repository: str,
        token: str,
        *,
        opener: Callable = _DEFAULT_OPENER,
        page_limit: int = 10,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._repository = repository
        self._token = token
        self._opener = opener
        self._page_limit = page_limit

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
            with self._opener(request, timeout=30.0) as response:
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
            with self._opener(request, timeout=60.0) as response:
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

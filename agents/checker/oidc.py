"""GitHub Actions OIDC acquisition and Anthropic WIF identity-token-file
lifecycle for Phase-5 provider-capable workflows (P5-B Part 3/3,
revision-c seams 1 and 4).

Seam 4 (credential exclusion from the Agent-SDK child): the Claude
Agent SDK spawns the CLI as a subprocess and merges the *entire*
parent process environment into it (see ``agents/checker/auth.py``'s
own module docstring for the confirmed mechanism). ``pop_github_token``
and ``capture_actions_request_source`` therefore both *remove* the
credential they read from the process environment in the same call
that reads it — never leaving a window where the value sits in
``os.environ`` for a later-spawned SDK child to inherit.

Seam 1 (refreshable token file): the GitHub-issued identity JWT expires
roughly five minutes after issuance, but a scheduled (20-minute) or
gate (30-minute) job outlives that. ``TokenFileRefresher`` re-fetches
a fresh JWT and atomically reinstalls ``ANTHROPIC_IDENTITY_TOKEN_FILE``
on a fixed interval, using only the request credentials captured in
parent memory at S09 — never re-reading them from the environment,
because they no longer live there.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_DEFAULT_AUDIENCE = "https://api.anthropic.com"
_DEFAULT_REFRESH_INTERVAL_SECONDS = 180  # safely below GitHub's ~5-minute JWT lifetime


class OidcAcquisitionError(RuntimeError):
    """A required GitHub Actions OIDC environment variable was missing,
    the token request failed, or the token-file install target was
    unsafe. Never carries a token or request-credential value."""


class OidcRefreshFault(RuntimeError):
    """The parent-only token refresher failed a refresh attempt. Once
    raised, no NEW model invocation may start; an invocation already
    in flight is allowed to terminate honestly."""


# ---------------------------------------------------------------------------
# GITHUB_TOKEN — used only by GithubEvidenceClient, never by the provider
# ---------------------------------------------------------------------------


def pop_github_token(env=None) -> str:
    """Read and REMOVE ``GITHUB_TOKEN`` from ``env`` (defaults to the
    real process environment) in one call. Must run at entrypoint
    startup, before any Agent-SDK/provider object is constructed."""
    source = env if env is not None else os.environ
    token = source.get("GITHUB_TOKEN")
    if not token:
        raise OidcAcquisitionError("GITHUB_TOKEN is not set")
    del source["GITHUB_TOKEN"]
    return token


# ---------------------------------------------------------------------------
# OIDC request-credential capture (S09) — parent-memory only from here on
# ---------------------------------------------------------------------------


class OidcRequestSource:
    """Private, parent-only holder of GitHub's job-scoped OIDC
    token-request credentials. Never exposes them through ``repr`` or
    any public attribute."""

    __slots__ = ("_request_url", "_request_token")

    def __init__(self, request_url: str, request_token: str) -> None:
        self._request_url = request_url
        self._request_token = request_token

    def __repr__(self) -> str:  # never leak either value
        return "OidcRequestSource(<redacted>)"


def capture_actions_request_source(env=None) -> OidcRequestSource:
    """Read ``ACTIONS_ID_TOKEN_REQUEST_URL`` and
    ``ACTIONS_ID_TOKEN_REQUEST_TOKEN`` and REMOVE both from ``env`` in
    the same call. From this point neither name is present in the
    process environment, so no later-spawned Agent-SDK child can ever
    inherit them — the parent keeps the only reference, inside this
    object."""
    source = env if env is not None else os.environ
    url = source.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    token = source.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not token:
        raise OidcAcquisitionError(
            "ACTIONS_ID_TOKEN_REQUEST_URL and ACTIONS_ID_TOKEN_REQUEST_TOKEN must both be set"
        )
    if "ACTIONS_ID_TOKEN_REQUEST_URL" in source:
        del source["ACTIONS_ID_TOKEN_REQUEST_URL"]
    if "ACTIONS_ID_TOKEN_REQUEST_TOKEN" in source:
        del source["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]
    return OidcRequestSource(request_url=url, request_token=token)


def fetch_github_oidc_token(
    source: OidcRequestSource,
    *,
    audience: str = _DEFAULT_AUDIENCE,
    opener: Callable = urllib.request.urlopen,
) -> str:
    """Exchange the captured request credentials for one fresh GitHub
    identity JWT. Uses ONLY the parent-memory ``source`` — never reads
    the environment. Error text carries an HTTP status only, never the
    request token or the response body."""
    separator = "&" if "?" in source._request_url else "?"
    url = f"{source._request_url}{separator}audience={audience}"
    request = urllib.request.Request(url, headers={"Authorization": f"bearer {source._request_token}"})
    try:
        with opener(request, timeout=30.0) as response:
            status = response.status
            body = response.read()
    except urllib.error.URLError as exc:
        raise OidcAcquisitionError("GitHub OIDC token request failed with a transport error") from exc
    if status != 200:
        raise OidcAcquisitionError(f"GitHub OIDC token request returned HTTP {status}")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise OidcAcquisitionError("GitHub OIDC token response was not valid JSON") from exc
    value = data.get("value") if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        raise OidcAcquisitionError("GitHub OIDC token response is missing 'value'")
    return value


# ---------------------------------------------------------------------------
# Identity-token-file lifecycle
# ---------------------------------------------------------------------------


def write_placeholder_token_file(env) -> Path:
    """S07: create an empty, 0600, regular file at
    ``ANTHROPIC_IDENTITY_TOKEN_FILE`` — required because
    ``auth._assert_identity_token_file_safe`` demands an existing
    regular (non-symlink) file before any token exists. Refuses if the
    path already exists or is a symlink."""
    path = Path(env["ANTHROPIC_IDENTITY_TOKEN_FILE"])
    if path.exists() or path.is_symlink():
        raise OidcAcquisitionError("refusing to create token-file placeholder: path already exists")
    path.write_text("", encoding="ascii")
    _chmod_best_effort(path)
    return path


def install_identity_token_file(env, token: str) -> Path:
    """Atomic, symlink-safe install: write a fresh 0600 temp sibling
    under the same directory, then ``os.replace`` it into the final
    path. A concurrent reader (the refresher's own re-installs, or the
    SDK re-reading the file on token refresh) can therefore only ever
    observe a complete prior JWT or a complete new one — never a
    partially-written file."""
    path = Path(env["ANTHROPIC_IDENTITY_TOKEN_FILE"])
    if path.is_symlink():
        raise OidcAcquisitionError("refusing to install identity token: target path is a symlink")
    tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    tmp.write_text(token, encoding="ascii")
    _chmod_best_effort(tmp)
    os.replace(tmp, path)
    return path


def scrub_identity_token_file(env) -> None:
    """Overwrite-with-zeros then unlink. Exception-safe and idempotent
    — a missing file, or a missing path variable, is a no-op, never an
    error. Runs unconditionally in every provider-capable entrypoint's
    ``finally`` block."""
    path_value = env.get("ANTHROPIC_IDENTITY_TOKEN_FILE") if hasattr(env, "get") else None
    if not path_value:
        return
    path = Path(path_value)
    if not path.exists():
        return
    try:
        size = path.stat().st_size
        if size:
            path.write_bytes(b"\x00" * size)
    except OSError:
        pass
    try:
        path.unlink()
    except OSError:
        pass


def _chmod_best_effort(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # platforms without POSIX chmod semantics (e.g. plain Windows FS)


# ---------------------------------------------------------------------------
# Parent-only refresher (seam 1)
# ---------------------------------------------------------------------------


@dataclass
class TokenFileRefresher:
    """Runs entirely in the parent process. Uses ONLY the captured
    ``OidcRequestSource`` — never re-reads the environment for request
    credentials, since they no longer live there after S09. ``tick()``
    is public and synchronous so tests exercise multiple refresh
    cycles deterministically without a real background thread; ``start``
    drives it from a daemon thread in production."""

    source: OidcRequestSource
    env: object
    interval_seconds: float = _DEFAULT_REFRESH_INTERVAL_SECONDS
    fetch_token: Callable[[OidcRequestSource], str] = field(default=fetch_github_oidc_token)
    install: Callable[[object, str], Path] = field(default=install_identity_token_file)
    _fault: Exception | None = field(default=None, init=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: "threading.Thread | None" = field(default=None, init=False)

    def tick(self) -> None:
        try:
            token = self.fetch_token(self.source)
            self.install(self.env, token)
        except Exception as exc:  # noqa: BLE001 - any refresh failure is a health fault
            self._fault = exc

    def assert_healthy(self) -> None:
        if self._fault is not None:
            raise OidcRefreshFault(
                f"token-file refresh failed: {type(self._fault).__name__}"
            ) from self._fault

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            self.tick()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None


@dataclass
class OidcSession:
    """The provider-capable path's single credential handle. ``source``
    and ``first_jwt`` are populated by ``acquire_oidc`` (S09);
    ``install_and_start`` (S10) installs the first JWT and starts the
    refresher; ``shutdown`` (S18) always runs in ``finally``."""

    source: OidcRequestSource
    first_jwt: str
    refresher: "TokenFileRefresher | None" = field(default=None, init=False)

    def assert_healthy(self) -> None:
        if self.refresher is not None:
            self.refresher.assert_healthy()

    def install_and_start(self, env, *, interval_seconds: float = _DEFAULT_REFRESH_INTERVAL_SECONDS) -> None:
        install_identity_token_file(env, self.first_jwt)
        self.refresher = TokenFileRefresher(
            source=self.source, env=env, interval_seconds=interval_seconds
        )
        self.refresher.start()

    def shutdown(self, env) -> None:
        if self.refresher is not None:
            self.refresher.stop()
        scrub_identity_token_file(env)


def acquire_oidc(env=None, *, audience: str = _DEFAULT_AUDIENCE) -> OidcSession:
    """S09: capture the request source (removing both env names) and
    synchronously fetch the first JWT. Does NOT install the file or
    start the refresher — that is ``OidcSession.install_and_start``
    (S10), a distinct step so the frozen preflight order stays
    mechanically visible."""
    source = capture_actions_request_source(env)
    first_jwt = fetch_github_oidc_token(source, audience=audience)
    return OidcSession(source=source, first_jwt=first_jwt)


# ---------------------------------------------------------------------------
# Health-gated provider invocation wrapper
# ---------------------------------------------------------------------------


def health_gated(query_fn: Callable, session: OidcSession) -> Callable:
    """Wrap a ``CagedCheckerStub.query_fn``-shaped callable so
    ``session.assert_healthy()`` runs immediately before EVERY actual
    SDK invocation — not just the first. A refresh fault therefore
    prevents any NEW model invocation from starting; an invocation
    already in flight when the fault occurs is unaffected by this
    wrapper and terminates through the existing ADR-0008 failure path.
    Preserves whether the wrapped callable is a coroutine function, so
    ``CagedCheckerStub._invoke``'s ``inspect.iscoroutinefunction``
    dispatch is unaffected."""
    import functools
    import inspect

    if inspect.iscoroutinefunction(query_fn):

        @functools.wraps(query_fn)
        async def _wrapped_async(check_class, reservation, state, user_prompt, model=None):
            session.assert_healthy()
            return await query_fn(check_class, reservation, state, user_prompt, model)

        return _wrapped_async

    @functools.wraps(query_fn)
    def _wrapped_sync(check_class, reservation, state, user_prompt, model=None):
        session.assert_healthy()
        return query_fn(check_class, reservation, state, user_prompt, model)

    return _wrapped_sync

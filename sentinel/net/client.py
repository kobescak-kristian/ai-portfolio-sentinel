"""HTTP client seam: stdlib-only production implementation, a fake
for tests, and thin retry/budget wrappers.

Stdlib ``urllib.request`` is the only HTTP dependency — the whole
network surface is unauthenticated GET against a couple of hosts, at
most a few hundred requests per run, no streaming, no auth flows.
Adding ``requests``/``httpx`` would pull in several new pinned
transitive dependencies for ergonomics this design abstracts behind
``HttpClient`` anyway.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Literal, Mapping, NamedTuple, Protocol, Union

from sentinel.ids import Sleeper


class HttpResponse(NamedTuple):
    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str


@dataclass
class HttpError(Exception):
    kind: Literal["dns", "timeout", "connection", "protocol"]
    message: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.message}"


class RequestBudgetExceeded(RuntimeError):
    """The per-run HTTP request budget was exceeded — fails the
    calling task, never the whole run."""


class RateLimited(RuntimeError):
    """A 403 carrying x-ratelimit-remaining: 0 — never retried."""


class HttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        method: str = "GET",
    ) -> HttpResponse: ...


class UrllibHttpClient:
    """Single-attempt production HTTP client. Non-2xx/3xx responses
    are returned as an HttpResponse (never raised) so callers can
    classify status codes themselves; only transport-level failures
    raise HttpError."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        method: str = "GET",
    ) -> HttpResponse:
        request = urllib.request.Request(url, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                body = resp.read()
                return HttpResponse(
                    status=resp.status,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    body=body,
                    url=resp.url,
                )
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp is not None else b""
            return HttpResponse(
                status=exc.code,
                headers={k.lower(): v for k, v in (exc.headers or {}).items()},
                body=body,
                url=url,
            )
        except TimeoutError as exc:
            raise HttpError("timeout", str(exc)) from exc
        except urllib.error.URLError as exc:
            reason = str(exc.reason)
            kind: Literal["dns", "timeout", "connection"] = "connection"
            if "timed out" in reason.lower():
                kind = "timeout"
            elif "name or service not known" in reason.lower() or "getaddrinfo" in reason.lower():
                kind = "dns"
            raise HttpError(kind, reason) from exc
        except OSError as exc:
            raise HttpError("connection", str(exc)) from exc


@dataclass
class RetryingHttpClient:
    """Wraps another HttpClient with a fixed, deterministic retry
    budget. Sleeps go through the injected Sleeper so tests run at
    full speed."""

    inner: HttpClient
    sleeper: Sleeper
    max_attempts: int
    backoff_seconds: tuple[float, ...]
    retry_statuses: frozenset[int]

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        method: str = "GET",
    ) -> HttpResponse:
        last_error: HttpError | None = None
        last_response: HttpResponse | None = None
        for attempt in range(self.max_attempts):
            try:
                response = self.inner.get(url, headers=headers, timeout=timeout, method=method)
            except HttpError as exc:
                last_error = exc
                if attempt < self.max_attempts - 1:
                    self.sleeper.sleep(self._backoff_for(attempt))
                    continue
                raise
            if response.status in self.retry_statuses and attempt < self.max_attempts - 1:
                last_response = response
                self.sleeper.sleep(self._backoff_for(attempt))
                continue
            return response
        if last_response is not None:
            return last_response
        assert last_error is not None
        raise last_error

    def _backoff_for(self, attempt: int) -> float:
        if not self.backoff_seconds:
            return 0.0
        index = min(attempt, len(self.backoff_seconds) - 1)
        return self.backoff_seconds[index]


@dataclass
class BudgetedHttpClient:
    """Wraps another HttpClient with a per-run request-count ceiling."""

    inner: HttpClient
    max_requests: int
    count: int = field(default=0)

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        method: str = "GET",
    ) -> HttpResponse:
        if self.count >= self.max_requests:
            raise RequestBudgetExceeded(
                f"exceeded {self.max_requests} HTTP requests in this run"
            )
        self.count += 1
        return self.inner.get(url, headers=headers, timeout=timeout, method=method)


@dataclass
class FakeHttpClient:
    """Test double. ``responses`` maps an exact URL to either a
    scripted HttpResponse or an HttpError to raise. Every call is
    recorded in ``calls`` for assertions (e.g. no Authorization
    header was ever sent)."""

    responses: Mapping[str, Union[HttpResponse, HttpError]]
    calls: list[tuple[str, str, Mapping[str, str], float]] = field(default_factory=list)

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        method: str = "GET",
    ) -> HttpResponse:
        self.calls.append((method, url, dict(headers), timeout))
        result = self.responses.get(url)
        if result is None:
            return HttpResponse(status=404, headers={}, body=b"", url=url)
        if isinstance(result, HttpError):
            raise result
        return result

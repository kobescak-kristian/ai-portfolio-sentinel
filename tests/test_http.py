"""HTTP retry/budget/link-status behavior — FakeHttpClient only, no
live network."""

from __future__ import annotations

import pytest

from sentinel.ids import NoOpSleeper
from sentinel.net.client import (
    BudgetedHttpClient,
    FakeHttpClient,
    HttpError,
    RequestBudgetExceeded,
    RetryingHttpClient,
    HttpResponse,
)
from sentinel.net.links import HttpLinkResolver, LinkTruthResolver, StaticLinkResolver

URL = "https://example.invalid/x"


def _retrying(responses, max_attempts=3):
    fake = FakeHttpClient(responses=responses)
    sleeper = NoOpSleeper()
    retrying = RetryingHttpClient(
        inner=fake,
        sleeper=sleeper,
        max_attempts=max_attempts,
        backoff_seconds=(1.0, 2.0),
        retry_statuses=frozenset({429, 500, 502, 503, 504}),
    )
    return retrying, fake, sleeper


def test_confirmed_dead_maps_to_dead_status():
    fake = FakeHttpClient(responses={URL: HttpResponse(404, {}, b"", URL)})
    resolver = HttpLinkResolver(http=fake, user_agent="ua", timeout=5.0)
    assert resolver.resolve(URL) == "dead"


def test_confirmed_live_maps_to_live_status():
    fake = FakeHttpClient(responses={URL: HttpResponse(200, {}, b"", URL)})
    resolver = HttpLinkResolver(http=fake, user_agent="ua", timeout=5.0)
    assert resolver.resolve(URL) == "live"


@pytest.mark.parametrize("status", [403, 429, 500, 502, 503, 999])
def test_never_confirmed_maps_to_unknown_not_dead(status):
    fake = FakeHttpClient(responses={URL: HttpResponse(status, {}, b"", URL)})
    resolver = HttpLinkResolver(http=fake, user_agent="ua", timeout=5.0)
    assert resolver.resolve(URL) == "unknown"


def test_timeout_is_unknown_not_dead():
    fake = FakeHttpClient(responses={URL: HttpError("timeout", "boom")})
    resolver = HttpLinkResolver(http=fake, user_agent="ua", timeout=5.0)
    assert resolver.resolve(URL) == "unknown"


def test_dns_failure_is_unknown_not_dead():
    """C3: DNS errors are unknown, never dead — a rare but explicit
    closed-set requirement."""
    fake = FakeHttpClient(responses={URL: HttpError("dns", "no such host")})
    resolver = HttpLinkResolver(http=fake, user_agent="ua", timeout=5.0)
    assert resolver.resolve(URL) == "unknown"


def test_head_rejected_falls_back_to_ranged_get():
    fake = FakeHttpClient(responses={})
    fake.responses = {URL: HttpResponse(405, {}, b"", URL)}

    class TwoStepFake:
        def __init__(self):
            self.calls = []

        def get(self, url, *, headers, timeout, method="GET"):
            self.calls.append((method, url, dict(headers), timeout))
            if method == "HEAD":
                return HttpResponse(405, {}, b"", url)
            return HttpResponse(200, {}, b"", url)

    two_step = TwoStepFake()
    resolver = HttpLinkResolver(http=two_step, user_agent="ua", timeout=5.0)
    assert resolver.resolve(URL) == "live"
    assert [c[0] for c in two_step.calls] == ["HEAD", "GET"]
    assert two_step.calls[1][2].get("Range") == "bytes=0-0"


def test_link_truth_resolver_never_touches_network(tmp_path):
    path = tmp_path / "link_truth.jsonl"
    path.write_text(
        '{"line":1,"path":"README.md","snapshot":"s","status":"dead","url":"https://dead.example.invalid"}\n',
        encoding="utf-8",
    )
    resolver = LinkTruthResolver.from_file(path)
    assert resolver.resolve("https://dead.example.invalid") == "dead"
    assert resolver.resolve("https://not-in-map.example.invalid") == "unknown"


def test_static_link_resolver_defaults_to_unknown():
    resolver = StaticLinkResolver(mapping={"https://a": "live"})
    assert resolver.resolve("https://a") == "live"
    assert resolver.resolve("https://b") == "unknown"


def test_retry_exhausts_then_returns_last_response():
    retrying, fake, sleeper = _retrying({URL: HttpResponse(503, {}, b"", URL)})
    response = retrying.get(URL, headers={}, timeout=5.0)
    assert response.status == 503
    assert len(fake.calls) == 3  # max_attempts
    assert len(sleeper.calls) == 2  # slept between attempts, not after the last


def test_retry_succeeds_after_transient_error():
    call_count = {"n": 0}

    class FlakyThenOk:
        def get(self, url, *, headers, timeout, method="GET"):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise HttpError("connection", "reset")
            return HttpResponse(200, {}, b"ok", url)

    sleeper = NoOpSleeper()
    retrying = RetryingHttpClient(
        inner=FlakyThenOk(), sleeper=sleeper, max_attempts=3, backoff_seconds=(1.0,), retry_statuses=frozenset()
    )
    response = retrying.get(URL, headers={}, timeout=5.0)
    assert response.status == 200
    assert call_count["n"] == 2


def test_retry_timeout_is_passed_through_every_attempt():
    fake = FakeHttpClient(responses={URL: HttpResponse(500, {}, b"", URL)})
    retrying = RetryingHttpClient(
        inner=fake, sleeper=NoOpSleeper(), max_attempts=2, backoff_seconds=(0.0,),
        retry_statuses=frozenset({500}),
    )
    retrying.get(URL, headers={}, timeout=7.5)
    for _method, _url, _headers, timeout in fake.calls:
        assert timeout == 7.5


def test_budgeted_client_raises_after_max_requests():
    fake = FakeHttpClient(responses={URL: HttpResponse(200, {}, b"", URL)})
    budgeted = BudgetedHttpClient(inner=fake, max_requests=2)
    budgeted.get(URL, headers={}, timeout=5.0)
    budgeted.get(URL, headers={}, timeout=5.0)
    with pytest.raises(RequestBudgetExceeded):
        budgeted.get(URL, headers={}, timeout=5.0)


def test_fake_http_client_records_calls_for_assertions():
    fake = FakeHttpClient(responses={})
    fake.get(URL, headers={"User-Agent": "ua"}, timeout=5.0)
    assert fake.calls == [("GET", URL, {"User-Agent": "ua"}, 5.0)]

"""Tests for agents/checker/oidc.py (P5-B Part 3/3, revision-c seams 1 and 4)."""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from agents.checker import oidc


class _FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_opener(status=200, body=b'{"value":"fresh-jwt"}', capture: list | None = None):
    def opener(request, timeout=None):
        if capture is not None:
            capture.append(request.full_url)
        return _FakeResponse(status, body)

    return opener


def test_pop_github_token_removes_from_env():
    env = {"GITHUB_TOKEN": "secret-token", "OTHER": "x"}
    token = oidc.pop_github_token(env)
    assert token == "secret-token"
    assert "GITHUB_TOKEN" not in env
    assert env["OTHER"] == "x"


def test_pop_github_token_raises_when_absent():
    with pytest.raises(oidc.OidcAcquisitionError):
        oidc.pop_github_token({})


def test_capture_actions_request_source_removes_both_vars():
    env = {
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example/token",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "req-tok",
    }
    source = oidc.capture_actions_request_source(env)
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" not in env
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in env
    assert "req-tok" not in repr(source)
    assert "example" not in repr(source)


def test_capture_actions_request_source_raises_when_missing():
    with pytest.raises(oidc.OidcAcquisitionError):
        oidc.capture_actions_request_source({"ACTIONS_ID_TOKEN_REQUEST_URL": "x"})


def test_fetch_github_oidc_token_uses_audience_and_bearer():
    env = {
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example/token?foo=bar",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "req-tok",
    }
    source = oidc.capture_actions_request_source(env)
    urls: list[str] = []
    captured_auth: list[str] = []

    def opener(request, timeout=None):
        urls.append(request.full_url)
        captured_auth.append(request.get_header("Authorization"))
        return _FakeResponse(200, b'{"value":"jwt-abc"}')

    token = oidc.fetch_github_oidc_token(source, opener=opener)
    assert token == "jwt-abc"
    assert "audience=https://api.anthropic.com" in urls[0]
    assert captured_auth[0] == "bearer req-tok"


def test_fetch_github_oidc_token_error_never_carries_token(monkeypatch):
    env = {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://example/token", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "secret-req"}
    source = oidc.capture_actions_request_source(env)

    def opener(request, timeout=None):
        return _FakeResponse(500, b"")

    with pytest.raises(oidc.OidcAcquisitionError) as excinfo:
        oidc.fetch_github_oidc_token(source, opener=opener)
    assert "secret-req" not in str(excinfo.value)


def test_install_identity_token_file_atomic_and_0600(tmp_path):
    target = tmp_path / "token"
    env = {"ANTHROPIC_IDENTITY_TOKEN_FILE": str(target)}
    oidc.install_identity_token_file(env, "jwt-1")
    assert target.read_text(encoding="ascii") == "jwt-1"
    # no leftover temp files
    assert list(tmp_path.glob("*.tmp-*")) == []
    oidc.install_identity_token_file(env, "jwt-2")
    assert target.read_text(encoding="ascii") == "jwt-2"


def test_install_identity_token_file_refuses_symlink(tmp_path):
    real = tmp_path / "real-token"
    real.write_text("x", encoding="ascii")
    link = tmp_path / "link-token"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires elevated privilege on this platform")
    env = {"ANTHROPIC_IDENTITY_TOKEN_FILE": str(link)}
    with pytest.raises(oidc.OidcAcquisitionError):
        oidc.install_identity_token_file(env, "jwt")


def test_write_placeholder_token_file_creates_empty_regular_file(tmp_path):
    target = tmp_path / "placeholder"
    env = {"ANTHROPIC_IDENTITY_TOKEN_FILE": str(target)}
    oidc.write_placeholder_token_file(env)
    assert target.is_file()
    assert target.read_text(encoding="ascii") == ""


def test_write_placeholder_refuses_if_path_already_exists(tmp_path):
    target = tmp_path / "placeholder"
    target.write_text("already here", encoding="ascii")
    env = {"ANTHROPIC_IDENTITY_TOKEN_FILE": str(target)}
    with pytest.raises(oidc.OidcAcquisitionError):
        oidc.write_placeholder_token_file(env)


def test_scrub_identity_token_file_overwrites_and_unlinks(tmp_path):
    target = tmp_path / "token"
    target.write_text("secret-jwt-value", encoding="ascii")
    env = {"ANTHROPIC_IDENTITY_TOKEN_FILE": str(target)}
    oidc.scrub_identity_token_file(env)
    assert not target.exists()


def test_scrub_identity_token_file_is_idempotent(tmp_path):
    target = tmp_path / "token"
    env = {"ANTHROPIC_IDENTITY_TOKEN_FILE": str(target)}
    oidc.scrub_identity_token_file(env)  # missing file: no-op
    oidc.scrub_identity_token_file({})  # missing var: no-op


def test_token_file_refresher_multiple_ticks_atomically_replace(tmp_path):
    target = tmp_path / "token"
    env = {"ANTHROPIC_IDENTITY_TOKEN_FILE": str(target)}
    fake_source = oidc.OidcRequestSource(request_url="https://x/token", request_token="req")

    calls = {"n": 0}

    def fetch(source):
        calls["n"] += 1
        return f"jwt-{calls['n']}"

    refresher = oidc.TokenFileRefresher(source=fake_source, env=env, fetch_token=fetch)
    refresher.tick()
    assert target.read_text(encoding="ascii") == "jwt-1"
    refresher.tick()
    assert target.read_text(encoding="ascii") == "jwt-2"
    refresher.assert_healthy()  # no fault
    refresher.stop()  # never started a real thread; must not raise


def test_token_file_refresher_fault_prevents_health_check(tmp_path):
    target = tmp_path / "token"
    env = {"ANTHROPIC_IDENTITY_TOKEN_FILE": str(target)}
    fake_source = oidc.OidcRequestSource(request_url="https://x/token", request_token="req")

    def failing_fetch(source):
        raise RuntimeError("network down")

    refresher = oidc.TokenFileRefresher(source=fake_source, env=env, fetch_token=failing_fetch)
    refresher.tick()
    with pytest.raises(oidc.OidcRefreshFault):
        refresher.assert_healthy()


def test_health_gated_blocks_new_invocation_after_fault():
    class FakeSession:
        def __init__(self):
            self.faulted = False

        def assert_healthy(self):
            if self.faulted:
                raise oidc.OidcRefreshFault("faulted")

    session = FakeSession()
    calls = []

    def query_fn(check_class, reservation, state, user_prompt, model=None):
        calls.append(1)
        return "ok"

    wrapped = oidc.health_gated(query_fn, session)
    assert wrapped("c", "r", "s", "p") == "ok"
    session.faulted = True
    with pytest.raises(oidc.OidcRefreshFault):
        wrapped("c", "r", "s", "p")
    assert len(calls) == 1  # the faulted call never reached query_fn


def test_health_gated_preserves_async_coroutine_function():
    # Windows note: anyio.run()'s ProactorEventLoop opens a local
    # self-pipe via socket.socketpair(), which conftest.py's blanket
    # network-connect guard also trips (the same interaction
    # agents/checker/harness.py's own _invoke docstring documents) — so
    # this test proves the coroutine-function property CagedCheckerStub
    # dispatches on, via a manual coroutine step, rather than driving it
    # through anyio.run().
    import inspect

    class FakeSession:
        def assert_healthy(self):
            return None

    async def async_query_fn(check_class, reservation, state, user_prompt, model=None):
        return "async-ok"

    wrapped = oidc.health_gated(async_query_fn, FakeSession())
    assert inspect.iscoroutinefunction(wrapped)

    coro = wrapped("c", "r", "s", "p")
    try:
        coro.send(None)
    except StopIteration as exc:
        assert exc.value == "async-ok"
    else:
        pytest.fail("coroutine did not complete synchronously")


def test_no_secret_value_ever_appears_in_source_repr_or_env_after_capture():
    env = {
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example/tok",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "top-secret-request-token",
    }
    source = oidc.capture_actions_request_source(env)
    assert "top-secret-request-token" not in repr(source)
    assert "top-secret-request-token" not in str(source)

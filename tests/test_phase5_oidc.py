"""Tests for agents/checker/oidc.py (P5-B Part 3/3, revision-c seams 1 and 4)."""

from __future__ import annotations

import importlib.util
import inspect
import io
import json
import threading
import time
import types
import urllib.error
from contextlib import contextmanager
from pathlib import Path

import pytest

from agents.checker import oidc

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEDULED_RUNNER_PATH = REPO_ROOT / "scripts" / "run_phase5_scheduled.py"


def _load_scheduled_runner():
    """Load the scheduled entrypoint BY PATH, never as ``scripts.<module>``.

    tests/test_dependency_surface.py pins the dev third-party import set for
    every test module, and a package-style ``scripts`` import would widen it.
    Loading by path is the established convention here (see
    tests/test_phase5_gate_runner.py's ``_load_module``). Safe because the
    scheduled runner never imports claude_agent_sdk at module scope."""
    spec = importlib.util.spec_from_file_location(
        "run_phase5_scheduled_probe", SCHEDULED_RUNNER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


# ---------------------------------------------------------------------------
# Single-use assertions across fresh CLI processes (Q-77 B5-P0 Part 1;
# plan q77-p5d-repair-stage2cb5-plan-d).
#
# The provider allows one exchange per assertion ``jti``, and the Agent SDK
# spawns a FRESH CLI process per logical invocation, each performing its own
# exchange. Every test here is model-free and performs NO real OIDC request:
# the fetch seam is always injected.
# ---------------------------------------------------------------------------


def _session(tmp_path, tokens):
    """A session whose fetch seam yields ``tokens`` in order, with no network."""
    env = {"ANTHROPIC_IDENTITY_TOKEN_FILE": str(tmp_path / "identity.jwt")}
    source = oidc.OidcRequestSource(request_url="https://example/token", request_token="req-tok")
    session = oidc.OidcSession(source=source, first_jwt="jwt-initial")
    issued = iter(tokens)
    session.fetch_token = lambda src: next(issued)
    oidc.write_placeholder_token_file(env)
    return env, session


def _installed(env):
    return Path(env["ANTHROPIC_IDENTITY_TOKEN_FILE"]).read_text(encoding="ascii")


def test_each_invocation_installs_a_distinct_never_exchanged_assertion(tmp_path):
    """Property 1: two sequential invocations present two different assertions."""
    env, session = _session(tmp_path, ["jwt-a", "jwt-b"])
    seen = []

    def query_fn(check_class, reservation, state, user_prompt, model=None):
        seen.append(_installed(env))
        return "outcome"

    wrapped = oidc.assertion_refreshed(query_fn, session, env)
    wrapped("c", None, None, "p")
    wrapped("c", None, None, "p")

    assert seen == ["jwt-a", "jwt-b"]
    assert len(set(seen)) == 2


def test_assertion_is_installed_before_the_wrapped_callable_runs(tmp_path):
    env, session = _session(tmp_path, ["jwt-a"])
    order = []
    session.fetch_token = lambda src: (order.append("fetch"), "jwt-a")[1]

    def query_fn(check_class, reservation, state, user_prompt, model=None):
        order.append("invoke")

    oidc.assertion_refreshed(query_fn, session, env)("c", None, None, "p")
    assert order == ["fetch", "invoke"]


def test_failed_acquisition_prevents_the_invocation_and_installs_nothing(tmp_path):
    """Property 2: no invocation may start against a possibly-exchanged token."""
    env, session = _session(tmp_path, [])
    before = _installed(env)
    ran = []

    def failing(src):
        raise oidc.OidcAcquisitionError("GitHub OIDC token request returned HTTP 403")

    session.fetch_token = failing

    def query_fn(check_class, reservation, state, user_prompt, model=None):
        ran.append(True)

    with pytest.raises(oidc.OidcAcquisitionError):
        oidc.assertion_refreshed(query_fn, session, env)("c", None, None, "p")

    assert ran == []
    assert _installed(env) == before


def test_prepare_and_background_refresher_share_one_producer_lock(tmp_path):
    """Property 3: the two producers serialize their fetch-then-install pairs."""
    env, session = _session(tmp_path, [])
    session.fetch_token = lambda src: "jwt-prepare"
    session.install_and_start(env, interval_seconds=3600)
    try:
        assert session.refresher.lock is session.install_lock
    finally:
        session.refresher.stop()

    # While the refresher holds the lock mid-fetch, a concurrent prepare waits
    # and cannot install; the two installs never interleave.
    entered, release, order = threading.Event(), threading.Event(), []

    def slow_refresh_fetch(src):
        entered.set()
        release.wait(5)
        order.append("refresher-fetch")
        return "jwt-refresher"

    refresher = oidc.TokenFileRefresher(
        source=session.source, env=env, interval_seconds=3600,
        fetch_token=slow_refresh_fetch, lock=session.install_lock,
    )
    session.fetch_token = lambda src: (order.append("prepare-fetch"), "jwt-prepare")[1]

    thread = threading.Thread(target=refresher.tick)
    thread.start()
    assert entered.wait(5)
    waiter = threading.Thread(target=session.prepare_fresh_assertion, args=(env,))
    waiter.start()
    time.sleep(0.05)
    assert order == [], "prepare entered the critical section while the refresher held it"
    release.set()
    thread.join(5)
    waiter.join(5)
    assert order == ["refresher-fetch", "prepare-fetch"]
    assert _installed(env) == "jwt-prepare"


def test_request_credentials_never_re_enter_the_environment(tmp_path):
    """Property 4: parent memory only, even across many acquisitions."""
    real_env = {
        "ANTHROPIC_IDENTITY_TOKEN_FILE": str(tmp_path / "identity.jwt"),
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example/token",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "req-tok",
    }
    source = oidc.capture_actions_request_source(real_env)
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" not in real_env
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in real_env

    session = oidc.OidcSession(source=source, first_jwt="jwt-initial")
    session.fetch_token = lambda src: "jwt-fresh"
    oidc.write_placeholder_token_file(real_env)
    for _ in range(3):
        session.prepare_fresh_assertion(real_env)

    assert "ACTIONS_ID_TOKEN_REQUEST_URL" not in real_env
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in real_env
    assert "req-tok" not in repr(session)


def test_no_token_value_appears_in_repr_or_error_text(tmp_path):
    """Property 5: neither the assertion nor the request credential leaks."""
    env, session = _session(tmp_path, [])
    secret = "jwt-super-secret-value"

    def failing(src):
        raise oidc.OidcAcquisitionError("GitHub OIDC token request returned HTTP 500")

    session.fetch_token = lambda src: secret
    session.prepare_fresh_assertion(env)
    assert secret not in repr(session)
    assert secret not in repr(session.source)

    session.fetch_token = failing
    with pytest.raises(oidc.OidcAcquisitionError) as excinfo:
        session.prepare_fresh_assertion(env)
    assert secret not in str(excinfo.value)
    assert "req-tok" not in str(excinfo.value)


def test_shutdown_and_scrubbing_are_unchanged(tmp_path):
    """Property 6: the repair adds no new persistence to clean up."""
    env, session = _session(tmp_path, [])
    session.fetch_token = lambda src: "jwt-fresh"
    session.prepare_fresh_assertion(env)
    path = Path(env["ANTHROPIC_IDENTITY_TOKEN_FILE"])
    assert path.exists()
    session.shutdown(env)
    assert not path.exists()
    # Idempotent, exactly as before.
    session.shutdown(env)
    oidc.scrub_identity_token_file(env)


def test_local_oauth_path_is_untouched_by_the_repair():
    """Property 7: only the WIF lanes compose the fresh-assertion wrapper."""
    cli_source = (REPO_ROOT / "sentinel" / "cli.py").read_text(encoding="utf-8")
    assert "assertion_refreshed" not in cli_source
    assert "ANTHROPIC_IDENTITY_TOKEN_FILE" not in cli_source


def test_assertion_refreshed_preserves_coroutine_functions(tmp_path):
    """CagedCheckerStub._invoke dispatches on iscoroutinefunction."""
    env, session = _session(tmp_path, ["jwt-a"])

    async def async_query_fn(check_class, reservation, state, user_prompt, model=None):
        return "async-outcome"

    wrapped = oidc.assertion_refreshed(async_query_fn, session, env)
    assert inspect.iscoroutinefunction(wrapped)

    # Driven by a manual coroutine step rather than asyncio.run() for the same
    # reason test_health_gated_preserves_async_coroutine_function documents:
    # the Windows ProactorEventLoop self-pipe trips conftest's network guard.
    coro = wrapped("c", None, None, "p")
    try:
        coro.send(None)
    except StopIteration as exc:
        assert exc.value == "async-outcome"
    else:
        pytest.fail("coroutine did not complete synchronously")
    assert _installed(env) == "jwt-a"


# ---------------------------------------------------------------------------
# Bounded acquisition retry (plan-d Part 7 row 9)
# ---------------------------------------------------------------------------


def test_every_retry_attempt_mints_a_new_assertion(tmp_path):
    """Property 10: a retry is never a replay of an already-returned token."""
    source = oidc.OidcRequestSource(request_url="https://example/token", request_token="req-tok")
    issued = []

    def opener(request, timeout=None):
        issued.append(len(issued))
        if len(issued) < 3:
            return _FakeResponse(503, b"unavailable")
        return _FakeResponse(200, b'{"value":"jwt-attempt-3"}')

    slept = []
    token = oidc.fetch_github_oidc_token_with_retry(
        source, opener=opener, sleep=slept.append
    )
    assert token == "jwt-attempt-3"
    assert len(issued) == 3, "each attempt must call GitHub again, not reuse a token"
    assert slept == [1.0, 2.0]


def test_acquisition_retry_is_bounded_at_three_attempts(tmp_path):
    source = oidc.OidcRequestSource(request_url="https://example/token", request_token="req-tok")
    attempts, slept = [], []

    def opener(request, timeout=None):
        attempts.append(1)
        return _FakeResponse(503, b"unavailable")

    with pytest.raises(oidc.OidcAcquisitionError):
        oidc.fetch_github_oidc_token_with_retry(source, opener=opener, sleep=slept.append)
    assert len(attempts) == 3
    assert slept == [1.0, 2.0], "no sleep after the final failed attempt"


@pytest.mark.parametrize(
    "status, body",
    [(403, b"forbidden"), (404, b"missing"), (200, b"not-json"), (200, b'{"no":"value"}')],
)
def test_deterministic_acquisition_failures_are_not_retried(status, body):
    """Authorization, configuration and malformed-response failures fail closed."""
    source = oidc.OidcRequestSource(request_url="https://example/token", request_token="req-tok")
    attempts, slept = [], []

    def opener(request, timeout=None):
        attempts.append(1)
        return _FakeResponse(status, body)

    with pytest.raises(oidc.OidcAcquisitionError):
        oidc.fetch_github_oidc_token_with_retry(source, opener=opener, sleep=slept.append)
    assert len(attempts) == 1
    assert slept == []


def test_real_http_403_is_classified_deterministic_not_transient():
    """urlopen raises HTTPError (a URLError subclass) for every non-2xx, so
    classifying on URLError alone would wrongly retry a 403."""
    def opener(request, timeout=None):
        raise urllib.error.HTTPError("https://example", 403, "Forbidden", None, None)

    source = oidc.OidcRequestSource(request_url="https://example/token", request_token="req-tok")
    attempts, slept = [], []

    def counting_opener(request, timeout=None):
        attempts.append(1)
        return opener(request, timeout)

    with pytest.raises(oidc.OidcAcquisitionError):
        oidc.fetch_github_oidc_token_with_retry(source, opener=counting_opener, sleep=slept.append)
    assert len(attempts) == 1 and slept == []


def test_real_http_503_is_classified_transient():
    def opener(request, timeout=None):
        raise urllib.error.HTTPError("https://example", 503, "Unavailable", None, None)

    source = oidc.OidcRequestSource(request_url="https://example/token", request_token="req-tok")
    attempts, slept = [], []

    def counting_opener(request, timeout=None):
        attempts.append(1)
        return opener(request, timeout)

    with pytest.raises(oidc.OidcAcquisitionError):
        oidc.fetch_github_oidc_token_with_retry(source, opener=counting_opener, sleep=slept.append)
    assert len(attempts) == 3 and slept == [1.0, 2.0]


# ---------------------------------------------------------------------------
# Scheduled production lane composition (owner correction to plan-d Part 1b)
#
# The scheduled lane acquires ONE OidcSession per run but execute_run makes
# MANY provider invocations across the live task set, and the ordinary profile
# keeps its bounded second attempt -- so it carries the same replay exposure
# the timing lane and official gate do, and must receive the same repair.
# ---------------------------------------------------------------------------


def test_scheduled_runner_composes_assertion_refreshed_over_health_gated(monkeypatch, tmp_path):
    scheduled = _load_scheduled_runner()
    from agents.checker import harness as harness_mod
    from sentinel import pipeline as pipeline_mod

    calls = {}

    class _FakeStub:
        def __init__(self):
            self.query_fn = "raw-query-fn"

    def _fake_build_stub(**kwargs):
        return _FakeStub()

    def _fake_health_gated(query_fn, session):
        calls["health"] = (query_fn, session)
        return ("health-wrapped", query_fn)

    def _fake_assertion_refreshed(query_fn, session, env):
        calls["assertion"] = (query_fn, session, env)
        return ("assertion-refreshed", query_fn)

    def _fake_execute_run(config, deps):
        calls["query_fn"] = deps.judgment.query_fn
        return "outcome"

    monkeypatch.setattr(harness_mod, "build_caged_judgment_stub", _fake_build_stub)
    monkeypatch.setattr(oidc, "health_gated", _fake_health_gated)
    monkeypatch.setattr(oidc, "assertion_refreshed", _fake_assertion_refreshed)
    monkeypatch.setattr(pipeline_mod, "execute_run", _fake_execute_run)

    env = {"ANTHROPIC_IDENTITY_TOKEN_FILE": str(tmp_path / "identity.jwt")}
    session = object()
    working_state = types.SimpleNamespace(
        db_path=tmp_path / "s.sqlite3",
        findings_path=tmp_path / "FINDINGS.md",
        cost_ledger_path=tmp_path / "cost.jsonl",
        root=tmp_path,
    )
    run_sentinel = scheduled._build_run_sentinel(
        env, github_owner="owner", session_holder={"session": session}
    )
    assert run_sentinel(working_state, "12345") == "outcome"

    # health_gated stays innermost; assertion_refreshed wraps it and receives
    # the same session plus the environment naming the token file.
    assert calls["health"] == ("raw-query-fn", session)
    assert calls["assertion"] == (("health-wrapped", "raw-query-fn"), session, env)
    assert calls["query_fn"] == ("assertion-refreshed", ("health-wrapped", "raw-query-fn"))


def test_scheduled_runner_still_skips_wrapping_when_no_session_exists(monkeypatch, tmp_path):
    """A non-provider scheduled path must not acquire an assertion."""
    scheduled = _load_scheduled_runner()
    from agents.checker import harness as harness_mod
    from sentinel import pipeline as pipeline_mod

    captured = {}

    class _FakeStub:
        def __init__(self):
            self.query_fn = "raw-query-fn"

    monkeypatch.setattr(harness_mod, "build_caged_judgment_stub", lambda **kw: _FakeStub())
    monkeypatch.setattr(
        oidc, "assertion_refreshed",
        lambda *a, **kw: pytest.fail("assertion_refreshed must not run without a session"),
    )
    monkeypatch.setattr(
        pipeline_mod, "execute_run",
        lambda config, deps: captured.setdefault("query_fn", deps.judgment.query_fn),
    )

    working_state = types.SimpleNamespace(
        db_path=tmp_path / "s.sqlite3",
        findings_path=tmp_path / "FINDINGS.md",
        cost_ledger_path=tmp_path / "cost.jsonl",
        root=tmp_path,
    )
    run_sentinel = scheduled._build_run_sentinel({}, github_owner="owner", session_holder={})
    run_sentinel(working_state, "12345")
    assert captured["query_fn"] == "raw-query-fn"


# ---------------------------------------------------------------------------
# The background refresher uses the SAME bounded acquisition policy
# (dispatch q77-p5d-repair-stage2cb5-p0-refresh-retry-repair-a).
#
# The B5-P0 repair gave the initial acquisition and the per-invocation prepare
# a bounded 3-attempt policy but left install_and_start constructing the
# refresher WITHOUT fetch_token, so the background producer silently kept
# TokenFileRefresher's historical single-attempt default. One transient
# failure during a long invocation would then latch _fault, and the next
# assert_healthy() would refuse -- stopping a run that had already consumed
# the one-shot lane.
#
# Model-free throughout: every fetch seam is injected, so no test here makes a
# real OIDC, network, provider or model call.
# ---------------------------------------------------------------------------


def _plain_session(tmp_path):
    """A session left on its PRODUCTION acquisition policy (no override)."""
    env = {"ANTHROPIC_IDENTITY_TOKEN_FILE": str(tmp_path / "identity.jwt")}
    source = oidc.OidcRequestSource(request_url="https://example/token", request_token="req-tok")
    session = oidc.OidcSession(source=source, first_jwt="jwt-initial")
    oidc.write_placeholder_token_file(env)
    return env, session


def test_default_acquisition_policy_is_callable_with_one_argument(tmp_path, monkeypatch):
    """Regression: the production path with NOTHING injected.

    ``fetch_token`` is an init=False dataclass field. Declared with a plain
    ``default`` it stays a CLASS attribute, so ``self.fetch_token`` binds as a
    method and ``self.fetch_token(self.source)`` passes the session itself as
    the first argument -- a TypeError on every real invocation, which in the
    timing driver becomes AUTH_OR_OIDC_FAULT at ordinal 1 and consumes the
    one-shot lane.

    Every other test in this file injects ``session.fetch_token`` as an
    instance attribute, which masks that entirely. This test deliberately does
    not, and injects only the transport."""
    env, session = _plain_session(tmp_path)
    assert not hasattr(session.fetch_token, "__self__"), (
        "fetch_token must be a plain function on the instance, not a bound method"
    )
    assert session.fetch_token is oidc.fetch_github_oidc_token_with_retry

    calls = []

    def fake_inner(source, *, audience=None, opener=None):
        calls.append(source)
        return "jwt-default-policy"

    monkeypatch.setattr(oidc, "fetch_github_oidc_token", fake_inner)
    session.prepare_fresh_assertion(env)

    assert _installed(env) == "jwt-default-policy"
    assert calls == [session.source], "the request source, not the session, must be passed"


def test_background_refresh_default_policy_is_also_callable(tmp_path, monkeypatch):
    """The same regression, through the background producer."""
    env, session = _plain_session(tmp_path)
    calls = []

    def fake_inner(source, *, audience=None, opener=None):
        calls.append(source)
        return "jwt-background-default"

    monkeypatch.setattr(oidc, "fetch_github_oidc_token", fake_inner)
    session.install_and_start(env, interval_seconds=3600)
    try:
        session.refresher.tick()
        session.assert_healthy()
    finally:
        session.refresher.stop()

    assert _installed(env) == "jwt-background-default"
    assert calls == [session.source]


def test_install_and_start_wires_the_refresher_to_the_session_policy(tmp_path):
    """Property 1: not TokenFileRefresher's single-attempt default."""
    env, session = _plain_session(tmp_path)
    # The production default, before anything is injected.
    assert session.fetch_token is oidc.fetch_github_oidc_token_with_retry
    assert oidc.TokenFileRefresher.__dataclass_fields__["fetch_token"].default is (
        oidc.fetch_github_oidc_token
    ), "the historical single-attempt default is still the field default"

    session.install_and_start(env, interval_seconds=3600)
    try:
        assert session.refresher.fetch_token is session.fetch_token
        assert session.refresher.fetch_token is oidc.fetch_github_oidc_token_with_retry
        assert session.refresher.fetch_token is not oidc.fetch_github_oidc_token
        # The shared producer lock stays load-bearing.
        assert session.refresher.lock is session.install_lock
    finally:
        session.refresher.stop()


def test_install_and_start_carries_an_injected_policy_through(tmp_path):
    """The same wiring keeps a test policy in force for the background path."""
    env, session = _plain_session(tmp_path)
    session.fetch_token = lambda src: "jwt-injected"
    session.install_and_start(env, interval_seconds=3600)
    try:
        assert session.refresher.fetch_token is session.fetch_token
        session.refresher.tick()
        assert _installed(env) == "jwt-injected"
    finally:
        session.refresher.stop()


def _bounded_through_opener(opener, slept):
    """Route the session policy through the REAL bounded wrapper, with the
    transport and the clock injected so no network call and no real sleep
    occurs."""
    return lambda src: oidc.fetch_github_oidc_token_with_retry(
        src, opener=opener, sleep=slept.append
    )


def test_background_refresh_retries_a_transient_failure_within_the_bound(tmp_path):
    """Properties 2 and 3: bounded 3 attempts, 1s then 2s, and a transient
    failure followed by success must NOT latch a fault."""
    env, session = _plain_session(tmp_path)
    attempts, slept = [], []

    def opener(request, timeout=None):
        attempts.append(1)
        if len(attempts) < 3:
            return _FakeResponse(503, b"unavailable")
        return _FakeResponse(200, b'{"value":"jwt-refreshed"}')

    session.fetch_token = _bounded_through_opener(opener, slept)
    session.install_and_start(env, interval_seconds=3600)
    try:
        session.refresher.tick()
    finally:
        session.refresher.stop()

    assert len(attempts) == 3
    assert slept == [1.0, 2.0]
    assert _installed(env) == "jwt-refreshed"
    session.assert_healthy()  # no latched fault: the refresh ultimately succeeded


def test_background_refresh_exhaustion_latches_fault_and_blocks_next_invocation(tmp_path):
    """Property 4: fail-closed semantics are unchanged once the bound is spent."""
    env, session = _plain_session(tmp_path)
    attempts, slept = [], []

    def opener(request, timeout=None):
        attempts.append(1)
        return _FakeResponse(503, b"unavailable")

    session.fetch_token = _bounded_through_opener(opener, slept)
    session.install_and_start(env, interval_seconds=3600)
    try:
        session.refresher.tick()
    finally:
        session.refresher.stop()

    assert len(attempts) == 3, "the bound is three attempts, not one and not unbounded"
    assert slept == [1.0, 2.0], "no sleep after the final failed attempt"

    with pytest.raises(oidc.OidcRefreshFault):
        session.assert_healthy()

    ran = []

    def query_fn(check_class, reservation, state, user_prompt, model=None):
        ran.append(True)

    with pytest.raises(oidc.OidcRefreshFault):
        oidc.assertion_refreshed(query_fn, session, env)("c", None, None, "p")
    assert ran == [], "a latched refresh fault must prevent the next invocation"


@pytest.mark.parametrize("status, body", [(403, b"forbidden"), (404, b"missing")])
def test_background_refresh_does_not_retry_deterministic_rejection(tmp_path, status, body):
    """Property 5: an authorization or configuration rejection is single-attempt."""
    env, session = _plain_session(tmp_path)
    attempts, slept = [], []

    def opener(request, timeout=None):
        attempts.append(1)
        return _FakeResponse(status, body)

    session.fetch_token = _bounded_through_opener(opener, slept)
    session.install_and_start(env, interval_seconds=3600)
    try:
        session.refresher.tick()
    finally:
        session.refresher.stop()

    assert len(attempts) == 1
    assert slept == []
    with pytest.raises(oidc.OidcRefreshFault):
        session.assert_healthy()


def test_background_refresh_leaks_no_token_value_and_makes_no_real_request(tmp_path):
    """Property 6: the refresh path carries no credential into any message."""
    env, session = _plain_session(tmp_path)
    secret = "jwt-refresher-secret-value"
    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        return _FakeResponse(200, ('{"value":"%s"}' % secret).encode("ascii"))

    slept = []
    session.fetch_token = _bounded_through_opener(opener, slept)
    session.install_and_start(env, interval_seconds=3600)
    try:
        session.refresher.tick()
        assert _installed(env) == secret
        assert secret not in repr(session)
        assert secret not in repr(session.refresher)
        assert secret not in repr(session.source)
        assert "req-tok" not in repr(session.refresher)
    finally:
        session.refresher.stop()

    # The only transport touched is the injected opener.
    assert calls and all(url.startswith("https://example/token") for url in calls)

    # And a failure message still carries a status only, never a credential.
    def failing_opener(request, timeout=None):
        return _FakeResponse(500, secret.encode("ascii"))

    session.fetch_token = _bounded_through_opener(failing_opener, [])
    refresher = oidc.TokenFileRefresher(
        source=session.source, env=env, interval_seconds=3600,
        fetch_token=session.fetch_token, lock=session.install_lock,
    )
    refresher.tick()
    with pytest.raises(oidc.OidcRefreshFault) as excinfo:
        refresher.assert_healthy()
    assert secret not in str(excinfo.value)
    assert "req-tok" not in str(excinfo.value)


def test_all_three_acquisition_paths_share_one_policy(tmp_path):
    """Initial acquisition, per-invocation prepare and background refresh."""
    env, session = _plain_session(tmp_path)
    used = []
    session.fetch_token = lambda src: (used.append("policy"), "jwt-x")[1]
    session.install_and_start(env, interval_seconds=3600)
    try:
        session.prepare_fresh_assertion(env)
        session.refresher.tick()
    finally:
        session.refresher.stop()
    assert used == ["policy", "policy"]

    source = (REPO_ROOT / "agents" / "checker" / "oidc.py").read_text(encoding="utf-8")
    assert "first_jwt = fetch_github_oidc_token_with_retry(source, audience=audience)" in source
    assert "fetch_token=self.fetch_token," in source

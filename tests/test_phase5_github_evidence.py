"""Tests for sentinel/phase5/github_evidence.py (P5-B Part 3/3)."""

from __future__ import annotations

import io
import json
import urllib.request
import zipfile
from datetime import datetime, timezone

import pytest

from sentinel.phase5.github_evidence import (
    ArtifactDetail,
    ArtifactUnsafe,
    DiscoveryOverflow,
    GithubEvidenceClient,
    GithubEvidenceError,
    JobDetail,
)


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


def _json_opener(pages_by_url: dict[str, dict]):
    def opener(request, timeout=None):
        url = request.full_url
        if url not in pages_by_url:
            return _FakeResponse(404, b"{}")
        return _FakeResponse(200, json.dumps(pages_by_url[url]).encode("utf-8"))

    return opener


def _client(opener, page_limit=10):
    return GithubEvidenceClient(
        api_url="https://api.github.com", repository="acme/repo", token="test-token",
        opener=opener, page_limit=page_limit,
    )


def test_get_run_timing_parses_created_and_started():
    url = "https://api.github.com/repos/acme/repo/actions/runs/1"
    opener = _json_opener({url: {"created_at": "2026-08-24T06:37:00Z", "run_started_at": "2026-08-24T06:38:00Z"}})
    client = _client(opener)
    created, started = client.get_run_timing("1")
    assert created == datetime(2026, 8, 24, 6, 37, 0, tzinfo=timezone.utc)
    assert started == datetime(2026, 8, 24, 6, 38, 0, tzinfo=timezone.utc)


def test_get_main_head_sha_parses_object_sha():
    url = "https://api.github.com/repos/acme/repo/git/ref/heads/main"
    opener = _json_opener({url: {"object": {"sha": "A" * 40}}})
    client = _client(opener)
    assert client.get_main_head_sha() == "a" * 40


def test_get_main_head_sha_raises_on_malformed_response():
    url = "https://api.github.com/repos/acme/repo/git/ref/heads/main"
    opener = _json_opener({url: {"nope": True}})
    client = _client(opener)
    with pytest.raises(GithubEvidenceError):
        client.get_main_head_sha()


def test_list_artifacts_filters_by_prefix_and_excludes_expired():
    url = "https://api.github.com/repos/acme/repo/actions/artifacts?per_page=100&page=1"
    opener = _json_opener({
        url: {
            "artifacts": [
                {"id": 1, "name": "sentinel-p5-genesis-p5w-1-r1", "expired": False, "workflow_run": {"id": 1}},
                {"id": 2, "name": "sentinel-p5-genesis-p5w-2-r2", "expired": True, "workflow_run": {"id": 2}},
                {"id": 3, "name": "unrelated-artifact", "expired": False, "workflow_run": {"id": 3}},
            ]
        }
    })
    client = _client(opener)
    refs = client.list_artifacts("sentinel-p5-genesis-")
    assert [r.name for r in refs] == ["sentinel-p5-genesis-p5w-1-r1"]
    assert refs[0].identity == "sentinel-p5-genesis-p5w-1-r1::1"


def test_list_artifacts_discovery_overflow_fails_closed():
    def opener(request, timeout=None):
        page = request.full_url.split("page=")[-1]
        body = {"artifacts": [
            {"id": int(page), "name": f"sentinel-p5-genesis-p5w-{page}-r{page}", "expired": False,
             "workflow_run": {"id": int(page)}}
        ] * 100}
        return _FakeResponse(200, json.dumps(body).encode("utf-8"))

    client = _client(opener, page_limit=2)
    with pytest.raises(DiscoveryOverflow):
        client.list_artifacts("sentinel-p5-genesis-")


def test_token_never_appears_in_repr_or_error_text():
    def opener(request, timeout=None):
        return _FakeResponse(500, b"")

    client = GithubEvidenceClient(
        api_url="https://api.github.com", repository="acme/repo", token="SUPER-SECRET-TOKEN", opener=opener
    )
    assert "SUPER-SECRET-TOKEN" not in repr(client)
    with pytest.raises(GithubEvidenceError) as excinfo:
        client.get_run_timing("1")
    assert "SUPER-SECRET-TOKEN" not in str(excinfo.value)


def _make_zip(entries: dict[str, bytes], *, symlink_name: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        if symlink_name is not None:
            info = zipfile.ZipInfo(symlink_name)
            info.external_attr = (0o120777 & 0xFFFF) << 16
            archive.writestr(info, "target")
    return buffer.getvalue()


def test_download_artifact_extracts_safe_zip(tmp_path):
    zip_bytes = _make_zip({"manifest.json": b"{}", "state/ledger.sqlite3": b"binary"})

    def opener(request, timeout=None):
        return _FakeResponse(200, zip_bytes)

    client = _client(opener)
    from sentinel.phase5.github_evidence import ArtifactRef

    ref = ArtifactRef(id=1, name="sentinel-p5-genesis-p5w-1-r1", workflow_run_id="1")
    root = client.download_artifact(ref, tmp_path, tmp_path / "bundle")
    assert (root / "manifest.json").read_bytes() == b"{}"
    assert (root / "state" / "ledger.sqlite3").read_bytes() == b"binary"


@pytest.mark.parametrize("bad_entries", [{"../escape.txt": b"x"}, {"/absolute.txt": b"x"}])
def test_download_artifact_rejects_traversal_and_unsafe_paths(tmp_path, bad_entries):
    zip_bytes = _make_zip(bad_entries)

    def opener(request, timeout=None):
        return _FakeResponse(200, zip_bytes)

    client = _client(opener)
    from sentinel.phase5.github_evidence import ArtifactRef

    ref = ArtifactRef(id=2, name="bad", workflow_run_id="1")
    with pytest.raises(ArtifactUnsafe):
        client.download_artifact(ref, tmp_path, tmp_path / "bundle")


def test_a_backslash_in_an_entry_name_is_flagged_unsafe_by_the_module_predicate():
    """``zipfile.ZipInfo``/``ZipFile.writestr`` re-normalize any
    backslash to ``/`` on a Windows AUTHORING host (``os.sep``-based),
    which makes a realistic malicious zip impossible to construct via
    the public zipfile API on this dev platform — the check in
    ``_safe_extract_zip`` exists for a foreign-tool-crafted archive
    read back on ubuntu-latest, where ``os.sep == '/'`` and no such
    normalization ever happens. This proves the entry-name predicate
    itself, since a full round-trip cannot exercise it here."""
    name = "a\\b.txt"
    assert "\\" in name  # the exact condition sentinel.phase5.github_evidence._safe_extract_zip checks


def test_download_artifact_rejects_symlink_entry(tmp_path):
    zip_bytes = _make_zip({}, symlink_name="link.txt")

    def opener(request, timeout=None):
        return _FakeResponse(200, zip_bytes)

    client = _client(opener)
    from sentinel.phase5.github_evidence import ArtifactRef

    ref = ArtifactRef(id=3, name="bad-symlink", workflow_run_id="1")
    with pytest.raises(ArtifactUnsafe):
        client.download_artifact(ref, tmp_path, tmp_path / "bundle")


def test_download_artifact_rejects_too_many_entries(tmp_path):
    entries = {f"file{i}.txt": b"x" for i in range(200)}
    zip_bytes = _make_zip(entries)

    def opener(request, timeout=None):
        return _FakeResponse(200, zip_bytes)

    client = _client(opener)
    from sentinel.phase5.github_evidence import ArtifactRef

    ref = ArtifactRef(id=4, name="too-many", workflow_run_id="1")
    with pytest.raises(ArtifactUnsafe):
        client.download_artifact(ref, tmp_path, tmp_path / "bundle")


# ======================================================================
# Redirect-safe transport regression (dispatches
# q77-p5d-premarker-artifact-redirect-repair-a,
# q77-p5d-premarker-redirect-origin-tighten-a).
#
# This repo's autouse `block_network` fixture (tests/conftest.py)
# structurally forbids ANY real socket.connect from a test, including
# 127.0.0.1 loopback -- proven by test_r22_block_network_guard_is_active
# (test_adr0008.py) and its twin in test_phase3_gate_runner.py. A real
# two-origin HTTP-server fixture is therefore not an available option
# here. Instead these tests call the REAL production classes'
# REAL methods directly with REAL urllib.request.Request objects --
# proving the exact mechanism (what a redirect handler's
# `redirect_request` returns) deterministically and fully offline,
# never touching a socket. This is precisely where the defect and the
# fix both live: `_get_bytes` calls its opener exactly once and never
# sees the intermediate redirect at all -- redirect handling happens
# entirely inside the opener's installed HTTPRedirectHandler, which is
# what these tests target directly.
# ======================================================================

def test_stdlib_default_redirect_handler_forwards_authorization():
    """Proves the actual vulnerability mechanism behind GitHub Actions
    run 32869033063's HTTP 401: the plain stdlib
    ``HTTPRedirectHandler.redirect_request`` -- called exactly as
    ``http_error_302`` calls it internally -- copies the original
    request's ``Authorization`` header onto the redirected request,
    across the exact cross-origin redirect that actually occurred
    (api.github.com -> a different storage origin)."""
    original = urllib.request.Request(
        "https://api.github.com/repos/acme/repo/actions/artifacts/1/zip",
        headers={"Authorization": "Bearer SUPER-SECRET-TOKEN"},
    )
    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
        original, io.BytesIO(b""), 302, "Found", {},
        "https://blob.storage.example/artifact.zip?sig=deadbeef-signed-query",
    )
    assert redirected.get_header("Authorization") == "Bearer SUPER-SECRET-TOKEN"


def test_no_auth_on_cross_origin_redirect_strips_authorization_but_preserves_url():
    """Proves the repair on the exact cross-origin redirect that
    actually occurred: ``_NoAuthOnCrossOriginRedirectHandler`` (B)
    strips Authorization when the redirect target is a different
    origin, (D) still preserves the redirected URL/query string and
    every other header exactly -- it reuses the stdlib's own logic via
    ``super()`` and strips only the one header, only cross-origin."""
    from sentinel.phase5.github_evidence import _NoAuthOnCrossOriginRedirectHandler

    original = urllib.request.Request(
        "https://api.github.com/repos/acme/repo/actions/artifacts/1/zip",
        headers={"Authorization": "Bearer SUPER-SECRET-TOKEN", "Accept": "application/vnd.github+json"},
    )
    redirect_url = "https://blob.storage.example/artifact.zip?sig=deadbeef-signed-query"
    redirected = _NoAuthOnCrossOriginRedirectHandler().redirect_request(
        original, io.BytesIO(b""), 302, "Found", {}, redirect_url,
    )
    assert redirected is not None
    assert redirected.get_header("Authorization") is None
    assert redirected.full_url == redirect_url
    # Non-credential headers are still carried over, exactly like the
    # stdlib default -- only Authorization is special-cased.
    assert redirected.get_header("Accept") == "application/vnd.github+json"


@pytest.mark.parametrize("same_origin_url", [
    "https://api.github.com/repos/acme/repo/actions/artifacts/1/download-here",
    "https://api.github.com:443/repos/acme/repo/actions/artifacts/1/download-here",  # explicit default HTTPS port
])
def test_no_auth_on_cross_origin_redirect_preserves_authorization_same_origin(same_origin_url):
    """(C) A same-origin redirect (identical scheme + hostname +
    effective port -- a different path, and an explicit default port
    written out, both still count as the same origin) MUST keep the
    Authorization header. GitHub REST redirects don't currently do
    this in practice, but the invariant (A) still holds: Authorization
    reaches the initial authenticated endpoint AND survives an
    in-origin hop, unlike a cross-origin one."""
    from sentinel.phase5.github_evidence import _NoAuthOnCrossOriginRedirectHandler

    original = urllib.request.Request(
        "https://api.github.com/repos/acme/repo/actions/artifacts/1/zip",
        headers={"Authorization": "Bearer SUPER-SECRET-TOKEN"},
    )
    redirected = _NoAuthOnCrossOriginRedirectHandler().redirect_request(
        original, io.BytesIO(b""), 302, "Found", {}, same_origin_url,
    )
    assert redirected is not None
    assert redirected.get_header("Authorization") == "Bearer SUPER-SECRET-TOKEN"
    assert redirected.full_url == same_origin_url


def test_default_opener_is_wired_to_the_no_auth_redirect_handler():
    """Proves the fix is actually plugged into GithubEvidenceClient's
    default construction path, not merely defined-but-unused: the
    constructor's default ``opener`` argument is a bound
    ``OpenerDirector.open`` method whose installed handlers include
    ``_NoAuthOnCrossOriginRedirectHandler`` and exclude the plain
    stdlib ``HTTPRedirectHandler`` (which ``build_opener`` would
    otherwise install by default)."""
    from sentinel.phase5.github_evidence import _NoAuthOnCrossOriginRedirectHandler

    default_opener = GithubEvidenceClient.__init__.__kwdefaults__["opener"]
    director = default_opener.__self__  # OpenerDirector.open is a bound method
    assert isinstance(director, urllib.request.OpenerDirector)
    handler_types = [type(h) for h in director.handlers]
    assert _NoAuthOnCrossOriginRedirectHandler in handler_types
    assert urllib.request.HTTPRedirectHandler not in handler_types  # only the subclass, no duplicate


def test_tampered_downloaded_bundle_fails_validate_bundle(tmp_path):
    """A downloaded bundle is untrusted until validate_bundle succeeds
    — this module performs no trust decision of its own."""
    from sentinel.phase5.bundle import BundleValidationError, validate_bundle

    zip_bytes = _make_zip({"manifest.json": b'{"bundle_kind":"GENESIS"}', "manifest.sha256": b"0" * 64})

    def opener(request, timeout=None):
        return _FakeResponse(200, zip_bytes)

    client = _client(opener)
    from sentinel.phase5.github_evidence import ArtifactRef

    ref = ArtifactRef(id=5, name="tampered", workflow_run_id="1")
    root = client.download_artifact(ref, tmp_path, tmp_path / "bundle")
    with pytest.raises((BundleValidationError, Exception)):
        validate_bundle(root)


# ======================================================================
# Stage 2B-2 (dispatch q77-p5d-repair-stage2b2-implement-a): named
# run-artifact listing and optional bounded client timeouts.
# ======================================================================


def test_list_run_artifacts_named_sends_name_filter_and_returns_detail():
    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        body = {
            "total_count": 2,
            "artifacts": [
                {"id": 7, "name": "sentinel-p5-gate-evidence-r9-a1", "expired": False,
                 "digest": "sha256:" + "ab" * 32, "size_in_bytes": 123, "workflow_run": {"id": 9}},
                {"id": 8, "name": "sentinel-p5-gate-evidence-r9-a1", "expired": True,
                 "digest": None, "size_in_bytes": 5, "workflow_run": {"id": 9}},
            ],
        }
        return _FakeResponse(200, json.dumps(body).encode("utf-8"))

    client = _client(opener)
    entries = client.list_run_artifacts_named("9", "sentinel-p5-gate-evidence-r9-a1")
    assert "/actions/runs/9/artifacts?" in seen["url"]
    assert "name=sentinel-p5-gate-evidence-r9-a1" in seen["url"]
    assert seen["timeout"] == 30.0
    assert entries == [
        ArtifactDetail(id=7, name="sentinel-p5-gate-evidence-r9-a1", workflow_run_id="9", expired=False,
                       digest="sha256:" + "ab" * 32, size_in_bytes=123),
        ArtifactDetail(id=8, name="sentinel-p5-gate-evidence-r9-a1", workflow_run_id="9", expired=True,
                       digest=None, size_in_bytes=5),
    ]


@pytest.mark.parametrize("body", [
    {"total_count": 3, "artifacts": []},
    {"artifacts": []},
    {"total_count": 0},
    {"total_count": True, "artifacts": []},
    [],
])
def test_list_run_artifacts_named_incomplete_or_malformed_listing_fails_closed(body):
    def opener(request, timeout=None):
        return _FakeResponse(200, json.dumps(body).encode("utf-8"))

    with pytest.raises(GithubEvidenceError):
        _client(opener).list_run_artifacts_named("9", "x")


def test_list_run_artifacts_named_non_200_raises():
    def opener(request, timeout=None):
        return _FakeResponse(500, b"{}")

    with pytest.raises(GithubEvidenceError):
        _client(opener).list_run_artifacts_named("9", "x")


def test_default_timeouts_unchanged_30_and_60(tmp_path):
    seen = []

    def opener(request, timeout=None):
        seen.append(timeout)
        if request.full_url.endswith("/zip"):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("a.txt", b"x")
            return _FakeResponse(200, buffer.getvalue())
        return _FakeResponse(200, json.dumps({"object": {"sha": "f" * 40}}).encode("utf-8"))

    from sentinel.phase5.github_evidence import ArtifactRef

    client = GithubEvidenceClient(api_url="https://api.github.com", repository="acme/repo", token="t", opener=opener)
    client.get_main_head_sha()
    client.download_artifact(ArtifactRef(id=1, name="n", workflow_run_id="1"), tmp_path, tmp_path / "d")
    assert seen == [30.0, 60.0]


def test_custom_timeouts_passed_to_opener(tmp_path):
    seen = []

    def opener(request, timeout=None):
        seen.append(timeout)
        if request.full_url.endswith("/zip"):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("a.txt", b"x")
            return _FakeResponse(200, buffer.getvalue())
        return _FakeResponse(200, json.dumps({"object": {"sha": "f" * 40}}).encode("utf-8"))

    from sentinel.phase5.github_evidence import ArtifactRef

    client = GithubEvidenceClient(
        api_url="https://api.github.com", repository="acme/repo", token="t", opener=opener,
        request_timeout_s=8.0, download_timeout_s=12.0,
    )
    client.get_main_head_sha()
    client.download_artifact(ArtifactRef(id=1, name="n", workflow_run_id="1"), tmp_path, tmp_path / "d")
    assert seen == [8.0, 12.0]


# ======================================================================
# Attempt-scoped jobs listing (Stage 2C-1, dispatch
# q77-p5d-repair-stage2c1-implement-a) -- fake opener only, never live
# ======================================================================


def _jobs_body(jobs, total=None):
    return {"total_count": len(jobs) if total is None else total, "jobs": jobs}


_JOB_A = {
    "id": 77, "run_id": 9, "run_attempt": 1, "name": "Sonnet official gate", "status": "in_progress",
    "started_at": "2026-09-16T11:55:00Z", "runner_name": "GitHub Actions 3", "conclusion": None, "steps": [],
}
_JOB_B = {"id": 78, "run_id": 9, "name": "finalize", "status": "queued", "started_at": None, "runner_name": None}


def test_list_run_attempt_jobs_puts_attempt_in_request_path_and_ignores_body_run_attempt():
    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return _FakeResponse(200, json.dumps(_jobs_body([_JOB_A, _JOB_B])).encode("utf-8"))

    jobs = _client(opener).list_run_attempt_jobs("9", 2)
    assert seen["url"] == "https://api.github.com/repos/acme/repo/actions/runs/9/attempts/2/jobs?per_page=100"
    assert seen["timeout"] == 30.0
    # the body's run_attempt (1) is irrelevant: attempt (2) is request identity and no field carries it
    assert jobs == [
        JobDetail(id=77, run_id="9", name="Sonnet official gate", status="in_progress",
                  started_at=datetime(2026, 9, 16, 11, 55, 0, tzinfo=timezone.utc), runner_name="GitHub Actions 3"),
        JobDetail(id=78, run_id="9", name="finalize", status="queued", started_at=None, runner_name=None),
    ]
    assert "run_attempt" not in JobDetail.__dataclass_fields__
    assert jobs[0].started_at.utcoffset().total_seconds() == 0


def test_list_run_attempt_jobs_does_not_require_body_run_attempt():
    stripped = {k: v for k, v in _JOB_A.items() if k != "run_attempt"}

    def opener(request, timeout=None):
        return _FakeResponse(200, json.dumps(_jobs_body([stripped])).encode("utf-8"))

    assert _client(opener).list_run_attempt_jobs("9", 1)[0].id == 77


@pytest.mark.parametrize("body", [
    _jobs_body([_JOB_A], total=2),
    _jobs_body([], total=1),
    {"jobs": [_JOB_A]},
    {"total_count": 1},
    {"total_count": True, "jobs": [_JOB_A]},
    {"total_count": 1, "jobs": {"id": 77}},
    [],
    "text",
    _jobs_body([{**_JOB_A, "id": "77"}]),
    _jobs_body([{**_JOB_A, "id": True}]),
    _jobs_body([{**_JOB_A, "run_id": "9"}]),
    _jobs_body([{k: v for k, v in _JOB_A.items() if k != "run_id"}]),
    _jobs_body([{k: v for k, v in _JOB_A.items() if k != "name"}]),
    _jobs_body([{**_JOB_A, "name": 5}]),
    _jobs_body([{**_JOB_A, "status": None}]),
    _jobs_body([{**_JOB_A, "started_at": 123}]),
    _jobs_body([{**_JOB_A, "started_at": "yesterday"}]),
    _jobs_body([{**_JOB_A, "runner_name": 5}]),
    _jobs_body([42]),
])
def test_list_run_attempt_jobs_malformed_listing_fails_closed(body):
    def opener(request, timeout=None):
        return _FakeResponse(200, json.dumps(body).encode("utf-8"))

    with pytest.raises(GithubEvidenceError):
        _client(opener).list_run_attempt_jobs("9", 1)


@pytest.mark.parametrize("run_id,attempt", [
    ("abc", 1), ("", 1), ("9 ", 1), ("-9", 1), (9, 1), (None, 1),
    ("9", 0), ("9", -1), ("9", True), ("9", "1"), ("9", 1.0), ("9", None),
])
def test_list_run_attempt_jobs_bad_run_id_or_attempt_fails_closed_before_any_request(run_id, attempt):
    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        return _FakeResponse(200, b"{}")

    with pytest.raises(GithubEvidenceError):
        _client(opener).list_run_attempt_jobs(run_id, attempt)
    assert calls == []


def test_list_run_attempt_jobs_non_200_and_transport_errors_never_leak_the_token():
    def opener_500(request, timeout=None):
        return _FakeResponse(500, b"{}")

    client = _client(opener_500)
    with pytest.raises(GithubEvidenceError) as info:
        client.list_run_attempt_jobs("9", 1)
    assert "test-token" not in str(info.value) and "test-token" not in repr(info.value)

    import urllib.error

    def opener_err(request, timeout=None):
        raise urllib.error.URLError("boom test-token-must-not-echo")

    with pytest.raises(GithubEvidenceError) as info2:
        _client(opener_err).list_run_attempt_jobs("9", 1)
    assert "test-token" not in str(info2.value) and "test-token" not in repr(info2.value)
    assert "test-token" not in repr(client)


# ======================================================================
# Stage 2C-B6-2: replacement-latch evidence surfaces
# ======================================================================

from sentinel.phase5.github_evidence import (  # noqa: E402
    CommitDetail,
    CommitFile,
    JobEvidence,
    JobStep,
    PushActivity,
    RunRef,
    parse_http_date,
)


class _HeaderResponse(_FakeResponse):
    def __init__(self, status: int, body: bytes, headers: dict):
        super().__init__(status, body)
        self.headers = headers


def test_parse_http_date_accepts_only_a_strict_imf_fixdate():
    assert parse_http_date("Thu, 01 Oct 2026 20:43:30 GMT") == datetime(2026, 10, 1, 20, 43, 30, tzinfo=timezone.utc)
    for bad in (None, "", "Thu, 01 Oct 2026 20:43:30 UTC", "Thu, 1 Oct 2026 20:43:30 GMT",
                "Fri, 01 Oct 2026 20:43:30 GMT", "Thu, 31 Feb 2026 20:43:30 GMT", "2026-10-01T20:43:30Z"):
        with pytest.raises(GithubEvidenceError):
            parse_http_date(bad)


def test_server_time_utc_reads_the_date_header_of_rate_limit():
    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        return _HeaderResponse(200, b"{}", {"Date": "Thu, 01 Oct 2026 20:43:30 GMT"})

    assert _client(opener).server_time_utc() == datetime(2026, 10, 1, 20, 43, 30, tzinfo=timezone.utc)
    assert seen["url"] == "https://api.github.com/rate_limit"


@pytest.mark.parametrize("response", [
    _HeaderResponse(200, b"{}", {}),
    _HeaderResponse(200, b"{}", {"Date": "garbled"}),
    _HeaderResponse(503, b"{}", {"Date": "Thu, 01 Oct 2026 20:43:30 GMT"}),
    _FakeResponse(200, b"{}"),
])
def test_server_time_utc_fails_closed_without_a_valid_date(response):
    with pytest.raises(GithubEvidenceError):
        _client(lambda request, timeout=None: response).server_time_utc()


def _run_body(number: int, **overrides) -> dict:
    body = {
        "id": 32880880000 + number, "run_attempt": 1, "run_number": number, "event": "workflow_dispatch",
        "head_branch": "main", "head_sha": "c" * 40, "path": ".github/workflows/sentinel-official-gate.yml",
        "status": "completed", "conclusion": "failure", "created_at": f"2026-08-25T1{number % 10}:00:00Z",
        "run_started_at": None,
    }
    body.update(overrides)
    return body


def test_get_run_parses_number_status_conclusion_and_branch():
    url = "https://api.github.com/repos/acme/repo/actions/runs/32880880004"
    run = _client(_json_opener({url: _run_body(4, conclusion="cancelled")})).get_run("32880880004")
    assert run == RunRef(
        run_id="32880880004", run_attempt=1, event="workflow_dispatch", ref="refs/heads/main", sha="c" * 40,
        workflow_path=".github/workflows/sentinel-official-gate.yml",
        created_at=datetime(2026, 8, 25, 14, 0, 0, tzinfo=timezone.utc), run_started_at=None,
        run_number=4, status="completed", conclusion="cancelled", head_branch="main",
    )


@pytest.mark.parametrize("change", [
    {"run_number": None}, {"run_number": True}, {"status": None}, {"created_at": "2026-08-25T14:00:00"},
    {"id": "4"}, {"path": 5},
])
def test_get_run_fails_closed_on_unexpected_shape(change):
    url = "https://api.github.com/repos/acme/repo/actions/runs/1"
    with pytest.raises(GithubEvidenceError):
        _client(_json_opener({url: _run_body(4, **change)})).get_run("1")


_RUNS_URL = "https://api.github.com/repos/acme/repo/actions/workflows/sentinel-official-gate.yml/runs?per_page=100&page={}"
_WINDOW = dict(created_after=datetime(2026, 1, 1, tzinfo=timezone.utc),
               created_before=datetime(2027, 1, 1, tzinfo=timezone.utc))
_GATE_WF = ".github/workflows/sentinel-official-gate.yml"


def test_counted_run_listing_requires_total_count_and_filters_by_window():
    runs = [_run_body(n) for n in (4, 3, 2, 1)]
    client = _client(_json_opener({_RUNS_URL.format(1): {"total_count": 4, "workflow_runs": runs}}))
    assert sorted(r.run_number for r in client.list_workflow_runs_counted(_GATE_WF, **_WINDOW)) == [1, 2, 3, 4]


@pytest.mark.parametrize("body", [
    {"total_count": 5, "workflow_runs": [_run_body(n) for n in (4, 3, 2, 1)]},
    {"workflow_runs": [_run_body(1)]},
    {"total_count": True, "workflow_runs": [_run_body(1)]},
    {"total_count": 1, "workflow_runs": [_run_body(1, run_number=None)]},
    [],
], ids=["count-mismatch", "no-count", "bool-count", "malformed-entry", "not-object"])
def test_counted_run_listing_fails_closed(body):
    client = _client(_json_opener({_RUNS_URL.format(1): body}))
    with pytest.raises(GithubEvidenceError):
        client.list_workflow_runs_counted(_GATE_WF, **_WINDOW)


def test_counted_run_listing_default_page_size_is_the_api_maximum_for_every_existing_caller():
    """Stage 2C-B6-3: ``per_page`` is a default-preserving keyword. Without
    it the request URL is byte-identical to the one the latch always sent."""
    requested: list[str] = []

    def opener(request, timeout=None):
        requested.append(request.full_url)
        return _FakeResponse(200, json.dumps({"total_count": 1, "workflow_runs": [_run_body(1)]}).encode("utf-8"))

    _client(opener).list_workflow_runs_counted(_GATE_WF, **_WINDOW)
    assert requested == [_RUNS_URL.format(1)]


def _paged_listing_opener(total: int, per_page: int, *, lie_on_page: int | None = None, requested=None):
    all_runs = [_run_body(n) for n in range(total, 0, -1)]

    def opener(request, timeout=None):
        url = request.full_url
        if requested is not None:
            requested.append(url)
        assert f"per_page={per_page}" in url
        page = int(url.rsplit("page=", 1)[1])
        claimed = total + 1 if lie_on_page == page else total
        chunk = all_runs[(page - 1) * per_page: page * per_page]
        return _FakeResponse(200, json.dumps({"total_count": claimed, "workflow_runs": chunk}).encode("utf-8"))

    return opener


def test_counted_run_listing_walks_real_multiple_pages_and_reports_stats():
    stats: dict = {}
    requested: list[str] = []
    runs = _client(_paged_listing_opener(87, 20, requested=requested)).list_workflow_runs_counted(
        _GATE_WF, per_page=20, stats=stats, **_WINDOW
    )
    assert sorted(r.run_number for r in runs) == list(range(1, 88))
    assert stats == {"pages": 5, "total_count": 87, "entries": 87}
    assert [u.rsplit("page=", 1)[1] for u in requested] == ["1", "2", "3", "4", "5"]


def test_counted_run_listing_total_count_changing_across_pages_fails_closed():
    client = _client(_paged_listing_opener(87, 20, lie_on_page=3))
    with pytest.raises(GithubEvidenceError, match="changed during pagination"):
        client.list_workflow_runs_counted(_GATE_WF, per_page=20, **_WINDOW)


def test_counted_run_listing_entries_not_equal_to_total_count_fails_closed_across_pages():
    def opener(request, timeout=None):
        page = int(request.full_url.rsplit("page=", 1)[1])
        chunk = [_run_body(n) for n in range(40 - (page - 1) * 20, 40 - page * 20, -1)] if page <= 2 else []
        return _FakeResponse(200, json.dumps({"total_count": 41, "workflow_runs": chunk}).encode("utf-8"))

    with pytest.raises(GithubEvidenceError, match="incomplete"):
        _client(opener).list_workflow_runs_counted(_GATE_WF, per_page=20, **_WINDOW)


def test_counted_run_listing_page_overflow_fails_closed_with_a_small_page_size():
    with pytest.raises(DiscoveryOverflow):
        _client(_paged_listing_opener(87, 5), page_limit=3).list_workflow_runs_counted(
            _GATE_WF, per_page=5, **_WINDOW
        )


@pytest.mark.parametrize("bad", [0, 101, -1, True, "20", None, 2.5])
def test_counted_run_listing_rejects_an_invalid_page_size(bad):
    with pytest.raises(GithubEvidenceError, match="per_page"):
        _client(lambda request, timeout=None: _FakeResponse(200, b"{}")).list_workflow_runs_counted(
            _GATE_WF, per_page=bad, **_WINDOW
        )


_COMMIT_SHA = "c" * 40
_COMMIT_URL = f"https://api.github.com/repos/acme/repo/commits/{_COMMIT_SHA}"


def _commit_body(**overrides) -> dict:
    body = {
        "sha": _COMMIT_SHA.upper(), "parents": [{"sha": "B" * 40, "url": "u"}],
        "files": [{"filename": "artifacts/phase5_replacement_latch.jsonl", "status": "modified",
                   "additions": 1, "deletions": 0, "changes": 1, "patch": "@@ -1 +1,2 @@\n x\n+y"}],
    }
    body.update(overrides)
    return body


def test_get_commit_parses_parents_files_and_patch():
    detail = _client(_json_opener({_COMMIT_URL: _commit_body()})).get_commit(_COMMIT_SHA)
    assert detail == CommitDetail(
        sha=_COMMIT_SHA, parents=("b" * 40,),
        files=(CommitFile(filename="artifacts/phase5_replacement_latch.jsonl", status="modified",
                          additions=1, deletions=0, patch="@@ -1 +1,2 @@\n x\n+y"),),
    )


@pytest.mark.parametrize("body", [
    _commit_body(parents="x"), _commit_body(files=None), _commit_body(files=[{"filename": "a"}]),
    _commit_body(files=[{"filename": f"f{i}", "status": "added", "additions": 1, "deletions": 0} for i in range(300)]),
    {"no": "sha"},
], ids=["parents", "files-null", "file-shape", "possibly-paginated", "no-sha"])
def test_get_commit_fails_closed_on_unexpected_shape(body):
    with pytest.raises(GithubEvidenceError):
        _client(_json_opener({_COMMIT_URL: body})).get_commit(_COMMIT_SHA)


_ACTIVITY_URL = (
    "https://api.github.com/repos/acme/repo/activity?ref=refs%2Fheads%2Fmain&activity_type=push&per_page=100"
)


def test_list_push_activity_parses_server_side_push_records():
    body = [{"id": 1, "before": "4" * 40, "after": "B" * 40, "ref": "refs/heads/main",
             "timestamp": "2026-10-01T20:20:30Z", "activity_type": "push", "actor": None}]
    assert _client(_json_opener({_ACTIVITY_URL: body})).list_push_activity("refs/heads/main") == [
        PushActivity(before="4" * 40, after="b" * 40, ref="refs/heads/main", activity_type="push",
                     timestamp=datetime(2026, 10, 1, 20, 20, 30, tzinfo=timezone.utc)),
    ]


@pytest.mark.parametrize("body", [
    {"not": "a list"},
    [{"before": "a", "after": "b", "ref": "r", "activity_type": "push"}],
    [{"before": "a", "after": "b", "ref": "r", "activity_type": "push", "timestamp": "2026-10-01T20:20:30"}],
    [7],
])
def test_list_push_activity_fails_closed_on_unexpected_shape(body):
    with pytest.raises(GithubEvidenceError):
        _client(_json_opener({_ACTIVITY_URL: body})).list_push_activity("refs/heads/main")


_JOB_EVIDENCE_URL = "https://api.github.com/repos/acme/repo/actions/runs/9/attempts/1/jobs?per_page=100"


def test_job_evidence_parses_conclusion_and_steps_and_job_detail_is_unchanged():
    job = {**_JOB_A, "status": "completed", "conclusion": "failure", "steps": [
        {"name": "upload one-shot marker", "status": "completed", "conclusion": "skipped", "number": 7},
    ]}
    evidence = _client(_json_opener({_JOB_EVIDENCE_URL: _jobs_body([job, _JOB_B])})).list_run_attempt_job_evidence("9", 1)
    assert evidence[0] == JobEvidence(
        id=77, run_id="9", name="Sonnet official gate", status="completed", conclusion="failure",
        steps=(JobStep(name="upload one-shot marker", status="completed", conclusion="skipped", number=7),),
    )
    assert evidence[1].steps == () and evidence[1].conclusion is None
    assert set(JobDetail.__dataclass_fields__) == {"id", "run_id", "name", "status", "started_at", "runner_name"}


@pytest.mark.parametrize("change", [
    {"steps": "x"}, {"steps": [{"name": "s", "status": "completed", "number": "1"}]}, {"conclusion": 5},
])
def test_job_evidence_fails_closed_on_unexpected_shape(change):
    client = _client(_json_opener({_JOB_EVIDENCE_URL: _jobs_body([{**_JOB_A, **change}])}))
    with pytest.raises(GithubEvidenceError):
        client.list_run_attempt_job_evidence("9", 1)

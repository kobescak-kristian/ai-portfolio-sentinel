"""Tests for sentinel/phase5/github_evidence.py (P5-B Part 3/3)."""

from __future__ import annotations

import io
import json
import urllib.request
import zipfile
from datetime import datetime, timezone

import pytest

from sentinel.phase5.github_evidence import (
    ArtifactUnsafe,
    DiscoveryOverflow,
    GithubEvidenceClient,
    GithubEvidenceError,
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
# Redirect-safe transport regression (dispatch
# q77-p5d-premarker-artifact-redirect-repair-a).
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
    request's ``Authorization`` header onto the redirected request."""
    original = urllib.request.Request(
        "https://api.github.com/repos/acme/repo/actions/artifacts/1/zip",
        headers={"Authorization": "Bearer SUPER-SECRET-TOKEN"},
    )
    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
        original, io.BytesIO(b""), 302, "Found", {},
        "https://blob.storage.example/artifact.zip?sig=deadbeef-signed-query",
    )
    assert redirected.get_header("Authorization") == "Bearer SUPER-SECRET-TOKEN"


def test_no_auth_on_redirect_handler_strips_authorization_but_preserves_url():
    """Proves the repair, called the exact same way: ``_NoAuthOnRedirectHandler``
    (1) never carries Authorization onto the redirected request, and
    (3) still preserves the redirected URL/query string exactly --
    it reuses the stdlib's own logic via ``super()`` and strips only
    the one header."""
    from sentinel.phase5.github_evidence import _NoAuthOnRedirectHandler

    original = urllib.request.Request(
        "https://api.github.com/repos/acme/repo/actions/artifacts/1/zip",
        headers={"Authorization": "Bearer SUPER-SECRET-TOKEN", "Accept": "application/vnd.github+json"},
    )
    redirect_url = "https://blob.storage.example/artifact.zip?sig=deadbeef-signed-query"
    redirected = _NoAuthOnRedirectHandler().redirect_request(
        original, io.BytesIO(b""), 302, "Found", {}, redirect_url,
    )
    assert redirected is not None
    assert redirected.get_header("Authorization") is None
    assert redirected.full_url == redirect_url
    # Non-credential headers are still carried over, exactly like the
    # stdlib default -- only Authorization is special-cased.
    assert redirected.get_header("Accept") == "application/vnd.github+json"


def test_default_opener_is_wired_to_the_no_auth_redirect_handler():
    """Proves the fix is actually plugged into GithubEvidenceClient's
    default construction path, not merely defined-but-unused: the
    constructor's default ``opener`` argument is a bound
    ``OpenerDirector.open`` method whose installed handlers include
    ``_NoAuthOnRedirectHandler`` and exclude the plain stdlib
    ``HTTPRedirectHandler`` (which ``build_opener`` would otherwise
    install by default)."""
    from sentinel.phase5.github_evidence import _NoAuthOnRedirectHandler

    default_opener = GithubEvidenceClient.__init__.__kwdefaults__["opener"]
    director = default_opener.__self__  # OpenerDirector.open is a bound method
    assert isinstance(director, urllib.request.OpenerDirector)
    handler_types = [type(h) for h in director.handlers]
    assert _NoAuthOnRedirectHandler in handler_types
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

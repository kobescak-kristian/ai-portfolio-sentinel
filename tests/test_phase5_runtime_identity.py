"""Tests for sentinel/phase5/runtime_identity.py (ADR-0012 Amendment A8;
dispatch q77-p5d-repair-stage2c2-implement-a, Stage 2C-2).

Model-free and provider-free: the SDK is never imported by the module
under test and no CLI is executed. Fake installations are built as real
dist-info trees under ``tmp_path``. Authority is per runtime: every
assertion about hashes is self-consistency against the installation's
own RECORD, never a comparison with another platform's wheel.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import importlib.metadata
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agents.checker import harness
from agents.checker.budget import Reservation
from sentinel.phase5 import runtime_identity as ri
from sentinel.phase5.models import canonical_json_bytes

REPO_ROOT = Path(__file__).resolve().parent.parent
LINUX_ONLY = pytest.mark.skipif(sys.platform != "linux", reason="Linux wheel identity is only meaningful on Linux")

FAKE_TRANSPORT = '''
import platform
import shutil
from pathlib import Path


class SubprocessCLITransport:
    def _find_cli(self) -> str:
        """Find Claude Code CLI binary."""
        bundled_cli = self._find_bundled_cli()
        if bundled_cli:
            return bundled_cli
        if cli := shutil.which("claude"):
            return cli
        raise RuntimeError("not found")

    def _find_bundled_cli(self):
        cli_name = "claude.exe" if platform.system() == "Windows" else "claude"
        bundled_path = Path(__file__).parent.parent.parent / "_bundled" / cli_name
        if bundled_path.exists() and bundled_path.is_file():
            return str(bundled_path)
        return None
'''


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _install(tmp_path: Path, *, platform_system="Linux", version="0.2.110", transport=FAKE_TRANSPORT,
             cli_bytes=b"\x7fELF fake cli bytes", wheel_tags=("py3-none-manylinux_2_17_x86_64",)):
    site = tmp_path / "site"
    files = {
        "claude_agent_sdk/__init__.py": b"",
        ri.TRANSPORT_RECORD_PATH: transport.encode("utf-8"),
        ri.CLI_VERSION_RECORD_PATH: b'"""Bundled Claude Code CLI version."""\n\n__cli_version__ = "9.9.9"\n',
        ri.bundled_cli_record_path(platform_system): cli_bytes,
    }
    lines = []
    for rel, data in files.items():
        target = site / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        lines.append(f"{rel},sha256={_b64(data)},{len(data)}")
    dist_info = site / f"claude_agent_sdk-{version}.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(f"Metadata-Version: 2.4\nName: claude-agent-sdk\nVersion: {version}\n", encoding="utf-8")
    (dist_info / "WHEEL").write_text("Wheel-Version: 1.0\n" + "".join(f"Tag: {t}\n" for t in wheel_tags), encoding="utf-8")
    lines.append(f"claude_agent_sdk-{version}.dist-info/METADATA,,")
    lines.append(f"claude_agent_sdk-{version}.dist-info/RECORD,,")
    (dist_info / "RECORD").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return importlib.metadata.PathDistribution(dist_info), site


def _capture(dist, platform_system="Linux", env=None, installed=()):
    return ri.capture_runtime_identity(env=env or {}, platform_system=platform_system, sdk_distribution=dist,
                                       installed_distributions=installed)


@pytest.fixture(scope="module")
def installed_identity():
    return ri.capture_runtime_identity(env={})


# ======================================================================
# Per-runtime RECORD self-consistency
# ======================================================================


def test_fake_linux_installation_is_self_consistent_against_its_own_record(tmp_path):
    dist, _ = _install(tmp_path)
    identity = _capture(dist)
    sdk = identity.sdk
    assert sdk.bundled_cli.record_path == "claude_agent_sdk/_bundled/claude"
    assert sdk.bundled_cli.sha256_actual == sdk.bundled_cli.sha256_declared == hashlib.sha256(b"\x7fELF fake cli bytes").hexdigest()
    assert sdk.transport_module.sha256_actual == sdk.transport_module.sha256_declared
    assert sdk.cli_selection == "BUNDLED_FIRST" and sdk.bundled_cli.cli_version_declared == "9.9.9"
    assert sdk.wheel_tags == ("py3-none-manylinux_2_17_x86_64",)
    record = (tmp_path / "site" / "claude_agent_sdk-0.2.110.dist-info" / "RECORD").read_bytes()
    assert sdk.record_sha256 == hashlib.sha256(record).hexdigest()
    assert sdk.bundled_cli.size_bytes == len(b"\x7fELF fake cli bytes")


def test_windows_installation_uses_the_exe_name_and_no_executable_bit(tmp_path):
    dist, _ = _install(tmp_path, platform_system="Windows", wheel_tags=("py3-none-win_amd64",))
    sdk = _capture(dist, platform_system="Windows").sdk
    assert sdk.bundled_cli.record_path == "claude_agent_sdk/_bundled/claude.exe"
    assert sdk.bundled_cli.is_executable is None


@pytest.mark.parametrize("rel", [ri.TRANSPORT_RECORD_PATH, "claude_agent_sdk/_bundled/claude"])
def test_bytes_differing_from_their_own_record_fail(tmp_path, rel):
    dist, site = _install(tmp_path)
    (site / rel).write_bytes((site / rel).read_bytes() + b"tampered")
    with pytest.raises(ri.RuntimeIdentityError):
        _capture(dist)


def test_missing_cli_fails(tmp_path):
    dist, site = _install(tmp_path)
    (site / "claude_agent_sdk" / "_bundled" / "claude").unlink()
    with pytest.raises(ri.RuntimeIdentityError):
        _capture(dist)


def test_cli_for_the_wrong_platform_name_fails(tmp_path):
    dist, _ = _install(tmp_path, platform_system="Linux")
    with pytest.raises(ri.RuntimeIdentityError):
        _capture(dist, platform_system="Windows")  # RECORD lists claude, not claude.exe


def test_cli_that_is_not_a_regular_file_or_is_a_symlink_fails(tmp_path):
    dist, site = _install(tmp_path)
    cli = site / "claude_agent_sdk" / "_bundled" / "claude"
    cli.unlink()
    cli.mkdir()
    with pytest.raises(ri.RuntimeIdentityError):
        _capture(dist)
    cli.rmdir()
    real = site / "elsewhere"
    real.write_bytes(b"\x7fELF fake cli bytes")
    try:
        os.symlink(real, cli)
    except OSError:
        symlink_supported = False  # Windows without symlink privilege; Linux CI always exercises this
    else:
        symlink_supported = True
    if symlink_supported:
        with pytest.raises(ri.RuntimeIdentityError):
            _capture(dist)
    assert symlink_supported or sys.platform != "linux"


def test_sdk_not_installed_fails(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(ri.importlib.metadata, "distribution", missing)
    with pytest.raises(ri.RuntimeIdentityError):
        ri.capture_runtime_identity(env={})


def test_record_digest_mismatch_is_unconstructible_in_the_schema():
    good = "a" * 64
    with pytest.raises(ValidationError):
        ri.RecordedFile(record_path=ri.TRANSPORT_RECORD_PATH, sha256_actual=good, sha256_declared="b" * 64)
    for bad_path in ("/abs/claude", "C:/x/claude", "claude_agent_sdk\\_bundled\\claude", "a/../b", ""):
        with pytest.raises(ValidationError):
            ri.RecordedFile(record_path=bad_path, sha256_actual=good, sha256_declared=good)


# ======================================================================
# Bundled-CLI selection, proven structurally per runtime
# ======================================================================


def test_installed_transport_source_selects_bundled_cli_first():
    dist = importlib.metadata.distribution("claude-agent-sdk")
    entry = next(f for f in dist.files if str(f).replace("\\", "/") == ri.TRANSPORT_RECORD_PATH)
    source = Path(dist.locate_file(entry)).read_text(encoding="utf-8")
    assert ri.verify_bundled_cli_selection(source) == "BUNDLED_FIRST"


@pytest.mark.parametrize("mutation", [
    lambda s: s.replace('        bundled_cli = self._find_bundled_cli()\n        if bundled_cli:\n            return bundled_cli\n',
                        '        if cli := shutil.which("claude"):\n            return cli\n        bundled_cli = self._find_bundled_cli()\n        if bundled_cli:\n            return bundled_cli\n'),
    lambda s: s.replace('"""Find Claude Code CLI binary."""', '"""Find."""\n        import os\n        os.environ.get("CLAUDE_CLI")'),
    lambda s: s.replace('/ "_bundled" /', '/ "_elsewhere" /'),
    lambda s: s.replace("class SubprocessCLITransport", "class OtherTransport"),
    lambda s: s + "\nthis is not python(",
])
def test_altered_transport_source_fails_selection_proof(tmp_path, mutation):
    altered = mutation(FAKE_TRANSPORT)
    assert altered != FAKE_TRANSPORT
    with pytest.raises(ri.RuntimeIdentityError):
        ri.verify_bundled_cli_selection(altered)
    dist, _ = _install(tmp_path, transport=altered)
    with pytest.raises(ri.RuntimeIdentityError):
        _capture(dist)


def test_harness_options_leave_cli_selection_to_the_bundled_default():
    options = harness.build_options("missing-synthetic-label", Reservation(reserved_eur_micros=1, sdk_max_budget_usd=0.01))
    assert options.cli_path is None
    assert options.env == {}


# ======================================================================
# The installed runtime (Windows locally, Linux on CI)
# ======================================================================


def test_installed_runtime_is_self_consistent_and_pinned(installed_identity):
    sdk = installed_identity.sdk
    assert ri.sdk_pin_matches(installed_identity)
    assert sdk.transport_module.sha256_actual == sdk.transport_module.sha256_declared
    assert sdk.bundled_cli.sha256_actual == sdk.bundled_cli.sha256_declared
    assert sdk.bundled_cli.record_path == ri.bundled_cli_record_path("Windows" if sys.platform == "win32" else "Linux")
    assert sdk.cli_selection == "BUNDLED_FIRST"
    assert installed_identity.sys_platform == sys.platform


def test_runtime_identity_id_is_canonical_and_stable(installed_identity):
    again = ri.capture_runtime_identity(env={})
    assert again.runtime_identity_id == installed_identity.runtime_identity_id
    assert len(installed_identity.runtime_identity_id) == 64
    rebuilt = ri.RuntimeIdentity.model_validate(installed_identity.model_dump())
    assert canonical_json_bytes(rebuilt) == canonical_json_bytes(installed_identity)
    assert rebuilt.runtime_identity_id == hashlib.sha256(canonical_json_bytes(installed_identity)).hexdigest()


@LINUX_ONLY
def test_linux_runtime_identity_is_the_linux_wheel(installed_identity):
    sdk = installed_identity.sdk
    assert sdk.bundled_cli.record_path == "claude_agent_sdk/_bundled/claude"
    assert sdk.bundled_cli.is_executable is True
    assert sdk.wheel_tags and not any("win" in tag for tag in sdk.wheel_tags)
    assert installed_identity.sys_platform == "linux"


def test_no_windows_wheel_hash_is_used_as_an_oracle(tmp_path):
    windows_transport = "59bc8db2" + "071a1a65248bddfbc96299f1c698f21109c244ede5c28fb837df8059"
    windows_cli = "83397a6a" + "029c7da663fb1ce27211e05174a3546d8b151e42451bf4590b8343d7"
    for path in (REPO_ROOT / "sentinel" / "phase5" / "runtime_identity.py",
                 REPO_ROOT / "agents" / "checker" / "process_control.py",
                 REPO_ROOT / "agents" / "checker" / "envelope_guard.py",
                 Path(__file__)):
        text = path.read_text(encoding="utf-8")
        assert windows_transport not in text and windows_cli not in text
        assert windows_transport[:8] not in text.replace('"' + windows_transport[:8] + '"', "")
    # A Linux installation with bytes unlike any Windows artifact validates on its own RECORD alone.
    dist, _ = _install(tmp_path, transport=FAKE_TRANSPORT + "\n# linux build\n")
    identity = _capture(dist)
    assert identity.sdk.transport_module.sha256_actual not in (windows_transport,)
    assert identity.sdk.bundled_cli.sha256_actual != windows_cli


# ======================================================================
# Determinism, allowlisting and no secret or absolute-path leakage
# ======================================================================


def test_distribution_set_is_normalized_sorted_and_deduplicated(tmp_path):
    dist, _ = _install(tmp_path)

    def d(name, version):
        return SimpleNamespace(metadata={"Name": name}, version=version)

    installed = [d("Zeta_Pkg", "1.0"), d("alpha.pkg", "2.0"), d("zeta-pkg", "1.0"), d("", "1.0"),
                 SimpleNamespace(metadata=None, version="1.0")]
    first = _capture(dist, installed=installed)
    second = _capture(dist, installed=list(reversed(installed)))
    assert first.distributions == (("alpha-pkg", "2.0"), ("zeta-pkg", "1.0"))
    assert first.runtime_identity_id == second.runtime_identity_id
    with pytest.raises(ValidationError):
        ri.RuntimeIdentity.model_validate({**first.model_dump(), "distributions": (("b", "1"), ("a", "1"))})


def test_only_allowlisted_runner_variables_are_read_and_no_secret_leaks(tmp_path):
    dist, _ = _install(tmp_path)
    canary = "CANARY-SECRET-DO-NOT-LEAK"
    env = {
        "GITHUB_TOKEN": canary, "ACTIONS_ID_TOKEN_REQUEST_TOKEN": canary, "ANTHROPIC_API_KEY": canary,
        "ANTHROPIC_IDENTITY_TOKEN_FILE": "/tmp/" + canary, "HOME": "/home/" + canary,
        "ImageOS": "ubuntu24", "ImageVersion": "20260914.1", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
        "RUNNER_ENVIRONMENT": "github-hosted",
    }
    identity = _capture(dist, env=env)
    assert identity.runner.model_dump() == {
        "image_os": "ubuntu24", "image_version": "20260914.1", "runner_os": "Linux",
        "runner_arch": "X64", "runner_environment": "github-hosted",
    }
    assert canary.encode() not in canonical_json_bytes(identity)
    assert _capture(dist).runner.model_dump() == dict.fromkeys(identity.runner.model_dump())
    tree = ast.parse((REPO_ROOT / "sentinel" / "phase5" / "runtime_identity.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        assert not (isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"))


def test_installed_identity_contains_no_absolute_local_path(installed_identity):
    data = canonical_json_bytes(installed_identity)
    for fragment in (str(Path.home()), "site-packages", ":\\\\", ":/", "/home/", "/usr/", "/opt/", "/tmp/"):
        assert fragment.encode() not in data, fragment

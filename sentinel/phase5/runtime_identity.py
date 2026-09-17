"""P5-D runtime identity (ADR-0012 Amendment A8; dispatch
q77-p5d-repair-stage2c2-implement-a, Stage 2C-2).

Captures, without any provider call and without importing the Agent SDK,
the runtime facts later readiness binds: interpreter and platform, an
allowlisted set of GitHub runner-image variables, the fully resolved
installed distribution set, and the pinned ``claude-agent-sdk``
installation's own identity (version, wheel tags, RECORD digest, the
subprocess-transport module and the bundled CLI executable).

Authority is per runtime. Every recorded file must hash to the value its
OWN installed distribution's RECORD declares; there is no cross-platform
oracle, and a Windows wheel's hashes never validate a Linux installation
(the platform wheels are different artifacts). The Linux
``runtime_identity_id`` is bound later from the actual GitHub runner or
governed rehearsal environment; a Windows capture is local evidence only.

Bundled-CLI selection is proven structurally on each runtime by parsing
that runtime's installed transport source with ``ast`` (never importing
it): ``_find_cli`` must return ``self._find_bundled_cli()`` before any
``shutil.which`` fallback and read no environment variable, and
``_find_bundled_cli`` must resolve ``Path(__file__).parent.parent.parent
/ "_bundled" / cli_name``.

Out of scope here: the provider-resolved model identifier (requires
provider execution; a readiness decision) and before/after CLI hash
equality around a real CLI run (kill rehearsal / readiness).

No secret is read: only five allowlisted runner-image variables are
taken from the supplied environment mapping. No absolute path is
recorded: every file path is the RECORD-relative path.

stdlib + pydantic only.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import importlib.metadata
import os
import platform
import re
import stat
import sys
from pathlib import Path
from typing import Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .execution_envelope import REHEARSAL_SDK_PIN
from .models import canonical_json_bytes

SDK_DISTRIBUTION = "claude-agent-sdk"
TRANSPORT_RECORD_PATH = "claude_agent_sdk/_internal/transport/subprocess_cli.py"
CLI_VERSION_RECORD_PATH = "claude_agent_sdk/_cli_version.py"
MAX_DISTRIBUTIONS = 4096
MAX_FIELD_CHARS = 128

RUNNER_IMAGE_ENV_KEYS: tuple[tuple[str, str], ...] = (
    ("image_os", "ImageOS"),
    ("image_version", "ImageVersion"),
    ("runner_os", "RUNNER_OS"),
    ("runner_arch", "RUNNER_ARCH"),
    ("runner_environment", "RUNNER_ENVIRONMENT"),
)

_HEX64 = re.compile(r"[0-9a-f]{64}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_CLI_VERSION = re.compile(r"""__cli_version__\s*=\s*["']([0-9A-Za-z.+-]{1,64})["']""")
_BUNDLED_PATH_EXPR = "Path(__file__).parent.parent.parent / '_bundled' / cli_name"
_HASH_CHUNK = 1024 * 1024


class RuntimeIdentityError(RuntimeError):
    """Runtime identity could not be established fail-closed. Never
    carries an absolute local path or an environment value."""


def bundled_cli_record_path(platform_system: str) -> str:
    """The SDK's own naming rule: ``claude.exe`` on Windows, else ``claude``."""
    name = "claude.exe" if platform_system == "Windows" else "claude"
    return f"claude_agent_sdk/_bundled/{name}"


def _bounded_text(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not value or len(value) > MAX_FIELD_CHARS or _CONTROL.search(value):
        raise ValueError(f"{label} must be a non-empty bounded single-line string")
    return value


def _require_relative_record_path(value: str) -> str:
    if (
        not value or value.startswith("/") or "\\" in value or ":" in value
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValueError("record_path must be a normalized relative RECORD path")
    return value


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class RecordedFile(BaseModel):
    """A file of the installed SDK distribution, with its actual digest
    and the digest its own RECORD declares. They must be equal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_path: str
    sha256_actual: str
    sha256_declared: str

    @model_validator(mode="after")
    def _validate(self) -> "RecordedFile":
        _require_relative_record_path(self.record_path)
        for name in ("sha256_actual", "sha256_declared"):
            if not _HEX64.fullmatch(getattr(self, name)):
                raise ValueError(f"{name} must be exactly 64 lowercase hexadecimal characters")
        if self.sha256_actual != self.sha256_declared:
            raise ValueError("actual digest differs from the installed distribution's RECORD digest")
        return self


class BundledCli(RecordedFile):
    size_bytes: StrictInt = Field(ge=0)
    is_executable: bool | None
    cli_version_declared: str | None


class RunnerImage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    image_os: str | None = None
    image_version: str | None = None
    runner_os: str | None = None
    runner_arch: str | None = None
    runner_environment: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "RunnerImage":
        for field_name, _env_key in RUNNER_IMAGE_ENV_KEYS:
            _bounded_text(getattr(self, field_name), field_name)
        return self


class SdkIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    distribution_name: Literal["claude-agent-sdk"]
    version: str
    wheel_tags: tuple[str, ...]
    record_sha256: str
    transport_module: RecordedFile
    bundled_cli: BundledCli
    cli_selection: Literal["BUNDLED_FIRST"]

    @model_validator(mode="after")
    def _validate(self) -> "SdkIdentity":
        _bounded_text(self.version, "version")
        for tag in self.wheel_tags:
            _bounded_text(tag, "wheel_tag")
        if not _HEX64.fullmatch(self.record_sha256):
            raise ValueError("record_sha256 must be exactly 64 lowercase hexadecimal characters")
        if self.transport_module.record_path != TRANSPORT_RECORD_PATH:
            raise ValueError("transport_module must be the SDK subprocess transport")
        if not self.bundled_cli.record_path.startswith("claude_agent_sdk/_bundled/"):
            raise ValueError("bundled_cli must live under claude_agent_sdk/_bundled/")
        return self


class RuntimeIdentity(BaseModel):
    """Deterministic, timestamp-free runtime identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    python_version: str
    python_implementation: str
    sys_platform: str
    machine: str
    os_release: str
    runner: RunnerImage
    distributions: tuple[tuple[str, str], ...]
    sdk: SdkIdentity

    @model_validator(mode="after")
    def _validate(self) -> "RuntimeIdentity":
        for name in ("python_version", "python_implementation", "sys_platform", "machine", "os_release"):
            _bounded_text(getattr(self, name), name)
        if len(self.distributions) > MAX_DISTRIBUTIONS:
            raise ValueError("too many installed distributions")
        if list(self.distributions) != sorted(set(self.distributions)):
            raise ValueError("distributions must be sorted and unique")
        for name, version in self.distributions:
            _bounded_text(name, "distribution name")
            _bounded_text(version, "distribution version")
        return self

    @property
    def runtime_identity_id(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self)).hexdigest()


def sdk_pin_matches(identity: RuntimeIdentity) -> bool:
    return f"{identity.sdk.distribution_name}=={identity.sdk.version}" == REHEARSAL_SDK_PIN


# ---------------------------------------------------------------------------
# Structural CLI-selection proof
# ---------------------------------------------------------------------------


def _method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node  # type: ignore[return-value]
    raise RuntimeIdentityError(f"transport method {name} not found")


def _is_self_call(node: ast.AST, attr: str) -> bool:
    return (
        isinstance(node, ast.Call) and not node.args and not node.keywords
        and isinstance(node.func, ast.Attribute) and node.func.attr == attr
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
    )


def _calls_shutil_which(nodes: Iterable[ast.AST]) -> bool:
    for root in nodes:
        for node in ast.walk(root):
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "which" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "shutil"
            ):
                return True
    return False


def verify_bundled_cli_selection(source_text: str) -> Literal["BUNDLED_FIRST"]:
    """Prove from the installed transport source that the bundled CLI is
    selected first, with no environment override. Raises on mismatch."""
    try:
        tree = ast.parse(source_text)
    except SyntaxError as exc:
        raise RuntimeIdentityError("transport source does not parse") from exc
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SubprocessCLITransport"]
    if len(classes) != 1:
        raise RuntimeIdentityError("SubprocessCLITransport not found exactly once")
    find_cli = _method(classes[0], "_find_cli")
    find_bundled = _method(classes[0], "_find_bundled_cli")

    body = list(find_cli.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
        body = body[1:]
    if len(body) < 2:
        raise RuntimeIdentityError("_find_cli does not select the bundled CLI first")
    first, second = body[0], body[1]
    if not (
        isinstance(first, ast.Assign) and len(first.targets) == 1
        and isinstance(first.targets[0], ast.Name) and _is_self_call(first.value, "_find_bundled_cli")
    ):
        raise RuntimeIdentityError("_find_cli does not select the bundled CLI first")
    bound = first.targets[0].id
    if not (
        isinstance(second, ast.If) and isinstance(second.test, ast.Name) and second.test.id == bound
        and len(second.body) == 1 and isinstance(second.body[0], ast.Return)
        and isinstance(second.body[0].value, ast.Name) and second.body[0].value.id == bound
        and not second.orelse
    ):
        raise RuntimeIdentityError("_find_cli does not return the bundled CLI before any fallback")
    if _calls_shutil_which([first, second]):
        raise RuntimeIdentityError("_find_cli consults PATH before the bundled CLI")
    for node in ast.walk(find_cli):
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
            raise RuntimeIdentityError("_find_cli reads the environment")

    if not any(
        isinstance(node, ast.Assign) and ast.unparse(node.value) == _BUNDLED_PATH_EXPR
        for node in ast.walk(find_bundled)
    ):
        raise RuntimeIdentityError("_find_bundled_cli does not resolve the packaged _bundled directory")
    return "BUNDLED_FIRST"


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _record_hex(file) -> str:
    file_hash = getattr(file, "hash", None)
    if file_hash is None or file_hash.mode != "sha256":
        raise RuntimeIdentityError("RECORD declares no sha256 digest for a required SDK file")
    value = file_hash.value
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise RuntimeIdentityError("RECORD digest is not valid base64") from exc
    if len(raw) != 32:
        raise RuntimeIdentityError("RECORD digest is not a sha256 digest")
    return raw.hex()


def _regular_file(path: Path) -> os.stat_result:
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise RuntimeIdentityError("a required SDK file is missing") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise RuntimeIdentityError("a required SDK file is not a regular file")
    return st


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files_by_record_path(distribution) -> dict[str, object]:
    files = distribution.files
    if not files:
        raise RuntimeIdentityError("the SDK distribution has no RECORD file list")
    return {str(file).replace("\\", "/"): file for file in files}


def _recorded(files: Mapping[str, object], distribution, record_path: str) -> tuple[RecordedFile, Path, os.stat_result]:
    file = files.get(record_path)
    if file is None:
        raise RuntimeIdentityError("a required SDK file is not listed in its RECORD")
    declared = _record_hex(file)
    location = Path(distribution.locate_file(file))
    st = _regular_file(location)
    recorded = {"record_path": record_path, "sha256_actual": _sha256_file(location), "sha256_declared": declared}
    if recorded["sha256_actual"] != declared:
        raise RuntimeIdentityError("an SDK file does not match its installed distribution's RECORD digest")
    return RecordedFile(**recorded), location, st


def capture_sdk_identity(distribution, *, platform_system: str) -> SdkIdentity:
    files = _files_by_record_path(distribution)
    record_entries = [path for path in files if path.endswith(".dist-info/RECORD")]
    if len(record_entries) != 1:
        raise RuntimeIdentityError("the SDK distribution does not list exactly one RECORD")
    record_location = Path(distribution.locate_file(files[record_entries[0]]))
    _regular_file(record_location)
    record_sha256 = _sha256_file(record_location)

    wheel_text = distribution.read_text("WHEEL") or ""
    wheel_tags = tuple(
        line.split(":", 1)[1].strip() for line in wheel_text.splitlines() if line.startswith("Tag:")
    )

    transport, transport_location, _ = _recorded(files, distribution, TRANSPORT_RECORD_PATH)
    selection = verify_bundled_cli_selection(transport_location.read_text(encoding="utf-8"))

    cli_path = bundled_cli_record_path(platform_system)
    cli, cli_location, cli_stat = _recorded(files, distribution, cli_path)

    version_declared = None
    version_file = files.get(CLI_VERSION_RECORD_PATH)
    if version_file is not None:
        version_location = Path(distribution.locate_file(version_file))
        _regular_file(version_location)
        match = _CLI_VERSION.search(version_location.read_text(encoding="utf-8"))
        version_declared = match.group(1) if match else None

    is_executable = None if platform_system == "Windows" else os.access(cli_location, os.X_OK)
    return SdkIdentity(
        distribution_name=SDK_DISTRIBUTION,
        version=distribution.version,
        wheel_tags=wheel_tags,
        record_sha256=record_sha256,
        transport_module=transport,
        bundled_cli=BundledCli(
            **cli.model_dump(), size_bytes=cli_stat.st_size,
            is_executable=is_executable, cli_version_declared=version_declared,
        ),
        cli_selection=selection,
    )


def capture_runtime_identity(
    *,
    env: Mapping[str, str],
    platform_system: str | None = None,
    sdk_distribution=None,
    installed_distributions: Iterable | None = None,
) -> RuntimeIdentity:
    """Capture the runtime identity of the current interpreter. ``env`` is
    read ONLY for the five allowlisted runner-image keys."""
    system = platform_system if platform_system is not None else platform.system()
    try:
        sdk = sdk_distribution if sdk_distribution is not None else importlib.metadata.distribution(SDK_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeIdentityError("claude-agent-sdk is not installed") from exc

    pairs: set[tuple[str, str]] = set()
    source = installed_distributions if installed_distributions is not None else importlib.metadata.distributions()
    for distribution in source:
        name = distribution.metadata["Name"] if distribution.metadata is not None else None
        if not name or not distribution.version:
            continue
        pairs.add((_normalize_name(name), distribution.version))
    if len(pairs) > MAX_DISTRIBUTIONS:
        raise RuntimeIdentityError("too many installed distributions")

    runner = RunnerImage(**{field: env.get(key) or None for field, key in RUNNER_IMAGE_ENV_KEYS})
    return RuntimeIdentity(
        schema_version=1,
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        sys_platform=sys.platform,
        machine=platform.machine() or "unknown",
        os_release=platform.release() or "unknown",
        runner=runner,
        distributions=tuple(sorted(pairs)),
        sdk=capture_sdk_identity(sdk, platform_system=system),
    )

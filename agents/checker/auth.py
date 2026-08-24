"""Fail-closed authentication-override check (dispatch q77-p3-a:
"fail closed if any of these could override that authentication").

The intended gate authentication is the operator's local Claude Code
CLI subscription OAuth login. The Claude Agent SDK spawns the CLI as a
subprocess and *merges* the parent process's environment into that
subprocess's environment (options.env only overrides on top of it —
confirmed by reading claude_agent_sdk's own
_internal/transport/subprocess_cli.py, 2026-08). So a credential
variable merely being set in this process's environment is enough to
leak into the subprocess even if this code never reads or forwards it
itself.

The current official Agent SDK docs (checked 2026-08, see
THREAT_MODEL.md) document no API to positively confirm which auth mode
is active before a query. What *is* achievable, and what this module
does, is a fail-closed check: refuse to run in agent mode at all if any
documented override-capable variable is present in the environment,
before any model call. Values are never read into a variable, printed,
logged, or persisted — only variable *names* are inspected.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agents.checker.config import AUTH_MODE_LABEL

# Every environment variable the current official docs (code.claude.com,
# checked 2026-08) document as capable of overriding subscription OAuth
# or rerouting requests away from the default Anthropic API endpoint:
# API keys / auth tokens, base-URL overrides, and cloud-provider
# (Bedrock/Vertex/Foundry) routing/auth variables. This list is a
# documented-as-of-date enumeration, not a guarantee of completeness —
# see THREAT_MODEL.md's residual-risk section.
AUTH_OVERRIDE_ENV_VARS: frozenset[str] = frozenset(
    {
        # API keys / auth tokens
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_AWS_API_KEY",
        "ANTHROPIC_FOUNDRY_API_KEY",
        "ANTHROPIC_FOUNDRY_AUTH_TOKEN",
        "AWS_BEARER_TOKEN_BEDROCK",
        # Base-URL / endpoint overrides
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AWS_BASE_URL",
        "ANTHROPIC_BEDROCK_BASE_URL",
        "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
        "ANTHROPIC_VERTEX_BASE_URL",
        "ANTHROPIC_FOUNDRY_BASE_URL",
        # Cloud-provider routing toggles
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    }
)


class AuthCheckFailure(RuntimeError):
    """Common base for every auth-profile check failure (dispatch
    q77-p5b-foundation-a), so a caller that doesn't care which profile
    is active can catch one type. Every concrete check below raises a
    subclass of this rather than a bare RuntimeError."""


class AuthOverrideRisk(AuthCheckFailure):
    """Raised before any model call if a variable that could override
    the intended subscription-OAuth authentication is present in the
    environment. Agent mode must not proceed — fail closed, per the
    binding SDK/auth-discovery requirement."""


def assert_no_auth_override_risk(env: "os._Environ[str] | dict[str, str] | None" = None) -> None:
    """Raise AuthOverrideRisk if any override-capable variable is set
    (non-empty) in ``env`` (defaults to the real process environment).
    Never reads or returns the values themselves."""
    source = env if env is not None else os.environ
    present = sorted(name for name in AUTH_OVERRIDE_ENV_VARS if source.get(name))
    if present:
        raise AuthOverrideRisk(
            "refusing to run the caged checker agent: the following "
            "environment variable(s) could override subscription-OAuth "
            f"authentication and must be unset first: {', '.join(present)}"
        )


# ---------------------------------------------------------------------
# Phase-5 (dispatch q77-p5b-foundation-a, ADR-0011 §3): GitHub Actions
# Workload Identity Federation auth-readiness check. This module only
# validates configuration BY NAME and precedence-cleanliness before any
# activity — it never requests an OIDC token, never exchanges one, and
# never reads a credential/token value. That exchange is a later part
# of this same program (P5-C), not this dispatch.
# ---------------------------------------------------------------------

# Current Anthropic-documented direct-federation environment variables
# (platform.claude.com/docs/en/manage-claude/wif-providers/github-actions,
# checked 2026-08-24). ANTHROPIC_WORKSPACE_ID is deliberately excluded:
# current docs mark it optional for a federation rule that targets a
# single workspace, so its absence alone must never fail this check.
WIF_REQUIRED_ENV_VARS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_FEDERATION_RULE_ID",
        "ANTHROPIC_ORGANIZATION_ID",
        "ANTHROPIC_SERVICE_ACCOUNT_ID",
        "ANTHROPIC_IDENTITY_TOKEN_FILE",
    }
)

# Anything whose mere presence could shadow or reroute the intended
# direct-Anthropic WIF path, per the documented five-tier credential
# precedence (constructor args > ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN
# > explicit ANTHROPIC_PROFILE > federation env vars > implicit active
# profile) plus every existing cloud-routing/base-URL override this
# module already enumerates for the local-OAuth path above — those
# reroute away from direct Anthropic under WIF exactly as they do under
# local OAuth. ANTHROPIC_IDENTITY_TOKEN (the literal in-memory token,
# as opposed to the _FILE path variant) is also refused: this dispatch
# deliberately adopts file-based identity-token transport only.
STATIC_CREDENTIAL_SHADOW_VARS: frozenset[str] = frozenset(
    {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"}
)
WIF_SHADOW_ENV_VARS: frozenset[str] = (
    STATIC_CREDENTIAL_SHADOW_VARS | AUTH_OVERRIDE_ENV_VARS | {"ANTHROPIC_IDENTITY_TOKEN"}
)


class WifConfigurationError(AuthCheckFailure):
    """Raised before any OIDC/model activity if the WIF-federation
    environment is incomplete, could be shadowed by a static credential
    or a conflicting profile/routing variable, or names an identity-
    token-file path that is not a safe, existing regular file. Names
    the offending variable(s) only — never a credential/token value,
    never the path value itself."""


def _assert_identity_token_file_safe(path_value: str) -> None:
    """Confirm ``ANTHROPIC_IDENTITY_TOKEN_FILE`` points at a safe,
    existing regular file, without opening it and without echoing the
    path value in any error message.

    A symlink is refused explicitly and BEFORE any existence/regular-
    file check: ``Path.is_file()`` follows symlinks and would happily
    report True for a symlink pointing at an attacker-controlled
    regular file elsewhere on disk, silently defeating the safety this
    check exists for. Checking ``is_symlink()`` first closes that gap
    regardless of what the symlink resolves to."""
    path = Path(path_value)
    if path.is_symlink():
        raise WifConfigurationError(
            "WIF configuration refused: ANTHROPIC_IDENTITY_TOKEN_FILE "
            "must not be a symlink"
        )
    if not path.is_file():
        raise WifConfigurationError(
            "WIF configuration refused: ANTHROPIC_IDENTITY_TOKEN_FILE "
            "does not point to an existing regular file"
        )


def assert_wif_config_ready(
    env: "os._Environ[str] | dict[str, str] | None" = None,
) -> None:
    """Raise WifConfigurationError if the WIF-federation environment is
    not ready: any required variable missing or empty, any shadowing
    variable present (including as an empty string — a stricter test
    than the required-variable check, deliberately: an operator's WIF
    setup must fully unset these, not merely blank them), or the
    identity-token-file path failing the safety check above. Never
    reads or returns a credential/token/path value."""
    source = env if env is not None else os.environ
    missing = sorted(name for name in WIF_REQUIRED_ENV_VARS if not source.get(name))
    if missing:
        raise WifConfigurationError(
            "WIF configuration incomplete: missing required variable(s): "
            + ", ".join(missing)
        )
    shadowing = sorted(name for name in WIF_SHADOW_ENV_VARS if name in source)
    if shadowing:
        raise WifConfigurationError(
            "WIF configuration refused: variable(s) present that could "
            "shadow or reroute the intended federation path: " + ", ".join(shadowing)
        )
    _assert_identity_token_file_safe(source["ANTHROPIC_IDENTITY_TOKEN_FILE"])


@dataclass(frozen=True)
class AuthProfile:
    """Which auth check a caged run performs, and the truthful label
    recorded on its ``agent_calls`` audit rows. ``check`` takes the
    same optional ``env`` override every check function above takes."""

    label: str
    check: Callable[["os._Environ[str] | dict[str, str] | None"], None]


# The check callables are thin lambdas rather than direct function
# references so that existing/future test doubles which patch the
# module-level names (``unittest.mock.patch("agents.checker.harness.
# auth.assert_no_auth_override_risk", ...)``, the pattern already used
# throughout this test suite) still take effect: a lambda's bare-name
# call is resolved against this module's namespace at CALL time, so a
# patched module attribute is honored; a direct function reference
# captured here at import time would not be.
LOCAL_OAUTH = AuthProfile(
    label=AUTH_MODE_LABEL, check=lambda env: assert_no_auth_override_risk(env)
)
WIF = AuthProfile(
    label="github-actions-wif-federation", check=lambda env: assert_wif_config_ready(env)
)

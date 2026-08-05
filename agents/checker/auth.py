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


class AuthOverrideRisk(RuntimeError):
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

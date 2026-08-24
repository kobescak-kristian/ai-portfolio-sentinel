"""Phase-5 GitHub Actions WIF auth-readiness check (dispatch
q77-p5b-foundation-a, ADR-0011 §3).

This module validates configuration BY NAME and precedence-cleanliness
only. No test here requests an OIDC token, exchanges one, or reads a
credential/token value -- conftest.py's autouse ``block_network``
fixture would fail any test that reached the network regardless, and
``assert_wif_config_ready`` never attempts to.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agents.checker import auth
from agents.checker.budget import RunBudgetCoordinator
from agents.checker.fx import FxRate
from agents.checker.harness import CagedCheckerStub, CheckerAgentError
from checks.judgment.stubs import JudgmentRequest
from contracts.schemas import RunRecord
from sentinel import ledger

T0 = datetime(2026, 8, 24, 6, 0, 0, tzinfo=timezone.utc)
_FAKE_RATE = FxRate(
    source="ecb-eurofxref-daily", rate_date="2026-08-24", retrieved_at_utc=T0,
    usd_per_eur=Decimal("1.1554"),
)


def _valid_env(token_file) -> dict:
    return {
        "ANTHROPIC_FEDERATION_RULE_ID": "fdrl_abc123",
        "ANTHROPIC_ORGANIZATION_ID": "org_abc123",
        "ANTHROPIC_SERVICE_ACCOUNT_ID": "svac_abc123",
        "ANTHROPIC_IDENTITY_TOKEN_FILE": str(token_file),
    }


@pytest.fixture
def token_file(tmp_path):
    p = tmp_path / "oidc-token.txt"
    p.write_text("not-a-real-token", encoding="utf-8")
    return p


# =====================================================================
# Ready path
# =====================================================================


def test_ready_with_complete_required_set_and_safe_token_file(token_file):
    auth.assert_wif_config_ready(_valid_env(token_file))  # must not raise


def test_optional_workspace_id_absence_does_not_by_itself_fail(token_file):
    env = _valid_env(token_file)
    assert "ANTHROPIC_WORKSPACE_ID" not in env
    auth.assert_wif_config_ready(env)  # must not raise


def test_optional_workspace_id_presence_does_not_fail_either(token_file):
    env = _valid_env(token_file)
    env["ANTHROPIC_WORKSPACE_ID"] = "wrkspc_abc123"
    auth.assert_wif_config_ready(env)  # must not raise


# =====================================================================
# Required-variable set: must require non-empty
# =====================================================================


@pytest.mark.parametrize("missing_var", sorted(auth.WIF_REQUIRED_ENV_VARS))
def test_fails_when_a_required_variable_is_absent(token_file, missing_var):
    env = _valid_env(token_file)
    del env[missing_var]
    with pytest.raises(auth.WifConfigurationError) as exc_info:
        auth.assert_wif_config_ready(env)
    assert missing_var in str(exc_info.value)


@pytest.mark.parametrize("empty_var", sorted(auth.WIF_REQUIRED_ENV_VARS))
def test_fails_when_a_required_variable_is_present_but_empty(token_file, empty_var):
    env = _valid_env(token_file)
    env[empty_var] = ""
    with pytest.raises(auth.WifConfigurationError) as exc_info:
        auth.assert_wif_config_ready(env)
    assert empty_var in str(exc_info.value)


# =====================================================================
# Shadow set: presence, INCLUDING EMPTY STRING, is a conflict
# =====================================================================


@pytest.mark.parametrize("shadow_var", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"])
@pytest.mark.parametrize("value", ["some-value", ""], ids=["non-empty", "empty-string"])
def test_named_shadow_variables_fail_present_or_empty(token_file, shadow_var, value):
    env = _valid_env(token_file)
    env[shadow_var] = value
    with pytest.raises(auth.WifConfigurationError) as exc_info:
        auth.assert_wif_config_ready(env)
    assert shadow_var in str(exc_info.value)


def test_disallowed_literal_identity_token_fails():
    """Part 1 deliberately adopts file-based identity-token transport
    only; the literal in-memory variant must be refused even though it
    is not one of the 4 required variables."""
    with pytest.raises(auth.WifConfigurationError) as exc_info:
        auth.assert_wif_config_ready({**_valid_env("/dev/null"), "ANTHROPIC_IDENTITY_TOKEN": "eyJ..."})
    assert "ANTHROPIC_IDENTITY_TOKEN" in str(exc_info.value)


@pytest.mark.parametrize("cloud_route_var", sorted(auth.AUTH_OVERRIDE_ENV_VARS))
def test_every_existing_cloud_routing_variable_is_also_a_wif_conflict(token_file, cloud_route_var):
    """Proves the 'review the existing override set' requirement is
    actually wired in, not just asserted in prose: every variable this
    module already treats as a local-OAuth override risk is also
    refused under WIF, since all of them reroute away from direct
    Anthropic."""
    env = _valid_env(token_file)
    env[cloud_route_var] = "x"
    with pytest.raises(auth.WifConfigurationError) as exc_info:
        auth.assert_wif_config_ready(env)
    assert cloud_route_var in str(exc_info.value)


def test_wif_shadow_env_vars_is_the_expected_union():
    assert auth.WIF_SHADOW_ENV_VARS == (
        auth.STATIC_CREDENTIAL_SHADOW_VARS | auth.AUTH_OVERRIDE_ENV_VARS | {"ANTHROPIC_IDENTITY_TOKEN"}
    )


# =====================================================================
# Identity-token-file safety: existing regular file, no symlink, never
# echoed
# =====================================================================


def test_missing_token_file_fails_closed():
    env = _valid_env("/definitely/does/not/exist/token.txt")
    with pytest.raises(auth.WifConfigurationError):
        auth.assert_wif_config_ready(env)


def test_directory_target_fails_closed(tmp_path):
    env = _valid_env(tmp_path)  # a directory, not a regular file
    with pytest.raises(auth.WifConfigurationError):
        auth.assert_wif_config_ready(env)


def test_symlink_target_is_explicitly_rejected_even_when_it_points_at_a_real_file(tmp_path):
    """Path.is_file() follows symlinks -- a naive check would accept a
    symlink pointing at a legitimate regular file, silently defeating
    the safety this check exists for. is_symlink() must be checked
    first and reject regardless of what the link resolves to."""
    real_file = tmp_path / "real-token.txt"
    real_file.write_text("token-bytes", encoding="utf-8")
    link = tmp_path / "linked-token.txt"
    try:
        link.symlink_to(real_file)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted on this test platform")

    env = _valid_env(link)
    with pytest.raises(auth.WifConfigurationError) as exc_info:
        auth.assert_wif_config_ready(env)
    assert "symlink" in str(exc_info.value).lower()


def test_token_file_error_never_echoes_the_path_value(tmp_path):
    canary_path = tmp_path / "super-secret-token-file-name-canary.txt"
    env = _valid_env(canary_path)  # does not exist
    with pytest.raises(auth.WifConfigurationError) as exc_info:
        auth.assert_wif_config_ready(env)
    assert str(canary_path) not in str(exc_info.value)
    assert "super-secret-token-file-name-canary" not in str(exc_info.value)


def test_credential_and_token_values_never_appear_in_any_error_message(token_file):
    canary_value = "sk-ant-oat01-super-secret-canary-value"
    env = _valid_env(token_file)
    env["ANTHROPIC_API_KEY"] = canary_value
    with pytest.raises(auth.WifConfigurationError) as exc_info:
        auth.assert_wif_config_ready(env)
    assert canary_value not in str(exc_info.value)


# =====================================================================
# AuthProfile / AuthCheckFailure hierarchy
# =====================================================================


def test_wif_profile_label_is_truthful():
    assert auth.WIF.label == "github-actions-wif-federation"


def test_local_oauth_profile_label_is_the_existing_constant():
    from agents.checker.config import AUTH_MODE_LABEL

    assert auth.LOCAL_OAUTH.label == AUTH_MODE_LABEL


def test_both_check_failure_types_share_the_common_base():
    assert issubclass(auth.AuthOverrideRisk, auth.AuthCheckFailure)
    assert issubclass(auth.WifConfigurationError, auth.AuthCheckFailure)
    assert isinstance(auth.AuthOverrideRisk("x"), auth.AuthCheckFailure)
    assert isinstance(auth.WifConfigurationError("x"), auth.AuthCheckFailure)


def test_wif_profile_check_raises_through_the_auth_profile_indirection(token_file):
    env = dict(_valid_env(token_file))
    del env["ANTHROPIC_ORGANIZATION_ID"]
    with pytest.raises(auth.WifConfigurationError):
        auth.WIF.check(env)


def test_local_oauth_profile_check_raises_through_the_auth_profile_indirection():
    with pytest.raises(auth.AuthOverrideRisk):
        auth.LOCAL_OAUTH.check({"ANTHROPIC_API_KEY": "x"})


# =====================================================================
# Existing local-OAuth path is unchanged (semantic/behavioral
# preservation, not merely byte-identical source)
# =====================================================================


def test_auth_override_env_vars_golden_set_is_unchanged():
    assert auth.AUTH_OVERRIDE_ENV_VARS == frozenset(
        {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_AWS_API_KEY",
            "ANTHROPIC_FOUNDRY_API_KEY",
            "ANTHROPIC_FOUNDRY_AUTH_TOKEN",
            "AWS_BEARER_TOKEN_BEDROCK",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_AWS_BASE_URL",
            "ANTHROPIC_BEDROCK_BASE_URL",
            "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
            "ANTHROPIC_VERTEX_BASE_URL",
            "ANTHROPIC_FOUNDRY_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
        }
    )


def test_local_oauth_check_still_treats_empty_string_as_absent_unlike_wif():
    """The local-OAuth override-risk check is unchanged: an empty
    string is falsy and therefore treated as "not set". The new WIF
    shadow check is deliberately stricter (key presence, including
    empty string) -- this test pins the difference explicitly so a
    future edit cannot accidentally unify the two semantics."""
    auth.assert_no_auth_override_risk({"ANTHROPIC_API_KEY": ""})  # must not raise
    with pytest.raises(auth.AuthOverrideRisk):
        auth.assert_no_auth_override_risk({"ANTHROPIC_API_KEY": "x"})


def test_assert_no_auth_override_risk_behavior_is_unchanged():
    with pytest.raises(auth.AuthOverrideRisk):
        auth.assert_no_auth_override_risk({"ANTHROPIC_BASE_URL": "https://example.invalid"})
    auth.assert_no_auth_override_risk({"UNRELATED_VAR": "x"})  # must not raise


# =====================================================================
# End-to-end: a failing WIF precheck makes zero query_fn invocations
# =====================================================================


def test_caged_stub_with_failing_wif_precheck_makes_zero_query_fn_calls_and_records_rejected(tmp_path):
    calls_made = []

    def must_not_be_called(check_class, reservation, state, user_prompt, model=None):
        calls_made.append(1)
        raise AssertionError("the model path must not be reached on a failed WIF precheck")

    db_path = tmp_path / "sentinel.sqlite3"
    conn = ledger.open_ledger(db_path)
    with ledger.unit_of_work(conn):
        ledger.insert_run(
            conn,
            RunRecord(
                schema_version=1, run_id="r-1", run_kind="live", status="RUNNING",
                started_at_utc=T0, tasks_created=0, tasks_terminal=0,
                findings_new=0, findings_still_open=0, findings_resolved=0,
            ),
        )

    coordinator = RunBudgetCoordinator(fx_rate=_FAKE_RATE)
    # No WIF env vars set at all in this process -> assert_wif_config_ready
    # fails on the first missing-required-variable check.
    with patch.dict(os.environ, {}, clear=False):
        for var in auth.WIF_REQUIRED_ENV_VARS | auth.WIF_SHADOW_ENV_VARS:
            os.environ.pop(var, None)
        stub = CagedCheckerStub(
            run_id="r-1", conn=conn, coordinator=coordinator, clock=lambda: T0,
            query_fn=must_not_be_called, auth_profile=auth.WIF,
        )
        with pytest.raises(CheckerAgentError) as exc_info:
            stub.judge(
                JudgmentRequest(
                    surface="acme/STATE.md", check_class="missing-synthetic-label",
                    path="STATE.md", text="line one\nline two\nline three",
                )
            )
    assert isinstance(exc_info.value.__cause__, auth.WifConfigurationError)
    assert calls_made == []
    rows = ledger.list_agent_calls_for_run(conn, "r-1")
    assert len(rows) == 1
    assert rows[0].state == "REJECTED"
    assert rows[0].auth_mode == "github-actions-wif-federation"
    conn.close()

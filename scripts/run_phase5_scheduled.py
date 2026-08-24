#!/usr/bin/env python
"""Phase-5 scheduled live run entrypoint (P5-B Part 3/3, workflow A:
``.github/workflows/sentinel-schedule.yml``).

Thin control surface over ``sentinel.phase5.orchestrator.run_scheduled``
— builds the real ``ScheduledPorts``, runs the frozen S01..S18
preflight order, writes staged artifact names/paths to
``$GITHUB_OUTPUT`` for the workflow's upload steps, and returns the
orchestrator's exit code.

Never imports ``claude_agent_sdk`` at module scope: the Agent SDK path
(``agents.checker.harness``) is imported lazily, inside the
``run_sentinel`` port closure, exactly like
``sentinel/cli.py::_build_agent_mode_deps`` — so this script (and any
test that imports it) never requires the SDK to be installed unless
the provider path is actually reached.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._phase5_common import (  # noqa: E402
    REPO_ROOT,
    build_evidence_client,
    emit_github_output,
)
from sentinel.phase5.github_context import derive_github_context  # noqa: E402
from sentinel.phase5.orchestrator import ScheduledPorts, run_scheduled  # noqa: E402


def _build_wif_ready(env):
    def wif_ready() -> None:
        from agents.checker import auth, oidc

        oidc.write_placeholder_token_file(env)
        auth.assert_wif_config_ready(env)

    return wif_ready


def _build_run_sentinel(env, *, github_owner: str, session_holder: dict):
    def run_sentinel(working_state, github_run_id: str):
        from agents.checker import auth
        from agents.checker.config import HAIKU_ORDINARY
        from agents.checker.harness import build_caged_judgment_stub
        from agents.checker.oidc import health_gated
        from sentinel.config import RunConfig
        from sentinel.pipeline import Deps, execute_run

        run_id = f"r-p5-{github_run_id}"
        stub = build_caged_judgment_stub(
            run_id=run_id,
            db_path=working_state.db_path,
            profile=HAIKU_ORDINARY,
            auth_profile=auth.WIF,
        )
        session = session_holder.get("session")
        if session is not None:
            stub.query_fn = health_gated(stub.query_fn, session)
        config = RunConfig(
            run_kind="live",
            source="live",
            judgment_mode="agent",
            db_path=working_state.db_path,
            findings_path=working_state.findings_path,
            log_path=working_state.root / "run.jsonl",
            cost_ledger_path=working_state.cost_ledger_path,
            github_user=github_owner,
            run_id=run_id,
        )
        return execute_run(config, Deps(judgment=stub))

    return run_sentinel


def main() -> int:
    env = os.environ
    work_root = Path(os.environ.get("PHASE5_WORK_ROOT", REPO_ROOT / "var" / "phase5-schedule"))

    client = build_evidence_client(env)  # pops GITHUB_TOKEN first (seam 4)
    ctx = derive_github_context(env)

    from agents.checker import oidc

    session_holder: dict = {"session": None}

    def acquire_oidc():
        session = oidc.acquire_oidc(env)
        return session

    def install_token(session):
        session_holder["session"] = session
        session.install_and_start(env)

    def shutdown_oidc(session):
        if session is not None:
            session.shutdown(env)
        else:
            oidc.scrub_identity_token_file(env)

    ports = ScheduledPorts(
        env=env,
        clock=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        evidence_client=client,
        wif_ready=_build_wif_ready(env),
        acquire_oidc=acquire_oidc,
        install_token=install_token,
        shutdown_oidc=shutdown_oidc,
        run_sentinel=_build_run_sentinel(env, github_owner=ctx.repository_owner, session_holder=session_holder),
    )

    result = run_scheduled(ports, work_trusted_root=work_root)

    for artifact in result.staged_artifacts:
        if "slot" in artifact.name or "refusal" in artifact.name:
            emit_github_output("bundle_name", artifact.name)
            emit_github_output("bundle_path", str(artifact.path))
        else:
            emit_github_output("evidence_name", artifact.name)
            emit_github_output("evidence_path", str(artifact.path))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

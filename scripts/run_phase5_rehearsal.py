#!/usr/bin/env python
"""Phase-5 model-free rehearsal entrypoint (P5-B Part 3/3, workflow B:
``.github/workflows/sentinel-rehearsal.yml``).

Structurally cannot reach OIDC or a provider: no ``id-token`` workflow
permission exists for this lane, and this script builds no OIDC/WIF
port at all — only the six non-provider steps of
``preflight.REHEARSAL_STEP_ORDER`` ever run.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._phase5_common import REPO_ROOT, build_evidence_client, emit_github_output  # noqa: E402
from sentinel.phase5.orchestrator import ScheduledPorts, run_rehearsal  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, default=REPO_ROOT / "var" / "phase5-rehearsal")
    parser.add_argument("--expected-source-sha", required=True)
    args = parser.parse_args(argv)

    env = os.environ
    client = build_evidence_client(env)  # pops GITHUB_TOKEN; REST used read-only only

    def _unreachable(*_a, **_kw):
        raise RuntimeError("rehearsal must never reach a provider-capable port")

    ports = ScheduledPorts(
        env=env,
        clock=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        evidence_client=client,
        wif_ready=_unreachable,
        acquire_oidc=_unreachable,
        install_token=_unreachable,
        shutdown_oidc=lambda session: None,
        run_sentinel=_unreachable,
    )

    result = run_rehearsal(ports, work_trusted_root=args.work_root, expected_source_sha=args.expected_source_sha)

    for artifact in result.staged_artifacts:
        emit_github_output("evidence_name", artifact.name)
        emit_github_output("evidence_path", str(artifact.path))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

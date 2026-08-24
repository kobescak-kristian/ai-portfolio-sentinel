"""Phase-5 domain core and Actions adapters (ADR-0011; P5-B Parts 2/3
and 3/3).

The five Part-2 domain modules (``models``, ``bundle``, ``qualification``,
``cadence``, ``oneshot``) remain pure local machinery for Actions-era
state bundles, qualification windows, supersession, predecessor
selection, qualification classification, cadence/cost state and
one-shot markers: no network, no workflow, no provider, no OIDC/WIF,
no GitHub API operation.

Part 3 adds four adapter modules that stay stdlib(+pydantic)-only but
do perform the one network operation this package needs —
``github_evidence`` is the sole module in this package that makes an
HTTP request, and only to GitHub's REST/artifact API, never to a
provider. ``github_context`` and ``artifact_names`` stay pure
(env-mapping parsing, string construction only); ``preflight`` and
``evidence_records`` stay pure (in-process ordering, canonical
serialization only). ``orchestrator`` composes all of the above,
plus the domain modules, behind injected ports — it never imports
``claude_agent_sdk`` or any ``agents.*`` module itself.

No module in this package performs an OIDC/WIF exchange or a
provider/model call; that capability is injected as a port from
``agents/checker/oidc.py`` and ``agents/checker/harness.py`` by the
``scripts/run_phase5_*.py`` entry points.
"""

"""First-party package boundary for the Phase-3 caged checker agent
(BLUEPRINT.md §3, §4, §6 P3; dispatch q77-p3-a).

The Claude Agent SDK import lives only under this package tree —
never in ``sentinel/`` or ``checks/``. ``tests/test_read_only_boundary.py``
statically bans the model-SDK import roots there; ``tests/test_dependency_surface.py``
is the complementary check that ``agents`` is the only first-party
root permitted to import ``claude_agent_sdk``/``anyio``. Nothing here
is imported by ``sentinel`` or ``checks`` directly — the wiring point
is ``sentinel/cli.py``, which imports ``agents.checker.harness`` only
when ``--judgment-mode agent`` is explicitly selected.
"""

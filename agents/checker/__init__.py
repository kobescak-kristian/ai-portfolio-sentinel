"""The caged checker agent: real judgment for the two Phase-2 stubbed
check classes (stale-STATE-marker, missing-synthetic-label).

Replaces only the ``checks/judgment/stubs.py`` seam — ``CagedCheckerStub``
(harness.py) implements the same ``JudgmentStub`` protocol
(``checks.judgment.stubs.JudgmentStub``) that ``NullJudgmentStub``
implements today, so ``checks/judgment/stale_state.py`` and
``checks/judgment/synthetic_label.py`` are unchanged. Selected only via
``--judgment-mode agent`` (default remains ``stub``, BLUEPRINT §6 P3;
dispatch q77-p3-a).
"""

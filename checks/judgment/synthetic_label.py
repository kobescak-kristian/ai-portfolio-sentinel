"""missing-synthetic-label adapter: same shape as stale_state.py,
delegating to the injected judgment stub. Real judgment lands at
Phase 3."""

from __future__ import annotations

from checks.base import (
    CheckContext,
    CheckOutcome,
    Confirmed,
    Inconclusive,
    register_checker,
)
from checks.judgment.stubs import JudgmentRequest
from sentinel.inventory.base import Content, Unknown

CHECK_CLASS = "missing-synthetic-label"


@register_checker(CHECK_CLASS)
def check_missing_synthetic_label(ctx: CheckContext) -> CheckOutcome:
    result = ctx.fetch(ctx.detail_path)
    if isinstance(result, Unknown):
        return Inconclusive(result.reason)
    surface = f"{ctx.owner}/{ctx.detail_path}"
    text = result.text if isinstance(result, Content) else None
    request = JudgmentRequest(
        surface=surface, check_class=CHECK_CLASS, path=ctx.detail_path, text=text
    )
    findings = ctx.judgment.judge(request)
    for finding in findings:
        if finding.surface != surface or finding.check_class != CHECK_CLASS:
            return Inconclusive("judgment stub returned a finding outside its own scope")
    return Confirmed(list(findings))

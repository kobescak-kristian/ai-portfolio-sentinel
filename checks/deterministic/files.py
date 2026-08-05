"""missing-required-file checker (ADR 0004).

**C1 — inverted semantics.** A confirmed-absent required path *is*
the defect: the task ends DONE and emits the finding every run it
stays absent (the per-task lifecycle step in sentinel.lifecycle then
advances the existing OPEN row, or opens a new one — it never falls
into "unobserved", so it never auto-resolves while genuinely absent).
A confirmed-present path ends DONE with zero findings, which lets any
existing OPEN finding correctly auto-resolve via the normal scope
sweep. An unresolved fetch dead-letters the task instead of either
firing or resolving — a temporary failure must never look like
"file restored" or "file still gone".
"""

from __future__ import annotations

import ast

from checks.base import (
    CheckContext,
    CheckOutcome,
    Confirmed,
    Inconclusive,
    ObservedFinding,
    register_checker,
)
from sentinel.inventory.base import ConfirmedAbsent, Content, Unknown


@register_checker("missing-required-file")
def check_missing_required_file(ctx: CheckContext) -> CheckOutcome:
    result = ctx.fetch(ctx.detail_path)
    if isinstance(result, Unknown):
        return Inconclusive(result.reason)
    surface = f"{ctx.owner}/{ctx.detail_path}"
    if isinstance(result, ConfirmedAbsent):
        finding = ObservedFinding(
            surface=surface,
            check_class="missing-required-file",
            location=ctx.detail_path,
            detail=f"required file {ctx.detail_path} is absent from {ctx.owner}",
            normalized_content=f"required_path={ctx.detail_path}",
        )
        return Confirmed([finding])
    assert isinstance(result, Content)
    if ctx.policy_parse_required:
        try:
            ast.parse(result.text)
        except SyntaxError:
            return Inconclusive(
                "policy source content could not be parsed deterministically"
            )
    return Confirmed([])

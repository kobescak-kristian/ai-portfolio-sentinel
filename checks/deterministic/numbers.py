"""number-mismatch checker (ADR 0004).

Compares ``- label: value [unit]`` bullets between README.md and
EVAL_RESULTS.md, joined on a casefolded label. A label appearing more
than once in either file is skipped — determinism over coverage.
Comparison uses ``Decimal``, never ``float``. Per **C1**: a confirmed-
absent counterpart file means nothing to check (DONE, zero findings,
scope resolves); an unresolved fetch dead-letters the task.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from checks.base import (
    CheckContext,
    CheckOutcome,
    Confirmed,
    Inconclusive,
    ObservedFinding,
    split_lines,
    register_checker,
)
from sentinel.inventory.base import ConfirmedAbsent, Content, Unknown

_FIGURE = re.compile(
    r"^-\s+(?P<label>[^:]+):\s+(?P<value>-?\d+(?:\.\d+)?)(?:\s+(?P<unit>\S+))?\s*$"
)


def _extract_figures(text: str) -> dict[str, tuple[str, str, int]]:
    counts: dict[str, int] = {}
    figures: dict[str, tuple[str, str, int]] = {}
    for line_no, line in enumerate(split_lines(text), start=1):
        match = _FIGURE.match(line)
        if not match:
            continue
        key = " ".join(match.group("label").split()).casefold()
        counts[key] = counts.get(key, 0) + 1
        figures[key] = (
            match.group("value"),
            (match.group("unit") or "").casefold(),
            line_no,
        )
    return {key: value for key, value in figures.items() if counts[key] == 1}


@register_checker("number-mismatch")
def check_number_mismatch(ctx: CheckContext) -> CheckOutcome:
    readme = ctx.fetch(ctx.detail_path)  # "README.md"
    if isinstance(readme, Unknown):
        return Inconclusive(readme.reason)
    if isinstance(readme, ConfirmedAbsent):
        return Confirmed([])
    assert isinstance(readme, Content)

    eval_results = ctx.fetch("EVAL_RESULTS.md")
    if isinstance(eval_results, Unknown):
        return Inconclusive(eval_results.reason)
    if isinstance(eval_results, ConfirmedAbsent):
        return Confirmed([])
    assert isinstance(eval_results, Content)

    readme_figures = _extract_figures(readme.text)
    eval_figures = _extract_figures(eval_results.text)
    surface = f"{ctx.owner}/README.md"

    findings: list[ObservedFinding] = []
    for label, (readme_value, readme_unit, line_no) in readme_figures.items():
        if label not in eval_figures:
            continue
        eval_value, eval_unit, _ = eval_figures[label]
        if readme_unit != eval_unit:
            continue
        try:
            mismatch = Decimal(readme_value) != Decimal(eval_value)
        except InvalidOperation:
            continue
        if not mismatch:
            continue
        location = f"README.md:{line_no}"
        readme_suffix = f" {readme_unit}" if readme_unit else ""
        eval_suffix = f" {eval_unit}" if eval_unit else ""
        detail = (
            f'README figure "{label}" reads {readme_value}{readme_suffix}; '
            f"EVAL_RESULTS.md records {eval_value}{eval_suffix}"
        )
        findings.append(
            ObservedFinding(
                surface=surface,
                check_class="number-mismatch",
                location=location,
                detail=detail,
                normalized_content=f"label={label}|readme={readme_value}|eval_results={eval_value}",
            )
        )
    findings.sort(key=lambda f: f.location)
    return Confirmed(findings)

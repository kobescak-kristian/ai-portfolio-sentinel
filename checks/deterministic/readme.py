"""readme-structure checker.

Fixture/eval mode: the ADR-0004-frozen five-header exact sequence,
``enforce_readme_order=True``. Live mode (C7): applicable only to a
repo whose own active pre-push invokes a gate file; enforces
**presence only** of that repo's own declared
``required_readme_sections`` (``enforce_readme_order=False``) — never
Sentinel's own ADR-0004 order. Location semantics for the missing-
header and order-violation cases follow ``evals/SCORING.md`` §1
exactly, verified against the frozen fixture corpus during design.
"""

from __future__ import annotations

from checks.base import (
    CheckContext,
    CheckOutcome,
    Confirmed,
    Inconclusive,
    ObservedFinding,
    register_checker,
    split_lines,
)
from sentinel.inventory.base import ConfirmedAbsent, Content, Unknown


def _detect_present_headers(
    lines: list[str], required: frozenset[str]
) -> list[tuple[str, int]]:
    present: list[tuple[str, int]] = []
    seen: set[str] = set()
    for line_no, line in enumerate(lines, start=1):
        stripped = line.rstrip()
        if stripped in required and stripped not in seen:
            present.append((stripped, line_no))
            seen.add(stripped)
    return present


@register_checker("readme-structure")
def check_readme_structure(ctx: CheckContext) -> CheckOutcome:
    result = ctx.fetch(ctx.detail_path)
    if isinstance(result, Unknown):
        return Inconclusive(result.reason)
    if isinstance(result, ConfirmedAbsent):
        return Confirmed([])
    assert isinstance(result, Content)

    required = ctx.required_readme_sections
    if not required:
        return Confirmed([])

    lines = split_lines(result.text)
    present = _detect_present_headers(lines, frozenset(required))
    present_headers = [header for header, _ in present]
    present_line = dict(present)
    surface = f"{ctx.owner}/README.md"
    findings: list[ObservedFinding] = []

    missing = [header for header in required if header not in present_headers]
    for header in missing:
        idx = required.index(header)
        location_line: int | None = None
        for later in required[idx + 1 :]:
            if later in present_line:
                location_line = present_line[later]
                break
        if location_line is None:
            for earlier in reversed(required[:idx]):
                if earlier in present_line:
                    location_line = present_line[earlier]
                    break
        location = "README.md" if location_line is None else f"README.md:{location_line}"
        findings.append(
            ObservedFinding(
                surface=surface,
                check_class="readme-structure",
                location=location,
                detail=f'required README header "{header}" is missing',
                normalized_content=f"defect=missing-header|header={header}",
            )
        )

    if ctx.enforce_readme_order:
        expected_order = [header for header in required if header in present_headers]
        divergence: tuple[str, str] | None = None
        for found, expected in zip(present_headers, expected_order):
            if found != expected:
                divergence = (found, expected)
                break
        if divergence is not None:
            found, expected = divergence
            line_no = present_line[found]
            findings.append(
                ObservedFinding(
                    surface=surface,
                    check_class="readme-structure",
                    location=f"README.md:{line_no}",
                    detail=(
                        f'README header "{found}" appears where "{expected}" is '
                        "required by the frozen sequence"
                    ),
                    normalized_content=f"defect=header-order|header={found}|expected={expected}",
                )
            )

    findings.sort(key=lambda f: f.location)
    return Confirmed(findings)

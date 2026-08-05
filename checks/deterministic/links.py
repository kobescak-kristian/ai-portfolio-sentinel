"""broken-link checker (ADR 0004).

**Whole-task determinism (C1 + C3):** a task covers one file. It
reaches DONE only if *every* link in that file resolved to a
confirmed ``live`` or ``dead`` status; if even one resolves
``unknown``, the whole task is Inconclusive (FAILED->DEAD_LETTER) —
a single flaky link must not let a real dead-link finding elsewhere
in the same file silently resolve, nor let an unconfirmed one be
treated as checked-clean.
"""

from __future__ import annotations

import re

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

_MD_LINK = re.compile(r'\[[^\]]*\]\((?P<url>[^)\s]+)(?:\s+"[^"]*")?\)')
_AUTOLINK = re.compile(r"<(?P<url>https?://[^>\s]+)>")


def _find_links(text: str) -> list[tuple[int, str]]:
    occurrences: list[tuple[int, str]] = []
    for line_no, line in enumerate(split_lines(text), start=1):
        for match in _MD_LINK.finditer(line):
            url = match.group("url")
            if url.startswith("http://") or url.startswith("https://"):
                occurrences.append((line_no, url))
        for match in _AUTOLINK.finditer(line):
            occurrences.append((line_no, match.group("url")))
    return occurrences


@register_checker("broken-link")
def check_broken_link(ctx: CheckContext) -> CheckOutcome:
    result = ctx.fetch(ctx.detail_path)
    if isinstance(result, Unknown):
        return Inconclusive(result.reason)
    if isinstance(result, ConfirmedAbsent):
        return Confirmed([])
    assert isinstance(result, Content)

    surface = f"{ctx.owner}/{ctx.detail_path}"
    occurrences = _find_links(result.text)
    statuses = {
        (line_no, url): ctx.link_resolver.resolve(url) for line_no, url in occurrences
    }
    if any(status == "unknown" for status in statuses.values()):
        return Inconclusive("one or more links could not be conclusively resolved")

    findings: list[ObservedFinding] = []
    for (line_no, url), status in statuses.items():
        if status != "dead":
            continue
        location = f"{ctx.detail_path}:{line_no}"
        findings.append(
            ObservedFinding(
                surface=surface,
                check_class="broken-link",
                location=location,
                detail=f"link {url} does not resolve (confirmed dead)",
                normalized_content=f"url={url}",
            )
        )
    findings.sort(key=lambda f: (f.location, f.normalized_content))
    return Confirmed(findings)

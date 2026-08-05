"""Host-side evidence validation and deterministic finding construction
(dispatch q77-p3-a, section D).

The model never emits a complete ``ObservedFinding`` — it only
proposes bounded evidence (a closed reason code plus verbatim
line/excerpt pairs) through the one custom tool (tools.py). This
module is what turns that proposal into a validated ``ObservedFinding``,
or rejects it outright. No free-form model text ever reaches
``location``, ``normalized_content`` or ``detail`` — those are built
here, deterministically, from data verified against
``JudgmentRequest.text``, never from the model's own prose.
"""

from __future__ import annotations

from dataclasses import dataclass

from checks.base import ObservedFinding
from checks.judgment.stubs import JudgmentRequest

# Closed reason-code set per check class. prompts.py tells the model
# which codes it may use; this module is the actual enforcement point
# — the model's own claim about a reason code is never trusted without
# this check.
REASON_CODES_BY_CLASS: dict[str, tuple[str, ...]] = {
    "stale-STATE-marker": ("DATED_ENTRY_CONTRADICTS_CURRENT_STATE",),
    "missing-synthetic-label": ("FIGURE_WITHOUT_ADJACENT_SYNTHETIC_LABEL",),
}

# How many evidence locations each class's finding requires. One class
# needs two: the stale dated entry AND the current-state section it
# contradicts. The other needs one: the unlabeled figure.
EXPECTED_EVIDENCE_COUNT: dict[str, int] = {
    "stale-STATE-marker": 2,
    "missing-synthetic-label": 1,
}


class EvidenceRejected(ValueError):
    """The proposed evidence failed host-side validation: out-of-range
    line, altered/fabricated excerpt, wrong reason code, or wrong
    evidence count. Never partially accepted — the caller must not
    construct a finding from any part of a rejected proposal."""


@dataclass(frozen=True)
class EvidenceItem:
    line: int
    excerpt: str


def _validate_evidence_item(item: EvidenceItem, lines: list[str]) -> None:
    if item.line < 1 or item.line > len(lines):
        raise EvidenceRejected(
            f"line {item.line} is out of range (document has {len(lines)} lines)"
        )
    source_line = lines[item.line - 1]
    if not item.excerpt:
        raise EvidenceRejected("excerpt must not be empty")
    if item.excerpt not in source_line:
        raise EvidenceRejected(
            f"excerpt {item.excerpt!r} does not appear verbatim on line {item.line}"
        )


def build_observed_finding(
    request: JudgmentRequest,
    *,
    reason_code: str,
    evidence: list[EvidenceItem],
) -> ObservedFinding:
    """Validate a proposed (reason_code, evidence) pair against
    ``request.text`` and deterministically construct an
    ``ObservedFinding``. Raises ``EvidenceRejected`` on any validation
    failure — the caller (tools.py) must not accept a partial finding
    on rejection. ``surface``/``check_class``/``path`` come only from
    ``request``, never from the tool payload."""
    if request.text is None:
        raise EvidenceRejected("cannot emit a finding when the document is confirmed absent")

    allowed_codes = REASON_CODES_BY_CLASS.get(request.check_class)
    if allowed_codes is None:
        raise EvidenceRejected(f"no reason codes defined for check class {request.check_class!r}")
    if reason_code not in allowed_codes:
        raise EvidenceRejected(
            f"reason code {reason_code!r} is not valid for {request.check_class!r} "
            f"(allowed: {allowed_codes})"
        )

    expected_count = EXPECTED_EVIDENCE_COUNT[request.check_class]
    if len(evidence) != expected_count:
        raise EvidenceRejected(
            f"{request.check_class!r} requires exactly {expected_count} evidence "
            f"location(s), got {len(evidence)}"
        )

    lines = request.text.split("\n")
    for item in evidence:
        _validate_evidence_item(item, lines)

    # Deterministic construction from here on — no model prose.
    primary = evidence[0]
    location = f"{request.path}:{primary.line}"
    if len(evidence) == 1:
        normalized_content = f"{reason_code}|{primary.excerpt}"
        detail = f"{reason_code} at line {primary.line}: {primary.excerpt!r}"
    else:
        secondary = evidence[1]
        normalized_content = f"{reason_code}|{primary.excerpt}|{secondary.excerpt}"
        detail = (
            f"{reason_code}: line {primary.line} ({primary.excerpt!r}) "
            f"contradicts line {secondary.line} ({secondary.excerpt!r})"
        )

    return ObservedFinding(
        surface=request.surface,
        check_class=request.check_class,
        location=location,
        detail=detail,
        normalized_content=normalized_content,
    )

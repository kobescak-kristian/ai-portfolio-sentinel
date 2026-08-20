"""The one in-process MCP tool the caged checker agent may call
(dispatch q77-p3-a, sections C/D): ``emit_finding``. Built fresh per
``JudgmentRequest`` via ``build_emit_finding_tool`` — bound to exactly
one request, so a tool call can never name a different task's surface,
check_class or path; those never come from the model.

ADR-0008 addition (dispatch q77-p3-adr8-impl-a): each proposal now also
leaves a bounded ``ToolAttemptRecord`` in an in-memory buffer, so a
judgment call that later fails can still be diagnosed per-proposal
instead of collapsing to a bare attempt counter. The buffer is
persisted on every caught terminal path, atomically with the call's
finalization (see harness.py); nothing here writes to SQLite while the
SDK invocation is still running.

Data minimization: the persisted record carries the proposed reason
code, bounded evidence coordinates and a CLOSED rejection category —
never chain-of-thought, a full model response, a transcript, a raw
prompt, or arbitrary raw tool JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from claude_agent_sdk import tool

from agents.checker.config import MAX_TOOL_CALLS_PER_CHECK, TOOL_NAME
from agents.checker.evidence import (
    EXPECTED_EVIDENCE_COUNT,
    REASON_CODES_BY_CLASS,
    EvidenceItem,
    EvidenceRejected,
    build_observed_finding,
)
from checks.base import ObservedFinding
from checks.judgment.stubs import JudgmentRequest
from sentinel.logs import redact

# --- bounded audit vocabulary (ADR-0008 section 4) --------------------

ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"
DUPLICATE = "DUPLICATE"
BREAKER_REFUSED = "BREAKER_REFUSED"

TOOL_ATTEMPT_OUTCOMES = frozenset({ACCEPTED, REJECTED, DUPLICATE, BREAKER_REFUSED})

# Closed rejection categories. These mirror the ordered checks inside
# agents/checker/evidence.py::build_observed_finding, which is NOT
# modified — the constants it exports are reused read-only here, and a
# consistency test pins this classifier against evidence.py's actual
# behaviour so the two cannot drift.
DOCUMENT_ABSENT = "DOCUMENT_ABSENT"
EVIDENCE_NOT_A_LIST = "EVIDENCE_NOT_A_LIST"
MALFORMED_EVIDENCE_ITEM = "MALFORMED_EVIDENCE_ITEM"
REASON_CODE_NOT_ALLOWED = "REASON_CODE_NOT_ALLOWED"
EVIDENCE_COUNT_MISMATCH = "EVIDENCE_COUNT_MISMATCH"
LINE_OUT_OF_RANGE = "LINE_OUT_OF_RANGE"
EXCERPT_EMPTY = "EXCERPT_EMPTY"
EXCERPT_NOT_VERBATIM = "EXCERPT_NOT_VERBATIM"

REJECTION_CATEGORIES = frozenset(
    {
        DOCUMENT_ABSENT,
        EVIDENCE_NOT_A_LIST,
        MALFORMED_EVIDENCE_ITEM,
        REASON_CODE_NOT_ALLOWED,
        EVIDENCE_COUNT_MISMATCH,
        LINE_OUT_OF_RANGE,
        EXCERPT_EMPTY,
        EXCERPT_NOT_VERBATIM,
    }
)

#: Hard bound on the persisted proposed reason code. The model supplies
#: this string, so it is length-capped and control-stripped before it
#: can reach the ledger.
MAX_REASON_CODE_CHARS = 64

#: Hard bound on the one retained model-proposed excerpt snippet. See
#: the section 9A determination recorded in DATA_RETENTION_POLICY.md:
#: the snippet is stored ONLY on a rejection whose discriminator is the
#: proposed text itself, and only far enough to tell a near-miss from a
#: fabrication.
MAX_PROPOSED_EXCERPT_CHARS = 80

#: The rejection categories whose diagnostic discriminator IS the
#: proposed text. For every other category the coordinates and the
#: category already carry the full distinction, so no text is retained.
_TEXT_DISCRIMINATED_CATEGORIES = frozenset({EXCERPT_NOT_VERBATIM})


def bound_reason_code(value: object) -> str:
    """Control-strip and length-cap a model-supplied reason code."""
    text = "".join(ch for ch in str(value) if ord(ch) >= 32)
    return text[:MAX_REASON_CODE_CHARS]


def bound_excerpt(value: object) -> str:
    """Redact, then deterministically truncate, a model-proposed
    excerpt to the audit bound.

    Redaction reuses the existing first-party boundary
    ``sentinel/logs.py::redact`` — control-character stripping plus
    secret- and path-shaped token replacement — because a monitored
    document can itself carry injected secret-shaped text that the
    model might then propose back. Truncation is applied AFTER
    redaction so the stored value is bounded in its final form.

    Honest limitation: ``redact`` normalizes internal whitespace runs
    to single spaces, so a near-miss that differs from the source line
    only by repeated whitespace is not distinguishable in this field.
    Recorded in DATA_RETENTION_POLICY.md rather than worked around.
    """
    return redact(str(value))[:MAX_PROPOSED_EXCERPT_CHARS]


def _coerce_line(item: object) -> Optional[int]:
    """The proposed 1-based line, or None when the model supplied
    something that is not a usable coordinate at all."""
    if not isinstance(item, dict):
        return None
    try:
        line = int(item.get("line", -1))
    except (TypeError, ValueError):
        return None
    return line if line >= 1 else None


def _coerce_excerpt(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("excerpt", ""))


@dataclass(frozen=True)
class ToolAttemptRecord:
    """One ``emit_finding`` proposal's bounded audit record. Buffered in
    memory during an invocation; flushed durably with that call's
    terminal finalization."""

    ordinal: int
    proposed_reason_code: str
    proposed_evidence_count: int
    primary_line: Optional[int]
    secondary_line: Optional[int]
    outcome: str
    rejection_category: Optional[str] = None
    proposed_excerpt: Optional[str] = None


def classify_rejection(
    request: JudgmentRequest, *, reason_code: str, raw_evidence: object, exc: BaseException
) -> str:
    """Derive the closed rejection category structurally, in
    ``build_observed_finding``'s own check order. Never parses the
    exception's prose — the exception is used only to separate a
    host-contract rejection from a malformed-payload coercion error."""
    if not isinstance(exc, EvidenceRejected) and isinstance(exc, (TypeError, ValueError)):
        return MALFORMED_EVIDENCE_ITEM

    if request.text is None:
        return DOCUMENT_ABSENT
    if not isinstance(raw_evidence, list):
        return EVIDENCE_NOT_A_LIST

    allowed = REASON_CODES_BY_CLASS.get(request.check_class)
    if allowed is None or reason_code not in allowed:
        return REASON_CODE_NOT_ALLOWED

    expected = EXPECTED_EVIDENCE_COUNT.get(request.check_class)
    if expected is not None and len(raw_evidence) != expected:
        return EVIDENCE_COUNT_MISMATCH

    lines = request.text.split("\n")
    for item in raw_evidence:
        line = _coerce_line(item)
        if line is None or line > len(lines):
            return LINE_OUT_OF_RANGE
        excerpt = _coerce_excerpt(item)
        if not excerpt:
            return EXCERPT_EMPTY
        if excerpt not in lines[line - 1]:
            return EXCERPT_NOT_VERBATIM

    # Every structural precondition held, so the only remaining cause a
    # host rejection can have is a non-verbatim excerpt.
    return EXCERPT_NOT_VERBATIM


@dataclass
class CheckerToolState:
    """Fresh per SDK invocation — never shared across requests, never
    reused across a run, and (ADR-0008) never carried from a failed
    invocation into a retry. Accumulates host-validated findings and
    tracks the independent tool-call circuit breaker, the within-call
    dedup set, and the bounded per-proposal audit buffer for exactly
    one judgment call."""

    request: JudgmentRequest
    findings: list[ObservedFinding] = field(default_factory=list)
    tool_attempts: int = 0
    last_rejection_reason: str | None = None
    attempts: list[ToolAttemptRecord] = field(default_factory=list)
    _accepted_keys: set = field(default_factory=set)
    _breaker_tripped: bool = False

    def breaker_tripped(self) -> bool:
        return self._breaker_tripped

    def _check_breaker(self) -> dict | None:
        self.tool_attempts += 1
        if self.tool_attempts > MAX_TOOL_CALLS_PER_CHECK:
            self._breaker_tripped = True
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Circuit breaker tripped: tool-call ceiling "
                            f"({MAX_TOOL_CALLS_PER_CHECK}) reached for this "
                            "check. No further tool calls will be served."
                        ),
                    }
                ],
                "is_error": True,
            }
        return None

    def _record_attempt(
        self,
        *,
        reason_code: str,
        raw_evidence: object,
        outcome: str,
        rejection_category: str | None = None,
        proposed_excerpt: str | None = None,
    ) -> None:
        count = len(raw_evidence) if isinstance(raw_evidence, list) else 0
        items = raw_evidence if isinstance(raw_evidence, list) else []
        self.attempts.append(
            ToolAttemptRecord(
                ordinal=self.tool_attempts,
                proposed_reason_code=bound_reason_code(reason_code),
                proposed_evidence_count=count,
                primary_line=_coerce_line(items[0]) if len(items) >= 1 else None,
                secondary_line=_coerce_line(items[1]) if len(items) >= 2 else None,
                outcome=outcome,
                rejection_category=rejection_category,
                proposed_excerpt=proposed_excerpt,
            )
        )

    def accept(self, *, reason_code: str, raw_evidence: list) -> dict:
        tripped = self._check_breaker()
        if tripped is not None:
            self._record_attempt(
                reason_code=reason_code, raw_evidence=raw_evidence, outcome=BREAKER_REFUSED
            )
            return tripped
        try:
            if not isinstance(raw_evidence, list):
                raise EvidenceRejected("evidence must be a list")
            evidence = [
                EvidenceItem(
                    line=int(item.get("line", -1)) if isinstance(item, dict) else -1,
                    excerpt=str(item.get("excerpt", "")) if isinstance(item, dict) else "",
                )
                for item in raw_evidence
            ]
            finding = build_observed_finding(
                self.request, reason_code=reason_code, evidence=evidence
            )
        except (EvidenceRejected, TypeError, ValueError) as exc:
            category = classify_rejection(
                self.request, reason_code=reason_code, raw_evidence=raw_evidence, exc=exc
            )
            # Persisted diagnostics carry the CLOSED category, never the
            # raw EvidenceRejected prose — that prose embeds the
            # model-proposed excerpt verbatim and unbounded, which would
            # bypass this table's data-minimization guarantee through an
            # older text field.
            self.last_rejection_reason = category
            snippet = None
            if category in _TEXT_DISCRIMINATED_CATEGORIES:
                offending = _first_non_verbatim_excerpt(self.request, raw_evidence)
                if offending is not None:
                    snippet = bound_excerpt(offending)
            self._record_attempt(
                reason_code=reason_code,
                raw_evidence=raw_evidence,
                outcome=REJECTED,
                rejection_category=category,
                proposed_excerpt=snippet,
            )
            # The model-facing response is deliberately unchanged: the
            # agent still sees why its proposal was refused.
            return {
                "content": [{"type": "text", "text": f"Rejected: {exc}"}],
                "is_error": True,
            }

        key = (finding.check_class, finding.location, finding.normalized_content)
        if key in self._accepted_keys:
            self._record_attempt(
                reason_code=reason_code, raw_evidence=raw_evidence, outcome=DUPLICATE
            )
            return {
                "content": [
                    {"type": "text", "text": "Already recorded — duplicate call ignored."}
                ]
            }
        self._accepted_keys.add(key)
        self.findings.append(finding)
        self._record_attempt(
            reason_code=reason_code, raw_evidence=raw_evidence, outcome=ACCEPTED
        )
        return {
            "content": [
                {"type": "text", "text": f"Recorded: {reason_code} at {finding.location}"}
            ]
        }


def _first_non_verbatim_excerpt(request: JudgmentRequest, raw_evidence: object) -> Optional[str]:
    """The specific proposed excerpt that failed the verbatim check —
    the one span whose content is the diagnostic discriminator."""
    if request.text is None or not isinstance(raw_evidence, list):
        return None
    lines = request.text.split("\n")
    for item in raw_evidence:
        line = _coerce_line(item)
        if line is None or line > len(lines):
            continue
        excerpt = _coerce_excerpt(item)
        if excerpt and excerpt not in lines[line - 1]:
            return excerpt
    return None


def build_emit_finding_tool(state: CheckerToolState):
    @tool(
        TOOL_NAME,
        "Report exactly one defect for the check class you were asked "
        "about. reason_code must be exactly one of the codes you were "
        "given in your instructions. evidence is a list of "
        "{line, excerpt} objects — line is the 1-based line number shown "
        "in the document you were given, and excerpt must be copied "
        "verbatim from that exact line. Call this once per genuine "
        "defect found; call it zero times if you find nothing.",
        {"reason_code": str, "evidence": list},
    )
    async def emit_finding(args):
        return state.accept(
            reason_code=str(args.get("reason_code", "")),
            raw_evidence=args.get("evidence") or [],
        )

    return emit_finding

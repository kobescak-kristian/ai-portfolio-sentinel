"""The one in-process MCP tool the caged checker agent may call
(dispatch q77-p3-a, sections C/D): ``emit_finding``. Built fresh per
``JudgmentRequest`` via ``build_emit_finding_tool`` — bound to exactly
one request, so a tool call can never name a different task's surface,
check_class or path; those never come from the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from claude_agent_sdk import tool

from agents.checker.config import MAX_TOOL_CALLS_PER_CHECK, TOOL_NAME
from agents.checker.evidence import EvidenceItem, EvidenceRejected, build_observed_finding
from checks.base import ObservedFinding
from checks.judgment.stubs import JudgmentRequest


@dataclass
class CheckerToolState:
    """Fresh per ``JudgmentRequest`` — never shared across requests or
    reused across a run. Accumulates host-validated findings and
    tracks the independent tool-call circuit breaker and within-call
    dedup for exactly one judgment call."""

    request: JudgmentRequest
    findings: list[ObservedFinding] = field(default_factory=list)
    tool_attempts: int = 0
    last_rejection_reason: str | None = None
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

    def accept(self, *, reason_code: str, raw_evidence: list) -> dict:
        tripped = self._check_breaker()
        if tripped is not None:
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
            self.last_rejection_reason = str(exc)
            return {
                "content": [{"type": "text", "text": f"Rejected: {exc}"}],
                "is_error": True,
            }

        key = (finding.check_class, finding.location, finding.normalized_content)
        if key in self._accepted_keys:
            return {
                "content": [
                    {"type": "text", "text": "Already recorded — duplicate call ignored."}
                ]
            }
        self._accepted_keys.add(key)
        self.findings.append(finding)
        return {
            "content": [
                {"type": "text", "text": f"Recorded: {reason_code} at {finding.location}"}
            ]
        }


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

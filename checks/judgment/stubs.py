"""Injectable judgment-stub boundary for the two Phase-3 classes
(stale-STATE-marker, missing-synthetic-label).

``NullJudgmentStub`` is the **production** implementation for Phase
2: it performs no I/O, no model call, and returns nothing — the two
classes' tasks still run and reach DONE, correctly producing zero
findings, which is what lets "every task terminal" honestly cover all
six check classes while the judgment capability is absent. Phase 3
replaces only this one seam with a caged checker agent.
``ScriptedJudgmentStub`` is a test double only — it is never imported
by any production code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence, Union

from checks.base import ObservedFinding


@dataclass(frozen=True)
class JudgmentRequest:
    surface: str
    check_class: str
    path: str
    text: str | None  # LF-normalized text, or None if the file is confirmed absent


class JudgmentStub(Protocol):
    def judge(self, request: JudgmentRequest) -> Sequence[ObservedFinding]: ...


class NullJudgmentStub:
    def judge(self, request: JudgmentRequest) -> Sequence[ObservedFinding]:
        return ()


@dataclass
class ScriptedJudgmentStub:
    """``script`` maps (surface, check_class) -> a findings sequence
    or an exception instance to raise."""

    script: Mapping[tuple[str, str], Union[Sequence[ObservedFinding], BaseException]]
    calls: list[JudgmentRequest] = field(default_factory=list)

    def judge(self, request: JudgmentRequest) -> Sequence[ObservedFinding]:
        self.calls.append(request)
        result = self.script.get((request.surface, request.check_class), ())
        if isinstance(result, BaseException):
            raise result
        return list(result)

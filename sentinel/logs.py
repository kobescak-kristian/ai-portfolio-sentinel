"""Structured JSONL run logging.

Same serialization discipline ``telemetry/cost_ledger.py`` already
froze for the cost ledger: UTF-8, sorted keys, compact separators, no
NaN/Infinity, newline-terminated, one object per line. Reuses the
repo's existing path-guard convention directly (``contracts.schemas``
is frozen and must not be modified, so a private-name import with this
comment is the deliberate trade — it guarantees the log guard and the
ledger guard can never drift apart) plus a secret-token guard and
control-character stripping. Written only to the explicit ``--log``
path; local, gitignored, never a committed artifact at Phase 2.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from contracts.schemas import (
    _DETAIL_DRIVE_ROOT,
    _DETAIL_POSIX_ABSOLUTE,
    _UNC_ANYWHERE,
    serialize_db_datetime,
)

EVENTS: frozenset[str] = frozenset(
    {
        "run.started",
        "run.recovered",
        "run.failed",
        "run.completed",
        "task.claimed",
        "task.done",
        "task.failed",
        "task.dead_letter",
        "finding.new",
        "finding.advanced",
        "finding.resolved",
        "finding.recurrence",
        "finding.rejected",
        "finding.duplicate_within_run",
        "finding.scope_not_scanned",
        "inventory.repos_listed",
        "inventory.tree_truncated",
        "inventory.unavailable",
        "http.retry",
        "http.budget_exceeded",
        "report.appended",
        "report.section_already_present",
        "cost.row_appended",
        # Phase 4 (adr/0010-phase4-loop-safety-controls; dispatch
        # q77-p4-runner-a). The bounded-loop supervisor reuses this
        # logger rather than introducing a second logging system, so
        # loop events inherit the same redaction, path and secret
        # controls as every run-level event above. ADR-0010 section 5
        # requires the breaker events to be ERROR severity; severity is
        # the caller's argument, so it is asserted in the loop tests,
        # not encoded here.
        "loop.started",
        "loop.iteration_intent",
        "loop.iteration_finalized",
        "loop.recovered",
        "loop.completed",
        "loop.failed",
        "breaker.cost_tripped",
        "breaker.consecutive_failure_tripped",
    }
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ghp_[A-Za-z0-9]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}"),
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"\btoken=\S+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z_]*key\b\s*=\s*\S+", re.IGNORECASE),
)


def _looks_like_machine_local_path(token: str) -> bool:
    """Reuses contracts.schemas's *detail*-flavored path guard (the
    one that deliberately permits ``scheme://host/path`` URLs) rather
    than its stricter identifier guard — log messages, like ``detail``,
    are free text that legitimately carries URLs."""
    return bool(
        _DETAIL_DRIVE_ROOT.search(token)
        or _UNC_ANYWHERE.search(token)
        or _DETAIL_POSIX_ABSOLUTE.search(token)
    )


def redact(text: str) -> str:
    """Strip control characters, redact secret-shaped substrings and
    path-shaped tokens, and truncate to 200 characters. Every
    free-text field (error_message, surface, detail-derived strings)
    goes through this — ``str(exc)`` is never logged raw, since some
    exceptions embed a filename. Secret patterns are matched against
    the whole string first (some, like ``Bearer <token>``, span a
    space), then path-shaped tokens are redacted individually so an
    ordinary URL is never mistaken for a local path."""
    stripped = "".join(ch for ch in text if ord(ch) >= 32)
    for pattern in _SECRET_PATTERNS:
        stripped = pattern.sub("<redacted-secret>", stripped)
    tokens = stripped.split()
    redacted = " ".join(
        "<redacted-path>" if _looks_like_machine_local_path(t) else t for t in tokens
    )
    if len(redacted) > 200:
        redacted = redacted[:200] + "…"
    return redacted


class RunLogger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a", encoding="utf-8", newline="\n")

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "RunLogger":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def log(
        self,
        severity: str,
        event: str,
        *,
        now: datetime,
        run_id: str | None = None,
        task_id: str | None = None,
        check_class: str | None = None,
        surface: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        **extra: Any,
    ) -> None:
        if event not in EVENTS:
            raise ValueError(f"{event!r} is not a declared log event")
        record: dict[str, Any] = {
            "schema_version": 1,
            "ts": serialize_db_datetime(now),
            "severity": severity,
            "event": event,
        }
        if run_id is not None:
            record["run_id"] = run_id
        if task_id is not None:
            record["task_id"] = task_id
        if check_class is not None:
            record["check_class"] = check_class
        if surface is not None:
            record["surface"] = redact(surface)
        if error_type is not None:
            record["error_type"] = error_type
        if error_message is not None:
            record["error_message"] = redact(error_message)
        for key, value in extra.items():
            record[key] = redact(value) if isinstance(value, str) else value
        line = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self._handle.write(line + "\n")
        self._handle.flush()

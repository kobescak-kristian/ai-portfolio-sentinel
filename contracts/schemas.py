"""Typed handoff contracts (BLUEPRINT §3, §4).

Phase 0 lands CostRow only — the cost-telemetry harness exists before
the thing it measures (BLUEPRINT §6 P0). CheckTask, Finding and
RunRecord land at Phase 1 with the eval-gate freeze.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Free-text path guard: ledger rows must never carry machine-local
# absolute paths (public repo; telemetry is committed evidence).
# Delimiter-aware: a separator character in front of "/" or "\\" marks
# the start of an embedded path; a slash inside a plain identifier
# such as "provider/model-name" does not.
_DELIM = r"[\s=:\"',;(\[{]"
_POSIX_ABSOLUTE = re.compile(rf"(?:^|(?<={_DELIM}))/")
_UNC = re.compile(rf"(?:^|(?<={_DELIM}))\\\\")
_DRIVE_ROOT = re.compile(r"[A-Za-z]:[\\/]")


def _contains_machine_local_path(value: str) -> bool:
    return bool(
        _DRIVE_ROOT.search(value)
        or _POSIX_ABSOLUTE.search(value)
        or _UNC.search(value)
    )


class CostRow(BaseModel):
    """One telemetry row per run — every run, including dev, writes one.

    Frozen contract: exactly these eight fields, no additions, no
    renames. Currency is integer micro-euros; floating-point currency
    never appears in the ledger.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: str
    recorded_at_utc: datetime
    run_kind: Literal["dev", "eval", "live"]
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_eur_micros: int = Field(ge=0)

    @field_validator("run_id", "model")
    @classmethod
    def _free_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty after stripping")
        if _contains_machine_local_path(value):
            raise ValueError("machine-local absolute paths are not permitted")
        return value

    @field_validator("recorded_at_utc")
    @classmethod
    def _utc_only(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if value.tzinfo is None or offset is None:
            raise ValueError("naive datetimes are not permitted")
        if offset != timedelta(0):
            raise ValueError("UTC offset must be exactly zero")
        return value

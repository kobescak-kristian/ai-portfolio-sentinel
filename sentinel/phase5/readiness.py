"""P5-D replacement readiness matrix (ADR-0012 section 22, Amendment A9 and
Amendment B; Stage 2C-B6-3a, owner-approved plan revision 4 and owner
rulings D1 to D5, D7 and R6 to R10 of 2026-10-02).

What this module is. The frozen definition of the 25-row readiness matrix,
the strict record the B6-3b stage commits, and the PURE evaluators that turn
injected facts into row and component results. It reads no clock, no
environment, no file, no git repository and no network: every fact is
supplied by the caller (``scripts/run_phase5_readiness.py`` collects them).
It authorizes nothing: no marker, no latch record, no arming, no provider
access. stdlib + pydantic only.

Frozen semantics (plan revision 4).

- Rows 1 to 23 are the ADR list verbatim; rows 24 and 25 are Amendment B.
  Row 4 has components 4.1 (mechanism) and 4.2 (replacement rule); row 16 has
  components 16a to 16l. Every other row has one component.
- A component status is PASS, FAIL or DEFERRED. DEFERRED is legal only for
  4.2, 16h and 16i (D7: provider state that cannot exist before arming).
  Aggregation is FAIL > DEFERRED > PASS at every level, and a row entry whose
  stated status differs from its components' aggregate is INVALID, so an
  aggregate can never hide a FAIL.
- Evidence states (R8). ``T0`` is durable evidence; ``PREARM_BASELINE`` is
  collected in B6-3 before the readiness source commit R exists and never
  satisfies a final requirement; ``FINAL_T2`` exists only after R, must not
  predate R and must be at most two hours old at commit A (D5). A B6-3
  record may carry only T0 and PREARM_BASELINE evidence.
- The B6-3b record carries ``closure: PENDING_POST_PUSH_CI`` (R9). Its schema
  has no field that could hold a result for its own commit.
- ``evaluate_arming_eligibility`` mechanically requires the exact deferred
  shape (D7, R8): rows 4 and 16 DEFERRED with exactly {4.2, 16h, 16i}
  DEFERRED, every other row PASS, no FAIL anywhere.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, StrictInt, model_validator

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
MATRIX_VERSION = 1
STAGE = "B6-3b"
CLOSURE_PENDING = "PENDING_POST_PUSH_CI"
T2_MAX_AGE = timedelta(hours=2)  # owner ruling D5

OFFICIAL_WORKFLOW = ".github/workflows/sentinel-official-gate.yml"
PROBE_WORKFLOW = ".github/workflows/sentinel-latch-read-probe.yml"
PROBE_JOB_NAME = "gate"

REPLACEMENT_REPOSITORY = "kobescak-kristian/ai-portfolio-sentinel"
SDK_VERSION = "0.2.110"
REQUIRED_PRIMARY_MODEL = "claude-sonnet-5"
ALLOWED_MODEL_KEYS = frozenset({"claude-haiku-4-5-20251001", "claude-sonnet-5"})
PROHIBITED_OVERRIDE_ENV = (
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)
STATIC_CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
RESOLVED_MODEL_UNAVAILABLE = "UNAVAILABLE"

EXPECTED_ENVELOPE_ID = "3380e09da8afa056a3a3a9af8df68d886e3f02683cebfeabbf2fa658c5d62598"
EXPECTED_A8_BINDING_SHA256 = "891636d2396e1e80f1ee3a9c444b7a0e37700ff2d93f5555d2c166da9c55f1f3"
EXPECTED_REGISTRY_SHA256 = "f64c83afebf5064a6d4dd12b3f41c5df01edfa2e369f7f53b8cdcef5bc301f39"
EXPECTED_LATCH_FILE_SHA256 = "37799c774db5d7b7281d5c9290b76b531d849a6feaa30ea21a75d51996e6ee5a"
EXPECTED_LATCH_HEAD_SHA256 = "af5446752c3abed4f5b87efcc55027f22eb69d56c7b70b85a371bf9f9f69019b"
EXPECTED_STALL_BUDGET_MS = 600000
EXPECTED_WORKFLOW_TIMEOUT_MINUTES = 106
EXPECTED_ENVELOPE_VERSION = "1"

# Evidence sources for the carry-forward rule (owner ruling D4).
B2_SOURCE_SHA = "d93ba557ce907ba7f1f1c973467af768624bd194"
B5_SOURCE_SHA = "28e69e2fc42a33c24fcf530bf26afa4e9251ee20"
ORIGINAL_RUN_SOURCE_SHA = "eef88a289cf465ad352ee223221d5497465469b3"
B2_ARTIFACT_DIGESTS = (
    ("sentinel-p5-rehearsal-r35541478181-a1", "10615366134",
     "sha256:0a826d1ed79b3d99f90314e83b71eee986eaf11a7bedbdbb50e971cd602c29e4"),
    ("sentinel-p5-rehearsal-observations-r35541478181-a1", "10615345592",
     "sha256:34748c5253c177fed5f57cfbe4832df8ffe42a385b92e429334252339d04a698"),
)
B5_ARTIFACT_DIGESTS = (
    ("sentinel-p5-timing-r36903206215-a1", "11182223404",
     "sha256:baca64d33bd981a0c75af9b644b5987060455478137a9da6601736c318db2aac"),
)
B5_IDENTITY_FILE_SHA256 = "41701a9cfe40058844bc9c39baaa29539cad68dee8357eede995980b13011b57"
B5_RUNTIME_IDENTITY_ID = "5d9e357406c5b9f081de1d94c341ff9f29a24bf7323b623aaafef1c61bc6a2e5"

FINALIZATION_PATHS = (
    "scripts/run_phase5_gate_finalizer.py",
    "sentinel/phase5/terminal.py",
    "sentinel/phase5/journal.py",
)
PROCESS_CONTROL_PATHS = (
    "agents/checker/process_control.py",
    "agents/checker/envelope_guard.py",
)
INVOCATION_PATHS = (
    "agents", "contracts", "requirements.txt", "requirements-dev.txt", "evals", "fixtures",
    "rehearsal", "sentinel/phase5/runtime_identity.py", "sentinel/ledger.py",
)
WIF_MECHANISM_PATHS = ("agents/checker/auth.py", "agents/checker/oidc.py", "requirements.txt")
# The one official-workflow change D4 allows between B2 and now: the
# separately governed B6-1 timeout binding.
ALLOWED_WORKFLOW_DIFF_LINES = ("-    timeout-minutes: 30", "+    timeout-minutes: 106")

# Rows whose evidence is exact-SHA CI plus the presence of their covering test files.
ROW_TEST_FILES: Mapping[str, tuple[str, ...]] = {
    "1": ("tests/test_phase5_gate_runner.py",),
    "7": ("tests/test_phase5_gate_runner.py", "tests/test_phase5_terminal.py"),
    "10": ("tests/test_checker_process_control.py", "tests/test_phase5_kill_rehearsal.py"),
    "11": ("tests/test_phase5_terminal.py", "tests/test_phase5_journal.py"),
    "12": ("tests/test_phase5_gate_finalizer.py", "tests/test_phase5_workflow_contracts.py"),
    "18": ("tests/test_phase5_workflow_contracts.py", "tests/test_phase5_latch.py", "tests/test_phase5_gate_runner.py"),
    "19": ("tests/test_phase5_window_freeze.py",),
    "22": ("tests/test_phase5_gate_runner.py", "tests/test_phase5_workflow_contracts.py"),
}
QUALITY_PATHS = (
    "fixtures", "evals", "checks", "contracts", "agents", "scripts/run_phase3_dev_gate.py",
    "sentinel/pipeline.py", "sentinel/config.py", "sentinel/costs.py", "sentinel/ledger.py",
    "requirements.txt",
)
# Quality-path changes since the original official run source that are
# allowed: path -> (git status, sha256 of ``git diff --no-color`` text, or
# None for a new file). The harness hunk adds the attempt-count parameter
# whose default equals the frozen constant; oidc.py is the B5-P0 repair.
QUALITY_ALLOWED_DIFFS: Mapping[str, tuple[str, str | None]] = {
    "agents/checker/envelope_guard.py": ("A", None),
    "agents/checker/process_control.py": ("A", None),
    "agents/checker/oidc.py": ("M", "8d12109d09ab7c9e49625603be1f05623cfdf30b41d5ec465015b4913177eb03"),
    "agents/checker/harness.py": ("M", "4316ef0b9b1df5dde5e96ed611d5b42adcd37ca7697926402a9bc720b10b9bf9"),
}

_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_COMPONENT = re.compile(r"[0-9]{1,2}(\.[0-9]|[a-l])?")
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")

EvidenceState = Literal["T0", "PREARM_BASELINE", "FINAL_T2"]
Status = Literal["PASS", "FAIL", "DEFERRED"]


class ReadinessError(RuntimeError):
    """A record, input or write set violates the frozen readiness contract.
    Never carries a token, a secret or a local path."""


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RowDef:
    row_id: int
    title: str  # ADR wording, verbatim (tests compare against the ADR text)
    source: str
    locus: str  # LOCAL | GITHUB | RUNNER | PROVIDER | MIXED
    mode: str  # MODEL_FREE | REAL_PROVIDER
    tiers: tuple[str, ...]
    components: tuple[str, ...]


def _row(row_id: int, title: str, source: str, locus: str, tiers: tuple[str, ...],
         mode: str = "MODEL_FREE", components: tuple[str, ...] | None = None) -> RowDef:
    return RowDef(row_id, title, source, locus, mode, tiers, components or (str(row_id),))


_ROW16 = tuple(f"16{letter}" for letter in "abcdefghijkl")

ROW_DEFS: tuple[RowDef, ...] = (
    _row(1, "clean runtime and dependency closure", "ADR-0012 22.1", "MIXED", ("T0", "T1")),
    _row(2, "deployment parity", "ADR-0012 22.2", "RUNNER", ("T0", "T2")),
    _row(3, "artifact access", "ADR-0012 22.3", "MIXED", ("T0", "T2")),
    _row(4, "OIDC/WIF production path", "ADR-0012 22.4", "PROVIDER", ("T2",), "REAL_PROVIDER", ("4.1", "4.2")),
    _row(5, "work-root behavior", "ADR-0012 22.5", "MIXED", ("T0",)),
    _row(6, "replacement-marker semantics", "ADR-0012 22.6", "LOCAL", ("T0", "T1", "T3")),
    _row(7, "ordinary infrastructure exception", "ADR-0012 22.7", "LOCAL", ("T0",)),
    _row(8, "per-invocation timeout", "ADR-0012 22.8", "LOCAL", ("T0", "T1")),
    _row(9, "session timeout, including the job-start anchor", "ADR-0012 22.9", "MIXED", ("T0",)),
    _row(10, "local external-process kill", "ADR-0012 22.10", "MIXED", ("T0",)),
    _row(11, "journal and evidence behavior after process loss", "ADR-0012 22.11", "GITHUB", ("T0",)),
    _row(12, "workflow finalizer contract", "ADR-0012 22.12", "MIXED", ("T0", "T1")),
    _row(13, "real GitHub job-level kill rehearsal", "ADR-0012 22.13", "GITHUB", ("T0",)),
    _row(14, "real Sonnet timing rehearsal", "ADR-0012 22.14", "MIXED", ("T0",)),
    _row(15, "frozen quality-surface equivalence", "ADR-0012 22.15", "LOCAL", ("T0", "T1")),
    _row(16, "exact source and configuration readiness binding", "ADR-0012 22.16", "MIXED",
         ("T0", "T1", "T2"), components=_ROW16),
    _row(17, "original marker never reset", "ADR-0012 22.17", "MIXED", ("T0", "T2")),
    _row(18, "no automatic replacement retry", "ADR-0012 22.18", "LOCAL", ("T0", "T1", "T3")),
    _row(19, "P5-E replacement provenance compatibility", "ADR-0012 22.19", "LOCAL", ("T0",)),
    _row(20, "cost-class and accounting correctness", "ADR-0012 22.20", "LOCAL", ("T0",)),
    _row(21, "durable receipts for historical Phase-5 evidence created and verified before the earliest "
             "artifact expiry (A1)", "ADR-0012 A9.21", "LOCAL", ("T0", "T2")),
    _row(22, "no pre-publication quality exposure on any operator-visible surface, for both GREEN and "
             "HONEST_FAIL (A2)", "ADR-0012 A9.22", "LOCAL", ("T0", "T1")),
    _row(23, "cancellation-path finalization and publication within the platform cancellation window (A5)",
         "ADR-0012 A9.23", "GITHUB", ("T0",)),
    _row(24, "durable latch GitHub-read capability", "ADR-0012 Amendment B", "RUNNER", ("T0", "T2")),
    _row(25, "durable replacement latch state / enforcement", "ADR-0012 Amendment B", "MIXED",
         ("T0", "T2", "T3")),
)
ROW_BY_ID: Mapping[int, RowDef] = {row.row_id: row for row in ROW_DEFS}
ROW_IDS = tuple(row.row_id for row in ROW_DEFS)
ALL_COMPONENTS: tuple[str, ...] = tuple(c for row in ROW_DEFS for c in row.components)
DEFERRED_COMPONENTS = frozenset({"4.2", "16h", "16i"})
DEFERRED_ROWS = frozenset({4, 16})
assert ROW_IDS == tuple(range(1, 26)) and len(set(ALL_COMPONENTS)) == len(ALL_COMPONENTS)


def aggregate(statuses: Iterable[str]) -> str:
    """FAIL dominates DEFERRED dominates PASS."""
    seen = set(statuses)
    if not seen:
        raise ReadinessError("aggregate of no components")
    if "FAIL" in seen:
        return "FAIL"
    if "DEFERRED" in seen:
        return "DEFERRED"
    return "PASS"


# ---------------------------------------------------------------------------
# Record schema (strict, canonical JSON, no result for its own commit)
# ---------------------------------------------------------------------------


def _require_server_utc(value: datetime, label: str) -> datetime:
    offset = value.utcoffset() if isinstance(value, datetime) else None
    if not isinstance(value, datetime) or value.tzinfo is None or offset is None or offset != timedelta(0):
        raise ValueError(f"{label} must be an aware UTC datetime")
    if value.microsecond != 0:
        raise ValueError(f"{label} must have whole-second precision (GitHub server time)")
    return value


def _json_safe(value: object, *, depth: int = 0) -> None:
    """Bounded, JSON-safe evidence only: no floats, no huge structures."""
    if depth > 6:
        raise ValueError("evidence nesting too deep")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        if len(value) > 4096:
            raise ValueError("evidence string too long")
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 4096:
            raise ValueError("evidence list too long")
        for item in value:
            _json_safe(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 256:
            raise ValueError("evidence object too large")
        for key, item in value.items():
            if not isinstance(key, str) or not 0 < len(key) <= 96:
                raise ValueError("evidence keys must be short strings")
            _json_safe(item, depth=depth + 1)
        return
    raise ValueError("evidence must be JSON-safe (str, int, bool, null, list, object)")


class Component(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component: str
    status: Status
    evidence_state: EvidenceState
    collected_at_utc: datetime
    evidence: dict
    reason: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "Component":
        if not _COMPONENT.fullmatch(self.component) or self.component not in ALL_COMPONENTS:
            raise ValueError("unknown component")
        _require_server_utc(self.collected_at_utc, "collected_at_utc")
        _json_safe(self.evidence)
        if self.status == "FAIL" and not (self.reason or "").strip():
            raise ValueError("a FAIL component must state its reason")
        if self.status == "DEFERRED":
            if self.component not in DEFERRED_COMPONENTS:
                raise ValueError("DEFERRED is legal only for components 4.2, 16h and 16i")
            if not isinstance(self.evidence.get("frozen_definition"), str) or not (self.reason or "").strip():
                raise ValueError("a DEFERRED component must reference its frozen definition and state its reason")
        if self.evidence_state == "FINAL_T2":
            raise ValueError("FINAL_T2 evidence cannot exist before the readiness source commit R")
        return self


class RowEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    row_id: StrictInt
    status: Status
    components: tuple[Component, ...]

    @model_validator(mode="after")
    def _validate(self) -> "RowEntry":
        row = ROW_BY_ID.get(self.row_id)
        if row is None:
            raise ValueError("unknown row")
        if tuple(c.component for c in self.components) != row.components:
            raise ValueError("components must be exactly the row's components, in order")
        if self.status != aggregate(c.status for c in self.components):
            raise ValueError("row status must equal the aggregate of its components (FAIL > DEFERRED > PASS)")
        return self


class Adjudication(BaseModel):
    """An owner-accepted transitive dependency difference (R7). A rejected
    difference is a STOP and is never recorded as a row result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    package: str
    baseline_version: str | None
    observed_version: str | None
    decision: Literal["ACCEPTED"]
    ruling_ref: str

    @model_validator(mode="after")
    def _validate(self) -> "Adjudication":
        if not _PACKAGE.fullmatch(self.package):
            raise ValueError("package must be a normalized distribution name")
        if not _IDENTIFIER.fullmatch(self.ruling_ref):
            raise ValueError("ruling_ref must be a bounded identifier")
        if self.baseline_version == self.observed_version:
            raise ValueError("an adjudicated difference must differ")
        return self


class CiEvidence(BaseModel):
    """Exact-SHA CI success. The B6-3b record may name the B6-3a SHA only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha: str
    run_id: str
    head_sha: str
    conclusion: Literal["success"]

    @model_validator(mode="after")
    def _validate(self) -> "CiEvidence":
        if not _HEX40.fullmatch(self.sha) or not _HEX40.fullmatch(self.head_sha):
            raise ValueError("sha and head_sha must be 40 lowercase hexadecimal characters")
        if self.sha != self.head_sha:
            raise ValueError("CI evidence must be for exactly the named SHA")
        if not self.run_id.isdigit():
            raise ValueError("run_id must be a decimal string")
        return self


class ReadinessRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    matrix_version: Literal[1]
    stage: Literal["B6-3b"]
    recorded_at_utc: datetime
    base_source_sha: str
    closure: Literal["PENDING_POST_PUSH_CI"]
    ci: tuple[CiEvidence, ...]
    rows: tuple[RowEntry, ...]
    adjudications: tuple[Adjudication, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> "ReadinessRecord":
        _require_server_utc(self.recorded_at_utc, "recorded_at_utc")
        if not _HEX40.fullmatch(self.base_source_sha):
            raise ValueError("base_source_sha must be 40 lowercase hexadecimal characters")
        if tuple(r.row_id for r in self.rows) != ROW_IDS:
            raise ValueError("rows must be exactly rows 1 to 25, once each, in order")
        if not self.ci or any(c.sha != self.base_source_sha for c in self.ci):
            raise ValueError("CI evidence must exist and name the B6-3a SHA only")
        for entry in self.rows:
            for comp in entry.components:
                if comp.collected_at_utc > self.recorded_at_utc:
                    raise ValueError("a component cannot be collected after the record")
        packages = [a.package for a in self.adjudications]
        if len(packages) != len(set(packages)):
            raise ValueError("duplicate adjudication")
        return self


def component_map(record: ReadinessRecord) -> dict[str, Component]:
    return {c.component: c for r in record.rows for c in r.components}


# ---------------------------------------------------------------------------
# Eligibility (D7 / R8): the exact deferred shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reasons: tuple[str, ...]


def evaluate_arming_eligibility(record: ReadinessRecord) -> Eligibility:
    """A rows verdict only. It can never produce a closed or PASS state by
    itself (R9): closure is the post-push CI of the B6-3b commit."""
    reasons: list[str] = []
    comps = component_map(record)
    failed = sorted(name for name, c in comps.items() if c.status == "FAIL")
    if failed:
        reasons.append("FAIL components: " + ", ".join(failed))
    deferred = {name for name, c in comps.items() if c.status == "DEFERRED"}
    if deferred != DEFERRED_COMPONENTS:
        reasons.append("deferred components must be exactly 4.2, 16h and 16i")
    for entry in record.rows:
        want = "DEFERRED" if entry.row_id in DEFERRED_ROWS else "PASS"
        if entry.status != want and entry.status != "FAIL":
            reasons.append(f"row {entry.row_id} must be {want}, is {entry.status}")
    for name, comp in comps.items():
        if comp.status == "PASS" and comp.evidence_state not in ("T0", "PREARM_BASELINE"):
            reasons.append(f"component {name} carries a non-B6-3 evidence state")
    drift = comps["16g"].evidence.get("transitive_differences")
    reasons.extend(_adjudication_reasons(drift, record.adjudications))
    return Eligibility(eligible=not reasons, reasons=tuple(reasons))


def _adjudication_reasons(drift: object, adjudications: Sequence[Adjudication]) -> list[str]:
    diffs = drift if isinstance(drift, list) else []
    wanted = {
        (d.get("package"), d.get("baseline_version"), d.get("observed_version"))
        for d in diffs if isinstance(d, dict)
    }
    given = {(a.package, a.baseline_version, a.observed_version) for a in adjudications}
    reasons = []
    if wanted - given:
        reasons.append("transitive differences without owner adjudication: " + ", ".join(sorted(str(w[0]) for w in wanted - given)))
    if given - wanted:
        reasons.append("adjudication for a difference that was not observed")
    return reasons


def record_bytes(record: ReadinessRecord) -> bytes:
    from .models import canonical_json_bytes

    return canonical_json_bytes(record) + b"\n"


def record_sha256(record: ReadinessRecord) -> str:
    return hashlib.sha256(record_bytes(record)).hexdigest()


# ---------------------------------------------------------------------------
# Freshness (D5, R8)
# ---------------------------------------------------------------------------


def final_t2_satisfied(
    component: Component | None, *, evidence_state: str, collected_at_utc: datetime,
    r_commit_time: datetime, commit_a_time: datetime,
) -> tuple[bool, str]:
    """A final T2 requirement: only ``FINAL_T2`` evidence counts; it must be
    stamped at or after R and at most two hours before commit A. A
    PREARM_BASELINE entry never satisfies it."""
    if evidence_state != "FINAL_T2":
        return False, "only FINAL_T2 evidence can satisfy a final T2 requirement"
    if collected_at_utc < r_commit_time:
        return False, "FINAL_T2 evidence predates the readiness source commit R"
    if collected_at_utc > commit_a_time:
        return False, "evidence is stamped after commit A"
    if commit_a_time - collected_at_utc > T2_MAX_AGE:
        return False, "evidence is more than 2 hours old at commit A"
    return True, "ok"


PRE_DISPATCH_FACTS = ("repository_head", "github_variables", "run_history", "latch_state", "scheduler_state")
PRE_DISPATCH_EXTENDED_FACTS = ("model_lifecycle", "rule_state", "cap_state")


def pre_dispatch_required_facts(commit_a_time: datetime, dispatch_time: datetime) -> tuple[str, ...]:
    """D5 item 9 (flagged interpretation): the 24 h latch window never waives a
    safety-critical change, so a dispatch more than 2 h after commit A also
    re-reads lifecycle, rule and cap state."""
    if dispatch_time < commit_a_time:
        raise ReadinessError("dispatch cannot precede commit A")
    if dispatch_time - commit_a_time > T2_MAX_AGE:
        return PRE_DISPATCH_FACTS + PRE_DISPATCH_EXTENDED_FACTS
    return PRE_DISPATCH_FACTS


def evaluate_pre_dispatch_drift(
    before: Mapping[str, str], now: Mapping[str, str], required: Sequence[str]
) -> tuple[bool, tuple[str, ...]]:
    """Change detection only: every required fact must be present on both
    sides and byte-equal. Any drift is a STOP."""
    problems = [name for name in required if name not in before or name not in now or before[name] != now[name]]
    return (not problems, tuple(problems))


# ---------------------------------------------------------------------------
# Evaluator outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    status: str  # PASS | FAIL
    evidence: dict = field(default_factory=dict)
    reason: str | None = None


def _fail(reason: str, **evidence) -> Outcome:
    return Outcome("FAIL", dict(evidence), reason)


def _ok(**evidence) -> Outcome:
    return Outcome("PASS", dict(evidence), None)


# ---------------------------------------------------------------------------
# A8 lifecycle, environment, resolved model keys (R6)
# ---------------------------------------------------------------------------


def evaluate_model_lifecycle(snapshot: Mapping, *, now: datetime) -> Outcome:
    """Both allowed models Active, not deprecated, no deprecation notice, from
    a documentation readback that carries its own GitHub-server-time stamp."""
    try:
        read_at = datetime.fromisoformat(str(snapshot["read_at_utc"]))
        _require_server_utc(read_at, "read_at_utc")
        models = snapshot["models"]
        source = str(snapshot["source_url"])
        if not isinstance(models, list) or not source.startswith("https://"):
            raise ValueError("shape")
    except (KeyError, ValueError, TypeError):
        return _fail("lifecycle snapshot is malformed")
    if read_at > now:
        return _fail("lifecycle snapshot is stamped in the future")
    seen: dict[str, dict] = {}
    for entry in models:
        if not isinstance(entry, dict) or not isinstance(entry.get("model"), str):
            return _fail("lifecycle snapshot entry is malformed")
        if entry["model"] in seen:
            return _fail("lifecycle snapshot repeats a model")
        seen[entry["model"]] = entry
    if set(seen) != set(ALLOWED_MODEL_KEYS):
        return _fail("lifecycle snapshot must cover exactly the allowed model keys", models=sorted(seen))
    for name, entry in sorted(seen.items()):
        if entry.get("state") != "Active":
            return _fail(f"{name} is not Active", model=name, state=str(entry.get("state")))
        if entry.get("deprecation_notice") is not False or entry.get("deprecated") != "N/A":
            return _fail(f"{name} has a deprecation notice or date", model=name)
    return _ok(
        read_at_utc=read_at.isoformat(), source_url=source,
        models={n: {"state": e["state"], "not_sooner_than": str(e.get("tentative_retirement_not_sooner_than"))}
                for n, e in sorted(seen.items())},
    )


def evaluate_env_names(names: Iterable[str]) -> Outcome:
    """Names only, never values. A model-free lane must carry no provider
    variable at all; any of the four override names, the static-credential
    names or any ANTHROPIC_ name fails, even when set to the empty string."""
    present = sorted(set(names))
    bad = [n for n in present if n in PROHIBITED_OVERRIDE_ENV or n in STATIC_CREDENTIAL_ENV
           or n.startswith("ANTHROPIC_")]
    if bad:
        return _fail("provider or model-override variable present in the model-free lane", present=bad)
    return _ok(checked=list(PROHIBITED_OVERRIDE_ENV) + list(STATIC_CREDENTIAL_ENV), present=[])


def scan_text_for_override_names(text: str) -> list[str]:
    """Workflow or script text that names one of the four override variables."""
    return [name for name in PROHIBITED_OVERRIDE_ENV if re.search(rf"\b{re.escape(name)}\b", text)]


def check_resolved_model_keys(entries: Sequence[object], *, invocation_count: int) -> Outcome:
    """R6 contract. ``entries`` holds one item per invocation: a list of the
    SDK's resolved model keys, or the marker UNAVAILABLE. PASS only if every
    invocation's set is non-empty, contains ``claude-sonnet-5``, is a subset
    of the allowed keys, and the capture covers every invocation."""
    if isinstance(invocation_count, bool) or not isinstance(invocation_count, int) or invocation_count < 1:
        return _fail("invocation count must be a positive integer")
    if len(entries) != invocation_count:
        return _fail("capture does not cover every invocation", captured=len(entries), invocations=invocation_count)
    for index, entry in enumerate(entries, start=1):
        if entry == RESOLVED_MODEL_UNAVAILABLE or not isinstance(entry, (list, tuple)) or not entry:
            return _fail("resolved model keys are unavailable for an invocation", invocation=index)
        if not all(isinstance(k, str) for k in entry):
            return _fail("resolved model key is not a string", invocation=index)
        keys = set(entry)
        if REQUIRED_PRIMARY_MODEL not in keys:
            return _fail("required primary model is absent from an invocation", invocation=index)
        extra = sorted(keys - ALLOWED_MODEL_KEYS)
        if extra:
            return _fail("an unauthorized model key was resolved", invocation=index, keys=extra)
    return _ok(invocations=invocation_count)


# ---------------------------------------------------------------------------
# Runtime and dependency drift (D3, R7)
# ---------------------------------------------------------------------------


def normalize_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def parse_direct_pins(requirements_text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in requirements_text.splitlines():
        text = line.split("#", 1)[0].strip()
        if not text or text.startswith("-"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9._+!-]*)", text)
        if match is None:
            raise ReadinessError("requirements line is not an exact pin")
        pins[normalize_distribution(match.group(1))] = match.group(2)
    return pins


@dataclass(frozen=True)
class TransitiveDiff:
    package: str
    baseline_version: str | None
    observed_version: str | None

    def as_evidence(self) -> dict:
        return {"package": self.package, "baseline_version": self.baseline_version,
                "observed_version": self.observed_version}


@dataclass(frozen=True)
class DriftClassification:
    fatal: tuple[str, ...]
    residuals: tuple[str, ...]
    transitive: tuple[TransitiveDiff, ...]


def _identity_fields(doc: Mapping) -> dict | None:
    try:
        sdk = doc["sdk"]
        dists = {normalize_distribution(n): v for n, v in doc["distributions"]}
        return {
            "python": str(doc["python_version"]),
            "image": (doc.get("runner_image") or {}).get("image_version"),
            "sdk_version": str(sdk["version"]),
            "record": str(sdk["record_sha256"]),
            "transport": str(sdk["transport_module"]["sha256_actual"]),
            "cli_sha": str(sdk["bundled_cli"]["sha256_actual"]),
            "cli_version": sdk["bundled_cli"].get("cli_version_declared"),
            "dists": dists,
        }
    except (KeyError, TypeError, ValueError):
        return None


def classify_runtime_drift(
    baseline: Mapping, current: Mapping | None, *, direct_pins: Mapping[str, str]
) -> DriftClassification:
    """D3 and R7, frozen. Fatal: SDK, transport, bundled CLI, any direct pin,
    Python minor, capture failure. Accepted residuals (recorded): runner image
    string and Python patch. EVERY other distribution version change, addition
    or removal is transitive and requires owner adjudication; no carve-out,
    because absence of an influence proof is not proof of absence."""
    base = _identity_fields(baseline)
    if base is None:
        raise ReadinessError("baseline runtime identity is malformed")
    cur = _identity_fields(current) if current is not None else None
    if cur is None:
        return DriftClassification(("IDENTITY_CAPTURE_FAILED",), (), ())
    fatal: list[str] = []
    residuals: list[str] = []
    if cur["sdk_version"] != SDK_VERSION:
        fatal.append("SDK_VERSION")
    for key, label in (("record", "SDK_RECORD_DIGEST"), ("transport", "TRANSPORT_DIGEST"),
                       ("cli_sha", "CLI_DIGEST"), ("cli_version", "CLI_VERSION")):
        if cur[key] != base[key]:
            fatal.append(label)
    py_cur = str(cur["python"]).split(".")
    py_base = str(base["python"]).split(".")
    if py_cur[:2] != ["3", "12"] or py_cur[:2] != py_base[:2]:
        fatal.append("PYTHON_MINOR")
    elif py_cur != py_base:
        residuals.append(f"PYTHON_PATCH {base['python']} -> {cur['python']}")
    if cur["image"] != base["image"]:
        residuals.append(f"RUNNER_IMAGE {base['image']} -> {cur['image']}")
    for name, pinned in sorted(direct_pins.items()):
        if cur["dists"].get(name) != pinned or base["dists"].get(name) != pinned:
            fatal.append(f"DIRECT_PIN {name}")
    transitive = []
    for name in sorted(set(base["dists"]) | set(cur["dists"])):
        if name in direct_pins or name == "claude-agent-sdk":
            continue
        before, after = base["dists"].get(name), cur["dists"].get(name)
        if before != after:
            transitive.append(TransitiveDiff(name, before, after))
    return DriftClassification(tuple(fatal), tuple(residuals), tuple(transitive))


def evaluate_runtime_drift(
    classification: DriftClassification, adjudications: Sequence[Adjudication]
) -> Outcome:
    evidence = {
        "fatal": list(classification.fatal), "residuals": list(classification.residuals),
        "transitive_differences": [d.as_evidence() for d in classification.transitive],
    }
    if classification.fatal:
        return Outcome("FAIL", evidence, "fatal runtime drift: " + ", ".join(classification.fatal))
    problems = _adjudication_reasons(evidence["transitive_differences"], adjudications)
    if problems:
        return Outcome("FAIL", evidence, "; ".join(problems))
    return Outcome("PASS", evidence, None)


# ---------------------------------------------------------------------------
# Carry-forward (D4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CarrySpec:
    name: str
    source_sha: str
    artifacts: tuple[tuple[str, str, str], ...]
    path_set: tuple[str, ...]
    workflow_diff_allowed: bool


CARRY_SPECS: Mapping[str, CarrySpec] = {
    "wif": CarrySpec("wif", B5_SOURCE_SHA, B5_ARTIFACT_DIGESTS, WIF_MECHANISM_PATHS, False),
    "b2": CarrySpec("b2", B2_SOURCE_SHA, B2_ARTIFACT_DIGESTS, FINALIZATION_PATHS, True),
    "b2files": CarrySpec("b2files", B2_SOURCE_SHA, B2_ARTIFACT_DIGESTS, FINALIZATION_PATHS, False),
    "b5": CarrySpec("b5", B5_SOURCE_SHA, B5_ARTIFACT_DIGESTS, INVOCATION_PATHS, False),
    "pc": CarrySpec("pc", B5_SOURCE_SHA, B5_ARTIFACT_DIGESTS, PROCESS_CONTROL_PATHS, False),
}


def evaluate_carry_forward(spec: CarrySpec, facts: Mapping) -> Outcome:
    """All applicable mechanical checks must pass for the carried row (D4).
    ``facts``: ``artifacts`` {id: {digest, expired}}, ``path_diff`` (names that
    differ from the evidence source over ``spec.path_set``) and
    ``workflow_diff_lines`` (the +/- lines of the official workflow diff)."""
    problems: list[str] = []
    observed = facts.get("artifacts")
    if not isinstance(observed, Mapping):
        return _fail("carry-forward facts are malformed")
    digest_evidence = {}
    for name, artifact_id, expected in spec.artifacts:
        item = observed.get(artifact_id)
        if not isinstance(item, Mapping):
            problems.append(f"artifact {artifact_id} was not checked")
            continue
        if item.get("expired") is True:
            # Expired: the STATE digest is the durable record; REST cannot re-verify it.
            digest_evidence[artifact_id] = "EXPIRED_DURABLE_DIGEST_ONLY"
        elif item.get("digest") != expected:
            problems.append(f"artifact {artifact_id} digest does not equal the recorded digest")
        else:
            digest_evidence[artifact_id] = "VERIFIED"
    path_diff = facts.get("path_diff")
    if not isinstance(path_diff, list):
        problems.append("path diff was not enumerated")
    elif path_diff:
        problems.append("frozen path set changed since the evidence source: " + ", ".join(sorted(path_diff)))
    lines = facts.get("workflow_diff_lines")
    if spec.workflow_diff_allowed:
        if not isinstance(lines, list):
            problems.append("workflow diff was not enumerated")
        elif sorted(lines) != sorted(ALLOWED_WORKFLOW_DIFF_LINES):
            problems.append("official workflow diff is not exactly the allowed timeout change")
    evidence = {"source_sha": spec.source_sha, "artifacts": digest_evidence,
                "path_set": list(spec.path_set), "path_diff": list(path_diff) if isinstance(path_diff, list) else None,
                "workflow_diff_lines": list(lines) if isinstance(lines, list) else None}
    if problems:
        return Outcome("FAIL", evidence, "; ".join(problems))
    return Outcome("PASS", evidence, None)


# ---------------------------------------------------------------------------
# Quality surface, envelope, ledger, latch
# ---------------------------------------------------------------------------


def evaluate_quality_surface(facts: Mapping) -> Outcome:
    """Row 15. ``changed`` maps path -> git status over ``QUALITY_PATHS`` since the
    original official run source; ``diff_sha256`` maps path -> sha256 of that
    path's ``git diff`` text."""
    changed, hashes = facts.get("changed"), facts.get("diff_sha256")
    if not isinstance(changed, Mapping) or not isinstance(hashes, Mapping):
        return _fail("quality-surface facts are malformed")
    problems = []
    for path, status in sorted(changed.items()):
        allowed = QUALITY_ALLOWED_DIFFS.get(path)
        if allowed is None:
            problems.append(f"unallowed quality-path change: {path}")
        elif allowed[0] != status:
            problems.append(f"{path} has status {status}, allowed {allowed[0]}")
        elif allowed[1] is not None and hashes.get(path) != allowed[1]:
            problems.append(f"{path} diff differs from the reviewed hunk")
    for key, text in (("frozen_manifest_identical", "phase-1 frozen manifest is not identical"),
                      ("runner_never_overrides_attempts", "official runner overrides the attempt count"),
                      ("model_literal_pinned", "model, threshold or budget literals are not pinned")):
        if facts.get(key) is not True:
            problems.append(text)
    evidence = {"changed": dict(sorted(changed.items())), "allowlist": sorted(QUALITY_ALLOWED_DIFFS)}
    return Outcome("FAIL", evidence, "; ".join(problems)) if problems else Outcome("PASS", evidence, None)


def evaluate_envelope(envelope: Mapping, *, workflow_timeout_minutes: int | None) -> Outcome:
    """Rows 8, 9 and sub-check 16d: the committed envelope is the frozen value
    and the official workflow timeout is bound to it."""
    try:
        max_observed = int(envelope["max_observed_ms"])
        outer = int(envelope["outer_seconds"])
        timeout = int(envelope["workflow_timeout_minutes"])
        session = int(envelope["session_duration_s"])
        stall = int(envelope["stall_budget_ms"])
        env_id, version = str(envelope["envelope_id"]), str(envelope["envelope_version"])
    except (KeyError, TypeError, ValueError):
        return _fail("envelope facts are malformed")
    problems = []
    if env_id != EXPECTED_ENVELOPE_ID:
        problems.append("envelope_id is not the committed value")
    if version != EXPECTED_ENVELOPE_VERSION:
        problems.append("envelope_version is not 1")
    if stall != max(EXPECTED_STALL_BUDGET_MS, 10 * max_observed):
        problems.append("stall budget is not max(600 s, 10 x max_observed)")
    if outer != math.ceil(138 * max_observed / 1000) + 1080:
        problems.append("outer seconds do not follow the frozen formula")
    if session != outer - 480:
        problems.append("session duration is not outer minus the 8-minute reserve")
    if timeout != math.ceil(outer / 60) or timeout != EXPECTED_WORKFLOW_TIMEOUT_MINUTES:
        problems.append("workflow timeout is not ceil(outer / 60) = 106")
    if workflow_timeout_minutes != timeout:
        problems.append("official workflow timeout does not equal the envelope's")
    evidence = {"envelope_id": env_id, "max_observed_ms": max_observed, "outer_seconds": outer,
                "session_duration_s": session, "stall_budget_ms": stall, "workflow_timeout_minutes": timeout}
    return Outcome("FAIL", evidence, "; ".join(problems)) if problems else Outcome("PASS", evidence, None)


def evaluate_latch_row(facts: Mapping) -> Outcome:
    """Row 25 (Amendment B): the latch is present, valid and UNARMED, both
    consult points are wired, and no replacement activity exists."""
    required = (
        "kinds", "file_sha256", "head_sha256", "unarmed_refusal_reason", "eligibility_requires_latch",
        "preflight_consults_latch_in_order", "enforcement_tests_green", "history_permits_exactly_one",
        "official_run_numbers", "replacement_prefix_artifacts", "gate_evidence_prefix_artifacts",
        "replacement_receipts", "purpose_is_original", "envelope_is_none",
    )
    missing = [key for key in required if key not in facts]
    if missing:
        return _fail("latch facts are incomplete: " + ", ".join(missing))
    problems = []
    if facts["kinds"] != ["GENESIS"]:
        problems.append("latch is not exactly one GENESIS record (ATTEMPT_AUTHORIZED present or malformed)")
    if facts["file_sha256"] != EXPECTED_LATCH_FILE_SHA256 or facts["head_sha256"] != EXPECTED_LATCH_HEAD_SHA256:
        problems.append("latch bytes or chain head differ from the committed GENESIS")
    if facts["unarmed_refusal_reason"] != "LATCH_UNARMED":
        problems.append("an UNARMED latch does not refuse admission")
    for key, text in (("eligibility_requires_latch", "replacement eligibility does not require the latch"),
                      ("preflight_consults_latch_in_order", "preflight does not consult the latch in the required order"),
                      ("enforcement_tests_green", "latch enforcement tests are not green"),
                      ("history_permits_exactly_one", "durable history is not consistent with exactly one future replacement"),
                      ("purpose_is_original", "PURPOSE is not the original purpose"),
                      ("envelope_is_none", "ENVELOPE is not None")):
        if facts[key] is not True:
            problems.append(text)
    if facts["official_run_numbers"] != [1, 2, 3, 4]:
        problems.append("official-gate run history is not exactly runs 1 to 4")
    for key, text in (("replacement_prefix_artifacts", "a replacement marker artifact exists"),
                      ("gate_evidence_prefix_artifacts", "a gate-evidence artifact exists"),
                      ("replacement_receipts", "a replacement receipt exists")):
        if facts[key] != 0:
            problems.append(text)
    evidence = {k: facts[k] for k in required}
    return Outcome("FAIL", evidence, "; ".join(problems)) if problems else Outcome("PASS", evidence, None)


# ---------------------------------------------------------------------------
# Deferred provider components: frozen predicates (D7), evaluated later
# ---------------------------------------------------------------------------

FROZEN_RULE_EXPECTATION: Mapping[str, object] = {
    "status": "Active",
    "issuer": "github-actions",
    "check_jti": True,
    "service_account": "sentinel-github",
    "workspaces": ["sentinel"],
    "applies_to_all_workspaces": False,
    "scope": "workspace:developer",
    "token_lifetime_seconds": 600,
    "audience": "https://api.anthropic.com",
    "subject_prefix": "repo:kobescak-kristian/ai-portfolio-sentinel:ref:refs/heads/main",
    "claims": {
        "repository_owner": "kobescak-kristian",
        "event_name": "workflow_dispatch",
        "ref": "refs/heads/main",
        "workflow_ref": "kobescak-kristian/ai-portfolio-sentinel/" + OFFICIAL_WORKFLOW + "@refs/heads/main",
    },
    "cel_condition": None,
}


def evaluate_replacement_rule(readback: Mapping) -> Outcome:
    """Components 4.2 and 16h. Evaluated only at provider preparation, fresh
    readiness and the final GO; a B6-3 record may not mark it PASS."""
    rule = readback.get("rule")
    if not isinstance(rule, Mapping):
        return _fail("rule readback is missing")
    problems = [f"rule field {key} differs" for key, want in FROZEN_RULE_EXPECTATION.items() if rule.get(key) != want]
    others = readback.get("other_rules")
    if not isinstance(others, list):
        problems.append("rules list is missing")
    else:
        official_ref = FROZEN_RULE_EXPECTATION["claims"]["workflow_ref"]  # type: ignore[index]
        for other in others:
            if not isinstance(other, Mapping):
                problems.append("rules list entry is malformed")
                continue
            claims = other.get("claims") or {}
            if other.get("status") == "Active" and claims.get("workflow_ref") in (None, official_ref):
                problems.append(f"another Active rule can authorize the official workflow: {other.get('id')}")
    if readback.get("original_rules_archived") != {"P5C": "Archived", "P5D_ORIGINAL": "Archived", "TIMING": "Archived"}:
        problems.append("the original and timing rules are not all Archived")
    if readback.get("auth_events_for_rule") != 0:
        problems.append("the replacement rule already has authentication events")
    if readback.get("variable_value") != rule.get("id") or not rule.get("id"):
        problems.append("the rule variable does not equal the replacement rule id")
    if readback.get("oidc_customization") != {"use_default": True, "use_immutable_subject": False}:
        problems.append("OIDC subject customization changed")
    return Outcome("FAIL", {"rule_id": str(rule.get("id"))}, "; ".join(problems)) if problems else Outcome(
        "PASS", {"rule_id": str(rule.get("id"))}, None)


def _dec(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def evaluate_cap(readback: Mapping, *, now: datetime) -> Outcome:
    """Component 16i. cap must equal the smallest Console-supported value
    >= S + E + H after conversion (owner ruling Part D), and its conservative
    EUR equivalent must stay within the EUR 50 program ceiling. E is the
    replacement gate total, EUR 5.00. The FX retrieval must itself be within
    the two-hour T2 age."""
    try:
        s = _dec(readback["month_to_date_spend_usd"])
        h_eur = _dec(readback["headroom_eur"])
        granularity = _dec(readback["granularity_usd"])
        cap = _dec(readback["cap_usd"])
        fx = _dec(readback["fx_usd_per_eur"])
        fx_at = datetime.fromisoformat(str(readback["fx_retrieved_at_utc"]))
        _require_server_utc(fx_at, "fx_retrieved_at_utc")
        currency, period = readback["currency"], readback["period"]
    except (KeyError, ValueError, TypeError):
        return _fail("cap readback is malformed")
    if None in (s, h_eur, granularity, cap, fx) or granularity <= 0 or fx <= 0 or s < 0 or h_eur < 0:  # type: ignore[operator]
        return _fail("cap readback has invalid numbers")
    if currency != "USD" or period != "calendar-month-utc":
        return _fail("unexpected cap currency or period")
    if fx_at > now or now - fx_at > T2_MAX_AGE:
        return _fail("FX rate is stale or future-dated")
    required = s + (Decimal(5) + h_eur) * fx  # type: ignore[operator]
    smallest = (required / granularity).to_integral_value(rounding=ROUND_CEILING) * granularity  # type: ignore[operator]
    problems = []
    if cap != smallest:
        problems.append("cap is not the smallest supported value >= S + E + H" if cap > smallest else  # type: ignore[operator]
                        "cap is below S + E + H")
    if cap / fx > Decimal(50):  # type: ignore[operator]
        problems.append("cap exceeds the EUR 50 program ceiling")
    evidence = {"required_usd": str(required), "smallest_supported_usd": str(smallest), "cap_usd": str(cap)}
    return Outcome("FAIL", evidence, "; ".join(problems)) if problems else Outcome("PASS", evidence, None)


# ---------------------------------------------------------------------------
# Real-runner probe evidence (rows 1, 3, 5, 9, 24 and the runtime identity)
# ---------------------------------------------------------------------------

PROBE_REQUIRED_CHECKS = (
    "server_time", "current_run", "official_listing", "pagination", "attempt_jobs", "job_steps", "anchor",
    "commit", "push_activity", "artifact_discovery", "original_marker", "layout", "import_closure",
    "env_scan", "runtime_identity",
)
ROW24_CHECKS = (
    "server_time", "current_run", "official_listing", "pagination", "attempt_jobs", "job_steps", "anchor",
    "commit", "push_activity", "artifact_discovery",
)


class ProbeCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    ok: bool
    detail: dict

    @model_validator(mode="after")
    def _validate(self) -> "ProbeCheck":
        if self.name not in PROBE_REQUIRED_CHECKS:
            raise ValueError("unknown probe check")
        _json_safe(self.detail)
        return self


class ProbeEvidence(BaseModel):
    """The single canonical file the read probe publishes. No bodies, no
    headers, no token, no path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    lane: Literal["latch-read-probe"]
    run_id: str
    run_attempt: StrictInt
    event: str
    ref: str
    sha: str
    workflow_identity: str
    result: Literal["PASS", "STOP"]
    stop_reason: str | None
    server_time_first_utc: str | None
    server_time_last_utc: str | None
    checks: tuple[ProbeCheck, ...]
    runtime_identity: dict | None

    @model_validator(mode="after")
    def _validate(self) -> "ProbeEvidence":
        if not _HEX40.fullmatch(self.sha) or not self.run_id.isdigit():
            raise ValueError("probe identity is malformed")
        names = [c.name for c in self.checks]
        if len(names) != len(set(names)):
            raise ValueError("a probe check is repeated")
        if self.result == "PASS" and (self.stop_reason is not None or tuple(sorted(names)) != tuple(sorted(PROBE_REQUIRED_CHECKS))):
            raise ValueError("a PASS probe must carry every required check and no stop reason")
        if self.result == "STOP" and not (self.stop_reason or "").strip():
            raise ValueError("a STOP probe must state its reason")
        if self.runtime_identity is not None:
            _json_safe(self.runtime_identity)
        return self


def probe_check_map(probe: ProbeEvidence) -> dict[str, ProbeCheck]:
    return {c.name: c for c in probe.checks}


def evaluate_probe_for_row24(probe: ProbeEvidence, *, expected_sha: str) -> Outcome:
    """Row 24 (Amendment B). Every required GitHub surface read and parsed with
    the real runner token, real multi-page pagination proven, the job-start
    anchor resolved, and the probe ran at exactly the expected SHA."""
    if probe.result != "PASS":
        return _fail("the probe did not complete: " + str(probe.stop_reason))
    if probe.sha != expected_sha or probe.run_attempt != 1 or probe.event != "workflow_dispatch" or probe.ref != "refs/heads/main":
        return _fail("probe identity does not match the expected dispatch")
    if probe.workflow_identity != PROBE_WORKFLOW:
        return _fail("probe ran under an unexpected workflow")
    checks = probe_check_map(probe)
    for name in ROW24_CHECKS:
        if name not in checks or not checks[name].ok:
            return _fail(f"probe check {name} did not pass")
    pag = checks["pagination"].detail
    if not (isinstance(pag.get("pages"), int) and pag["pages"] >= 2 and pag.get("entries") == pag.get("total_count")):
        return _fail("real multi-page pagination with total_count equality was not proven")
    listing = checks["official_listing"].detail
    if listing.get("run_numbers") != [1, 2, 3, 4]:
        return _fail("official-gate listing is not exactly run numbers 1 to 4")
    if checks["server_time"].detail.get("monotonic") is not True:
        return _fail("GitHub server time was not monotonic")
    if checks["anchor"].detail.get("resolved") is not True:
        return _fail("job-start anchor did not resolve")
    return _ok(checks=sorted(ROW24_CHECKS), pages=pag["pages"], run_id=probe.run_id)


def evaluate_probe_check(probe: ProbeEvidence, name: str, *, expected_sha: str) -> Outcome:
    """A single probe check as evidence for another row (1, 3, 5, 9)."""
    if probe.result != "PASS" or probe.sha != expected_sha:
        return _fail("the probe did not complete at the expected SHA")
    check = probe_check_map(probe).get(name)
    if check is None or not check.ok:
        return _fail(f"probe check {name} did not pass")
    evidence = {k: v for k, v in check.detail.items() if isinstance(v, (int, bool, str))}
    evidence.update({"check": name, "run_id": probe.run_id})
    return _ok(**evidence)


# ---------------------------------------------------------------------------
# Write-set proofs (R10)
# ---------------------------------------------------------------------------

B63B_PATHS = ("artifacts/phase5_readiness_b63.json", "STATE.md", ".publicgate-allow")


def assert_precommit_write_set(
    dirty: Iterable[str], untracked: Iterable[str], staged: Iterable[str], allowed: Sequence[str] = B63B_PATHS
) -> None:
    """Before the B6-3b commit: the union of dirty, untracked and staged paths
    must equal exactly the allowed paths."""
    union = set(dirty) | set(untracked) | set(staged)
    if union != set(allowed):
        extra, missing = sorted(union - set(allowed)), sorted(set(allowed) - union)
        raise ReadinessError(f"pre-commit write set differs: extra={extra} missing={missing}")


def assert_postcommit_write_set(
    diff_names: Iterable[str], commit_count: int, parent_sha: str, base_sha: str,
    allowed: Sequence[str] = B63B_PATHS,
) -> None:
    """After the B6-3b commit and before the push: the commit holds exactly
    the allowed paths, there is exactly one commit, and its parent is B6-3a."""
    names = list(diff_names)
    if set(names) != set(allowed) or len(names) != len(set(names)):
        raise ReadinessError("B6-3b commit does not hold exactly the allowed paths")
    if commit_count != 1:
        raise ReadinessError("there must be exactly one B6-3b commit")
    if parent_sha != base_sha:
        raise ReadinessError("the B6-3b commit's parent is not the B6-3a SHA")

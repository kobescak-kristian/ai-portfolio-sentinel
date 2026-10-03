"""P5-D FINAL_T2 readiness record at the readiness source commit R (ADR-0012
Amendment C; Stage 2C-B6-6a, owner-approved readiness-at-R plan revision 4,
owner rulings C1 to C10 and R13 to R22 of 2026-10-03).

What this module is. The frozen schema of the composite FINAL_T2 record, the
armed-state readiness evaluators the B6-4 record left to this stage, the
GitHub-attested freshness window, and the mechanical ``owner_go_ref`` binding
between the frozen record and commit A. It is PURE: it reads no clock, file,
git repository, environment or network. Every fact is supplied by the caller
(``scripts/run_phase5_final_readiness.py``). It authorizes nothing: no marker,
no latch record, no provider access.

Frozen semantics (Amendment C).

- Two forms. ``FinalReadinessDraft`` is built by ``collect-final`` and is never
  authoritative. ``FinalReadinessRecord`` is the draft plus ``recorded_at_utc``,
  constructed exactly once inside ``authorize``; its canonical bytes are the
  only bytes ever digested and bound into commit A.
- Window. ``T_floor`` is the probe-at-R ``server_time_first_utc``. Every
  FINAL_T2 evidence collection stamp lies in ``[T_floor, recorded_at_utc]``.
  Governance / ordering / anchor stamps (``T_R``, ``T_CI``, the probe run's
  ``created_at`` and ``run_started_at``, ``conditional_go.issued_at_utc``,
  adjudication rulings) obey only their own ordering and are never rejected
  merely because they precede ``T_floor`` (R20).
- Pre-push age ``recorded_at_utc - T_floor <= 100 min`` (2 h minus a 20-minute
  push margin); post-push ``T_A - recorded_at_utc <= 20 min`` and
  ``T_A - T_floor <= 2 h``.
- ``owner_go_ref`` is exactly ``q77-p5d-final-go-a/<sha256 of the frozen
  bytes>``; the regex is authoritative (R19).
- No DEFERRED, no PREARM_BASELINE; every component of a row whose tiers include
  T2 carries FINAL_T2.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, StrictInt, model_validator

from . import readiness as rd
from .models import canonical_json_bytes
from .readiness import ProbeEvidence, _json_safe, _require_server_utc

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
STAGE = "B6-6b"
MAX_ATTEMPTS = 2
PUSH_MARGIN = timedelta(minutes=20)
PRE_PUSH_MAX_AGE = rd.T2_MAX_AGE - PUSH_MARGIN  # 100 minutes
AUTHORIZATION_WINDOW = timedelta(hours=24)  # equal to latch.MAX_AUTHORIZATION_WINDOW
# Console authentication history covers 7 days; the replacement rule was created
# no earlier than this instant minus 7 days (B6-5 record), so the zero-events
# proof holds only for a Console readback that closes before it.
AUTH_HISTORY_DEADLINE = datetime(2026, 10, 10, 13, 54, 0, tzinfo=timezone.utc)

OWNER_GO_REF_PREFIX = "q77-p5d-final-go-a/"
OWNER_GO_REF_PATTERN = re.compile(r"q77-p5d-final-go-a/[0-9a-f]{64}")
CONDITIONAL_GO_REF_PATTERN = re.compile(r"q77-p5d-cgo-[a-z0-9-]{1,48}")
CONDITIONAL_GO_TERMS = (
    "authorize commit A if and only if every frozen B6-6b predicate, FINAL_T2 predicate, "
    "digest/binding predicate and STOP condition passes"
)
CONDITIONAL_GO_TERMS_SHA256 = hashlib.sha256(CONDITIONAL_GO_TERMS.encode("utf-8")).hexdigest()
OWNER_CONSOLE_ATTESTATION = (
    "read-only Console readback by the owner between opened_at_utc and closed_at_utc; no mutation"
)
FROZEN_HEADROOM_EUR = "2.50"  # owner ruling D9-H

ARMED_PURPOSE = "P5D_REPLACEMENT_SONNET_GATE"
REPLACEMENT_MARKER_NAME_123 = "sentinel-p5-oneshot-p5d-replacement-sonnet-gate-r123"
REPLACEMENT_RULE_VARIABLE = "SENTINEL_P5D_REPLACEMENT_FEDERATION_RULE_ID"
OFFICIAL_CONCURRENCY_GROUP = "sentinel-oneshot-p5d"
# The official-workflow diff since the B2 source that the armed carry-forward
# allows: the B6-1 timeout binding plus exactly the six B6-4 arming lines.
ARMED_WORKFLOW_DIFF_LINES = (
    "-    timeout-minutes: 30",
    "+    timeout-minutes: 106",
    "-      ANTHROPIC_FEDERATION_RULE_ID: ${{ vars.SENTINEL_P5D_FEDERATION_RULE_ID }}",
    "+      ANTHROPIC_FEDERATION_RULE_ID: ${{ vars.SENTINEL_P5D_REPLACEMENT_FEDERATION_RULE_ID }}",
    '+      DISABLE_AUTOUPDATER: "1"',
    '+      DISABLE_UPDATES: "1"',
    "-          name: sentinel-p5-oneshot-p5d-official-sonnet-gate-r${{ github.run_id }}",
    "+          name: sentinel-p5-oneshot-p5d-replacement-sonnet-gate-r${{ github.run_id }}",
)

T2_ROWS = frozenset(row.row_id for row in rd.ROW_DEFS if "T2" in row.tiers)
COMPONENT_ROW = {c: row.row_id for row in rd.ROW_DEFS for c in row.components}

Provenance = Literal["RUNNER_ARTIFACT", "GITHUB_REST_OWNER_TOKEN", "LOCAL_MACHINE", "OWNER_CONSOLE", "AGENT_WEB"]
PROVENANCE_GROUPS = ("RUNNER_ARTIFACT", "GITHUB_REST_OWNER_TOKEN", "LOCAL_MACHINE", "OWNER_CONSOLE", "AGENT_WEB")

_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DECIMAL = re.compile(r"[0-9]{1,6}(\.[0-9]{1,6})?")


class FinalReadinessError(RuntimeError):
    """A record, input or binding violates the frozen FINAL_T2 contract.
    Never carries a token, a secret or a local path."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ConditionalGo(BaseModel):
    """The owner's pre-issued conditional, no-discretion GO (R17, R21)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conditional_go_ref: str
    issued_at_utc: datetime
    terms_sha256: str

    @model_validator(mode="after")
    def _validate(self) -> "ConditionalGo":
        if not CONDITIONAL_GO_REF_PATTERN.fullmatch(self.conditional_go_ref):
            raise ValueError("conditional_go_ref is malformed")
        _require_server_utc(self.issued_at_utc, "issued_at_utc")
        if self.terms_sha256 != CONDITIONAL_GO_TERMS_SHA256:
            raise ValueError("conditional GO terms differ from the frozen Amendment C terms")
        return self


class CiR(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sha: str
    run_id: str
    head_sha: str
    conclusion: Literal["success"]
    completed_at_utc: datetime

    @model_validator(mode="after")
    def _validate(self) -> "CiR":
        if not _HEX40.fullmatch(self.sha) or self.sha != self.head_sha or not self.run_id.isdigit():
            raise ValueError("CI evidence must be exact-SHA, for a decimal run id")
        _require_server_utc(self.completed_at_utc, "completed_at_utc")
        return self


class ProbeBinding(BaseModel):
    """The probe-at-R run, its GitHub-digested artifact and the embedded
    evidence (kept self-contained after the 90-day artifact expiry)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    run_attempt: Literal[1]
    artifact_id: str
    archive_digest: str
    file_sha256: str
    run_created_at_utc: datetime
    run_started_at_utc: datetime
    runs_at_r: tuple[str, ...]
    evidence: ProbeEvidence

    @model_validator(mode="after")
    def _validate(self) -> "ProbeBinding":
        if not self.run_id.isdigit() or not self.artifact_id.isdigit():
            raise ValueError("probe run and artifact ids must be decimal strings")
        if not _DIGEST.fullmatch(self.archive_digest) or not _HEX64.fullmatch(self.file_sha256):
            raise ValueError("probe digests are malformed")
        _require_server_utc(self.run_created_at_utc, "run_created_at_utc")
        _require_server_utc(self.run_started_at_utc, "run_started_at_utc")
        if self.evidence.run_id != self.run_id or self.evidence.run_attempt != 1 or self.evidence.result != "PASS":
            raise ValueError("embedded probe evidence must be the PASS evidence of this run, attempt 1")
        if not self.runs_at_r or len(set(self.runs_at_r)) != len(self.runs_at_r) or self.runs_at_r[-1] != self.run_id:
            raise ValueError("this probe must be the newest probe run at R")
        if self.evidence.server_time_first_utc is None:
            raise ValueError("probe evidence carries no first server time")
        return self

    @property
    def t_floor(self) -> datetime:
        return datetime.fromisoformat(str(self.evidence.server_time_first_utc))


class OwnerCap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    month_to_date_spend_usd: str
    granularity_usd: str
    cap_usd: str
    credit_usd: str
    auto_reload: Literal["Off"]
    currency: Literal["USD"]
    period: Literal["calendar-month-utc"]

    @model_validator(mode="after")
    def _validate(self) -> "OwnerCap":
        for value in (self.month_to_date_spend_usd, self.granularity_usd, self.cap_usd, self.credit_usd):
            if not _DECIMAL.fullmatch(value):
                raise ValueError("cap figures must be plain decimal strings")
        return self


class OwnerTranscription(BaseModel):
    """Owner-read Console facts in the B6-5 transcription schema. Non-public
    identifiers appear only as SHA-256."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: dict
    other_rules: list
    original_rules_archived: dict
    auth_events_for_rule: StrictInt
    organization_id_sha256: str
    service_account_id_sha256: str
    cap: OwnerCap

    @model_validator(mode="after")
    def _validate(self) -> "OwnerTranscription":
        for value in (self.rule, self.other_rules, self.original_rules_archived):
            _json_safe(value)
        if not _HEX64.fullmatch(self.organization_id_sha256) or not _HEX64.fullmatch(self.service_account_id_sha256):
            raise ValueError("identifier hashes must be 64 lowercase hexadecimal characters")
        return self


class OwnerConsole(BaseModel):
    """OWNER_CONSOLE provenance: owner-attested, bracketed by GitHub server time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transcription: OwnerTranscription
    transcription_sha256: str
    opened_at_utc: datetime
    closed_at_utc: datetime
    attestation: str

    @model_validator(mode="after")
    def _validate(self) -> "OwnerConsole":
        _require_server_utc(self.opened_at_utc, "opened_at_utc")
        _require_server_utc(self.closed_at_utc, "closed_at_utc")
        if self.closed_at_utc < self.opened_at_utc:
            raise ValueError("owner read bracket closes before it opens")
        if self.transcription_sha256 != sha256_hex(canonical_json_bytes(self.transcription)):
            raise ValueError("transcription digest does not match the transcription")
        if self.attestation != OWNER_CONSOLE_ATTESTATION:
            raise ValueError("owner attestation text differs from the frozen text")
        return self


class FxReading(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    usd_per_eur: str
    reference_date: str
    source_url: str
    retrieved_at_utc: datetime

    @model_validator(mode="after")
    def _validate(self) -> "FxReading":
        if not _DECIMAL.fullmatch(self.usd_per_eur) or not _DATE.fullmatch(self.reference_date):
            raise ValueError("FX reading is malformed")
        if not self.source_url.startswith("https://"):
            raise ValueError("FX source must be https")
        _require_server_utc(self.retrieved_at_utc, "retrieved_at_utc")
        return self


class FinalAdjudication(rd.Adjudication):
    """An owner adjudication made OUTSIDE any live attempt (R17): ruled before
    the attempt's probe was created."""

    ruled_at_utc: datetime

    @model_validator(mode="after")
    def _validate_time(self) -> "FinalAdjudication":
        _require_server_utc(self.ruled_at_utc, "ruled_at_utc")
        return self


class FinalComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component: str
    status: Literal["PASS", "FAIL"]
    evidence_state: Literal["T0", "FINAL_T2"]
    provenance: tuple[Provenance, ...]
    collected_at_utc: datetime
    evidence: dict
    reason: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "FinalComponent":
        if self.component not in COMPONENT_ROW:
            raise ValueError("unknown component")
        _require_server_utc(self.collected_at_utc, "collected_at_utc")
        _json_safe(self.evidence)
        if not self.provenance or tuple(sorted(set(self.provenance))) != self.provenance:
            raise ValueError("provenance must be a non-empty sorted set of groups")
        if self.status == "FAIL" and not (self.reason or "").strip():
            raise ValueError("a FAIL component must state its reason")
        if COMPONENT_ROW[self.component] in T2_ROWS and self.evidence_state != "FINAL_T2":
            raise ValueError("a component of a T2-tier row must carry FINAL_T2 evidence")
        return self


class FinalRowEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    row_id: StrictInt
    status: Literal["PASS", "FAIL"]
    components: tuple[FinalComponent, ...]

    @model_validator(mode="after")
    def _validate(self) -> "FinalRowEntry":
        row = rd.ROW_BY_ID.get(self.row_id)
        if row is None:
            raise ValueError("unknown row")
        if tuple(c.component for c in self.components) != row.components:
            raise ValueError("components must be exactly the row's components, in order")
        if self.status != rd.aggregate(c.status for c in self.components):
            raise ValueError("row status must equal the aggregate of its components")
        return self


# ---------------------------------------------------------------------------
# Draft and record
# ---------------------------------------------------------------------------


class FinalReadinessDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    stage: Literal["B6-6b"]
    attempt: StrictInt
    readiness_source_sha: str
    t_r_utc: datetime
    t_floor_utc: datetime
    ci_r: CiR
    conditional_go: ConditionalGo
    probe: ProbeBinding
    owner_console: OwnerConsole
    fx: FxReading
    lifecycle: dict
    provenance_stamps: dict[Provenance, datetime]
    rows: tuple[FinalRowEntry, ...]
    adjudications: tuple[FinalAdjudication, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> "FinalReadinessDraft":
        if not 1 <= self.attempt <= MAX_ATTEMPTS:
            raise ValueError("attempt must be 1 or 2")
        if not _HEX40.fullmatch(self.readiness_source_sha):
            raise ValueError("readiness_source_sha must be 40 lowercase hexadecimal characters")
        _require_server_utc(self.t_r_utc, "t_r_utc")
        _require_server_utc(self.t_floor_utc, "t_floor_utc")
        if self.ci_r.sha != self.readiness_source_sha or self.probe.evidence.sha != self.readiness_source_sha:
            raise ValueError("CI and probe evidence must both be for R")
        if self.t_floor_utc != self.probe.t_floor:
            raise ValueError("t_floor_utc must equal the probe's first server time")
        if len(self.probe.runs_at_r) != self.attempt:
            raise ValueError("attempt n must be bound to exactly n probe runs at R (one probe per attempt)")
        _json_safe(self.lifecycle)
        if set(self.provenance_stamps) != set(PROVENANCE_GROUPS):
            raise ValueError("every provenance group needs exactly one stamp")
        for stamp in self.provenance_stamps.values():
            _require_server_utc(stamp, "provenance stamp")
        if tuple(r.row_id for r in self.rows) != rd.ROW_IDS:
            raise ValueError("rows must be exactly rows 1 to 25, once each, in order")
        for entry in self.rows:
            for comp in entry.components:
                if comp.collected_at_utc != min(self.provenance_stamps[g] for g in comp.provenance):
                    raise ValueError("a component stamp must equal the earliest stamp of its provenance groups")
        packages = [a.package for a in self.adjudications]
        if len(packages) != len(set(packages)):
            raise ValueError("duplicate adjudication")
        return self


class FinalReadinessRecord(FinalReadinessDraft):
    recorded_at_utc: datetime

    @model_validator(mode="after")
    def _validate_recorded(self) -> "FinalReadinessRecord":
        _require_server_utc(self.recorded_at_utc, "recorded_at_utc")
        return self


def freeze(draft: FinalReadinessDraft, recorded_at_utc: datetime) -> FinalReadinessRecord:
    """The single construction of the frozen record (R13 step 4)."""
    return FinalReadinessRecord(**dict(draft), recorded_at_utc=recorded_at_utc)


def record_bytes(record: FinalReadinessRecord) -> bytes:
    return canonical_json_bytes(record) + b"\n"


def draft_bytes(draft: FinalReadinessDraft) -> bytes:
    return canonical_json_bytes(draft) + b"\n"


def component_map(doc: FinalReadinessDraft) -> dict[str, FinalComponent]:
    return {c.component: c for r in doc.rows for c in r.components}


# ---------------------------------------------------------------------------
# Window, anchors and eligibility (R20)
# ---------------------------------------------------------------------------


def _lifecycle_read_at(doc: FinalReadinessDraft) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(doc.lifecycle["read_at_utc"]))
        return _require_server_utc(value, "read_at_utc")
    except (KeyError, TypeError, ValueError):
        return None


def evidence_stamps(doc: FinalReadinessDraft) -> dict[str, datetime | None]:
    """Every FINAL_T2 evidence collection stamp, by label."""
    stamps: dict[str, datetime | None] = {f"component:{n}": c.collected_at_utc for n, c in component_map(doc).items()}
    stamps.update({f"provenance:{g}": t for g, t in doc.provenance_stamps.items()})
    stamps["owner_console.opened_at_utc"] = doc.owner_console.opened_at_utc
    stamps["owner_console.closed_at_utc"] = doc.owner_console.closed_at_utc
    stamps["fx.retrieved_at_utc"] = doc.fx.retrieved_at_utc
    stamps["lifecycle.read_at_utc"] = _lifecycle_read_at(doc)
    return stamps


def anchor_problems(doc: FinalReadinessDraft) -> list[str]:
    """Governance / ordering / anchor stamps: their own ordering only."""
    problems = []
    t_floor = doc.t_floor_utc
    if not doc.t_r_utc <= doc.ci_r.completed_at_utc <= t_floor:
        problems.append("anchor order T_R <= T_CI <= T_floor does not hold")
    if not doc.conditional_go.issued_at_utc < doc.probe.run_created_at_utc <= t_floor:
        problems.append("the conditional GO was not issued before the probe was created, or the probe postdates T_floor")
    if not doc.probe.run_started_at_utc <= t_floor:
        problems.append("the probe run started after T_floor")
    for adj in doc.adjudications:
        if not adj.ruled_at_utc < doc.probe.run_created_at_utc:
            problems.append(f"adjudication {adj.package} was ruled inside the live attempt")
    return problems


def window_problems(doc: FinalReadinessDraft, upper_utc: datetime) -> list[str]:
    """FINAL_T2 evidence stamps in ``[T_floor, upper]`` and the pre-push age."""
    problems = []
    t_floor = doc.t_floor_utc
    for label, stamp in sorted(evidence_stamps(doc).items()):
        if stamp is None:
            problems.append(f"{label} is missing or malformed")
        elif not t_floor <= stamp <= upper_utc:
            problems.append(f"{label} lies outside the FINAL_T2 window")
    if upper_utc - t_floor > PRE_PUSH_MAX_AGE:
        problems.append("the FINAL_T2 window exceeds the 100-minute pre-push limit")
    if not doc.owner_console.closed_at_utc < AUTH_HISTORY_DEADLINE:
        problems.append("the Console readback closed at or after the 7-day authentication-history deadline")
    # FX freshness needs no separate check: fx.retrieved_at_utc is an evidence
    # stamp inside [T_floor, upper], and upper - T_floor <= 100 minutes.
    return problems


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reasons: tuple[str, ...]


def evaluate_final(doc: FinalReadinessDraft, *, upper_utc: datetime) -> Eligibility:
    """Every row and component PASS, the anchors ordered, every evidence stamp
    inside the window, every 16g difference adjudicated outside the attempt."""
    reasons: list[str] = []
    failed = sorted(n for n, c in component_map(doc).items() if c.status != "PASS")
    if failed:
        reasons.append("non-PASS components: " + ", ".join(failed))
    reasons.extend(anchor_problems(doc))
    reasons.extend(window_problems(doc, upper_utc))
    drift = component_map(doc)["16g"].evidence.get("transitive_differences")
    reasons.extend(rd._adjudication_reasons(drift, doc.adjudications))
    return Eligibility(eligible=not reasons, reasons=tuple(reasons))


def evaluate_final_record(record: FinalReadinessRecord) -> Eligibility:
    return evaluate_final(record, upper_utc=record.recorded_at_utc)


def post_push_problems(record: FinalReadinessRecord, *, t_a: datetime, t_r: datetime) -> list[str]:
    problems = []
    if t_a < record.recorded_at_utc:
        problems.append("T_A precedes recorded_at_utc")
    if t_a - record.recorded_at_utc > PUSH_MARGIN:
        problems.append("commit A was pushed more than 20 minutes after recorded_at_utc")
    if t_r != record.t_r_utc:
        problems.append("T_R differs from the record")
    ok, why = rd.final_t2_satisfied(None, evidence_state="FINAL_T2", collected_at_utc=record.t_floor_utc,
                                    r_commit_time=t_r, commit_a_time=t_a)
    if not ok:
        problems.append(why)
    return problems


# ---------------------------------------------------------------------------
# owner_go_ref binding (R15, R19)
# ---------------------------------------------------------------------------


def owner_go_ref_for(digest: str) -> str:
    if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
        raise FinalReadinessError("digest must be 64 lowercase hexadecimal characters")
    return OWNER_GO_REF_PREFIX + digest


def parse_owner_go_ref(ref: object) -> str:
    if not isinstance(ref, str) or not OWNER_GO_REF_PATTERN.fullmatch(ref):
        raise FinalReadinessError("owner_go_ref does not have the exact final-GO form")
    return ref[len(OWNER_GO_REF_PREFIX):]


def load_frozen_record(frozen: bytes) -> FinalReadinessRecord:
    """Strict, canonical load of frozen record bytes."""
    try:
        record = FinalReadinessRecord.model_validate_json(frozen)
    except Exception as exc:  # pydantic ValidationError; identifier-only message
        raise FinalReadinessError("frozen record bytes fail strict validation") from exc
    if record_bytes(record) != frozen:
        raise FinalReadinessError("frozen record bytes are not canonical")
    return record


def assert_owner_go_ref_binds(ref: object, frozen: bytes, *, readiness_source_sha: str) -> FinalReadinessRecord:
    """Every binding leg: the exact form, the digest of the frozen bytes, the
    strict and canonical record, every predicate PASS, and R."""
    digest = parse_owner_go_ref(ref)
    if digest != sha256_hex(frozen):
        raise FinalReadinessError("owner_go_ref digest does not equal the SHA-256 of the frozen record bytes")
    record = load_frozen_record(frozen)
    eligibility = evaluate_final_record(record)
    if not eligibility.eligible:
        raise FinalReadinessError("frozen record is not eligible: " + "; ".join(eligibility.reasons))
    if record.readiness_source_sha != readiness_source_sha:
        raise FinalReadinessError("frozen record readiness_source_sha is not R")
    return record


# ---------------------------------------------------------------------------
# Armed-state evaluators (rows 6/16e, 16d, 18, 25) and the armed carry-forward
# ---------------------------------------------------------------------------

Outcome = rd.Outcome


def _checks(label: str, checks: Mapping[str, bool], **evidence) -> Outcome:
    failed = sorted(k for k, v in checks.items() if v is not True)
    data = {**{k: bool(v is True) for k, v in checks.items()}, **evidence}
    return Outcome("FAIL", data, f"{label}: " + ", ".join(failed)) if failed else Outcome("PASS", data, None)


def evaluate_armed_marker_semantics(facts: Mapping) -> Outcome:
    """Rows 6 and 16e, armed: the runner, its marker fields and the workflow
    marker name are bound to the replacement purpose."""
    return _checks("armed marker semantics", {
        "unconstructible_without_frozen_fields": facts.get("unconstructible_without_frozen_fields"),
        "canonical_name_is_replacement_slug": facts.get("canonical_name") == REPLACEMENT_MARKER_NAME_123,
        "history_permits_exactly_one": facts.get("history_permits_exactly_one"),
        "runner_purpose_is_replacement": facts.get("runner_purpose") == ARMED_PURPOSE,
        "marker_fields_bound": facts.get("marker_fields_bound"),
        "workflow_marker_name_is_replacement": facts.get("workflow_marker_name_is_replacement"),
    }, canonical_name=str(facts.get("canonical_name")))


def evaluate_armed_envelope_binding(envelope: Outcome, facts: Mapping) -> Outcome:
    """Component 16d, armed: the envelope is the frozen value and the runner's
    ENVELOPE identity equals the committed one."""
    return _checks("armed envelope binding", {
        "envelope_pass": envelope.status == "PASS",
        "runner_envelope_id_committed": facts.get("runner_envelope_id") == rd.EXPECTED_ENVELOPE_ID,
        "runner_envelope_version_1": facts.get("runner_envelope_version") == rd.EXPECTED_ENVELOPE_VERSION,
    }, envelope=envelope.evidence)


def evaluate_armed_retry_row(facts: Mapping) -> Outcome:
    """Row 18, armed: CI coverage, the unchanged non-cancelling concurrency
    group and the latch's attempt-1 refusal."""
    return _checks("no automatic replacement retry", {
        "ci_coverage": facts.get("ci_coverage"),
        "concurrency_group_unchanged": facts.get("concurrency_group") == OFFICIAL_CONCURRENCY_GROUP,
        "cancel_in_progress_false": facts.get("cancel_in_progress") is False,
        "latch_refuses_rerun_attempt": facts.get("latch_refuses_rerun_attempt"),
    })


ARMED_LATCH_KEYS = (
    "kinds", "file_sha256", "head_sha256", "unarmed_refusal_reason", "eligibility_requires_latch",
    "preflight_consults_latch_in_order", "enforcement_tests_green", "history_permits_exactly_one",
    "official_run_numbers", "replacement_prefix_artifacts", "gate_evidence_prefix_artifacts",
    "replacement_receipts", "runner_purpose", "runner_envelope_id",
)


def evaluate_armed_latch_row(facts: Mapping) -> Outcome:
    """Row 25, armed and still unauthorized at R."""
    missing = [k for k in ARMED_LATCH_KEYS if k not in facts]
    if missing:
        return Outcome("FAIL", {}, "latch facts are incomplete: " + ", ".join(missing))
    problems = []
    if facts["kinds"] != ["GENESIS"]:
        problems.append("latch is not exactly one GENESIS record at R")
    if facts["file_sha256"] != rd.EXPECTED_LATCH_FILE_SHA256 or facts["head_sha256"] != rd.EXPECTED_LATCH_HEAD_SHA256:
        problems.append("latch bytes or chain head differ from the committed GENESIS")
    if facts["unarmed_refusal_reason"] != "LATCH_UNARMED":
        problems.append("an UNARMED latch does not refuse admission")
    for key in ("eligibility_requires_latch", "preflight_consults_latch_in_order", "enforcement_tests_green",
                "history_permits_exactly_one"):
        if facts[key] is not True:
            problems.append(f"{key} does not hold")
    if facts["runner_purpose"] != ARMED_PURPOSE:
        problems.append("the runner is not bound to the replacement purpose")
    if facts["runner_envelope_id"] != rd.EXPECTED_ENVELOPE_ID:
        problems.append("the runner ENVELOPE identity is not the committed envelope")
    if facts["official_run_numbers"] != [1, 2, 3, 4]:
        problems.append("official-gate run history is not exactly runs 1 to 4")
    for key in ("replacement_prefix_artifacts", "gate_evidence_prefix_artifacts", "replacement_receipts"):
        if facts[key] != 0:
            problems.append(f"{key} is not zero")
    evidence = {k: facts[k] for k in ARMED_LATCH_KEYS}
    return Outcome("FAIL", evidence, "; ".join(problems)) if problems else Outcome("PASS", evidence, None)


def evaluate_armed_carry_forward(spec: rd.CarrySpec, facts: Mapping) -> Outcome:
    """The D4 carry-forward with the armed workflow-diff allowance: exactly the
    timeout change plus the six B6-4 lines, nothing else."""
    base = rd.evaluate_carry_forward(replace(spec, workflow_diff_allowed=False), facts)
    problems = [base.reason] if base.reason else []
    lines = facts.get("workflow_diff_lines")
    if spec.workflow_diff_allowed:
        if not isinstance(lines, list):
            problems.append("workflow diff was not enumerated")
        elif sorted(lines) != sorted(ARMED_WORKFLOW_DIFF_LINES):
            problems.append("official workflow diff is not exactly the timeout change plus the B6-4 arming lines")
    evidence = {**base.evidence, "workflow_diff_lines": list(lines) if isinstance(lines, list) else None}
    return Outcome("FAIL", evidence, "; ".join(problems)) if problems else Outcome("PASS", evidence, None)


def evaluate_identifiers(transcription: OwnerTranscription, variables: Mapping[str, str]) -> Outcome:
    """Console organization and service-account ids equal the repository
    variables, compared by SHA-256 only."""
    org = variables.get("ANTHROPIC_ORGANIZATION_ID")
    sa = variables.get("ANTHROPIC_SERVICE_ACCOUNT_ID")
    return _checks("identifiers", {
        "organization_id_equal": org is not None and sha256_hex(org.encode("utf-8")) == transcription.organization_id_sha256,
        "service_account_id_equal": sa is not None and sha256_hex(sa.encode("utf-8")) == transcription.service_account_id_sha256,
    })


def rule_readback(transcription: OwnerTranscription, *, variable_value: object, oidc_customization: object) -> dict:
    """The readback ``readiness.evaluate_replacement_rule`` consumes. The
    variable value and the OIDC customization come from the mechanical ``gh``
    read, never from the transcription."""
    return {
        "rule": transcription.rule, "other_rules": transcription.other_rules,
        "original_rules_archived": transcription.original_rules_archived,
        "auth_events_for_rule": transcription.auth_events_for_rule,
        "variable_value": variable_value, "oidc_customization": oidc_customization,
    }


def cap_readback(transcription: OwnerTranscription, fx: FxReading) -> dict:
    cap = transcription.cap
    return {
        "month_to_date_spend_usd": cap.month_to_date_spend_usd, "headroom_eur": FROZEN_HEADROOM_EUR,
        "granularity_usd": cap.granularity_usd, "cap_usd": cap.cap_usd, "fx_usd_per_eur": fx.usd_per_eur,
        "fx_retrieved_at_utc": fx.retrieved_at_utc.isoformat(), "currency": cap.currency, "period": cap.period,
    }


def evaluate_cap_final(transcription: OwnerTranscription, fx: FxReading, *, now: datetime) -> Outcome:
    """Component 16i: the frozen cap evaluator, plus credit covering the
    remaining exposure and auto-reload Off (the B6-5 checks)."""
    base = rd.evaluate_cap(cap_readback(transcription, fx), now=now)
    cap = transcription.cap
    credit_ok = Decimal(cap.credit_usd) >= Decimal(cap.cap_usd) - Decimal(cap.month_to_date_spend_usd)
    problems = [base.reason] if base.reason else []
    if not credit_ok:
        problems.append("prepaid credit does not cover the remaining exposure under the cap")
    evidence = {**base.evidence, "credit_covers_exposure": credit_ok, "auto_reload": cap.auto_reload}
    return Outcome("FAIL", evidence, "; ".join(problems)) if problems else Outcome("PASS", evidence, None)


def assemble_rows(outcomes: Mapping[str, tuple[Outcome, Sequence[str]]], stamps: Mapping[str, datetime]
                  ) -> tuple[FinalRowEntry, ...]:
    """Turn ``component -> (outcome, provenance groups)`` into the 25 rows.
    Every component is FINAL_T2 and stamped with its earliest group stamp."""
    rows = []
    for row in rd.ROW_DEFS:
        comps = []
        for name in row.components:
            outcome, groups = outcomes[name]
            prov = tuple(sorted(set(groups)))
            comps.append(FinalComponent(
                component=name, status="PASS" if outcome.status == "PASS" else "FAIL", evidence_state="FINAL_T2",
                provenance=prov, collected_at_utc=min(stamps[g] for g in prov), evidence=outcome.evidence,
                reason=outcome.reason if outcome.status != "PASS" else None,
            ))
        rows.append(FinalRowEntry(row_id=row.row_id, status=rd.aggregate(c.status for c in comps),
                                  components=tuple(comps)))
    return tuple(rows)

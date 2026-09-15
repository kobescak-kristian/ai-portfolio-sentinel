"""Durable Phase-5 evidence receipts (ADR-0012 Amendment A, rule A1;
dispatch q77-p5d-repair-stage1-implement-a, Stage 1).

GitHub Actions artifacts in a public repository expire after at most 90
days, so an artifact-only mechanism cannot truthfully enforce permanent
one-shot consumption or later reconstruction of the authoritative P5-D
history. This module is the committed, hash-chained, append-only
memory of evidence that was ALREADY independently established from the
artifact bytes while they were still downloadable. A receipt never
invents, reconstructs or infers missing quality content: it records
that an artifact existed, was strict-validated, and produced the stated
disposition -- nothing more.

Same canonicalization discipline as ``models.py``: ``extra="forbid"``,
frozen records, canonical JSON via ``models.canonical_json_bytes``,
UTC-only timestamps, SHA-256 over canonical bytes only. No credential,
token, secret value or local absolute path is ever stored.

Stage-2A wiring (dispatch q77-p5d-repair-stage2-implement-a): one-shot
discovery (``scripts/_phase5_common.py``), replacement eligibility
(``sentinel/phase5/replacement.py``), the official/probe gate
preflights and the P5-E seam (``scripts/run_phase5_window_freeze.py``)
now consult this registry before any marker or freeze decision. The
vocabulary widening below (the replacement purpose and its two
additional dispositions) is structural only: it is not armed, no
script constructs a replacement marker, and the historical four-line
registry is unaffected -- Stage 2A appends no receipt.

Immutability contract:

- an established semantic event, keyed by
  ``(receipt_class, purpose, github_run_id, run_attempt)``, is
  immutable; a second receipt with the same key is refused no matter
  what its other fields say, so a conflicting duplicate can never be
  hidden behind a different artifact ID, payload hash or metadata;
- there is no update, delete, truncate, replace or repair API here, and
  none may be added; if a historical correction is ever genuinely
  needed, work stops and returns to owner governance for a separately
  designed append-only correction record;
- a MISSING registry is an error for every ordinary read and append.
  Absence is never "no marker was consumed". The only exception is the
  explicit, one-time initial establishment of the historical registry
  (``allow_missing=True`` / ``allow_create=True``).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifact_names import gate_evidence_name, oneshot_marker_name, probe_evidence_name
from .models import canonical_json_bytes, sha256_hex_of_model

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ZERO_SHA256 = "0" * 64
DEFAULT_REGISTRY_PATH = Path("artifacts/phase5_receipt_registry.jsonl")

MARKER_PAYLOAD_FILENAME = "marker.json"
PROBE_PAYLOAD_FILENAME = "probe-evidence.json"
GATE_PAYLOAD_FILENAME = "phase5_official_gate.json"

EXECUTION_INVALID_NO_QUALITY_RESULT = "EXECUTION_INVALID / NO_QUALITY_RESULT"
EXECUTION_INVALID_INFRASTRUCTURE_FAILURE = "EXECUTION_INVALID / INFRASTRUCTURE_FAILURE"
PUBLICATION_FAILED = "PUBLICATION_FAILED"

# Deliberately duplicated string literals rather than importing from
# .replacement (this module's own established convention -- see the
# "Local validator helpers" note below; importing from .replacement
# would also be circular, since .replacement imports FROM this
# module). tests/test_phase5_replacement.py cross-pins these against
# the public constants in sentinel/phase5/replacement.py, the same
# anti-tautology precedent scripts/run_phase5_official_gate.py already
# uses for its own local cost literals.
_REPLACEMENT_PURPOSE = "P5D_REPLACEMENT_SONNET_GATE"
_REPLACEMENT_OF_RUN_ID = "32880880053"
_REPLACEMENT_OWNER_RULING_ID = "q77-p5d-replacement-owner-ruling-a"

ReceiptClass = Literal[
    "ONESHOT_MARKER_CONSUMED",
    "PROBE_EVIDENCE",
    "GATE_EVIDENCE",
    "EXECUTION_DISPOSITION",
]
ReceiptPurpose = Literal[
    "P5C_WIF_PROBE", "P5D_OFFICIAL_SONNET_GATE", "P5D_REPLACEMENT_SONNET_GATE"
]
ReceiptDisposition = Literal[
    "CONSUMED",
    "CAPABILITY_PASS",
    "CAPABILITY_FAIL",
    "GREEN",
    "HONEST_FAIL",
    "INFRASTRUCTURE_FAILURE",
    "EXECUTION_INVALID / NO_QUALITY_RESULT",
    "EXECUTION_INVALID / INFRASTRUCTURE_FAILURE",
    "PUBLICATION_FAILED",
]

_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


# ---------------------------------------------------------------------------
# Local validator helpers -- deliberately duplicated rather than reaching
# into models.py's private names (this package's established convention).
# ---------------------------------------------------------------------------


def _require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError("naive datetimes are not permitted")
    if offset != timedelta(0):
        raise ValueError("UTC offset must be exactly zero")
    return value


def _require_identifier(value: str) -> str:
    if value.strip() != value or not value:
        raise ValueError("must be a non-empty identifier with no surrounding whitespace")
    return value


def _require_hex40(value: str) -> str:
    if not _HEX40.fullmatch(value):
        raise ValueError("must be exactly 40 lowercase hexadecimal characters")
    return value


def _require_hex64(value: str) -> str:
    if not _HEX64.fullmatch(value):
        raise ValueError("must be exactly 64 lowercase hexadecimal characters")
    return value


def _require_run_id(value: str) -> str:
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("github_run_id must be a purely numeric GitHub run id")
    return value


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ReceiptRegistryError(RuntimeError):
    """The registry is missing, malformed, truncated, chain-broken, or an
    append could not be verified. Never carries a token or local path."""


class DuplicateSemanticEvent(ReceiptRegistryError):
    """A second receipt for an already-established semantic event key was
    found or would be created. Fails closed regardless of other fields."""


class AmbiguousQualityHistory(ReceiptRegistryError):
    """More than one authoritative quality receipt exists for a purpose.
    Fails closed rather than picking one."""


# ---------------------------------------------------------------------------
# Receipt record
# ---------------------------------------------------------------------------


class Phase5Receipt(BaseModel):
    """One immutable durable receipt. Strict: unknown fields are refused,
    every instance is frozen, and each ``receipt_class`` carries exactly
    the cross-field shape ADR-0012 A1 assigns to it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    receipt_class: ReceiptClass
    purpose: ReceiptPurpose
    github_run_id: str
    run_attempt: int = Field(ge=1)
    source_sha: str
    artifact_name: str | None
    artifact_id: int | None
    payload_filename: str | None
    payload_sha256: str | None
    disposition: ReceiptDisposition
    replacement_of_run_id: str | None
    owner_ruling_id: str | None
    governance_ref: str | None
    prev_receipt_sha256: str
    recorded_at_utc: datetime

    @property
    def semantic_key(self) -> tuple[str, str, str, int]:
        return (self.receipt_class, self.purpose, self.github_run_id, self.run_attempt)

    def _require_artifact_fields(self, expected_name: str, expected_payload: str) -> None:
        if self.artifact_name is None or self.artifact_id is None:
            raise ValueError(f"{self.receipt_class} requires artifact_name and artifact_id")
        if self.payload_filename is None or self.payload_sha256 is None:
            raise ValueError(f"{self.receipt_class} requires payload_filename and payload_sha256")
        if self.artifact_id < 1:
            raise ValueError("artifact_id must be a positive GitHub artifact id")
        if self.artifact_name != expected_name:
            raise ValueError(
                f"{self.receipt_class} artifact_name must equal the canonical name "
                f"{expected_name!r} for this purpose/run/attempt"
            )
        if self.payload_filename != expected_payload:
            raise ValueError(f"{self.receipt_class} payload_filename must be {expected_payload!r}")
        _require_hex64(self.payload_sha256)

    @model_validator(mode="after")
    def _validate(self) -> "Phase5Receipt":
        _require_run_id(self.github_run_id)
        _require_hex40(self.source_sha)
        _require_hex64(self.prev_receipt_sha256)
        _require_utc(self.recorded_at_utc)
        if self.governance_ref is not None:
            _require_identifier(self.governance_ref)

        # Purpose-keyed replacement provenance (ADR-0012 section 18;
        # dispatch q77-p5d-repair-stage2-implement-a). Every receipt
        # class for the replacement purpose must bind the exact frozen
        # original-run id and owner ruling id; every other purpose
        # (including the original P5-D purpose) must never carry
        # replacement_of_run_id at all -- receipt 4's own
        # owner_ruling_id stays valid and unrestricted in shape.
        is_replacement_purpose = self.purpose == _REPLACEMENT_PURPOSE
        if is_replacement_purpose:
            if self.replacement_of_run_id != _REPLACEMENT_OF_RUN_ID:
                raise ValueError(
                    f"purpose {_REPLACEMENT_PURPOSE!r} requires replacement_of_run_id == "
                    f"{_REPLACEMENT_OF_RUN_ID!r}"
                )
            if self.owner_ruling_id != _REPLACEMENT_OWNER_RULING_ID:
                raise ValueError(
                    f"purpose {_REPLACEMENT_PURPOSE!r} requires owner_ruling_id == "
                    f"{_REPLACEMENT_OWNER_RULING_ID!r}"
                )
        else:
            if self.replacement_of_run_id is not None:
                raise ValueError(
                    "replacement_of_run_id must be null for any purpose other than "
                    f"{_REPLACEMENT_PURPOSE!r}"
                )
            if self.owner_ruling_id is not None:
                _require_identifier(self.owner_ruling_id)

        cls = self.receipt_class
        if cls == "ONESHOT_MARKER_CONSUMED":
            if self.disposition != "CONSUMED":
                raise ValueError("ONESHOT_MARKER_CONSUMED requires disposition CONSUMED")
            self._require_artifact_fields(
                oneshot_marker_name(self.purpose, self.github_run_id), MARKER_PAYLOAD_FILENAME
            )
        elif cls == "PROBE_EVIDENCE":
            if self.purpose != "P5C_WIF_PROBE":
                raise ValueError("PROBE_EVIDENCE requires purpose P5C_WIF_PROBE")
            if self.disposition not in ("CAPABILITY_PASS", "CAPABILITY_FAIL"):
                raise ValueError("PROBE_EVIDENCE disposition must be CAPABILITY_PASS or CAPABILITY_FAIL")
            self._require_artifact_fields(
                probe_evidence_name(self.github_run_id, self.run_attempt), PROBE_PAYLOAD_FILENAME
            )
        elif cls == "GATE_EVIDENCE":
            if self.purpose != _REPLACEMENT_PURPOSE:
                raise ValueError(
                    f"GATE_EVIDENCE requires purpose {_REPLACEMENT_PURPOSE!r} -- the "
                    "original P5-D purpose is permanently non-qualifying and can never "
                    "acquire gate evidence (ADR-0012 Amendment A1 rule 4)"
                )
            if self.disposition not in ("GREEN", "HONEST_FAIL", "INFRASTRUCTURE_FAILURE"):
                raise ValueError(
                    "GATE_EVIDENCE disposition must be GREEN, HONEST_FAIL or INFRASTRUCTURE_FAILURE"
                )
            self._require_artifact_fields(
                gate_evidence_name(self.github_run_id, self.run_attempt), GATE_PAYLOAD_FILENAME
            )
        else:  # EXECUTION_DISPOSITION
            if is_replacement_purpose:
                if self.disposition not in (
                    EXECUTION_INVALID_INFRASTRUCTURE_FAILURE,
                    PUBLICATION_FAILED,
                ):
                    raise ValueError(
                        "EXECUTION_DISPOSITION for the replacement purpose must be "
                        f"{EXECUTION_INVALID_INFRASTRUCTURE_FAILURE!r} or {PUBLICATION_FAILED!r}"
                    )
            elif self.disposition != EXECUTION_INVALID_NO_QUALITY_RESULT:
                raise ValueError(
                    "EXECUTION_DISPOSITION requires disposition "
                    f"{EXECUTION_INVALID_NO_QUALITY_RESULT!r} for any purpose other than "
                    f"{_REPLACEMENT_PURPOSE!r}"
                )
            if any(
                field is not None
                for field in (self.artifact_name, self.artifact_id, self.payload_filename, self.payload_sha256)
            ):
                raise ValueError(
                    "EXECUTION_DISPOSITION carries no artifact fields -- it must never imply "
                    "that evidence bytes existed"
                )
            if self.governance_ref is None or self.owner_ruling_id is None:
                raise ValueError("EXECUTION_DISPOSITION requires governance_ref and owner_ruling_id")
        return self


# ---------------------------------------------------------------------------
# Hashing / line encoding
# ---------------------------------------------------------------------------


def receipt_sha256(receipt: Phase5Receipt) -> str:
    """SHA-256 of the receipt's canonical bytes -- the value the next
    receipt's ``prev_receipt_sha256`` must carry."""
    return sha256_hex_of_model(receipt)


def receipt_line_bytes(receipt: Phase5Receipt) -> bytes:
    """Exactly the bytes one registry line occupies: canonical JSON + LF."""
    return canonical_json_bytes(receipt) + b"\n"


def registry_head_sha256(receipts: tuple[Phase5Receipt, ...]) -> str:
    return receipt_sha256(receipts[-1]) if receipts else ZERO_SHA256


# ---------------------------------------------------------------------------
# Registry read -- strict, complete-chain validation, no repair
# ---------------------------------------------------------------------------


def load_registry(path: Path, *, allow_missing: bool = False) -> tuple[Phase5Receipt, ...]:
    """Strict-load and validate the COMPLETE registry chain.

    A missing file fails closed (``ReceiptRegistryError``) unless
    ``allow_missing=True``, which exists solely for the one-time initial
    establishment of the historical registry and must never be used by
    an ordinary durable-history read. Absence is never empty history.
    """
    path = Path(path)
    if not path.exists():
        if allow_missing:
            return ()
        raise ReceiptRegistryError(
            "receipt registry is missing -- absence is never empty history; refusing to read"
        )
    if path.is_symlink() or not path.is_file():
        raise ReceiptRegistryError("receipt registry path is not a regular file")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReceiptRegistryError("receipt registry is not valid UTF-8") from exc
    if "\r" in text:
        # The registry is always WRITTEN with LF. A Windows checkout under
        # core.autocrlf may present CRLF; that is a transport artifact,
        # not tampering, so consistent CRLF is normalized on read. A lone
        # CR is never valid.
        if text.count("\r") != text.count("\r\n"):
            raise ReceiptRegistryError("receipt registry contains a bare carriage return")
        text = text.replace("\r\n", "\n")
    if text == "":
        raise ReceiptRegistryError("receipt registry exists but is empty -- refusing to read as history")
    if not text.endswith("\n"):
        raise ReceiptRegistryError("receipt registry has a trailing fragment (no final newline)")
    lines = text[:-1].split("\n")

    receipts: list[Phase5Receipt] = []
    seen_keys: set[tuple[str, str, str, int]] = set()
    expected_prev = ZERO_SHA256
    for index, line in enumerate(lines, start=1):
        if line.strip() == "":
            raise ReceiptRegistryError(f"receipt registry line {index} is blank")
        try:
            json.loads(line)
        except ValueError as exc:
            raise ReceiptRegistryError(f"receipt registry line {index} is not valid JSON") from exc
        try:
            receipt = Phase5Receipt.model_validate_json(line)
        except Exception as exc:  # pydantic ValidationError; keep the message identifier-only
            raise ReceiptRegistryError(f"receipt registry line {index} failed strict validation") from exc
        if canonical_json_bytes(receipt) != line.encode("utf-8"):
            raise ReceiptRegistryError(f"receipt registry line {index} is not in canonical form")
        if receipt.prev_receipt_sha256 != expected_prev:
            raise ReceiptRegistryError(f"receipt registry line {index} breaks the hash chain")
        key = receipt.semantic_key
        if key in seen_keys:
            raise DuplicateSemanticEvent(
                f"receipt registry line {index} duplicates an established semantic event"
            )
        seen_keys.add(key)
        receipts.append(receipt)
        expected_prev = receipt_sha256(receipt)
    return tuple(receipts)


# ---------------------------------------------------------------------------
# Registry append -- the ONLY write operation
# ---------------------------------------------------------------------------


def append_receipt(
    path: Path,
    *,
    allow_create: bool = False,
    clock: Callable[[], datetime] | None = None,
    **fields,
) -> Phase5Receipt:
    """Validate the whole existing registry, refuse a duplicate semantic
    event, construct exactly one receipt, append one canonical UTF-8/LF
    line, flush, fsync, reload the whole registry, and require the new
    validated head to be the just-created receipt.

    ``allow_create=True`` is the one-time initial-establishment
    exception: it only permits the file to not exist yet. An existing
    file is always fully validated regardless of the flag. ``clock`` is
    injectable for tests; it must return an aware UTC datetime.
    """
    path = Path(path)
    if "prev_receipt_sha256" in fields or "recorded_at_utc" in fields:
        raise ReceiptRegistryError(
            "prev_receipt_sha256 and recorded_at_utc are established by the append itself"
        )
    existing = load_registry(path, allow_missing=allow_create)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    receipt = Phase5Receipt(
        **fields,
        prev_receipt_sha256=registry_head_sha256(existing),
        recorded_at_utc=_require_utc(now).replace(microsecond=0),
    )
    if any(receipt.semantic_key == other.semantic_key for other in existing):
        raise DuplicateSemanticEvent(
            "a receipt for this semantic event is already established; refusing to append"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as handle:
        handle.write(receipt_line_bytes(receipt))
        handle.flush()
        os.fsync(handle.fileno())
    reloaded = load_registry(path)
    if len(reloaded) != len(existing) + 1:
        raise ReceiptRegistryError("post-append verification failed: unexpected receipt count")
    if reloaded[-1] != receipt:
        raise ReceiptRegistryError("post-append verification failed: head is not the appended receipt")
    return receipt


# ---------------------------------------------------------------------------
# Read helpers over an already-loaded registry (later stages consume these)
# ---------------------------------------------------------------------------


def marker_receipts(
    receipts: tuple[Phase5Receipt, ...], purpose: str | None = None
) -> tuple[Phase5Receipt, ...]:
    return tuple(
        r
        for r in receipts
        if r.receipt_class == "ONESHOT_MARKER_CONSUMED" and (purpose is None or r.purpose == purpose)
    )


def consumed_purposes(receipts: tuple[Phase5Receipt, ...]) -> frozenset[str]:
    """Consumption truth comes from durable marker receipts, never from
    artifact retention."""
    return frozenset(r.purpose for r in marker_receipts(receipts))


def is_purpose_consumed(receipts: tuple[Phase5Receipt, ...], purpose: str) -> bool:
    return purpose in consumed_purposes(receipts)


def evidence_receipts(
    receipts: tuple[Phase5Receipt, ...], receipt_class: str
) -> tuple[Phase5Receipt, ...]:
    return tuple(r for r in receipts if r.receipt_class == receipt_class)


def execution_dispositions(
    receipts: tuple[Phase5Receipt, ...], github_run_id: str | None = None
) -> tuple[Phase5Receipt, ...]:
    return tuple(
        r
        for r in receipts
        if r.receipt_class == "EXECUTION_DISPOSITION"
        and (github_run_id is None or r.github_run_id == github_run_id)
    )


def authoritative_quality_receipt(
    receipts: tuple[Phase5Receipt, ...], purpose: str
) -> Phase5Receipt | None:
    """Exactly one GREEN/HONEST_FAIL GATE_EVIDENCE receipt for ``purpose``
    is authoritative. None means no authoritative quality result exists
    (which is exactly the original P5-D situation); more than one fails
    closed."""
    matches = [
        r
        for r in receipts
        if r.receipt_class == "GATE_EVIDENCE"
        and r.purpose == purpose
        and r.disposition in ("GREEN", "HONEST_FAIL")
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise AmbiguousQualityHistory(purpose)
    return matches[0]

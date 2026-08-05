"""Phase 0 telemetry harness tests (BLUEPRINT §6 P0).

Covers the frozen CostRow contract, the frozen JSONL serialization
rule, and the dry run's no-repository-residue guarantee.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schemas import CostRow
from telemetry.cost_ledger import append_cost_row, read_cost_rows, serialize_cost_row

REPO_ROOT = Path(__file__).resolve().parents[1]

FIXED_INSTANT = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)

EXPECTED_FIXED_LINE = (
    '{"cost_eur_micros":3,"input_tokens":1,"model":"provider/model-name",'
    '"output_tokens":2,"recorded_at_utc":"2026-08-03T12:00:00+00:00",'
    '"run_id":"fixture-001","run_kind":"dev","schema_version":1}'
)


def make_row(**overrides):
    data = dict(
        schema_version=1,
        run_id="fixture-001",
        recorded_at_utc=FIXED_INSTANT,
        run_kind="dev",
        model="provider/model-name",
        input_tokens=1,
        output_tokens=2,
        cost_eur_micros=3,
    )
    data.update(overrides)
    return CostRow(**data)


def test_write_and_read_back_equality(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    row = make_row()
    append_cost_row(ledger, row)
    rows = read_cost_rows(ledger)
    assert rows == [row]
    assert rows[0].recorded_at_utc == FIXED_INSTANT
    assert rows[0].recorded_at_utc.utcoffset() == timedelta(0)


def test_exact_serialization_frozen_rule():
    row = make_row()
    assert serialize_cost_row(row) == EXPECTED_FIXED_LINE
    assert CostRow.model_validate(json.loads(EXPECTED_FIXED_LINE)) == row


def test_multiple_rows_preserve_order(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    written = [make_row(run_id=f"fixture-{i:03d}") for i in range(1, 4)]
    for row in written:
        append_cost_row(ledger, row)
    assert read_cost_rows(ledger) == written


def test_unknown_extra_field_rejected():
    with pytest.raises(ValidationError):
        CostRow(
            schema_version=1,
            run_id="fixture-001",
            recorded_at_utc=FIXED_INSTANT,
            run_kind="dev",
            model="provider/model-name",
            input_tokens=1,
            output_tokens=2,
            cost_eur_micros=3,
            surprise_field=1,
        )


@pytest.mark.parametrize("field", ["run_id", "model"])
@pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
def test_empty_and_whitespace_only_rejected(field, bad):
    with pytest.raises(ValidationError):
        make_row(**{field: bad})


@pytest.mark.parametrize(
    "field", ["input_tokens", "output_tokens", "cost_eur_micros"]
)
def test_negative_values_rejected(field):
    with pytest.raises(ValidationError):
        make_row(**{field: -1})


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        make_row(recorded_at_utc=datetime(2026, 8, 3, 12, 0, 0))


def test_aware_non_utc_datetime_rejected():
    plus_two = timezone(timedelta(hours=2))
    with pytest.raises(ValidationError):
        make_row(recorded_at_utc=datetime(2026, 8, 3, 12, 0, 0, tzinfo=plus_two))


def test_utc_datetime_accepted():
    zero_offset = timezone(timedelta(0))
    row = make_row(recorded_at_utc=datetime(2026, 8, 3, 12, 0, 0, tzinfo=zero_offset))
    assert row.recorded_at_utc.utcoffset() == timedelta(0)


PATH_GUARD_REJECTED = [
    "/home/user/file",
    "path=/home/user/file",
    'source:"/var/tmp/file"',
    r"C:\Users\Name\file",
    "path=C:/Users/Name/file",
    r"\\server\share\file",
    r"path=\\server\share\file",
]

PATH_GUARD_ACCEPTED = [
    "provider/model-name",
    "model:v1/subtype",
]


@pytest.mark.parametrize("field", ["run_id", "model"])
@pytest.mark.parametrize("bad", PATH_GUARD_REJECTED)
def test_path_guard_rejected(field, bad):
    with pytest.raises(ValidationError):
        make_row(**{field: bad})


@pytest.mark.parametrize("field", ["run_id", "model"])
@pytest.mark.parametrize("good", PATH_GUARD_ACCEPTED)
def test_path_guard_accepted(field, good):
    row = make_row(**{field: good})
    assert getattr(row, field) == good


def test_environment_value_absent_from_serialized_output(monkeypatch):
    sentinel_value = "canary-e5c1a9d7f3b2-distinctive-test-only-value"
    monkeypatch.setenv("AI_PORTFOLIO_SENTINEL_TEST_CANARY", sentinel_value)
    line = serialize_cost_row(make_row())
    assert sentinel_value not in line


def _git_visible_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return set(result.stdout.splitlines())


def test_dry_run_leaves_git_visible_state_unchanged(monkeypatch):
    """Does not assume telemetry/cost_ledger.jsonl is absent -- once
    Phase 2 has run a real dev/live run, it legitimately exists with
    real, committed content. The invariant this test actually proves
    is narrower and just as strict: the dry run writes to a tempdir
    and reads it back from there, so it must never create *or mutate*
    the repo's real ledger, whatever state that ledger was already in."""
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    ledger_path = REPO_ROOT / "telemetry" / "cost_ledger.jsonl"
    before_files = _git_visible_files()
    before_ledger_bytes = ledger_path.read_bytes() if ledger_path.exists() else None

    result = subprocess.run(
        [sys.executable, "-m", "telemetry.dry_run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    printed = json.loads(result.stdout.strip())
    assert printed["run_kind"] == "dev"
    assert printed["cost_eur_micros"] == 0

    after_files = _git_visible_files()
    assert before_files == after_files

    after_ledger_bytes = ledger_path.read_bytes() if ledger_path.exists() else None
    assert before_ledger_bytes == after_ledger_bytes, (
        "the dry run must never create or mutate the repo's real, committed "
        "cost ledger -- it writes to a tempdir and reads it back from there"
    )

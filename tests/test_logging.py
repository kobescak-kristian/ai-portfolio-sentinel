"""Structured JSONL logging: schema, redaction, closed event
vocabulary, and the cost-ledger trailing-fragment repair."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from sentinel import costs
from sentinel.logs import EVENTS, RunLogger, redact
from telemetry.cost_ledger import append_cost_row, read_cost_rows

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


def test_every_line_is_one_json_object(tmp_path):
    path = tmp_path / "run.jsonl"
    with RunLogger(path) as logger:
        logger.log("INFO", "run.started", now=NOW, run_id="r1")
        logger.log("INFO", "run.completed", now=NOW, run_id="r1")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # raises if not valid JSON


def test_required_fields_present():
    path_records = []

    class Capture:
        def write(self, data):
            path_records.append(data)

        def flush(self):
            pass

    logger = RunLogger.__new__(RunLogger)
    import pathlib

    logger.path = pathlib.Path("unused")
    logger._handle = Capture()
    logger.log("INFO", "run.started", now=NOW, run_id="r1", task_id="t1", check_class="broken-link", surface="a/b.md")
    record = json.loads(path_records[0])
    assert record["schema_version"] == 1
    assert record["ts"] == "2026-08-04T12:00:00+00:00"
    assert record["severity"] == "INFO"
    assert record["event"] == "run.started"
    assert record["run_id"] == "r1"
    assert record["task_id"] == "t1"
    assert record["check_class"] == "broken-link"
    assert record["surface"] == "a/b.md"


def test_unknown_event_rejected(tmp_path):
    with RunLogger(tmp_path / "run.jsonl") as logger:
        with pytest.raises(ValueError):
            logger.log("INFO", "not.a.real.event", now=NOW)


def test_closed_event_vocabulary_is_exactly_declared():
    assert "run.started" in EVENTS
    assert "task.dead_letter" in EVENTS
    assert "not.a.real.event" not in EVENTS


@pytest.mark.parametrize(
    "raw,expected_marker",
    [
        ("C:\\Users\\kristian\\secret.txt", "<redacted-path>"),
        ("/home/kristian/.env", "<redacted-path>"),
        ("ghp_1234567890abcdef", "<redacted-secret>"),
        ("Bearer sometoken123", "<redacted-secret>"),
        ("token=abc123def456", "<redacted-secret>"),
    ],
)
def test_redact_strips_paths_and_secrets(raw, expected_marker):
    assert expected_marker in redact(raw)


def test_redact_leaves_ordinary_text_alone():
    assert redact("dead link at https://github.com/x does not resolve") == (
        "dead link at https://github.com/x does not resolve"
    )


def test_redact_truncates_long_messages():
    long_message = "x" * 500
    result = redact(long_message)
    assert len(result) <= 201  # 200 chars + ellipsis marker
    assert result.endswith("…")


def test_redact_strips_control_characters():
    assert "\x00" not in redact("bad\x00byte in message")


def test_error_message_field_goes_through_redaction(tmp_path):
    path = tmp_path / "run.jsonl"
    with RunLogger(path) as logger:
        logger.log(
            "ERROR", "task.dead_letter", now=NOW, run_id="r1",
            error_type="OSError", error_message="C:\\Users\\kristian\\file.txt not found",
        )
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "kristian" not in record["error_message"]
    assert "<redacted-path>" in record["error_message"]


def test_log_is_lf_only(tmp_path):
    path = tmp_path / "run.jsonl"
    with RunLogger(path) as logger:
        logger.log("INFO", "run.started", now=NOW, run_id="r1")
    assert b"\r" not in path.read_bytes()


# --- cost-ledger trailing-fragment repair (C4) ------------------------------


def test_repair_trailing_fragment_no_op_on_well_formed_file(tmp_path):
    path = tmp_path / "cost_ledger.jsonl"
    row = costs.build_zero_cost_row(run_id="r1", run_kind="dev", recorded_at_utc=NOW)
    append_cost_row(path, row)
    assert costs.repair_trailing_fragment(path) is False
    assert len(read_cost_rows(path)) == 1


def test_repair_trailing_fragment_removes_only_the_corrupt_line(tmp_path):
    path = tmp_path / "cost_ledger.jsonl"
    row = costs.build_zero_cost_row(run_id="r1", run_kind="dev", recorded_at_utc=NOW)
    append_cost_row(path, row)
    good_bytes = path.read_bytes()
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"run_id":"r2","truncated')  # no trailing newline, invalid JSON

    assert costs.has_cost_row_for_run(path, "r2") is False
    repaired = costs.repair_trailing_fragment(path)
    assert repaired is True
    assert path.read_bytes() == good_bytes  # r1's well-formed line untouched
    assert costs.has_cost_row_for_run(path, "r1") is True

    row2 = costs.build_zero_cost_row(run_id="r2", run_kind="dev", recorded_at_utc=NOW)
    append_cost_row(path, row2)
    assert costs.has_cost_row_for_run(path, "r2") is True
    rows = read_cost_rows(path)
    assert [r.run_id for r in rows] == ["r1", "r2"]


def test_zero_cost_row_is_a_true_measurement():
    row = costs.build_zero_cost_row(run_id="r1", run_kind="live", recorded_at_utc=NOW)
    assert row.input_tokens == 0
    assert row.output_tokens == 0
    assert row.cost_eur_micros == 0
    assert row.model == "none-deterministic"

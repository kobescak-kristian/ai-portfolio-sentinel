"""Phase 1 contract and ledger-schema tests (BLUEPRINT §6 P1, ADR 0004).

Covers the CheckTask / Finding / RunRecord contracts, the shared
validators (identifier, surface grammar, location, URL-permitting
detail), the finding-local content hash and fingerprint, the database
datetime serializer, and behavioral synchronization between
contracts/schemas.py and contracts/ledger_schema.sql.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schemas import (
    CHECK_CLASSES,
    CheckTask,
    Finding,
    RunRecord,
    compute_content_hash,
    compute_fingerprint,
    serialize_db_datetime,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DDL_PATH = REPO_ROOT / "contracts" / "ledger_schema.sql"

T0 = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)

CONTENT_HASH = compute_content_hash("README.md:12", "example normalized content")
FINGERPRINT = compute_fingerprint(
    "synthetic-01/README.md", "broken-link", CONTENT_HASH
)

# Machine-local path vectors for identifier fields (same guard as the
# frozen CostRow free-text fields). The drive-letter vector uses the
# neutral "Name" username on purpose — the same semantics for the
# validator, without tripping the publish gate's username-anchored
# blocking scan.
IDENTIFIER_PATH_REJECTED = [
    "/home/user/file",
    "path=/home/user/file",
    'source:"/var/tmp/file"',
    r"C:\Users\Name\file",
    "path=C:/Users/Name/file",
    r"\\server\share\file",
    r"path=\\server\share\file",
]

DETAIL_ACCEPTED = [
    "https://x.invalid/a",
    "dead link at https://github.com/x",
    "see synthetic-01/README.md",
]

DETAIL_REJECTED = [
    r"C:\Users\Name\x",
    "C:/Users/x",
    "/home/claude/x",
    r"\\server\share",
    " /mnt/data",
]

SURFACE_ACCEPTED = [
    "synthetic-01/README.md",
    "repo-name/docs/EVAL_RESULTS.md",
    "site/index",
    "site/reports/weekly",
]

SURFACE_REJECTED = [
    "",
    "   ",
    "README.md",
    "/synthetic-01/README.md",
    "synthetic-01//README.md",
    "synthetic-01/README.md/",
    "https://github.com/x/y",
    "repo:name/file",
    r"synthetic-01\README.md",
    "synthetic-01/../secrets",
    "synthetic-01/READ\x07ME.md",
]

LOCATION_ACCEPTED = [
    "README.md",
    "README.md:12",
    "docs/EVAL_RESULTS.md:7",
    ".githooks/pre-push",
]

LOCATION_REJECTED = [
    "",
    "   ",
    "/abs/path.md",
    "docs//file.md",
    "README.md:0",
    "README.md:-1",
    "README.md:1a",
    "README.md:01",
    "a:b:5",
    r"docs\file.md:3",
    "../escape.md:1",
    "READ\x07ME.md:2",
]


def make_task(**overrides):
    data = dict(
        schema_version=1,
        task_id="task-001",
        run_id="run-001",
        surface="synthetic-01/README.md",
        check_class="broken-link",
        created_at_utc=T0,
        status="PENDING",
    )
    data.update(overrides)
    return CheckTask(**data)


def make_finding(**overrides):
    data = dict(
        schema_version=1,
        fingerprint=FINGERPRINT,
        surface="synthetic-01/README.md",
        check_class="broken-link",
        content_hash=CONTENT_HASH,
        location="README.md:12",
        detail="link https://dead-node.example.invalid/report does not resolve",
        status="OPEN",
        first_seen_utc=T0,
        last_seen_utc=T1,
        resolved_at_utc=None,
        first_seen_run_id="run-001",
        last_seen_run_id="run-002",
        resolved_run_id=None,
    )
    fingerprint_inputs = {"surface", "check_class", "content_hash"}
    data.update(overrides)
    if "fingerprint" not in overrides and fingerprint_inputs & set(overrides):
        data["fingerprint"] = compute_fingerprint(
            data["surface"], data["check_class"], data["content_hash"]
        )
    return Finding(**data)


def make_resolved_finding(**overrides):
    data = dict(status="RESOLVED", resolved_at_utc=T2, resolved_run_id="run-003")
    data.update(overrides)
    return make_finding(**data)


def make_run(**overrides):
    data = dict(
        schema_version=1,
        run_id="run-001",
        run_kind="dev",
        status="COMPLETED",
        started_at_utc=T0,
        finished_at_utc=T1,
        tasks_created=6,
        tasks_terminal=6,
        findings_new=1,
        findings_still_open=2,
        findings_resolved=3,
    )
    data.update(overrides)
    return RunRecord(**data)


FACTORIES = [make_task, make_finding, make_run]


# --- model shape -----------------------------------------------------------


@pytest.mark.parametrize("factory", FACTORIES)
def test_unknown_extra_field_rejected(factory):
    with pytest.raises(ValidationError):
        factory(surprise_field=1)


def test_check_classes_export_matches_literal():
    assert len(CHECK_CLASSES) == 6
    assert len(set(CHECK_CLASSES)) == 6
    with pytest.raises(ValidationError):
        make_task(check_class="not-a-class")


# --- datetimes -------------------------------------------------------------

DATETIME_FIELDS = [
    (make_task, "created_at_utc"),
    (make_finding, "first_seen_utc"),
    (make_finding, "last_seen_utc"),
    (make_run, "started_at_utc"),
]


@pytest.mark.parametrize("factory,field", DATETIME_FIELDS)
def test_naive_datetime_rejected(factory, field):
    with pytest.raises(ValidationError):
        factory(**{field: datetime(2026, 8, 4, 12, 0, 0)})


@pytest.mark.parametrize("factory,field", DATETIME_FIELDS)
def test_aware_non_utc_datetime_rejected(factory, field):
    plus_two = timezone(timedelta(hours=2))
    with pytest.raises(ValidationError):
        factory(**{field: datetime(2026, 8, 4, 12, 0, 0, tzinfo=plus_two)})


def test_optional_datetime_fields_reject_naive_and_non_utc():
    with pytest.raises(ValidationError):
        make_resolved_finding(resolved_at_utc=datetime(2026, 8, 4, 15, 0, 0))
    with pytest.raises(ValidationError):
        make_run(
            finished_at_utc=datetime(
                2026, 8, 4, 15, 0, 0, tzinfo=timezone(timedelta(hours=2))
            )
        )


# --- identifier fields -----------------------------------------------------

IDENTIFIER_FIELDS = [
    (make_task, "task_id"),
    (make_task, "run_id"),
    (make_finding, "first_seen_run_id"),
    (make_finding, "last_seen_run_id"),
    (make_run, "run_id"),
]


@pytest.mark.parametrize("factory,field", IDENTIFIER_FIELDS)
@pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
def test_empty_identifier_rejected(factory, field, bad):
    with pytest.raises(ValidationError):
        factory(**{field: bad})


@pytest.mark.parametrize("factory,field", IDENTIFIER_FIELDS)
@pytest.mark.parametrize("bad", IDENTIFIER_PATH_REJECTED)
def test_identifier_path_guard_rejected(factory, field, bad):
    with pytest.raises(ValidationError):
        factory(**{field: bad})


def test_resolved_run_id_validated_when_present():
    with pytest.raises(ValidationError):
        make_resolved_finding(resolved_run_id="   ")
    with pytest.raises(ValidationError):
        make_resolved_finding(resolved_run_id="/home/user/file")


# --- surface / location / detail -------------------------------------------


@pytest.mark.parametrize("good", SURFACE_ACCEPTED)
def test_surface_accepted(good):
    assert make_task(surface=good).surface == good


@pytest.mark.parametrize("bad", SURFACE_REJECTED)
def test_surface_rejected(bad):
    with pytest.raises(ValidationError):
        make_task(surface=bad)


@pytest.mark.parametrize("good", LOCATION_ACCEPTED)
def test_location_accepted(good):
    finding = make_finding(location=good)
    assert finding.location == good


@pytest.mark.parametrize("bad", LOCATION_REJECTED)
def test_location_rejected(bad):
    with pytest.raises(ValidationError):
        make_finding(location=bad)


@pytest.mark.parametrize("good", DETAIL_ACCEPTED)
def test_detail_accepts_urls_and_repo_relative_paths(good):
    assert make_finding(detail=good).detail == good


@pytest.mark.parametrize("bad", DETAIL_REJECTED)
def test_detail_rejects_machine_local_paths(bad):
    with pytest.raises(ValidationError):
        make_finding(detail=bad)


@pytest.mark.parametrize("bad", ["", "   "])
def test_detail_rejects_empty(bad):
    with pytest.raises(ValidationError):
        make_finding(detail=bad)


# --- counts ----------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "tasks_created",
        "tasks_terminal",
        "findings_new",
        "findings_still_open",
        "findings_resolved",
    ],
)
def test_negative_counts_rejected(field):
    overrides = {field: -1}
    if field == "tasks_created":
        overrides["tasks_terminal"] = -1
    with pytest.raises(ValidationError):
        make_run(**overrides)


# --- hashes and fingerprint -------------------------------------------------

BAD_HASHES = [
    "a" * 63,
    "a" * 65,
    ("a" * 63) + "G",
    ("a" * 63) + "!",
    FINGERPRINT.upper(),
    ("x" * 64),
    "",
]


@pytest.mark.parametrize("field", ["fingerprint", "content_hash"])
@pytest.mark.parametrize("bad", BAD_HASHES)
def test_malformed_hash_rejected(field, bad):
    with pytest.raises(ValidationError):
        make_finding(**{field: bad})


def test_fingerprint_mismatch_rejected():
    wrong = compute_fingerprint("other-repo/README.md", "broken-link", CONTENT_HASH)
    with pytest.raises(ValidationError):
        make_finding(fingerprint=wrong)


def test_fingerprint_matching_accepted():
    finding = make_finding()
    assert finding.fingerprint == compute_fingerprint(
        finding.surface, finding.check_class, finding.content_hash
    )


def test_content_hash_distinguishes_locations_on_same_surface():
    hash_a = compute_content_hash("README.md:12", "same normalized content")
    hash_b = compute_content_hash("README.md:30", "same normalized content")
    assert hash_a != hash_b
    finding_a = make_finding(location="README.md:12", content_hash=hash_a)
    finding_b = make_finding(location="README.md:30", content_hash=hash_b)
    assert finding_a.fingerprint != finding_b.fingerprint


def test_content_hash_deterministic_for_identical_payload():
    assert compute_content_hash(
        "README.md:12", "normalized content"
    ) == compute_content_hash("README.md:12", "normalized content")


def test_content_hash_ignores_content_outside_payload():
    # The payload is location + normalized finding-local content only:
    # a change elsewhere in the file never reaches the hash inputs, so
    # the hash cannot change.
    before = compute_content_hash("README.md:12", "the broken figure line")
    after_unrelated_edit = compute_content_hash("README.md:12", "the broken figure line")
    assert before == after_unrelated_edit
    assert before != compute_content_hash("README.md:12", "an edited figure line")


# --- lifecycle coherence ----------------------------------------------------


def test_open_finding_accepted_and_resolved_finding_accepted():
    assert make_finding().status == "OPEN"
    assert make_resolved_finding().status == "RESOLVED"


@pytest.mark.parametrize(
    "overrides",
    [
        dict(status="RESOLVED", resolved_at_utc=None, resolved_run_id="run-003"),
        dict(status="RESOLVED", resolved_at_utc=T2, resolved_run_id=None),
        dict(status="RESOLVED", resolved_at_utc=None, resolved_run_id=None),
        dict(status="OPEN", resolved_at_utc=T2, resolved_run_id=None),
        dict(status="OPEN", resolved_at_utc=None, resolved_run_id="run-003"),
        dict(status="OPEN", resolved_at_utc=T2, resolved_run_id="run-003"),
    ],
)
def test_finding_status_nullability_coherence(overrides):
    with pytest.raises(ValidationError):
        make_finding(**overrides)


def test_last_seen_before_first_seen_rejected():
    with pytest.raises(ValidationError):
        make_finding(first_seen_utc=T1, last_seen_utc=T0)


def test_resolved_before_last_seen_rejected():
    with pytest.raises(ValidationError):
        make_resolved_finding(resolved_at_utc=T1 - timedelta(seconds=1))


def test_run_status_finished_coherence():
    running = make_run(
        status="RUNNING", finished_at_utc=None, tasks_created=6, tasks_terminal=2
    )
    assert running.finished_at_utc is None
    with pytest.raises(ValidationError):
        make_run(status="RUNNING", finished_at_utc=T1)
    with pytest.raises(ValidationError):
        make_run(status="COMPLETED", finished_at_utc=None)
    with pytest.raises(ValidationError):
        make_run(status="FAILED", finished_at_utc=None)


def test_run_terminal_count_coherence():
    failed = make_run(status="FAILED", tasks_created=6, tasks_terminal=4)
    assert failed.tasks_terminal < failed.tasks_created
    with pytest.raises(ValidationError):
        make_run(status="COMPLETED", tasks_created=6, tasks_terminal=5)
    with pytest.raises(ValidationError):
        make_run(tasks_created=6, tasks_terminal=7)
    with pytest.raises(ValidationError):
        make_run(finished_at_utc=T0 - timedelta(seconds=1))


# --- database datetime serializer ------------------------------------------


def test_serialize_db_datetime_shape():
    text = serialize_db_datetime(T0)
    assert text == "2026-08-04T12:00:00+00:00"
    assert len(text) == 25


def test_serialize_db_datetime_normalizes_and_truncates():
    plus_two = timezone(timedelta(hours=2))
    text = serialize_db_datetime(
        datetime(2026, 8, 4, 14, 0, 0, 999999, tzinfo=plus_two)
    )
    assert text == "2026-08-04T12:00:00+00:00"
    assert datetime.fromisoformat(text) == T0


def test_serialize_db_datetime_rejects_naive():
    with pytest.raises(ValueError):
        serialize_db_datetime(datetime(2026, 8, 4, 12, 0, 0))


# --- ledger DDL: behavioral synchronization --------------------------------


def to_db_row(model) -> dict:
    row = model.model_dump()
    for key, value in row.items():
        if isinstance(value, datetime):
            row[key] = serialize_db_datetime(value)
    return row


def insert(conn, table, row):
    columns = ", ".join(row)
    placeholders = ", ".join(":" + key for key in row)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", row)


def load_ledger():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(DDL_PATH.read_text(encoding="utf-8"))
    return conn


def seeded_conn():
    conn = load_ledger()
    for run_id in ("run-001", "run-002", "run-003"):
        insert(conn, "runs", to_db_row(make_run(run_id=run_id)))
    return conn


TABLE_MODEL_MAP = [
    ("runs", RunRecord, set()),
    ("tasks", CheckTask, set()),
    ("findings", Finding, {"id"}),
]

INTEGER_COLUMNS = {
    "id",
    "schema_version",
    "tasks_created",
    "tasks_terminal",
    "findings_new",
    "findings_still_open",
    "findings_resolved",
}

# findings.id is nullable in PRAGMA terms only: INTEGER PRIMARY KEY is
# the rowid alias and is auto-assigned, never stored NULL.
EXPECTED_NULLABLE = {
    "runs": {"finished_at_utc"},
    "tasks": set(),
    "findings": {"id", "resolved_at_utc", "resolved_run_id"},
}


@pytest.mark.parametrize("table,model,extra", TABLE_MODEL_MAP)
def test_ddl_columns_match_model_fields(table, model, extra):
    conn = load_ledger()
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    columns = {row[1] for row in info}
    assert columns == set(model.model_fields) | extra
    declared_types = {row[1]: row[2].upper() for row in info}
    for column, declared in declared_types.items():
        expected = "INTEGER" if column in INTEGER_COLUMNS else "TEXT"
        assert declared == expected, (table, column, declared)
    nullable = {row[1] for row in info if row[3] == 0}
    assert nullable == EXPECTED_NULLABLE[table]


def test_valid_rows_insert_and_round_trip():
    conn = seeded_conn()
    task = make_task()
    insert(conn, "tasks", to_db_row(task))
    open_finding = make_finding()
    insert(conn, "findings", to_db_row(open_finding))
    resolved = make_resolved_finding(
        content_hash=compute_content_hash("README.md:40", "another defect"),
        location="README.md:40",
    )
    insert(conn, "findings", to_db_row(resolved))

    conn.row_factory = sqlite3.Row
    run_row = dict(conn.execute("SELECT * FROM runs WHERE run_id='run-001'").fetchone())
    assert RunRecord.model_validate(run_row) == make_run()
    task_row = dict(conn.execute("SELECT * FROM tasks").fetchone())
    assert CheckTask.model_validate(task_row) == task
    finding_row = dict(
        conn.execute("SELECT * FROM findings WHERE status='OPEN'").fetchone()
    )
    finding_row.pop("id")
    assert Finding.model_validate(finding_row) == open_finding


@pytest.mark.parametrize(
    "mutate",
    [
        dict(check_class="bogus-class"),
        dict(status="LOST"),
        dict(task_id=""),
        dict(task_id="   "),
    ],
)
def test_task_constraints_enforced(mutate):
    conn = seeded_conn()
    row = to_db_row(make_task())
    row.update(mutate)
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "tasks", row)


@pytest.mark.parametrize(
    "mutate",
    [
        dict(run_kind="prod"),
        dict(status="DONE"),
        dict(tasks_created=-1),
        dict(findings_new=-1),
        dict(tasks_created=3, tasks_terminal=4),
        dict(status="COMPLETED", tasks_created=6, tasks_terminal=5),
        dict(status="RUNNING"),
        dict(status="COMPLETED", finished_at_utc=None),
        dict(started_at_utc="2026-08-04 12:00:00"),
        dict(started_at_utc="2026-08-04T12:00:00Z"),
        dict(started_at_utc="2026-08-04T12:00:00.000+00:00"),
        dict(run_id=""),
        dict(run_id="  padded  "),
    ],
)
def test_run_constraints_enforced(mutate):
    conn = load_ledger()
    row = to_db_row(make_run(run_id="run-x"))
    row.update(mutate)
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "runs", row)


@pytest.mark.parametrize(
    "mutate",
    [
        dict(fingerprint="a" * 63),
        dict(fingerprint=("a" * 63) + "A"),
        dict(content_hash="z" * 64),
        dict(check_class="bogus-class"),
        dict(status="CLOSED"),
        dict(detail=""),
        dict(surface="   "),
        dict(first_seen_utc="2026-08-04 12:00:00"),
        dict(resolved_at_utc="2026-08-04T15:00:00+00:00"),
        dict(status="RESOLVED"),
        dict(first_seen_utc="2026-08-04T13:00:01+00:00"),
    ],
)
def test_finding_constraints_enforced(mutate):
    conn = seeded_conn()
    row = to_db_row(make_finding())
    row.update(mutate)
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "findings", row)


def test_foreign_keys_enforced():
    conn = seeded_conn()
    task_row = to_db_row(make_task(run_id="run-unknown"))
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "tasks", task_row)
    finding_row = to_db_row(make_finding(first_seen_run_id="run-unknown"))
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "findings", finding_row)


def test_second_open_row_same_fingerprint_rejected():
    conn = seeded_conn()
    insert(conn, "findings", to_db_row(make_finding()))
    duplicate = to_db_row(make_finding(detail="the same defect, seen again"))
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "findings", duplicate)


def test_fingerprint_recurs_after_resolution():
    conn = seeded_conn()
    insert(conn, "findings", to_db_row(make_resolved_finding()))
    insert(conn, "findings", to_db_row(make_finding()))
    fingerprints = [
        row[0] for row in conn.execute("SELECT fingerprint FROM findings").fetchall()
    ]
    assert fingerprints[0] == fingerprints[1]


@pytest.mark.parametrize("table", ["runs", "tasks", "findings"])
def test_delete_aborts_on_every_table(table):
    conn = seeded_conn()
    insert(conn, "tasks", to_db_row(make_task()))
    insert(conn, "findings", to_db_row(make_finding()))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"DELETE FROM {table}")


def test_lifecycle_operation_a_advance_open_finding():
    conn = seeded_conn()
    insert(conn, "findings", to_db_row(make_finding()))
    conn.execute(
        "UPDATE findings SET last_seen_utc = ?, last_seen_run_id = ?",
        (serialize_db_datetime(T2), "run-003"),
    )
    row = conn.execute(
        "SELECT last_seen_utc, last_seen_run_id, status FROM findings"
    ).fetchone()
    assert row == (serialize_db_datetime(T2), "run-003", "OPEN")


def test_lifecycle_operation_b_resolve_open_finding():
    conn = seeded_conn()
    insert(conn, "findings", to_db_row(make_finding()))
    conn.execute(
        "UPDATE findings SET status = 'RESOLVED', resolved_at_utc = ?, "
        "resolved_run_id = ?",
        (serialize_db_datetime(T2), "run-003"),
    )
    row = conn.execute(
        "SELECT status, resolved_at_utc, resolved_run_id FROM findings"
    ).fetchone()
    assert row == ("RESOLVED", serialize_db_datetime(T2), "run-003")


@pytest.mark.parametrize(
    "statement,params",
    [
        # last_seen moving backwards on an OPEN row
        ("UPDATE findings SET last_seen_utc = ?", ("2026-08-04T12:30:00+00:00",)),
        # combined advance + resolve in one update
        (
            "UPDATE findings SET status='RESOLVED', resolved_at_utc=?, "
            "resolved_run_id='run-003', last_seen_utc=?",
            ("2026-08-04T15:00:00+00:00", "2026-08-04T14:00:00+00:00"),
        ),
        # identity/immutable field mutations on an OPEN row
        ("UPDATE findings SET location = 'README.md:99'", ()),
        ("UPDATE findings SET detail = 'rewritten detail'", ()),
        ("UPDATE findings SET surface = 'synthetic-02/README.md'", ()),
        ("UPDATE findings SET check_class = 'number-mismatch'", ()),
        ("UPDATE findings SET first_seen_utc = '2026-08-04T11:00:00+00:00'", ()),
        ("UPDATE findings SET first_seen_run_id = 'run-002'", ()),
    ],
)
def test_lifecycle_trigger_blocks_prohibited_updates(statement, params):
    conn = seeded_conn()
    insert(conn, "findings", to_db_row(make_finding()))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(statement, params)


def test_lifecycle_trigger_blocks_updates_to_resolved_rows():
    conn = seeded_conn()
    insert(conn, "findings", to_db_row(make_resolved_finding()))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE findings SET status='OPEN', resolved_at_utc=NULL, "
            "resolved_run_id=NULL"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE findings SET last_seen_run_id='run-001'")

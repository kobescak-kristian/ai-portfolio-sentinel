-- Ledger schema v1 (BLUEPRINT §3, §6 P1; ADR 0004). DDL only — no
-- database access layer exists at Phase 1. Single audit trail for
-- runs, tasks and findings (SPEC §1).
--
-- Cost telemetry remains in the frozen Phase 0 JSONL ledger. Unifying
-- it with this SQLite ledger would alter a frozen contract and
-- requires a separate ADR.
--
-- Conventions, mirrored from contracts/schemas.py:
--   * Datetime TEXT values use exactly YYYY-MM-DDTHH:MM:SS+00:00
--     (length 25, whole seconds) — the output of
--     serialize_db_datetime(); SQLite's default datetime adapters are
--     never used. Pydantic remains responsible for semantic validity.
--   * Hashes are exactly 64 lowercase hexadecimal characters.
--   * Identifiers are stripped and non-empty.
--   * Consumers MUST run: PRAGMA foreign_keys = ON;
--   * Ledger rows are never deleted (delete-abort triggers below).
--   * findings.id is the single deliberate persistence-layer extra
--     field: a surrogate key, because a fingerprint may recur after
--     resolution (new row, same fingerprint).

CREATE TABLE IF NOT EXISTS runs (
    -- NOT NULL is explicit: a bare TEXT PRIMARY KEY still admits NULL
    -- in SQLite.
    run_id             TEXT NOT NULL PRIMARY KEY
                       CHECK (run_id = trim(run_id) AND length(run_id) > 0),
    schema_version     INTEGER NOT NULL CHECK (schema_version = 1),
    run_kind           TEXT NOT NULL CHECK (run_kind IN ('dev', 'eval', 'live')),
    status             TEXT NOT NULL
                       CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
    started_at_utc     TEXT NOT NULL CHECK (
                           length(started_at_utc) = 25
                           AND started_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                       ),
    finished_at_utc    TEXT CHECK (
                           finished_at_utc IS NULL
                           OR (
                               length(finished_at_utc) = 25
                               AND finished_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                           )
                       ),
    tasks_created      INTEGER NOT NULL CHECK (tasks_created >= 0),
    tasks_terminal     INTEGER NOT NULL CHECK (tasks_terminal >= 0),
    findings_new       INTEGER NOT NULL CHECK (findings_new >= 0),
    findings_still_open INTEGER NOT NULL CHECK (findings_still_open >= 0),
    findings_resolved  INTEGER NOT NULL CHECK (findings_resolved >= 0),
    CHECK (tasks_terminal <= tasks_created),
    CHECK ((status = 'RUNNING') = (finished_at_utc IS NULL)),
    CHECK (status <> 'COMPLETED' OR tasks_terminal = tasks_created),
    CHECK (finished_at_utc IS NULL OR finished_at_utc >= started_at_utc)
);

CREATE TABLE IF NOT EXISTS tasks (
    schema_version     INTEGER NOT NULL CHECK (schema_version = 1),
    task_id            TEXT NOT NULL
                       CHECK (task_id = trim(task_id) AND length(task_id) > 0),
    run_id             TEXT NOT NULL REFERENCES runs(run_id)
                       CHECK (run_id = trim(run_id) AND length(run_id) > 0),
    surface            TEXT NOT NULL
                       CHECK (surface = trim(surface) AND length(surface) > 0),
    check_class        TEXT NOT NULL CHECK (check_class IN (
                           'broken-link', 'number-mismatch',
                           'stale-STATE-marker', 'missing-required-file',
                           'missing-synthetic-label', 'readme-structure'
                       )),
    created_at_utc     TEXT NOT NULL CHECK (
                           length(created_at_utc) = 25
                           AND created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                       ),
    status             TEXT NOT NULL CHECK (status IN (
                           'PENDING', 'IN_PROGRESS', 'DONE', 'FAILED',
                           'DEAD_LETTER'
                       )),
    PRIMARY KEY (run_id, task_id)
);

CREATE TABLE IF NOT EXISTS findings (
    id                 INTEGER PRIMARY KEY,
    schema_version     INTEGER NOT NULL CHECK (schema_version = 1),
    fingerprint        TEXT NOT NULL CHECK (
                           length(fingerprint) = 64
                           AND fingerprint NOT GLOB '*[^0-9a-f]*'
                       ),
    surface            TEXT NOT NULL
                       CHECK (surface = trim(surface) AND length(surface) > 0),
    check_class        TEXT NOT NULL CHECK (check_class IN (
                           'broken-link', 'number-mismatch',
                           'stale-STATE-marker', 'missing-required-file',
                           'missing-synthetic-label', 'readme-structure'
                       )),
    content_hash       TEXT NOT NULL CHECK (
                           length(content_hash) = 64
                           AND content_hash NOT GLOB '*[^0-9a-f]*'
                       ),
    location           TEXT NOT NULL
                       CHECK (location = trim(location) AND length(location) > 0),
    detail             TEXT NOT NULL
                       CHECK (detail = trim(detail) AND length(detail) > 0),
    status             TEXT NOT NULL CHECK (status IN ('OPEN', 'RESOLVED')),
    first_seen_utc     TEXT NOT NULL CHECK (
                           length(first_seen_utc) = 25
                           AND first_seen_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                       ),
    last_seen_utc      TEXT NOT NULL CHECK (
                           length(last_seen_utc) = 25
                           AND last_seen_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                       ),
    resolved_at_utc    TEXT CHECK (
                           resolved_at_utc IS NULL
                           OR (
                               length(resolved_at_utc) = 25
                               AND resolved_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                           )
                       ),
    first_seen_run_id  TEXT NOT NULL REFERENCES runs(run_id)
                       CHECK (first_seen_run_id = trim(first_seen_run_id)
                              AND length(first_seen_run_id) > 0),
    last_seen_run_id   TEXT NOT NULL REFERENCES runs(run_id)
                       CHECK (last_seen_run_id = trim(last_seen_run_id)
                              AND length(last_seen_run_id) > 0),
    resolved_run_id    TEXT REFERENCES runs(run_id)
                       CHECK (resolved_run_id IS NULL
                              OR (resolved_run_id = trim(resolved_run_id)
                                  AND length(resolved_run_id) > 0)),
    CHECK (last_seen_utc >= first_seen_utc),
    CHECK (resolved_at_utc IS NULL OR resolved_at_utc >= last_seen_utc),
    CHECK ((status = 'OPEN') = (resolved_at_utc IS NULL AND resolved_run_id IS NULL)),
    CHECK ((status = 'RESOLVED') = (resolved_at_utc IS NOT NULL AND resolved_run_id IS NOT NULL))
);

-- Dedup invariant (SPEC §1 step 4): at most one OPEN row per
-- fingerprint; recurrence after resolution inserts a new row.
CREATE UNIQUE INDEX IF NOT EXISTS idx_findings_open_fingerprint
    ON findings (fingerprint) WHERE status = 'OPEN';

-- Ledger rows are never deleted.
CREATE TRIGGER IF NOT EXISTS runs_never_deleted
BEFORE DELETE ON runs
BEGIN
    SELECT RAISE(ABORT, 'ledger rows are never deleted');
END;

CREATE TRIGGER IF NOT EXISTS tasks_never_deleted
BEFORE DELETE ON tasks
BEGIN
    SELECT RAISE(ABORT, 'ledger rows are never deleted');
END;

CREATE TRIGGER IF NOT EXISTS findings_never_deleted
BEFORE DELETE ON findings
BEGIN
    SELECT RAISE(ABORT, 'ledger rows are never deleted');
END;

-- Finding lifecycle guard: every column is immutable except under the
-- two permitted operations —
--   A. advance an OPEN finding: only last_seen_utc (monotonic) and
--      last_seen_run_id may change; status stays OPEN.
--   B. resolve an OPEN finding: only status (OPEN -> RESOLVED),
--      resolved_at_utc and resolved_run_id may change.
-- Everything else — including RESOLVED -> OPEN, any update to a
-- resolved row, and any identity-field mutation — aborts. Recurrence
-- after resolution is a new INSERT with the same fingerprint.
CREATE TRIGGER IF NOT EXISTS findings_lifecycle_guard
BEFORE UPDATE ON findings
FOR EACH ROW
WHEN NOT (
    (
        OLD.status = 'OPEN' AND NEW.status = 'OPEN'
        AND NEW.id = OLD.id
        AND NEW.schema_version = OLD.schema_version
        AND NEW.fingerprint = OLD.fingerprint
        AND NEW.surface = OLD.surface
        AND NEW.check_class = OLD.check_class
        AND NEW.content_hash = OLD.content_hash
        AND NEW.location = OLD.location
        AND NEW.detail = OLD.detail
        AND NEW.first_seen_utc = OLD.first_seen_utc
        AND NEW.first_seen_run_id = OLD.first_seen_run_id
        AND NEW.resolved_at_utc IS NULL
        AND NEW.resolved_run_id IS NULL
        AND NEW.last_seen_utc >= OLD.last_seen_utc
    )
    OR
    (
        OLD.status = 'OPEN' AND NEW.status = 'RESOLVED'
        AND NEW.id = OLD.id
        AND NEW.schema_version = OLD.schema_version
        AND NEW.fingerprint = OLD.fingerprint
        AND NEW.surface = OLD.surface
        AND NEW.check_class = OLD.check_class
        AND NEW.content_hash = OLD.content_hash
        AND NEW.location = OLD.location
        AND NEW.detail = OLD.detail
        AND NEW.first_seen_utc = OLD.first_seen_utc
        AND NEW.first_seen_run_id = OLD.first_seen_run_id
        AND NEW.last_seen_utc = OLD.last_seen_utc
        AND NEW.last_seen_run_id = OLD.last_seen_run_id
        AND NEW.resolved_at_utc IS NOT NULL
        AND NEW.resolved_run_id IS NOT NULL
    )
)
BEGIN
    SELECT RAISE(ABORT,
        'finding update violates lifecycle: only OPEN last_seen advance or OPEN->RESOLVED is permitted');
END;

-- ---------------------------------------------------------------------
-- Phase 3 addition (BLUEPRINT §6 P3; adr/0003 P3; dispatch q77-p3-a).
-- Additive to the frozen v1 schema above — no existing table's
-- definition changes. Main-ledger audit trail for the caged checker
-- agent's judgment calls: one row per attempted call, written RESERVED
-- before the SDK is invoked and updated to a terminal state after —
-- never a second, separate audit database. All statements above and
-- below use IF NOT EXISTS so this file can be safely re-applied
-- against a pre-Phase-3 database (initialize_schema calls it
-- unconditionally on every open_ledger, not just on first creation).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_calls (
    id                   INTEGER PRIMARY KEY,
    run_id               TEXT NOT NULL REFERENCES runs(run_id)
                         CHECK (run_id = trim(run_id) AND length(run_id) > 0),
    task_key             TEXT NOT NULL
                         CHECK (task_key = trim(task_key) AND length(task_key) > 0),
    surface              TEXT NOT NULL
                         CHECK (surface = trim(surface) AND length(surface) > 0),
    check_class          TEXT NOT NULL CHECK (check_class IN (
                             'stale-STATE-marker', 'missing-synthetic-label'
                         )),
    model                TEXT NOT NULL
                         CHECK (model = trim(model) AND length(model) > 0),
    auth_mode            TEXT NOT NULL
                         CHECK (auth_mode = trim(auth_mode) AND length(auth_mode) > 0),
    state                TEXT NOT NULL CHECK (state IN (
                             'RESERVED', 'COMPLETED', 'FAILED', 'REJECTED', 'EXHAUSTED'
                         )),
    started_at_utc       TEXT NOT NULL CHECK (
                             length(started_at_utc) = 25
                             AND started_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                         ),
    finished_at_utc      TEXT CHECK (
                             finished_at_utc IS NULL
                             OR (
                                 length(finished_at_utc) = 25
                                 AND finished_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                             )
                         ),
    -- Integer micro-euros throughout, mirroring CostRow.cost_eur_micros.
    reserved_eur_micros  INTEGER NOT NULL CHECK (reserved_eur_micros >= 0),
    charged_eur_micros   INTEGER CHECK (charged_eur_micros IS NULL OR charged_eur_micros >= 0),
    sdk_turns            INTEGER CHECK (sdk_turns IS NULL OR sdk_turns >= 0),
    sdk_is_error         INTEGER CHECK (sdk_is_error IS NULL OR sdk_is_error IN (0, 1)),
    sdk_subtype          TEXT,
    input_tokens         INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens        INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    -- The SDK's own client-side estimate (total_cost_usd), stored as
    -- exact decimal text — never a binary float. Development insight
    -- only, per the SDK's own documented accuracy caveat; never the
    -- basis of an authoritative-billing claim (DATA_CONTRACT.md).
    usd_cost_estimate    TEXT,
    fx_source            TEXT NOT NULL
                         CHECK (fx_source = trim(fx_source) AND length(fx_source) > 0),
    fx_rate_date         TEXT NOT NULL
                         CHECK (fx_rate_date = trim(fx_rate_date) AND length(fx_rate_date) > 0),
    fx_retrieved_at_utc  TEXT NOT NULL CHECK (
                             length(fx_retrieved_at_utc) = 25
                             AND fx_retrieved_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                         ),
    fx_rate_decimal      TEXT NOT NULL
                         CHECK (fx_rate_decimal = trim(fx_rate_decimal) AND length(fx_rate_decimal) > 0),
    tool_attempts        INTEGER NOT NULL DEFAULT 0 CHECK (tool_attempts >= 0),
    accepted             INTEGER NOT NULL DEFAULT 0 CHECK (accepted IN (0, 1)),
    rejection_reason     TEXT,
    CHECK ((state = 'RESERVED') = (finished_at_utc IS NULL)),
    CHECK (finished_at_utc IS NULL OR finished_at_utc >= started_at_utc)
);

CREATE INDEX IF NOT EXISTS idx_agent_calls_run_id ON agent_calls (run_id);

-- Ledger rows are never deleted (same discipline as runs/tasks/findings).
CREATE TRIGGER IF NOT EXISTS agent_calls_never_deleted
BEFORE DELETE ON agent_calls
BEGIN
    SELECT RAISE(ABORT, 'ledger rows are never deleted');
END;

"""Model-free tests for the frozen Phase-4 technical gate and the
ITERATION_LOG derived-evidence surface (dispatch q77-p4-gate-a).

**Model-free by construction.** Nothing here constructs an SDK client,
resolves a live FX rate, opens a socket or reaches a provider. The one
budget-propagation assertion uses a deterministic injected FX object and
proves only that a reduced allowance reaches ``RunBudgetCoordinator``.

Two scoping notes, because they are what makes these tests evidence rather
than decoration:

* The gate is exercised ONCE end to end, into ``tmp_path`` outputs, by a
  module-scoped fixture. That invocation is an IMPLEMENTATION TEST. It is
  NOT the designated ``q77-p4-gate-exec-a`` execution, and it deliberately
  writes nowhere near the repository root — the last test in this file
  asserts that the official ``ITERATION_LOG.md`` and
  ``artifacts/phase4_loop_gate.json`` were not created.
* Dangerous-looking canary values are BUILT AT RUNTIME from fragments, so
  the tracked source of this file contains no complete secret or absolute
  path that a publication control could reasonably flag. The runtime
  strings are genuinely unsafe-shaped and the assertions are not weakened;
  no allowlist exemption exists for them.

``scripts/run_phase4_loop_gate.py`` is loaded through ``importlib`` rather
than a plain ``from scripts...`` import, matching
``tests/test_phase3_gate_runner.py``: ``scripts`` is not a first-party root
for ``tests/test_dependency_surface.py``'s scan, and a literal import would
be read as an unpinned third-party dependency.
"""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner import breakers
from runner.iteration_log import (
    PHASE4_FAILURE_ALERT,
    SEEDED_FAULT,
    SYNTHETIC,
    IterationLogError,
    IterationMachineRow,
    SectionMeta,
    append_section,
    is_section_complete,
    iteration_log_sha256,
    parse_sections,
    render_section,
)
from runner.state import open_loop_state

gate = importlib.import_module("scripts.run_phase4_loop_gate")

#: A clearly synthetic 40-hex value. The gate only ever validates the shape
#: of this input; it never resolves it against a repository.
SOURCE_SHA = "0123456789abcdef0123456789abcdef01234567"

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- runtime-built unsafe canaries -----------------------------------------
#
# Assembled from fragments on purpose (see the module docstring). Each is a
# genuinely unsafe SHAPE: a drive-rooted Windows path, a POSIX absolute
# path, the two GitHub token prefixes, and a bearer credential.


def _windows_path() -> str:
    return "C:" + chr(92) + "Users" + chr(92) + "example" + chr(92) + "secrets.txt"


def _posix_path() -> str:
    return "/" + "home/example/.config/credentials"


def _github_token() -> str:
    return "gh" + "p_" + ("A" * 24)


def _github_pat() -> str:
    return "github" + "_pat_" + ("B" * 24)


def _bearer_token() -> str:
    return "Bear" + "er " + ("C" * 24)


def unsafe_canaries() -> list[str]:
    return [
        _windows_path(),
        _posix_path(),
        _github_token(),
        _github_pat(),
        _bearer_token(),
    ]


# --- ITERATION_LOG fixtures -------------------------------------------------


def make_meta(**overrides):
    base = dict(
        loop_id="loop-unit",
        gate_leg="LEG1",
        gate_case="unit-case",
        classification=SYNTHETIC,
        source_sha=SOURCE_SHA,
        max_iterations=2,
        loop_budget_eur_micros=750_000,
        failure_threshold=3,
        stop_reason="COMPLETED_ITERATION_CAP",
        exit_code=0,
        iterations_recorded=1,
        alert_label=None,
    )
    base.update(overrides)
    return SectionMeta(**base)


def make_row(**overrides):
    base = dict(
        iteration_index=0,
        planned_run_id="r-unit-000",
        iteration_state="FINALIZED",
        bound_run_id="r-unit-000",
        run_status="COMPLETED",
        tasks_created=80,
        tasks_terminal=80,
        findings_new=40,
        findings_still_open=0,
        findings_resolved=0,
        iteration_cost_eur_micros=0,
        cumulative_cost_eur_micros=0,
        effective_allowance_eur_micros=750_000,
        consecutive_failures_after=0,
        breaker=None,
        started_at_utc="2026-01-01T00:00:00+00:00",
        finished_at_utc="2026-01-01T00:00:01+00:00",
    )
    base.update(overrides)
    return IterationMachineRow(**base)


# --- ITERATION_LOG: rendering ----------------------------------------------


def test_render_is_a_pure_deterministic_function_of_its_input():
    meta, rows = make_meta(), [make_row()]
    assert render_section(meta, rows) == render_section(meta, rows)


def test_section_opens_and_closes_with_matching_markers_on_the_final_line():
    text = render_section(make_meta(), [make_row()])
    section_id = make_meta().section_id
    assert text.startswith(f"<!-- sentinel:phase4-loop {section_id} -->")
    assert text.rstrip("\n").splitlines()[-1] == (
        f"<!-- sentinel:phase4-loop-end {section_id} -->"
    )


def test_section_id_derives_from_loop_and_case_not_display_text():
    assert make_meta(loop_id="loop-a", gate_case="case-x").section_id == "loop-a::case-x"
    # Two sections whose headings would read alike still get distinct ids.
    assert make_meta(gate_case="case-x").section_id != make_meta(gate_case="case-y").section_id


def test_iterations_recorded_must_match_the_number_of_machine_rows():
    with pytest.raises(IterationLogError):
        render_section(make_meta(iterations_recorded=2), [make_row()])


def test_alert_label_appears_only_where_intended():
    plain = render_section(make_meta(), [make_row()])
    assert PHASE4_FAILURE_ALERT not in plain

    alerted = render_section(
        make_meta(
            loop_id="loop-alert",
            gate_case="alert-case",
            stop_reason="CONSECUTIVE_FAILURE_BREAKER_TRIPPED",
            exit_code=1,
            alert_label=PHASE4_FAILURE_ALERT,
        ),
        [make_row(run_status="FAILED", consecutive_failures_after=3)],
    )
    assert PHASE4_FAILURE_ALERT in alerted


def test_the_header_states_that_the_file_is_not_authoritative(tmp_path):
    path = tmp_path / "ITERATION_LOG.md"
    append_section(path, make_meta().section_id, render_section(make_meta(), [make_row()]))
    header = path.read_text(encoding="utf-8")
    assert "DERIVED PUBLIC EVIDENCE" in header
    assert "NOT authoritative loop state" in header
    assert "durable SQLite ledger" in header.replace("\n", " ")


def test_no_numerical_fact_appears_only_in_prose():
    """Every number in the one narrative paragraph is also a metadata
    field, which is what the generated-prose design guarantees."""
    meta = make_meta(max_iterations=7, iterations_recorded=1, failure_threshold=3)
    text = render_section(meta, [make_row()])
    prose = [
        line
        for line in text.splitlines()
        if line and not line.startswith(("<!--", "#", "```", "{"))
    ]
    assert len(prose) == 1
    metadata = meta.as_metadata_dict()
    for number in ("7", "750000", "3", "1", "0"):
        if number in prose[0]:
            assert any(str(v) == number for v in metadata.values()), number


# --- ITERATION_LOG: append semantics ----------------------------------------


def test_append_then_reappend_is_an_idempotent_no_op(tmp_path):
    path = tmp_path / "ITERATION_LOG.md"
    meta, rows = make_meta(), [make_row()]
    text = render_section(meta, rows)

    assert append_section(path, meta.section_id, text) is True
    first = path.read_bytes()
    assert append_section(path, meta.section_id, text) is False
    assert path.read_bytes() == first
    assert is_section_complete(path, meta.section_id) is True


def test_a_crash_truncated_trailing_section_is_repaired(tmp_path):
    path = tmp_path / "ITERATION_LOG.md"
    meta, rows = make_meta(), [make_row()]
    text = render_section(meta, rows)

    # A crash mid-render: the opening marker landed, the closing one did not.
    fragment = text[: len(text) // 2]
    append_section(path, meta.section_id, fragment)
    assert is_section_complete(path, meta.section_id) is False

    assert append_section(path, meta.section_id, text) is True
    assert is_section_complete(path, meta.section_id) is True
    # Exactly one open and one close marker: the fragment was truncated, not
    # left behind alongside the repaired section.
    body = path.read_text(encoding="utf-8")
    assert body.count(f"<!-- sentinel:phase4-loop {meta.section_id} -->") == 1
    assert body.count(f"<!-- sentinel:phase4-loop-end {meta.section_id} -->") == 1
    assert len(parse_sections(body)) == 1


def test_an_earlier_complete_section_is_byte_preserved_by_a_later_repair(tmp_path):
    path = tmp_path / "ITERATION_LOG.md"
    first_meta = make_meta(loop_id="loop-first", gate_case="first-case")
    first_text = render_section(first_meta, [make_row()])
    append_section(path, first_meta.section_id, first_text)
    before = path.read_bytes()

    second_meta = make_meta(loop_id="loop-second", gate_case="second-case")
    second_text = render_section(second_meta, [make_row(planned_run_id="r-unit-001",
                                                        bound_run_id="r-unit-001")])
    append_section(path, second_meta.section_id, second_text[: len(second_text) // 2])
    append_section(path, second_meta.section_id, second_text)

    after = path.read_bytes()
    assert after.startswith(before)  # nothing before the repaired fragment moved
    assert len(parse_sections(path.read_text(encoding="utf-8"))) == 2


def test_iteration_log_sha256_is_taken_over_the_exact_bytes(tmp_path):
    import hashlib

    path = tmp_path / "ITERATION_LOG.md"
    append_section(path, make_meta().section_id, render_section(make_meta(), [make_row()]))
    assert iteration_log_sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest()


# --- ITERATION_LOG: reparsing written bytes ---------------------------------


def test_machine_rows_parse_back_out_of_written_bytes(tmp_path):
    path = tmp_path / "ITERATION_LOG.md"
    meta = make_meta(iterations_recorded=2)
    rows = [make_row(), make_row(iteration_index=1, planned_run_id="r-unit-001",
                                 bound_run_id="r-unit-001", findings_new=0,
                                 findings_still_open=40)]
    append_section(path, meta.section_id, render_section(meta, rows))

    parsed = parse_sections(path.read_text(encoding="utf-8"))
    section = parsed[meta.section_id]
    assert section.metadata == meta.as_metadata_dict()
    assert section.rows == [row.as_machine_dict() for row in rows]


def test_parse_refuses_a_machine_row_with_an_unknown_field(tmp_path):
    path = tmp_path / "ITERATION_LOG.md"
    meta = make_meta()
    append_section(path, meta.section_id, render_section(meta, [make_row()]))
    text = path.read_text(encoding="utf-8").replace(
        '"iteration_index":0', '"iteration_index":0,"smuggled":1'
    )
    with pytest.raises(IterationLogError):
        parse_sections(text)


def test_parse_refuses_a_machine_row_with_a_missing_field(tmp_path):
    path = tmp_path / "ITERATION_LOG.md"
    meta = make_meta()
    append_section(path, meta.section_id, render_section(meta, [make_row()]))
    text = path.read_text(encoding="utf-8").replace('"breaker":null,', "")
    with pytest.raises(IterationLogError):
        parse_sections(text)


def test_parse_refuses_a_row_whose_field_fails_its_validator(tmp_path):
    path = tmp_path / "ITERATION_LOG.md"
    meta = make_meta()
    append_section(path, meta.section_id, render_section(meta, [make_row()]))
    text = path.read_text(encoding="utf-8").replace(
        '"run_status":"COMPLETED"', '"run_status":"MAYBE"'
    )
    with pytest.raises(IterationLogError):
        parse_sections(text)


def test_parse_refuses_a_section_declaring_the_wrong_row_count(tmp_path):
    path = tmp_path / "ITERATION_LOG.md"
    meta = make_meta()
    append_section(path, meta.section_id, render_section(meta, [make_row()]))
    text = path.read_text(encoding="utf-8").replace(
        '"iterations_recorded":1', '"iterations_recorded":2'
    )
    with pytest.raises(IterationLogError):
        parse_sections(text)


# --- ITERATION_LOG: the closed input schema is the hygiene mechanism --------


@pytest.mark.parametrize("field_name", ["loop_id", "gate_leg", "gate_case"])
def test_unsafe_values_are_refused_by_the_identifier_validator(field_name):
    for canary in unsafe_canaries():
        with pytest.raises(IterationLogError):
            make_meta(**{field_name: canary}).as_metadata_dict()


@pytest.mark.parametrize("field_name", ["planned_run_id", "bound_run_id"])
def test_unsafe_run_ids_are_refused(field_name):
    for canary in unsafe_canaries():
        with pytest.raises(IterationLogError):
            make_row(**{field_name: canary}).as_machine_dict()


def test_a_temporary_gate_root_value_cannot_become_an_identifier(tmp_path):
    with pytest.raises(IterationLogError):
        make_meta(loop_id=str(tmp_path)).as_metadata_dict()


def test_the_closed_schema_refuses_bad_enums_counters_timestamps_and_shas():
    with pytest.raises(IterationLogError):
        make_meta(classification="REAL").as_metadata_dict()
    with pytest.raises(IterationLogError):
        make_meta(stop_reason="SOMETHING_ELSE").as_metadata_dict()
    with pytest.raises(IterationLogError):
        make_meta(alert_label="PAGERDUTY").as_metadata_dict()
    with pytest.raises(IterationLogError):
        make_meta(source_sha="ABC").as_metadata_dict()
    with pytest.raises(IterationLogError):
        make_meta(source_sha=SOURCE_SHA.upper()).as_metadata_dict()
    with pytest.raises(IterationLogError):
        make_meta(max_iterations=-1).as_metadata_dict()
    with pytest.raises(IterationLogError):
        make_row(tasks_created=True).as_machine_dict()
    with pytest.raises(IterationLogError):
        make_row(iteration_state="RUNNING").as_machine_dict()
    with pytest.raises(IterationLogError):
        make_row(started_at_utc="2026-01-01 00:00:00").as_machine_dict()


def test_nullable_fields_are_permitted_where_there_is_no_executed_value():
    """An iteration still in INTENT genuinely has no bound run, no status
    and no counts. Values are not invented to make rows look uniform."""
    row = make_row(
        iteration_state="INTENT",
        bound_run_id=None,
        run_status=None,
        tasks_created=None,
        tasks_terminal=None,
        findings_new=None,
        findings_still_open=None,
        findings_resolved=None,
        effective_allowance_eur_micros=None,
        finished_at_utc=None,
    ).as_machine_dict()
    assert row["bound_run_id"] is None and row["run_status"] is None


def test_the_closed_schema_refuses_a_bad_exit_code():
    for bad in (-1, True, "0", None):
        with pytest.raises(IterationLogError):
            make_meta(exit_code=bad).as_metadata_dict()


def test_is_section_complete_is_false_for_a_file_that_does_not_exist(tmp_path):
    assert is_section_complete(tmp_path / "absent.md", "loop-x::case-x") is False


def test_parse_refuses_a_section_with_no_closing_marker(tmp_path):
    meta = make_meta()
    text = render_section(meta, [make_row()])
    truncated = text[: text.index("<!-- sentinel:phase4-loop-end")]
    with pytest.raises(IterationLogError):
        parse_sections(truncated)


def test_parse_refuses_a_section_whose_machine_blocks_are_missing():
    meta = make_meta()
    section_id = meta.section_id
    text = (
        f"<!-- sentinel:phase4-loop {section_id} -->\n"
        "## no machine blocks at all\n"
        f"<!-- sentinel:phase4-loop-end {section_id} -->\n"
    )
    with pytest.raises(IterationLogError):
        parse_sections(text)


def test_parse_refuses_a_metadata_block_that_is_not_exactly_one_line():
    meta = make_meta()
    text = render_section(meta, [make_row()])
    doubled = text.replace(
        '{"alert_label"', '{"alert_label":null}\n{"alert_label"', 1
    )
    with pytest.raises(IterationLogError):
        parse_sections(doubled)


def test_parse_refuses_a_machine_line_that_is_not_a_json_object():
    meta = make_meta()
    text = render_section(meta, [make_row()])
    start = text.index('{"alert_label"')
    end = text.index("\n", start)
    mangled = text[:start] + "[1, 2, 3]" + text[end:]
    with pytest.raises(IterationLogError):
        parse_sections(mangled)


def test_parse_refuses_the_same_section_appearing_twice():
    meta = make_meta()
    text = render_section(meta, [make_row()])
    with pytest.raises(IterationLogError):
        parse_sections(text + text)


def test_the_module_takes_no_caller_supplied_free_prose():
    """Public hygiene is a property of the input schema. If a narrative
    parameter is ever added, this test is where that shows up."""
    from dataclasses import fields as dataclass_fields

    names = {f.name for f in dataclass_fields(SectionMeta)} | {
        f.name for f in dataclass_fields(IterationMachineRow)
    }
    assert not names & {"narrative", "note", "notes", "detail", "message", "comment"}


# --- the frozen gate contract ------------------------------------------------


def test_gate_schema_constants_are_frozen():
    assert gate.SCHEMA_VERSION == 1
    assert gate.GATE == "phase4_bounded_loop"
    assert gate.GATE_CONTRACT == "ADR-0010-section-7"
    assert gate.MODEL_CALLS == 0
    assert gate.PROVIDER_SPEND_EUR_MICROS == 0
    assert gate.LEG1_ITERATIONS == 10


def test_the_gates_local_literals_match_the_runners_frozen_values():
    """The gate restates ADR-0010's constants as local literals so it does
    not agree with the enforcement mechanism by construction. This test is
    what stops the two copies drifting apart silently."""
    assert gate.LOOP_CEILING_EUR_MICROS == breakers.LOOP_BUDGET_EUR_MICROS == 750_000
    assert gate.FAILURE_THRESHOLD == breakers.CONSECUTIVE_FAILURE_THRESHOLD == 3
    assert gate.PER_RUN_CAP_EUR_MICROS == 750_000
    assert gate.EXPECTED_EXIT_CODES == breakers.EXIT_CODES
    assert set(gate.EXPECTED_EXIT_CODES) == set(breakers.STOP_REASONS)
    assert gate.LEG1_ITERATIONS <= breakers.MAX_ITERATIONS


def test_the_predicate_set_is_closed_and_covers_every_leg():
    assert len(gate.PREDICATE_IDS) == len(set(gate.PREDICATE_IDS))
    covered = set()
    for ids in gate.LEG_PREDICATES.values():
        covered |= set(ids)
    covered |= {"ITERATION_LOG_MATCHES_DURABLE_STATE", "PUBLIC_OUTPUT_CLEAN"}
    assert covered == set(gate.PREDICATE_IDS)
    for required in (
        "LEG1_NORMAL_N10_ITERATION_COUNT",
        "LEG1_NORMAL_N10_CONTINUITY",
        "LEG2_749999_MIDLOOP_CONTINUES",
        "LEG2_REDUCED_ALLOWANCE_PROPAGATED",
        "LEG2_EXACT_CAP_MIDLOOP_REFUSED",
        "LEG2_OVERSHOOT_FULL_NOT_CLAMPED",
        "LEG2_TERMINAL_EXACT_CAP_NORMAL",
        "LEG2_TERMINAL_OVERSHOOT_TRIPS",
        "LEG3_TRIP_AT_THREE",
        "LEG3_FOUR_PART_ALERT",
        "LEG3_RESET_SEQUENCE",
        "LEG3_TERMINAL_STREAK_PRECEDENCE",
        "LEG4_TERMINAL_BEFORE_FINALIZE_ADOPTED",
        "LEG4_INTENT_BEFORE_RUN_REUSED",
        "LEG4_TERMINAL_OUTPUTS_RECONCILED",
        "ITERATION_LOG_MATCHES_DURABLE_STATE",
        "PUBLIC_OUTPUT_CLEAN",
    ):
        assert required in gate.PREDICATE_IDS


def test_an_unknown_predicate_is_refused():
    recorder = gate.PredicateRecorder()
    with pytest.raises(gate.GateContractError):
        recorder.record("LEG9_INVENTED_AT_RUN_TIME", True)


def test_a_duplicate_predicate_is_refused():
    recorder = gate.PredicateRecorder()
    recorder.record("LEG1_NORMAL_N10_EXIT_ZERO", True)
    with pytest.raises(gate.GateContractError):
        recorder.record("LEG1_NORMAL_N10_EXIT_ZERO", False)


def test_a_never_recorded_predicate_is_reported_as_failed_and_blocks_pass():
    recorder = gate.PredicateRecorder()
    for predicate_id in gate.PREDICATE_IDS[:-1]:
        recorder.record(predicate_id, True)
    rows = recorder.results()
    assert len(rows) == len(gate.PREDICATE_IDS)
    missing = [r for r in rows if r["id"] == gate.PREDICATE_IDS[-1]]
    assert missing[0]["result"] == "FAIL"
    assert recorder.overall() == "FAIL"


def test_overall_pass_requires_every_frozen_predicate_to_pass():
    recorder = gate.PredicateRecorder()
    for predicate_id in gate.PREDICATE_IDS:
        recorder.record(predicate_id, True)
    assert recorder.overall() == "PASS"

    recorder = gate.PredicateRecorder()
    for index, predicate_id in enumerate(gate.PREDICATE_IDS):
        recorder.record(predicate_id, index != 3)
    assert recorder.overall() == "FAIL"


def test_free_text_evidence_is_refused_outside_the_sanitized_detail_field():
    recorder = gate.PredicateRecorder()
    with pytest.raises(gate.GateContractError):
        recorder.record("LEG1_NORMAL_N10_EXIT_ZERO", True, note=_posix_path())


def test_a_detail_string_is_sanitized_before_it_is_stored():
    recorder = gate.PredicateRecorder()
    recorder.record("LEG1_NORMAL_N10_EXIT_ZERO", False, detail=_posix_path())
    stored = recorder.results()[gate.PREDICATE_IDS.index("LEG1_NORMAL_N10_EXIT_ZERO")]
    assert _posix_path() not in stored["detail"]


@pytest.mark.parametrize(
    "bad_sha",
    ["", "abc", SOURCE_SHA[:-1], SOURCE_SHA.upper(), "g" * 40, SOURCE_SHA + "0"],
)
def test_the_gate_refuses_a_source_sha_that_is_not_forty_lowercase_hex(bad_sha, tmp_path):
    with pytest.raises(gate.GateContractError):
        gate.run_gate(source_sha=bad_sha, iteration_log_path=tmp_path / "ITERATION_LOG.md")


# --- the frozen artifact schema ---------------------------------------------


def minimal_core(log_path: Path, **overrides):
    core = {
        "schema_version": gate.SCHEMA_VERSION,
        "gate": gate.GATE,
        "gate_contract": gate.GATE_CONTRACT,
        "source_sha": SOURCE_SHA,
        "model_calls": 0,
        "provider_spend_eur_micros": 0,
        "loop_budget_eur_micros": 750_000,
        "failure_threshold": 3,
        "legs": [{"leg": "LEG1", "cases": ["leg1-normal-n10"], "result": "PASS"}],
        "iteration_log_sha256": iteration_log_sha256(log_path),
        "predicate_results": [{"id": "LEG1_NORMAL_N10_EXIT_ZERO", "result": "PASS"}],
    }
    core.update(overrides)
    return core


@pytest.fixture
def written_log(tmp_path):
    path = tmp_path / "ITERATION_LOG.md"
    append_section(path, make_meta().section_id, render_section(make_meta(), [make_row()]))
    return path


def test_the_artifact_schema_is_closed(written_log):
    core = minimal_core(written_log)
    gate.validate_artifact(core, complete=False)
    with pytest.raises(gate.GateContractError):
        gate.validate_artifact({**core, "extra": 1}, complete=False)


@pytest.mark.parametrize(
    "override",
    [
        {"schema_version": 2},
        {"gate": "something_else"},
        {"gate_contract": "ADR-0010-section-8"},
        {"source_sha": "nope"},
        {"model_calls": 1},
        {"provider_spend_eur_micros": 1},
        {"loop_budget_eur_micros": 1_000_000},
        {"failure_threshold": 5},
        {"iteration_log_sha256": "short"},
    ],
)
def test_the_artifact_schema_refuses_a_tampered_frozen_field(written_log, override):
    with pytest.raises(gate.GateContractError):
        gate.validate_artifact(minimal_core(written_log, **override), complete=False)


def test_a_complete_artifact_must_carry_every_frozen_predicate(written_log):
    core = minimal_core(written_log)
    complete = {
        **core,
        "predicate_results": [
            {"id": p, "result": "PASS"} for p in gate.PREDICATE_IDS[:-1]
        ],
        "overall": "PASS",
    }
    with pytest.raises(gate.GateContractError):
        gate.validate_artifact(complete)


def test_a_complete_artifact_refuses_a_duplicated_predicate(written_log):
    rows = [{"id": p, "result": "PASS"} for p in gate.PREDICATE_IDS]
    rows.append({"id": gate.PREDICATE_IDS[0], "result": "PASS"})
    with pytest.raises(gate.GateContractError):
        gate.validate_artifact(
            {**minimal_core(written_log), "predicate_results": rows, "overall": "PASS"}
        )


def test_a_complete_artifact_refuses_an_overall_that_contradicts_its_predicates(written_log):
    rows = [{"id": p, "result": "PASS"} for p in gate.PREDICATE_IDS]
    rows[2] = {"id": gate.PREDICATE_IDS[2], "result": "FAIL"}
    with pytest.raises(gate.GateContractError):
        gate.validate_artifact(
            {**minimal_core(written_log), "predicate_results": rows, "overall": "PASS"}
        )


# --- public-output hygiene ---------------------------------------------------


def test_public_output_is_clean_for_a_well_formed_pair(written_log, tmp_path):
    ok, detail = gate.public_output_clean(
        written_log, minimal_core(written_log), tmp_path / "gate-root"
    )
    assert ok, detail


def test_public_output_hygiene_flags_the_temporary_gate_root(written_log, tmp_path):
    work_root = tmp_path / "p4gate-abcdef"
    work_root.mkdir()
    core = minimal_core(written_log)
    # A path that slipped past sanitization into a diagnostic.
    core["predicate_results"] = [
        {"id": "LEG1_NORMAL_N10_EXIT_ZERO", "result": "FAIL", "detail": str(work_root)}
    ]
    ok, detail = gate.public_output_clean(written_log, core, work_root)
    assert ok is False
    assert "gate-root" in detail or "temporary" in detail


def test_public_output_hygiene_flags_an_unsanitized_diagnostic(written_log, tmp_path):
    for canary in unsafe_canaries():
        core = minimal_core(written_log)
        core["predicate_results"] = [
            {"id": "LEG1_NORMAL_N10_EXIT_ZERO", "result": "FAIL", "detail": canary}
        ]
        ok, _detail = gate.public_output_clean(written_log, core, tmp_path / "gate-root")
        assert ok is False, canary


def test_public_output_hygiene_flags_a_traceback_block(written_log, tmp_path):
    core = minimal_core(written_log)
    core["predicate_results"] = [
        {
            "id": "LEG1_NORMAL_N10_EXIT_ZERO",
            "result": "FAIL",
            "detail": "Traceback (most recent call last)",
        }
    ]
    ok, _detail = gate.public_output_clean(written_log, core, tmp_path / "gate-root")
    assert ok is False


def test_public_output_hygiene_flags_an_unparseable_iteration_log(tmp_path):
    path = tmp_path / "ITERATION_LOG.md"
    meta = make_meta()
    append_section(path, meta.section_id, render_section(meta, [make_row()]))
    path.write_text(
        path.read_text(encoding="utf-8").replace('"run_status":"COMPLETED"', '"run_status":"X"'),
        encoding="utf-8",
    )
    ok, _detail = gate.public_output_clean(
        path, minimal_core(path), tmp_path / "gate-root"
    )
    assert ok is False


def test_the_gate_never_dumps_an_environment(tmp_path):
    """No raw environment mapping reaches public evidence. The gate reads no
    environment variable at all, which is the structural version of this."""
    source = (REPO_ROOT / "scripts" / "run_phase4_loop_gate.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
            pytest.fail("the gate script reads an environment variable")


# --- static gate boundary (dispatch section 12) -----------------------------

#: The designated gate runs OUTSIDE pytest's autouse network guard, so its
#: provider/network boundary has to be asserted statically.
BANNED_GATE_IMPORT_ROOTS = {
    "claude_agent_sdk",
    "anthropic",
    "requests",
    "httpx",
    "urllib",
    "socket",
    "http",
    "openai",
}


def gate_source() -> str:
    return (REPO_ROOT / "scripts" / "run_phase4_loop_gate.py").read_text(encoding="utf-8")


def test_the_gate_script_imports_no_provider_or_network_surface():
    tree = ast.parse(gate_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in BANNED_GATE_IMPORT_ROOTS, f"gate imports {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            assert root not in BANNED_GATE_IMPORT_ROOTS, f"gate imports from {node.module}"


def test_the_gate_script_reaches_no_provider_execution_surface():
    """The gate may use first-party Sentinel fixture/pipeline surfaces — it
    must run the real integration — but never the harness, the auth path,
    the tool cage or the SDK."""
    tree = ast.parse(gate_source())
    banned_modules = {
        "agents.checker.harness",
        "agents.checker.auth",
        "agents.checker.tools",
        "agents.checker.fx",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in banned_modules, f"gate imports {node.module}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in banned_modules, f"gate imports {alias.name}"


def test_the_static_boundary_test_is_not_vacuous():
    """It must be able to fail: the gate really does import the first-party
    Sentinel surfaces it needs, so an absent-import assertion is meaningful
    rather than trivially true."""
    source = gate_source()
    assert "from sentinel.pipeline import" in source
    assert "from runner.loop import" in source


def test_the_gate_offers_no_flag_that_changes_a_frozen_bound():
    parser = gate.build_parser()
    options = {
        option for action in parser._actions for option in action.option_strings
    }
    assert options == {"-h", "--help", "--source-sha", "--iteration-log", "--artifact"}
    for forbidden in ("ceiling", "budget", "threshold", "judgment", "model", "agent",
                      "provider", "predicate"):
        assert not any(forbidden in option for option in options), forbidden


# --- the durable-state self-check -------------------------------------------


@pytest.fixture
def self_check_case(tmp_path):
    """One small real loop over real durable state, with its section
    written — the smallest thing the self-check can be pointed at."""
    work_root = tmp_path / "work"
    work_root.mkdir()
    result = gate._run_seeded_loop(
        work_root,
        "selfcheck",
        loop_id="loop-selfcheck",
        max_iterations=2,
        statuses=("COMPLETED",),
        costs=(0,),
    )
    log_path = tmp_path / "ITERATION_LOG.md"
    evidence = gate.GateEvidence(source_sha=SOURCE_SHA, log_path=log_path)
    conn = open_loop_state(result.env.db_path)
    section_id = evidence.write_section(
        conn,
        loop_id=result.loop_id,
        gate_leg="LEG2",
        gate_case="selfcheck-case",
        classification=SEEDED_FAULT,
        max_iterations=2,
        stop_reason=result.outcome.stop_reason,
        exit_code=result.outcome.exit_code,
        allowances=result.allowances,
    )
    yield SimpleNamespace(
        conn=conn,
        result=result,
        evidence=evidence,
        section_id=section_id,
        log_path=log_path,
    )
    conn.close()


def test_the_self_check_passes_on_a_cleanly_written_section(self_check_case):
    section_id, ok, detail = self_check_case.evidence.self_checks[0]
    assert section_id == self_check_case.section_id
    assert ok is True, detail


def test_the_self_check_detects_one_corrupted_machine_figure(self_check_case):
    """The corruption stays schema-valid on purpose, so the failure is a
    MISMATCH against durable state rather than a parse error — which is the
    thing ADR-0010 section 7's self-check actually has to catch."""
    path = self_check_case.log_path
    original = path.read_text(encoding="utf-8")
    assert '"consecutive_failures_after":0' in original
    path.write_text(
        original.replace('"consecutive_failures_after":0', '"consecutive_failures_after":7', 1),
        encoding="utf-8",
    )

    ok, detail = gate.self_check_section(
        path,
        self_check_case.section_id,
        self_check_case.conn,
        self_check_case.result.loop_id,
        self_check_case.result.allowances,
    )
    assert ok is False
    assert "consecutive_failures_after" in detail


def test_the_self_check_reads_the_file_rather_than_the_render_object(self_check_case):
    """Deleting the written bytes must fail the self-check even though the
    in-memory render succeeded moments earlier."""
    self_check_case.log_path.write_text("", encoding="utf-8")
    ok, _detail = gate.self_check_section(
        self_check_case.log_path,
        self_check_case.section_id,
        self_check_case.conn,
        self_check_case.result.loop_id,
        self_check_case.result.allowances,
    )
    assert ok is False


def test_the_self_check_compares_the_fields_adr0010_requires():
    for name in (
        "iteration_index",
        "planned_run_id",
        "bound_run_id",
        "iteration_state",
        "run_status",
        "tasks_created",
        "tasks_terminal",
        "findings_new",
        "findings_still_open",
        "findings_resolved",
        "iteration_cost_eur_micros",
        "cumulative_cost_eur_micros",
        "consecutive_failures_after",
    ):
        assert name in gate.SELF_CHECK_FIELDS


# --- the gate, end to end (ONE model-free invocation) -----------------------


@pytest.fixture(scope="module")
def gate_run(tmp_path_factory):
    """Run the frozen gate once, into tmp_path outputs.

    This is an IMPLEMENTATION TEST of the gate machinery. It is NOT the
    designated q77-p4-gate-exec-a execution, and it writes nothing to the
    repository root."""
    out = tmp_path_factory.mktemp("phase4-gate")
    log_path = out / "ITERATION_LOG.md"
    artifact_path = out / "phase4_loop_gate.json"
    exit_code = gate.main(
        [
            "--source-sha", SOURCE_SHA,
            "--iteration-log", str(log_path),
            "--artifact", str(artifact_path),
        ]
    )
    return SimpleNamespace(
        exit_code=exit_code,
        log_path=log_path,
        artifact_path=artifact_path,
        artifact=json.loads(artifact_path.read_text(encoding="utf-8")),
    )


def predicate(artifact, predicate_id):
    for row in artifact["predicate_results"]:
        if row["id"] == predicate_id:
            return row
    raise AssertionError(f"{predicate_id} absent from the artifact")


def test_the_gate_records_every_frozen_predicate_and_passes(gate_run):
    results = gate_run.artifact["predicate_results"]
    assert [row["id"] for row in results] == list(gate.PREDICATE_IDS)
    failed = [row for row in results if row["result"] != "PASS"]
    assert failed == [], failed
    assert gate_run.artifact["overall"] == "PASS"
    assert gate_run.exit_code == 0


def test_the_gate_is_model_free(gate_run):
    assert gate_run.artifact["model_calls"] == 0
    assert gate_run.artifact["provider_spend_eur_micros"] == 0


def test_the_gate_artifact_carries_the_frozen_contract(gate_run):
    artifact = gate_run.artifact
    assert artifact["schema_version"] == 1
    assert artifact["gate"] == "phase4_bounded_loop"
    assert artifact["gate_contract"] == "ADR-0010-section-7"
    assert artifact["source_sha"] == SOURCE_SHA
    assert artifact["loop_budget_eur_micros"] == 750_000
    assert artifact["failure_threshold"] == 3
    gate.validate_artifact(artifact)


def test_the_recorded_hash_is_of_the_written_iteration_log(gate_run):
    assert gate_run.artifact["iteration_log_sha256"] == iteration_log_sha256(gate_run.log_path)
    # No circular hash: the log does not contain the artifact's own digest.
    assert gate_run.artifact["iteration_log_sha256"] not in gate_run.log_path.read_text(
        encoding="utf-8"
    )


def test_every_leg_wrote_its_cases(gate_run):
    legs = {leg["leg"]: leg for leg in gate_run.artifact["legs"]}
    assert set(legs) == {"LEG1", "LEG2", "LEG3", "LEG4"}
    assert all(leg["result"] == "PASS" for leg in legs.values())
    assert legs["LEG1"]["cases"] == ["leg1-normal-n10"]
    assert len(legs["LEG2"]["cases"]) == 5
    assert len(legs["LEG3"]["cases"]) == 3
    assert len(legs["LEG4"]["cases"]) == 3


def test_leg1_ran_ten_real_iterations_with_continuity(gate_run):
    sections = parse_sections(gate_run.log_path.read_text(encoding="utf-8"))
    leg1 = sections["loop-p4g-leg1::leg1-normal-n10"]
    assert leg1.metadata["max_iterations"] == 10
    assert leg1.metadata["stop_reason"] == "COMPLETED_ITERATION_CAP"
    assert leg1.metadata["exit_code"] == 0
    assert [row["iteration_index"] for row in leg1.rows] == list(range(10))
    assert len({row["planned_run_id"] for row in leg1.rows}) == 10
    assert all(row["planned_run_id"] == row["bound_run_id"] for row in leg1.rows)
    assert all(row["run_status"] == "COMPLETED" for row in leg1.rows)
    assert all(row["tasks_created"] == row["tasks_terminal"] > 0 for row in leg1.rows)
    # Continuity: only the first iteration observes anything new.
    assert leg1.rows[0]["findings_new"] > 0
    assert all(row["findings_new"] == 0 for row in leg1.rows[1:])
    # Real runs, zero cost: that is what "no model call happened" looks like.
    assert leg1.rows[-1]["cumulative_cost_eur_micros"] == 0


def test_leg2_reduced_allowance_is_one_and_never_restored(gate_run):
    row = predicate(gate_run.artifact, "LEG2_REDUCED_ALLOWANCE_PROPAGATED")
    assert row["result"] == "PASS"
    assert row["evidence"]["allowance_seen"] == 1
    assert row["evidence"]["coordinator_remaining"] == 1


def test_leg2_boundary_cases_land_on_the_frozen_stop_reasons(gate_run):
    sections = parse_sections(gate_run.log_path.read_text(encoding="utf-8"))
    expected = {
        "loop-p4g-leg2a::leg2a-749999-midloop": ("COMPLETED_ITERATION_CAP", 0, 749_999),
        "loop-p4g-leg2b::leg2b-exact-cap-midloop": ("COST_BREAKER_TRIPPED", 1, 750_000),
        "loop-p4g-leg2c::leg2c-overshoot-midloop": ("COST_BREAKER_TRIPPED", 1, 750_001),
        "loop-p4g-leg2d::leg2d-terminal-exact-cap": ("COMPLETED_ITERATION_CAP", 0, 750_000),
        "loop-p4g-leg2e::leg2e-terminal-overshoot": ("COST_BREAKER_TRIPPED", 1, 750_001),
    }
    for section_id, (stop_reason, exit_code, cumulative) in expected.items():
        section = sections[section_id]
        assert section.metadata["stop_reason"] == stop_reason, section_id
        assert section.metadata["exit_code"] == exit_code, section_id
        # The overshoot is accounted in full and never clamped to the ceiling.
        assert section.rows[-1]["cumulative_cost_eur_micros"] == cumulative, section_id


def test_leg2_refusals_started_no_further_run(gate_run):
    for predicate_id in ("LEG2_NO_NEXT_RUN_AT_EXACT_CAP", "LEG2_NO_NEXT_RUN_AFTER_OVERSHOOT"):
        row = predicate(gate_run.artifact, predicate_id)
        assert row["result"] == "PASS"
        assert row["evidence"]["intents"] == 1
        assert row["evidence"]["runs"] == 1


def test_leg3_alert_has_all_four_parts(gate_run):
    row = predicate(gate_run.artifact, "LEG3_FOUR_PART_ALERT")
    assert row["result"] == "PASS"
    assert row["evidence"] == {
        "durable_stop_reason": True,
        "labeled_iteration_log_section": True,
        "nonzero_exit": True,
        "structured_error_event": True,
    }
    sections = parse_sections(gate_run.log_path.read_text(encoding="utf-8"))
    alert = sections["loop-p4g-leg3trip::leg3-trip-at-three"]
    assert alert.metadata["alert_label"] == PHASE4_FAILURE_ALERT
    assert alert.metadata["stop_reason"] == "CONSECUTIVE_FAILURE_BREAKER_TRIPPED"
    assert len(alert.rows) == 3
    assert alert.rows[-1]["consecutive_failures_after"] == 3
    # The label is on the alert section only, not on every section.
    labelled = [s for s in sections.values() if s.metadata["alert_label"] is not None]
    assert len(labelled) == 1


def test_leg3_reset_sequence_does_not_trip_from_a_stale_streak(gate_run):
    sections = parse_sections(gate_run.log_path.read_text(encoding="utf-8"))
    reset = sections["loop-p4g-leg3reset::leg3-reset-sequence"]
    assert [row["run_status"] for row in reset.rows] == [
        "FAILED", "FAILED", "COMPLETED", "FAILED", "FAILED",
    ]
    assert [row["consecutive_failures_after"] for row in reset.rows] == [1, 2, 0, 1, 2]
    assert reset.metadata["stop_reason"] == "COMPLETED_ITERATION_CAP"
    assert reset.metadata["exit_code"] == 0


def test_leg3_streak_outranks_normal_completion_at_the_boundary(gate_run):
    sections = parse_sections(gate_run.log_path.read_text(encoding="utf-8"))
    precedence = sections["loop-p4g-leg3prec::leg3-terminal-precedence"]
    # N reached AND the streak at three: the breaker wins.
    assert precedence.metadata["max_iterations"] == 3
    assert len(precedence.rows) == 3
    assert precedence.metadata["stop_reason"] == "CONSECUTIVE_FAILURE_BREAKER_TRIPPED"
    assert precedence.metadata["exit_code"] == 1


def test_leg4_adopted_the_terminal_run_without_re_executing_it(gate_run):
    row = predicate(gate_run.artifact, "LEG4_TERMINAL_BEFORE_FINALIZE_ADOPTED")
    assert row["result"] == "PASS"
    assert row["evidence"]["adopted_without_reexecution"] is True
    sections = parse_sections(gate_run.log_path.read_text(encoding="utf-8"))
    primary = sections["loop-p4g-leg4primary::leg4-primary-before-finalize"]
    assert [r["iteration_index"] for r in primary.rows] == [0, 1]
    # The adopted iteration executed nothing on the restart, so it carries no
    # allowance; the iteration that did run does.
    assert primary.rows[0]["effective_allowance_eur_micros"] is None
    assert primary.rows[1]["effective_allowance_eur_micros"] == 750_000


def test_leg4_reused_the_planned_run_id_after_a_crash_before_any_run(gate_run):
    row = predicate(gate_run.artifact, "LEG4_INTENT_BEFORE_RUN_REUSED")
    assert row["result"] == "PASS"
    assert row["evidence"]["runs"] == 1


def test_leg4_reconciled_terminal_outputs_from_exactly_one_cost_source(gate_run):
    row = predicate(gate_run.artifact, "LEG4_TERMINAL_OUTPUTS_RECONCILED")
    assert row["result"] == "PASS"
    assert row["evidence"]["cost_rows"] == 1


def test_the_written_iteration_log_states_it_is_not_authoritative(gate_run):
    text = gate_run.log_path.read_text(encoding="utf-8")
    assert "NOT authoritative loop state" in text
    assert "durable SQLite ledger" in text.replace("\n", " ")


def test_the_written_iteration_log_carries_no_unsafe_value(gate_run):
    text = gate_run.log_path.read_text(encoding="utf-8")
    artifact_text = gate_run.artifact_path.read_text(encoding="utf-8")
    for canary in unsafe_canaries():
        assert canary not in text
        assert canary not in artifact_text
    for forbidden in gate.FORBIDDEN_SUBSTRINGS:
        assert forbidden not in text, forbidden
        assert forbidden not in artifact_text, forbidden
    # And every section still revalidates through the closed schema.
    assert len(parse_sections(text)) == 12


def test_the_gate_tests_create_no_repository_root_outputs(gate_run):
    """The official outputs belong to q77-p4-gate-exec-a alone. Nothing in
    this session — including the end-to-end fixture above, which has already
    run by the time this test executes — may create them."""
    assert not (REPO_ROOT / "ITERATION_LOG.md").exists()
    assert not (REPO_ROOT / "artifacts" / "phase4_loop_gate.json").exists()

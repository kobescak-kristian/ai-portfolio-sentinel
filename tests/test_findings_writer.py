"""FINDINGS.md rendering: pure render, idempotent append, LF-only,
and the C4 trailing-fragment repair."""

from __future__ import annotations

from datetime import datetime, timezone

from contracts.schemas import Finding
from sentinel.report import (
    ReportInput,
    append_run_section,
    is_section_complete,
    render_run_section,
)

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


def _run_record(run_id="r1", run_kind="dev", status="COMPLETED", tasks_created=3, tasks_terminal=3):
    from contracts.schemas import RunRecord

    return RunRecord(
        schema_version=1, run_id=run_id, run_kind=run_kind, status=status,
        started_at_utc=NOW, finished_at_utc=NOW,
        tasks_created=tasks_created, tasks_terminal=tasks_terminal,
        findings_new=0, findings_still_open=0, findings_resolved=0,
    )


def _finding(run_id="r1"):
    from contracts.schemas import compute_content_hash, compute_fingerprint

    content_hash = compute_content_hash("README.md:1", "url=x")
    fingerprint = compute_fingerprint("acme/README.md", "broken-link", content_hash)
    return Finding(
        schema_version=1, fingerprint=fingerprint, surface="acme/README.md",
        check_class="broken-link", content_hash=content_hash, location="README.md:1",
        detail="dead link", status="OPEN", first_seen_utc=NOW, last_seen_utc=NOW,
        first_seen_run_id=run_id, last_seen_run_id=run_id,
    )


def test_render_is_pure_and_deterministic():
    data = ReportInput(run=_run_record(), tasks_done=3, tasks_failed=0, tasks_dead_letter=0,
                        new_findings=[_finding()], resolved_findings=[])
    assert render_run_section(data) == render_run_section(data)


def test_render_states_live_vs_synthetic_label():
    live = render_run_section(ReportInput(run=_run_record(run_kind="live"), tasks_done=0, tasks_failed=0,
                                           tasks_dead_letter=0, new_findings=[], resolved_findings=[]))
    dev = render_run_section(ReportInput(run=_run_record(run_kind="dev"), tasks_done=0, tasks_failed=0,
                                          tasks_dead_letter=0, new_findings=[], resolved_findings=[]))
    assert "REAL DATA" in live
    assert "SYNTHETIC FIXTURES" in dev


def test_render_states_failed_partial_status():
    data = ReportInput(run=_run_record(status="FAILED", tasks_created=5, tasks_terminal=2),
                        tasks_done=1, tasks_failed=1, tasks_dead_letter=0, new_findings=[], resolved_findings=[])
    text = render_run_section(data)
    assert "FAILED (partial — 2/5 tasks terminal)" in text


def test_render_is_lf_only():
    data = ReportInput(run=_run_record(), tasks_done=0, tasks_failed=0, tasks_dead_letter=0,
                        new_findings=[_finding()], resolved_findings=[])
    text = render_run_section(data)
    assert "\r" not in text


def test_append_creates_file_with_header(tmp_path):
    path = tmp_path / "FINDINGS.md"
    data = ReportInput(run=_run_record(), tasks_done=3, tasks_failed=0, tasks_dead_letter=0,
                        new_findings=[], resolved_findings=[])
    appended = append_run_section(path, "r1", render_run_section(data))
    assert appended is True
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<!--")
    assert "sentinel:run r1" in text
    assert "\r" not in path.read_bytes().decode("utf-8")


def test_append_is_idempotent_for_the_same_run_id(tmp_path):
    path = tmp_path / "FINDINGS.md"
    data = ReportInput(run=_run_record(), tasks_done=3, tasks_failed=0, tasks_dead_letter=0,
                        new_findings=[], resolved_findings=[])
    section = render_run_section(data)
    assert append_run_section(path, "r1", section) is True
    before = path.read_bytes()
    assert append_run_section(path, "r1", section) is False
    after = path.read_bytes()
    assert before == after


def test_append_never_duplicates_across_many_runs(tmp_path):
    path = tmp_path / "FINDINGS.md"
    for i in range(1, 4):
        run_id = f"r{i}"
        data = ReportInput(run=_run_record(run_id=run_id), tasks_done=1, tasks_failed=0,
                            tasks_dead_letter=0, new_findings=[], resolved_findings=[])
        append_run_section(path, run_id, render_run_section(data))
    text = path.read_text(encoding="utf-8")
    assert text.count("<!-- sentinel:run") == 3
    assert text.count("<!-- /sentinel:run") == 3


def test_is_section_complete_false_for_missing_file(tmp_path):
    assert is_section_complete(tmp_path / "does-not-exist.md", "r1") is False


def test_trailing_incomplete_fragment_is_repaired_not_duplicated(tmp_path):
    """A crash mid-render leaves an opening marker with no matching
    close. append_run_section must truncate only that fragment (bytes
    before it untouched) and append the complete section."""
    path = tmp_path / "FINDINGS.md"
    complete_data = ReportInput(run=_run_record(run_id="r0"), tasks_done=1, tasks_failed=0,
                                tasks_dead_letter=0, new_findings=[], resolved_findings=[])
    append_run_section(path, "r0", render_run_section(complete_data))
    before_bytes = path.read_bytes()

    # Simulate a crash mid-render of r1: only the opening marker landed.
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("<!-- sentinel:run r1 -->\n## Run r1 (never finished writ")

    assert is_section_complete(path, "r1") is False
    data = ReportInput(run=_run_record(run_id="r1"), tasks_done=1, tasks_failed=0, tasks_dead_letter=0,
                        new_findings=[], resolved_findings=[])
    appended = append_run_section(path, "r1", render_run_section(data))
    assert appended is True

    text = path.read_text(encoding="utf-8")
    assert text.startswith(before_bytes.decode("utf-8"))  # r0's section untouched
    assert text.count("sentinel:run r1") == 2  # open + close, exactly once
    assert "never finished writ" not in text
    assert is_section_complete(path, "r1") is True


def test_new_findings_and_resolved_sections_render_expected_lines():
    data = ReportInput(run=_run_record(), tasks_done=1, tasks_failed=0, tasks_dead_letter=0,
                        new_findings=[_finding()], resolved_findings=[])
    text = render_run_section(data)
    assert "New findings — proposals only" in text
    assert "`[broken-link]` acme/README.md" in text
    assert "fp:" in text

"""Tests for sentinel/phase5/journal.py (ADR-0012 section 9, Amendment
A2 rule 4; dispatch q77-p5d-repair-stage2b1-implement-a).

Model-free and network-blocked (tests/conftest.py ``block_network``).
The POSIX signal re-delivery subprocess test runs on Linux CI and skips
on Windows.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import textwrap
import threading
import typing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from sentinel.phase5 import journal as jr

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)


def _event(**overrides) -> jr.JournalEvent:
    fields = dict(schema_version=1, seq=1, recorded_at_utc=NOW, elapsed_ms=0, writer="RUNNER", event="JOURNAL_OPENED")
    fields.update(overrides)
    return jr.JournalEvent(**fields)


def _open(path: Path, writer="RUNNER", **kwargs) -> jr.OperationalJournal:
    return jr.OperationalJournal(path, writer=writer, clock=lambda: NOW, **kwargs).open()


# ======================================================================
# Schema (F)
# ======================================================================


VALID_EVENTS = [
    dict(event="JOURNAL_OPENED"),
    dict(event="STATE_TRANSITION", state_to="PREFLIGHTED"),
    dict(event="STATE_TRANSITION", state_from="PREFLIGHTED", state_to="REPLACEMENT_MARKED"),
    dict(event="RUN_STARTED", run_ordinal=1),
    dict(event="RUN_FINISHED", run_ordinal=2),
    dict(event="INVOCATION_STARTED", run_ordinal=1, invocation_ordinal=92),
    dict(event="INVOCATION_FINISHED", run_ordinal=1, invocation_ordinal=1, invocation_outcome="RAISED"),
    dict(event="HEARTBEAT"),
    dict(event="SIGNAL_OBSERVED", signal="SIGTERM"),
    dict(event="RUNNER_EXCEPTION", cause="PRE_PROVIDER_FAILURE", exception_type="OidcAcquisitionError"),
    dict(event="OBJECTIVE_CAUSE_LATCHED", cause="SESSION_DEADLINE"),
    dict(event="OBJECTIVE_CAUSE_LATCHED", cause="INVOCATION_STALL_DEADLINE"),
    dict(event="OBJECTIVE_CAUSE_LATCHED", cause="WATCHDOG"),
    dict(event="WATCHDOG_ESCALATED", cause="WATCHDOG"),
    dict(event="WATCHDOG_ESCALATED", cause="SESSION_DEADLINE"),
    dict(event="WATCHDOG_ESCALATED", cause="RUNNER_EXCEPTION"),
    dict(event="INVOCATION_FINISHED", run_ordinal=2, invocation_ordinal=7, invocation_outcome="TIMED_OUT"),
    dict(event="TERMINAL_WRITE_STARTED", record_kind="QUALITY"),
    dict(event="TERMINAL_WRITE_COMPLETED", record_kind="INFRASTRUCTURE_INVALID", sha256="a" * 64),
    dict(event="TERMINAL_WRITE_FAILED", record_kind="QUALITY", exception_type="OSError"),
    dict(event="JOURNAL_FRAGMENT_OBSERVED", byte_length=12),
    dict(writer="FINALIZER", event="FINALIZER_CONSUMPTION", consumption="ASSUMED_CONSUMED_REST_UNAVAILABLE"),
    dict(writer="FINALIZER", event="FINALIZER_CANDIDATE", candidate_verdict="ABSENT"),
    dict(writer="FINALIZER", event="FINALIZER_CANDIDATE", candidate_verdict="UNPARSEABLE", sha256="b" * 64),
    dict(writer="FINALIZER", event="FINALIZER_QUARANTINED", path_class="UNEXPECTED", sha256="c" * 64),
    dict(writer="FINALIZER", event="FINALIZER_DECISION", action="WRITE_UNCLASSIFIED"),
]


@pytest.mark.parametrize("fields", VALID_EVENTS)
def test_every_event_constructs_with_its_exact_fields(fields):
    event = _event(**fields)
    line = jr.event_line_bytes(event)
    assert len(line) <= jr.MAX_LINE_BYTES
    assert jr._parse_line(line[:-1]) == event


def test_vocabulary_is_closed_and_fully_mapped():
    assert set(typing.get_args(jr.JournalEventType)) == set(jr._EVENT_FIELDS)
    with pytest.raises(ValidationError):
        _event(event="FREE_TEXT_EVENT")


@pytest.mark.parametrize(
    "fields",
    [
        dict(event="SIGNAL_OBSERVED"),  # missing required
        dict(event="JOURNAL_OPENED", signal="SIGINT"),  # not permitted
        dict(event="STATE_TRANSITION", state_to="EXECUTING"),  # no state_from except PREFLIGHTED
        dict(event="RUNNER_EXCEPTION", cause="WATCHDOG", exception_type="X"),  # 2C cause never a runner exception
        dict(event="RUNNER_EXCEPTION", cause="SESSION_DEADLINE", exception_type="X"),
        dict(event="RUNNER_EXCEPTION", cause="INVOCATION_STALL_DEADLINE", exception_type="X"),
        dict(event="OBJECTIVE_CAUSE_LATCHED", cause="RUNNER_EXCEPTION"),  # 2B cause is never latched
        dict(event="OBJECTIVE_CAUSE_LATCHED", cause="PRE_PROVIDER_FAILURE"),
        dict(event="OBJECTIVE_CAUSE_LATCHED"),  # cause required
        dict(event="OBJECTIVE_CAUSE_LATCHED", cause="SIGTERM"),  # a signal is never a cause
        dict(event="OBJECTIVE_CAUSE_LATCHED", cause="SESSION_DEADLINE", exception_type="X"),  # not permitted
        dict(event="OBJECTIVE_CAUSE_LATCHED", cause="SESSION_DEADLINE", signal="SIGTERM"),
        dict(writer="FINALIZER", event="OBJECTIVE_CAUSE_LATCHED", cause="SESSION_DEADLINE"),  # RUNNER only
        dict(event="WATCHDOG_ESCALATED"),  # cause required
        dict(event="WATCHDOG_ESCALATED", cause="SIGINT"),
        dict(event="WATCHDOG_ESCALATED", cause="WATCHDOG", exception_type="X"),
        dict(writer="FINALIZER", event="WATCHDOG_ESCALATED", cause="WATCHDOG"),  # RUNNER only
        dict(event="INVOCATION_FINISHED", run_ordinal=1, invocation_ordinal=1, invocation_outcome="CANCELLED"),
        dict(event="HEARTBEAT", cause="WATCHDOG"),  # cause only on cause-bearing events
        dict(event="RUNNER_EXCEPTION", cause="RUNNER_EXCEPTION", exception_type="module.ValueError"),
        dict(event="RUNNER_EXCEPTION", cause="RUNNER_EXCEPTION", exception_type="ValueError: secret message"),
        dict(event="RUNNER_EXCEPTION", cause="RUNNER_EXCEPTION", exception_type="A" * 81),
        dict(event="TERMINAL_WRITE_COMPLETED", record_kind="QUALITY", sha256="ABC"),
        dict(event="TERMINAL_WRITE_STARTED", record_kind="GREEN"),  # no quality disposition vocabulary
        dict(event="INVOCATION_STARTED", run_ordinal=3, invocation_ordinal=1),
        dict(event="INVOCATION_STARTED", run_ordinal=1, invocation_ordinal=201),
        dict(event="FINALIZER_DECISION", action="WRITE_UNCLASSIFIED"),  # runner writer
        dict(writer="FINALIZER", event="RUNNER_EXCEPTION", cause="RUNNER_EXCEPTION", exception_type="X"),
        dict(event="HEARTBEAT", seq=0),
        dict(event="HEARTBEAT", elapsed_ms=-1),
        dict(event="HEARTBEAT", recorded_at_utc=datetime(2026, 9, 15, 12, 0)),
        dict(event="HEARTBEAT", recorded_at_utc=datetime(2026, 9, 15, 12, 0, tzinfo=timezone(timedelta(hours=2)))),
        dict(event="HEARTBEAT", message="free text"),  # extra forbidden
    ],
)
def test_schema_refusals(fields):
    with pytest.raises(ValidationError):
        _event(**fields)


def test_no_unbounded_free_text_field_exists():
    for name, info in jr.JournalEvent.model_fields.items():
        args = set(typing.get_args(info.annotation)) or {info.annotation}
        if str in args:
            assert name in ("exception_type", "sha256"), name  # both regex-bounded in the validator


def test_liveness_line_is_bounded_elapsed_only():
    import re

    for value in (0, 1.9, 12345.6, -5):
        assert re.fullmatch(r"HEARTBEAT elapsed_s=\d+", jr.liveness_line(value))
    assert jr.liveness_line(-5) == "HEARTBEAT elapsed_s=0"


# ======================================================================
# Writer (F)
# ======================================================================


def test_writer_monotonic_seq_and_fsync_per_append(tmp_path):
    synced: list[int] = []
    path = tmp_path / "j.jsonl"
    journal = jr.OperationalJournal(
        path, writer="RUNNER", clock=lambda: NOW, fsync=lambda fd: synced.append(fd)
    ).open()
    journal.append("STATE_TRANSITION", state_to="PREFLIGHTED")
    journal.append("RUN_STARTED", run_ordinal=1)
    journal.close()
    result = jr.read_journal(path)
    assert result.integrity == "OK"
    assert [e.seq for e in result.events] == [1, 2, 3]
    assert len(synced) == 3
    assert journal.broken is False


def test_elapsed_ms_uses_monotonic_clock(tmp_path):
    ticks = iter([100.0, 100.25, 101.0])
    journal = jr.OperationalJournal(
        tmp_path / "j.jsonl", writer="RUNNER", clock=lambda: NOW, monotonic=lambda: next(ticks)
    ).open()
    event = journal.append("HEARTBEAT")
    journal.close()
    assert event.elapsed_ms == 1000


def test_concurrent_appends_are_serialized(tmp_path):
    path = tmp_path / "j.jsonl"
    journal = _open(path, fsync=lambda fd: None)

    def worker():
        for _ in range(200):
            journal.append("HEARTBEAT")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    journal.close()
    result = jr.read_journal(path)
    assert result.integrity == "OK"
    assert len(result.events) == 1 + 8 * 200
    assert [e.seq for e in result.events] == list(range(1, 1602))


def test_resume_after_trailing_fragment_is_acknowledged(tmp_path):
    path = tmp_path / "j.jsonl"
    runner = _open(path)
    runner.append("STATE_TRANSITION", state_to="PREFLIGHTED")
    runner.close()
    with open(path, "ab") as handle:
        handle.write(b'{"seq": 3, "partial')
    assert jr.read_journal(path).integrity == "TRAILING_FRAGMENT"
    finalizer = _open(path, writer="FINALIZER")
    finalizer.append("FINALIZER_DECISION", action="WRITE_UNCLASSIFIED")
    finalizer.close()
    result = jr.read_journal(path)
    assert result.integrity == "OK"
    events = [e.event for e in result.events]
    assert events[-3:] == ["JOURNAL_FRAGMENT_OBSERVED", "JOURNAL_OPENED", "FINALIZER_DECISION"]
    assert result.events[-3].byte_length == len(b'{"seq": 3, "partial')
    assert [e.seq for e in result.events] == list(range(1, 6))


def test_corrupt_middle_line_and_seq_gap(tmp_path):
    path = tmp_path / "j.jsonl"
    good = [jr.event_line_bytes(_event(seq=i + 1, event="HEARTBEAT")) for i in range(3)]
    path.write_bytes(good[0] + b"garbage\n" + good[1])
    result = jr.read_journal(path)
    assert result.integrity == "CORRUPT" and len(result.events) == 1
    path.write_bytes(good[0] + good[2])
    result = jr.read_journal(path)
    assert result.integrity == "CORRUPT" and len(result.events) == 1
    path.write_bytes(jr.event_line_bytes(_event(seq=2, event="HEARTBEAT")))
    assert jr.read_journal(path).integrity == "CORRUPT"


def test_non_canonical_line_is_corrupt(tmp_path):
    path = tmp_path / "j.jsonl"
    line = jr.event_line_bytes(_event(event="HEARTBEAT"))
    path.write_bytes(line.replace(b",", b", ", 1))
    assert jr.read_journal(path).integrity == "CORRUPT"


def test_reader_absent_empty_oversize_and_directory(tmp_path, monkeypatch):
    assert jr.read_journal(tmp_path / "missing.jsonl").integrity == "ABSENT"
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert jr.read_journal(empty).integrity == "OK"
    (tmp_path / "dir.jsonl").mkdir()
    assert jr.read_journal(tmp_path / "dir.jsonl").integrity == "CORRUPT"
    big = tmp_path / "big.jsonl"
    big.write_bytes(b"x" * 20)
    monkeypatch.setattr(jr, "MAX_JOURNAL_BYTES", 10)
    assert jr.read_journal(big).integrity == "CORRUPT"


def test_symlinked_journal_path_latches_and_writes_nothing(tmp_path):
    import os

    target = tmp_path / "target.jsonl"
    target.write_bytes(b"")
    link = tmp_path / "link.jsonl"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    journal = _open(link)
    assert journal.broken is True
    assert journal.append("HEARTBEAT") is None
    assert target.read_bytes() == b""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_journal_file_mode_is_0600(tmp_path):
    import os
    import stat

    path = tmp_path / "j.jsonl"
    _open(path).close()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_append_failure_latches_and_never_raises(tmp_path):
    def failing_fsync(_fd):
        raise OSError("disk gone")

    path = tmp_path / "j.jsonl"
    journal = jr.OperationalJournal(path, writer="RUNNER", clock=lambda: NOW, fsync=failing_fsync).open()
    assert journal.broken is True
    assert journal.append("HEARTBEAT") is None
    journal.observe_signal("SIGINT")
    assert journal.signals_observed == ("SIGINT",)
    journal.close()


def test_invalid_append_fields_latch_instead_of_raising(tmp_path):
    journal = _open(tmp_path / "j.jsonl")
    assert journal.append("SIGNAL_OBSERVED", signal="SIGKILL") is None
    assert journal.broken is True


def test_capacity_caps_latch(tmp_path):
    journal = _open(tmp_path / "a.jsonl", max_events=2)
    assert journal.append("HEARTBEAT") is not None
    assert journal.append("HEARTBEAT") is None and journal.broken
    journal = _open(tmp_path / "b.jsonl", max_bytes=600)
    assert journal.append("HEARTBEAT") is None and journal.broken


def test_append_before_open_is_a_noop(tmp_path):
    journal = jr.OperationalJournal(tmp_path / "j.jsonl", writer="RUNNER")
    assert journal.append("HEARTBEAT") is None
    journal.close()
    with pytest.raises(ValueError):
        jr.OperationalJournal(tmp_path / "j.jsonl", writer="CONFIRM")


def test_context_manager_opens_and_closes(tmp_path):
    path = tmp_path / "j.jsonl"
    with jr.OperationalJournal(path, writer="FINALIZER", clock=lambda: NOW) as journal:
        journal.append("FINALIZER_CONSUMPTION", consumption="CONSUMED")
    assert [e.event for e in jr.read_journal(path).events] == ["JOURNAL_OPENED", "FINALIZER_CONSUMPTION"]


# ======================================================================
# Signals (F)
# ======================================================================


def test_observe_signal_while_lock_held_does_not_deadlock_and_is_drained(tmp_path):
    path = tmp_path / "j.jsonl"
    journal = _open(path)
    journal._lock.acquire()
    try:
        journal.observe_signal("SIGINT")  # must return immediately
    finally:
        journal._lock.release()
    assert journal.signals_observed == ("SIGINT",)
    journal.append("HEARTBEAT")
    journal.close()
    events = [e.event for e in jr.read_journal(path).events]
    assert events == ["JOURNAL_OPENED", "HEARTBEAT", "SIGNAL_OBSERVED"]


def test_observe_signal_records_distinct_in_order(tmp_path):
    journal = _open(tmp_path / "j.jsonl")
    for name in ("SIGTERM", "SIGINT", "SIGTERM"):
        journal.observe_signal(name)
    journal.close()
    assert journal.signals_observed == ("SIGTERM", "SIGINT")


def test_sigint_handler_records_then_calls_previous_handler(tmp_path):
    path = tmp_path / "j.jsonl"
    journal = _open(path)
    calls = []
    handler = jr.make_observing_handler(journal, "SIGINT", lambda signum, frame: calls.append(signum))
    handler(signal.SIGINT, None)
    assert calls == [signal.SIGINT]
    handler_default = jr.make_observing_handler(journal, "SIGINT", signal.default_int_handler)
    with pytest.raises(KeyboardInterrupt):
        handler_default(signal.SIGINT, None)
    ignored = jr.make_observing_handler(journal, "SIGINT", signal.SIG_IGN)
    ignored(signal.SIGINT, None)
    journal.close()
    assert [e.signal for e in jr.read_journal(path).events if e.event == "SIGNAL_OBSERVED"] == ["SIGINT"] * 3


def test_install_and_restore_handlers():
    before = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))

    class _Stub:
        def observe_signal(self, _name):
            pass

    restore = jr.install_observing_signal_handlers(_Stub())
    try:
        assert signal.getsignal(signal.SIGINT) is not before[0]
    finally:
        restore()
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal re-delivery")
def test_sigterm_still_terminates_process_by_signal_and_is_journaled(tmp_path):
    path = tmp_path / "j.jsonl"
    script = textwrap.dedent(
        f"""
        import os, signal, sys, time
        sys.path.insert(0, {str(REPO_ROOT)!r})
        from sentinel.phase5.journal import OperationalJournal, install_observing_signal_handlers
        journal = OperationalJournal({str(path)!r}, writer="RUNNER").open()
        install_observing_signal_handlers(journal)
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(5)
        sys.exit(0)
        """
    )
    completed = subprocess.run([sys.executable, "-c", script], timeout=30)
    assert completed.returncode == -signal.SIGTERM
    events = jr.read_journal(path).events
    assert [e.signal for e in events if e.event == "SIGNAL_OBSERVED"] == ["SIGTERM"]


# ======================================================================
# Summary and content-freedom (F / A)
# ======================================================================


def test_summarize_journal(tmp_path):
    path = tmp_path / "j.jsonl"
    runner = _open(path)
    runner.append("STATE_TRANSITION", state_to="PREFLIGHTED")
    runner.observe_signal("SIGTERM")
    runner.append("OBJECTIVE_CAUSE_LATCHED", cause="SESSION_DEADLINE")  # after a signal: not authoritative
    runner.append("RUNNER_EXCEPTION", cause="RUNNER_EXCEPTION", exception_type="ValueError")
    runner.append("RUNNER_EXCEPTION", cause="PRE_PROVIDER_FAILURE", exception_type="OSError")
    runner.append("TERMINAL_WRITE_FAILED", record_kind="QUALITY", exception_type="OSError")
    runner.append("STATE_TRANSITION", state_from="PREFLIGHTED", state_to="REPLACEMENT_MARKED")
    runner.close()
    finalizer = _open(path, writer="FINALIZER")
    finalizer.observe_signal("SIGINT")
    finalizer.observe_signal("SIGTERM")
    finalizer.close()
    summary = jr.summarize_journal(jr.read_journal(path))
    assert summary.integrity == "OK"
    assert summary.signals == ("SIGTERM", "SIGINT")
    assert summary.runner_exception_cause == "RUNNER_EXCEPTION"
    assert summary.terminal_write_failed is True
    assert summary.last_state == "REPLACEMENT_MARKED"
    assert summary.objective_cause is None
    assert jr.summarize_journal(jr.read_journal(tmp_path / "none")).integrity == "ABSENT"


# ======================================================================
# Objective cause authority (Stage 2C-1)
# ======================================================================


def test_cause_before_signal_is_retained_and_later_signals_stay_descriptive(tmp_path):
    path = tmp_path / "j.jsonl"
    runner = _open(path)
    runner.append("STATE_TRANSITION", state_to="PREFLIGHTED")
    runner.append("OBJECTIVE_CAUSE_LATCHED", cause="INVOCATION_STALL_DEADLINE")
    runner.append("INVOCATION_FINISHED", run_ordinal=1, invocation_ordinal=3, invocation_outcome="TIMED_OUT")
    runner.observe_signal("SIGTERM")
    runner.append("WATCHDOG_ESCALATED", cause="INVOCATION_STALL_DEADLINE")
    runner.append("OBJECTIVE_CAUSE_LATCHED", cause="WATCHDOG")  # a second latch event never replaces the first
    runner.close()
    finalizer = _open(path, writer="FINALIZER")
    finalizer.observe_signal("SIGINT")
    finalizer.close()
    summary = jr.summarize_journal(jr.read_journal(path))
    assert summary.integrity == "OK"
    assert summary.objective_cause == "INVOCATION_STALL_DEADLINE"
    assert summary.signals == ("SIGTERM", "SIGINT")
    assert summary.runner_exception_cause is None


def test_signal_before_cause_is_not_authoritative(tmp_path):
    path = tmp_path / "j.jsonl"
    runner = _open(path)
    runner.observe_signal("SIGINT")
    runner.append("OBJECTIVE_CAUSE_LATCHED", cause="SESSION_DEADLINE")
    runner.close()
    summary = jr.summarize_journal(jr.read_journal(path))
    assert summary.integrity == "OK" and summary.signals == ("SIGINT",)
    assert summary.objective_cause is None


def test_watchdog_escalated_alone_establishes_nothing(tmp_path):
    path = tmp_path / "j.jsonl"
    runner = _open(path)
    runner.append("WATCHDOG_ESCALATED", cause="WATCHDOG")
    runner.close()
    assert jr.summarize_journal(jr.read_journal(path)).objective_cause is None


def test_corrupt_journal_discards_an_earlier_parsed_objective_cause(tmp_path):
    path = tmp_path / "j.jsonl"
    runner = _open(path)
    runner.append("OBJECTIVE_CAUSE_LATCHED", cause="SESSION_DEADLINE")
    runner.close()
    with open(path, "ab") as handle:
        handle.write(b"not a journal line\n")
    result = jr.read_journal(path)
    assert result.integrity == "CORRUPT"
    assert any(event.event == "OBJECTIVE_CAUSE_LATCHED" for event in result.events)
    assert jr.summarize_journal(result).objective_cause is None


def test_trailing_fragment_keeps_a_prior_objective_cause_authoritative(tmp_path):
    path = tmp_path / "j.jsonl"
    runner = _open(path)
    runner.append("OBJECTIVE_CAUSE_LATCHED", cause="WATCHDOG")
    runner.close()
    with open(path, "ab") as handle:
        handle.write(b'{"schema_version":1,"seq":3')
    result = jr.read_journal(path)
    assert result.integrity == "TRAILING_FRAGMENT"
    assert jr.summarize_journal(result).objective_cause == "WATCHDOG"


def test_finalizer_cannot_write_objective_cause_or_escalation(tmp_path):
    path = tmp_path / "j.jsonl"
    finalizer = _open(path, writer="FINALIZER")
    assert finalizer.append("OBJECTIVE_CAUSE_LATCHED", cause="SESSION_DEADLINE") is None
    finalizer.close()
    other = _open(tmp_path / "k.jsonl", writer="FINALIZER")
    assert other.append("WATCHDOG_ESCALATED", cause="WATCHDOG") is None
    other.close()
    for name in ("j.jsonl", "k.jsonl"):
        assert b"OBJECTIVE_CAUSE_LATCHED" not in (tmp_path / name).read_bytes()
        assert b"WATCHDOG_ESCALATED" not in (tmp_path / name).read_bytes()


def test_cause_field_is_the_canonical_termination_cause_and_no_second_literal_exists():
    from sentinel.phase5.evidence_records import TerminationCause

    def _literal_members(annotation) -> set:
        members = set()
        for arg in typing.get_args(annotation):
            if arg is type(None):
                continue
            members.update(typing.get_args(arg))
        return members

    cause_literals = [a for a in typing.get_args(jr.JournalEvent.model_fields["cause"].annotation) if a is not type(None)]
    assert len(cause_literals) == 1 and cause_literals[0] == TerminationCause
    assert _literal_members(jr.JournalEvent.model_fields["cause"].annotation) == set(typing.get_args(TerminationCause))
    assert jr.STAGE2C_CAUSES | jr.STAGE2B_CAUSES == set(typing.get_args(TerminationCause))
    assert _literal_members(jr.JournalEvent.model_fields["invocation_outcome"].annotation) == {
        "RETURNED", "RAISED", "TIMED_OUT",
    }


def test_canary_quality_and_secret_content_can_never_reach_journal_bytes(tmp_path):
    path = tmp_path / "j.jsonl"
    journal = _open(path)
    canary = "sk-canary-value-and-finding-text-GREEN-HONEST_FAIL"
    attempts = [
        dict(event="RUNNER_EXCEPTION", cause="RUNNER_EXCEPTION", exception_type=canary),
        dict(event="TERMINAL_WRITE_STARTED", record_kind=canary),
        dict(event="HEARTBEAT", detail=canary),
    ]
    for fields in attempts:
        fresh = _open(tmp_path / f"{len(fields)}-{fields['event']}.jsonl")
        assert fresh.append(**fields) is None
        fresh.close()
        assert canary.encode() not in (tmp_path / f"{len(fields)}-{fields['event']}.jsonl").read_bytes()
    journal.close()
    assert canary.encode() not in path.read_bytes()

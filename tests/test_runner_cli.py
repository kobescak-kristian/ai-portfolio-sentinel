"""CLI argument validation and the exit-code contract.

Runs ``main()`` in-process (fast, coverage-measured) — no subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.cli import main


def test_no_args_exits_2(capsys):
    code = main([])
    assert code == 2


def test_unknown_flag_exits_2(capsys):
    code = main(["run", "--nonexistent-flag"])
    assert code == 2


def test_unknown_command_exits_2():
    code = main(["frobnicate"])
    assert code == 2


def test_run_kind_restricted_to_three_values():
    code = main(["run", "--run-kind", "bogus", "--source", "fixtures"])
    assert code == 2


def test_source_live_requires_github_user(tmp_path):
    code = main(["run", "--run-kind", "live", "--source", "live"])
    assert code == 2


def test_source_live_forbids_eval_run_kind(tmp_path):
    code = main(
        ["run", "--run-kind", "eval", "--source", "live", "--github-user", "someone"]
    )
    assert code == 2


def test_db_with_nonexistent_parent_still_creates_it(tmp_path):
    """RunConfig/open_ledger creates missing parent directories —
    this is the documented, deliberate behavior (not a usage error)."""
    db = tmp_path / "nested" / "deeper" / "sentinel.sqlite3"
    code = main(
        [
            "run",
            "--run-kind",
            "dev",
            "--source",
            "fixtures",
            "--fixtures-root",
            str(_empty_fixtures_root(tmp_path)),
            "--db",
            str(db),
            "--findings",
            str(tmp_path / "FINDINGS.md"),
            "--log",
            str(tmp_path / "run.jsonl"),
            "--cost-ledger",
            str(tmp_path / "cost.jsonl"),
        ]
    )
    assert code == 0
    assert db.exists()


def test_relative_and_absolute_paths_both_accepted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixtures_root = _empty_fixtures_root(tmp_path)
    code = main(
        [
            "run",
            "--run-kind",
            "dev",
            "--source",
            "fixtures",
            "--fixtures-root",
            "fixtures_copy",
            "--db",
            "sentinel.sqlite3",
            "--findings",
            "FINDINGS.md",
            "--log",
            "run.jsonl",
            "--cost-ledger",
            "cost.jsonl",
        ]
    )
    assert code == 0
    assert (tmp_path / "sentinel.sqlite3").exists()


def test_run_id_flag_produces_that_exact_run_id(tmp_path):
    from sentinel import ledger

    db = tmp_path / "sentinel.sqlite3"
    code = main(
        [
            "run",
            "--run-kind",
            "dev",
            "--source",
            "fixtures",
            "--fixtures-root",
            str(_empty_fixtures_root(tmp_path)),
            "--db",
            str(db),
            "--findings",
            str(tmp_path / "FINDINGS.md"),
            "--log",
            str(tmp_path / "run.jsonl"),
            "--cost-ledger",
            str(tmp_path / "cost.jsonl"),
            "--run-id",
            "fixed-001",
        ]
    )
    assert code == 0
    conn = ledger.open_ledger(db, create=False)
    try:
        assert ledger.get_run(conn, "fixed-001") is not None
    finally:
        conn.close()


def test_containment_writes_only_under_explicit_paths(tmp_path, monkeypatch):
    """Every path the run writes lands under one of the four explicit
    CLI paths — nothing appears elsewhere in tmp_path."""
    monkeypatch.chdir(tmp_path)
    fixtures_root = _empty_fixtures_root(tmp_path)
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    code = main(
        [
            "run",
            "--run-kind",
            "dev",
            "--source",
            "fixtures",
            "--fixtures-root",
            str(fixtures_root),
            "--db",
            str(tmp_path / "out" / "sentinel.sqlite3"),
            "--findings",
            str(tmp_path / "out" / "FINDINGS.md"),
            "--log",
            str(tmp_path / "out" / "run.jsonl"),
            "--cost-ledger",
            str(tmp_path / "out" / "cost.jsonl"),
        ]
    )
    assert code == 0
    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    new_files = after - before
    for path in new_files:
        assert str(path).startswith(str(tmp_path / "out"))


def test_judgment_mode_defaults_to_stub_and_needs_no_agent_setup(tmp_path):
    """A stub-mode run must succeed with no Agent SDK setup at all --
    proving --judgment-mode's default doesn't require FX/auth."""
    fixtures_root = _empty_fixtures_root(tmp_path)
    code = main(
        [
            "run", "--run-kind", "dev", "--source", "fixtures",
            "--fixtures-root", str(fixtures_root),
            "--db", str(tmp_path / "sentinel.sqlite3"),
            "--findings", str(tmp_path / "FINDINGS.md"),
            "--log", str(tmp_path / "run.jsonl"),
            "--cost-ledger", str(tmp_path / "cost.jsonl"),
        ]
    )
    assert code == 0


def test_judgment_mode_agent_setup_failure_exits_2_before_any_run_row(tmp_path, monkeypatch):
    """If agent-mode setup fails (e.g. an auth-override risk or FX
    resolution failure), the CLI must exit 2 -- usage/config error, no
    run row created -- never attempt execute_run at all."""
    from agents.checker import auth

    monkeypatch.setenv("ANTHROPIC_API_KEY", "canary-should-block")
    fixtures_root = _empty_fixtures_root(tmp_path)
    db = tmp_path / "sentinel.sqlite3"
    code = main(
        [
            "run", "--run-kind", "dev", "--source", "fixtures",
            "--fixtures-root", str(fixtures_root),
            "--db", str(db),
            "--findings", str(tmp_path / "FINDINGS.md"),
            "--log", str(tmp_path / "run.jsonl"),
            "--cost-ledger", str(tmp_path / "cost.jsonl"),
            "--judgment-mode", "agent",
        ]
    )
    assert code == 2
    assert not db.exists()  # ledger.open_ledger inside build_caged_judgment_stub
    # never got called with a usable auth state before failing


def test_judgment_mode_invalid_value_exits_2():
    code = main(["run", "--run-kind", "dev", "--source", "fixtures", "--judgment-mode", "bogus"])
    assert code == 2


def test_recover_subcommand_returns_3(tmp_path):
    from sentinel import ledger

    db = tmp_path / "sentinel.sqlite3"
    conn = ledger.open_ledger(db)
    conn.close()
    code = main(["recover", "--db", str(db), "--log", str(tmp_path / "recover.jsonl")])
    assert code == 3


def _empty_fixtures_root(tmp_path: Path) -> Path:
    root = tmp_path / "fixtures_copy"
    root.mkdir(exist_ok=True)
    return root

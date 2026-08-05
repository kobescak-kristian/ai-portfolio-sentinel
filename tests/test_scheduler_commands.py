"""sentinel.scheduling — pure argv/task-param builders. No live Task
Scheduler is ever invoked; the module imports no winreg/ctypes.windll
at module scope, so it's importable and testable on any platform,
including this ubuntu-latest CI leg."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

from sentinel.scheduling import ALLOWED_CADENCES, build_run_argv, build_task_params


def test_golden_argv_exact():
    """Uses PureWindowsPath deliberately — a Windows-style absolute
    path (with a drive letter) is what this builder always receives
    in real scheduled-task use, and PureWindowsPath's is_absolute()/
    str() behavior is platform-independent, so this golden test is
    exact on both this Windows dev machine and ubuntu-latest CI."""
    argv = build_run_argv(
        python_exe="C:\\Python312\\python.exe",
        github_user="kobescak-kristian",
        db_path=PureWindowsPath("C:/ProgramData/sentinel/var/sentinel.sqlite3"),
        findings_path=PureWindowsPath("C:/ProgramData/sentinel/FINDINGS.md"),
        log_path=PureWindowsPath("C:/ProgramData/sentinel/var/logs/sentinel-scheduled.jsonl"),
    )
    assert argv == [
        "C:\\Python312\\python.exe",
        "-m", "sentinel", "run",
        "--run-kind", "live",
        "--source", "live",
        "--github-user", "kobescak-kristian",
        "--db", "C:\\ProgramData\\sentinel\\var\\sentinel.sqlite3",
        "--findings", "C:\\ProgramData\\sentinel\\FINDINGS.md",
        "--log", "C:\\ProgramData\\sentinel\\var\\logs\\sentinel-scheduled.jsonl",
    ]


def test_argv_is_a_list_never_a_shell_string(tmp_path):
    argv = build_run_argv(
        python_exe="python",
        github_user="user",
        db_path=tmp_path / "db.sqlite3",
        findings_path=tmp_path / "FINDINGS.md",
        log_path=tmp_path / "run.jsonl",
    )
    assert isinstance(argv, list)
    for element in argv:
        assert isinstance(element, str)
        for shell_char in ("&", "|", ";", "`", "$("):
            assert shell_char not in element


def test_path_with_space_is_a_single_argv_element_not_split(tmp_path):
    with_space = tmp_path / "with space"
    with_space.mkdir()
    argv = build_run_argv(
        python_exe="python",
        github_user="user",
        db_path=with_space / "db.sqlite3",
        findings_path=tmp_path / "FINDINGS.md",
        log_path=tmp_path / "run.jsonl",
    )
    assert str(with_space / "db.sqlite3") in argv  # one element, not shell-split


def test_uses_absolute_paths_never_cwd_relative():
    with pytest.raises(ValueError):
        build_run_argv(
            python_exe="python", github_user="user",
            db_path=Path("relative/db.sqlite3"),
            findings_path=Path("/abs/FINDINGS.md"),
            log_path=Path("/abs/run.jsonl"),
        )


def test_rejects_empty_required_values(tmp_path):
    paths = dict(db_path=tmp_path / "a", findings_path=tmp_path / "b", log_path=tmp_path / "c")
    with pytest.raises(ValueError):
        build_run_argv(python_exe="", github_user="u", **paths)
    with pytest.raises(ValueError):
        build_run_argv(python_exe="python", github_user="", **paths)


def test_cross_platform_no_platform_specific_import_at_module_scope():
    """The module must not import winreg/ctypes.windll at module
    scope — this is what makes it importable on ubuntu CI."""
    import ast

    import sentinel.scheduling as mod

    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    banned = {"winreg", "ctypes"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned


def test_module_produces_identical_output_regardless_of_sys_platform(monkeypatch, tmp_path):
    paths = dict(db_path=tmp_path / "a", findings_path=tmp_path / "b", log_path=tmp_path / "c")
    monkeypatch.setattr(sys, "platform", "linux")
    argv_linux = build_run_argv(python_exe="python", github_user="u", **paths)
    monkeypatch.setattr(sys, "platform", "win32")
    argv_win = build_run_argv(python_exe="python", github_user="u", **paths)
    assert argv_linux == argv_win


def test_schtasks_never_invoked(monkeypatch, tmp_path):
    def _boom(*args, **kwargs):
        raise AssertionError("subprocess must never be invoked by this pure builder")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    build_run_argv(
        python_exe="python", github_user="u",
        db_path=tmp_path / "a", findings_path=tmp_path / "b", log_path=tmp_path / "c",
    )


def test_build_task_params_rejects_empty_name():
    with pytest.raises(ValueError):
        build_task_params(task_name="", cadence="Daily")


def test_build_task_params_rejects_bad_cadence():
    with pytest.raises(ValueError):
        build_task_params(task_name="SentinelDailyRun", cadence="Fortnightly")


def test_build_task_params_every_n_days_requires_interval():
    with pytest.raises(ValueError):
        build_task_params(task_name="x", cadence="EveryNDays")
    params = build_task_params(task_name="x", cadence="EveryNDays", days_interval=2)
    assert params["days_interval"] == 2


def test_allowed_cadences_matches_documented_set():
    assert ALLOWED_CADENCES == {"Daily", "EveryNDays", "Weekly", "GateBurst"}


def test_build_task_params_is_pure_data_no_side_effects():
    params = build_task_params(task_name="SentinelDailyRun", cadence="Daily", at="07:15")
    assert params == {
        "task_name": "SentinelDailyRun", "cadence": "Daily", "at": "07:15", "days_interval": None,
    }

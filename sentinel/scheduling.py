"""Pure argv/task-param builders for the Windows Task Scheduler tool.

No ``winreg``/``ctypes.windll`` import at module scope — this module
is importable and unit-testable on any platform, including
``ubuntu-latest`` CI. ``scripts/Sentinel-Schedule.ps1`` mirrors this
exact argument shape in PowerShell; the two are kept in sync by
comment cross-reference, not by generation.
"""

from __future__ import annotations

from pathlib import Path

ALLOWED_CADENCES: frozenset[str] = frozenset({"Daily", "EveryNDays", "Weekly", "GateBurst"})


def build_run_argv(
    *,
    python_exe: str,
    github_user: str,
    db_path: Path,
    findings_path: Path,
    log_path: Path,
    run_kind: str = "live",
) -> list[str]:
    """The exact argv the scheduled task action invokes. A list, never
    a shell string — command injection is structurally impossible."""
    if not python_exe:
        raise ValueError("python_exe must not be empty")
    if not github_user:
        raise ValueError("github_user must not be empty")
    for path in (db_path, findings_path, log_path):
        if not path.is_absolute():
            raise ValueError(f"{path} must be an absolute path for a scheduled run")
    return [
        python_exe,
        "-m",
        "sentinel",
        "run",
        "--run-kind",
        run_kind,
        "--source",
        "live",
        "--github-user",
        github_user,
        "--db",
        str(db_path),
        "--findings",
        str(findings_path),
        "--log",
        str(log_path),
    ]


def build_task_params(
    *,
    task_name: str,
    cadence: str,
    at: str | None = None,
    days_interval: int | None = None,
) -> dict[str, object]:
    """Pure data describing the scheduled task's trigger — never
    invokes schtasks/Register-ScheduledTask itself."""
    if not task_name or not task_name.strip():
        raise ValueError("task_name must not be empty")
    if cadence not in ALLOWED_CADENCES:
        raise ValueError(f"cadence must be one of {sorted(ALLOWED_CADENCES)}")
    if cadence == "EveryNDays" and not days_interval:
        raise ValueError("EveryNDays requires days_interval")
    return {
        "task_name": task_name,
        "cadence": cadence,
        "at": at,
        "days_interval": days_interval,
    }

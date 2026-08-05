<#
.SYNOPSIS
    Install/inspect/remove the local, current-user Windows Task Scheduler
    entry that runs Sentinel's live pipeline (BLUEPRINT §6 P2).

.DESCRIPTION
    Narrow, single-purpose scheduler tooling. No admin privileges required
    (registers under the current user, no elevation). Stores no password,
    token or secret: the task runs with -User $env:USERNAME and no
    -Password, producing an interactive-token task  -  it fires only while
    the operator is logged in, and Windows stores nothing on its behalf.

    The repository path is derived at install time from this script's own
    location ($PSScriptRoot/..)  -  never hardcoded into a tracked file.
    Runtime config (github_user, an optional python.exe override) comes
    from scripts/sentinel.local.json (gitignored; copy
    scripts/sentinel.local.example.json to create it).

    The exact argv this script constructs mirrors
    sentinel/scheduling.py::build_run_argv  -  that Python module is what
    pytest and CI actually exercise (no live Task Scheduler in CI); keep
    the two in sync by comment cross-reference, not by generation.

    Actions:
      Show      (default) - print the resolved command and current task info
      Install   - register or update the named task
      Remove    - unregister the named task (idempotent)
      Evidence  - print Get-ScheduledTaskInfo + recent Task Scheduler
                  operational-log entries for the named task (advisory
                  diagnostics only per C5  -  never a closure proof)

.PARAMETER WhatIfOnly
    Print the fully-resolved action/trigger/settings and exit without
    registering anything. Always run this before -Action Install.

.NOTES
    Authored and unit-tested (via sentinel/scheduling.py) only in this
    dispatch (q77-p2-c)  -  no task is registered, no gate-run evidence is
    gathered here. That is explicitly out of scope; see the plan §6/§12.
#>

[CmdletBinding(DefaultParameterSetName = 'Show')]
param(
    [ValidateSet('Install', 'Update', 'Show', 'Remove', 'Evidence')]
    [string] $Action = 'Show',

    [string] $TaskName = 'SentinelDailyRun',
    [string] $TaskPath = '\Sentinel\',

    [ValidateSet('Daily', 'EveryNDays', 'Weekly', 'GateBurst')]
    [string] $Cadence = 'Daily',
    [string] $At = '07:15',
    [int]    $DaysInterval = 1,
    [string] $WeeklyDay = 'Monday',

    # GateBurst only  -  a bounded temporary recurrence for the two-run
    # scheduler gate. Spacing must be chosen from the measured per-run
    # GitHub request count (60 req/hr unauthenticated): <=25 req/run keeps
    # 20-minute spacing safe; otherwise use ~65-minute spacing so the two
    # runs land in separate rate-limit windows.
    [int] $BurstIntervalMinutes = 20,
    [int] $BurstDurationMinutes = 25,
    [int] $BurstStartDelayMinutes = 3,

    [string] $PythonExe,
    [string] $ConfigPath,
    [string] $RunKind = 'live',

    [switch] $WhatIfOnly
)

$ErrorActionPreference = 'Stop'

# $PSScriptRoot is not reliably populated inside a param() default-value
# expression in Windows PowerShell 5.1 (it's a top-level-scope automatic
# variable) -- resolved here in the script body instead, where it always is.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $PSScriptRoot 'sentinel.local.json'
}

if (-not (Test-Path $ConfigPath)) {
    throw "Missing $ConfigPath  -  copy scripts/sentinel.local.example.json to sentinel.local.json and fill in github_user."
}
$LocalConfig = Get-Content $ConfigPath -Raw | ConvertFrom-Json
if (-not $LocalConfig.github_user) {
    throw "sentinel.local.json must set github_user"
}
if (-not $PythonExe) {
    if ($LocalConfig.python_exe) {
        $PythonExe = $LocalConfig.python_exe
    } else {
        $PythonExe = (Get-Command python -ErrorAction Stop).Source
    }
}

$DbPath = Join-Path $RepoRoot 'var\sentinel.sqlite3'
$FindingsPath = Join-Path $RepoRoot 'FINDINGS.md'
# Every scheduled task (GateBurst and the standing schedule alike) uses
# this one path -- it is what separates scheduled-run provenance from a
# manual/measurement run's sentinel-manual.jsonl, per the gate's
# provenance chain design. Never a burst-specific variant.
$LogPath = Join-Path $RepoRoot 'var\logs\sentinel-scheduled.jsonl'
$CostLedgerPath = Join-Path $RepoRoot 'telemetry\cost_ledger.jsonl'

# Mirrors sentinel/scheduling.py::build_run_argv exactly.
$ArgList = @(
    '-m', 'sentinel', 'run',
    '--run-kind', $RunKind,
    '--source', 'live',
    '--github-user', $LocalConfig.github_user,
    '--db', $DbPath,
    '--findings', $FindingsPath,
    '--log', $LogPath,
    '--cost-ledger', $CostLedgerPath
)
$ArgLine = ($ArgList | ForEach-Object { if ($_ -match '\s') { "`"$_`"" } else { $_ } }) -join ' '

function Show-ResolvedCommand {
    Write-Host "Resolved scheduled command:"
    Write-Host "  $PythonExe $ArgLine"
    Write-Host "  WorkingDirectory: $RepoRoot"
    Write-Host "  TaskName: $TaskPath$TaskName"
    Write-Host "  Cadence: $Cadence  At: $At  DaysInterval: $DaysInterval"
}

switch ($Action) {
    { $_ -in 'Install', 'Update' } {
        Show-ResolvedCommand
        if ($WhatIfOnly) { return }

        $CommonParams = @{
            Execute          = $PythonExe
            Argument         = $ArgLine
            WorkingDirectory = $RepoRoot
        }
        $ScheduledAction = New-ScheduledTaskAction @CommonParams

        switch ($Cadence) {
            'Daily'      { $Trigger = New-ScheduledTaskTrigger -Daily -At $At }
            'EveryNDays' { $Trigger = New-ScheduledTaskTrigger -Daily -DaysInterval $DaysInterval -At $At }
            'Weekly'     { $Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $WeeklyDay -At $At }
            'GateBurst'  {
                $Start = (Get-Date).AddMinutes($BurstStartDelayMinutes)
                $Trigger = New-ScheduledTaskTrigger -Once -At $Start `
                    -RepetitionInterval (New-TimeSpan -Minutes $BurstIntervalMinutes) `
                    -RepetitionDuration (New-TimeSpan -Minutes $BurstDurationMinutes)
            }
        }

        $Settings = New-ScheduledTaskSettingsSet `
            -MultipleInstances IgnoreNew `
            -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
            -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
            -RestartCount 0

        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false -ErrorAction SilentlyContinue

        Register-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath `
            -Action $ScheduledAction -Trigger $Trigger -Settings $Settings `
            -Description 'ai-portfolio-sentinel scheduled live run (read-only, no credentials).' `
            -User $env:USERNAME | Out-Null

        Write-Host "Registered $TaskPath$TaskName."
        Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath | Format-List TaskName, TaskPath, State
    }

    'Show' {
        Show-ResolvedCommand
        $Task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
        if (-not $Task) {
            Write-Host "No task registered at $TaskPath$TaskName."
            return
        }
        $Task | Format-List TaskName, TaskPath, State
        $Task.Actions | Format-List *
        $Task.Triggers | Format-List *
        Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath $TaskPath | Format-List *
    }

    'Remove' {
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "Removed (or already absent): $TaskPath$TaskName"
        Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue | Format-Table TaskName, State
    }

    'Evidence' {
        # Advisory diagnostics only (C5)  -  never a closure proof. Actual
        # provenance is the ledger correlation chain: task identity +
        # resolved command, LastRunTime/LastTaskResult, scheduled-log
        # entries, matching ledger run_id/UTC timestamps, zero-token
        # CostRows, and no manual invocation between scheduled fires.
        Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath $TaskPath |
            Format-List TaskName, LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns
        try {
            Get-WinEvent -LogName 'Microsoft-Windows-TaskScheduler/Operational' -MaxEvents 200 -ErrorAction Stop |
                Where-Object { $_.Message -match [regex]::Escape($TaskName) } |
                Select-Object TimeCreated, Id, LevelDisplayName, Message | Format-List
        } catch {
            Write-Host "Task Scheduler operational log unavailable  -  relying on the ledger correlation chain instead."
        }
    }
}

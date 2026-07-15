[CmdletBinding()]
param(
    [ValidateSet("Plan", "Install", "Status", "Run", "Uninstall")]
    [string]$Mode = "Status",
    [string]$TaskName = "AI Token Tracker Claude Import",
    [int]$IntervalMinutes = 60
)

# Periodically import REAL local Claude Code usage into the running collector. The import is
# idempotent (store de-duplicates by deterministic event_id), so re-running every hour only
# ever adds genuinely new assistant turns and never double-counts. At-logon and at-startup
# triggers plus StartWhenAvailable let it catch up after the machine was off or asleep.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir "..")).Path
$runner = (Resolve-Path (Join-Path $scriptDir "tt-claude-import.cmd")).Path
$store = if ($env:TRACKER_STORE) { $env:TRACKER_STORE } else { "C:\ai-token-tracker-data\collector_events.jsonl" }
$runtimeDir = Split-Path -Parent $store
$logDir = Join-Path $runtimeDir "health"
$taskLog = Join-Path $logDir "claude-import.log"

$plan = [ordered]@{
    task_name = $TaskName
    runner = $runner
    source_root = $root
    working_directory = $runtimeDir
    triggers = @("at_startup", "at_logon", "every_${IntervalMinutes}_minutes")
    start_when_available = $true
    interval_minutes = $IntervalMinutes
    task_log = $taskLog
}

function Write-ImportTaskStatus {
    $task = $null
    try { $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop } catch {}
    $info = if ($task) { Get-ScheduledTaskInfo -TaskName $TaskName } else { $null }
    [ordered]@{
        task_name = $TaskName
        installed = [bool]$task
        task_state = if ($task) { [string]$task.State } else { "NotInstalled" }
        last_run_time = if ($info) { $info.LastRunTime } else { $null }
        next_run_time = if ($info) { $info.NextRunTime } else { $null }
        last_task_result = if ($info) { $info.LastTaskResult } else { $null }
        task_log = $taskLog
    } | ConvertTo-Json -Depth 4
}

if ($Mode -eq "Plan") { $plan | ConvertTo-Json -Depth 4; exit 0 }

if ($Mode -eq "Install") {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $cmd = "$env:SystemRoot\System32\cmd.exe"
    $arguments = "/c `"`"$runner`" >> `"$taskLog`" 2>&1`""
    $action = New-ScheduledTaskAction -Execute $cmd -Argument $arguments -WorkingDirectory $runtimeDir
    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    # No AtStartup trigger: it runs in the pre-logon system context and a standard (non-admin)
    # user is denied registering it. This task only makes sense while the user is logged in
    # (it reads ~/.claude and posts to the loopback collector), so at-logon plus an interval
    # repetition, with StartWhenAvailable to catch missed runs, is sufficient and installs
    # without elevation.
    $triggers = @(
        (New-ScheduledTaskTrigger -AtLogOn -User $userId),
        (New-ScheduledTaskTrigger `
            -Once `
            -At (Get-Date).AddMinutes(2) `
            -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes))
    )
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $triggers `
        -Principal $principal `
        -Settings $settings `
        -Description "Idempotent hourly import of local Claude Code usage into the AI token collector" `
        -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    Write-ImportTaskStatus
    exit 0
}

if ($Mode -eq "Run") {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    Write-ImportTaskStatus
    exit 0
}

if ($Mode -eq "Uninstall") {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-ImportTaskStatus
    exit 0
}

Write-ImportTaskStatus

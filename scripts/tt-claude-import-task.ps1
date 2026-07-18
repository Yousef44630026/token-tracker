[CmdletBinding()]
param(
    [ValidateSet("Plan", "Install", "Status", "Run", "Uninstall")]
    [string]$Mode = "Status",
    [string]$TaskName = "AI Token Tracker Claude Import",
    [int]$IntervalMinutes = 60
)

# Periodically import REAL local Claude Code usage into the running collector. The import is
# incremental and idempotent (atomic byte checkpoint + deterministic event_id), so hourly
# runs scan only appended transcript bytes and crash replay cannot double-count. At-logon and
# periodic triggers plus StartWhenAvailable catch up after the machine was off or asleep.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir "..")).Path
$runner = (Resolve-Path (Join-Path $scriptDir "tt-claude-import.cmd")).Path
$taskRunner = (Resolve-Path (Join-Path $scriptDir "tt-claude-import-task-run.ps1")).Path
$store = if ($env:TRACKER_STORE) { $env:TRACKER_STORE } else { "C:\ai-token-tracker-data\collector_events.jsonl" }
$runtimeDir = Split-Path -Parent $store
$authTokenFile = if ($env:TRACKER_AUTH_TOKEN_FILE) {
    $env:TRACKER_AUTH_TOKEN_FILE
} else {
    Join-Path (Join-Path $runtimeDir "config") "collector-auth.token"
}
$logDir = Join-Path $runtimeDir "health"
$taskLog = Join-Path $logDir "claude-import.log"
$stateFile = if ($env:TRACKER_CLAUDE_IMPORT_STATE) { $env:TRACKER_CLAUDE_IMPORT_STATE } else { Join-Path $logDir "claude-import-state.json" }

$plan = [ordered]@{
    task_name = $TaskName
    runner = $runner
    task_runner = $taskRunner
    source_root = $root
    working_directory = $runtimeDir
    triggers = @("at_logon", "every_${IntervalMinutes}_minutes")
    start_when_available = $true
    dont_stop_on_idle_end = $true
    interval_minutes = $IntervalMinutes
    task_log = $taskLog
    state_file = $stateFile
    auth_token_file = $authTokenFile
}

function Write-ImportTaskStatus {
    $task = $null
    $inspectionError = $null
    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    } catch [Microsoft.Management.Infrastructure.CimException] {
        $inspectionError = $_.Exception.GetType().Name
    } catch {
        if ($_.FullyQualifiedErrorId -notlike "*NoMatchingMSFT_ScheduledTask*") {
            $inspectionError = $_.Exception.GetType().Name
        }
    }
    $info = if ($task) { Get-ScheduledTaskInfo -TaskName $TaskName } else { $null }
    [ordered]@{
        task_name = $TaskName
        installed = if ($inspectionError) { $null } else { [bool]$task }
        task_state = if ($task) { [string]$task.State } elseif ($inspectionError) { "Unknown" } else { "NotInstalled" }
        inspection_error = $inspectionError
        last_run_time = if ($info) { $info.LastRunTime } else { $null }
        next_run_time = if ($info) { $info.NextRunTime } else { $null }
        last_task_result = if ($info) { $info.LastTaskResult } else { $null }
        task_log = $taskLog
        state_file = $stateFile
    } | ConvertTo-Json -Depth 4
}

if ($Mode -eq "Plan") { $plan | ConvertTo-Json -Depth 4; exit 0 }

if ($Mode -eq "Install") {
    if (-not (Test-Path -LiteralPath $authTokenFile -PathType Leaf)) {
        throw "Collector auth is not configured. Run scripts\tt-local-auth.ps1 -Mode Configure first."
    }
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = (
        "-NoProfile -NonInteractive -ExecutionPolicy Bypass " +
        "-File `"$taskRunner`" -TaskLog `"$taskLog`" -StateFile `"$stateFile`" " +
        "-AuthTokenFile `"$authTokenFile`""
    )
    $action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments -WorkingDirectory $runtimeDir
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
        -DontStopOnIdleEnd `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $triggers `
        -Principal $principal `
        -Settings $settings `
        -Description "Incremental hourly import of local Claude Code usage into the AI token collector" `
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

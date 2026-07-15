[CmdletBinding()]
param(
    [ValidateSet("Plan", "Install", "Status", "Run", "Uninstall")]
    [string]$Mode = "Status",
    [string]$TaskName = "AI Token Tracker Monitor",
    [string]$CollectorTaskName = "AI Token Tracker Collector"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir "..")).Path
$runner = (Resolve-Path (Join-Path $scriptDir "tt-collector-monitor.cmd")).Path
$taskRunner = (Resolve-Path (Join-Path $scriptDir "tt-collector-monitor-task-run.ps1")).Path
$store = if ($env:TRACKER_STORE) { $env:TRACKER_STORE } else { "C:\ai-token-tracker-data\collector_events.jsonl" }
$healthDir = Join-Path (Split-Path -Parent $store) "health"
$healthLog = if ($env:TRACKER_HEALTH_LOG) { $env:TRACKER_HEALTH_LOG } else { Join-Path $healthDir "collector-health.jsonl" }
$alertLog = if ($env:TRACKER_ALERT_LOG) { $env:TRACKER_ALERT_LOG } else { Join-Path $healthDir "collector-alerts.jsonl" }
$taskLog = Join-Path $healthDir "collector-monitor-launcher.log"
$runtimeDir = Split-Path -Parent $store
$recoveryDelaySeconds = 15

$plan = [ordered]@{
    task_name = $TaskName
    runner = $runner
    task_runner = $taskRunner
    source_root = $root
    working_directory = $runtimeDir
    triggers = @("at_startup", "at_logon", "every_minute")
    start_when_available = $true
    dont_stop_on_idle_end = $true
    interval_seconds = 60
    collector_task_name = $CollectorTaskName
    recovery_delay_seconds = $recoveryDelaySeconds
    health_log = $healthLog
    alert_log = $alertLog
    task_log = $taskLog
}

function Write-MonitorTaskStatus {
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
    $taskInfo = if ($task) { Get-ScheduledTaskInfo -TaskName $TaskName } else { $null }
    [ordered]@{
        task_name = $TaskName
        installed = if ($inspectionError) { $null } else { [bool]$task }
        task_state = if ($task) { [string]$task.State } elseif ($inspectionError) { "Unknown" } else { "NotInstalled" }
        inspection_error = $inspectionError
        last_run_time = if ($taskInfo) { $taskInfo.LastRunTime } else { $null }
        next_run_time = if ($taskInfo) { $taskInfo.NextRunTime } else { $null }
        last_task_result = if ($taskInfo) { $taskInfo.LastTaskResult } else { $null }
        health_log = $healthLog
        alert_log = $alertLog
    } | ConvertTo-Json -Depth 4
}

if ($Mode -eq "Plan") {
    $plan | ConvertTo-Json -Depth 4
    exit 0
}

if ($Mode -eq "Install") {
    New-Item -ItemType Directory -Force -Path $healthDir | Out-Null
    $powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = (
        "-NoProfile -NonInteractive -ExecutionPolicy Bypass " +
        "-File `"$taskRunner`" " +
        "-HealthLog `"$healthLog`" -AlertLog `"$alertLog`" -TaskLog `"$taskLog`" " +
        "-CollectorTaskName `"$CollectorTaskName`" -RecoveryDelaySeconds $recoveryDelaySeconds"
    )
    $action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments -WorkingDirectory $runtimeDir
    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $triggers = @(
        (New-ScheduledTaskTrigger -AtStartup),
        (New-ScheduledTaskTrigger -AtLogOn -User $userId),
        (New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 1))
    )
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -DontStopOnIdleEnd `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $triggers `
        -Principal $principal `
        -Settings $settings `
        -Description "Periodic health and downtime evidence for the AI token collector" `
        -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
    Write-MonitorTaskStatus
    exit 0
}

if ($Mode -eq "Run") {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
    Write-MonitorTaskStatus
    exit 0
}

if ($Mode -eq "Uninstall") {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-MonitorTaskStatus
    exit 0
}

Write-MonitorTaskStatus

[CmdletBinding()]
param(
    [ValidateSet("Plan", "Install", "Status", "Run", "Uninstall")]
    [string]$Mode = "Status",
    [string]$TaskName = "AI Token Tracker Dashboard Refresh",
    [int]$IntervalMinutes = 60
)

# Refresh the presentation-only workbook on logon and every hour. StartWhenAvailable catches
# a missed run after sleep or shutdown. The runner publishes the workbook and its evidence
# atomically, so readers keep the previous known-good file when a refresh fails.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir "..")).Path
$taskRunner = (Resolve-Path (Join-Path $scriptDir "tt-dashboard-task-run.ps1")).Path
$store = if ($env:TRACKER_STORE) { $env:TRACKER_STORE } else { "C:\ai-token-tracker-data\collector_events.jsonl" }
$runtimeDir = Split-Path -Parent $store
$dataDir = if ($env:TRACKER_DASHBOARD_DATA_DIR) { $env:TRACKER_DASHBOARD_DATA_DIR } else { $runtimeDir }
$outputFile = if ($env:TRACKER_DASHBOARD_OUTPUT) { $env:TRACKER_DASHBOARD_OUTPUT } else { Join-Path $runtimeDir "dashboard.xlsx" }
$prices = if ($env:TRACKER_DASHBOARD_PRICES) { $env:TRACKER_DASHBOARD_PRICES } else { "" }
$healthDir = Join-Path $runtimeDir "health"
$taskLog = Join-Path $healthDir "dashboard-refresh.log"
$evidenceFile = if ($env:TRACKER_DASHBOARD_EVIDENCE) {
    $env:TRACKER_DASHBOARD_EVIDENCE
} else {
    Join-Path $healthDir "dashboard-refresh.json"
}

$plan = [ordered]@{
    task_name = $TaskName
    task_runner = $taskRunner
    source_root = $root
    working_directory = $runtimeDir
    triggers = @("at_logon", "every_${IntervalMinutes}_minutes")
    start_when_available = $true
    dont_stop_on_idle_end = $true
    interval_minutes = $IntervalMinutes
    restart_interval_seconds = 120
    restart_count = 3
    data_directory = $dataDir
    output_file = $outputFile
    prices_configured = [bool]$prices
    task_log = $taskLog
    evidence_file = $evidenceFile
}

function Write-DashboardTaskStatus {
    param([switch]$Strict)
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
    $evidence = $null
    $evidenceError = $null
    if (Test-Path -LiteralPath $evidenceFile -PathType Leaf) {
        try {
            $evidence = Get-Content -LiteralPath $evidenceFile -Raw | ConvertFrom-Json
        } catch {
            $evidenceError = $_.Exception.GetType().Name
        }
    }
    $refreshAgeSeconds = if ($evidence -and $evidence.timestamp) {
        [math]::Max(0, ((Get-Date).ToUniversalTime() - ([datetime]$evidence.timestamp).ToUniversalTime()).TotalSeconds)
    } else {
        $null
    }
    $maxAgeSeconds = [math]::Max(300, $IntervalMinutes * 180)
    $taskResultOk = $info -and $info.LastTaskResult -in @(0, 267009)
    $statusOk = (
        -not $inspectionError -and
        [bool]$task -and
        [string]$task.State -in @("Ready", "Running") -and
        $taskResultOk -and
        -not $evidenceError -and
        $evidence.status -eq "ok" -and
        $evidence.exit_code -eq 0 -and
        $refreshAgeSeconds -ne $null -and
        $refreshAgeSeconds -le $maxAgeSeconds
    )
    $failureReason = if ($inspectionError) {
        "task_inspection_failed"
    } elseif (-not $task) {
        "task_not_installed"
    } elseif ([string]$task.State -notin @("Ready", "Running")) {
        "dashboard_task_disabled_or_stopped"
    } elseif (-not $taskResultOk) {
        "dashboard_last_run_failed"
    } elseif ($evidenceError) {
        "dashboard_evidence_unreadable"
    } elseif (-not $evidence) {
        "dashboard_evidence_missing"
    } elseif ($evidence.status -ne "ok" -or $evidence.exit_code -ne 0) {
        "dashboard_refresh_failed"
    } elseif ($refreshAgeSeconds -gt $maxAgeSeconds) {
        "dashboard_evidence_stale"
    } else {
        $null
    }
    [ordered]@{
        task_name = $TaskName
        installed = if ($inspectionError) { $null } else { [bool]$task }
        task_state = if ($task) { [string]$task.State } elseif ($inspectionError) { "Unknown" } else { "NotInstalled" }
        inspection_error = $inspectionError
        last_run_time = if ($info) { $info.LastRunTime } else { $null }
        next_run_time = if ($info) { $info.NextRunTime } else { $null }
        last_task_result = if ($info) { $info.LastTaskResult } else { $null }
        status_ok = $statusOk
        failure_reason = $failureReason
        refresh_age_seconds = if ($refreshAgeSeconds -ne $null) { [math]::Round($refreshAgeSeconds, 1) } else { $null }
        max_age_seconds = $maxAgeSeconds
        task_log = $taskLog
        evidence_file = $evidenceFile
        evidence_error = $evidenceError
        refresh = $evidence
    } | ConvertTo-Json -Depth 6
    if ($Strict -and -not $statusOk) { exit 1 }
}

if ($Mode -eq "Plan") { $plan | ConvertTo-Json -Depth 4; exit 0 }

if ($Mode -eq "Install") {
    if ($IntervalMinutes -le 0) { throw "IntervalMinutes must be positive" }
    New-Item -ItemType Directory -Force -Path $healthDir | Out-Null
    $powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = (
        "-NoProfile -NonInteractive -ExecutionPolicy Bypass " +
        "-File `"$taskRunner`" -TaskLog `"$taskLog`" -EvidenceFile `"$evidenceFile`" " +
        "-DataDir `"$dataDir`" -OutputFile `"$outputFile`""
    )
    if ($prices) {
        $arguments += " -Prices `"$prices`""
    }
    $action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments -WorkingDirectory $runtimeDir
    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
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
        -RestartInterval (New-TimeSpan -Minutes 2) `
        -RestartCount 3 `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $triggers `
        -Principal $principal `
        -Settings $settings `
        -Description "Hourly atomic refresh of the AI Token Tracker Excel dashboard" `
        -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    Write-DashboardTaskStatus
    exit 0
}

if ($Mode -eq "Run") {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    Write-DashboardTaskStatus
    exit 0
}

if ($Mode -eq "Uninstall") {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-DashboardTaskStatus
    exit 0
}

Write-DashboardTaskStatus -Strict

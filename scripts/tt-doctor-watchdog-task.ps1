[CmdletBinding()]
param(
    [ValidateSet("Plan", "Install", "Status", "Run", "Uninstall")]
    [string]$Mode = "Status",
    [string]$TaskName = "AI Token Tracker Doctor Watchdog",
    [ValidateRange(15, 1440)]
    [int]$IntervalMinutes = 60
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir "..")).Path
$taskRunner = (Resolve-Path (Join-Path $scriptDir "tt-doctor-watchdog-task-run.ps1")).Path
$store = if ($env:TRACKER_STORE) { $env:TRACKER_STORE } else { "C:\ai-token-tracker-data\collector_events.jsonl" }
$runtimeDir = Split-Path -Parent $store
$healthDir = Join-Path $runtimeDir "health"
$evidenceFile = Join-Path $healthDir "doctor-watchdog.json"
$taskLog = Join-Path $healthDir "doctor-watchdog.jsonl"
$alertLog = Join-Path $healthDir "doctor-alerts.jsonl"
$authTokenFile = if ($env:TRACKER_AUTH_TOKEN_FILE) {
    $env:TRACKER_AUTH_TOKEN_FILE
} else {
    Join-Path (Join-Path $runtimeDir "config") "collector-auth.token"
}

$plan = [ordered]@{
    task_name = $TaskName
    task_runner = $taskRunner
    source_root = $root
    working_directory = $runtimeDir
    store = $store
    triggers = @("at_logon", "every_${IntervalMinutes}_minutes")
    interval_minutes = $IntervalMinutes
    start_when_available = $true
    dont_stop_on_idle_end = $true
    strict_warnings = $true
    evidence_file = $evidenceFile
    task_log = $taskLog
    alert_log = $alertLog
    auth_token_file = $authTokenFile
}

function Write-WatchdogStatus {
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
    if (Test-Path -LiteralPath $evidenceFile) {
        try { $evidence = Get-Content -LiteralPath $evidenceFile -Raw | ConvertFrom-Json }
        catch { $evidenceError = $_.Exception.GetType().Name }
    }
    [ordered]@{
        task_name = $TaskName
        installed = if ($inspectionError) { $null } else { [bool]$task }
        task_state = if ($task) { [string]$task.State } elseif ($inspectionError) { "Unknown" } else { "NotInstalled" }
        inspection_error = $inspectionError
        last_run_time = if ($info) { $info.LastRunTime } else { $null }
        next_run_time = if ($info) { $info.NextRunTime } else { $null }
        last_task_result = if ($info) { $info.LastTaskResult } else { $null }
        evidence_file = $evidenceFile
        evidence_error = $evidenceError
        evidence = $evidence
        alert_log = $alertLog
    } | ConvertTo-Json -Depth 6
}

if ($Mode -eq "Plan") { $plan | ConvertTo-Json -Depth 4; exit 0 }

if ($Mode -eq "Install") {
    if (-not (Test-Path -LiteralPath $authTokenFile -PathType Leaf)) {
        throw "Collector auth is not configured. Run scripts\tt-local-auth.ps1 -Mode Configure first."
    }
    New-Item -ItemType Directory -Force -Path $healthDir | Out-Null
    $powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = (
        "-NoProfile -NonInteractive -ExecutionPolicy Bypass " +
        "-File `"$taskRunner`" -Store `"$store`" -EvidenceFile `"$evidenceFile`" " +
        "-TaskLog `"$taskLog`" -AlertLog `"$alertLog`" -AuthTokenFile `"$authTokenFile`" -StrictWarnings"
    )
    $action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments -WorkingDirectory $runtimeDir
    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $triggers = @(
        (New-ScheduledTaskTrigger -AtLogOn -User $userId),
        (New-ScheduledTaskTrigger `
            -Once `
            -At (Get-Date).AddMinutes(3) `
            -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes))
    )
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -DontStopOnIdleEnd `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $triggers `
        -Principal $principal `
        -Settings $settings `
        -Description "Periodic strict Doctor gate for collector, import, dashboard, and storage evidence" `
        -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    Write-WatchdogStatus
    exit 0
}

if ($Mode -eq "Run") {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    Write-WatchdogStatus
    exit 0
}

if ($Mode -eq "Uninstall") {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-WatchdogStatus
    exit 0
}

Write-WatchdogStatus

[CmdletBinding()]
param(
    [ValidateSet("Plan", "Install", "Status", "Start", "Stop", "Uninstall")]
    [string]$Mode = "Status",
    [string]$TaskName = "AI Token Tracker Collector"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir "..")).Path
$runner = (Resolve-Path (Join-Path $scriptDir "tt-collector-run.cmd")).Path
$store = if ($env:TRACKER_STORE) { $env:TRACKER_STORE } else { "C:\ai-token-tracker-data\collector_events.jsonl" }
$hostAddress = if ($env:TRACKER_HOST) { $env:TRACKER_HOST } else { "127.0.0.1" }
$port = if ($env:TRACKER_PORT) { [int]$env:TRACKER_PORT } else { 8787 }
$logDir = Join-Path (Split-Path -Parent $store) "logs"
$logPath = Join-Path $logDir "collector-service.log"
$durable = if ($env:TRACKER_DURABLE) {
    $env:TRACKER_DURABLE.Trim().ToLowerInvariant() -in @("1", "true", "yes", "on")
} else {
    $true
}

$plan = [ordered]@{
    task_name = $TaskName
    runner = $runner
    working_directory = $root
    store = $store
    host = $hostAddress
    port = $port
    durable = $durable
    log = $logPath
    trigger = "at_logon"
    restart_interval_seconds = 60
    restart_count = 10
}

function Get-CollectorHealth {
    $baseUri = "http://${hostAddress}:$port"
    try {
        $health = Invoke-RestMethod -Uri "$baseUri/healthz" -TimeoutSec 3
        $stats = Invoke-RestMethod -Uri "$baseUri/v1/stats" -TimeoutSec 3
        return [ordered]@{
            reachable = $true
            status = $health.status
            events = $stats.events
            total = $stats.total
        }
    } catch {
        return [ordered]@{
            reachable = $false
            status = "offline"
            error_type = $_.Exception.GetType().Name
        }
    }
}

function Write-TaskStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $taskInfo = if ($task) { Get-ScheduledTaskInfo -TaskName $TaskName } else { $null }
    [ordered]@{
        task_name = $TaskName
        installed = [bool]$task
        task_state = if ($task) { [string]$task.State } else { "NotInstalled" }
        last_run_time = if ($taskInfo) { $taskInfo.LastRunTime } else { $null }
        last_task_result = if ($taskInfo) { $taskInfo.LastTaskResult } else { $null }
        health = Get-CollectorHealth
        store = $store
        log = $logPath
    } | ConvertTo-Json -Depth 5
}

if ($Mode -eq "Plan") {
    $plan | ConvertTo-Json -Depth 4
    exit 0
}

if ($Mode -eq "Install") {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $taskCommand = "`"$runner`" >> `"$logPath`" 2>&1"
    $arguments = "/d /c `"$taskCommand`""
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument $arguments -WorkingDirectory $root
    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 10 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Loopback AI token collector with durable JSONL persistence" `
        -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
    Write-TaskStatus
    exit 0
}

if ($Mode -eq "Start") {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
    Write-TaskStatus
    exit 0
}

if ($Mode -eq "Stop") {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
    Write-TaskStatus
    exit 0
}

if ($Mode -eq "Uninstall") {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-TaskStatus
    exit 0
}

Write-TaskStatus

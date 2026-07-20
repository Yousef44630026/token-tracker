[CmdletBinding()]
param(
    [ValidateSet("Plan", "Install", "Status", "Run", "Uninstall")]
    [string]$Mode = "Status",
    [string]$TaskName = "AI Token Tracker Backup",
    [int]$IntervalMinutes = 720
)

# Verified, archive-inclusive ledger backup on a schedule. The live ledger lives on a
# non-synced local volume; this copies a verified logical snapshot (active + every archive
# segment) INTO a synced OneDrive folder, so a second, off-machine copy exists. No AtStartup
# trigger (a standard, non-admin user cannot register one); at-logon plus an interval, with
# StartWhenAvailable, is sufficient and installs without elevation.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = (Resolve-Path (Join-Path $scriptDir "tt-backup.cmd")).Path
$store = if ($env:TRACKER_STORE) { $env:TRACKER_STORE } else { "C:\ai-token-tracker-data\collector_events.jsonl" }
$runtimeDir = Split-Path -Parent $store
$logDir = Join-Path $runtimeDir "health"
$taskLog = Join-Path $logDir "backup.log"

# Off-machine destination: prefer a OneDrive-synced folder so the copy leaves the disk.
$oneDrive = $env:OneDrive
if (-not $oneDrive) { $oneDrive = $env:OneDriveCommercial }
$destDir = if ($env:TRACKER_BACKUP_DIR) {
    $env:TRACKER_BACKUP_DIR
} elseif ($oneDrive) {
    Join-Path $oneDrive "ai-token-tracker-backups"
} else {
    Join-Path $runtimeDir "backups"
}

$plan = [ordered]@{
    task_name = $TaskName
    runner = $runner
    dest_dir = $destDir
    off_machine = [bool]$oneDrive
    triggers = @("at_logon", "every_${IntervalMinutes}_minutes")
    interval_minutes = $IntervalMinutes
    task_log = $taskLog
}

function Write-BackupTaskStatus {
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
        dest_dir = $destDir
        task_log = $taskLog
    } | ConvertTo-Json -Depth 4
}

if ($Mode -eq "Plan") { $plan | ConvertTo-Json -Depth 4; exit 0 }

if ($Mode -eq "Install") {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    $cmd = "$env:SystemRoot\System32\cmd.exe"
    $arguments = "/c `"`"$runner`" --dest `"$destDir`" >> `"$taskLog`" 2>&1`""
    $action = New-ScheduledTaskAction -Execute $cmd -Argument $arguments -WorkingDirectory $runtimeDir
    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $triggers = @(
        (New-ScheduledTaskTrigger -AtLogOn -User $userId),
        (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes))
    )
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings `
        -Description "Verified archive-inclusive off-machine backup of the AI token ledger" -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 4
    Write-BackupTaskStatus
    exit 0
}

if ($Mode -eq "Run") { Start-ScheduledTask -TaskName $TaskName; Start-Sleep -Seconds 4; Write-BackupTaskStatus; exit 0 }

if ($Mode -eq "Uninstall") {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-BackupTaskStatus
    exit 0
}

Write-BackupTaskStatus

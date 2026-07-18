[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$HealthLog,
    [Parameter(Mandatory = $true)]
    [string]$AlertLog,
    [Parameter(Mandatory = $true)]
    [string]$TaskLog,
    [string]$AuthTokenFile,
    [string]$CollectorTaskName = "AI Token Tracker Collector",
    [ValidateRange(1, 300)]
    [int]$RecoveryDelaySeconds = 15
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptDir "tt-collector-monitor.cmd"
$logDir = Split-Path -Parent $TaskLog
if (-not $AuthTokenFile) {
    $AuthTokenFile = Join-Path (Join-Path (Split-Path -Parent $logDir) "config") "collector-auth.token"
}
$env:TRACKER_AUTH_TOKEN_FILE = $AuthTokenFile

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-LauncherEvent {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Payload
    )
    $Payload["timestamp"] = [DateTime]::UtcNow.ToString("o")
    Add-Content -LiteralPath $TaskLog -Value ($Payload | ConvertTo-Json -Compress) -Encoding UTF8
}

function Invoke-CollectorProbe {
    & $runner --health-log $HealthLog --alert-log $AlertLog --json 2>&1 | ForEach-Object {
        Add-Content -LiteralPath $TaskLog -Value ([string]$_) -Encoding UTF8
    }
    return [int]$LASTEXITCODE
}

try {
    $exitCode = Invoke-CollectorProbe
    if ($exitCode -ne 0) {
        Write-LauncherEvent @{
            action = "start_collector_task"
            collector_task_name = $CollectorTaskName
            initial_probe_exit_code = $exitCode
        }
        Start-ScheduledTask -TaskName $CollectorTaskName -ErrorAction Stop
        Start-Sleep -Seconds $RecoveryDelaySeconds
        $exitCode = Invoke-CollectorProbe
        Write-LauncherEvent @{
            action = "collector_recovery_result"
            collector_task_name = $CollectorTaskName
            recovery_probe_exit_code = $exitCode
        }
    }
} catch {
    Write-LauncherEvent @{
        action = "collector_recovery_failure"
        collector_task_name = $CollectorTaskName
        error_type = $_.Exception.GetType().Name
    }
    $exitCode = 1
}

exit $exitCode

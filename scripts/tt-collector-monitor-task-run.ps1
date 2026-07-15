[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$HealthLog,
    [Parameter(Mandatory = $true)]
    [string]$AlertLog,
    [Parameter(Mandatory = $true)]
    [string]$TaskLog
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptDir "tt-collector-monitor.cmd"
$logDir = Split-Path -Parent $TaskLog

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

try {
    & $runner --health-log $HealthLog --alert-log $AlertLog --json 2>&1 | ForEach-Object {
        Add-Content -LiteralPath $TaskLog -Value ([string]$_) -Encoding UTF8
    }
    $exitCode = $LASTEXITCODE
} catch {
    Add-Content `
        -LiteralPath $TaskLog `
        -Value ("collector monitor launcher failure: " + $_.Exception.GetType().Name) `
        -Encoding UTF8
    $exitCode = 1
}

exit $exitCode

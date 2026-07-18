[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Store,
    [Parameter(Mandatory = $true)]
    [string]$EvidenceFile,
    [Parameter(Mandatory = $true)]
    [string]$TaskLog,
    [Parameter(Mandatory = $true)]
    [string]$AlertLog,
    [Parameter(Mandatory = $true)]
    [string]$AuthTokenFile,
    [switch]$StrictWarnings
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir "..")).Path
$python = Join-Path $scriptDir "_python.cmd"
$env:TRACKER_STORE = $Store
$env:TRACKER_AUTH_TOKEN_FILE = $AuthTokenFile

try {
    $arguments = @(
        "-m", "tracker.ops.doctor_watchdog",
        "--store", $Store,
        "--evidence-file", $EvidenceFile,
        "--task-log", $TaskLog,
        "--alert-log", $AlertLog,
        "--secret-scan-root", $root
    )
    if ($StrictWarnings) { $arguments += "--strict-warnings" }
    & $python @arguments
    $exitCode = [int]$LASTEXITCODE
} catch {
    $failure = [ordered]@{
        timestamp = [DateTime]::UtcNow.ToString("o")
        status = "launcher_failure"
        error_type = $_.Exception.GetType().Name
    } | ConvertTo-Json -Compress
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $TaskLog) | Out-Null
    Add-Content -LiteralPath $TaskLog -Value $failure -Encoding UTF8
    Add-Content -LiteralPath $AlertLog -Value $failure -Encoding UTF8
    $exitCode = 1
}

exit $exitCode

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$LogPath,
    [string]$AuthTokenFile
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptDir "tt-collector-run.cmd"
$logDir = Split-Path -Parent $LogPath
if (-not $AuthTokenFile) {
    $AuthTokenFile = Join-Path (Join-Path (Split-Path -Parent $logDir) "config") "collector-auth.token"
}
$env:TRACKER_AUTH_TOKEN_FILE = $AuthTokenFile

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

try {
    & $runner 2>&1 | ForEach-Object {
        Add-Content -LiteralPath $LogPath -Value ([string]$_) -Encoding UTF8
    }
    $exitCode = $LASTEXITCODE
} catch {
    Add-Content `
        -LiteralPath $LogPath `
        -Value ("collector task launcher failure: " + $_.Exception.GetType().Name) `
        -Encoding UTF8
    $exitCode = 1
}

exit $exitCode

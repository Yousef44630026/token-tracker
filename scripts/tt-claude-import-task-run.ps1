[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TaskLog,
    [Parameter(Mandatory = $true)]
    [string]$StateFile
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptDir "tt-claude-import.cmd"
$logDir = Split-Path -Parent $TaskLog

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

try {
    & $runner --state-file $StateFile --json 2>&1 | ForEach-Object {
        Add-Content -LiteralPath $TaskLog -Value ([string]$_) -Encoding UTF8
    }
    $exitCode = [int]$LASTEXITCODE
} catch {
    $failure = [ordered]@{
        timestamp = [DateTime]::UtcNow.ToString("o")
        status = "launcher_failure"
        error_type = $_.Exception.GetType().Name
    } | ConvertTo-Json -Compress
    Add-Content -LiteralPath $TaskLog -Value $failure -Encoding UTF8
    $exitCode = 1
}

exit $exitCode

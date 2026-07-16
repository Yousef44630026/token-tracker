[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TaskLog,
    [Parameter(Mandatory = $true)]
    [string]$EvidenceFile,
    [Parameter(Mandatory = $true)]
    [string]$DataDir,
    [Parameter(Mandatory = $true)]
    [string]$OutputFile,
    [string]$Prices = ""
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptDir "tt-dashboard.cmd"
$logDir = Split-Path -Parent $TaskLog
$evidenceDir = Split-Path -Parent $EvidenceFile
$outputDir = Split-Path -Parent $OutputFile
$temporaryOutput = Join-Path $outputDir ("dashboard-refresh-{0}.xlsx" -f $PID)
$timestamp = [DateTime]::UtcNow.ToString("o")
$exitCode = 1
$report = $null
$status = "error"
$errorType = $null

New-Item -ItemType Directory -Force -Path $logDir, $evidenceDir, $outputDir | Out-Null

try {
    $arguments = @("--data-dir", $DataDir, "--output", $temporaryOutput, "--json")
    if ($Prices) {
        $arguments += @("--prices", $Prices)
    }
    # Native stderr is expected when no price table is configured. Capture it for the task
    # log without letting PowerShell 5 turn the warning into a terminating RemoteException;
    # the native exit code remains the authority for success or failure.
    $ErrorActionPreference = "Continue"
    $outputLines = @(& $runner @arguments 2>&1)
    $exitCode = [int]$LASTEXITCODE
    $ErrorActionPreference = "Stop"
    $jsonLine = $outputLines |
        ForEach-Object { [string]$_ } |
        Where-Object { $_.TrimStart().StartsWith("{") } |
        Select-Object -Last 1
    if ($jsonLine) {
        $report = $jsonLine | ConvertFrom-Json
    }
    if ($exitCode -ne 0) {
        throw "dashboard runner exited with code $exitCode"
    }
    if (-not $report) {
        throw "dashboard runner produced no JSON report"
    }
    if (-not (Test-Path -LiteralPath $temporaryOutput -PathType Leaf)) {
        throw "dashboard runner produced no workbook"
    }
    Move-Item -LiteralPath $temporaryOutput -Destination $OutputFile -Force
    $report.output = $OutputFile
    $status = "ok"
} catch {
    $errorType = $_.Exception.GetType().Name
    if ($exitCode -eq 0) {
        $exitCode = 1
    }
} finally {
    $ErrorActionPreference = "Stop"
    Remove-Item -LiteralPath $temporaryOutput -Force -ErrorAction SilentlyContinue
}

$evidence = [ordered]@{
    timestamp = $timestamp
    status = $status
    exit_code = $exitCode
    error_type = $errorType
    output_file = $OutputFile
    report = $report
}
$evidenceJson = $evidence | ConvertTo-Json -Depth 6 -Compress
$temporaryEvidence = "$EvidenceFile.tmp-$PID"
[IO.File]::WriteAllText($temporaryEvidence, $evidenceJson + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false)))
Move-Item -LiteralPath $temporaryEvidence -Destination $EvidenceFile -Force
Add-Content -LiteralPath $TaskLog -Value $evidenceJson -Encoding UTF8

exit $exitCode

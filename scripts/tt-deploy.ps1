[CmdletBinding()]
param(
    [ValidateSet("Plan", "Install", "Status", "Uninstall")]
    [string]$Mode = "Status"
)

# One-command deployment orchestrator for the AI Token Tracker stack.
#
#   Plan       show what Install would do (no changes)
#   Install    bring the whole stack up: data dir, ACL-restricted auth token, the scheduled
#              tasks, then verify with the Doctor. Idempotent and re-runnable.
#   Status     health of every component in one view (tasks + auth + Doctor)
#   Uninstall  remove every scheduled task (the ledger and auth token are left in place)
#
# Two tasks (Collector, Monitor) use an at-startup trigger and require an ELEVATED shell to
# (re)install; a standard user still gets the four logon-triggered tasks. This is stated, not
# hidden: on a non-admin run the script installs what it can and prints the exact elevated
# command for the rest.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir "..")).Path
$store = if ($env:TRACKER_STORE) { $env:TRACKER_STORE } else { "C:\ai-token-tracker-data\collector_events.jsonl" }
$dataDir = Split-Path -Parent $store

$STANDARD_TASKS = @(
    @{ name = "Claude Import";   script = "tt-claude-import-task.ps1" },
    @{ name = "Dashboard";       script = "tt-dashboard-task.ps1" },
    @{ name = "Backup";          script = "tt-backup-task.ps1" },
    @{ name = "Doctor Watchdog"; script = "tt-doctor-watchdog-task.ps1" }
)
$ELEVATED_TASKS = @(
    @{ name = "Collector"; script = "tt-collector-task.ps1" },
    @{ name = "Monitor";   script = "tt-collector-monitor-task.ps1" }
)

function Test-Admin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-Manager([string]$script, [string]$mode) {
    $path = Join-Path $scriptDir $script
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $path -Mode $mode
}

function Invoke-Doctor {
    Push-Location $root
    try { & (Join-Path $scriptDir "tt-doctor.cmd") --store $store --strict-warnings }
    finally { Pop-Location }
}

if ($Mode -eq "Plan") {
    Write-Host "AI Token Tracker deploy plan"
    Write-Host "  root       : $root"
    Write-Host "  data dir   : $dataDir  (must be a NON-synced local volume)"
    Write-Host "  auth token : outside the repo, ACL-restricted (tt-local-auth.ps1)"
    Write-Host "  admin now  : $(Test-Admin)"
    Write-Host "  standard-user tasks (install without elevation):"
    $STANDARD_TASKS | ForEach-Object { Write-Host "      - AI Token Tracker $($_.name)" }
    Write-Host "  elevated tasks (need an admin shell):"
    $ELEVATED_TASKS | ForEach-Object { Write-Host "      - AI Token Tracker $($_.name)" }
    Write-Host "  then: tt-doctor --strict-warnings to verify"
    exit 0
}

if ($Mode -eq "Status") {
    Write-Host "=== AI Token Tracker stack status ==="
    Write-Host "-- auth --"
    Invoke-Manager "tt-local-auth.ps1" "Status"
    foreach ($t in ($STANDARD_TASKS + $ELEVATED_TASKS)) {
        Write-Host "-- task: $($t.name) --"
        try { Invoke-Manager $t.script "Status" } catch { Write-Host "  (status error: $($_.Exception.Message))" }
    }
    Write-Host "-- doctor --"
    try { Invoke-Doctor } catch { Write-Host "  (doctor error: $($_.Exception.Message))" }
    exit 0
}

if ($Mode -eq "Install") {
    Write-Host "=== deploying AI Token Tracker stack ==="
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $dataDir "config") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $dataDir "health") | Out-Null

    Write-Host "-- ensuring ACL-restricted auth token --"
    Invoke-Manager "tt-local-auth.ps1" "Configure"

    Write-Host "-- installing standard-user tasks --"
    foreach ($t in $STANDARD_TASKS) {
        Write-Host "   installing: AI Token Tracker $($t.name)"
        Invoke-Manager $t.script "Install"
    }

    if (Test-Admin) {
        Write-Host "-- installing elevated tasks (admin detected) --"
        foreach ($t in $ELEVATED_TASKS) {
            Write-Host "   installing: AI Token Tracker $($t.name)"
            Invoke-Manager $t.script "Install"
        }
    } else {
        Write-Host "-- SKIPPED elevated tasks (not an admin shell) --"
        Write-Host "   Collector and Monitor use an at-startup trigger a standard user cannot register."
        Write-Host "   In an ADMIN PowerShell, run:"
        foreach ($t in $ELEVATED_TASKS) {
            Write-Host "     powershell -ExecutionPolicy Bypass -File `"$(Join-Path $scriptDir $t.script)`" -Mode Install"
        }
    }

    Write-Host "-- verifying with the Doctor --"
    try { Invoke-Doctor } catch { Write-Host "  (doctor reported issues: $($_.Exception.Message))" }
    Write-Host "=== deploy complete. Re-run: tt-deploy.ps1 -Mode Status ==="
    exit 0
}

if ($Mode -eq "Uninstall") {
    Write-Host "=== removing AI Token Tracker scheduled tasks (ledger + auth left intact) ==="
    foreach ($t in ($STANDARD_TASKS + $ELEVATED_TASKS)) {
        Write-Host "   uninstalling: AI Token Tracker $($t.name)"
        try { Invoke-Manager $t.script "Uninstall" } catch { Write-Host "   (skip: $($_.Exception.Message))" }
    }
    exit 0
}

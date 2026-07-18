[CmdletBinding()]
param(
    [ValidateSet("Plan", "Configure", "Rotate", "Status", "Remove")]
    [string]$Mode = "Status"
)

$ErrorActionPreference = "Stop"
$store = if ($env:TRACKER_STORE) { $env:TRACKER_STORE } else { "C:\ai-token-tracker-data\collector_events.jsonl" }
$runtimeDir = Split-Path -Parent $store
$tokenFile = if ($env:TRACKER_AUTH_TOKEN_FILE) {
    $env:TRACKER_AUTH_TOKEN_FILE
} else {
    Join-Path (Join-Path $runtimeDir "config") "collector-auth.token"
}

function New-TrackerBearer {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Protect-TrackerTokenFile {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $userRule = New-Object Security.AccessControl.FileSystemAccessRule(
        $identity.User,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow
    )
    $systemSid = New-Object Security.Principal.SecurityIdentifier("S-1-5-18")
    $systemRule = New-Object Security.AccessControl.FileSystemAccessRule(
        $systemSid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow
    )
    $acl.AddAccessRule($userRule)
    $acl.AddAccessRule($systemRule)
    Set-Acl -LiteralPath $tokenFile -AclObject $acl
}

function Write-AuthStatus {
    $configured = Test-Path -LiteralPath $tokenFile -PathType Leaf
    $length = $null
    $valid = $false
    if ($configured) {
        try {
            $value = (Get-Content -LiteralPath $tokenFile -Raw).Trim()
            $length = $value.Length
            $valid = $length -ge 32 -and $value -notmatch "\s"
        } catch {
            $valid = $false
        }
    }
    [ordered]@{
        configured = $configured
        valid = $valid
        token_length = $length
        token_file = $tokenFile
        store = $store
        secret_embedded_in_task = $false
    } | ConvertTo-Json -Depth 3
}

if ($Mode -eq "Plan") { Write-AuthStatus; exit 0 }

if ($Mode -in @("Configure", "Rotate")) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $tokenFile) | Out-Null
    $token = New-TrackerBearer
    [IO.File]::WriteAllText($tokenFile, $token, (New-Object Text.UTF8Encoding($false)))
    Protect-TrackerTokenFile
    $env:TRACKER_AUTH_TOKEN_FILE = $tokenFile
    Write-AuthStatus
    exit 0
}

if ($Mode -eq "Remove") {
    Remove-Item -LiteralPath $tokenFile -Force -ErrorAction SilentlyContinue
    Write-AuthStatus
    exit 0
}

Write-AuthStatus

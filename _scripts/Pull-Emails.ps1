# Pull-Emails.ps1
# Moves emails from OneDrive EmailCapture folders to Vault 00-Inbox.
# Designed to run periodically via Windows Task Scheduler.
#
# NOTE: This script MUST run on Windows (via Task Scheduler).
# Vault ingestion (/w-daily) runs on WSL/Linux and accesses the same directories.
#
# - Sent emails get SENT- prefix (for direction detection during ingestion)
# - Received emails keep original filename
# - Originals move to Processed/ subfolder after successful copy
# - Collision detection: appends -2, -3 etc. if destination already exists

$ErrorActionPreference = "Stop"

$SentDir   = "$env:USERPROFILE\OneDrive - Acme Corp\EmailCapture\Sent"
$VaultDir  = "$env:USERPROFILE\OneDrive - Acme Corp\EmailCapture\Vault"
# Your vault's local path (edit to match where your vault lives):
$Inbox     = "$env:USERPROFILE\Obsidian\Vault\00-Inbox"
$LogFile   = "$env:USERPROFILE\Obsidian\Vault\_scripts\pull-emails.log"

# Ensure directories exist
foreach ($dir in @("$SentDir\Processed", "$VaultDir\Processed", $Inbox)) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

# Log rotation: archive if > 500KB
if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 500KB) {
    $rotated = "$LogFile.$((Get-Date).ToString('yyyyMMdd-HHmmss'))"
    Rename-Item -Path $LogFile -NewName $rotated -Force
    # Keep only last 3 rotated logs
    Get-ChildItem "$LogFile.*" -File | Sort-Object LastWriteTime -Descending | Select-Object -Skip 3 | Remove-Item -Force
}

$timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
$countSent = 0
$countRecv = 0
$errors = @()

# Helper: get unique destination path (appends -2, -3 etc. on collision)
function Get-UniqueDestination {
    param([string]$Dir, [string]$Name)
    $dest = Join-Path $Dir $Name
    if (-not (Test-Path -LiteralPath $dest)) { return $dest }
    $base = [System.IO.Path]::GetFileNameWithoutExtension($Name)
    $ext  = [System.IO.Path]::GetExtension($Name)
    $counter = 2
    while (Test-Path -LiteralPath (Join-Path $Dir "$base-$counter$ext")) { $counter++ }
    return Join-Path $Dir "$base-$counter$ext"
}

# Pull sent emails (prefix with SENT-)
Get-ChildItem -LiteralPath $SentDir -Filter "*.txt" -File -ErrorAction SilentlyContinue | ForEach-Object {
    $dest = Get-UniqueDestination -Dir $Inbox -Name "SENT-$($_.Name)"
    try {
        Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
        Move-Item -LiteralPath $_.FullName -Destination (Join-Path "$SentDir\Processed" $_.Name) -Force
        $countSent++
    } catch {
        $errors += "sent: $($_.TargetObject) - $($_.Exception.Message)"
        # Clean up partial copy if it exists
        if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue }
    }
}

# Pull received emails (no prefix)
Get-ChildItem -LiteralPath $VaultDir -Filter "*.txt" -File -ErrorAction SilentlyContinue | ForEach-Object {
    $dest = Get-UniqueDestination -Dir $Inbox -Name $_.Name
    try {
        Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
        Move-Item -LiteralPath $_.FullName -Destination (Join-Path "$VaultDir\Processed" $_.Name) -Force
        $countRecv++
    } catch {
        $errors += "received: $($_.TargetObject) - $($_.Exception.Message)"
        if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue }
    }
}

# Pull calendar JSON (overwrite via temp file for atomicity)
$CalendarDir = "$env:USERPROFILE\OneDrive - Acme Corp\EmailCapture\Calendar"
$countCal = 0

if (Test-Path -LiteralPath $CalendarDir) {
    # Only copy today's calendar file (Power Automate may leave older snapshots)
    $todayPrefix = (Get-Date).ToString("yyyy-MM-dd")

    Get-ChildItem -LiteralPath $CalendarDir -Filter "*-calendar.json" -File -ErrorAction SilentlyContinue | ForEach-Object {
        $dest = Join-Path $Inbox $_.Name
        $tempDest = Join-Path $Inbox "$($_.Name).tmp"
        try {
            Copy-Item -LiteralPath $_.FullName -Destination $tempDest -Force
            if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force }
            Rename-Item -LiteralPath $tempDest -NewName $_.Name -Force
            $countCal++
        } catch {
            $errors += "calendar: $($_.Name) - $($_.Exception.Message)"
            if (Test-Path -LiteralPath $tempDest) { Remove-Item -LiteralPath $tempDest -Force -ErrorAction SilentlyContinue }
        }
    }

    # Clean up old calendar files from inbox (keep only today's)
    Get-ChildItem -LiteralPath $Inbox -Filter "*-calendar.json" -File -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -notlike "$todayPrefix*"
    } | ForEach-Object {
        try {
            Remove-Item -LiteralPath $_.FullName -Force
        } catch {
            $errors += "calendar-cleanup: $($_.Name) - $($_.Exception.Message)"
        }
    }
}

# Log errors first
foreach ($err in $errors) {
    Add-Content -Path $LogFile -Value "$timestamp ERROR $err"
}

# Log summary (only if something was pulled or errors occurred)
if ($countSent -gt 0 -or $countRecv -gt 0 -or $countCal -gt 0 -or $errors.Count -gt 0) {
    $summary = "$timestamp Pulled $countSent sent, $countRecv received, $countCal calendar"
    if ($errors.Count -gt 0) { $summary += " ($($errors.Count) errors)" }
    Add-Content -Path $LogFile -Value $summary
}

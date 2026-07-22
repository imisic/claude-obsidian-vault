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
# - Attachments land in 00-Inbox\_email-attachments\ (a dir, so classify-inbox's
#   is_file() filter skips it) and are matched to their email by receive-second

$ErrorActionPreference = "Stop"

# Your vault's local path (edit to match where your vault lives):
$VaultRoot     = "$env:USERPROFILE\Obsidian\Vault"
$SentDir       = "$env:USERPROFILE\OneDrive - Acme Corp\EmailCapture\Sent"
$VaultDir      = "$env:USERPROFILE\OneDrive - Acme Corp\EmailCapture\Vault"
$AttachDir     = "$env:USERPROFILE\OneDrive - Acme Corp\EmailCapture\Vault\Attachments"
$Inbox         = "$VaultRoot\00-Inbox"
$AttachStaging = "$Inbox\_email-attachments"
$LogFile       = "$VaultRoot\_scripts\pull-emails.log"

# The capture flow writes the .txt BEFORE its attachment loop, so an email
# reliably hits disk a second or so ahead of its own attachments (observed: .txt
# at 13:12:21, PDF at 13:12:22). Pulling inside that gap would process the email
# attachment-less and orphan the PDF permanently, since nothing re-links it
# afterwards. Defer any email that has not sat still this long; it costs one
# scheduled cycle at worst.
$SettleSeconds = 120

# Ensure directories exist
foreach ($dir in @("$SentDir\Processed", "$VaultDir\Processed", "$AttachDir\Processed", $Inbox, $AttachStaging)) {
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
$countAtt = 0
$countDeferred = 0
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

# Helper: make an attachment filename safe to put inside an Obsidian [[wikilink]].
# Outlook suffixes duplicate names with [1], and [ ] | # ^ all break wikilink
# syntax. The flow's yyyy-MM-dd_HHmmss-NN- prefix contains none of these, so
# rewriting the tail never affects email-to-attachment matching.
function Get-WikilinkSafeName {
    param([string]$Name)
    return $Name -replace '\[', '(' -replace '\]', ')' -replace '[|#^]', '-'
}

# Pull attachments FIRST, so an email pulled later in this same run always finds
# its own attachments already staged.
Get-ChildItem -LiteralPath $AttachDir -File -ErrorAction SilentlyContinue | ForEach-Object {
    $safe = Get-WikilinkSafeName -Name $_.Name
    $dest = Get-UniqueDestination -Dir $AttachStaging -Name $safe
    try {
        Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
        Move-Item -LiteralPath $_.FullName -Destination (Get-UniqueDestination -Dir "$AttachDir\Processed" -Name $_.Name) -Force
        $countAtt++
    } catch {
        $errors += "attachment: $($_.TargetObject) - $($_.Exception.Message)"
        if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue }
    }
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

# Pull received emails (no prefix). Skip any that have not settled: their
# attachments may still be syncing down from OneDrive. Sent mail has no
# attachment capture, so it is never deferred.
Get-ChildItem -LiteralPath $VaultDir -Filter "*.txt" -File -ErrorAction SilentlyContinue | Where-Object {
    if ((New-TimeSpan -Start $_.LastWriteTime -End (Get-Date)).TotalSeconds -lt $SettleSeconds) {
        $script:countDeferred++
        $false
    } else { $true }
} | ForEach-Object {
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
if ($countSent -gt 0 -or $countRecv -gt 0 -or $countCal -gt 0 -or $countAtt -gt 0 -or $errors.Count -gt 0) {
    $summary = "$timestamp Pulled $countSent sent, $countRecv received, $countAtt attachments, $countCal calendar"
    if ($countDeferred -gt 0) { $summary += " ($countDeferred deferred, not settled)" }
    if ($errors.Count -gt 0) { $summary += " ($($errors.Count) errors)" }
    Add-Content -Path $LogFile -Value $summary
}

# Install-EmailPullTask.ps1
# Run this ONCE in an elevated PowerShell to create the scheduled task.
# It runs Pull-Emails.ps1 every 15 minutes when the user is logged in.
# No expiration, runs indefinitely until manually removed.

$VbsPath = Join-Path $PSScriptRoot "Run-Hidden.vbs"
$TaskName = "Vault-PullEmails"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Use wscript.exe + VBS wrapper for truly invisible execution (no console flash)
$Action = New-ScheduledTaskAction `
    -Execute "wscript.exe" `
    -Argument "`"$VbsPath`""

$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes 15)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Pulls emails from OneDrive EmailCapture to Vault inbox every 15 minutes"

Write-Host "Task '$TaskName' registered. Runs every 15 minutes indefinitely when logged in."
Write-Host "To test: schtasks /run /tn $TaskName"
Write-Host "To remove: Unregister-ScheduledTask -TaskName $TaskName"
Write-Host ""
Write-Host "IMPORTANT: Re-run this script after major Windows updates if the task stops."

' Run-Hidden.vbs
' Launches a PowerShell script with NO visible window at all.
' Used by the Vault-PullEmails scheduled task.

Set objShell = CreateObject("WScript.Shell")
objShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & Replace(WScript.ScriptFullName, "Run-Hidden.vbs", "Pull-Emails.ps1") & """", 0, False

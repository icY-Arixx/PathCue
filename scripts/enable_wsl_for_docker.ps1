# Run this file from PowerShell opened with "Run as administrator".
$ErrorActionPreference = "Stop"

dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

Write-Host "WSL and VirtualMachinePlatform were enabled. Restart Windows before installing Docker Desktop."

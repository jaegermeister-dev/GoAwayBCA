@echo off
echo WinDeploy read-only Windows hardware report. No settings or drivers are changed.
echo PowerShell execution policy is bypassed for this process only, not changed permanently.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Collect-Windows-Hardware.ps1"
echo.
pause

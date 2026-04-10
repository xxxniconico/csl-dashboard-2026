@echo off
REM Daily CSL pipeline wrapper for Task Scheduler (avoids schtasks /TR quoting issues).
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_csl_data.ps1"
exit /b %ERRORLEVEL%

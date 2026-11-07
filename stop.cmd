@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1" -Action stop %*
set "LAUNCH_EXIT=%ERRORLEVEL%"
if not defined CI pause
exit /b %LAUNCH_EXIT%

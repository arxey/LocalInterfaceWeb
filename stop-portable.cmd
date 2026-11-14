@echo off
setlocal
if not exist "%~dp0runtime\python\python.exe" (
    echo The bundled Python runtime was not found. Extract the complete portable release.
    if not defined CI pause
    exit /b 1
)
"%~dp0runtime\python\python.exe" -I -B "%~dp0portable\launcher.py" --stop %*
set "PORTABLE_EXIT=%ERRORLEVEL%"
if not "%PORTABLE_EXIT%"=="0" if not defined CI pause
exit /b %PORTABLE_EXIT%

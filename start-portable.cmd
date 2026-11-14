@echo off
setlocal
if not exist "%~dp0runtime\python\python.exe" (
    echo Incomplete portable ZIP. Extract the complete Windows portable release into a writable folder.
    echo The GitHub source ZIP does not include the Python and PostgreSQL runtimes.
    if not defined CI pause
    exit /b 1
)
"%~dp0runtime\python\python.exe" -I -B "%~dp0portable\launcher.py" %*
set "PORTABLE_EXIT=%ERRORLEVEL%"
if not "%PORTABLE_EXIT%"=="0" if not defined CI pause
exit /b %PORTABLE_EXIT%

@echo off
REM ======================================================================
REM  setup-cm-monitor.bat - Central Monitor (agentless Windows monitoring)
REM  Install / update. Edit cm-monitor-config.txt first. Argument: resetpw = new password.
REM  Double-click. Asks for administrator rights by itself.
REM ======================================================================
setlocal
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
    echo Asking for administrator rights...
    if "%~1"=="" (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    ) else (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    )
    exit /b
)
if not exist "%~dp0setup-cm-monitor.ps1" (
    echo [FAIL] setup-cm-monitor.ps1 not found next to this file.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-cm-monitor.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

@echo off
REM ======================================================================
REM  uninstall-cm-agent.bat - Central Monitor cm-agent (Windows)
REM  Double-click. Asks for administrator rights by itself.
REM ======================================================================
setlocal
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
    echo Asking for administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
if not exist "%~dp0uninstall-cm-agent.ps1" (
    echo [FAIL] uninstall-cm-agent.ps1 not found next to this file.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall-cm-agent.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

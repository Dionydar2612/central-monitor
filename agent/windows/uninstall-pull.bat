@echo off
REM ======================================================================
REM  uninstall-pull.bat - remove Central Monitor pull mode (no PowerShell)
REM    service cm-exporter + firewall rule "Central Monitor Exporter (pull)"
REM    add "purge" to also delete C:\ProgramData\central-monitor-agent
REM  Use: right-click > Run as administrator
REM ======================================================================
setlocal EnableExtensions
net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Please right-click uninstall-pull.bat and choose "Run as administrator".
    pause
    exit /b 1
)
sc query cm-exporter >nul 2>&1
if not errorlevel 1 (
    sc stop cm-exporter >nul 2>&1
    timeout /t 3 /nobreak >nul
    sc delete cm-exporter >nul
    echo removed service cm-exporter
)
netsh advfirewall firewall delete rule name="Central Monitor Exporter (pull)" >nul 2>&1 && echo removed firewall rule
if /i "%~1"=="purge" (
    rmdir /s /q "%ProgramData%\central-monitor-agent" 2>nul
    echo deleted %ProgramData%\central-monitor-agent
)
echo done
pause

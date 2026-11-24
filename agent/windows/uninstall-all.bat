@echo off
REM ======================================================================
REM  uninstall-all.bat - Central Monitor: remove EVERYTHING from this Windows machine
REM    push + pull mode: services cm-agent, cm-exporter
REM    scheduled tasks CentralMonitor* + running cm-netstat.ps1
REM    firewall rule "Central Monitor Exporter (pull)"
REM    folders %ProgramFiles%\central-monitor-agent, %ProgramData%\central-monitor-agent
REM    re-enables an OLD windows_exporter service that the installer had disabled
REM    does NOT touch any other program
REM  Use: double-click (asks for administrator)
REM ======================================================================
setlocal EnableExtensions
net session >nul 2>&1
if errorlevel 1 (
    powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
echo Removing Central Monitor from %COMPUTERNAME% ...
echo.
for %%T in (CentralMonitorNetstat CentralMonitorNetSec CentralMonitorLogs CentralMonitorProbe CentralMonitorInventory) do (
    schtasks /query /tn "%%T" >nul 2>&1 && (schtasks /end /tn "%%T" >nul 2>&1 & schtasks /delete /tn "%%T" /f >nul && echo [OK] removed scheduled task %%T)
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -like '*cm-netstat.ps1*' -or $_.CommandLine -like '*cm-logs.ps1*' -or $_.CommandLine -like '*cm-netsec.ps1*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('[OK] stopped ' + $_.ProcessId) }"
for %%S in (cm-agent cm-exporter) do (
    sc query %%S >nul 2>&1
    if not errorlevel 1 (
        sc stop %%S >nul 2>&1
        timeout /t 3 /nobreak >nul
        sc delete %%S >nul && echo [OK] removed service %%S
    )
)
netsh advfirewall firewall delete rule name="Central Monitor Exporter (pull)" >nul 2>&1 && echo [OK] removed firewall rule
timeout /t 2 /nobreak >nul
if exist "%ProgramFiles%\central-monitor-agent" rmdir /s /q "%ProgramFiles%\central-monitor-agent" && echo [OK] deleted %ProgramFiles%\central-monitor-agent
if exist "%ProgramData%\central-monitor-agent" rmdir /s /q "%ProgramData%\central-monitor-agent" && echo [OK] deleted %ProgramData%\central-monitor-agent
REM windows_exporter of another team / old install that our installer disabled -> turn it back on
sc qc windows_exporter 2>nul | find "DISABLED" >nul
if not errorlevel 1 (
    sc config windows_exporter start= auto >nul
    sc start windows_exporter >nul 2>&1
    echo [OK] re-enabled the old windows_exporter service
)
echo.
echo ---- check (should be empty) ----
sc query cm-agent >nul 2>&1 && echo [LEFT] service cm-agent
sc query cm-exporter >nul 2>&1 && echo [LEFT] service cm-exporter
schtasks /query /fo csv 2>nul | find /i "CentralMonitor" && echo [LEFT] scheduled task above
if exist "%ProgramFiles%\central-monitor-agent" echo [LEFT] %ProgramFiles%\central-monitor-agent (file in use - reboot and run again)
if exist "%ProgramData%\central-monitor-agent" echo [LEFT] %ProgramData%\central-monitor-agent (file in use - reboot and run again)
echo ---------------------------------
echo Done. Also remove this machine in Central Monitor ^> Settings if you no longer monitor it.
pause

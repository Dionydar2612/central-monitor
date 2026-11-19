@echo off
REM ======================================================================
REM  check-netstat.bat - Central Monitor: check Netstat Monitor on this Windows machine
REM    read-only: installs / stops / changes nothing
REM  Use: double-click (or right-click > Run as administrator for full task details)
REM ======================================================================
setlocal EnableExtensions
set "FAIL=0"
set "PF=%ProgramFiles%\central-monitor-agent"
set "PROM=%ProgramData%\central-monitor-agent\textfile\cm_netstat.prom"
echo Central Monitor - Netstat Monitor check
echo.

if exist "%PF%\cm-netstat.ps1" (echo [ OK ] cm-netstat.ps1 installed) else (echo [FAIL] cm-netstat.ps1 not found - reinstall the agent with the NEW zip from central & set "FAIL=1")

schtasks /query /tn CentralMonitorNetstat >nul 2>&1
if errorlevel 1 (
    echo [FAIL] scheduled task CentralMonitorNetstat not found - reinstall the agent with the NEW zip
    set "FAIL=1"
) else (
    schtasks /query /tn CentralMonitorNetstat /fo LIST | findstr /i /c:"Running" >nul && echo [ OK ] task CentralMonitorNetstat is running || (
        echo [WARN] task CentralMonitorNetstat exists but is not running - it restarts by itself within 5 minutes
        echo        to start now: schtasks /run /tn CentralMonitorNetstat   ^(as administrator^)
    )
)

if not exist "%PROM%" (
    echo [FAIL] %PROM% not found - collector has not written any data yet
    set "FAIL=1"
    goto :exporter
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "exit 7" >nul 2>&1
if not "%errorlevel%"=="7" (
    echo [INFO] PowerShell blocked - cannot check how fresh the data is
    goto :exporter
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$f='%PROM%'; $age=[int]((Get-Date)-(Get-Item $f).LastWriteTime).TotalSeconds; $t=Get-Content $f; $i=($t|Select-String '^cm_netstat_info').Line; $ms=(($t|Select-String '^cm_netstat_collect_ms').Line -split ' ')[-1]; $tcp=@($t|Select-String '^cm_netstat_listen\{proto=\"tcp\"').Count; $udp=@($t|Select-String '^cm_netstat_listen\{proto=\"udp\"').Count; $q=(($t|Select-String 'queue_supported\{kind=\"accept\"\}').Line -split ' ')[-1]; $iv=if($i -match 'interval=\"(\d+)\"'){$matches[1]}else{'?'}; if($age -le 15){Write-Host ('[ OK ] data updated '+$age+' s ago (every '+$iv+' s, collect '+$ms+' ms)')}else{Write-Host ('[FAIL] data is '+$age+' s old - collector stopped'); exit 1}; Write-Host ('[INFO] listening ports: '+$tcp+' TCP, '+$udp+' UDP'); if($q -eq '1'){Write-Host '[INFO] Listening Queue: readable (IIS / HTTP.sys ports)'}else{Write-Host '[INFO] Listening Queue: not available on this machine (no IIS / HTTP.sys) - shown as Unsupported'}"
if errorlevel 1 set "FAIL=1"

:exporter
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { $r=(New-Object Net.WebClient).DownloadString('http://127.0.0.1:9182/metrics?collect[]=textfile'); if($r -match 'cm_netstat_heartbeat_seconds'){Write-Host '[ OK ] windows_exporter :9182 serves Netstat data'; exit 0} else {Write-Host '[FAIL] windows_exporter :9182 answers but has no Netstat data'; exit 1} } catch { Write-Host ('[FAIL] windows_exporter :9182 not answering: '+$_.Exception.Message); exit 1 }" 2>nul
if errorlevel 1 set "FAIL=1"

echo.
if "%FAIL%"=="0" (echo RESULT: OK - open Apps ^& Web ^> Netstat Monitor ^> Hosts ^& Ports on central) else (echo RESULT: PROBLEM - see [FAIL] lines above)
echo Nothing was changed on this machine.
echo.
pause

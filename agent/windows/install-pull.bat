@echo off
REM ======================================================================
REM  install-pull.bat - Central Monitor: Windows "pull" mode WITHOUT PowerShell
REM ----------------------------------------------------------------------
REM  For machines that cannot reach central (firewall) and cannot run PowerShell.
REM  Central (Prometheus) pulls metrics from windows_exporter on this machine.
REM    - installs windows_exporter as service "cm-exporter" listening 0.0.0.0:9182
REM    - Windows Firewall: allow TCP 9182 ONLY from the central IP
REM    - stops/disables "cm-agent" (push mode) if it exists
REM  Metrics only (CPU / Memory / Disk / Network / Service / Process). No logs.
REM
REM  Use: extract the zip, right-click install-pull.bat > Run as administrator
REM       (or from an admin cmd:  install-pull.bat 172.27.2.22 [name] [nops])
REM
REM  PowerShell first: if PowerShell can run on this machine, this file runs
REM    install-fluentbit.ps1 -Pull  (better: also installs the Software inventory
REM    tasks and tests windows_exporter properly). Only if PowerShell is blocked /
REM    missing, it installs with plain cmd (sc / netsh) - no Software tab.
REM    "nops" as 3rd argument = always use the plain cmd install.
REM  Offline: put windows_exporter-0.32.1-amd64.exe next to this file.
REM  Remove:  uninstall-pull.bat
REM ======================================================================
setlocal EnableExtensions EnableDelayedExpansion

set "PORT=9182"
set "VER=0.32.1"
set "SVC=cm-exporter"
set "DIR=%ProgramData%\central-monitor-agent"
set "EXE=%ProgramData%\central-monitor-agent\windows_exporter.exe"
set "TEXTDIR=%ProgramData%\central-monitor-agent\textfile"
set "LOG=%ProgramData%\central-monitor-agent\windows_exporter.log"
set "RULE=Central Monitor Exporter (pull)"
set "URL=https://github.com/prometheus-community/windows_exporter/releases/download/v%VER%/windows_exporter-%VER%-amd64.exe"

echo.
echo === Central Monitor - Windows pull mode ===

REM ---------------------------------------------------------------- admin
net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Please right-click install-pull.bat and choose "Run as administrator".
    goto :fail
)

REM ---------------------------------------------------------------- central IP
set "CENTRAL=%~1"
set "NAME=%~2"
if "%NAME%"=="-" set "NAME="
if not "%CENTRAL%"=="" goto :havecentral
set "INPUT="
set /p "INPUT=IP of central: "
REM typed "IP name" on one line -> split into IP and name
for /f "tokens=1,2" %%a in ("%INPUT%") do (
    set "CENTRAL=%%a"
    if not "%%b"=="" set "NAME=%%b"
)
if "%CENTRAL%"=="" (
    echo [ERROR] central IP is required.
    goto :fail
)
if "%NAME%"=="" set /p "NAME=Name of this machine in central (Enter = from computer name): "
:havecentral
echo %CENTRAL%| findstr /r "^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo [ERROR] "%CENTRAL%" is not an IP address. Enter only the IP of central, for example 10.0.0.5
    goto :fail
)
echo central IP : %CENTRAL%   ^(only this IP may read port %PORT%^)
if not "%NAME%"=="" echo name       : %NAME%

REM ---------------------------------------------------------------- PowerShell first (better install)
if /i "%~3"=="nops" goto :cmdonly
if not exist "%~dp0install-fluentbit.ps1" (
    echo [INFO] install-fluentbit.ps1 is not next to this file - using the plain cmd install.
    goto :cmdonly
)
where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo [INFO] PowerShell not found - using the plain cmd install.
    goto :cmdonly
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "exit 7" >nul 2>&1
if not "%errorlevel%"=="7" (
    echo [INFO] PowerShell is blocked on this machine - using the plain cmd install.
    goto :cmdonly
)
echo.
echo PowerShell is available - running install-fluentbit.ps1 -Pull ^(includes the Software tab^)
echo.
set "PSNAME="
if not "%NAME%"=="" set "PSNAME=-Name %NAME%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-fluentbit.ps1" -Pull -Central %CENTRAL% %PSNAME%
if errorlevel 1 (
    echo.
    echo [ERROR] install-fluentbit.ps1 -Pull failed ^(see the messages above^).
    set "N2=%NAME%"
    if "!N2!"=="" set "N2=-"
    echo         To try the plain cmd install instead:  install-pull.bat %CENTRAL% !N2! nops
    goto :fail
)
echo.
pause
exit /b 0

:cmdonly
echo.
echo --- plain cmd install ^(no Software tab^) ---
REM ---------------------------------------------------------------- 1) windows_exporter file
echo.
echo [1/4] windows_exporter %VER%
set "SRC="
if exist "%~dp0windows_exporter-%VER%-amd64.exe" set "SRC=%~dp0windows_exporter-%VER%-amd64.exe"
if not defined SRC if exist "%USERPROFILE%\Downloads\windows_exporter-%VER%-amd64.exe" set "SRC=%USERPROFILE%\Downloads\windows_exporter-%VER%-amd64.exe"
if not defined SRC (
    set "SRC=%TEMP%\windows_exporter-%VER%-amd64.exe"
    echo     downloading %URL%
    where curl.exe >nul 2>&1
    if not errorlevel 1 (
        curl.exe -fsSL -o "!SRC!" "%URL%"
    ) else (
        bitsadmin /transfer cmexporter /download /priority normal "%URL%" "!SRC!" >nul
    )
)
if not exist "%SRC%" (
    echo [ERROR] windows_exporter-%VER%-amd64.exe not found and download failed.
    echo         Download it in a browser and put it next to install-pull.bat:
    echo         %URL%
    goto :fail
)
for %%A in ("%SRC%") do if %%~zA LSS 5000000 (
    echo [ERROR] %SRC% is too small - incomplete download or replaced by a proxy page.
    goto :fail
)
echo     file: %SRC%

REM ---------------------------------------------------------------- 2) service cm-exporter
echo.
echo [2/4] service %SVC% on 0.0.0.0:%PORT%
sc query %SVC% >nul 2>&1
if not errorlevel 1 (
    sc stop %SVC% >nul 2>&1
    timeout /t 3 /nobreak >nul
)
sc query windows_exporter >nul 2>&1
if not errorlevel 1 (
    echo     [WARN] disabling existing service windows_exporter ^(same port %PORT%^)
    sc stop windows_exporter >nul 2>&1
    sc config windows_exporter start= disabled >nul 2>&1
    timeout /t 3 /nobreak >nul
)
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
    echo [ERROR] port %PORT% is already used by another program:
    netstat -ano | findstr /r /c:":%PORT% .*LISTENING"
    goto :fail
)
if not exist "%DIR%" mkdir "%DIR%"
if not exist "%TEXTDIR%" mkdir "%TEXTDIR%"
copy /y "%SRC%" "%EXE%" >nul
if errorlevel 1 (
    echo [ERROR] cannot copy to %EXE%
    goto :fail
)

set "OK="
call :try "cpu,cpu_info,os,memory,logical_disk,physical_disk,net,tcp,system,service,process,time,pagefile,textfile"
if not defined OK call :try "cpu,os,memory,logical_disk,net,system,service,process,textfile"
if not defined OK call :try "cpu,os,memory,logical_disk,net,system,service"
if not defined OK (
    echo [ERROR] service %SVC% does not start. Last lines of %LOG%:
    if exist "%LOG%" type "%LOG%"
    goto :fail
)
sc failure %SVC% reset= 86400 actions= restart/5000/restart/10000/restart/30000 >nul

REM ---------------------------------------------------------------- 3) Windows Firewall
echo.
echo [3/4] Windows Firewall: allow TCP %PORT% from %CENTRAL% only
netsh advfirewall firewall delete rule name="%RULE%" >nul 2>&1
netsh advfirewall firewall add rule name="%RULE%" dir=in action=allow protocol=TCP localport=%PORT% remoteip=%CENTRAL% profile=any >nul
if errorlevel 1 (
    echo     [WARN] could not add the firewall rule - check that "%CENTRAL%" is a valid IP
) else (
    echo     rule "%RULE%" added
)

REM ---------------------------------------------------------------- 4) push agent (if any)
echo.
echo [4/4] push agent
sc query cm-agent >nul 2>&1
if not errorlevel 1 (
    sc stop cm-agent >nul 2>&1
    sc config cm-agent start= disabled >nul 2>&1
    echo     stopped and disabled service cm-agent ^(push mode is replaced by pull^)
) else (
    echo     none
)

echo.
echo ==================================================================
echo  DONE - pull mode. Service %SVC% is listening on port %PORT%.
echo  IP of this machine:
ipconfig | findstr /c:"IPv4"
echo.
echo  On central: Settings ^> this machine ^> Connection ^> mode = pull,
echo              windows_exporter = ^<IP of this machine^>:%PORT%  then Save.
echo  Test from central:  curl -s http://^<IP of this machine^>:%PORT%/metrics ^| head
echo  The machine shows OK in about 1 minute. Pull mode has metrics only, no logs.
echo ==================================================================
echo.
pause
exit /b 0

REM ---------------------------------------------------------------- create/update service and test one collector set
:try
set "COLS=%~1"
set "BIN=%EXE% --web.listen-address=0.0.0.0:%PORT% --collectors.enabled=%COLS% --collector.textfile.directories=%TEXTDIR% --log.file=%LOG%"
sc query %SVC% >nul 2>&1
if errorlevel 1 (
    sc create %SVC% binPath= "%BIN%" start= auto DisplayName= "Central Monitor Exporter (windows_exporter)" >nul
) else (
    sc config %SVC% binPath= "%BIN%" start= auto >nul
)
sc start %SVC% >nul 2>&1
for /l %%i in (1,1,15) do (
    if not defined OK (
        timeout /t 2 /nobreak >nul
        sc query %SVC% | find "RUNNING" >nul && netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul && set "OK=1"
    )
)
if defined OK (
    echo     collectors: %COLS%
) else (
    echo     [WARN] collectors failed, trying a smaller set: %COLS%
    sc stop %SVC% >nul 2>&1
    timeout /t 2 /nobreak >nul
)
exit /b 0

:fail
echo.
echo Installation did not finish.
pause
exit /b 1

@echo off
REM ======================================================================
REM  precheck.bat - Central Monitor: check this Windows machine BEFORE install
REM    read-only: installs / stops / changes nothing
REM    runs precheck.ps1 (full check) if PowerShell is allowed, else basic cmd checks
REM  Use: right-click > Run as administrator, enter the IP of central
REM       or from an admin cmd:  precheck.bat 172.27.2.22 [pull]
REM ======================================================================
setlocal EnableExtensions
set "D=%~dp0"
set "CENTRAL=%~1"
if "%CENTRAL%"=="" set /p "CENTRAL=IP of central: "
for /f "tokens=1" %%a in ("%CENTRAL%") do set "CENTRAL=%%a"
set "MODE="
if /i "%~2"=="pull" set "MODE=-Pull"
if /i "%~2"=="-pull" set "MODE=-Pull"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "exit 7" >nul 2>&1
if "%errorlevel%"=="7" if exist "%D%precheck.ps1" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%D%precheck.ps1" -Central %CENTRAL% %MODE%
    echo.
    pause
    exit /b
)

echo PowerShell is blocked here - basic checks only
echo.
net session >nul 2>&1 && echo [ OK ] Administrator || echo [FAIL] not Administrator - right-click ^> Run as administrator
netstat -ano | findstr /r /c:":9182 .*LISTENING" >nul && (
    echo [FAIL] port 9182 is already in use by:
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":9182 .*LISTENING"') do tasklist /fi "PID eq %%p" /nh
) || echo [ OK ] port 9182 free
sc query windows_exporter >nul 2>&1 && echo [WARN] service windows_exporter exists - installer will stop and disable it (same port) || echo [ OK ] no other windows_exporter service
sc query cm-exporter >nul 2>&1 && echo [INFO] cm-exporter already installed - reinstall updates it
sc query cm-agent >nul 2>&1 && echo [INFO] cm-agent already installed
for /f "tokens=3" %%f in ('dir /-c %SystemDrive%\ ^| findstr /c:"bytes free"') do echo [INFO] free space on %SystemDrive% : %%f bytes (need about 1 GB)
sc query MpsSvc | findstr RUNNING >nul && echo [ OK ] Windows Firewall running || echo [WARN] Windows Firewall not running - port 9182 open to everyone the network allows
echo.
echo Nothing was changed on this machine.
pause

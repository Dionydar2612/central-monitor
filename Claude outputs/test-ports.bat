@echo off
REM ======================================================================
REM  test-ports.bat - Central Monitor: test ports from THIS Windows PC (outside Docker)
REM    read-only: changes nothing
REM  Use: double-click, enter the IP of the machine you want to monitor
REM ======================================================================
setlocal EnableExtensions
set "IP=%~1"
if "%IP%"=="" set /p "IP=IP of the machine to monitor: "
for /f "tokens=1" %%a in ("%IP%") do set "IP=%%a"
echo.
echo Testing %IP% from this PC (not from Docker)...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "foreach ($p in 22,3389,9100,9182) { $c = New-Object Net.Sockets.TcpClient; try { $done = $c.BeginConnect('%IP%', $p, $null, $null).AsyncWaitHandle.WaitOne(3000); if (-not $done) { Write-Host ('{0,-5} BLOCKED / TIMEOUT' -f $p) } elseif ($c.Connected) { Write-Host ('{0,-5} OPEN' -f $p) } else { Write-Host ('{0,-5} REFUSED (reachable, nothing listening)' -f $p) } } catch { Write-Host ('{0,-5} ERROR {1}' -f $p, $_.Exception.Message) } finally { $c.Close() } }"
echo.
echo Route used to reach %IP%:
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Find-NetRoute -RemoteIPAddress '%IP%' -ErrorAction Stop | Select-Object -First 2; Write-Host ('  interface : ' + $r[1].InterfaceAlias); Write-Host ('  via       : ' + $r[1].NextHop); Write-Host ('  this PC IP: ' + $r[0].IPAddress) } catch { Write-Host '  (cannot find route)' }"
echo.
echo Nothing was changed on this PC.
pause

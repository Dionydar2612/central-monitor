@echo off
REM ======================================================================
REM  check-health.bat - Central Monitor: why does this Windows machine hang / restart?
REM    read-only: changes nothing. Send the whole output to the Central Monitor admin.
REM  Use: double-click (asks for administrator)
REM ======================================================================
setlocal EnableExtensions
net session >nul 2>&1
if errorlevel 1 (
    powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s = Get-Content -Raw -LiteralPath '%~f0'; $i = $s.IndexOf('####PS-START####' + [char]13); Invoke-Expression $s.Substring($i + 18)"
pause
exit /b
####PS-START####
$ErrorActionPreference = 'SilentlyContinue'
function H($t) { Write-Host ''; Write-Host "== $t ==" -ForegroundColor Cyan }
$os = Get-CimInstance Win32_OperatingSystem
H 'Machine'
Write-Host ("{0} | {1} | last boot {2} | up {3:N1} days" -f $env:COMPUTERNAME, $os.Caption, $os.LastBootUpTime, ((Get-Date) - $os.LastBootUpTime).TotalDays)
Write-Host ("RAM total {0:N1} GB, free {1:N1} GB | commit used {2:N1} / {3:N1} GB" -f ($os.TotalVisibleMemorySize/1MB), ($os.FreePhysicalMemory/1MB), (($os.TotalVirtualMemorySize-$os.FreeVirtualMemory)/1MB), ($os.TotalVirtualMemorySize/1MB))
$inst = Get-ScheduledTask -TaskName CentralMonitorInventory -ErrorAction SilentlyContinue
if ($inst) { Write-Host ("Central Monitor agent installed: task created {0}" -f $inst.Date) }

H 'Unexpected restarts / hangs in last 30 days (41=power loss/hang, 6008=unexpected shutdown, 1001=bluescreen, 2004=low memory)'
$ev = Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,6008,1001,2004,1074; StartTime=(Get-Date).AddDays(-30)} -MaxEvents 40
if ($ev) { $ev | ForEach-Object { Write-Host ("{0:yyyy-MM-dd HH:mm}  id {1,-5} {2}" -f $_.TimeCreated, $_.Id, (($_.Message -split "`n")[0]).Trim()) } } else { Write-Host 'none' }

H 'Top 10 processes by CPU time'
Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 | ForEach-Object { Write-Host ("{0,-28} pid {1,-6} cpu {2,8:N0}s  mem {3,6:N0} MB  handles {4}" -f $_.ProcessName, $_.Id, $_.CPU, ($_.WorkingSet64/1MB), $_.HandleCount) }

H 'Top 10 processes by memory'
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 | ForEach-Object { Write-Host ("{0,-28} pid {1,-6} mem {2,6:N0} MB  private {3,6:N0} MB  handles {4}" -f $_.ProcessName, $_.Id, ($_.WorkingSet64/1MB), ($_.PrivateMemorySize64/1MB), $_.HandleCount) }

H 'WMI provider hosts (WmiPrvSE)'
Get-Process WmiPrvSE | ForEach-Object { Write-Host ("WmiPrvSE pid {0,-6} cpu {1,8:N0}s  mem {2,6:N0} MB  handles {3}  started {4}" -f $_.Id, $_.CPU, ($_.WorkingSet64/1MB), $_.HandleCount, $_.StartTime) }
$wmi = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-WMI-Activity/Operational'; Id=5858,5603; StartTime=(Get-Date).AddDays(-1)} -MaxEvents 2000
Write-Host ("WMI errors last 24h: {0}" -f @($wmi).Count)

H 'Central Monitor processes'
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*central-monitor*' -or $_.CommandLine -like '*cm-netstat*' -or $_.CommandLine -like '*cm-logs*' -or $_.CommandLine -like '*cm-netsec*' -or $_.Name -like 'windows_exporter*' -or $_.Name -like 'fluent-bit*' } | ForEach-Object {
  $p = Get-Process -Id $_.ProcessId
  $what = if ($_.CommandLine -match '(cm-[a-z]+\.ps1)') { $matches[1] } else { $_.Name }
  Write-Host ("{0,-22} pid {1,-6} cpu {2,8:N0}s  mem {3,6:N0} MB  handles {4,-6} priority {5}  started {6}" -f $what, $_.ProcessId, $p.CPU, ($p.WorkingSet64/1MB), $p.HandleCount, $p.PriorityClass, $p.StartTime) }
foreach ($s in 'cm-exporter','cm-agent','windows_exporter') { $v = Get-Service $s; if ($v) { Write-Host ("service {0,-16} {1} ({2})" -f $s, $v.Status, $v.StartType) } }

H 'Central Monitor scheduled tasks'
Get-ScheduledTask -TaskName 'CentralMonitor*' | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; Write-Host ("{0,-26} {1,-8} last {2}  result 0x{3:X}" -f $_.TaskName, $_.State, $i.LastRunTime, $i.LastTaskResult) }
$prom = Join-Path $env:ProgramData 'central-monitor-agent\textfile\cm_netstat.prom'
if (Test-Path $prom) { $m = Select-String -Path $prom -Pattern '^cm_netstat_collect_ms (\d+)'; if ($m) { Write-Host ("netstat collect time: {0} ms per round" -f $m.Matches[0].Groups[1].Value) } }

H 'Disk'
Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object { Write-Host ("{0} free {1:N1} / {2:N1} GB" -f $_.DeviceID, ($_.FreeSpace/1GB), ($_.Size/1GB)) }
Write-Host ''
Write-Host 'Nothing was changed on this machine.'

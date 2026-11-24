@echo off
REM ======================================================================
REM  set-features.bat - Central Monitor agent: turn features ON / OFF on THIS Windows machine
REM    no reinstall. Saved in %ProgramData%\central-monitor-agent\features.json
REM    (install-fluentbit.ps1 keeps these settings when you reinstall / update the agent)
REM  Use: double-click -> menu
REM   or: set-features.bat netstat off      (names: netstat netsec logs inventory probe, value: on / off)
REM ======================================================================
setlocal EnableExtensions
net session >nul 2>&1
if errorlevel 1 (
    powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    exit /b
)
set "CM_SELF=%~f0"
set "CM_ARGS=%*"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:CM_SELF); $m='#'+'###PS-START####'; Invoke-Expression $s.Substring($s.IndexOf($m)+$m.Length)"
echo.
pause
exit /b
####PS-START####
$ErrorActionPreference = 'SilentlyContinue'
$Data = Join-Path $env:ProgramData 'central-monitor-agent'
$Text = Join-Path $Data 'textfile'
$Conf = Join-Path $Data 'features.json'
$F = [ordered]@{
    netstat   = @{ task = 'CentralMonitorNetstat';   prom = 'cm_netstat.prom';   ps = 'cm-netstat.ps1'; name = 'Netstat Monitor (ports / queue, runs all the time) - heaviest' }
    netsec    = @{ task = 'CentralMonitorNetSec';    prom = 'cm_netsec.prom';    ps = 'cm-netsec.ps1';  name = 'Network & Security (connections, firewall, logons)' }
    logs      = @{ task = 'CentralMonitorLogs';      prom = 'cm_logs.prom';      ps = 'cm-logs.ps1';    name = 'Logs (Event Log + log files, pull mode)' }
    inventory = @{ task = 'CentralMonitorInventory'; prom = 'cm_inventory.prom'; ps = '';               name = 'Software / listening ports list' }
    probe     = @{ task = 'CentralMonitorProbe';     prom = 'cm_probe.prom';     ps = '';               name = 'Docker containers + local web check' }
}
function Load-Off { try { @((Get-Content $Conf -Raw | ConvertFrom-Json).off) } catch { @() } }
function Save-Off($off) {
    New-Item -ItemType Directory -Force -Path $Data | Out-Null
    [IO.File]::WriteAllText($Conf, (@{ off = @($off | Select-Object -Unique) } | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
}
function Set-Feature($key, [bool]$on) {
    $f = $F[$key]
    $t = Get-ScheduledTask -TaskName $f.task
    if (-not $t) { Write-Host "[SKIP] $key : task $($f.task) not installed on this machine"; return }
    $off = @(Load-Off | Where-Object { $_ -ne $key })
    if ($on) {
        Enable-ScheduledTask -TaskName $f.task | Out-Null
        Start-ScheduledTask -TaskName $f.task
        Write-Host "[ON ] $key"
    } else {
        Stop-ScheduledTask -TaskName $f.task
        Disable-ScheduledTask -TaskName $f.task | Out-Null
        if ($f.ps) { Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like "*$($f.ps)*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force } }
        Remove-Item (Join-Path $Text $f.prom) -Force
        $off += $key
        Write-Host "[OFF] $key"
    }
    Save-Off $off
}
function Show {
    Write-Host ''
    Write-Host "Central Monitor agent features on $env:COMPUTERNAME"
    $i = 0
    foreach ($k in $F.Keys) {
        $i++
        $t = Get-ScheduledTask -TaskName $F[$k].task
        $st = if (-not $t) { 'not installed' } elseif ($t.State -eq 'Disabled') { 'OFF' } else { 'ON ' }
        Write-Host ("  {0}  {1,-10} {2,-14} {3}" -f $i, $k, $st, $F[$k].name)
    }
}
$a = @("$env:CM_ARGS" -split '\s+' | Where-Object { $_ })
if ($a.Count -ge 2) {
    $k = $a[0].ToLower(); $v = $a[1].ToLower()
    if (-not $F.Contains($k) -or $v -notin 'on', 'off') { Write-Host '[ERROR] use: set-features.bat <netstat|netsec|logs|inventory|probe> <on|off>'; return }
    Set-Feature $k ($v -eq 'on'); Show; return
}
while ($true) {
    Show
    $n = Read-Host "`nNumber to switch ON/OFF (Enter = exit)"
    if (-not $n) { break }
    $keys = @($F.Keys)
    if ($n -notmatch '^\d$' -or [int]$n -lt 1 -or [int]$n -gt $keys.Count) { Write-Host '[ERROR] enter 1-5'; continue }
    $k = $keys[[int]$n - 1]
    $t = Get-ScheduledTask -TaskName $F[$k].task
    Set-Feature $k ($t -and $t.State -eq 'Disabled')
}
Write-Host 'Saved. CPU / RAM / disk / Windows services monitoring are not affected.'

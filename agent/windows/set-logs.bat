@echo off
REM ======================================================================
REM  set-logs.bat - Central Monitor (pull mode): change which log FILES the agent reads
REM    no reinstall: rewrites C:\ProgramData\central-monitor-agent\cm-logs.json
REM    the agent (task CentralMonitorLogs, every 1 minute) uses the new list right away
REM  Use: double-click (asks for Administrator), type the paths separated by ;
REM       or:  set-logs.bat "C:\App\Logs\*.txt;D:\Other\*.log"
REM  Only ERROR / WARN lines are sent (error, fatal, critical, exception, failed, warn ...)
REM  Push mode: use the install command from central Settings instead
REM ======================================================================
setlocal
net session >nul 2>&1
if errorlevel 1 (
    echo Asking for Administrator...
    if "%~1"=="" (powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs") else (powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '\"%~1\"' -Verb RunAs")
    exit /b
)
set "CM_LOGS=%~1"
set "CM_SELF=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:CM_SELF); $m='#'+'###PS-START####'; Invoke-Expression $s.Substring($s.IndexOf($m)+$m.Length)"
echo.
pause
exit /b

####PS-START####
$ErrorActionPreference = 'Stop'
$Dir  = Join-Path $env:ProgramData 'central-monitor-agent'
$Conf = Join-Path $Dir 'cm-logs.json'
if (-not (Test-Path $Conf)) {
    if (Test-Path (Join-Path $Dir 'fluent-bit.conf')) {
        Write-Host '[INFO] This machine uses PUSH mode. To change log files run the install command'
        Write-Host '       from central: Settings > Edit this machine > connection tab.'
    } else { Write-Host '[FAIL] Central Monitor agent (pull mode) is not installed on this machine.' }
    return
}
$c = Get-Content $Conf -Raw | ConvertFrom-Json
$channels = @($c.channels | Where-Object { $_ })
if (-not $channels) { $channels = @('System', 'Application') }
$logs = @($c.logs | Where-Object { $_ })
Write-Host 'Current log files:'
if ($logs) { $logs | ForEach-Object { Write-Host "  $_" } } else { Write-Host '  (none)' }
$in = $env:CM_LOGS
if (-not $in) {
    Write-Host ''
    Write-Host 'Type ALL log file paths, separated by ;   (wildcard in the file name is OK, e.g. C:\App\Logs\*.txt)'
    Write-Host 'Enter = no change     - = remove all files (Event Log stays)'
    $in = Read-Host 'Paths'
}
if (-not $in) { Write-Host 'No change.'; return }
$new = @()
if ($in.Trim() -ne '-') { $new = @($in -split ';' | ForEach-Object { $_.Trim().Trim('"') } | Where-Object { $_ }) }
Write-Host ''
foreach ($p in $new) {
    $folder = Split-Path $p -Parent
    if (-not (Test-Path -LiteralPath $folder)) { Write-Host "[WARN] folder not found: $folder (kept, read when it exists)"; continue }
    $files = @(Get-ChildItem -Path $p -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    if (-not $files) { Write-Host "[WARN] no file matches $p (kept, read when a file appears)"; continue }
    $f = $files[0]
    $b = New-Object byte[] 2
    $fs = [IO.File]::Open($f.FullName, 'Open', 'Read', 'ReadWrite'); $n = $fs.Read($b, 0, 2); $fs.Close()
    Write-Host "[ OK ] $p : $($files.Count) file(s), newest $($f.Name) ($($f.LastWriteTime))"
    if ($n -eq 2 -and (($b[0] -eq 0xFF -and $b[1] -eq 0xFE) -or ($b[0] -eq 0xFE -and $b[1] -eq 0xFF))) {
        Write-Host "[WARN] $($f.Name) is UTF-16 - the agent reads UTF-8, ERROR / WARN lines will not be found. Tell the central admin."
    }
}
$out = New-Object PSObject -Property @{ channels = $channels; logs = $new }
[IO.File]::WriteAllText($Conf, ($out | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
Write-Host ''
Write-Host "[ OK ] saved $Conf"
schtasks /run /tn CentralMonitorLogs | Out-Null
Write-Host '[ OK ] agent reads the new list now (first time: last 64 KB of each file, then only new lines).'
Write-Host '       Only ERROR / WARN lines are sent. Logs page on central shows them in about 1-2 minutes.'
Write-Host ''
Write-Host 'Also put the same paths in central: Settings > Edit this machine > log files'
Write-Host '(so a future reinstall keeps them).'

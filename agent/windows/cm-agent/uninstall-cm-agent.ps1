# =====================================================================
# uninstall-cm-agent.ps1 - remove Central Monitor cm-agent from this machine
# Run it through uninstall-cm-agent.bat, or let central remove it from the web page.
# Removes: services cm-agent + cm-exporter, task CentralMonitorInventory,
#          %ProgramFiles%\central-monitor-agent, %ProgramData%\central-monitor-agent
# Does not touch the agentless (WinRM / cm-monitor) setup or any other program. ASCII only.
# =====================================================================
param([string[]]$Options = @())
$ErrorActionPreference = 'Continue'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Here 'logs'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogFile = Join-Path $LogDir ('remove-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c; Add-Content -Path $LogFile -Value ((Get-Date -Format 'HH:mm:ss') + ' ' + $m) -Encoding UTF8 }

$left = @()
foreach ($svc in @('cm-agent', 'cm-exporter')) {
    $s = Get-Service -Name $svc -ErrorAction SilentlyContinue
    if ($s) {
        Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue
        Start-Sleep 2
        & sc.exe delete $svc | Out-Null
        Say "[ OK ] removed service $svc" 'Green'
    }
}
foreach ($t in @('CentralMonitorInventory', 'CentralMonitorProbe', 'CentralMonitorNetstat', 'CentralMonitorNetSec', 'CentralMonitorLogs')) {
    if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
        Say "[ OK ] removed task $t" 'Green'
    }
}
Start-Sleep 2
foreach ($d in @((Join-Path $env:ProgramFiles 'central-monitor-agent'), (Join-Path $env:ProgramData 'central-monitor-agent'))) {
    if (Test-Path -LiteralPath $d) {
        Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $d) { $left += $d; Say "[WARN] could not delete $d (a file is in use - delete it after a reboot)" 'Yellow' }
        else { Say "[ OK ] deleted $d" 'Green' }
    }
}
foreach ($svc in @('cm-agent', 'cm-exporter')) { if (Get-Service -Name $svc -ErrorAction SilentlyContinue) { $left += "service $svc" } }
Say ''
if ($left.Count) { Say "RESULT: DONE with leftovers: $($left -join ', ')" 'Yellow' } else { Say 'RESULT: SUCCESS - cm-agent removed' 'Green' }
try {
    [ordered]@{ ok = $true; leftovers = $left; computer = $env:COMPUTERNAME } | ConvertTo-Json -Compress |
        Set-Content -LiteralPath (Join-Path $Here 'remove-result.json') -Encoding ASCII
} catch { }
exit 0

# =====================================================================
# check-cm-monitor.ps1 - READ-ONLY health check of Central Monitor agentless monitoring
# Run it through check-cm-monitor.bat. Changes nothing.
# Shows: endpoint + limits, listener, firewall, user + groups, allowed paths + permissions,
#        resource use of the WinRM session process (wsmprovhost) of cm-monitor,
#        recent logons of cm-monitor, disk used by our files.
# =====================================================================
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Continue'

$EpName   = 'CentralMonitor'
$UserName = 'cm-monitor'
$DataDir  = Join-Path $env:ProgramData 'CentralMonitor'
$ModDir   = Join-Path $env:ProgramFiles 'WindowsPowerShell\Modules\CentralMonitor'
$RuleName = 'CentralMonitor-WinRM-HTTPS'
$script:Bad = 0

function Ok([string]$m)   { Write-Host "[ OK ] $m" -ForegroundColor Green }
function Info([string]$m) { Write-Host "       $m" }
function Bad([string]$m)  { Write-Host "[WARN] $m" -ForegroundColor Yellow; $script:Bad++ }
function Head([string]$m) { Write-Host ''; Write-Host "--- $m" -ForegroundColor Cyan }

Write-Host '=== Central Monitor - check (read-only) ===' -ForegroundColor Cyan
Info ("computer: $env:COMPUTERNAME   PowerShell: " + $PSVersionTable.PSVersion)

Head 'WinRM'
$w = Get-Service WinRM -ErrorAction SilentlyContinue
if ($null -ne $w -and $w.Status -eq 'Running') { Ok 'WinRM running' } else { Bad 'WinRM is not running' }
try {
    foreach ($l in @(Get-ChildItem WSMan:\localhost\Listener)) {
        $port = ''; $th = ''
        foreach ($i in @(Get-ChildItem ('WSMan:\localhost\Listener\' + $l.Name))) {
            if ($i.Name -eq 'Port') { $port = $i.Value }
            if ($i.Name -eq 'CertificateThumbprint') { $th = $i.Value }
        }
        Info ("listener: " + ($l.Keys -join ' ') + "  port $port  $th")
    }
    foreach ($n in @('AllowUnencrypted', 'Auth\Basic')) {
        $v = (Get-Item ("WSMan:\localhost\Service\" + $n)).Value
        if ($v -eq 'true') { Bad "Service\$n = true (not needed by Central Monitor)" } else { Ok "Service\$n = $v" }
    }
} catch { Bad "cannot read WinRM settings: $($_.Exception.Message)" }

Head 'ExecutionPolicy / module file'
foreach ($sc in @('MachinePolicy', 'UserPolicy', 'LocalMachine')) { Info ("ExecutionPolicy $sc = " + (Get-ExecutionPolicy -Scope $sc)) }
if ([string](Get-ExecutionPolicy -Scope MachinePolicy) -eq 'AllSigned') { Bad 'Group Policy forces AllSigned - the module must be code-signed' }
$psm = Join-Path $ModDir 'CentralMonitor.psm1'
if (Test-Path $psm) {
    $zone = Get-Item -LiteralPath $psm -Stream Zone.Identifier -ErrorAction SilentlyContinue
    if ($null -ne $zone) { Bad 'CentralMonitor.psm1 is marked as downloaded from the internet (blocked) - run setup-cm-monitor.bat again' }
    else { Ok 'module file not blocked' }
    $sig = Get-AuthenticodeSignature -FilePath $psm
    if ($sig.Status -eq 'Valid') { Ok ("module signature valid (" + $sig.SignerCertificate.Subject + ")") }
    else { Bad ("module signature: " + $sig.Status + " - run setup-cm-monitor.bat again") }
}

Head 'JEA endpoint'
$ep = Get-PSSessionConfiguration -Name $EpName -ErrorAction SilentlyContinue
if ($null -eq $ep) { Bad 'endpoint CentralMonitor not registered' }
else {
    Ok "endpoint registered (session type $($ep.SessionType), language $($ep.LanguageMode))"
    Info ("permission: " + $ep.Permission)
    foreach ($q in @('MaxMemoryPerShellMB', 'MaxShellsPerUser', 'MaxShells', 'MaxConcurrentUsers', 'IdleTimeoutms')) {
        try { Info ("$q = " + (Get-Item ("WSMan:\localhost\Plugin\$EpName\Quotas\$q")).Value) } catch { }
    }
}
if (Test-Path (Join-Path $ModDir 'RoleCapabilities\CentralMonitor.psrc')) { Ok "module + role capability present" } else { Bad "module missing: $ModDir" }

Head 'Firewall'
$r = Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue
if ($null -eq $r) { Bad 'firewall rule not found (or firewall off)' }
else {
    $a = $r | Get-NetFirewallAddressFilter; $p = $r | Get-NetFirewallPortFilter
    Ok ("rule enabled=" + $r.Enabled + "  TCP " + $p.LocalPort + "  from " + ($a.RemoteAddress -join ','))
}

Head 'User'
$u = Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue
if ($null -eq $u) { Bad 'user cm-monitor not found' }
else {
    Ok ("user enabled=" + $u.Enabled + "  last logon " + $u.LastLogon)
    foreach ($g in @(Get-LocalGroup)) {
        $mem = @(Get-LocalGroupMember -Group $g -ErrorAction SilentlyContinue | Where-Object { $_.SID.Value -eq $u.SID.Value })
        if ($mem.Count -gt 0) {
            if ($g.SID.Value -eq 'S-1-5-32-544') { Bad "cm-monitor is in Administrators - remove it!" } else { Info ("member of " + $g.Name) }
        }
    }
}

Head 'Service Control Manager'
if ($null -ne $u) {
    $scm = @(& sc.exe sdshow scmanager | Where-Object { $_ -match '^D:' })[0]
    if ($scm -and $scm.Contains($u.SID.Value)) { Ok 'cm-monitor may list services (read-only)' }
    else { Bad 'cm-monitor cannot list services over WinRM - run setup-cm-monitor.bat again' }
}

Head 'Allowed log paths'
$allow = Join-Path $DataDir 'allowed-paths.txt'
if (Test-Path $allow) {
    $rules = @(Get-Content $allow | Where-Object { $_.Trim() -ne '' -and -not $_.Trim().StartsWith('#') })
    if ($rules.Count -eq 0) { Info '(none - Event Log only)' }
    foreach ($x in $rules) {
        $d = $x; if (-not $x.EndsWith('\')) { $d = [System.IO.Path]::GetDirectoryName($x) }
        if (-not [System.IO.Directory]::Exists($d)) { Bad "$x  (folder not found)"; continue }
        $has = $false
        if ($null -ne $u) { $has = @((Get-Acl $d).Access | Where-Object { $_.IdentityReference.Value -like "*\$UserName" }).Count -gt 0 }
        Info ("$x   permission for cm-monitor: " + $(if ($has) { 'yes' } else { 'inherited / none' }))
    }
} else { Bad 'allowed-paths.txt not found' }

Head 'Session process (wsmprovhost of cm-monitor)'
$found = 0
foreach ($p in @(Get-Process -Name wsmprovhost -IncludeUserName -ErrorAction SilentlyContinue)) {
    if ($p.UserName -notlike "*\$UserName") { continue }
    $found++
    $age = (Get-Date) - $p.StartTime
    $cpuPct = 0
    if ($age.TotalSeconds -gt 0) { $cpuPct = [Math]::Round($p.TotalProcessorTime.TotalSeconds / $age.TotalSeconds * 100, 3) }
    $msg = ("pid {0}  RAM {1} MB (peak {2} MB)  CPU time {3} s over {4:N1} h = {5}% of one core  priority {6}" -f $p.Id, [Math]::Round($p.WorkingSet64 / 1MB, 1), [Math]::Round($p.PeakWorkingSet64 / 1MB, 1), [Math]::Round($p.TotalProcessorTime.TotalSeconds, 1), $age.TotalHours, $cpuPct, $p.PriorityClass)
    if ($p.PeakWorkingSet64 -gt 150MB) { Bad $msg } else { Ok $msg }
}
if ($found -eq 0) { Info 'no session open right now (central not connected, or idle-closed)' }

Head 'Open WinRM sessions (shells) of cm-monitor'
try {
    $sh = @(Get-WSManInstance -ResourceURI shell -Enumerate -ErrorAction Stop | Where-Object { [string]$_.Owner -like "*$UserName" })
    $lim = 5
    try { $lim = [int](Get-Item ("WSMan:\localhost\Plugin\$EpName\Quotas\MaxShellsPerUser")).Value } catch { }
    if ($sh.Count -ge $lim) { Bad "open: $($sh.Count) of $lim allowed - quota full (old sessions expire by themselves, or run setup-cm-monitor.bat again)" }
    else { Ok "open: $($sh.Count) of $lim allowed" }
    foreach ($x in $sh) { Info ("  shell " + $x.ShellId + "  running " + $x.ShellRunTime + "  idle " + $x.ShellInactivity) }
} catch { Info "cannot list WinRM shells: $($_.Exception.Message)" }

Head 'Recent logons of cm-monitor (Security log, last 24 h)'
try {
    $xp = "*[System[(EventID=4624 or EventID=4625) and TimeCreated[timediff(@SystemTime) <= 86400000]] and EventData[Data[@Name='TargetUserName']='$UserName']]"
    $ev = @(Get-WinEvent -LogName Security -FilterXPath $xp -MaxEvents 200 -ErrorAction Stop)
    $okN = @($ev | Where-Object { $_.Id -eq 4624 }).Count
    $badN = @($ev | Where-Object { $_.Id -eq 4625 }).Count
    Info ("successful: $okN   failed: $badN   (max 200 read)")
    if ($ev.Count -gt 0) { Info ("latest: " + $ev[0].TimeCreated) }
    if ($badN -gt 0) { Bad 'failed logons found - wrong password in central, or someone is guessing' }
} catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { Info 'no logons in the last 24 h' } else { Info "cannot read Security log: $($_.Exception.Message)" }
}

Head 'Disk used by Central Monitor on this machine'
$sum = 0
foreach ($d in @($DataDir, $ModDir)) {
    if (Test-Path $d) { $sum += (Get-ChildItem $d -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum }
}
Ok ("{0:N1} KB (module + settings; no data is stored here)" -f ($sum / 1KB))

Write-Host ''
if ($script:Bad -gt 0) { Write-Host "RESULT: $($script:Bad) WARNING(S)" -ForegroundColor Yellow; exit 2 }
Write-Host 'RESULT: OK' -ForegroundColor Green
exit 0

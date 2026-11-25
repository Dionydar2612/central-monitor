# =====================================================================
# remove-cm-monitor.ps1 - remove Central Monitor agentless monitoring from this machine
# Run it through remove-cm-monitor.bat (asks for administrator rights itself).
#
# Removes ONLY what setup-cm-monitor created (read from manifest.json):
#   JEA endpoint, module, firewall rule, HTTPS listener + certificate (only if setup created them),
#   folder read permissions, deny-logon rights, user cm-monitor, %ProgramData%\CentralMonitor
# Without a manifest it removes only items with our exact names
# (endpoint CentralMonitor, firewall rule CentralMonitor-WinRM-HTTPS, module folder, user
#  cm-monitor with our description) and never touches any listener / certificate.
# The WinRM service itself stays as it is.
# =====================================================================
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$EpName   = 'CentralMonitor'
$UserName = 'cm-monitor'
$UserDesc = 'Central Monitor read-only (JEA). Created by setup-cm-monitor.'
$DataDir  = Join-Path $env:ProgramData 'CentralMonitor'
$ModDir   = Join-Path $env:ProgramFiles 'WindowsPowerShell\Modules\CentralMonitor'
$RuleName = 'CentralMonitor-WinRM-HTTPS'
$ManiPath = Join-Path $DataDir 'manifest.json'
$script:Problems = 0

function Ok([string]$m)   { Write-Host "[ OK ] $m" -ForegroundColor Green }
function Info([string]$m) { Write-Host "[INFO] $m" }
function Warn([string]$m) { Write-Host "[WARN] $m" -ForegroundColor Yellow; $script:Problems++ }
function Try-Do([string]$what, [scriptblock]$action) {
    try { & $action; Ok $what } catch { Warn "$what - failed: $($_.Exception.Message)" }
}

function Set-DenyLogonRights([string]$Sid) {
    $tmp = Join-Path $env:TEMP ('cm-secpol-' + [guid]::NewGuid().ToString('N'))
    try {
        & secedit.exe /export /cfg "$tmp.inf" /areas USER_RIGHTS | Out-Null
        $current = Get-Content -LiteralPath "$tmp.inf" -Encoding Unicode
        $out = New-Object System.Collections.ArrayList
        foreach ($right in @('SeDenyInteractiveLogonRight', 'SeDenyRemoteInteractiveLogonRight')) {
            $vals = @()
            foreach ($l in $current) {
                if ($l -match ('^\s*' + $right + '\s*=\s*(.*)$')) { $vals = @($Matches[1].Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }) }
            }
            $vals = @($vals | Where-Object { $_ -ne ('*' + $Sid) })
            [void]$out.Add($right + ' = ' + ($vals -join ','))
        }
        $inf = @('[Unicode]', 'Unicode=yes', '[Version]', 'signature="$CHICAGO$"', 'Revision=1', '[Privilege Rights]') + $out
        Set-Content -LiteralPath "$tmp.inf" -Value $inf -Encoding Unicode
        & secedit.exe /configure /db "$tmp.sdb" /cfg "$tmp.inf" /areas USER_RIGHTS /quiet | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "secedit exit code $LASTEXITCODE" }
    } finally {
        Remove-Item -LiteralPath "$tmp.inf", "$tmp.sdb", "$tmp.jfm" -Force -ErrorAction SilentlyContinue
    }
}

Write-Host '=== Central Monitor - remove agentless monitoring ===' -ForegroundColor Cyan
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host '[FAIL] Run remove-cm-monitor.bat as administrator.' -ForegroundColor Red
    exit 1
}

$c = @{}
if (Test-Path -LiteralPath $ManiPath) {
    try {
        $j = Get-Content -LiteralPath $ManiPath -Raw | ConvertFrom-Json
        foreach ($p in $j.created.PSObject.Properties) { $c[$p.Name] = $p.Value }
        Info 'manifest.json found - removing what setup recorded'
    } catch { Warn "manifest.json unreadable - removing items by name only" }
} else {
    Info 'no manifest.json - removing items by name only (listener / certificate are kept)'
}

$u = Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue
$ours = ($null -ne $u) -and ($c.ContainsKey('user') -or $u.Description -eq $UserDesc)
$userSid = $null
if ($ours) { $userSid = $u.SID.Value }

# endpoint (WinRM restarts once)
if ($null -ne (Get-PSSessionConfiguration -Name $EpName -ErrorAction SilentlyContinue)) {
    Try-Do 'JEA endpoint CentralMonitor removed' { Unregister-PSSessionConfiguration -Name $EpName -NoServiceRestart -Force }
    Try-Do 'WinRM restarted once' { Restart-Service -Name WinRM -Force }
} else { Info 'endpoint not present' }

# listener + certificate (only if setup created them)
if ($c.ContainsKey('listener_https') -and $c['listener_https']) {
    $thumb = ''
    if ($c.ContainsKey('cert_thumbprint')) { $thumb = [string]$c['cert_thumbprint'] }
    foreach ($l in @(Get-ChildItem -Path WSMan:\localhost\Listener)) {
        if ($l.Keys -notcontains 'Transport=HTTPS') { continue }
        $lt = ''
        foreach ($i in @(Get-ChildItem -Path ('WSMan:\localhost\Listener\' + $l.Name))) {
            if ($i.Name -eq 'CertificateThumbprint') { $lt = ([string]$i.Value).Replace(' ', '').ToUpper() }
        }
        if ($thumb -ne '' -and $lt -eq $thumb) {
            Try-Do 'HTTPS listener (created by setup) removed' { Remove-Item -Path ('WSMan:\localhost\Listener\' + $l.Name) -Recurse -Force }
        }
    }
}
if ($c.ContainsKey('cert_thumbprint') -and $c['cert_thumbprint']) {
    $cp = 'Cert:\LocalMachine\My\' + [string]$c['cert_thumbprint']
    if (Test-Path -LiteralPath $cp) { Try-Do 'certificate (created by setup) removed' { Remove-Item -LiteralPath $cp -Force } }
}

# firewall rule
if ($null -ne (Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue)) {
    Try-Do 'firewall rule removed' { Remove-NetFirewallRule -Name $RuleName }
}

# folder permissions
if ($null -ne $userSid -and $c.ContainsKey('acl_dirs') -and $null -ne $c['acl_dirs']) {
    foreach ($d in @($c['acl_dirs'])) {
        if ([System.IO.Directory]::Exists($d)) { Try-Do "read permission removed: $d" { & icacls.exe $d /remove:g "*$userSid" | Out-Null } }
    }
}

# deny logon rights
if ($null -ne $userSid) {
    Try-Do 'deny-logon rights for cm-monitor removed' { Set-DenyLogonRights -Sid $userSid }
}

# user
if ($ours) { Try-Do 'user cm-monitor removed' { Remove-LocalUser -Name $UserName } }
elseif ($null -ne $u) { Warn "user '$UserName' exists but was not created by setup - kept" }

# module + data
if (Test-Path -LiteralPath $ModDir) { Try-Do 'module folder removed' { Remove-Item -LiteralPath $ModDir -Recurse -Force } }
if (Test-Path -LiteralPath $DataDir) { Try-Do "$DataDir removed" { Remove-Item -LiteralPath $DataDir -Recurse -Force } }

if ($c.ContainsKey('winrm_started') -and $c['winrm_started']) {
    Info 'setup had started the WinRM service - it is left running (stop it yourself if nothing else needs it)'
}

Write-Host ''
if ($script:Problems -gt 0) { Write-Host "RESULT: DONE WITH $($script:Problems) WARNING(S)" -ForegroundColor Yellow; exit 2 }
Write-Host 'RESULT: REMOVED' -ForegroundColor Green
Write-Host 'Remember to delete the machine in central (Settings) too.'
exit 0
